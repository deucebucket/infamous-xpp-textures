"""Strict same-page assembly of checksum-pinned character material GLBs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Sequence

from .character_source_export import _pack_glb
from .component_ledger import (
    CharacterComponentLedgerError,
    _load_json,
    _read_pinned,
    _safe_token,
    _validate_material_report,
)
from .material_gap_locator import (
    MaterialGapLocatorError,
    _accessor_rows,
    _parse_glb,
)


MAX_COMPONENTS = 32
MAX_RUNTIME_PAGE = 17
MAX_MATERIAL_REPORT_BYTES = 1024 * 1024
MAX_COMPONENT_GLB_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_GLB_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_REPORT_BYTES = 1024 * 1024
MAX_TOTAL_VERTICES = 2_000_000
MAX_TOTAL_TRIANGLES = 2_000_000

_TOOL_ID = "xpp-tool.character-material-assembly-export.v1"
_OBSERVED_BINDINGS = {
    "exact runtime triangle subset",
    "runtime-observed exact triangle subset",
    "multi-observation exact triangle union",
}
_GAP_BINDING = "unobserved diagnostic topology only"
_MATERIAL_TEXTURE_PATHS = (
    ("pbrMetallicRoughness", "baseColorTexture"),
    ("pbrMetallicRoughness", "metallicRoughnessTexture"),
    ("normalTexture",),
    ("occlusionTexture",),
    ("emissiveTexture",),
)


class MaterialAssemblyError(ValueError):
    """Raised when exact material components cannot be assembled safely."""


@dataclass(frozen=True)
class MaterialAssemblyInput:
    """One checksum-pinned report/GLB pair."""

    report: Path
    report_sha256: str
    glb: Path
    glb_sha256: str


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MaterialAssemblyError(f"{label} is not a bounded integer")
    return value


def _object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise MaterialAssemblyError(f"{label} is not an object")
    return value


def _array(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise MaterialAssemblyError(f"{label} is not an array")
    return value


def _finite_vector(value: object, label: str, length: int) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != length
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(item)
            or abs(item) > 1_000_000_000.0
            for item in value
        )
    ):
        raise MaterialAssemblyError(f"{label} is not a bounded finite vector")
    return [float(item) for item in value]


def _same_vector(first: Sequence[float], second: Sequence[float]) -> bool:
    return len(first) == len(second) and all(
        math.isclose(left, right, rel_tol=0.0, abs_tol=1e-6)
        for left, right in zip(first, second, strict=True)
    )


def _rounded(value: float) -> float:
    result = round(float(value), 9)
    return 0.0 if result == 0 else result


def _vector(values: Sequence[float]) -> list[float]:
    return [_rounded(value) for value in values]


def _glb_local_bounds(
    source_minimum: Sequence[float],
    source_maximum: Sequence[float],
    center: Sequence[float],
) -> tuple[list[float], list[float]]:
    """Mirror the material exporter's source XYZ -> glTF X,Z,-Y transform."""

    return (
        [
            source_minimum[0] - center[0],
            source_minimum[2] - center[2],
            -(source_maximum[1] - center[1]),
        ],
        [
            source_maximum[0] - center[0],
            source_maximum[2] - center[2],
            -(source_minimum[1] - center[1]),
        ],
    )


def _glb_translation(
    center: Sequence[float], assembly_center: Sequence[float]
) -> list[float]:
    return [
        center[0] - assembly_center[0],
        center[2] - assembly_center[2],
        -(center[1] - assembly_center[1]),
    ]


def _material_texture_indices(material: dict, texture_count: int) -> None:
    extensions = material.get("extensions", {})
    if not isinstance(extensions, dict) or set(extensions) - {"KHR_materials_unlit"}:
        raise MaterialAssemblyError("material uses an unsupported extension")
    for path in _MATERIAL_TEXTURE_PATHS:
        current: object = material
        for key in path:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if current is None:
            continue
        texture_info = _object(current, "material texture info")
        index = _integer(texture_info.get("index"), "material texture index")
        if index >= texture_count:
            raise MaterialAssemblyError("material texture index is out of range")


def _remap_material(material: dict, texture_base: int, texture_count: int) -> dict:
    result = deepcopy(material)
    _material_texture_indices(result, texture_count)
    for path in _MATERIAL_TEXTURE_PATHS:
        current: object = result
        for key in path:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if current is not None:
            current["index"] += texture_base
    return result


def _validate_report_frame(raw: dict) -> tuple[list[float], list[float], list[float]]:
    bounds = _object(raw.get("source_position_bounds"), "source position bounds")
    minimum = _finite_vector(bounds.get("minimum"), "source position minimum", 3)
    maximum = _finite_vector(bounds.get("maximum"), "source position maximum", 3)
    center = _finite_vector(raw.get("recentered_position_center"), "recenter center", 3)
    if any(low > high for low, high in zip(minimum, maximum, strict=True)):
        raise MaterialAssemblyError("source position bounds are inverted")
    expected_center = [
        (low + high) / 2.0 for low, high in zip(minimum, maximum, strict=True)
    ]
    if not _same_vector(center, expected_center):
        raise MaterialAssemblyError("recenter center contradicts source bounds")
    limitations = _object(raw.get("limitations"), "material limitations")
    if (
        limitations.get("position_attribute_is_diagnostic_hypothesis") is not True
        or limitations.get("position_semantic") is not False
        or limitations.get("generated_inspection_normals_are_retail_normals")
        is not False
        or raw.get("payload_bytes_serialized_in_report") is not False
    ):
        raise MaterialAssemblyError(
            "material report overclaims its coordinate evidence"
        )
    return minimum, maximum, center


def _validate_glb(
    payload: bytes,
    normalized: dict,
    raw: dict,
    source_minimum: Sequence[float],
    source_maximum: Sequence[float],
    center: Sequence[float],
) -> tuple[dict, bytes]:
    try:
        document, binary = _parse_glb(payload)
    except MaterialGapLocatorError as exc:
        raise MaterialAssemblyError(str(exc)) from exc
    if document.get("scene") != 0 or document.get("extensionsRequired") is not None:
        raise MaterialAssemblyError(
            "component GLB scene or required extension is unsupported"
        )
    extensions_used = document.get("extensionsUsed", [])
    if not isinstance(extensions_used, list) or set(extensions_used) - {
        "KHR_materials_unlit"
    }:
        raise MaterialAssemblyError("component GLB uses an unsupported extension")
    if any(key in document for key in ("animations", "skins", "cameras", "extensions")):
        raise MaterialAssemblyError(
            "component GLB contains unsupported runtime structure"
        )

    scenes = _array(document.get("scenes"), "component scenes")
    nodes = _array(document.get("nodes"), "component nodes")
    meshes = _array(document.get("meshes"), "component meshes")
    if (
        len(scenes) != 1
        or scenes[0].get("nodes") != [0]
        or len(nodes) != 1
        or len(meshes) != 1
    ):
        raise MaterialAssemblyError(
            "component GLB must contain one scene, node, and mesh"
        )
    node = _object(nodes[0], "component node")
    if node.get("mesh") != 0 or any(
        key in node
        for key in ("matrix", "translation", "rotation", "scale", "children")
    ):
        raise MaterialAssemblyError(
            "component node transform is not the expected origin"
        )

    views = _array(document.get("bufferViews"), "component buffer views")
    accessors = _array(document.get("accessors"), "component accessors")
    samplers = _array(document.get("samplers"), "component samplers")
    images = _array(document.get("images"), "component images")
    textures = _array(document.get("textures"), "component textures")
    materials = _array(document.get("materials"), "component materials")
    buffers = _array(document.get("buffers"), "component buffers")
    if len(buffers) != 1:
        raise MaterialAssemblyError("component GLB must contain one embedded buffer")
    declared_binary_bytes = _integer(
        _object(buffers[0], "component buffer").get("byteLength"),
        "component buffer byte count",
        minimum=1,
    )
    if declared_binary_bytes > len(binary):
        raise MaterialAssemblyError("component GLB binary extent is invalid")
    for view in views:
        view = _object(view, "component buffer view")
        if view.get("buffer") != 0:
            raise MaterialAssemblyError("component buffer view is not embedded")
        start = _integer(view.get("byteOffset", 0), "buffer-view offset")
        length = _integer(view.get("byteLength"), "buffer-view byte count", minimum=1)
        if start + length > declared_binary_bytes:
            raise MaterialAssemblyError("component buffer view exceeds its buffer")

    if (
        not samplers
        or len(images) != len(textures)
        or len(textures) != len(normalized["textures"])
    ):
        raise MaterialAssemblyError(
            "component texture/image counts drifted from report"
        )
    report_names = {row["name"] for row in normalized["textures"]}
    image_names: set[str] = set()
    for image in images:
        image = _object(image, "component image")
        if image.get("mimeType") != "image/png" or "uri" in image:
            raise MaterialAssemblyError("component image is not embedded PNG data")
        view_index = _integer(image.get("bufferView"), "component image buffer view")
        if view_index >= len(views) or not isinstance(image.get("name"), str):
            raise MaterialAssemblyError("component image reference is invalid")
        image_names.add(image["name"])
    texture_names: set[str] = set()
    for texture in textures:
        texture = _object(texture, "component texture")
        source = _integer(texture.get("source"), "component texture source")
        sampler = _integer(texture.get("sampler"), "component texture sampler")
        if (
            source >= len(images)
            or sampler >= len(samplers)
            or not isinstance(texture.get("name"), str)
        ):
            raise MaterialAssemblyError("component texture reference is invalid")
        if texture["name"] != images[source].get("name"):
            raise MaterialAssemblyError("component texture/image names diverge")
        texture_names.add(texture["name"])
    if image_names != report_names or texture_names != report_names:
        raise MaterialAssemblyError("component embedded image identities drifted")
    for material in materials:
        _material_texture_indices(
            _object(material, "component material"), len(textures)
        )

    primitives = _array(
        _object(meshes[0], "component mesh").get("primitives"), "mesh primitives"
    )
    observed: list[dict] = []
    gaps: list[dict] = []
    position_accessor: int | None = None
    normal_accessor: int | None = None
    uv_accessor: int | None = None
    for primitive in primitives:
        primitive = _object(primitive, "component primitive")
        if primitive.get("mode", 4) != 4 or "targets" in primitive:
            raise MaterialAssemblyError(
                "component primitive is not a static triangle list"
            )
        attributes = _object(primitive.get("attributes"), "component attributes")
        if set(attributes) != {"POSITION", "NORMAL", "TEXCOORD_0"}:
            raise MaterialAssemblyError("component attribute set is unsupported")
        current_position = _integer(attributes["POSITION"], "position accessor")
        current_normal = _integer(attributes["NORMAL"], "normal accessor")
        current_uv = _integer(attributes["TEXCOORD_0"], "UV accessor")
        if any(
            index >= len(accessors)
            for index in (current_position, current_normal, current_uv)
        ):
            raise MaterialAssemblyError("component attribute accessor is out of range")
        if position_accessor is None:
            position_accessor, normal_accessor, uv_accessor = (
                current_position,
                current_normal,
                current_uv,
            )
        elif (current_position, current_normal, current_uv) != (
            position_accessor,
            normal_accessor,
            uv_accessor,
        ):
            raise MaterialAssemblyError("component primitives do not share vertex rows")
        index_accessor = _integer(primitive.get("indices"), "index accessor")
        material_index = _integer(primitive.get("material"), "material index")
        if index_accessor >= len(accessors) or material_index >= len(materials):
            raise MaterialAssemblyError("component primitive reference is out of range")
        binding = _object(primitive.get("extras"), "primitive extras").get(
            "materialBinding"
        )
        if binding in _OBSERVED_BINDINGS:
            observed.append(primitive)
        elif binding == _GAP_BINDING:
            gaps.append(primitive)
        else:
            raise MaterialAssemblyError("component primitive role is unsupported")
    if (
        position_accessor is None
        or len(observed) != 1
        or len(gaps) != int(normalized["topology"]["material_unobserved_triangles"] > 0)
    ):
        raise MaterialAssemblyError(
            "component observed/gap primitives do not reconcile"
        )

    try:
        positions = _accessor_rows(
            document,
            binary,
            position_accessor,
            expected_type="VEC3",
            expected_component_type=5126,
        )
        normals = _accessor_rows(
            document,
            binary,
            normal_accessor,
            expected_type="VEC3",
            expected_component_type=5126,
        )
        uvs = _accessor_rows(
            document,
            binary,
            uv_accessor,
            expected_type="VEC2",
            expected_component_type=5126,
        )
        observed_indices = _accessor_rows(
            document,
            binary,
            observed[0]["indices"],
            expected_type="SCALAR",
            expected_component_type=5123,
        )
        gap_indices = (
            _accessor_rows(
                document,
                binary,
                gaps[0]["indices"],
                expected_type="SCALAR",
                expected_component_type=5123,
            )
            if gaps
            else []
        )
    except MaterialGapLocatorError as exc:
        raise MaterialAssemblyError(str(exc)) from exc
    vertex_count = normalized["topology"]["vertices"]
    if (
        len(positions) != vertex_count
        or len(normals) != vertex_count
        or len(uvs) != vertex_count
    ):
        raise MaterialAssemblyError("component vertex rows drifted from report")
    if (
        len(observed_indices)
        != normalized["topology"]["material_observed_triangles"] * 3
        or len(gap_indices)
        != normalized["topology"]["material_unobserved_triangles"] * 3
        or any(index >= vertex_count for index in observed_indices + gap_indices)
    ):
        raise MaterialAssemblyError("component triangle rows drifted from report")
    actual_minimum = [min(row[axis] for row in positions) for axis in range(3)]
    actual_maximum = [max(row[axis] for row in positions) for axis in range(3)]
    expected_minimum, expected_maximum = _glb_local_bounds(
        source_minimum, source_maximum, center
    )
    if not _same_vector(actual_minimum, expected_minimum) or not _same_vector(
        actual_maximum, expected_maximum
    ):
        raise MaterialAssemblyError(
            "component GLB does not reverse its recorded recenter"
        )

    evidence = _object(
        _object(
            _object(document.get("asset"), "component asset").get("extras"),
            "asset extras",
        ).get("infamousMaterialEvidence"),
        "component material evidence",
    )
    node_evidence = _object(node.get("extras"), "component node extras").get(
        "infamousMaterialEvidence"
    )
    if node_evidence != evidence or (
        evidence.get("recordOffset") != normalized["record_offset"]
        or evidence.get("observedMaterialTriangles")
        != normalized["topology"]["material_observed_triangles"]
        or evidence.get("unobservedMaterialTriangles")
        != normalized["topology"]["material_unobserved_triangles"]
        or evidence.get("uvProved") is not True
        or evidence.get("retailTextureIdentitiesProved") is not True
        or evidence.get("positionSemanticProved") is not False
        or evidence.get("fullCharacterProved") is not False
    ):
        raise MaterialAssemblyError("component GLB evidence drifted from report")
    return document, binary[:declared_binary_bytes]


def _append_component(
    output: dict,
    binary: bytearray,
    document: dict,
    component_binary: bytes,
    *,
    translation: Sequence[float],
    record_offset: int,
    report_sha256: str,
    glb_sha256: str,
    preview_extrapolated: bool,
) -> None:
    while len(binary) & 3:
        binary.append(0)
    binary_base = len(binary)
    binary.extend(component_binary)
    view_base = len(output["bufferViews"])
    accessor_base = len(output["accessors"])
    sampler_base = len(output["samplers"])
    image_base = len(output["images"])
    texture_base = len(output["textures"])
    material_base = len(output["materials"])
    mesh_base = len(output["meshes"])

    for source in document["bufferViews"]:
        view = deepcopy(source)
        view["buffer"] = 0
        view["byteOffset"] = binary_base + _integer(
            view.get("byteOffset", 0), "buffer-view offset"
        )
        output["bufferViews"].append(view)
    for source in document["accessors"]:
        accessor = deepcopy(source)
        accessor["bufferView"] = (
            _integer(accessor.get("bufferView"), "accessor buffer view") + view_base
        )
        output["accessors"].append(accessor)
    output["samplers"].extend(deepcopy(document["samplers"]))
    for source in document["images"]:
        image = deepcopy(source)
        image["bufferView"] = (
            _integer(image.get("bufferView"), "image buffer view") + view_base
        )
        output["images"].append(image)
    for source in document["textures"]:
        texture = deepcopy(source)
        texture["sampler"] = (
            _integer(texture.get("sampler"), "texture sampler") + sampler_base
        )
        texture["source"] = (
            _integer(texture.get("source"), "texture source") + image_base
        )
        output["textures"].append(texture)
    output["materials"].extend(
        _remap_material(material, texture_base, len(document["textures"]))
        for material in document["materials"]
    )
    mesh = deepcopy(document["meshes"][0])
    observed_materials = {
        primitive["material"]
        for primitive in mesh["primitives"]
        if primitive["extras"]["materialBinding"] in _OBSERVED_BINDINGS
    }
    if preview_extrapolated and len(observed_materials) != 1:
        raise MaterialAssemblyError(
            "selective preview requires exactly one observed component material"
        )
    preview_material = next(iter(observed_materials)) if preview_extrapolated else None
    for primitive in mesh["primitives"]:
        if (
            preview_extrapolated
            and primitive["extras"]["materialBinding"] == _GAP_BINDING
        ):
            primitive["material"] = preview_material
            primitive["extras"]["materialBinding"] = (
                "selective assembly preview extrapolation over unresolved topology"
            )
        primitive["indices"] += accessor_base
        primitive["material"] += material_base
        primitive["attributes"] = {
            semantic: accessor + accessor_base
            for semantic, accessor in primitive["attributes"].items()
        }
    output["meshes"].append(mesh)
    original_node = document["nodes"][0]
    extras = deepcopy(original_node.get("extras", {}))
    extras["infamousAssemblyEvidence"] = {
        "recordOffset": record_offset,
        "materialReportSha256": report_sha256,
        "materialGlbSha256": glb_sha256,
        "translationOnlyFromRecordedRecenter": True,
        "selectivePreviewExtrapolated": preview_extrapolated,
    }
    output["nodes"].append(
        {
            "mesh": mesh_base,
            "name": original_node.get("name", f"record {record_offset}"),
            "translation": _vector(translation),
            "extras": extras,
        }
    )


def build_character_material_assembly(
    inputs: Sequence[MaterialAssemblyInput],
    *,
    title_id: str,
    build_id: str,
    candidate_id: str,
    page: int,
    preview_records: Sequence[int] = (),
) -> tuple[bytes, dict]:
    """Restore recorded recenter translations and combine exact same-page GLBs."""

    try:
        title_id = _safe_token(title_id, "title ID")
        build_id = _safe_token(build_id, "build ID")
        candidate_id = _safe_token(candidate_id, "candidate ID")
    except CharacterComponentLedgerError as exc:
        raise MaterialAssemblyError(str(exc)) from exc
    page = _integer(page, "runtime page", minimum=1)
    if page > MAX_RUNTIME_PAGE or not 2 <= len(inputs) <= MAX_COMPONENTS:
        raise MaterialAssemblyError(
            "runtime page or component count is outside its bound"
        )
    normalized_preview_records = []
    for value in preview_records:
        normalized_preview_records.append(
            _integer(value, "selective preview source record", minimum=1)
        )
    if len(set(normalized_preview_records)) != len(normalized_preview_records):
        raise MaterialAssemblyError("selective preview source record is duplicated")
    preview_record_set = set(normalized_preview_records)

    loaded: list[dict] = []
    seen_paths: set[Path] = set()
    seen_records: set[int] = set()
    total_input_bytes = 0
    for item in inputs:
        paths = (item.report.resolve(), item.glb.resolve())
        if paths[0] == paths[1] or any(path in seen_paths for path in paths):
            raise MaterialAssemblyError("component input path is duplicated")
        seen_paths.update(paths)
        try:
            report_payload = _read_pinned(
                item.report,
                item.report_sha256,
                MAX_MATERIAL_REPORT_BYTES,
                "material report",
            )
            glb_payload = _read_pinned(
                item.glb, item.glb_sha256, MAX_COMPONENT_GLB_BYTES, "material GLB"
            )
            raw = _load_json(report_payload, "material report")
            normalized = _validate_material_report(raw, item.report_sha256)
        except CharacterComponentLedgerError as exc:
            raise MaterialAssemblyError(str(exc)) from exc
        if normalized["page"] != page:
            raise MaterialAssemblyError(
                "material components do not share the requested page"
            )
        if normalized["record_offset"] in seen_records:
            raise MaterialAssemblyError("material components repeat a source record")
        seen_records.add(normalized["record_offset"])
        if normalized["glb"] != {
            "bytes": len(glb_payload),
            "sha256": item.glb_sha256,
        }:
            raise MaterialAssemblyError(
                "material report GLB identity does not match input"
            )
        source_minimum, source_maximum, center = _validate_report_frame(raw)
        document, component_binary = _validate_glb(
            glb_payload,
            normalized,
            raw,
            source_minimum,
            source_maximum,
            center,
        )
        total_input_bytes += len(report_payload) + len(glb_payload)
        loaded.append(
            {
                "normalized": normalized,
                "report_bytes": len(report_payload),
                "report_sha256": item.report_sha256,
                "glb_bytes": len(glb_payload),
                "glb_sha256": item.glb_sha256,
                "source_minimum": source_minimum,
                "source_maximum": source_maximum,
                "center": center,
                "document": document,
                "binary": component_binary,
            }
        )
    loaded.sort(key=lambda row: row["normalized"]["record_offset"])
    loaded_records = {row["normalized"]["record_offset"] for row in loaded}
    if preview_record_set - loaded_records:
        raise MaterialAssemblyError(
            "selective preview source record is not an admitted component"
        )
    for row in loaded:
        normalized = row["normalized"]
        if (
            normalized["record_offset"] in preview_record_set
            and normalized["topology"]["material_unobserved_triangles"] == 0
        ):
            raise MaterialAssemblyError(
                "selective preview source record has no unresolved material faces"
            )
    total_vertices = sum(row["normalized"]["topology"]["vertices"] for row in loaded)
    total_triangles = sum(row["normalized"]["topology"]["triangles"] for row in loaded)
    if total_vertices > MAX_TOTAL_VERTICES or total_triangles > MAX_TOTAL_TRIANGLES:
        raise MaterialAssemblyError("assembled topology exceeds its bound")

    source_minimum = [
        min(row["source_minimum"][axis] for row in loaded) for axis in range(3)
    ]
    source_maximum = [
        max(row["source_maximum"][axis] for row in loaded) for axis in range(3)
    ]
    assembly_center = [
        (low + high) / 2.0
        for low, high in zip(source_minimum, source_maximum, strict=True)
    ]
    assembled_minimum, assembled_maximum = _glb_local_bounds(
        source_minimum, source_maximum, assembly_center
    )
    evidence = {
        "diagnosticOnly": True,
        "sameRuntimePageProved": True,
        "componentCount": len(loaded),
        "selectivePreviewExtrapolatedRecordCount": len(preview_record_set),
        "strictMaterialProofRetained": True,
        "translationOnlyFromRecordedRecenter": True,
        "positionSemanticProved": False,
        "generatedInspectionNormals": True,
        "retailNormalsProved": False,
        "fullCharacterProved": False,
        "nativePbrProved": False,
        "rpcs3RoundTripProved": False,
        "nativeImportProved": False,
    }
    document = {
        "asset": {
            "version": "2.0",
            "generator": "xpp-tool character material assembly exporter",
            "extras": {"infamousMaterialAssemblyEvidence": evidence},
        },
        "scene": 0,
        "scenes": [
            {
                "name": f"{candidate_id} page {page} material assembly diagnostic",
                "nodes": list(range(len(loaded))),
                "extras": {"infamousMaterialAssemblyEvidence": evidence},
            }
        ],
        "nodes": [],
        "meshes": [],
        "materials": [],
        "textures": [],
        "images": [],
        "samplers": [],
        "bufferViews": [],
        "accessors": [],
    }
    binary = bytearray()
    components: list[dict] = []
    extensions_used: set[str] = set()
    for row in loaded:
        normalized = row["normalized"]
        translation = _glb_translation(row["center"], assembly_center)
        _append_component(
            document,
            binary,
            row["document"],
            row["binary"],
            translation=translation,
            record_offset=normalized["record_offset"],
            report_sha256=row["report_sha256"],
            glb_sha256=row["glb_sha256"],
            preview_extrapolated=normalized["record_offset"] in preview_record_set,
        )
        extensions_used.update(row["document"].get("extensionsUsed", []))
        components.append(
            {
                "record_offset": normalized["record_offset"],
                "texture_family": normalized["texture_family"],
                "event": normalized["event"],
                "draw_event": normalized["draw_event"],
                "vertices": normalized["topology"]["vertices"],
                "retail_triangle_occurrences": normalized["topology"]["triangles"],
                "material_observed_triangle_occurrences": normalized["topology"][
                    "material_observed_triangles"
                ],
                "material_unobserved_triangle_occurrences": normalized["topology"][
                    "material_unobserved_triangles"
                ],
                "presentation_preview_extrapolated": (
                    normalized["record_offset"] in preview_record_set
                ),
                "presentation_extrapolated_triangle_occurrences": (
                    normalized["topology"]["material_unobserved_triangles"]
                    if normalized["record_offset"] in preview_record_set
                    else 0
                ),
                "translation": _vector(translation),
                "material_report": {
                    "bytes": row["report_bytes"],
                    "sha256": row["report_sha256"],
                },
                "material_glb": {
                    "bytes": row["glb_bytes"],
                    "sha256": row["glb_sha256"],
                },
            }
        )
    if extensions_used:
        document["extensionsUsed"] = sorted(extensions_used)
    glb = _pack_glb(document, binary)
    if len(glb) > MAX_OUTPUT_GLB_BYTES:
        raise MaterialAssemblyError("assembled GLB exceeds the output bound")
    report = {
        "format": "infamous-character-material-assembly-export",
        "version": 1,
        "tool_inventory_id": _TOOL_ID,
        "status": "same-page-relative-material-assembly-glb-written",
        "scope": {
            "title_id": title_id,
            "build_id": build_id,
            "candidate_id": candidate_id,
            "runtime_page": page,
        },
        "authorities": {
            "component_count": len(loaded),
            "total_input_bytes": total_input_bytes,
            "components": components,
        },
        "assembly": {
            "vertices": total_vertices,
            "retail_triangle_occurrences": total_triangles,
            "material_observed_triangle_occurrences": sum(
                row["normalized"]["topology"]["material_observed_triangles"]
                for row in loaded
            ),
            "material_unobserved_triangle_occurrences": sum(
                row["normalized"]["topology"]["material_unobserved_triangles"]
                for row in loaded
            ),
            "diagnostic_bounds": {
                "minimum": _vector(assembled_minimum),
                "maximum": _vector(assembled_maximum),
                "dimensions": _vector(
                    [
                        high - low
                        for low, high in zip(
                            assembled_minimum, assembled_maximum, strict=True
                        )
                    ]
                ),
            },
        },
        "presentation": {
            "mode": (
                "selective-preview" if preview_record_set else "strict-observed-only"
            ),
            "preview_extrapolated_record_offsets": sorted(preview_record_set),
            "preview_extrapolated_triangle_occurrences": sum(
                row["normalized"]["topology"]["material_unobserved_triangles"]
                for row in loaded
                if row["normalized"]["record_offset"] in preview_record_set
            ),
            "strict_material_observation_counts_preserved": True,
            "preview_is_runtime_material_proof": False,
        },
        "glb": {"bytes": len(glb), "sha256": _sha256(glb)},
        "proof": {
            "all_input_sha256_pins_revalidated": True,
            "all_report_glb_identities_revalidated": True,
            "one_runtime_page_proved": True,
            "unique_source_records_proved": True,
            "component_recenter_reversed_exactly": True,
            "relative_translation_uses_only_reported_centers": True,
            "mesh_topology_material_resources_textures_and_images_preserved": True,
            "strict_material_assignments_preserved": not preview_record_set,
            "selective_preview_extrapolation_declared": bool(preview_record_set),
            "deterministic_editable_glb": True,
            "payload_bytes_serialized_in_report": False,
        },
        "limitations": {
            "position_semantic_proved": False,
            "original_object_space_proved": False,
            "retail_normals_tangents_proved": False,
            "missing_components_recovered": False,
            "all_material_faces_proved": False,
            "full_character": False,
            "rigged": False,
            "four_x_textures": False,
            "authored_pbr": False,
            "rpcs3_mod_round_trip": False,
            "native_decomp_import": False,
        },
        "bounds": {
            "minimum_components": 2,
            "maximum_components": MAX_COMPONENTS,
            "maximum_runtime_page": MAX_RUNTIME_PAGE,
            "maximum_component_report_bytes": MAX_MATERIAL_REPORT_BYTES,
            "maximum_component_glb_bytes": MAX_COMPONENT_GLB_BYTES,
            "maximum_output_glb_bytes": MAX_OUTPUT_GLB_BYTES,
            "maximum_output_report_bytes": MAX_OUTPUT_REPORT_BYTES,
            "maximum_total_vertices": MAX_TOTAL_VERTICES,
            "maximum_total_triangles": MAX_TOTAL_TRIANGLES,
            "single_process": True,
            "network_access": False,
            "new_output_only": True,
        },
        "next_gate": (
            "render and inspect this same-page relative placement, then add missing "
            "source components and independently prove original position semantics, "
            "retail normals/tangents, rigging, complete materials, and mod round trip"
        ),
    }
    if len(render_material_assembly_report(report)) > MAX_OUTPUT_REPORT_BYTES:
        raise MaterialAssemblyError("assembly report exceeds the output bound")
    return glb, report


def render_material_assembly_report(report: dict) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _stage_new(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def write_new_material_assembly(
    glb_path: Path, report_path: Path, glb: bytes, report: dict
) -> None:
    """Atomically publish the GLB/report pair without replacing either output."""

    if glb_path.resolve() == report_path.resolve():
        raise MaterialAssemblyError("assembly GLB and report destinations must differ")
    for path in (glb_path, report_path):
        if path.is_symlink() or path.exists():
            raise MaterialAssemblyError("assembly output already exists")
    report_payload = render_material_assembly_report(report)
    if (
        len(glb) > MAX_OUTPUT_GLB_BYTES
        or len(report_payload) > MAX_OUTPUT_REPORT_BYTES
        or report.get("glb") != {"bytes": len(glb), "sha256": _sha256(glb)}
    ):
        raise MaterialAssemblyError("assembly output does not match its receipt")
    staged: list[Path] = []
    published: list[Path] = []
    try:
        staged.append(_stage_new(glb_path, glb))
        staged.append(_stage_new(report_path, report_payload))
        for temporary, destination in zip(staged, (glb_path, report_path), strict=True):
            if destination.exists():
                raise MaterialAssemblyError(
                    "assembly output appeared during publication"
                )
            os.link(temporary, destination)
            published.append(destination)
    except BaseException:
        for destination in published:
            destination.unlink(missing_ok=True)
        raise
    finally:
        for temporary in staged:
            temporary.unlink(missing_ok=True)
