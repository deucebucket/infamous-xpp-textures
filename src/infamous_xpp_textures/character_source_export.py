"""Diagnostic GLB export from one exact packed XPP character stream."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile

from .character import find_skinned_geometry_contracts, unpack_packed_components_msb
from .mesh import GlbBuilder
from .xpp import parse_xpp


NUMERIC_FAMILIES = (
    "endpoint-unsigned",
    "offset-scale-unsigned",
    "scale-offset-unsigned",
)
MAX_XPP_SOURCE_BYTES = 64 * 1024 * 1024
MAX_DIAGNOSTIC_GLB_BYTES = 64 * 1024 * 1024
_MAX_FLOAT32 = 3.4028234663852886e38


class CharacterSourceExportError(ValueError):
    """Raised when a packed-source diagnostic cannot be proved safe."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_new_atomic(destination: Path, payload: bytes) -> None:
    if destination.is_symlink() or destination.exists():
        raise CharacterSourceExportError(
            "diagnostic output already exists; refusing to overwrite it"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
        os.unlink(temporary)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _pack_glb(document: dict, binary: bytearray) -> bytes:
    document["buffers"] = [{"byteLength": len(binary)}]
    json_bytes = json.dumps(document, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )
    json_bytes += b" " * (-len(json_bytes) & 3)
    while len(binary) & 3:
        binary.append(0)
    total = 12 + 8 + len(json_bytes) + 8 + len(binary)
    return (
        struct.pack("<III", 0x46546C67, 2, total)
        + struct.pack("<II", len(json_bytes), 0x4E4F534A)
        + json_bytes
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


def _parameter_vectors(
    data: bytes,
    payload_offset: int,
    parameter_offset: int,
    parameter_byte_count: int,
    component_count: int,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    prefix_size = component_count * 2 * 4
    if parameter_byte_count < prefix_size:
        raise CharacterSourceExportError(
            "parameter block lacks its finite vector prefix"
        )
    start = payload_offset + parameter_offset
    end = start + prefix_size
    if start < 0 or end > len(data):
        raise CharacterSourceExportError("parameter vectors leave the package")
    values = struct.unpack(f">{component_count * 2}f", data[start:end])
    if not all(math.isfinite(value) for value in values):
        raise CharacterSourceExportError("parameter vectors contain nonfinite values")
    return tuple(values[:component_count]), tuple(values[component_count:])


def _decode_hypothesis(
    rows: tuple[tuple[int, int, int, int], ...],
    widths: tuple[int, int, int, int],
    first: tuple[float, ...],
    second: tuple[float, ...],
    numeric_family: str,
) -> tuple[tuple[float, float, float], ...]:
    if numeric_family not in NUMERIC_FAMILIES:
        raise CharacterSourceExportError(
            f"unsupported numeric family {numeric_family!r}"
        )
    if len(first) != 3 or len(second) != 3 or any(width <= 0 for width in widths[:3]):
        raise CharacterSourceExportError(
            "diagnostic GLB requires three nonzero packed components"
        )
    decoded = []
    for row in rows:
        output = []
        for index in range(3):
            integer = row[index]
            maximum = (1 << widths[index]) - 1
            if not 0 <= integer <= maximum:
                raise CharacterSourceExportError(
                    "packed integer leaves its descriptor width"
                )
            if numeric_family == "endpoint-unsigned":
                value = first[index] + (integer / maximum) * (
                    second[index] - first[index]
                )
            elif numeric_family == "offset-scale-unsigned":
                value = first[index] + integer * second[index]
            else:
                value = second[index] + integer * first[index]
            if not math.isfinite(value):
                raise CharacterSourceExportError(
                    "numeric hypothesis produced a nonfinite value"
                )
            output.append(value)
        decoded.append((output[0], output[1], output[2]))
    return tuple(decoded)


def _cross_length_squared(
    left: tuple[float, float, float],
    middle: tuple[float, float, float],
    right: tuple[float, float, float],
) -> float:
    a = tuple(middle[axis] - left[axis] for axis in range(3))
    b = tuple(right[axis] - left[axis] for axis in range(3))
    cross = (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )
    return sum(value * value for value in cross)


def export_character_source_diagnostic_glb(
    xpp_data: bytes,
    output: Path,
    *,
    record_offset: int,
    stream_index: int,
    numeric_family: str,
) -> dict:
    """Export exact topology with one explicitly unproved packed-stream hypothesis."""

    if not xpp_data or len(xpp_data) > MAX_XPP_SOURCE_BYTES:
        raise CharacterSourceExportError(
            "XPP source is empty or exceeds the 64 MiB bound"
        )
    if (
        isinstance(record_offset, bool)
        or not isinstance(record_offset, int)
        or record_offset < 0
    ):
        raise CharacterSourceExportError("record offset must be a nonnegative integer")
    if isinstance(stream_index, bool) or stream_index not in (1, 2, 3):
        raise CharacterSourceExportError("stream index must be 1, 2, or 3")

    parsed = parse_xpp(xpp_data, len(xpp_data))
    contracts = [
        item
        for item in find_skinned_geometry_contracts(xpp_data, parsed)
        if item.record_offset == record_offset
    ]
    if len(contracts) != 1:
        raise CharacterSourceExportError(
            f"record offset selects {len(contracts)} proved character contracts"
        )
    contract = contracts[0]
    streams = [
        item
        for item in contract.packed_vertex_streams
        if item.envelope_stream_index == stream_index
    ]
    if len(streams) != 1:
        raise CharacterSourceExportError(
            f"stream index selects {len(streams)} descriptor-backed streams"
        )
    stream = streams[0]
    if stream.component_count != 3 or any(stream.component_bit_widths[3:]):
        raise CharacterSourceExportError(
            "diagnostic GLB requires an exact three-component packed stream"
        )

    stream_start = parsed.data_offset + stream.stream_offset
    stream_bytes = xpp_data[stream_start : stream_start + stream.logical_byte_count]
    if (
        len(stream_bytes) != stream.logical_byte_count
        or _sha256(stream_bytes) != stream.stream_sha256
    ):
        raise CharacterSourceExportError("packed stream failed exact identity")
    unpacked = unpack_packed_components_msb(
        stream_bytes, stream.component_bit_widths, contract.vertex_count
    )
    first, second = _parameter_vectors(
        xpp_data,
        parsed.data_offset,
        stream.parameter_offset,
        stream.parameter_byte_count,
        stream.component_count,
    )
    source_positions = _decode_hypothesis(
        unpacked,
        stream.component_bit_widths,
        first,
        second,
        numeric_family,
    )
    if any(abs(value) > _MAX_FLOAT32 for row in source_positions for value in row):
        raise CharacterSourceExportError(
            "numeric hypothesis exceeds finite float32 GLB coordinates"
        )

    index_start = parsed.data_offset + contract.index_offset
    index_bytes_be = xpp_data[index_start : index_start + contract.index_byte_count]
    if (
        len(index_bytes_be) != contract.index_byte_count
        or _sha256(index_bytes_be) != contract.index_sha256
    ):
        raise CharacterSourceExportError("triangle index stream failed exact identity")
    indices = struct.unpack(f">{contract.index_count}H", index_bytes_be)
    if not indices or len(indices) % 3 or max(indices) >= contract.vertex_count:
        raise CharacterSourceExportError("triangle index topology is invalid")

    nondegenerate = sum(
        _cross_length_squared(
            source_positions[indices[offset]],
            source_positions[indices[offset + 1]],
            source_positions[indices[offset + 2]],
        )
        > 1e-12
        for offset in range(0, len(indices), 3)
    )
    if nondegenerate == 0:
        raise CharacterSourceExportError(
            "numeric hypothesis makes every triangle degenerate"
        )

    source_min = [min(row[axis] for row in source_positions) for axis in range(3)]
    source_max = [max(row[axis] for row in source_positions) for axis in range(3)]
    center = [(source_min[axis] + source_max[axis]) / 2.0 for axis in range(3)]
    positions = [
        (row[0] - center[0], row[2] - center[2], -(row[1] - center[1]))
        for row in source_positions
    ]
    position_min = [min(row[axis] for row in positions) for axis in range(3)]
    position_max = [max(row[axis] for row in positions) for axis in range(3)]

    builder = GlbBuilder()
    position_bytes = b"".join(struct.pack("<3f", *row) for row in positions)
    index_bytes = struct.pack(f"<{len(indices)}H", *indices)
    position_accessor = builder.add_accessor(
        position_bytes,
        5126,
        contract.vertex_count,
        "VEC3",
        34962,
        position_min,
        position_max,
    )
    index_accessor = builder.add_accessor(
        index_bytes, 5123, len(indices), "SCALAR", 34963
    )
    evidence = {
        "diagnosticOnly": True,
        "recordOffset": contract.record_offset,
        "packedStreamIndex": stream.envelope_stream_index,
        "numericFamily": f"{numeric_family}-hypothesis",
        "topologyProved": True,
        "packedStreamIdentityProved": True,
        "positionSemanticProved": False,
        "rigged": False,
        "uvProved": False,
        "materialProved": False,
        "injectionAuthorized": False,
    }
    document = {
        "asset": {
            "version": "2.0",
            "generator": "xpp-tool 2.20.0 packed-source diagnostic exporter",
            "extras": {"infamousDiagnostic": evidence},
        },
        "extensionsUsed": ["KHR_materials_unlit"],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {
                "mesh": 0,
                "name": "Packed source-stream hypothesis",
                "extras": {"infamousDiagnostic": evidence},
            }
        ],
        "meshes": [
            {
                "name": "Exact topology / packed numeric hypothesis",
                "primitives": [
                    {
                        "attributes": {"POSITION": position_accessor},
                        "indices": index_accessor,
                        "material": 0,
                        "mode": 4,
                    }
                ],
            }
        ],
        "materials": [
            {
                "name": "Diagnostic source-stream clay",
                "doubleSided": True,
                "extensions": {"KHR_materials_unlit": {}},
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.95, 0.40, 0.08, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            }
        ],
        "bufferViews": builder.views,
        "accessors": builder.accessors,
    }
    glb = _pack_glb(document, builder.binary)
    if len(glb) > MAX_DIAGNOSTIC_GLB_BYTES:
        raise CharacterSourceExportError(
            "diagnostic GLB exceeds the 64 MiB output bound"
        )
    _write_new_atomic(output, glb)
    return {
        "format": "infamous-character-packed-source-diagnostic",
        "version": 1,
        "status": "diagnostic-glb-written",
        "source_sha256": _sha256(xpp_data),
        "record_offset": contract.record_offset,
        "vertices": contract.vertex_count,
        "triangles": contract.triangle_count,
        "nondegenerate_triangles": nondegenerate,
        "stream_index": stream.envelope_stream_index,
        "stream_sha256": stream.stream_sha256,
        "component_bit_widths": list(stream.component_bit_widths),
        "parameter_sha256": stream.parameter_sha256,
        "numeric_family": numeric_family,
        "position_semantic_proved": False,
        "source_bounds_min": source_min,
        "source_bounds_max": source_max,
        "source_bounds_center": center,
        "recentered_for_inspection": True,
        "index_sha256": contract.index_sha256,
        "output_sha256": _sha256(glb),
        "output_size": len(glb),
        "bounds": {
            "maximum_xpp_bytes": MAX_XPP_SOURCE_BYTES,
            "maximum_glb_bytes": MAX_DIAGNOSTIC_GLB_BYTES,
            "concurrency": 1,
            "network": False,
        },
        "gates": {
            "topology": True,
            "packed_stream_identity": True,
            "numeric_family": False,
            "position_semantic": False,
            "uv": False,
            "skin_weights": False,
            "joint_palette": False,
            "skeleton": False,
            "inverse_binds": False,
            "material": False,
            "rigged_export": False,
            "injection": False,
        },
        "claim_boundary": (
            "exact topology and packed source values; numeric family, position meaning, "
            "rigging, UVs, materials, ownership, completeness, and injection remain unproved"
        ),
    }
