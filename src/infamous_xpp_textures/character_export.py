"""Fail-closed diagnostic character-draw export for Blender inspection."""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import tempfile
from pathlib import Path

from .character import build_xpp_character_report
from .mesh import GlbBuilder
from .xpp import parse_xpp


class CharacterDiagnosticExportError(ValueError):
    """Raised when a diagnostic character export cannot be proved safe."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise CharacterDiagnosticExportError(f"{label} must be a JSON object")
    return value


def _array(value, label: str) -> list:
    if not isinstance(value, list):
        raise CharacterDiagnosticExportError(f"{label} must be a JSON array")
    return value


def _integer(value, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CharacterDiagnosticExportError(f"{label} must be an integer")
    return value


def _one(items: list, label: str):
    if len(items) != 1:
        raise CharacterDiagnosticExportError(f"expected one {label}, found {len(items)}")
    return items[0]


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


def _pack_glb(document: dict, binary: bytearray) -> bytes:
    document["buffers"] = [{"byteLength": len(binary)}]
    json_bytes = json.dumps(
        document, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
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


def _write_atomic(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def export_character_diagnostic_glb(
    xpp_data: bytes,
    binding_report: dict,
    attribute_payload: bytes,
    output: Path,
    *,
    position_hypothesis_attribute: int,
) -> dict:
    """Export one exact topology with an explicit, unproved position hypothesis."""

    position_hypothesis_attribute = _integer(
        position_hypothesis_attribute, "position hypothesis attribute"
    )
    binding_report = _object(binding_report, "binding report")
    if _integer(
        binding_report.get("draw_binding_count"), "draw binding count"
    ) != 1:
        raise CharacterDiagnosticExportError("binding report must contain exactly one draw")
    exact = _object(
        _one(
            _array(binding_report.get("exact_matches"), "exact matches"),
            "exact topology match",
        ),
        "exact topology match",
    )
    draw = _object(
        _one(
            _array(binding_report.get("draw_bindings"), "draw bindings"),
            "draw binding",
        ),
        "draw binding",
    )
    state = draw.get("rsx_draw_state")
    if not isinstance(state, dict) or state.get("rsx_vertex_binding_proved") is not True:
        raise CharacterDiagnosticExportError("RSX vertex binding is not complete")
    if state.get("status") != "complete-vertex-binding":
        raise CharacterDiagnosticExportError("RSX vertex binding status is not complete")

    target = build_xpp_character_report(xpp_data, "owned-character.xpp")
    if not target["topology_proved"]:
        raise CharacterDiagnosticExportError("XPP character topology is not proved")
    contracts = [
        item
        for item in target["contracts"]
        if item["record_offset"] == exact.get("record_offset")
        and item["index_sha256"] == exact.get("index_sha256")
    ]
    contract = _one(contracts, "matching XPP geometry contract")
    vertex_count = contract["vertex_count"]
    if (
        _integer(exact.get("vertex_count"), "exact vertex count") != vertex_count
        or _integer(exact.get("index_count"), "exact index count")
        != contract["index_count"]
        or _integer(exact.get("index_min"), "exact index minimum") != 0
        or _integer(exact.get("index_max"), "exact index maximum")
        != vertex_count - 1
    ):
        raise CharacterDiagnosticExportError("exact topology counts do not reconcile")

    parsed = parse_xpp(xpp_data, len(xpp_data))
    index_start = parsed.data_offset + contract["index_offset"]
    index_end = index_start + contract["index_byte_count"]
    index_bytes_be = xpp_data[index_start:index_end]
    if len(index_bytes_be) != contract["index_byte_count"] or _sha256(
        index_bytes_be
    ) != contract["index_sha256"]:
        raise CharacterDiagnosticExportError("XPP index extent failed exact identity")
    indices = struct.unpack(f">{contract['index_count']}H", index_bytes_be)
    if not indices or max(indices) >= vertex_count or len(indices) % 3:
        raise CharacterDiagnosticExportError("XPP triangle indices are invalid")

    vertex_arrays = [
        _object(item, "vertex array")
        for item in _array(state.get("vertex_arrays"), "vertex arrays")
    ]
    attributes = [
        item
        for item in vertex_arrays
        if _integer(item.get("attribute"), "vertex attribute number")
        == position_hypothesis_attribute
    ]
    attribute = _one(attributes, "selected position-hypothesis attribute")
    if attribute.get("binding_proved") is not True:
        raise CharacterDiagnosticExportError("selected attribute is not bound to one payload")
    if (
        attribute.get("type_raw") != 2
        or attribute.get("type_name") != "float32"
        or attribute.get("component_count") != 3
        or _integer(attribute.get("frequency"), "attribute frequency") != 0
        or _integer(attribute.get("stride"), "attribute stride") < 12
        or _integer(attribute.get("index_span"), "attribute index span")
        != vertex_count
    ):
        raise CharacterDiagnosticExportError(
            "selected attribute is not a bounded zero-frequency float32x3 stream"
        )
    block = _object(
        _one(
            _array(
                attribute.get("matching_memory_blocks"),
                "matching attribute memory blocks",
            ),
            "attribute payload binding",
        ),
        "attribute payload binding",
    )
    expected_capture_size = _integer(
        attribute.get("expected_capture_size"), "expected capture size"
    )
    if len(attribute_payload) != expected_capture_size:
        raise CharacterDiagnosticExportError("attribute payload size does not match the binding")
    payload_sha256 = _sha256(attribute_payload)
    if payload_sha256 != block.get("payload_sha256"):
        raise CharacterDiagnosticExportError("attribute payload SHA-256 does not match the binding")
    stride = _integer(attribute.get("stride"), "attribute stride")
    required_bytes = (vertex_count - 1) * stride + 12
    if required_bytes > len(attribute_payload):
        raise CharacterDiagnosticExportError("attribute payload is truncated")

    source_positions = [
        struct.unpack_from(">3f", attribute_payload, vertex * stride)
        for vertex in range(vertex_count)
    ]
    if not all(math.isfinite(value) for xyz in source_positions for value in xyz):
        raise CharacterDiagnosticExportError("position-hypothesis payload is not finite")
    if len(set(source_positions)) < 3:
        raise CharacterDiagnosticExportError("position-hypothesis payload lacks distinct vertices")
    nondegenerate = sum(
        _cross_length_squared(
            source_positions[indices[offset]],
            source_positions[indices[offset + 1]],
            source_positions[indices[offset + 2]],
        )
        > 1e-12
        for offset in range(0, len(indices), 3)
    )
    if not nondegenerate:
        raise CharacterDiagnosticExportError("position hypothesis makes every triangle degenerate")

    source_min = [min(value[axis] for value in source_positions) for axis in range(3)]
    source_max = [max(value[axis] for value in source_positions) for axis in range(3)]
    center = [(source_min[axis] + source_max[axis]) / 2.0 for axis in range(3)]
    positions = [
        (
            value[0] - center[0],
            value[2] - center[2],
            -(value[1] - center[1]),
        )
        for value in source_positions
    ]
    position_min = [min(value[axis] for value in positions) for axis in range(3)]
    position_max = [max(value[axis] for value in positions) for axis in range(3)]

    builder = GlbBuilder()
    position_bytes = b"".join(struct.pack("<3f", *value) for value in positions)
    index_bytes = struct.pack(f"<{len(indices)}H", *indices)
    position_accessor = builder.add_accessor(
        position_bytes,
        5126,
        vertex_count,
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
        "positionHypothesisAttribute": position_hypothesis_attribute,
        "positionSemanticProved": False,
        "topologyProved": True,
        "recenteredForInspection": True,
        "originalBoundsCenter": center,
        "rigged": False,
        "uvProved": False,
        "materialProved": False,
        "injectionAuthorized": False,
        "indexSha256": contract["index_sha256"],
        "attributePayloadSha256": payload_sha256,
    }
    document = {
        "asset": {
            "version": "2.0",
            "generator": "xpp-tool 2.8.0 diagnostic character exporter",
            "extras": {"infamousDiagnostic": evidence},
        },
        "extensionsUsed": ["KHR_materials_unlit"],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {
                "mesh": 0,
                "name": "Diagnostic character draw (semantics unproved)",
                "extras": {"infamousDiagnostic": evidence},
            }
        ],
        "meshes": [
            {
                "name": "Exact XPP topology with explicit position hypothesis",
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
                "name": "Diagnostic neutral material (retail material unproved)",
                "doubleSided": True,
                "extensions": {"KHR_materials_unlit": {}},
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.55, 0.68, 0.9, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            }
        ],
        "bufferViews": builder.views,
        "accessors": builder.accessors,
    }
    glb = _pack_glb(document, builder.binary)
    _write_atomic(output, glb)
    return {
        "format": "infamous-character-diagnostic-export",
        "version": 1,
        "status": "diagnostic-glb-written",
        "record_offset": contract["record_offset"],
        "vertices": vertex_count,
        "triangles": len(indices) // 3,
        "nondegenerate_triangles": nondegenerate,
        "position_hypothesis_attribute": position_hypothesis_attribute,
        "position_payload_sha256": payload_sha256,
        "index_sha256": contract["index_sha256"],
        "source_bounds_min": source_min,
        "source_bounds_max": source_max,
        "source_bounds_center": center,
        "recentered_for_inspection": True,
        "output_size": len(glb),
        "output_sha256": _sha256(glb),
        "gates": {
            "topology": True,
            "payload_identity": True,
            "finite_float3_hypothesis": True,
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
    }
