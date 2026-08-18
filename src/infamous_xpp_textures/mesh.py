"""Static rigid XPP mesh → GLB. Character packages have zero sections."""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

from .decode import decode_level, iter_textures, write_png
from .pngio import read_png
from .xpp import XppFile, parse_xpp

OBJECT_CHUNK = 0x01100000
GEOMETRY_HEAP_CHUNK = 0x0B800000
MATERIAL_CLASS = 0x0000015F


class MeshExportError(ValueError):
    pass


@dataclass(frozen=True)
class MeshSection:
    record_offset: int
    oid: int
    triangle_count: int
    material_offset: int
    attribute_count: int
    bounds: tuple[float, float, float, float, float, float]
    position_offset: int
    attribute_offset: int
    index_offset: int
    vertex_count: int
    split_streams: bool


@dataclass(frozen=True)
class JointBinding:
    index: int
    oid: int | None
    local_to_world: tuple[float, ...]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _extent_contains(start: int, size: int, address: int, length: int) -> bool:
    return length >= 0 and start <= address and address + length <= start + size


def find_mesh_sections(data: bytes, parsed: XppFile) -> list[MeshSection]:
    heaps = [c for c in parsed.chunks if c.type_tag == GEOMETRY_HEAP_CHUNK]
    if len(heaps) != 1:
        return []
    heap = heaps[0]
    found: list[MeshSection] = []
    for chunk in parsed.chunks:
        if chunk.type_tag != OBJECT_CHUNK or chunk.size < 0x60:
            continue
        for relative in range(0, chunk.size - 0x60 + 1, 0x10):
            record = chunk.offset + relative
            words = struct.unpack_from(">24I", data, parsed.data_offset + record)
            triangle_count = words[1] >> 16
            flags = words[1] & 0xFFFF
            attribute_count = words[3] >> 16
            material = words[2]
            position = words[13]
            attribute = words[15]
            indices = words[19]
            vertex_count = words[21]
            split = attribute not in (0, 0xFFFFFFFF)
            if not (
                0 < triangle_count <= 0xFFFF
                and flags in (1, 3)
                and attribute_count in (5, 6)
                and (words[3] & 0xFFFF) == 0
                and words[13] == words[14]
                and (
                    (words[15] == 0 and words[16] == 0xFFFFFFFF)
                    or (words[15] not in (0, 0xFFFFFFFF) and words[15] == words[16])
                )
                and words[17] == 0
                and words[18] == 0xFFFFFFFF
                and words[19] == words[20]
                and 0 < vertex_count <= 0xFFFF
                and words[22] == 0
                and words[23] == 0
                and 0 <= material <= parsed.data_size - 4
                and _u32(data, parsed.data_offset + material) == MATERIAL_CLASS
            ):
                continue
            position_stride = 12 if split else 26
            attribute_start = attribute if split else position + 12
            attribute_stride = 14 if split else 26
            if not (
                _extent_contains(heap.offset, heap.size, position, vertex_count * position_stride)
                and _extent_contains(
                    heap.offset,
                    heap.size,
                    attribute_start,
                    (vertex_count - 1) * attribute_stride + 14,
                )
                and _extent_contains(heap.offset, heap.size, indices, triangle_count * 3 * 2)
            ):
                continue
            bounds = tuple(struct.unpack(">f", value.to_bytes(4, "big"))[0] for value in words[4:10])
            if not all(math.isfinite(value) for value in bounds):
                continue
            if any(bounds[axis] > bounds[axis + 3] for axis in range(3)):
                continue
            index_values = struct.unpack_from(
                f">{triangle_count * 3}H", data, parsed.data_offset + indices
            )
            if max(index_values) >= vertex_count:
                continue
            positions = [
                struct.unpack_from(
                    ">3f", data, parsed.data_offset + position + index * position_stride
                )
                for index in range(vertex_count)
            ]
            if not all(all(math.isfinite(v) for v in xyz) for xyz in positions):
                continue
            if any(
                not all(
                    bounds[axis] - 0.05 <= xyz[axis] <= bounds[axis + 3] + 0.05 for axis in range(3)
                )
                for xyz in positions
            ):
                continue
            found.append(
                MeshSection(
                    record,
                    words[0],
                    triangle_count,
                    material,
                    attribute_count,
                    bounds,
                    position,
                    attribute_start,
                    indices,
                    vertex_count,
                    split,
                )
            )
    return found


def _compose_affine(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
    rotation = tuple(
        sum(left[row * 3 + axis] * right[axis * 3 + column] for axis in range(3))
        for row in range(3)
        for column in range(3)
    )
    translation = tuple(
        sum(left[row * 3 + axis] * right[9 + axis] for axis in range(3)) + left[9 + row]
        for row in range(3)
    )
    return rotation + translation


def joint_bindings(data: bytes, parsed: XppFile) -> dict[int | None, JointBinding]:
    candidates: list[dict[int | None, JointBinding]] = []
    payload = parsed.data_offset
    for marker in range(0, parsed.data_size - 4, 4):
        word = _u32(data, payload + marker)
        count = word >> 24
        if word & 0xFFFFFF != 0x000100 or not 2 <= count <= 128:
            continue
        matrix_start = marker - count * 48
        oid_start = marker + 0x14
        hierarchy_start = oid_start + (count - 1) * 4
        if matrix_start < 0 or hierarchy_start + count * 16 > parsed.data_size:
            continue
        matrices = []
        source_matrices = []
        valid = True
        for index in range(count):
            values = struct.unpack_from(">12f", data, payload + matrix_start + index * 48)
            if not all(math.isfinite(value) for value in values):
                valid = False
                break
            r = (values[0:3], values[4:7], values[8:11])
            source_matrices.append(
                tuple(r[row][axis] for row in range(3) for axis in range(3))
                + (values[3], values[7], values[11])
            )
            determinant = (
                r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
                - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
                + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0])
            )
            if abs(determinant - 1.0) > 1e-3:
                valid = False
                break
            for left in range(3):
                for right in range(3):
                    dot = sum(r[left][axis] * r[right][axis] for axis in range(3))
                    if abs(dot - (1.0 if left == right else 0.0)) > 1e-3:
                        valid = False
            t = (values[3], values[7], values[11])
            rt = tuple(tuple(r[column][row] for column in range(3)) for row in range(3))
            it = tuple(-sum(rt[row][axis] * t[axis] for axis in range(3)) for row in range(3))
            matrices.append(tuple(rt[row][axis] for row in range(3) for axis in range(3)) + it)
        if not valid:
            continue
        for index in range(count):
            entry = struct.unpack_from(">4I", data, payload + hierarchy_start + index * 16)
            if entry[0] != 0x80000000:
                valid = False
                break
            if any(value != 0xFFFFFFFF and value >= count for value in entry[1:]):
                valid = False
                break
        if not valid:
            continue
        identity = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        result: dict[int | None, JointBinding] = {None: JointBinding(0, None, identity)}
        for index in range(1, count):
            oid = _u32(data, payload + oid_start + (index - 1) * 4)
            transform = _compose_affine(source_matrices[0], matrices[index])
            result[oid] = JointBinding(index, oid, transform)
        candidates.append(result)
    if len(candidates) > 1:
        raise MeshExportError(f"ambiguous rigid joint blocks: {len(candidates)}")
    return candidates[0] if candidates else {}


def apply_joint(binding: JointBinding, xyz: tuple[float, float, float]) -> tuple[float, float, float]:
    m = binding.local_to_world
    return (
        m[0] * xyz[0] + m[1] * xyz[1] + m[2] * xyz[2] + m[9],
        m[3] * xyz[0] + m[4] * xyz[1] + m[5] * xyz[2] + m[10],
        m[6] * xyz[0] + m[7] * xyz[1] + m[8] * xyz[2] + m[11],
    )


class GlbBuilder:
    def __init__(self) -> None:
        self.binary = bytearray()
        self.views: list[dict] = []
        self.accessors: list[dict] = []

    def add_view(self, payload: bytes, target: int | None = None) -> int:
        while len(self.binary) & 3:
            self.binary.append(0)
        offset = len(self.binary)
        self.binary.extend(payload)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
        if target is not None:
            view["target"] = target
        self.views.append(view)
        return len(self.views) - 1

    def add_accessor(
        self,
        payload: bytes,
        component_type: int,
        count: int,
        kind: str,
        target: int,
        minimum: list[float] | None = None,
        maximum: list[float] | None = None,
    ) -> int:
        accessor = {
            "bufferView": self.add_view(payload, target),
            "componentType": component_type,
            "count": count,
            "type": kind,
        }
        if minimum is not None:
            accessor["min"] = minimum
        if maximum is not None:
            accessor["max"] = maximum
        self.accessors.append(accessor)
        return len(self.accessors) - 1


def _material_png(data: bytes, parsed: XppFile, material: int) -> bytes:
    """Decode the first 2D texture that looks bound after the material record."""
    from .heap import read_records

    recs = read_records(data, parsed)
    later = []
    end = min(parsed.data_size, material + 0x400)
    oids = []
    for offset in range(material, end - 3, 4):
        value = _u32(data, parsed.data_offset + offset)
        if value not in oids:
            oids.append(value)
    # Descriptor OID lives at +0x20 of the 0x70 record (same as original exporter).
    desc_oids = []
    for rec in recs:
        if len(rec.raw) >= 0x24:
            desc_oids.append((struct.unpack_from(">I", rec.raw, 0x20)[0], rec))
    for oid in oids:
        for desc_oid, rec in desc_oids:
            if desc_oid == oid and rec.faces == 1:
                texels = None
                for idx, srec, heap in iter_textures(data, parsed):
                    if srec.raw == rec.raw:
                        _w, _h, rgba, _n = decode_level(srec, heap, 0, srec.heap_offset)
                        return bytes(rgba) + struct.pack(">II", srec.width, srec.height)
    raise MeshExportError("could not bind a 2D texture for this material; pass --texture")


def export_glb(
    data: bytes,
    output: Path,
    *,
    record_offsets: set[int] | None,
    texture_path: Path | None,
) -> dict:
    parsed = parse_xpp(data, len(data))
    sections = find_mesh_sections(data, parsed)
    if not sections:
        raise MeshExportError(
            "no static mesh sections (skinned/character packages are not this format)"
        )
    if record_offsets:
        selected = [s for s in sections if s.record_offset in record_offsets]
        missing = record_offsets - {s.record_offset for s in selected}
        if missing:
            formatted = ", ".join(f"0x{o:x}" for o in sorted(missing))
            raise MeshExportError(f"records not found or not static mesh: {formatted}")
    elif len(sections) == 1:
        selected = sections
    else:
        raise MeshExportError(
            f"{len(sections)} static sections; pass --record-offset for each piece to assemble"
        )
    bindings = joint_bindings(data, parsed)
    if texture_path is not None:
        tw, th, rgba = read_png(texture_path)
        image_bytes = Path(texture_path).read_bytes()
    else:
        rgba_blob = _material_png(data, parsed, selected[0].material_offset)
        tw, th = struct.unpack_from(">II", rgba_blob, -8)
        rgba = rgba_blob[:-8]
        tmp = output.with_suffix(".tmp.png")
        write_png(tmp, tw, th, rgba)
        image_bytes = tmp.read_bytes()
        tmp.unlink(missing_ok=True)

    builder = GlbBuilder()
    primitives = []
    for section in selected:
        position_stride = 12 if section.split_streams else 26
        attribute_stride = 14 if section.split_streams else 26
        positions = []
        texcoords = []
        for index in range(section.vertex_count):
            xyz = struct.unpack_from(
                ">3f",
                data,
                parsed.data_offset + section.position_offset + index * position_stride,
            )
            binding = bindings.get(section.oid, bindings.get(None))
            if binding is not None:
                xyz = apply_joint(binding, xyz)
            positions.append((xyz[0], xyz[2], -xyz[1]))
            attr = parsed.data_offset + section.attribute_offset + index * attribute_stride
            texcoords.append(struct.unpack_from(">2e", data, attr + 8))
        indices = struct.unpack_from(
            f">{section.triangle_count * 3}H",
            data,
            parsed.data_offset + section.index_offset,
        )
        position_bytes = b"".join(struct.pack("<3f", *value) for value in positions)
        texcoord_bytes = b"".join(struct.pack("<2f", *value) for value in texcoords)
        index_bytes = struct.pack(f"<{len(indices)}H", *indices)
        pmin = [min(value[axis] for value in positions) for axis in range(3)]
        pmax = [max(value[axis] for value in positions) for axis in range(3)]
        primitives.append(
            {
                "attributes": {
                    "POSITION": builder.add_accessor(
                        position_bytes, 5126, len(positions), "VEC3", 34962, pmin, pmax
                    ),
                    "TEXCOORD_0": builder.add_accessor(
                        texcoord_bytes, 5126, len(texcoords), "VEC2", 34962
                    ),
                },
                "indices": builder.add_accessor(index_bytes, 5123, len(indices), "SCALAR", 34963),
                "material": 0,
                "mode": 4,
            }
        )
    image_view = builder.add_view(image_bytes)
    document = {
        "asset": {"version": "2.0", "generator": "if1-tex"},
        "extensionsUsed": ["KHR_materials_unlit"],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": primitives}],
        "materials": [
            {
                "doubleSided": True,
                "extensions": {"KHR_materials_unlit": {}},
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": 0},
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            }
        ],
        "textures": [{"sampler": 0, "source": 0}],
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
        "images": [{"bufferView": image_view, "mimeType": "image/png"}],
        "buffers": [{"byteLength": len(builder.binary)}],
        "bufferViews": builder.views,
        "accessors": builder.accessors,
    }
    json_bytes = json.dumps(document, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    json_bytes += b" " * (-len(json_bytes) & 3)
    while len(builder.binary) & 3:
        builder.binary.append(0)
    total = 12 + 8 + len(json_bytes) + 8 + len(builder.binary)
    glb = (
        struct.pack("<III", 0x46546C67, 2, total)
        + struct.pack("<II", len(json_bytes), 0x4E4F534A)
        + json_bytes
        + struct.pack("<II", len(builder.binary), 0x004E4942)
        + builder.binary
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(glb)
    return {
        "sections": len(selected),
        "recordOffsets": [s.record_offset for s in selected],
        "vertices": sum(s.vertex_count for s in selected),
        "triangles": sum(s.triangle_count for s in selected),
        "output": str(output),
    }
