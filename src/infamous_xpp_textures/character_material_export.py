"""Deterministic GLB export for one shader-proved character material binding."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile
from collections import Counter

from .character import find_skinned_geometry_contracts
from .character_source_export import _pack_glb
from .decode import decode_level, iter_textures
from .mesh import GlbBuilder
from .pngio import encode_png
from .runtime_topology_export import (
    RuntimeTopologyExportError,
    _load_bundle,
    _paged_capture_metadata,
    _read_payload,
)
from .shader_lineage import _read_pinned_json
from .xpp import parse_xpp


class CharacterMaterialExportError(ValueError):
    """Raised when a proposed character material export is not exact."""


MAX_XPP_BYTES = 64 * 1024 * 1024
MAX_GLB_BYTES = 64 * 1024 * 1024
MAX_REPORT_BYTES = 256 * 1024
_POSITION_ATTRIBUTE = 0
_POSITION_TYPE = 2
_POSITION_COMPONENTS = 3
_COLOR_SUFFIX = "C"
_NORMAL_SUFFIX = "N"
_MAX_SHADER_TEXTURES = 8
_MATERIAL_COVERAGE_MODES = ("observed-only", "preview-full-record")
_SINGLE_EXPORT_TOOL = "xpp-tool.character-material-export.v1"
_UNION_EXPORT_TOOL = "xpp-tool.character-material-coverage-export.v1"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _integer(value, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CharacterMaterialExportError(f"{label} must be an integer")
    return value


def _object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise CharacterMaterialExportError(f"{label} must be an object")
    return value


def _array(value, label: str) -> list:
    if not isinstance(value, list):
        raise CharacterMaterialExportError(f"{label} must be an array")
    return value


def _one(values: list, label: str):
    if len(values) != 1:
        raise CharacterMaterialExportError(f"expected one {label}, found {len(values)}")
    return values[0]


def _cross(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _inspection_normals(
    positions: list[tuple[float, float, float]], indices: tuple[int, ...]
) -> tuple[list[tuple[float, float, float]], int]:
    """Generate deterministic review normals; these are not retail normal data."""

    sums = [[0.0, 0.0, 0.0] for _ in positions]
    nondegenerate = 0
    for offset in range(0, len(indices), 3):
        ia, ib, ic = indices[offset : offset + 3]
        a, b, c = positions[ia], positions[ib], positions[ic]
        ab = tuple(b[axis] - a[axis] for axis in range(3))
        ac = tuple(c[axis] - a[axis] for axis in range(3))
        normal = _cross(ab, ac)
        length = math.sqrt(sum(value * value for value in normal))
        if length <= 1e-12:
            continue
        nondegenerate += 1
        for vertex in (ia, ib, ic):
            for axis in range(3):
                sums[vertex][axis] += normal[axis]
    if not nondegenerate:
        raise CharacterMaterialExportError(
            "position hypothesis makes every triangle degenerate"
        )
    result = []
    for values in sums:
        length = math.sqrt(sum(value * value for value in values))
        if length <= 1e-12:
            result.append((0.0, 0.0, 1.0))
        else:
            result.append(tuple(value / length for value in values))
    return result, nondegenerate


def _triangle_partition(
    full_indices: tuple[int, ...], observed_indices: tuple[int, ...]
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Keep the exact observed material subset separate from unobserved faces."""

    if len(full_indices) % 3 or len(observed_indices) % 3:
        raise CharacterMaterialExportError("triangle index counts do not reconcile")
    full_triangles = [
        tuple(full_indices[offset : offset + 3])
        for offset in range(0, len(full_indices), 3)
    ]
    observed_triangles = [
        tuple(observed_indices[offset : offset + 3])
        for offset in range(0, len(observed_indices), 3)
    ]
    full_counts = Counter(full_triangles)
    observed_counts = Counter(observed_triangles)
    if observed_counts - full_counts:
        raise CharacterMaterialExportError(
            "runtime material triangles are not an exact subset of retail topology"
        )
    remaining_observed = observed_counts.copy()
    unobserved_triangles = []
    for triangle in full_triangles:
        if remaining_observed[triangle]:
            remaining_observed[triangle] -= 1
        else:
            unobserved_triangles.append(triangle)
    if any(remaining_observed.values()):
        raise CharacterMaterialExportError(
            "runtime material triangle subtraction did not reconcile"
        )
    unobserved_indices = tuple(
        index for triangle in unobserved_triangles for index in triangle
    )
    return observed_indices, unobserved_indices


def _triangle_bytes(indices: tuple[int, ...]) -> bytes:
    if len(indices) % 3:
        raise CharacterMaterialExportError("triangle index counts do not reconcile")
    return struct.pack(f">{len(indices)}H", *indices)


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_material_union_override(
    report: dict,
    receipt_sha256: str,
    material_indices: tuple[int, ...],
    unobserved_indices: tuple[int, ...],
    *,
    xpp_sha256: str,
    xpp_bytes: int,
    allowlist_sha256: str,
    record_offset: int,
    vertex_count: int,
    retail_indices: tuple[int, ...],
    retail_index_sha256: str,
    uv_payload_sha256: str,
    uv_byte_offset: int,
    texture_family: str,
    texture_identities: list[tuple[str, str]],
    anchor_lineage_sha256: str,
) -> dict:
    """Reconcile a payload-free union receipt with its private exact indices."""

    if not isinstance(report, dict) or not _valid_sha256(receipt_sha256):
        raise CharacterMaterialExportError("material coverage union receipt is invalid")
    rendered = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if _sha256(rendered) != receipt_sha256:
        raise CharacterMaterialExportError(
            "material coverage union receipt SHA-256 does not match its payload"
        )
    if (
        report.get("format") != "infamous-character-material-coverage-union"
        or report.get("version") != 1
        or report.get("tool_inventory_id")
        != "xpp-tool.character-material-coverage-union.v1"
        or report.get("status")
        not in (
            "partial-retail-material-coverage-proved",
            "full-retail-material-coverage-proved",
        )
        or report.get("payload_bytes_serialized") is not False
    ):
        raise CharacterMaterialExportError(
            "material coverage union receipt has the wrong schema"
        )
    authorities = _object(report.get("authorities"), "coverage union authorities")
    component = _object(report.get("component"), "coverage union component")
    union = _object(report.get("union"), "coverage union result")
    observations = _array(report.get("observations"), "coverage union observations")
    if (
        authorities.get("xpp_sha256") != xpp_sha256
        or authorities.get("xpp_bytes") != xpp_bytes
        or authorities.get("texture_allowlist_sha256") != allowlist_sha256
        or authorities.get("retail_index_sha256") != retail_index_sha256
    ):
        raise CharacterMaterialExportError(
            "material coverage union authorities drifted from the export"
        )
    if (
        not 2 <= len(texture_identities) <= _MAX_SHADER_TEXTURES
        or any(
            not isinstance(suffix, str)
            or not suffix
            or len(suffix) > 8
            or not suffix.isalnum()
            or not isinstance(name, str)
            or not name
            or len(name) > 256
            for suffix, name in texture_identities
        )
        or len({suffix for suffix, _name in texture_identities})
        != len(texture_identities)
        or len({name for _suffix, name in texture_identities})
        != len(texture_identities)
    ):
        raise CharacterMaterialExportError(
            "material texture identities are not bounded and unique"
        )
    expected_texture_names = [
        name for _suffix, name in sorted(texture_identities, key=lambda row: row)
    ]
    if (
        component.get("record_offset") != record_offset
        or component.get("vertices") != vertex_count
        or component.get("retail_triangle_occurrences") != len(retail_indices) // 3
        or component.get("uv_payload_sha256") != uv_payload_sha256
        or component.get("uv_byte_offset") != uv_byte_offset
        or component.get("texture_family") != texture_family
        or component.get("texture_names") != expected_texture_names
    ):
        raise CharacterMaterialExportError(
            "material coverage union component drifted from the export"
        )
    covered = len(material_indices) // 3
    missing = len(unobserved_indices) // 3
    full = missing == 0
    if (
        union.get("observation_count") != len(observations)
        or union.get("covered_retail_triangle_occurrences") != covered
        or union.get("unobserved_retail_triangle_occurrences") != missing
        or union.get("full_retail_material_coverage_proved") is not full
        or union.get("covered_triangle_multiset_sha256")
        != _sha256(_triangle_bytes(material_indices))
        or union.get("unobserved_triangle_multiset_sha256")
        != _sha256(_triangle_bytes(unobserved_indices))
        or report.get("status")
        != (
            "full-retail-material-coverage-proved"
            if full
            else "partial-retail-material-coverage-proved"
        )
    ):
        raise CharacterMaterialExportError(
            "material coverage union counts or triangle identities do not reconcile"
        )
    anchor_matches = [
        item
        for item in observations
        if isinstance(item, dict)
        and item.get("lineage_sha256") == anchor_lineage_sha256
    ]
    if len(anchor_matches) != 1:
        raise CharacterMaterialExportError(
            "anchor lineage must identify exactly one union observation"
        )
    return {
        "receipt_sha256": receipt_sha256,
        "observation_count": len(observations),
        "covered_retail_triangle_occurrences": covered,
        "unobserved_retail_triangle_occurrences": missing,
        "full_retail_material_coverage_proved": full,
        "covered_triangle_multiset_sha256": union["covered_triangle_multiset_sha256"],
        "unobserved_triangle_multiset_sha256": union[
            "unobserved_triangle_multiset_sha256"
        ],
    }


def _select_position_block(event, vertex_count: int):
    candidates = []
    for block in event.blocks:
        if block.range_first != 0 or block.range_count != vertex_count:
            continue
        attributes = [
            item
            for item in block.attributes
            if item["attribute"] == _POSITION_ATTRIBUTE
            and item["type"] == _POSITION_TYPE
            and item["components"] == _POSITION_COMPONENTS
            and item["frequency"] == 0
            and item["modulo"] == 0
            and item["array_stride"] == block.stride
            and block.stride >= 12
        ]
        if len(attributes) == 1:
            candidates.append(block)
    return _one(candidates, "bounded float32x3 position-hypothesis block")


def _decode_positions(
    payload: bytes, stride: int, vertex_count: int
) -> tuple[
    list[tuple[float, float, float]],
    list[float],
    list[float],
    list[float],
]:
    if len(payload) != vertex_count * stride:
        raise CharacterMaterialExportError(
            "position payload does not cover the exact vertex range"
        )
    source = [
        struct.unpack_from(">3f", payload, vertex * stride)
        for vertex in range(vertex_count)
    ]
    if not all(math.isfinite(value) for row in source for value in row):
        raise CharacterMaterialExportError("position payload contains nonfinite values")
    source_min = [min(row[axis] for row in source) for axis in range(3)]
    source_max = [max(row[axis] for row in source) for axis in range(3)]
    center = [(source_min[axis] + source_max[axis]) / 2.0 for axis in range(3)]
    positions = [
        (row[0] - center[0], row[2] - center[2], -(row[1] - center[1]))
        for row in source
    ]
    return positions, source_min, source_max, center


def _decode_uvs(
    payload: bytes,
    *,
    stride: int,
    byte_offset: int,
    vertex_count: int,
) -> list[tuple[float, float]]:
    if (
        byte_offset < 0
        or byte_offset + 4 > stride
        or len(payload) != vertex_count * stride
    ):
        raise CharacterMaterialExportError("UV payload extent does not reconcile")
    values = [
        struct.unpack_from(">2e", payload, vertex * stride + byte_offset)
        for vertex in range(vertex_count)
    ]
    if not all(math.isfinite(value) for row in values for value in row):
        raise CharacterMaterialExportError("UV payload contains nonfinite values")
    return values


def _retail_texture_images(
    xpp_data: bytes, parsed, bindings: list[dict]
) -> tuple[dict[str, dict], list[dict]]:
    selected = {binding["descriptor_index"]: binding for binding in bindings}
    if len(selected) != len(bindings):
        raise CharacterMaterialExportError("texture descriptor indices are not unique")
    found: dict[str, dict] = {}
    receipts = []
    for index, record, texels in iter_textures(xpp_data, parsed):
        binding = selected.get(index)
        if binding is None:
            continue
        suffix = binding.get("name_suffix")
        if (
            not isinstance(suffix, str)
            or not suffix
            or len(suffix) > 8
            or not suffix.isalnum()
            or suffix in found
        ):
            raise CharacterMaterialExportError(
                "material texture suffixes must be unique short alphanumeric values"
            )
        try:
            expected_format = int(binding["format"], 16)
        except (KeyError, TypeError, ValueError) as exc:
            raise CharacterMaterialExportError(
                "texture binding format is invalid"
            ) from exc
        if (
            record.reason
            or record.faces != 1
            or record.format_byte != expected_format
            or record.width != binding.get("width")
            or record.height != binding.get("height")
        ):
            raise CharacterMaterialExportError(
                "retail texture descriptor drifted from the shader lineage"
            )
        prefix_bytes = _integer(binding.get("matched_prefix_bytes"), "mip prefix bytes")
        start = record.heap_offset
        end = start + prefix_bytes
        if start < 0 or end > len(texels) or end <= start:
            raise CharacterMaterialExportError(
                "retail mip prefix leaves the texture heap"
            )
        prefix_sha256 = _sha256(texels[start:end])
        if prefix_sha256 != binding.get("runtime_prefix_sha256"):
            raise CharacterMaterialExportError(
                "retail mip prefix does not match the runtime texture identity"
            )
        try:
            width, height, rgba, decode_kind = decode_level(
                record, texels, 0, record.heap_offset
            )
            png = encode_png(width, height, bytes(rgba))
        except ValueError as exc:
            raise CharacterMaterialExportError(
                "retail texture could not be decoded exactly"
            ) from exc
        item = {
            "sampler": binding["sampler"],
            "suffix": suffix,
            "name": binding["name"],
            "descriptor_index": index,
            "format": binding["format"],
            "width": width,
            "height": height,
            "runtime_prefix_sha256": prefix_sha256,
            "runtime_prefix_bytes": prefix_bytes,
            "decoded_rgba_sha256": _sha256(bytes(rgba)),
            "embedded_png_sha256": _sha256(png),
            "embedded_png_bytes": len(png),
            "decode_kind": decode_kind,
            "png": png,
        }
        found[suffix] = item
        receipts.append({key: value for key, value in item.items() if key != "png"})
    if not {_COLOR_SUFFIX, _NORMAL_SUFFIX}.issubset(found):
        raise CharacterMaterialExportError(
            "retail XPP did not provide the required color and normal pair"
        )
    if len(found) != len(bindings):
        raise CharacterMaterialExportError(
            "retail XPP did not provide every shader-bound descriptor"
        )
    receipts.sort(key=lambda item: item["sampler"])
    return found, receipts


def build_character_material_export(
    xpp_data: bytes,
    bundle: Path,
    texture_allowlist: Path,
    capture_key_exclusion: Path | None,
    lineage_path: Path,
    lineage_sha256: str,
    material_coverage_mode: str = "observed-only",
    *,
    material_indices_override: tuple[int, ...] | None = None,
    material_coverage_union_report: dict | None = None,
    material_coverage_union_sha256: str | None = None,
    tool_inventory_id: str = _SINGLE_EXPORT_TOOL,
) -> tuple[bytes, dict]:
    """Build one exact-UV retail-material GLB and payload-free receipt."""

    if not xpp_data or len(xpp_data) > MAX_XPP_BYTES:
        raise CharacterMaterialExportError(
            "XPP source is empty or exceeds the 64 MiB bound"
        )
    if material_coverage_mode not in _MATERIAL_COVERAGE_MODES:
        raise CharacterMaterialExportError("material coverage mode is invalid")
    union_values = (
        material_indices_override,
        material_coverage_union_report,
        material_coverage_union_sha256,
    )
    union_export = all(value is not None for value in union_values)
    if any(value is not None for value in union_values) != union_export:
        raise CharacterMaterialExportError(
            "material union override requires indices, report, and SHA-256 together"
        )
    if union_export:
        if (
            material_coverage_mode != "observed-only"
            or tool_inventory_id != _UNION_EXPORT_TOOL
        ):
            raise CharacterMaterialExportError(
                "material union override requires the strict union exporter"
            )
    elif tool_inventory_id != _SINGLE_EXPORT_TOOL:
        raise CharacterMaterialExportError("material exporter tool identity is invalid")
    try:
        lineage, lineage_identity = _read_pinned_json(
            lineage_path, lineage_sha256, "shader-lineage report"
        )
    except ValueError as exc:
        raise CharacterMaterialExportError(str(exc)) from exc
    if (
        lineage.get("format") != "infamous-character-uv-texture-binding"
        or lineage.get("version") != 1
        or lineage.get("status") != "exact-shader-lineage-with-unique-packed-layout"
    ):
        raise CharacterMaterialExportError("shader-lineage report has the wrong schema")
    proof = _object(lineage.get("proof"), "shader-lineage proof")
    if proof.get("geometry_to_uv_to_texture_binding") is not True:
        raise CharacterMaterialExportError("shader-lineage proof is incomplete")
    selection = _object(lineage.get("selection"), "shader-lineage selection")
    shader = _object(lineage.get("shader_lineage"), "shader lineage")
    authorities = _object(lineage.get("authorities"), "shader-lineage authorities")
    bindings = [
        _object(item, "texture binding")
        for item in _array(lineage.get("texture_bindings"), "texture bindings")
    ]
    suffixes = [item.get("name_suffix") for item in bindings]
    samplers = [item.get("sampler") for item in bindings]
    families = {item.get("family") for item in bindings}
    if (
        not 2 <= len(bindings) <= _MAX_SHADER_TEXTURES
        or len(set(suffixes)) != len(suffixes)
        or not {_COLOR_SUFFIX, _NORMAL_SUFFIX}.issubset(suffixes)
        or any(
            not isinstance(suffix, str)
            or not suffix
            or len(suffix) > 8
            or not suffix.isalnum()
            for suffix in suffixes
        )
        or len(set(samplers)) != len(samplers)
        or any(
            not isinstance(sampler, int)
            or isinstance(sampler, bool)
            or not 0 <= sampler <= 15
            for sampler in samplers
        )
        or len(families) != 1
    ):
        raise CharacterMaterialExportError(
            "shader lineage does not select one bounded texture family with unique "
            "samplers/suffixes and required color/normal descriptors"
        )
    texture_family = families.pop()
    if (
        not isinstance(texture_family, str)
        or not texture_family
        or len(texture_family) > 128
    ):
        raise CharacterMaterialExportError("shader lineage texture family is invalid")
    xpp_identity = _sha256(xpp_data)
    if xpp_identity != authorities.get("source_xpp_sha256") or len(
        xpp_data
    ) != authorities.get("source_xpp_bytes"):
        raise CharacterMaterialExportError("retail XPP failed the lineage identity")

    try:
        completion, events, allowlist_identity = _load_bundle(
            bundle, texture_allowlist, capture_key_exclusion
        )
    except RuntimeTopologyExportError as exc:
        raise CharacterMaterialExportError(str(exc)) from exc
    if completion.get("format") != authorities.get(
        "bundle_format"
    ) or allowlist_identity != authorities.get("texture_allowlist_sha256"):
        raise CharacterMaterialExportError(
            "bundle authorities drifted from the lineage"
        )
    paging = _paged_capture_metadata(completion)
    if lineage.get("paging") != paging:
        raise CharacterMaterialExportError(
            "bundle paging/exclusion authority drifted from the lineage"
        )
    event_number = _integer(selection.get("event"), "event number")
    event = events.get(event_number)
    if event is None or event.draw_event != selection.get("draw_event"):
        raise CharacterMaterialExportError("lineage event is absent from the bundle")
    if (
        event.vertex_program_sha256 != selection.get("vertex_program_sha256")
        or event.fragment_program_sha256 != selection.get("fragment_program_sha256")
        or tuple(event.target_texture_sha256s)
        != tuple(item["runtime_prefix_sha256"] for item in bindings)
    ):
        raise CharacterMaterialExportError("event shader/texture identities drifted")

    parsed = parse_xpp(xpp_data, len(xpp_data))
    record_offset = _integer(selection.get("record_offset"), "record offset")
    contracts = [
        item
        for item in find_skinned_geometry_contracts(xpp_data, parsed)
        if item.record_offset == record_offset
    ]
    contract = _one(contracts, "retail character geometry contract")
    vertex_count = _integer(selection.get("vertex_count"), "vertex count")
    if contract.vertex_count != vertex_count:
        raise CharacterMaterialExportError("retail topology vertex count drifted")
    index_start = parsed.data_offset + contract.index_offset
    index_end = index_start + contract.index_byte_count
    index_bytes_be = xpp_data[index_start:index_end]
    if (
        len(index_bytes_be) != contract.index_byte_count
        or _sha256(index_bytes_be) != contract.index_sha256
    ):
        raise CharacterMaterialExportError("retail index stream failed exact identity")
    indices = struct.unpack(f">{contract.index_count}H", index_bytes_be)
    if (
        not indices
        or len(indices) % 3
        or min(indices) != 0
        or max(indices) >= vertex_count
    ):
        raise CharacterMaterialExportError("retail index topology is invalid")

    position_block = _select_position_block(event, vertex_count)
    uv_block_number = _integer(selection.get("source_block"), "UV source block")
    uv_block = _one(
        [item for item in event.blocks if item.number == uv_block_number],
        "source-bound UV block",
    )
    try:
        position_payload = _read_payload(
            bundle,
            position_block.payload_file,
            position_block.payload_bytes,
            position_block.payload_sha256,
        )
        uv_payload = _read_payload(
            bundle,
            uv_block.payload_file,
            uv_block.payload_bytes,
            uv_block.payload_sha256,
        )
        runtime_index_payload = _read_payload(
            bundle,
            event.index_payload_file,
            event.index_bytes,
            event.index_sha256,
        )
    except RuntimeTopologyExportError as exc:
        raise CharacterMaterialExportError(str(exc)) from exc
    if (
        uv_block.payload_sha256 != selection.get("source_stream_sha256")
        or uv_block.stride != selection.get("source_stream_stride")
        or uv_block.range_first != 0
        or uv_block.range_count != vertex_count
    ):
        raise CharacterMaterialExportError("source-bound UV payload drifted")
    if (
        shader.get("vertex_input_attribute") != 9
        or shader.get("vertex_input_type") != 3
        or shader.get("vertex_input_components") not in (2, 3)
        or shader.get("fragment_input_name") != "TEX0"
    ):
        raise CharacterMaterialExportError(
            "shader lineage is not the bounded half2/half3 TEX0 case"
        )
    uv_offset = _integer(shader.get("vertex_input_byte_offset"), "UV byte offset")

    if (
        event.index_bytes != event.index_count * 2
        or event.index_count % 3
        or len(runtime_index_payload) != event.index_bytes
    ):
        raise CharacterMaterialExportError("runtime index payload extent is invalid")
    runtime_indices = struct.unpack(f">{event.index_count}H", runtime_index_payload)
    if not runtime_indices or max(runtime_indices) >= vertex_count:
        raise CharacterMaterialExportError("runtime index topology is invalid")
    material_indices, unobserved_indices = _triangle_partition(
        indices,
        material_indices_override if union_export else runtime_indices,
    )
    if not material_indices:
        raise CharacterMaterialExportError("material export has no proved triangles")
    coverage_union = None
    if union_export:
        coverage_union = _validate_material_union_override(
            material_coverage_union_report,
            material_coverage_union_sha256,
            material_indices,
            unobserved_indices,
            xpp_sha256=xpp_identity,
            xpp_bytes=len(xpp_data),
            allowlist_sha256=allowlist_identity,
            record_offset=record_offset,
            vertex_count=vertex_count,
            retail_indices=indices,
            retail_index_sha256=contract.index_sha256,
            uv_payload_sha256=uv_block.payload_sha256,
            uv_byte_offset=uv_offset,
            texture_family=texture_family,
            texture_identities=[
                (item["name_suffix"], item["name"]) for item in bindings
            ],
            anchor_lineage_sha256=lineage_identity,
        )
    presentation_mode = "observed-union" if union_export else material_coverage_mode

    positions, source_min, source_max, center = _decode_positions(
        position_payload, position_block.stride, vertex_count
    )
    uvs = _decode_uvs(
        uv_payload,
        stride=uv_block.stride,
        byte_offset=uv_offset,
        vertex_count=vertex_count,
    )
    normals, nondegenerate = _inspection_normals(positions, indices)
    textures, texture_receipts = _retail_texture_images(xpp_data, parsed, bindings)

    position_min = [min(row[axis] for row in positions) for axis in range(3)]
    position_max = [max(row[axis] for row in positions) for axis in range(3)]
    uv_min = [min(row[axis] for row in uvs) for axis in range(2)]
    uv_max = [max(row[axis] for row in uvs) for axis in range(2)]
    builder = GlbBuilder()
    position_accessor = builder.add_accessor(
        b"".join(struct.pack("<3f", *row) for row in positions),
        5126,
        vertex_count,
        "VEC3",
        34962,
        position_min,
        position_max,
    )
    normal_accessor = builder.add_accessor(
        b"".join(struct.pack("<3f", *row) for row in normals),
        5126,
        vertex_count,
        "VEC3",
        34962,
    )
    uv_accessor = builder.add_accessor(
        b"".join(struct.pack("<2f", *row) for row in uvs),
        5126,
        vertex_count,
        "VEC2",
        34962,
        uv_min,
        uv_max,
    )
    exported_material_indices = (
        indices if material_coverage_mode == "preview-full-record" else material_indices
    )
    material_index_accessor = builder.add_accessor(
        struct.pack(f"<{len(exported_material_indices)}H", *exported_material_indices),
        5123,
        len(exported_material_indices),
        "SCALAR",
        34963,
        [min(exported_material_indices)],
        [max(exported_material_indices)],
    )
    unobserved_index_accessor = None
    if unobserved_indices and material_coverage_mode == "observed-only":
        unobserved_index_accessor = builder.add_accessor(
            struct.pack(f"<{len(unobserved_indices)}H", *unobserved_indices),
            5123,
            len(unobserved_indices),
            "SCALAR",
            34963,
            [min(unobserved_indices)],
            [max(unobserved_indices)],
        )
    ordered_bindings = sorted(bindings, key=lambda item: item["sampler"])
    ordered_textures = [textures[item["name_suffix"]] for item in ordered_bindings]
    texture_views = [builder.add_view(item["png"]) for item in ordered_textures]
    texture_indices = {
        item["suffix"]: index for index, item in enumerate(ordered_textures)
    }
    unassigned_suffixes = [
        item["suffix"]
        for item in ordered_textures
        if item["suffix"] not in (_COLOR_SUFFIX, _NORMAL_SUFFIX)
    ]
    evidence = {
        "diagnosticOnly": True,
        "recordOffset": record_offset,
        "topologyProved": True,
        "observedMaterialTriangles": len(material_indices) // 3,
        "unobservedMaterialTriangles": len(unobserved_indices) // 3,
        "fullTopologyMaterialCoverageProved": not unobserved_indices,
        "materialCoveragePresentation": presentation_mode,
        "coverageUnionRevalidated": union_export,
        "unobservedMaterialPreviewExtrapolated": (
            material_coverage_mode == "preview-full-record" and bool(unobserved_indices)
        ),
        "uvProved": True,
        "retailTextureIdentitiesProved": True,
        "textureFamily": texture_family,
        "shaderBoundTextureSuffixes": [item["suffix"] for item in ordered_textures],
        "unassignedTextureSuffixes": unassigned_suffixes,
        "materialRolesFromRetailNames": True,
        "extraTextureRolesAssigned": False,
        "positionHypothesisAttribute": _POSITION_ATTRIBUTE,
        "positionSemanticProved": False,
        "generatedInspectionNormals": True,
        "retailNormalsProved": False,
        "nativePbrProved": False,
        "fullCharacterProved": False,
        "rpcs3RoundTripProved": False,
        "nativeImportProved": False,
    }
    primitives = [
        {
            "attributes": {
                "POSITION": position_accessor,
                "NORMAL": normal_accessor,
                "TEXCOORD_0": uv_accessor,
            },
            "indices": material_index_accessor,
            "material": 0,
            "mode": 4,
            "extras": {
                "materialBinding": (
                    "preview extrapolation across full retail record"
                    if material_coverage_mode == "preview-full-record"
                    else "multi-observation exact triangle union"
                    if union_export
                    else "runtime-observed exact triangle subset"
                )
            },
        }
    ]
    if unobserved_index_accessor is not None:
        primitives.append(
            {
                "attributes": {
                    "POSITION": position_accessor,
                    "NORMAL": normal_accessor,
                    "TEXCOORD_0": uv_accessor,
                },
                "indices": unobserved_index_accessor,
                "material": 1,
                "mode": 4,
                "extras": {"materialBinding": "unobserved diagnostic topology only"},
            }
        )
    materials = [
        {
            "name": (
                f"{texture_family} retail C/N PROVISIONAL full-record preview"
                if material_coverage_mode == "preview-full-record"
                else f"{texture_family} retail C/N observed union"
                if union_export
                else f"{texture_family} retail C/N observed subset"
            ),
            "doubleSided": True,
            "alphaMode": "BLEND",
            "pbrMetallicRoughness": {
                "baseColorTexture": {
                    "index": texture_indices[_COLOR_SUFFIX],
                    "texCoord": 0,
                },
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
            "normalTexture": {
                "index": texture_indices[_NORMAL_SUFFIX],
                "texCoord": 0,
                "scale": 1.0,
            },
            "extras": {"infamousMaterialEvidence": evidence},
        }
    ]
    if unobserved_index_accessor is not None:
        materials.append(
            {
                "name": "UNOBSERVED MATERIAL - diagnostic clay",
                "doubleSided": True,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.8, 0.18, 0.03, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
                "extensions": {"KHR_materials_unlit": {}},
                "extras": {"infamousMaterialEvidence": evidence},
            }
        )
    document = {
        "asset": {
            "version": "2.0",
            "generator": "xpp-tool 2.32.0 character material exporter",
            "extras": {"infamousMaterialEvidence": evidence},
        },
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {
                "mesh": 0,
                "name": f"{texture_family} retail-material diagnostic",
                "extras": {"infamousMaterialEvidence": evidence},
            }
        ],
        "meshes": [
            {
                "name": (
                    f"Exact {texture_family} topology / observed material union"
                    if union_export
                    else f"Exact {texture_family} topology / observed material subset"
                ),
                "primitives": primitives,
            }
        ],
        "materials": materials,
        "samplers": [
            {
                "magFilter": 9729,
                "minFilter": 9987,
                "wrapS": 10497,
                "wrapT": 10497,
            }
        ],
        "textures": [
            {
                "sampler": 0,
                "source": index,
                "name": item["name"],
                "extras": {
                    "retailNameSuffix": item["suffix"],
                    "shaderSampler": item["sampler"],
                    "displayRole": (
                        "baseColor"
                        if item["suffix"] == _COLOR_SUFFIX
                        else "normal"
                        if item["suffix"] == _NORMAL_SUFFIX
                        else None
                    ),
                },
            }
            for index, item in enumerate(ordered_textures)
        ],
        "images": [
            {
                "bufferView": view,
                "mimeType": "image/png",
                "name": item["name"],
            }
            for view, item in zip(texture_views, ordered_textures)
        ],
        "bufferViews": builder.views,
        "accessors": builder.accessors,
    }
    if unobserved_index_accessor is not None:
        document["extensionsUsed"] = ["KHR_materials_unlit"]
    glb = _pack_glb(document, builder.binary)
    if len(glb) > MAX_GLB_BYTES:
        raise CharacterMaterialExportError("material GLB exceeds the 64 MiB bound")
    report = {
        "format": "infamous-character-material-export",
        "version": 1,
        "tool_inventory_id": tool_inventory_id,
        "status": "retail-material-progress-glb-written",
        "presentation_mode": presentation_mode,
        "authorities": {
            "xpp_sha256": xpp_identity,
            "xpp_bytes": len(xpp_data),
            "lineage_sha256": lineage_identity,
            "bundle_format": completion["format"],
            "texture_allowlist_sha256": allowlist_identity,
            "capture_key_exclusion_sha256": (
                paging["exclusion_manifest_sha256"] if paging is not None else None
            ),
            **(
                {"coverage_union_sha256": material_coverage_union_sha256}
                if union_export
                else {}
            ),
        },
        "selection": {
            "page": selection["page"],
            "event": event_number,
            "draw_event": event.draw_event,
            "record_offset": record_offset,
            "vertices": vertex_count,
            "triangles": len(indices) // 3,
            "material_observed_triangles": len(material_indices) // 3,
            "material_unobserved_triangles": len(unobserved_indices) // 3,
            "nondegenerate_triangles": nondegenerate,
            "index_sha256": contract.index_sha256,
            "material_event_index_sha256": event.index_sha256,
            **(
                {
                    "material_union_index_sha256": _sha256(
                        _triangle_bytes(material_indices)
                    )
                }
                if union_export
                else {}
            ),
            "position_payload_sha256": position_block.payload_sha256,
            "uv_payload_sha256": uv_block.payload_sha256,
            "uv_byte_offset": uv_offset,
            "uv_minimum": uv_min,
            "uv_maximum": uv_max,
            "texture_family": texture_family,
            "shader_bound_texture_count": len(ordered_textures),
            "display_assigned_texture_suffixes": [_COLOR_SUFFIX, _NORMAL_SUFFIX],
            "unassigned_texture_suffixes": unassigned_suffixes,
        },
        "textures": texture_receipts,
        "source_position_bounds": {"minimum": source_min, "maximum": source_max},
        "recentered_position_center": center,
        "glb": {"bytes": len(glb), "sha256": _sha256(glb)},
        **({"coverage_union": coverage_union} if union_export else {}),
        "proof": {
            "exact_retail_topology": True,
            "exact_full_vertex_range": True,
            "shader_proved_texcoord_0": True,
            "exact_uv_rows": True,
            "runtime_prefix_to_retail_descriptor": True,
            "embedded_retail_color_and_normal": True,
            "embedded_all_shader_bound_textures": True,
            "all_extra_texture_roles_left_unassigned": True,
            "exact_observed_triangle_material_subset": True,
            "coverage_union_revalidated": union_export,
            "exact_union_triangle_material_subset": union_export,
            "deterministic_material_glb": True,
        },
        "limitations": {
            "position_attribute_is_diagnostic_hypothesis": True,
            "position_semantic": False,
            "generated_inspection_normals_are_retail_normals": False,
            "material_roles_from_retail_name_suffixes": True,
            "unassigned_texture_suffixes": unassigned_suffixes,
            "native_pbr": False,
            "full_character": False,
            "all_materials": False,
            "full_topology_material_coverage": not unobserved_indices,
            "unobserved_material_preview_extrapolated": (
                material_coverage_mode == "preview-full-record"
                and bool(unobserved_indices)
            ),
            "rigged": False,
            "four_x_textures": False,
            "rpcs3_mod_round_trip": False,
            "native_decomp_import": False,
        },
        "payload_bytes_serialized_in_report": False,
        "next_gate": (
            "render with imported GLB materials preserved, inspect the separately marked "
            "unobserved faces, and resolve their own material binding before promoting "
            "full topology material coverage or any full-character claim"
        ),
    }
    rendered = render_character_material_report(report)
    if len(rendered) > MAX_REPORT_BYTES:
        raise CharacterMaterialExportError("material report exceeds the 256 KiB bound")
    return glb, report


def render_character_material_report(report: dict) -> bytes:
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


def write_new_character_material_export(
    glb_path: Path, report_path: Path, glb: bytes, report: dict
) -> None:
    """Publish one GLB/report pair without replacing either destination."""

    if glb_path.resolve() == report_path.resolve():
        raise CharacterMaterialExportError("GLB and report destinations must differ")
    for path in (glb_path, report_path):
        if path.is_symlink() or path.exists():
            raise CharacterMaterialExportError(
                "material export destination already exists"
            )
    report_payload = render_character_material_report(report)
    if (
        len(glb) > MAX_GLB_BYTES
        or len(report_payload) > MAX_REPORT_BYTES
        or report.get("glb") != {"bytes": len(glb), "sha256": _sha256(glb)}
    ):
        raise CharacterMaterialExportError(
            "material output bytes do not reconcile with the bounded receipt"
        )
    staged: list[Path] = []
    published: list[Path] = []
    try:
        staged.append(_stage_new(glb_path, glb))
        staged.append(_stage_new(report_path, report_payload))
        for temporary, destination in zip(staged, (glb_path, report_path)):
            os.link(temporary, destination)
            published.append(destination)
    except BaseException:
        for destination in published:
            destination.unlink(missing_ok=True)
        raise
    finally:
        for temporary in staged:
            temporary.unlink(missing_ok=True)
