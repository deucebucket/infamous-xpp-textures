"""Exact multiset union of material coverage across repeated character draws."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .character import find_skinned_geometry_contracts
from .character_material_export import (
    CharacterMaterialExportError,
    _triangle_partition,
)
from .component_ledger import (
    CharacterComponentLedgerError,
    _load_json,
    _read_pinned,
    _validate_material_report,
    _valid_sha256,
)
from .runtime_topology_export import (
    RuntimeTopologyExportError,
    _load_bundle,
    _paged_capture_metadata,
    _read_payload,
)
from .xpp import parse_xpp


MAX_XPP_BYTES = 64 * 1024 * 1024
MAX_MATERIAL_REPORT_BYTES = 1024 * 1024
MAX_OBSERVATIONS = 16
MAX_COMPLETION_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 256 * 1024


class MaterialCoverageUnionError(ValueError):
    """Raised when material-coverage evidence is unsafe or contradictory."""


@dataclass(frozen=True)
class MaterialCoverageObservation:
    """One pinned material report and its exact immutable runtime bundle."""

    report: Path
    report_sha256: str
    bundle: Path
    capture_key_exclusion: Path | None


@dataclass(frozen=True)
class PartialMaterialCoverageObservation:
    """One pinned partial lineage plus every authority needed to revalidate it."""

    lineage: Path
    lineage_sha256: str
    bundle: Path
    capture_key_exclusion: Path | None
    source_census: Path
    source_census_sha256: str
    character_census: Path
    character_census_sha256: str


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _one(values: list, label: str):
    if len(values) != 1:
        raise MaterialCoverageUnionError(f"expected one {label}, found {len(values)}")
    return values[0]


def _bundle_completion_receipt(bundle: Path) -> dict:
    completion = bundle / "capture.complete"
    if completion.is_symlink() or not completion.is_file():
        raise MaterialCoverageUnionError(
            "bundle completion marker is missing or is not a regular file"
        )
    size = completion.stat().st_size
    if not 0 < size <= MAX_COMPLETION_BYTES:
        raise MaterialCoverageUnionError(
            "bundle completion marker exceeds the byte bound"
        )
    payload = completion.read_bytes()
    if len(payload) != size:
        raise MaterialCoverageUnionError(
            "bundle completion marker changed while it was read"
        )
    return {"bytes": size, "sha256": _sha256(payload)}


def _triangle_bytes(triangles: Sequence[tuple[int, int, int]]) -> bytes:
    return b"".join(struct.pack(">3H", *triangle) for triangle in triangles)


def _expand_counter(
    retail_triangles: Sequence[tuple[int, int, int]], counts: Counter
) -> list[tuple[int, int, int]]:
    remaining = counts.copy()
    result: list[tuple[int, int, int]] = []
    for triangle in retail_triangles:
        if remaining[triangle]:
            result.append(triangle)
            remaining[triangle] -= 1
    if any(remaining.values()):
        raise MaterialCoverageUnionError(
            "triangle multiset cannot be ordered against retail topology"
        )
    return result


def _full_range_texture_contract(
    raw_report: dict, report: dict, *, index: int
) -> tuple[list[dict], list[dict]]:
    """Return the display anchor and complete compatible texture set for one pass."""

    selection = raw_report.get("selection")
    textures = report.get("textures")
    if not isinstance(selection, dict) or not isinstance(textures, list):
        raise MaterialCoverageUnionError(
            f"material report {index} texture contract is malformed"
        )
    by_suffix = {item["suffix"]: item for item in textures}
    display = selection.get("display_assigned_texture_suffixes")
    unassigned = selection.get("unassigned_texture_suffixes")
    if display is None and unassigned is None:
        display = list(by_suffix)
        unassigned = []
    elif display is None or unassigned is None:
        raise MaterialCoverageUnionError(
            f"material report {index} pass assignments are incomplete"
        )
    if (
        not isinstance(display, list)
        or not isinstance(unassigned, list)
        or not 1 <= len(display) <= 16
        or len(unassigned) > 15
        or not all(isinstance(item, str) and item for item in (*display, *unassigned))
        or len(set(display)) != len(display)
        or len(set(unassigned)) != len(unassigned)
        or set(display) & set(unassigned)
        or set(display) | set(unassigned) != set(by_suffix)
    ):
        raise MaterialCoverageUnionError(
            f"material report {index} pass assignments do not partition its textures"
        )
    shader_bound = selection.get("shader_bound_texture_count")
    if shader_bound is not None and (
        isinstance(shader_bound, bool)
        or not isinstance(shader_bound, int)
        or shader_bound != len(textures)
    ):
        raise MaterialCoverageUnionError(
            f"material report {index} shader-bound texture count is inconsistent"
        )

    def compatibility_identity(item: dict) -> dict:
        # The same decoded retail image can have a different deterministic PNG
        # container when exported beside a different number of pass images.
        # Pin the decoded pixels and runtime/source identities; the anchor owns
        # the display PNG that is ultimately embedded in the union GLB.
        return {
            key: item[key]
            for key in (
                "descriptor_index",
                "name",
                "suffix",
                "width",
                "height",
                "decoded_rgba_sha256",
                "runtime_prefix_sha256",
            )
        }

    display_textures = sorted(
        (compatibility_identity(by_suffix[suffix]) for suffix in display),
        key=lambda row: (row["suffix"], row["name"]),
    )
    compatible_textures = sorted(
        (compatibility_identity(item) for item in textures),
        key=lambda row: (row["suffix"], row["name"]),
    )
    return display_textures, compatible_textures


def _partial_material_observation(
    observation: PartialMaterialCoverageObservation,
    *,
    index: int,
    xpp_sha256: str,
    xpp_bytes: int,
    record_offset: int,
    vertex_count: int,
    retail_indices: tuple[int, ...],
    retail_index_sha256: str,
    texture_allowlist: Path,
    common: dict,
) -> tuple[dict, Counter, str]:
    """Revalidate one safe partial lineage as material-coverage evidence."""

    try:
        lineage_payload = _read_pinned(
            observation.lineage,
            observation.lineage_sha256,
            MAX_MATERIAL_REPORT_BYTES,
            f"partial lineage {index}",
        )
        lineage = _load_json(lineage_payload, f"partial lineage {index}")
        source_payload = _read_pinned(
            observation.source_census,
            observation.source_census_sha256,
            MAX_MATERIAL_REPORT_BYTES,
            f"partial source census {index}",
        )
        source_census = _load_json(source_payload, f"partial source census {index}")
        character_payload = _read_pinned(
            observation.character_census,
            observation.character_census_sha256,
            MAX_MATERIAL_REPORT_BYTES,
            f"partial character census {index}",
        )
        character_census = _load_json(
            character_payload, f"partial character census {index}"
        )
    except CharacterComponentLedgerError as exc:
        raise MaterialCoverageUnionError(str(exc)) from exc
    if (
        lineage.get("format") != "infamous-character-uv-texture-binding"
        or lineage.get("version") != 1
        or lineage.get("tool_inventory_id")
        != "xpp-tool.character-uv-texture-binding.v1"
        or lineage.get("status")
        != "exact-partial-shader-lineage-with-unique-packed-layout"
        or source_census.get("kind") != "if1-rsx-paged-xpp-source-census"
        or source_census.get("schema_version") != 1
        or character_census.get("format") != "infamous-character-asset-census"
        or character_census.get("version") != 1
    ):
        raise MaterialCoverageUnionError(
            "partial lineage or source census has the wrong schema"
        )
    authorities = lineage.get("authorities")
    selection = lineage.get("selection")
    shader = lineage.get("shader_lineage")
    proof = lineage.get("proof")
    coverage = lineage.get("partial_runtime_coverage")
    bindings = lineage.get("texture_bindings")
    if not all(
        isinstance(item, dict)
        for item in (authorities, selection, shader, proof, coverage)
    ) or not isinstance(bindings, list):
        raise MaterialCoverageUnionError("partial lineage structure is malformed")
    required_proof = (
        "same_xpp_source_record",
        "exact_source_stream_bytes",
        "exact_shader_payloads",
        "target_sampler_coordinate_input",
        "component_level_vertex_lineage",
        "named_texture_identity",
        "two_dimensional_texture_coordinate_semantic",
        "packed_layout_uniquely_reconstructed",
        "geometry_to_uv_to_texture_binding",
        "partial_source_vertex_range",
        "runtime_indices_within_source_range",
        "runtime_retail_triangle_subset",
        "safe_for_material_coverage_union",
    )
    if (
        not all(proof.get(key) is True for key in required_proof)
        or proof.get("full_source_vertex_range") is not False
        or coverage.get("safe_for_material_coverage_union") is not True
    ):
        raise MaterialCoverageUnionError("partial lineage proof is incomplete")
    source = source_census.get("source")
    if (
        not isinstance(source, dict)
        or authorities.get("source_census_sha256") != observation.source_census_sha256
        or authorities.get("character_census_sha256")
        != observation.character_census_sha256
        or authorities.get("source_xpp_sha256") != xpp_sha256
        or authorities.get("source_xpp_bytes") != xpp_bytes
        or source.get("source_sha256") != xpp_sha256
        or source.get("source_size") != xpp_bytes
        or authorities.get("texture_allowlist_sha256") != common["allowlist_sha256"]
    ):
        raise MaterialCoverageUnionError("partial lineage authorities drifted")

    targets = character_census.get("targets")
    descriptors_by_side = character_census.get("target_texture_descriptors")
    if not isinstance(targets, dict) or not isinstance(descriptors_by_side, dict):
        raise MaterialCoverageUnionError("partial character census is malformed")
    target_sides = [
        side
        for side, target in targets.items()
        if isinstance(side, str)
        and isinstance(target, dict)
        and target.get("sha256") == xpp_sha256
        and target.get("bytes") == xpp_bytes
        and target.get("relative_path") == authorities.get("character_target")
    ]
    if len(target_sides) != 1:
        raise MaterialCoverageUnionError(
            "partial character census does not select one source target"
        )
    character_descriptors = descriptors_by_side.get(target_sides[0])
    if (
        not isinstance(character_descriptors, list)
        or not 1 <= len(character_descriptors) <= 512
    ):
        raise MaterialCoverageUnionError(
            "partial character texture descriptors exceed the bound"
        )

    page = selection.get("page")
    event_number = selection.get("event")
    source_range_first = selection.get("source_range_first")
    source_range_count = selection.get("source_range_count")
    source_range_end = selection.get("source_range_end")
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or not 1 <= page <= 17
        or isinstance(event_number, bool)
        or not isinstance(event_number, int)
        or not 1 <= event_number <= 16
        or selection.get("record_offset") != record_offset
        or selection.get("source_stream_index") != 0
        or selection.get("source_vertex_count") != vertex_count
        or selection.get("vertex_count") != source_range_count
        or not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (source_range_first, source_range_count, source_range_end)
        )
        or not 0 <= source_range_first < source_range_end <= vertex_count
        or source_range_count != source_range_end - source_range_first
        or shader.get("vertex_input_attribute") != 9
        or shader.get("vertex_input_type") != 3
        or shader.get("vertex_input_components") not in (2, 3)
        or shader.get("vertex_input_byte_offset") != common["uv_byte_offset"]
        or shader.get("fragment_input_name") != "TEX0"
    ):
        raise MaterialCoverageUnionError(
            "partial lineage source range or UV contract conflicts with the anchor"
        )

    normalized_bindings: list[dict] = []
    seen_suffixes: set[str] = set()
    seen_samplers: set[int] = set()
    seen_descriptors: set[int] = set()
    for binding_index, binding in enumerate(bindings):
        if not isinstance(binding, dict):
            raise MaterialCoverageUnionError(
                f"partial texture binding {binding_index} is malformed"
            )
        suffix = binding.get("name_suffix")
        sampler = binding.get("sampler")
        name = binding.get("name")
        descriptor_index = binding.get("descriptor_index")
        if (
            not isinstance(suffix, str)
            or not suffix
            or len(suffix) > 8
            or not suffix.isalnum()
            or suffix in seen_suffixes
            or isinstance(sampler, bool)
            or not isinstance(sampler, int)
            or not 0 <= sampler <= 15
            or sampler in seen_samplers
            or isinstance(descriptor_index, bool)
            or not isinstance(descriptor_index, int)
            or not 0 <= descriptor_index < 512
            or descriptor_index in seen_descriptors
            or not isinstance(name, str)
            or not name
            or len(name) > 256
            or not _valid_sha256(binding.get("runtime_prefix_sha256"))
        ):
            raise MaterialCoverageUnionError(
                "partial texture identities are not bounded and unique"
            )
        seen_suffixes.add(suffix)
        seen_samplers.add(sampler)
        seen_descriptors.add(descriptor_index)
        descriptor_matches = [
            item
            for item in character_descriptors
            if isinstance(item, dict) and item.get("index") == descriptor_index
        ]
        if len(descriptor_matches) != 1:
            raise MaterialCoverageUnionError(
                "partial texture identity does not select one character descriptor"
            )
        descriptor = descriptor_matches[0]
        mip_matches = [
            item
            for item in descriptor.get("mip_rows", [])
            if isinstance(item, dict)
            and item.get("level") == binding.get("matched_mip_level")
        ]
        if (
            len(mip_matches) != 1
            or descriptor.get("name") != name
            or descriptor.get("family") != lineage.get("texture_family")
            or descriptor.get("name_suffix") != suffix
            or descriptor.get("format") != binding.get("format")
            or descriptor.get("width") != binding.get("width")
            or descriptor.get("height") != binding.get("height")
            or descriptor.get("faces") != 1
            or binding.get("faces") != 1
            or mip_matches[0].get("prefix_bytes") != binding.get("matched_prefix_bytes")
            or mip_matches[0].get("prefix_sha256")
            != binding.get("runtime_prefix_sha256")
        ):
            raise MaterialCoverageUnionError(
                "partial texture identity drifted from the character census"
            )
        normalized_bindings.append(
            {
                "descriptor_index": descriptor_index,
                "name": name,
                "suffix": suffix,
                "sampler": sampler,
                "width": binding.get("width"),
                "height": binding.get("height"),
                "runtime_prefix_sha256": binding["runtime_prefix_sha256"],
            }
        )
    if (
        not 2 <= len(normalized_bindings) <= 8
        or lineage.get("texture_family") != common["texture_family"]
    ):
        raise MaterialCoverageUnionError(
            "partial lineage does not select one compatible texture family"
        )
    by_suffix = {item["suffix"]: item for item in normalized_bindings}
    for anchor_texture in common["textures"]:
        partial_texture = by_suffix.get(anchor_texture["suffix"])
        if partial_texture is None or any(
            partial_texture[key] != anchor_texture[key]
            for key in (
                "descriptor_index",
                "name",
                "width",
                "height",
                "runtime_prefix_sha256",
            )
        ):
            raise MaterialCoverageUnionError(
                "partial lineage conflicts with an anchor texture identity"
            )

    source_events = [
        item
        for item in source_census.get("events", [])
        if isinstance(item, dict)
        and item.get("page") == page
        and item.get("event") == event_number
    ]
    if len(source_events) != 1:
        raise MaterialCoverageUnionError(
            "partial source census does not select one page/event"
        )
    source_event = source_events[0]
    mapping = source_event.get("mapping")
    source_records = source.get("records", [])
    record_matches = [
        item
        for item in source_records
        if isinstance(item, dict) and item.get("record_offset") == record_offset
    ]
    if len(record_matches) != 1:
        raise MaterialCoverageUnionError(
            "partial source census does not select one retail record"
        )
    source_record = record_matches[0]
    if (
        not source_event.get("same_xpp_source_record_proved")
        or not isinstance(mapping, dict)
        or mapping.get("record_offset") != record_offset
        or mapping.get("block") != selection.get("source_block")
        or mapping.get("range_first") != source_range_first
        or mapping.get("range_count") != source_range_count
        or mapping.get("range_end") != source_range_end
        or mapping.get("source_vertex_count") != vertex_count
        or mapping.get("full_vertex_range") is not False
        or mapping.get("matched_stream_slice_sha256")
        != selection.get("source_stream_sha256")
        or mapping.get("stream_zero_record_bytes")
        != selection.get("source_stream_stride")
        or source_record.get("vertex_count") != vertex_count
        or source_record.get("index_count") != len(retail_indices)
        or source_record.get("index_sha256") != retail_index_sha256
    ):
        raise MaterialCoverageUnionError(
            "partial source mapping does not reconcile with retail topology"
        )
    mapping_coverage = mapping.get("runtime_index_coverage")
    if (
        not isinstance(mapping_coverage, dict)
        or mapping_coverage.get("safe_for_retail_coverage_union") is not True
        or mapping_coverage.get("runtime_indices_within_mapped_vertex_range")
        is not True
        or mapping_coverage.get("runtime_index_sha256")
        != coverage.get("runtime_index_sha256")
        or mapping_coverage.get("covered_triangle_multiset_sha256")
        != coverage.get("covered_triangle_multiset_sha256")
        or mapping_coverage.get("unobserved_triangle_multiset_sha256")
        != coverage.get("unobserved_triangle_multiset_sha256")
    ):
        raise MaterialCoverageUnionError(
            "partial source coverage receipt does not reconcile"
        )

    try:
        completion, events, allowlist_identity = _load_bundle(
            observation.bundle,
            texture_allowlist,
            observation.capture_key_exclusion,
        )
    except RuntimeTopologyExportError as exc:
        raise MaterialCoverageUnionError(str(exc)) from exc
    paging = _paged_capture_metadata(completion)
    if (
        completion.get("format") != authorities.get("bundle_format")
        or allowlist_identity != common["allowlist_sha256"]
        or lineage.get("paging") != paging
    ):
        raise MaterialCoverageUnionError("partial runtime bundle authority drifted")
    event = events.get(event_number)
    if (
        event is None
        or event.draw_event != selection.get("draw_event")
        or event.index_sha256 != coverage.get("runtime_index_sha256")
        or event.vertex_program_sha256 != selection.get("vertex_program_sha256")
        or event.fragment_program_sha256 != selection.get("fragment_program_sha256")
        or tuple(event.target_texture_slots)
        != tuple(item["sampler"] for item in normalized_bindings)
        or tuple(event.target_texture_sha256s)
        != tuple(item["runtime_prefix_sha256"] for item in normalized_bindings)
    ):
        raise MaterialCoverageUnionError("partial runtime event identity drifted")
    blocks = [
        item for item in event.blocks if item.number == selection.get("source_block")
    ]
    block = _one(blocks, "partial source-bound UV block")
    if (
        block.payload_sha256 != selection.get("source_stream_sha256")
        or block.stride != selection.get("source_stream_stride")
        or block.range_first != source_range_first
        or block.range_count != source_range_count
    ):
        raise MaterialCoverageUnionError("partial source-bound UV block drifted")
    try:
        _read_payload(
            observation.bundle,
            block.payload_file,
            block.payload_bytes,
            block.payload_sha256,
        )
        runtime_payload = _read_payload(
            observation.bundle,
            event.index_payload_file,
            event.index_bytes,
            event.index_sha256,
        )
    except RuntimeTopologyExportError as exc:
        raise MaterialCoverageUnionError(str(exc)) from exc
    if (
        event.index_bytes != event.index_count * 2
        or event.index_count <= 0
        or event.index_count % 3
        or len(runtime_payload) != event.index_bytes
        or event.index_count // 3 != coverage.get("covered_retail_triangle_occurrences")
    ):
        raise MaterialCoverageUnionError("partial runtime index extent drifted")
    observed_indices = struct.unpack(f">{event.index_count}H", runtime_payload)
    if (
        not observed_indices
        or min(observed_indices) < source_range_first
        or max(observed_indices) >= source_range_end
        or min(observed_indices) != coverage.get("runtime_min_vertex_index")
        or max(observed_indices) != coverage.get("runtime_max_vertex_index")
    ):
        raise MaterialCoverageUnionError(
            "partial material indices leave their proved UV range"
        )
    try:
        _triangle_partition(retail_indices, observed_indices)
    except CharacterMaterialExportError as exc:
        raise MaterialCoverageUnionError(str(exc)) from exc
    observed_triangles = [
        tuple(observed_indices[offset : offset + 3])
        for offset in range(0, len(observed_indices), 3)
    ]
    retail_triangles = [
        tuple(retail_indices[offset : offset + 3])
        for offset in range(0, len(retail_indices), 3)
    ]
    retail_counts = Counter(retail_triangles)
    observed_counts = Counter(observed_triangles)
    covered_triangles = _expand_counter(retail_triangles, observed_counts)
    unobserved_triangles = _expand_counter(
        retail_triangles, retail_counts - observed_counts
    )
    if (
        mapping_coverage.get("status") != "retail-triangle-subset-proved"
        or mapping_coverage.get("runtime_triangle_occurrences")
        != len(observed_triangles)
        or mapping_coverage.get("covered_retail_triangle_occurrences")
        != len(covered_triangles)
        or mapping_coverage.get("unobserved_retail_triangle_occurrences")
        != len(unobserved_triangles)
        or mapping_coverage.get("runtime_min_vertex_index") != min(observed_indices)
        or mapping_coverage.get("runtime_max_vertex_index") != max(observed_indices)
        or mapping_coverage.get("covered_triangle_multiset_sha256")
        != _sha256(_triangle_bytes(covered_triangles))
        or mapping_coverage.get("unobserved_triangle_multiset_sha256")
        != _sha256(_triangle_bytes(unobserved_triangles))
    ):
        raise MaterialCoverageUnionError(
            "partial source coverage does not match the retail triangle partition"
        )
    return (
        {
            "page": page,
            "event": event_number,
            "draw_event": event.draw_event,
            "lineage_sha256": observation.lineage_sha256,
            "partial_lineage_report_sha256": observation.lineage_sha256,
            "source_census_sha256": observation.source_census_sha256,
            "character_census_sha256": observation.character_census_sha256,
            "evidence_kind": "safe-partial-range-shader-lineage",
            "bundle_format": completion["format"],
            "bundle_completion": _bundle_completion_receipt(observation.bundle),
            "capture_key_exclusion_sha256": (
                paging["exclusion_manifest_sha256"] if paging is not None else None
            ),
            "runtime_index_sha256": event.index_sha256,
            "source_range_first": source_range_first,
            "source_range_count": source_range_count,
            "source_range_end": source_range_end,
            "texture_names": sorted(item["name"] for item in normalized_bindings),
            "observed_triangles": len(observed_triangles),
        },
        observed_counts,
        allowlist_identity,
    )


def _build_material_coverage_union(
    xpp_path: Path,
    xpp_sha256: str,
    texture_allowlist: Path,
    observations: Sequence[MaterialCoverageObservation],
    *,
    record_offset: int,
    partial_observations: Sequence[PartialMaterialCoverageObservation] = (),
) -> tuple[dict, tuple[int, ...]]:
    """Union exact runtime material triangles for one retail source record."""

    if (
        isinstance(record_offset, bool)
        or not isinstance(record_offset, int)
        or record_offset < 0
    ):
        raise MaterialCoverageUnionError("record offset is invalid")
    if (
        not 1 <= len(observations) <= MAX_OBSERVATIONS
        or len(observations) + len(partial_observations) > MAX_OBSERVATIONS
    ):
        raise MaterialCoverageUnionError("observation count is invalid")
    if not _valid_sha256(xpp_sha256):
        raise MaterialCoverageUnionError("XPP SHA-256 pin is not canonical")

    try:
        xpp_data = _read_pinned(xpp_path, xpp_sha256, MAX_XPP_BYTES, "retail XPP")
    except CharacterComponentLedgerError as exc:
        raise MaterialCoverageUnionError(str(exc)) from exc
    try:
        parsed = parse_xpp(xpp_data, len(xpp_data))
        contract = _one(
            [
                item
                for item in find_skinned_geometry_contracts(xpp_data, parsed)
                if item.record_offset == record_offset
            ],
            "retail character geometry contract",
        )
    except (ValueError, CharacterMaterialExportError) as exc:
        raise MaterialCoverageUnionError(str(exc)) from exc
    index_start = parsed.data_offset + contract.index_offset
    index_end = index_start + contract.index_byte_count
    index_payload = xpp_data[index_start:index_end]
    if (
        len(index_payload) != contract.index_byte_count
        or _sha256(index_payload) != contract.index_sha256
        or contract.index_count % 3
    ):
        raise MaterialCoverageUnionError("retail index topology failed exact identity")
    retail_indices = struct.unpack(f">{contract.index_count}H", index_payload)
    if (
        not retail_indices
        or min(retail_indices) != 0
        or max(retail_indices) >= contract.vertex_count
    ):
        raise MaterialCoverageUnionError("retail index topology is invalid")
    retail_triangles = [
        tuple(retail_indices[offset : offset + 3])
        for offset in range(0, len(retail_indices), 3)
    ]
    retail_counts = Counter(retail_triangles)

    seen_report_paths: set[Path] = set()
    seen_observations: set[tuple[int, int, str]] = set()
    normalized: list[dict] = []
    common: dict | None = None
    allowlist_identity: str | None = None
    compatible_full_range_textures: dict[str, dict] = {}
    for index, observation in enumerate(observations):
        report_path = observation.report.resolve()
        if report_path in seen_report_paths:
            raise MaterialCoverageUnionError("material report path is duplicated")
        seen_report_paths.add(report_path)
        try:
            report_payload = _read_pinned(
                observation.report,
                observation.report_sha256,
                MAX_MATERIAL_REPORT_BYTES,
                f"material report {index}",
            )
            raw_report = _load_json(report_payload, f"material report {index}")
            report = _validate_material_report(raw_report, observation.report_sha256)
        except CharacterComponentLedgerError as exc:
            raise MaterialCoverageUnionError(str(exc)) from exc
        if raw_report.get("presentation_mode") != "observed-only":
            raise MaterialCoverageUnionError(
                "coverage union requires strict observed-only material reports"
            )
        if report["record_offset"] != record_offset:
            raise MaterialCoverageUnionError(
                "material report selects a different source record"
            )
        display_textures, compatible_textures = _full_range_texture_contract(
            raw_report, report, index=index
        )
        for item in compatible_textures:
            name = item["name"]
            prior = compatible_full_range_textures.get(name)
            if prior is not None and prior != item:
                raise MaterialCoverageUnionError(
                    "compatible full-range texture identities conflict"
                )
            compatible_full_range_textures[name] = item
        report_common = {
            "xpp_sha256": report["source"]["xpp_sha256"],
            "xpp_bytes": report["source"]["xpp_bytes"],
            "vertices": report["topology"]["vertices"],
            "retail_triangles": report["topology"]["triangles"],
            "retail_index_sha256": report["index_sha256"],
            "uv_payload_sha256": report["uv"]["payload_sha256"],
            "uv_byte_offset": report["uv"]["byte_offset"],
            "texture_family": report["texture_family"],
            "textures": display_textures,
        }
        if common is None:
            common = report_common
        elif common != report_common:
            raise MaterialCoverageUnionError(
                "material observations conflict on source topology, UV, or texture family"
            )
        if (
            report_common["xpp_sha256"] != xpp_sha256
            or report_common["xpp_bytes"] != len(xpp_data)
            or report_common["vertices"] != contract.vertex_count
            or report_common["retail_triangles"] != len(retail_triangles)
            or report_common["retail_index_sha256"] != contract.index_sha256
        ):
            raise MaterialCoverageUnionError(
                "material report conflicts with the pinned retail XPP"
            )

        try:
            completion, events, observed_allowlist = _load_bundle(
                observation.bundle,
                texture_allowlist,
                observation.capture_key_exclusion,
            )
        except RuntimeTopologyExportError as exc:
            raise MaterialCoverageUnionError(str(exc)) from exc
        if allowlist_identity is None:
            allowlist_identity = observed_allowlist
        elif allowlist_identity != observed_allowlist:
            raise MaterialCoverageUnionError(
                "runtime bundles do not share one texture allowlist"
            )
        authorities = raw_report.get("authorities")
        if not isinstance(authorities, dict):
            raise MaterialCoverageUnionError("material authorities are malformed")
        paging = _paged_capture_metadata(completion)
        expected_exclusion = (
            paging["exclusion_manifest_sha256"] if paging is not None else None
        )
        if (
            completion.get("format") != authorities.get("bundle_format")
            or observed_allowlist != authorities.get("texture_allowlist_sha256")
            or expected_exclusion != authorities.get("capture_key_exclusion_sha256")
        ):
            raise MaterialCoverageUnionError(
                "runtime bundle authorities drifted from the material report"
            )
        event = events.get(report["event"])
        if (
            event is None
            or event.draw_event != report["draw_event"]
            or event.index_sha256 != report["material_event_index_sha256"]
            or event.index_count
            != report["topology"]["material_observed_triangles"] * 3
            or event.index_bytes != event.index_count * 2
        ):
            raise MaterialCoverageUnionError(
                "runtime event identity drifted from the material report"
            )
        try:
            runtime_payload = _read_payload(
                observation.bundle,
                event.index_payload_file,
                event.index_bytes,
                event.index_sha256,
            )
            observed_indices = struct.unpack(f">{event.index_count}H", runtime_payload)
            _triangle_partition(retail_indices, observed_indices)
        except (RuntimeTopologyExportError, CharacterMaterialExportError) as exc:
            raise MaterialCoverageUnionError(str(exc)) from exc
        observed_triangles = [
            tuple(observed_indices[offset : offset + 3])
            for offset in range(0, len(observed_indices), 3)
        ]
        observed_counts = Counter(observed_triangles)
        identity = (report["page"], report["event"], event.index_sha256)
        if identity in seen_observations:
            raise MaterialCoverageUnionError(
                "material observations repeat one page/event/index identity"
            )
        seen_observations.add(identity)
        normalized.append(
            {
                "page": report["page"],
                "event": report["event"],
                "draw_event": report["draw_event"],
                "material_report_sha256": observation.report_sha256,
                "lineage_sha256": report["lineage_sha256"],
                "bundle_format": completion["format"],
                "bundle_completion": _bundle_completion_receipt(observation.bundle),
                "capture_key_exclusion_sha256": expected_exclusion,
                "runtime_index_sha256": event.index_sha256,
                "evidence_kind": (
                    "full-range-compatible-material-pass"
                    if compatible_textures != display_textures
                    else "full-range-material-export"
                ),
                **(
                    {
                        "compatible_texture_names": [
                            item["name"] for item in compatible_textures
                        ]
                    }
                    if compatible_textures != display_textures
                    else {}
                ),
                "observed_counts": observed_counts,
                "observed_triangles": len(observed_triangles),
            }
        )

    if common is None or allowlist_identity is None:
        raise MaterialCoverageUnionError("coverage union has no validated observations")
    common["allowlist_sha256"] = allowlist_identity
    partial_texture_names: set[str] = set()
    for index, observation in enumerate(partial_observations):
        lineage_path = observation.lineage.resolve()
        if lineage_path in seen_report_paths:
            raise MaterialCoverageUnionError(
                "partial lineage path duplicates another observation"
            )
        seen_report_paths.add(lineage_path)
        row, observed_counts, observed_allowlist = _partial_material_observation(
            observation,
            index=index,
            xpp_sha256=xpp_sha256,
            xpp_bytes=len(xpp_data),
            record_offset=record_offset,
            vertex_count=contract.vertex_count,
            retail_indices=retail_indices,
            retail_index_sha256=contract.index_sha256,
            texture_allowlist=texture_allowlist,
            common=common,
        )
        if observed_allowlist != allowlist_identity:
            raise MaterialCoverageUnionError(
                "partial runtime bundle uses a different texture allowlist"
            )
        identity = (row["page"], row["event"], row["runtime_index_sha256"])
        if identity in seen_observations:
            raise MaterialCoverageUnionError(
                "material observations repeat one page/event/index identity"
            )
        seen_observations.add(identity)
        partial_texture_names.update(row["texture_names"])
        normalized.append({**row, "observed_counts": observed_counts})
    normalized.sort(
        key=lambda row: (row["page"], row["event"], row["runtime_index_sha256"])
    )
    union_counts: Counter = Counter()
    observation_rows: list[dict] = []
    total_observed = 0
    for row in normalized:
        counts = row.pop("observed_counts")
        newly_covered = counts - union_counts
        overlap = counts & union_counts
        union_counts |= counts
        total_observed += row["observed_triangles"]
        observation_rows.append(
            {
                **row,
                "new_triangle_occurrences": sum(newly_covered.values()),
                "already_covered_triangle_occurrences": sum(overlap.values()),
                "cumulative_union_triangle_occurrences": sum(union_counts.values()),
            }
        )
    if union_counts - retail_counts:
        raise MaterialCoverageUnionError(
            "runtime material union exceeds the retail triangle multiset"
        )
    missing_counts = retail_counts - union_counts
    covered_triangles = _expand_counter(retail_triangles, union_counts)
    missing_triangles = _expand_counter(retail_triangles, missing_counts)
    covered_count = len(covered_triangles)
    missing_count = len(missing_triangles)
    full_coverage = missing_count == 0
    report = {
        "format": "infamous-character-material-coverage-union",
        "version": 1,
        "tool_inventory_id": "xpp-tool.character-material-coverage-union.v1",
        "status": (
            "full-retail-material-coverage-proved"
            if full_coverage
            else "partial-retail-material-coverage-proved"
        ),
        "authorities": {
            "xpp_bytes": len(xpp_data),
            "xpp_sha256": xpp_sha256,
            "texture_allowlist_sha256": allowlist_identity,
            "retail_index_sha256": contract.index_sha256,
        },
        "component": {
            "record_offset": record_offset,
            "vertices": contract.vertex_count,
            "retail_triangle_occurrences": len(retail_triangles),
            "uv_payload_sha256": common["uv_payload_sha256"],
            "uv_byte_offset": common["uv_byte_offset"],
            "texture_family": common["texture_family"],
            "texture_names": [item["name"] for item in common["textures"]],
        },
        "observations": observation_rows,
        "union": {
            "observation_count": len(observation_rows),
            "distinct_runtime_index_payloads": len(
                {row["runtime_index_sha256"] for row in observation_rows}
            ),
            "total_observed_triangle_occurrences": total_observed,
            "redundant_triangle_occurrences": total_observed - covered_count,
            "covered_retail_triangle_occurrences": covered_count,
            "unobserved_retail_triangle_occurrences": missing_count,
            "full_retail_material_coverage_proved": full_coverage,
            "covered_triangle_multiset_sha256": _sha256(
                _triangle_bytes(covered_triangles)
            ),
            "unobserved_triangle_multiset_sha256": _sha256(
                _triangle_bytes(missing_triangles)
            ),
        },
        "payload_bytes_serialized": False,
        "limitations": {
            "one_texture_family_only": True,
            "triangle_order_is_retained_only_as_hashes": True,
            "position_semantic": False,
            "retail_normals_tangents_proved": False,
            "full_character": False,
            "rigged": False,
            "four_x_textures": False,
            "authored_pbr": False,
            "rpcs3_mod_round_trip": False,
            "native_decomp_import": False,
        },
        "next_gate": (
            "promote this component's material coverage in the canonical ledger"
            if full_coverage
            else "capture another exact draw of this record/family that covers the remaining triangle multiset"
        ),
    }
    if partial_observations:
        report["component"]["compatible_partial_texture_names"] = sorted(
            partial_texture_names
        )
        report["union"].update(
            {
                "full_range_observation_count": len(observations),
                "partial_range_observation_count": len(partial_observations),
            }
        )
    anchor_texture_names = {item["name"] for item in common["textures"]}
    compatible_full_range_texture_names = set(compatible_full_range_textures)
    if compatible_full_range_texture_names != anchor_texture_names:
        report["component"]["compatible_full_range_texture_names"] = sorted(
            compatible_full_range_texture_names
        )
    payload = render_material_coverage_union(report)
    if len(payload) > MAX_OUTPUT_BYTES:
        raise MaterialCoverageUnionError("coverage union report exceeds the byte bound")
    covered_indices = tuple(
        vertex for triangle in covered_triangles for vertex in triangle
    )
    return report, covered_indices


def build_material_coverage_union(
    xpp_path: Path,
    xpp_sha256: str,
    texture_allowlist: Path,
    observations: Sequence[MaterialCoverageObservation],
    *,
    record_offset: int,
    partial_observations: Sequence[PartialMaterialCoverageObservation] = (),
) -> dict:
    """Build the public payload-free union receipt."""

    report, _covered_indices = _build_material_coverage_union(
        xpp_path,
        xpp_sha256,
        texture_allowlist,
        observations,
        record_offset=record_offset,
        partial_observations=partial_observations,
    )
    return report


def build_material_coverage_union_with_indices(
    xpp_path: Path,
    xpp_sha256: str,
    texture_allowlist: Path,
    observations: Sequence[MaterialCoverageObservation],
    *,
    record_offset: int,
    partial_observations: Sequence[PartialMaterialCoverageObservation] = (),
) -> tuple[dict, tuple[int, ...]]:
    """Return the checked union plus private in-memory indices for a GLB export."""

    return _build_material_coverage_union(
        xpp_path,
        xpp_sha256,
        texture_allowlist,
        observations,
        record_offset=record_offset,
        partial_observations=partial_observations,
    )


def render_material_coverage_union(report: dict) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_new_material_coverage_union(path: Path, report: dict) -> None:
    """Atomically publish a deterministic report without replacing evidence."""

    if path.is_symlink() or path.exists():
        raise MaterialCoverageUnionError("coverage union output already exists")
    payload = render_material_coverage_union(report)
    if len(payload) > MAX_OUTPUT_BYTES:
        raise MaterialCoverageUnionError("coverage union report exceeds the byte bound")
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
            raise MaterialCoverageUnionError(
                "coverage union output appeared during publication"
            )
        os.link(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
