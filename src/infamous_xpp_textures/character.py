"""Fail-closed character topology and external NIF compatibility reports."""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections import Counter
from dataclasses import asdict, dataclass

from .xpp import XppFile, parse_xpp


OBJECT_CHUNK = 0x01100000
GEOMETRY_HEAP_CHUNK = 0x0B800000
ENVELOPE_WORDS = 15
VERTEX_COUNT_RELATIVE_OFFSETS = (0x54, 0x58, 0x5C, 0x60)
DESCRIPTOR_SPAN_WORDS = frozenset(
    {0x00680000, 0x00700000, 0x00740000, 0x007C0000, 0x00800000}
)
FIRST_VERTEX_DESCRIPTOR_WORDS = frozenset({0x01430020, 0x01630020})
PACKED_VERTEX_DESCRIPTOR_WORDS = (
    FIRST_VERTEX_DESCRIPTOR_WORDS,
    frozenset({0x02430038, 0x02430044}),
    frozenset({0x03430050, 0x0343005C, 0x03440050, 0x0344005C, 0x0363005C}),
)
NIF_HEADER = re.compile(rb"^Gamebryo File Format, Version (\d+)\.(\d+)\.(\d+)\.(\d+)\n$")
NIF_MAX_BLOCKS = 1_000_000
NIF_MAX_STRINGS = 1_000_000
NIF_MAX_STRING_BYTES = 16 * 1024 * 1024


class CharacterReportError(ValueError):
    """Raised when character evidence is malformed or ambiguous."""


@dataclass(frozen=True)
class EdgeGeometryEnvelope:
    record_offset: int
    stream_offsets: tuple[int, ...]
    packed_stream_words: tuple[int, int]
    index_offset: int
    metadata_words: tuple[int, int, int]


@dataclass(frozen=True)
class PackedVertexStreamContract:
    envelope_stream_index: int
    stream_offset: int
    descriptor_word: int
    component_count: int
    component_bit_widths: tuple[int, int, int, int]
    bits_per_vertex: int
    logical_byte_count: int
    aligned_byte_count: int
    bit_order: str
    tail_padding_bit_count: int
    parameter_offset: int
    parameter_byte_count: int
    parameter_sha256: str
    stream_sha256: str


@dataclass(frozen=True)
class SkinnedGeometryContract:
    record_offset: int
    triangle_count: int
    index_offset: int
    index_byte_count: int
    index_count: int
    index_sha256: str
    vertex_count: int
    vertex_count_field_offset: int
    descriptor_span_word: int
    first_vertex_descriptor_word: int
    packed_vertex_streams: tuple[PackedVertexStreamContract, ...]


class _Reader:
    def __init__(self, data: bytes, offset: int = 0, endian: str = "<") -> None:
        self.data = data
        self.offset = offset
        self.endian = endian

    def take(self, count: int, label: str) -> bytes:
        if count < 0 or self.offset + count > len(self.data):
            raise CharacterReportError(f"truncated NIF {label}")
        value = self.data[self.offset : self.offset + count]
        self.offset += count
        return value

    def u8(self, label: str) -> int:
        return self.take(1, label)[0]

    def u16(self, label: str) -> int:
        return struct.unpack(self.endian + "H", self.take(2, label))[0]

    def u32(self, label: str) -> int:
        return struct.unpack(self.endian + "I", self.take(4, label))[0]

    def i32(self, label: str) -> int:
        return struct.unpack(self.endian + "i", self.take(4, label))[0]

    def sized_string(self, label: str) -> str:
        length = self.u32(f"{label} length")
        if length > NIF_MAX_STRING_BYTES:
            raise CharacterReportError(f"NIF {label} is unreasonably large")
        raw = self.take(length, label)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CharacterReportError(f"NIF {label} is not UTF-8") from error

    def export_string(self, label: str) -> bytes:
        length = self.u8(f"{label} length")
        raw = self.take(length, label)
        if raw and raw[-1] != 0:
            raise CharacterReportError(f"NIF {label} lacks its null terminator")
        return raw[:-1] if raw else raw


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _contains(start: int, size: int, address: int, length: int = 1) -> bool:
    return length >= 0 and start <= address and address + length <= start + size


def find_edge_geometry_envelopes(
    data: bytes, parsed: XppFile
) -> list[EdgeGeometryEnvelope]:
    """Recognize paired character geometry pointers without naming semantics."""

    heaps = [chunk for chunk in parsed.chunks if chunk.type_tag == GEOMETRY_HEAP_CHUNK]
    if len(heaps) != 1:
        raise CharacterReportError(
            f"expected one 0x{GEOMETRY_HEAP_CHUNK:08x} geometry heap, found {len(heaps)}"
        )
    heap = heaps[0]
    found: list[EdgeGeometryEnvelope] = []
    for chunk in parsed.chunks:
        if chunk.type_tag != OBJECT_CHUNK or chunk.size < ENVELOPE_WORDS * 4:
            continue
        for relative in range(0, chunk.size - ENVELOPE_WORDS * 4 + 1, 4):
            record_offset = chunk.offset + relative
            words = struct.unpack_from(
                ">15I", data, parsed.data_offset + record_offset
            )
            if not all(
                words[index] == words[index + 1]
                and _contains(heap.offset, heap.size, words[index], 2)
                for index in (0, 2, 4, 10)
            ):
                continue
            if words[6] != words[7] or (
                words[6] != 0 and not _contains(heap.offset, heap.size, words[6], 2)
            ):
                continue
            if (
                not words[8]
                or not words[9]
                or words[8] & 0xFFFF
                or words[9] & 0xFFFF
            ):
                continue
            descriptor_prefix = words[14] >> 16
            if words[14] & 0xFFFF != 0x000C or not 1 <= descriptor_prefix <= 16:
                continue
            streams = tuple(words[index] for index in (0, 2, 4, 6) if words[index])
            if len(streams) not in (3, 4) or len(set(streams)) != len(streams):
                continue
            if words[10] in streams:
                continue
            found.append(
                EdgeGeometryEnvelope(
                    record_offset=record_offset,
                    stream_offsets=streams,
                    packed_stream_words=(words[8], words[9]),
                    index_offset=words[10],
                    metadata_words=(words[12], words[13], words[14]),
                )
            )
    return found


def unpack_packed_components_msb(
    data: bytes,
    component_bit_widths: tuple[int, int, int, int],
    vertex_count: int,
) -> tuple[tuple[int, int, int, int], ...]:
    """Unpack proven MSB-first integers while deliberately withholding semantics."""

    if vertex_count < 0 or not all(0 <= width <= 16 for width in component_bit_widths):
        raise CharacterReportError("invalid packed-stream count or component width")
    bits_per_vertex = sum(component_bit_widths)
    expected_bits = vertex_count * bits_per_vertex
    expected_bytes = (expected_bits + 7) // 8
    if len(data) != expected_bytes:
        raise CharacterReportError(
            f"expected {expected_bytes} packed bytes, found {len(data)}"
        )
    tail_bits = (-expected_bits) % 8
    if tail_bits and data[-1] & ((1 << tail_bits) - 1):
        raise CharacterReportError("nonzero low-order MSB tail padding")
    bit_offset = 0
    unpacked: list[tuple[int, int, int, int]] = []
    for _ in range(vertex_count):
        components: list[int] = []
        for width in component_bit_widths:
            value = 0
            for _ in range(width):
                byte = data[bit_offset // 8]
                shift = 7 - bit_offset % 8
                value = (value << 1) | ((byte >> shift) & 1)
                bit_offset += 1
            components.append(value)
        unpacked.append(tuple(components))
    return tuple(unpacked)


def _descriptor_vertex_count(
    data: bytes,
    parsed: XppFile,
    envelope: EdgeGeometryEnvelope,
    expected_vertex_count: int,
) -> tuple[int, int, int] | None:
    matches: list[tuple[int, int, int]] = []
    for relative in VERTEX_COUNT_RELATIVE_OFFSETS:
        start = parsed.data_offset + envelope.record_offset + relative
        if start < 0 or start + 7 * 4 > len(data):
            continue
        count, one, zero0, zero1, span_word, zero2, format_word = struct.unpack_from(
            ">7I", data, start
        )
        if (
            count == expected_vertex_count
            and one == 1
            and zero0 == zero1 == zero2 == 0
            and span_word in DESCRIPTOR_SPAN_WORDS
            and format_word in FIRST_VERTEX_DESCRIPTOR_WORDS
        ):
            matches.append((relative, span_word, format_word))
    return matches[0] if len(matches) == 1 else None


def _packed_vertex_stream_contracts(
    data: bytes,
    parsed: XppFile,
    heap_offset: int,
    heap_size: int,
    envelope: EdgeGeometryEnvelope,
    vertex_count_field_offset: int,
    vertex_count: int,
    descriptor_span_word: int,
    known_heap_offsets: set[int],
) -> tuple[PackedVertexStreamContract, ...] | None:
    if len(envelope.stream_offsets) != 4:
        return None
    descriptor_start = parsed.data_offset + vertex_count_field_offset + 6 * 4
    if descriptor_start < 0 or descriptor_start + 6 * 4 > len(data):
        return None
    words = struct.unpack_from(">6I", data, descriptor_start)
    parameter_offsets = tuple(words[index] & 0xFFFF for index in (0, 2, 4))
    contracts: list[PackedVertexStreamContract] = []
    for stream_index in range(1, 4):
        descriptor_word = words[(stream_index - 1) * 2]
        width_word = words[(stream_index - 1) * 2 + 1]
        if descriptor_word not in PACKED_VERTEX_DESCRIPTOR_WORDS[stream_index - 1]:
            return None
        widths = tuple(width_word.to_bytes(4, "big"))
        if not all(0 <= width <= 16 for width in widths) or not all(widths[:3]):
            return None
        descriptor_tag = (descriptor_word >> 16) & 0xFF
        parameter_kind = descriptor_tag >> 4
        component_count = descriptor_tag & 0x0F
        if parameter_kind not in (4, 6) or component_count not in (3, 4):
            return None
        if any(widths[component_count:]):
            return None
        next_parameter_offset = (
            parameter_offsets[stream_index]
            if stream_index < 3
            else descriptor_span_word >> 16
        )
        parameter_byte_count = next_parameter_offset - parameter_offsets[stream_index - 1]
        if parameter_byte_count != component_count * parameter_kind * 2:
            return None
        descriptor_base = vertex_count_field_offset + 0x10
        parameter_offset = descriptor_base + parameter_offsets[stream_index - 1]
        parameter_start = parsed.data_offset + parameter_offset
        parameter_end = parameter_start + parameter_byte_count
        if parameter_start < 0 or parameter_end > len(data):
            return None
        parameter_bytes = data[parameter_start:parameter_end]
        float_byte_count = component_count * 2 * 4
        floats = struct.unpack(
            f">{float_byte_count // 4}f", parameter_bytes[:float_byte_count]
        )
        if not all(math.isfinite(value) for value in floats):
            return None
        bits_per_vertex = sum(widths)
        logical_byte_count = (vertex_count * bits_per_vertex + 7) // 8
        aligned_byte_count = (logical_byte_count + 15) & ~15
        stream_offset = envelope.stream_offsets[stream_index]
        if not _contains(
            heap_offset, heap_size, stream_offset, aligned_byte_count
        ) or any(
            stream_offset < other < stream_offset + aligned_byte_count
            for other in known_heap_offsets
        ):
            return None
        start = parsed.data_offset + stream_offset
        logical = data[start : start + logical_byte_count]
        padding = data[start + logical_byte_count : start + aligned_byte_count]
        if len(logical) != logical_byte_count or any(padding):
            return None
        try:
            unpack_packed_components_msb(logical, widths, vertex_count)
        except CharacterReportError:
            return None
        contracts.append(
            PackedVertexStreamContract(
                envelope_stream_index=stream_index,
                stream_offset=stream_offset,
                descriptor_word=descriptor_word,
                component_count=component_count,
                component_bit_widths=widths,
                bits_per_vertex=bits_per_vertex,
                logical_byte_count=logical_byte_count,
                aligned_byte_count=aligned_byte_count,
                bit_order="msb-first",
                tail_padding_bit_count=(-(vertex_count * bits_per_vertex)) % 8,
                parameter_offset=parameter_offset,
                parameter_byte_count=parameter_byte_count,
                parameter_sha256=_sha256(parameter_bytes),
                stream_sha256=_sha256(logical),
            )
        )
    return tuple(contracts)


def find_skinned_geometry_contracts(
    data: bytes, parsed: XppFile
) -> list[SkinnedGeometryContract]:
    heaps = [chunk for chunk in parsed.chunks if chunk.type_tag == GEOMETRY_HEAP_CHUNK]
    if len(heaps) != 1:
        raise CharacterReportError(
            f"expected one 0x{GEOMETRY_HEAP_CHUNK:08x} geometry heap, found {len(heaps)}"
        )
    heap = heaps[0]
    envelopes = find_edge_geometry_envelopes(data, parsed)
    known_heap_offsets = {
        offset
        for envelope in envelopes
        for offset in (*envelope.stream_offsets, envelope.index_offset)
    }
    contracts: list[SkinnedGeometryContract] = []
    for envelope in envelopes:
        triangle_count = envelope.packed_stream_words[0] >> 16
        index_byte_count = envelope.packed_stream_words[1] >> 16
        if index_byte_count != triangle_count * 3 * 2 or not _contains(
            heap.offset, heap.size, envelope.index_offset, index_byte_count
        ):
            continue
        start = parsed.data_offset + envelope.index_offset
        index_bytes = data[start : start + index_byte_count]
        if len(index_bytes) != index_byte_count or not index_bytes:
            continue
        indices = struct.unpack(f">{index_byte_count // 2}H", index_bytes)
        vertex_count = max(indices) + 1
        if len(set(indices)) != vertex_count:
            continue
        descriptor = _descriptor_vertex_count(data, parsed, envelope, vertex_count)
        if descriptor is None:
            continue
        relative, span_word, format_word = descriptor
        vertex_count_field_offset = envelope.record_offset + relative
        packed_streams = _packed_vertex_stream_contracts(
            data,
            parsed,
            heap.offset,
            heap.size,
            envelope,
            vertex_count_field_offset,
            vertex_count,
            span_word,
            known_heap_offsets,
        )
        if packed_streams is None:
            continue
        contracts.append(
            SkinnedGeometryContract(
                record_offset=envelope.record_offset,
                triangle_count=triangle_count,
                index_offset=envelope.index_offset,
                index_byte_count=index_byte_count,
                index_count=len(indices),
                index_sha256=_sha256(index_bytes),
                vertex_count=vertex_count,
                vertex_count_field_offset=vertex_count_field_offset,
                descriptor_span_word=span_word,
                first_vertex_descriptor_word=format_word,
                packed_vertex_streams=packed_streams,
            )
        )
    return contracts


def build_xpp_character_report(data: bytes, source_name: str) -> dict:
    parsed = parse_xpp(data, len(data))
    envelopes = find_edge_geometry_envelopes(data, parsed)
    contracts = find_skinned_geometry_contracts(data, parsed)
    chunk_types = Counter(f"0x{chunk.type_tag:08x}" for chunk in parsed.chunks)
    topology_proved = bool(envelopes) and len(contracts) == len(envelopes)
    return {
        "format": "infamous-xpp-character-report",
        "version": 1,
        "source": source_name,
        "source_sha256": _sha256(data),
        "source_size": len(data),
        "xpp_version": parsed.version,
        "chunk_type_counts": dict(sorted(chunk_types.items())),
        "geometry_envelope_count": len(envelopes),
        "skinned_geometry_contract_count": len(contracts),
        "contract_coverage": f"{len(contracts)}/{len(envelopes)}",
        "triangle_count": sum(item.triangle_count for item in contracts),
        "index_count": sum(item.index_count for item in contracts),
        "descriptor_local_vertex_count": sum(item.vertex_count for item in contracts),
        "packed_stream_count": sum(len(item.packed_vertex_streams) for item in contracts),
        "topology_proved": topology_proved,
        "contracts": [asdict(item) for item in contracts],
        "gates": {
            "triangle_topology": topology_proved,
            "packed_integer_extents": topology_proved,
            "stream_zero_numeric_reconstruction": False,
            "packed_stream_semantics": False,
            "mesh_local_joint_palette": False,
            "skin_weights": False,
            "hierarchy_binding": False,
            "inverse_bind_direction": False,
            "material_binding": False,
            "runtime_visibility": False,
        },
        "export_authorized": False,
        "injection_authorized": False,
        "limitations": (
            "triangle topology and three MSB-first packed stream extents only; "
            "positions, normals, UVs, weights, joint palettes, hierarchy binding, "
            "inverse binds, materials, wrapper ownership, and visibility are not decoded"
        ),
    }


def _nif_block_hash(data: bytes, offset: int, size: int) -> str:
    return _sha256(data[offset : offset + size])


def build_nif_report(data: bytes, source_name: str) -> dict:
    newline = data.find(b"\n", 0, 256)
    if newline < 0:
        raise CharacterReportError("NIF header line is absent or too long")
    header = data[: newline + 1]
    match = NIF_HEADER.fullmatch(header)
    if match is None:
        raise CharacterReportError("unsupported NIF header string")
    reader = _Reader(data, newline + 1, "<")
    version_word = reader.u32("version")
    version = tuple(int(item) for item in match.groups())
    expected_version_word = (
        version[0] << 24 | version[1] << 16 | version[2] << 8 | version[3]
    )
    if version_word != expected_version_word:
        raise CharacterReportError("NIF header and binary version disagree")
    endian_type = reader.u8("endian type")
    if endian_type != 1:
        raise CharacterReportError("only little-endian modern NIFs are supported")
    user_version = reader.u32("user version")
    block_count = reader.u32("block count")
    if not 0 < block_count <= NIF_MAX_BLOCKS:
        raise CharacterReportError("NIF block count is invalid")
    if user_version != 12:
        raise CharacterReportError(
            f"external NIF user version {user_version} is not the supported Bethesda stream"
        )
    bs_version = reader.u32("Bethesda stream version")
    if bs_version not in {130, 132, 139, 155}:
        raise CharacterReportError(
            f"Bethesda stream version {bs_version} is not a supported Fallout 4/76 NIF"
        )
    reader.export_string("author")
    if bs_version > 130:
        reader.u32("Bethesda unknown header integer")
    if bs_version < 131:
        reader.export_string("process script")
    reader.export_string("export script")
    if 103 <= bs_version < 170:
        reader.export_string("maximum filepath")

    block_type_count = reader.u16("block type count")
    if not 0 < block_type_count <= 4096:
        raise CharacterReportError("NIF block type count is invalid")
    block_types = [
        reader.sized_string(f"block type {index}") for index in range(block_type_count)
    ]
    if len(set(block_types)) != len(block_types) or any(not item for item in block_types):
        raise CharacterReportError("NIF block type table is empty or duplicated")
    block_type_indices = [reader.u16(f"block type index {index}") for index in range(block_count)]
    if any(index >= block_type_count for index in block_type_indices):
        raise CharacterReportError("NIF block type index is out of range")
    block_sizes = [reader.u32(f"block size {index}") for index in range(block_count)]
    string_count = reader.u32("string count")
    maximum_string_length = reader.u32("maximum string length")
    if string_count > NIF_MAX_STRINGS or maximum_string_length > NIF_MAX_STRING_BYTES:
        raise CharacterReportError("NIF string table limits are unreasonable")
    strings = [reader.sized_string(f"string {index}") for index in range(string_count)]
    if any(len(item.encode("utf-8")) > maximum_string_length for item in strings):
        raise CharacterReportError("NIF maximum string length is understated")
    group_count = reader.u32("group count")
    if group_count > block_count:
        raise CharacterReportError("NIF group count exceeds block count")
    groups = [reader.u32(f"group {index}") for index in range(group_count)]
    del groups

    block_table_end = reader.offset
    block_offsets: list[int] = []
    cursor = block_table_end
    for index, size in enumerate(block_sizes):
        if cursor + size > len(data):
            raise CharacterReportError(f"NIF block {index} exceeds the file")
        block_offsets.append(cursor)
        cursor += size
    footer = _Reader(data, cursor, "<")
    root_count = footer.u32("root count")
    if root_count > block_count:
        raise CharacterReportError("NIF root count exceeds block count")
    roots = [footer.i32(f"root {index}") for index in range(root_count)]
    if any(root < 0 or root >= block_count for root in roots) or footer.offset != len(data):
        raise CharacterReportError("NIF footer roots are invalid or trailing bytes remain")

    resolved_types = [block_types[index] for index in block_type_indices]
    type_counts = Counter(resolved_types)
    node_names: dict[int, str] = {}
    for index, block_type in enumerate(resolved_types):
        if block_type != "NiNode" or block_sizes[index] < 4:
            continue
        name_index = struct.unpack_from("<I", data, block_offsets[index])[0]
        if name_index != 0xFFFFFFFF:
            if name_index >= len(strings):
                raise CharacterReportError("NiNode name index exceeds the string table")
            node_names[index] = strings[name_index]

    skin_bindings: list[dict] = []
    for index, block_type in enumerate(resolved_types):
        if block_type != "BSSkin::Instance":
            continue
        body = _Reader(data, block_offsets[index], "<")
        body_end = block_offsets[index] + block_sizes[index]
        skeleton_root = body.i32("skin skeleton root")
        bone_data = body.i32("skin bone data")
        bone_count = body.u32("skin bone count")
        if bone_count > block_count:
            raise CharacterReportError("NIF skin bone count exceeds block count")
        bones = [body.i32(f"skin bone {bone}") for bone in range(bone_count)]
        scale_count = body.u32("skin scale count")
        body.take(scale_count * 12, "skin scales")
        if body.offset != body_end:
            raise CharacterReportError("NIF skin instance size does not close")
        if not (0 <= skeleton_root < block_count) or resolved_types[skeleton_root] != "NiNode":
            raise CharacterReportError("NIF skin skeleton root is not a NiNode")
        if not (
            0 <= bone_data < block_count
            and resolved_types[bone_data] == "BSSkin::BoneData"
        ):
            raise CharacterReportError("NIF skin data does not reference BSSkin::BoneData")
        if len(set(bones)) != len(bones) or any(
            bone < 0 or bone >= block_count or resolved_types[bone] != "NiNode"
            for bone in bones
        ):
            raise CharacterReportError("NIF skin bone references are invalid")
        data_offset = block_offsets[bone_data]
        data_size = block_sizes[bone_data]
        if data_size < 4:
            raise CharacterReportError("NIF bone-data block is truncated")
        data_count = struct.unpack_from("<I", data, data_offset)[0]
        if data_count != bone_count or data_size != 4 + data_count * 68:
            raise CharacterReportError("NIF bone-data count or extent disagrees with its skin")
        names = [node_names.get(bone, "") for bone in bones]
        if any(not name for name in names):
            raise CharacterReportError("NIF skin references an unnamed NiNode")
        bone_bytes = b"".join(struct.pack("<i", bone) for bone in bones)
        name_bytes = b"\0".join(name.encode("utf-8") for name in names)
        skin_bindings.append(
            {
                "instance_block": index,
                "instance_sha256": _nif_block_hash(data, block_offsets[index], block_sizes[index]),
                "skeleton_root_block": skeleton_root,
                "bone_data_block": bone_data,
                "bone_data_sha256": _nif_block_hash(data, data_offset, data_size),
                "bone_count": bone_count,
                "bone_reference_sha256": _sha256(bone_bytes),
                "bone_name_sha256": _sha256(name_bytes),
                "scale_count": scale_count,
            }
        )

    material_references = sorted(
        item for item in strings if item.lower().endswith((".bgsm", ".bgem"))
    )
    texture_references = sorted(item for item in strings if item.lower().endswith(".dds"))
    shape_count = sum(
        type_counts.get(name, 0)
        for name in ("BSSubIndexTriShape", "BSTriShape", "NiTriShape")
    )
    game = {
        130: "Fallout 4",
        132: "Fallout 4 variant",
        139: "Fallout 4 variant",
        155: "Fallout 76",
    }.get(bs_version, "unknown Bethesda game")
    string_bytes = b"".join(
        struct.pack("<I", len(item.encode("utf-8"))) + item.encode("utf-8")
        for item in strings
    )
    return {
        "format": "bethesda-nif-character-report",
        "version": 1,
        "source": source_name,
        "source_sha256": _sha256(data),
        "source_size": len(data),
        "nif_version": ".".join(str(item) for item in version),
        "user_version": user_version,
        "bethesda_stream_version": bs_version,
        "recognized_game": game,
        "block_count": block_count,
        "block_type_counts": dict(sorted(type_counts.items())),
        "block_payload_bytes": sum(block_sizes),
        "string_count": string_count,
        "string_table_sha256": _sha256(string_bytes),
        "root_blocks": roots,
        "shape_count": shape_count,
        "skin_instance_count": len(skin_bindings),
        "skin_bindings": skin_bindings,
        "unique_skin_bone_node_count": len(
            {
                bone
                for binding_index, block_type in enumerate(resolved_types)
                if block_type == "BSSkin::Instance"
                for bone in _skin_bones(data, block_offsets[binding_index])
            }
        ),
        "named_node_count": len(node_names),
        "material_reference_count": len(material_references),
        "material_reference_sha256": _sha256(b"\0".join(item.encode() for item in material_references)),
        "texture_reference_count": len(texture_references),
        "texture_reference_sha256": _sha256(b"\0".join(item.encode() for item in texture_references)),
        "rigged_source_proved": bool(shape_count and skin_bindings),
        "limitations": (
            "header/block/string/footer integrity and BSSkin node/bone-data ownership only; "
            "vertex layouts, per-vertex weights, material payloads, animation behavior, and "
            "conversion to an inFAMOUS hierarchy are not decoded"
        ),
    }


def _skin_bones(data: bytes, offset: int) -> tuple[int, ...]:
    count = struct.unpack_from("<I", data, offset + 8)[0]
    return struct.unpack_from(f"<{count}i", data, offset + 12)


def build_character_compatibility_report(
    xpp_data: bytes,
    xpp_source_name: str,
    external_data: bytes | None = None,
    external_source_name: str | None = None,
) -> dict:
    target = build_xpp_character_report(xpp_data, xpp_source_name)
    external = None
    if external_data is not None:
        external = build_nif_report(
            external_data, external_source_name or "external.nif"
        )
    blockers = [
        "target stream-zero numeric reconstruction is not proved",
        "target packed-stream position/normal/UV/weight semantics are not proved",
        "target mesh-local joint palette and per-vertex skin weights are not proved",
        "target hierarchy binding and inverse-bind direction are not revalidated here",
        "target material, wrapper, LOD, and runtime-visibility ownership are not proved",
    ]
    if external is None:
        blockers.insert(0, "no external rigged source was supplied")
    elif not external["rigged_source_proved"]:
        blockers.insert(0, "external source lacks a closed shape/skin/bone ownership contract")
    blockers.append("no deterministic external-bone to target-joint mapping is proved")
    return {
        "format": "infamous-character-compatibility-report",
        "version": 1,
        "target": target,
        "external": external,
        "pipeline": [
            "validated external NIF",
            "normalized skinned glTF 2.0",
            "target-template skeleton and material mapping",
            "rebuilt target XPP pointers/streams/fixups",
            "strict semantic round-trip validation",
            "controlled runtime scene validation",
        ],
        "blockers": blockers,
        "conversion_status": "blocked-unproved-target-semantics",
        "export_authorized": False,
        "injection_authorized": False,
        "next_gate": (
            "capture and hash one complete retail decoded character vertex stream, then "
            "prove its numeric rule and semantic binding against the packed XPP record"
        ),
    }


def render_report(report: dict) -> str:
    """Canonical pretty JSON for CLI output and reproducible report files."""

    return json.dumps(report, indent=2, sort_keys=True) + "\n"
