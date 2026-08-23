"""Bounded aggregate locator for unresolved faces in a strict material GLB."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile


MAX_GLB_BYTES = 64 * 1024 * 1024
MAX_REPORT_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_VERTICES = 1_048_576
MAX_TRIANGLES = 1_048_576

_TOOL_ID = "xpp-tool.character-material-gap-locator.v1"
_GLB_HEADER = struct.Struct("<4sII")
_CHUNK_HEADER = struct.Struct("<I4s")
_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}
_COMPONENT_FORMATS = {5123: ("H", 2), 5126: ("f", 4)}
_OBSERVED_BINDINGS = {
    "exact runtime triangle subset",
    "multi-observation exact triangle union",
}
_UNOBSERVED_BINDING = "unobserved diagnostic topology only"


class MaterialGapLocatorError(ValueError):
    """Raised when a strict material GLB cannot support an aggregate gap map."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MaterialGapLocatorError(f"{label} is not a bounded integer")
    return value


def _object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise MaterialGapLocatorError(f"{label} is not an object")
    return value


def _array(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise MaterialGapLocatorError(f"{label} is not an array")
    return value


def _safe_text(value: object, label: str, *, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or "/" in value
        or "\\" in value
        or any(ord(character) < 0x20 for character in value)
    ):
        raise MaterialGapLocatorError(f"{label} is malformed")
    return value


def read_bounded_regular(path: Path, *, limit: int, label: str) -> bytes:
    """Read one immutable regular non-symlink file under a fixed byte cap."""

    if path.is_symlink() or not path.is_file():
        raise MaterialGapLocatorError(f"{label} must be a regular non-symlink file")
    size = path.stat().st_size
    if not 0 < size <= limit:
        raise MaterialGapLocatorError(f"{label} exceeds the byte bound")
    payload = path.read_bytes()
    if len(payload) != size:
        raise MaterialGapLocatorError(f"{label} changed while it was read")
    return payload


def _parse_glb(payload: bytes) -> tuple[dict, bytes]:
    if len(payload) < _GLB_HEADER.size:
        raise MaterialGapLocatorError("material GLB is truncated")
    magic, version, declared_length = _GLB_HEADER.unpack_from(payload)
    if magic != b"glTF" or version != 2 or declared_length != len(payload):
        raise MaterialGapLocatorError("material GLB header is invalid")

    offset = _GLB_HEADER.size
    chunks: list[tuple[bytes, bytes]] = []
    while offset < len(payload):
        if offset + _CHUNK_HEADER.size > len(payload):
            raise MaterialGapLocatorError("material GLB chunk header is truncated")
        length, kind = _CHUNK_HEADER.unpack_from(payload, offset)
        offset += _CHUNK_HEADER.size
        end = offset + length
        if length % 4 or end > len(payload):
            raise MaterialGapLocatorError("material GLB chunk extent is invalid")
        chunks.append((kind, payload[offset:end]))
        offset = end
    if len(chunks) != 2 or chunks[0][0] != b"JSON" or chunks[1][0] != b"BIN\x00":
        raise MaterialGapLocatorError(
            "material GLB must contain one JSON and one BIN chunk"
        )
    try:
        document = json.loads(chunks[0][1].rstrip(b" \x00"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterialGapLocatorError("material GLB JSON is invalid") from exc
    if not isinstance(document, dict):
        raise MaterialGapLocatorError("material GLB JSON root is not an object")
    if _object(document.get("asset"), "GLB asset").get("version") != "2.0":
        raise MaterialGapLocatorError("material GLB asset version is invalid")
    buffers = _array(document.get("buffers"), "GLB buffers")
    if len(buffers) != 1 or not isinstance(buffers[0], dict):
        raise MaterialGapLocatorError("material GLB must use one embedded buffer")
    if _integer(buffers[0].get("byteLength"), "GLB buffer byte count", minimum=1) > len(
        chunks[1][1]
    ):
        raise MaterialGapLocatorError("material GLB buffer exceeds the BIN chunk")
    return document, chunks[1][1]


def _accessor_rows(document: dict, binary: bytes, accessor_index: object) -> list:
    accessors = _array(document.get("accessors"), "GLB accessors")
    views = _array(document.get("bufferViews"), "GLB buffer views")
    index = _integer(accessor_index, "GLB accessor index")
    if index >= len(accessors) or not isinstance(accessors[index], dict):
        raise MaterialGapLocatorError("GLB accessor index is out of range")
    accessor = accessors[index]
    if "sparse" in accessor or accessor.get("normalized") not in (None, False):
        raise MaterialGapLocatorError("sparse or normalized accessors are unsupported")
    view_index = _integer(accessor.get("bufferView"), "GLB buffer-view index")
    if view_index >= len(views) or not isinstance(views[view_index], dict):
        raise MaterialGapLocatorError("GLB buffer-view index is out of range")
    view = views[view_index]
    if view.get("buffer") != 0:
        raise MaterialGapLocatorError("GLB accessor does not use the embedded buffer")

    value_type = accessor.get("type")
    component_type = accessor.get("componentType")
    if value_type not in _COMPONENTS or component_type not in _COMPONENT_FORMATS:
        raise MaterialGapLocatorError("GLB accessor type is unsupported")
    components = _COMPONENTS[value_type]
    format_code, component_bytes = _COMPONENT_FORMATS[component_type]
    element_bytes = components * component_bytes
    stride = view.get("byteStride", element_bytes)
    if stride != element_bytes:
        raise MaterialGapLocatorError("interleaved GLB accessors are unsupported")
    count = _integer(accessor.get("count"), "GLB accessor count", minimum=1)
    if count > MAX_VERTICES * 3:
        raise MaterialGapLocatorError("GLB accessor exceeds the row bound")
    view_start = _integer(view.get("byteOffset", 0), "GLB buffer-view offset")
    view_bytes = _integer(
        view.get("byteLength"), "GLB buffer-view byte count", minimum=1
    )
    accessor_start = view_start + _integer(
        accessor.get("byteOffset", 0), "GLB accessor offset"
    )
    accessor_end = accessor_start + count * element_bytes
    if (
        accessor_start < view_start
        or accessor_end > view_start + view_bytes
        or accessor_end > len(binary)
    ):
        raise MaterialGapLocatorError("GLB accessor exceeds its buffer view")
    unpacker = struct.Struct(f"<{components}{format_code}")
    rows = [
        unpacker.unpack_from(binary, accessor_start + row * stride)
        for row in range(count)
    ]
    if component_type == 5126 and any(
        not math.isfinite(value) for row in rows for value in row
    ):
        raise MaterialGapLocatorError("GLB accessor contains a non-finite float")
    if components == 1:
        return [row[0] for row in rows]
    return rows


def _triangles(indices: list[int], label: str) -> list[tuple[int, int, int]]:
    if len(indices) % 3 or len(indices) // 3 > MAX_TRIANGLES:
        raise MaterialGapLocatorError(f"{label} index count is invalid")
    return [tuple(indices[offset : offset + 3]) for offset in range(0, len(indices), 3)]


def _rounded(value: float) -> float:
    rounded = round(float(value), 9)
    return 0.0 if rounded == 0 else rounded


def _vector(values) -> list[float]:
    return [_rounded(value) for value in values]


def _bounds(rows: list[tuple[float, ...]]) -> tuple[list[float], list[float]]:
    if not rows:
        raise MaterialGapLocatorError("cannot locate an empty coordinate set")
    return (
        [min(row[axis] for row in rows) for axis in range(len(rows[0]))],
        [max(row[axis] for row in rows) for axis in range(len(rows[0]))],
    )


def _centroid(rows: list[tuple[float, ...]]) -> list[float]:
    return [sum(row[axis] for row in rows) / len(rows) for axis in range(len(rows[0]))]


def _normalized(
    values: list[float], full_minimum: list[float], full_maximum: list[float]
) -> list[float | None]:
    result: list[float | None] = []
    for value, minimum, maximum in zip(values, full_minimum, full_maximum):
        span = maximum - minimum
        result.append(
            None if abs(span) <= 1e-20 else _rounded((value - minimum) / span)
        )
    return result


def _extent_fraction(
    minimum: list[float],
    maximum: list[float],
    full_minimum: list[float],
    full_maximum: list[float],
) -> list[float | None]:
    result: list[float | None] = []
    for low, high, full_low, full_high in zip(
        minimum, maximum, full_minimum, full_maximum
    ):
        span = full_high - full_low
        result.append(None if abs(span) <= 1e-20 else _rounded((high - low) / span))
    return result


def _edges(triangle: tuple[int, int, int]) -> set[tuple[int, int]]:
    a, b, c = triangle
    return {tuple(sorted(pair)) for pair in ((a, b), (b, c), (c, a))}


def _component_sizes(
    triangles: list[tuple[int, int, int]], *, by_edge: bool
) -> list[int]:
    parents = list(range(len(triangles)))

    def find(value: int) -> int:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    memberships: dict[object, list[int]] = defaultdict(list)
    for index, triangle in enumerate(triangles):
        keys = _edges(triangle) if by_edge else set(triangle)
        for key in keys:
            memberships[key].append(index)
    for indices in memberships.values():
        for other in indices[1:]:
            union(indices[0], other)
    sizes: dict[int, int] = defaultdict(int)
    for index in range(len(triangles)):
        sizes[find(index)] += 1
    return sorted(sizes.values(), reverse=True)


def _triangle_area(
    triangle: tuple[int, int, int], rows: list[tuple[float, ...]]
) -> float:
    a, b, c = (rows[index] for index in triangle)
    if len(a) == 2:
        return abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
    ab = tuple(b[axis] - a[axis] for axis in range(3))
    ac = tuple(c[axis] - a[axis] for axis in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    return math.sqrt(sum(value * value for value in cross)) / 2


def _orientation_counts(
    triangles: list[tuple[int, int, int]], positions: list[tuple[float, float, float]]
) -> dict[str, int]:
    counts = {
        name: 0
        for name in (
            "positive_x",
            "negative_x",
            "positive_y",
            "negative_y",
            "positive_z",
            "negative_z",
            "degenerate",
        )
    }
    axes = "xyz"
    for triangle in triangles:
        a, b, c = (positions[index] for index in triangle)
        ab = tuple(b[axis] - a[axis] for axis in range(3))
        ac = tuple(c[axis] - a[axis] for axis in range(3))
        normal = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        if math.sqrt(sum(value * value for value in normal)) <= 1e-12:
            counts["degenerate"] += 1
            continue
        axis = max(range(3), key=lambda candidate: abs(normal[candidate]))
        counts[f"{'positive' if normal[axis] >= 0 else 'negative'}_{axes[axis]}"] += 1
    return counts


def _validate_material_report(
    report: dict,
    payload: bytes,
    report_sha256: str,
    glb_payload: bytes,
    glb_sha256: str,
) -> dict:
    if _sha256(payload) != report_sha256 or _sha256(glb_payload) != glb_sha256:
        raise MaterialGapLocatorError("an input SHA-256 pin does not match")
    if (
        report.get("format") != "infamous-character-material-export"
        or report.get("version") != 1
        or report.get("tool_inventory_id")
        != "xpp-tool.character-material-coverage-export.v1"
        or report.get("status") != "retail-material-progress-glb-written"
        or report.get("presentation_mode") != "observed-union"
        or report.get("payload_bytes_serialized_in_report") is not False
    ):
        raise MaterialGapLocatorError(
            "material report has the wrong strict-union schema"
        )
    glb = _object(report.get("glb"), "material report GLB identity")
    if glb.get("sha256") != glb_sha256 or glb.get("bytes") != len(glb_payload):
        raise MaterialGapLocatorError("material report GLB identity drifted")
    selection = _object(report.get("selection"), "material report selection")
    vertices = _integer(selection.get("vertices"), "vertex count", minimum=1)
    triangles = _integer(selection.get("triangles"), "triangle count", minimum=1)
    observed = _integer(
        selection.get("material_observed_triangles"),
        "observed triangle count",
        minimum=1,
    )
    unobserved = _integer(
        selection.get("material_unobserved_triangles"),
        "unobserved triangle count",
        minimum=1,
    )
    if (
        vertices > MAX_VERTICES
        or triangles > MAX_TRIANGLES
        or observed + unobserved != triangles
        or _object(report.get("limitations"), "material report limitations").get(
            "full_topology_material_coverage"
        )
        is not False
    ):
        raise MaterialGapLocatorError("material report counts do not reconcile")
    return {
        "record_offset": _integer(selection.get("record_offset"), "record offset"),
        "texture_family": _safe_text(selection.get("texture_family"), "texture family"),
        "vertices": vertices,
        "triangles": triangles,
        "observed": observed,
        "unobserved": unobserved,
        "index_sha256": selection.get("index_sha256"),
        "material_union_index_sha256": selection.get("material_union_index_sha256"),
    }


def locate_material_gap(
    glb_payload: bytes,
    material_report_payload: bytes,
    *,
    glb_sha256: str,
    material_report_sha256: str,
) -> dict:
    """Validate a strict GLB and return payload-free spatial/UV gap aggregates."""

    if not _valid_sha256(glb_sha256) or not _valid_sha256(material_report_sha256):
        raise MaterialGapLocatorError("an input SHA-256 pin is invalid")
    try:
        report = json.loads(material_report_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterialGapLocatorError("material report is not valid JSON") from exc
    if not isinstance(report, dict):
        raise MaterialGapLocatorError("material report root is not an object")
    selection = _validate_material_report(
        report, material_report_payload, material_report_sha256, glb_payload, glb_sha256
    )
    if not _valid_sha256(selection["index_sha256"]) or not _valid_sha256(
        selection["material_union_index_sha256"]
    ):
        raise MaterialGapLocatorError("material report topology identity is invalid")

    document, binary = _parse_glb(glb_payload)
    meshes = _array(document.get("meshes"), "GLB meshes")
    if len(meshes) != 1 or not isinstance(meshes[0], dict):
        raise MaterialGapLocatorError("strict material GLB must contain one mesh")
    primitives = _array(meshes[0].get("primitives"), "GLB primitives")
    observed_primitives = []
    unobserved_primitives = []
    for primitive in primitives:
        if not isinstance(primitive, dict) or primitive.get("mode", 4) != 4:
            raise MaterialGapLocatorError(
                "strict material GLB contains a non-triangle primitive"
            )
        binding = _object(primitive.get("extras"), "primitive extras").get(
            "materialBinding"
        )
        if binding in _OBSERVED_BINDINGS:
            observed_primitives.append(primitive)
        elif binding == _UNOBSERVED_BINDING:
            unobserved_primitives.append(primitive)
        else:
            raise MaterialGapLocatorError(
                "strict material GLB has an unknown primitive role"
            )
    if len(observed_primitives) != 1 or len(unobserved_primitives) != 1:
        raise MaterialGapLocatorError(
            "strict material GLB must have one observed and one gap primitive"
        )

    observed_primitive = observed_primitives[0]
    gap_primitive = unobserved_primitives[0]
    observed_attributes = _object(
        observed_primitive.get("attributes"), "observed attributes"
    )
    gap_attributes = _object(gap_primitive.get("attributes"), "gap attributes")
    for semantic in ("POSITION", "TEXCOORD_0"):
        if (
            semantic not in observed_attributes
            or gap_attributes.get(semantic) != observed_attributes[semantic]
        ):
            raise MaterialGapLocatorError(
                "strict primitives do not share position and UV accessors"
            )

    positions = _accessor_rows(document, binary, observed_attributes["POSITION"])
    uvs = _accessor_rows(document, binary, observed_attributes["TEXCOORD_0"])
    observed_indices = _accessor_rows(
        document, binary, observed_primitive.get("indices")
    )
    gap_indices = _accessor_rows(document, binary, gap_primitive.get("indices"))
    if len(positions) != selection["vertices"] or len(uvs) != selection["vertices"]:
        raise MaterialGapLocatorError("strict material GLB vertex count drifted")
    if any(
        not isinstance(index, int) or index >= len(positions)
        for index in observed_indices + gap_indices
    ):
        raise MaterialGapLocatorError("strict material GLB index is out of range")
    observed_triangles = _triangles(observed_indices, "observed")
    gap_triangles = _triangles(gap_indices, "unobserved")
    if (
        len(observed_triangles) != selection["observed"]
        or len(gap_triangles) != selection["unobserved"]
    ):
        raise MaterialGapLocatorError("strict material GLB triangle counts drifted")

    evidence = _object(
        _object(document.get("asset"), "GLB asset").get("extras"), "GLB asset extras"
    ).get("infamousMaterialEvidence")
    evidence = _object(evidence, "GLB material evidence")
    if (
        evidence.get("recordOffset") != selection["record_offset"]
        or evidence.get("observedMaterialTriangles") != selection["observed"]
        or evidence.get("unobservedMaterialTriangles") != selection["unobserved"]
        or evidence.get("positionSemanticProved") is not False
        or evidence.get("uvProved") is not True
        or evidence.get("coverageUnionRevalidated") is not True
    ):
        raise MaterialGapLocatorError("GLB material evidence drifted from the report")

    full_minimum, full_maximum = _bounds(positions)
    gap_vertex_indices = sorted(
        {index for triangle in gap_triangles for index in triangle}
    )
    observed_vertex_indices = {
        index for triangle in observed_triangles for index in triangle
    }
    gap_positions = [positions[index] for index in gap_vertex_indices]
    gap_uvs = [uvs[index] for index in gap_vertex_indices]
    gap_position_minimum, gap_position_maximum = _bounds(gap_positions)
    full_uv_minimum, full_uv_maximum = _bounds(uvs)
    gap_uv_minimum, gap_uv_maximum = _bounds(gap_uvs)
    gap_position_centroid = _centroid(gap_positions)
    gap_uv_centroid = _centroid(gap_uvs)
    gap_triangle_centroids = [
        tuple(
            sum(positions[index][axis] for index in triangle) / 3 for axis in range(3)
        )
        for triangle in gap_triangles
    ]
    gap_triangle_minimum, gap_triangle_maximum = _bounds(gap_triangle_centroids)

    observed_edges = set().union(*(_edges(triangle) for triangle in observed_triangles))
    gap_edges = set().union(*(_edges(triangle) for triangle in gap_triangles))
    observed_area = sum(
        _triangle_area(triangle, positions) for triangle in observed_triangles
    )
    gap_area = sum(_triangle_area(triangle, positions) for triangle in gap_triangles)
    observed_uv_area = sum(
        _triangle_area(triangle, uvs) for triangle in observed_triangles
    )
    gap_uv_area = sum(_triangle_area(triangle, uvs) for triangle in gap_triangles)
    total_area = observed_area + gap_area
    total_uv_area = observed_uv_area + gap_uv_area
    if total_area <= 1e-20:
        raise MaterialGapLocatorError(
            "strict material GLB has no diagnostic surface area"
        )

    report = {
        "format": "infamous-character-material-gap-location",
        "version": 1,
        "tool_inventory_id": _TOOL_ID,
        "status": "unobserved-material-faces-located",
        "authorities": {
            "material_glb_bytes": len(glb_payload),
            "material_glb_sha256": glb_sha256,
            "material_report_bytes": len(material_report_payload),
            "material_report_sha256": material_report_sha256,
        },
        "component": {
            "record_offset": selection["record_offset"],
            "texture_family": selection["texture_family"],
            "vertices": selection["vertices"],
            "retail_triangle_occurrences": selection["triangles"],
            "observed_material_triangle_occurrences": selection["observed"],
            "unobserved_material_triangle_occurrences": selection["unobserved"],
            "retail_index_sha256": selection["index_sha256"],
            "observed_union_index_sha256": selection["material_union_index_sha256"],
        },
        "gap": {
            "unique_vertices": len(gap_vertex_indices),
            "vertices_also_used_by_observed_faces": len(
                set(gap_vertex_indices) & observed_vertex_indices
            ),
            "vertices_not_used_by_observed_faces": len(
                set(gap_vertex_indices) - observed_vertex_indices
            ),
            "connectivity": {
                "edge_connected_components": len(
                    _component_sizes(gap_triangles, by_edge=True)
                ),
                "edge_component_triangle_counts": _component_sizes(
                    gap_triangles, by_edge=True
                ),
                "vertex_connected_components": len(
                    _component_sizes(gap_triangles, by_edge=False)
                ),
                "vertex_component_triangle_counts": _component_sizes(
                    gap_triangles, by_edge=False
                ),
                "shared_boundary_edges_with_observed": len(gap_edges & observed_edges),
                "shared_vertices_with_observed": len(
                    set(gap_vertex_indices) & observed_vertex_indices
                ),
                "gap_faces_sharing_an_edge_with_observed": sum(
                    bool(_edges(triangle) & observed_edges)
                    for triangle in gap_triangles
                ),
                "gap_faces_sharing_a_vertex_with_observed": sum(
                    bool(set(triangle) & observed_vertex_indices)
                    for triangle in gap_triangles
                ),
            },
            "diagnostic_position": {
                "coordinate_semantic_proved": False,
                "full_bounds": {
                    "minimum": _vector(full_minimum),
                    "maximum": _vector(full_maximum),
                },
                "gap_vertex_bounds": {
                    "minimum": _vector(gap_position_minimum),
                    "maximum": _vector(gap_position_maximum),
                },
                "gap_triangle_centroid_bounds": {
                    "minimum": _vector(gap_triangle_minimum),
                    "maximum": _vector(gap_triangle_maximum),
                },
                "gap_vertex_centroid": _vector(gap_position_centroid),
                "normalized_gap_minimum": _normalized(
                    gap_position_minimum, full_minimum, full_maximum
                ),
                "normalized_gap_maximum": _normalized(
                    gap_position_maximum, full_minimum, full_maximum
                ),
                "normalized_gap_centroid": _normalized(
                    gap_position_centroid, full_minimum, full_maximum
                ),
                "gap_extent_fraction": _extent_fraction(
                    gap_position_minimum,
                    gap_position_maximum,
                    full_minimum,
                    full_maximum,
                ),
                "gap_surface_area_fraction": _rounded(gap_area / total_area),
                "dominant_triangle_orientation": _orientation_counts(
                    gap_triangles, positions
                ),
            },
            "uv": {
                "uv_binding_proved": True,
                "full_bounds": {
                    "minimum": _vector(full_uv_minimum),
                    "maximum": _vector(full_uv_maximum),
                },
                "gap_vertex_bounds": {
                    "minimum": _vector(gap_uv_minimum),
                    "maximum": _vector(gap_uv_maximum),
                },
                "gap_vertex_centroid": _vector(gap_uv_centroid),
                "normalized_gap_minimum": _normalized(
                    gap_uv_minimum, full_uv_minimum, full_uv_maximum
                ),
                "normalized_gap_maximum": _normalized(
                    gap_uv_maximum, full_uv_minimum, full_uv_maximum
                ),
                "normalized_gap_centroid": _normalized(
                    gap_uv_centroid, full_uv_minimum, full_uv_maximum
                ),
                "gap_extent_fraction": _extent_fraction(
                    gap_uv_minimum,
                    gap_uv_maximum,
                    full_uv_minimum,
                    full_uv_maximum,
                ),
                "gap_triangle_area_fraction": (
                    None
                    if total_uv_area <= 1e-20
                    else _rounded(gap_uv_area / total_uv_area)
                ),
            },
        },
        "proof": {
            "strict_material_report_revalidated": True,
            "glb_identity_revalidated": True,
            "one_observed_and_one_unobserved_primitive": True,
            "shared_position_and_uv_accessors": True,
            "triangle_and_vertex_bounds_reconciled": True,
            "payload_lists_withheld": True,
            "deterministic_aggregate_report": True,
        },
        "limitations": {
            "raw_vertex_positions_serialized": False,
            "raw_uv_rows_serialized": False,
            "raw_triangle_indices_serialized": False,
            "position_semantic_proved": False,
            "camera_direction_proved": False,
            "runtime_state_or_pass_selected": False,
            "material_assignment_closed": False,
            "retail_normals_tangents_proved": False,
            "rigged": False,
            "four_x_textures": False,
            "authored_pbr": False,
            "rpcs3_mod_round_trip": False,
            "native_decomp_import": False,
        },
        "next_gate": (
            "combine these aggregate clusters with exact runtime pass history; a new "
            "camera angle alone does not prove that the game will issue a different "
            "index/material draw"
        ),
    }
    if len(render_material_gap_location(report)) > MAX_OUTPUT_BYTES:
        raise MaterialGapLocatorError(
            "material-gap location report exceeds the byte bound"
        )
    return report


def render_material_gap_location(report: dict) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_new_material_gap_location(path: Path, report: dict) -> None:
    """Atomically publish a deterministic report without replacing evidence."""

    if path.is_symlink() or path.exists():
        raise MaterialGapLocatorError("material-gap location output already exists")
    payload = render_material_gap_location(report)
    if len(payload) > MAX_OUTPUT_BYTES:
        raise MaterialGapLocatorError(
            "material-gap location report exceeds the byte bound"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise MaterialGapLocatorError(
                "material-gap location output appeared during publication"
            )
        os.link(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
