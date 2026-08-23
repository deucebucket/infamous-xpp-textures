"""Bounded batch census for shader-bindable character material candidates."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from .runtime_topology_export import RuntimeTopologyExportError, _load_bundle
from .shader_lineage import (
    ShaderLineageError,
    _read_pinned_json,
    build_character_uv_texture_binding,
    render_character_uv_texture_binding,
)


class CharacterMaterialCensusError(ValueError):
    """Raised when a bounded candidate census is invalid or unsafe."""


MAX_SOURCE_EVENTS = 17 * 16
MAX_CANDIDATES = 16
MAX_REPORT_BYTES = 512 * 1024


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _candidate_identity(value: object) -> tuple[int, int, dict] | None:
    if not isinstance(value, dict):
        raise CharacterMaterialCensusError("source census event is not an object")
    page = value.get("page")
    event = value.get("event")
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or not 1 <= page <= 17
        or isinstance(event, bool)
        or not isinstance(event, int)
        or not 1 <= event <= 16
    ):
        raise CharacterMaterialCensusError("source census page/event is invalid")
    mapping = value.get("mapping")
    if not value.get("same_xpp_source_record_proved") or not isinstance(mapping, dict):
        return None
    range_count = mapping.get("range_count")
    source_vertex_count = mapping.get("source_vertex_count")
    stream_zero_record_bytes = mapping.get("stream_zero_record_bytes")
    if not mapping.get("full_vertex_range"):
        return None
    if (
        isinstance(range_count, bool)
        or not isinstance(range_count, int)
        or range_count <= 0
        or range_count != source_vertex_count
        or isinstance(stream_zero_record_bytes, bool)
        or not isinstance(stream_zero_record_bytes, int)
        or not 1 <= stream_zero_record_bytes <= 64
    ):
        raise CharacterMaterialCensusError("full-range source mapping is invalid")
    record_offset = mapping.get("record_offset")
    if (
        isinstance(record_offset, bool)
        or not isinstance(record_offset, int)
        or record_offset < 0
    ):
        raise CharacterMaterialCensusError("full-range source record offset is invalid")
    return event, record_offset, mapping


def build_character_material_candidate_census(
    bundle: Path,
    texture_allowlist: Path,
    capture_key_exclusion: Path | None,
    source_census_path: Path,
    source_census_sha256: str,
    character_census_path: Path,
    character_census_sha256: str,
    *,
    page_number: int,
    character_side: str,
    excluded_candidates: tuple[tuple[int, int], ...] = (),
) -> dict:
    """Classify every unexcluded full-record candidate on one exact page."""

    if (
        isinstance(page_number, bool)
        or not isinstance(page_number, int)
        or not 1 <= page_number <= 17
        or character_side not in ("left", "right")
    ):
        raise CharacterMaterialCensusError("page or character side is invalid")
    if len(excluded_candidates) > MAX_CANDIDATES:
        raise CharacterMaterialCensusError("candidate exclusions exceed the bound")
    normalized_exclusions: list[tuple[int, int]] = []
    for event, record_offset in excluded_candidates:
        if (
            isinstance(event, bool)
            or not isinstance(event, int)
            or not 1 <= event <= 16
            or isinstance(record_offset, bool)
            or not isinstance(record_offset, int)
            or record_offset < 0
        ):
            raise CharacterMaterialCensusError("candidate exclusion is invalid")
        normalized_exclusions.append((event, record_offset))
    if len(set(normalized_exclusions)) != len(normalized_exclusions):
        raise CharacterMaterialCensusError("candidate exclusions contain duplicates")
    exclusion_set = set(normalized_exclusions)

    try:
        source_census, source_identity = _read_pinned_json(
            source_census_path, source_census_sha256, "source census"
        )
        character_census, character_identity = _read_pinned_json(
            character_census_path, character_census_sha256, "character census"
        )
    except ShaderLineageError as exc:
        raise CharacterMaterialCensusError(str(exc)) from exc
    if (
        source_census.get("kind") != "if1-rsx-paged-xpp-source-census"
        or source_census.get("schema_version") != 1
    ):
        raise CharacterMaterialCensusError("source census has the wrong schema")
    if character_census.get("format") != "infamous-character-asset-census":
        raise CharacterMaterialCensusError("character census has the wrong format")
    source_events = source_census.get("events")
    if not isinstance(source_events, list) or len(source_events) > MAX_SOURCE_EVENTS:
        raise CharacterMaterialCensusError("source census events exceed the bound")
    try:
        completion, _events, allowlist_identity = _load_bundle(
            bundle, texture_allowlist, capture_key_exclusion
        )
    except RuntimeTopologyExportError as exc:
        raise CharacterMaterialCensusError(str(exc)) from exc
    if completion.get("format") not in (
        "if1-texture-bound-topology-v3",
        "if1-texture-bound-topology-v4",
    ):
        raise CharacterMaterialCensusError(
            "candidate census requires a complete v3/v4 bundle"
        )

    eligible: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for source_event in source_events:
        identity = _candidate_identity(source_event)
        if source_event["page"] != page_number or identity is None:
            continue
        event, record_offset, mapping = identity
        key = (event, record_offset)
        if key in seen:
            raise CharacterMaterialCensusError(
                "source census repeats a candidate identity"
            )
        seen.add(key)
        eligible.append(
            {
                "event": event,
                "record_offset": record_offset,
                "source_vertex_count": mapping["source_vertex_count"],
                "stream_zero_record_bytes": mapping["stream_zero_record_bytes"],
            }
        )
    eligible.sort(key=lambda item: (item["event"], item["record_offset"]))
    if len(eligible) > MAX_CANDIDATES:
        raise CharacterMaterialCensusError("full-range candidates exceed the bound")
    eligible_keys = {(item["event"], item["record_offset"]) for item in eligible}
    unknown_exclusions = exclusion_set - eligible_keys
    if unknown_exclusions:
        raise CharacterMaterialCensusError(
            "candidate exclusion is not eligible on this page"
        )
    selected = [
        item
        for item in eligible
        if (item["event"], item["record_offset"]) not in exclusion_set
    ]
    if not selected:
        raise CharacterMaterialCensusError("candidate census selected no new records")

    accepted: list[dict] = []
    rejected: list[dict] = []
    for candidate in selected:
        event = candidate["event"]
        record_offset = candidate["record_offset"]
        try:
            lineage = build_character_uv_texture_binding(
                bundle,
                texture_allowlist,
                capture_key_exclusion,
                source_census_path,
                source_census_sha256,
                character_census_path,
                character_census_sha256,
                event_number=event,
                page_number=page_number,
                record_offset=record_offset,
                character_side=character_side,
            )
        except ShaderLineageError as exc:
            rejected.append(
                {
                    **candidate,
                    "status": "rejected",
                    "reason": str(exc),
                }
            )
            continue
        lineage_bytes = render_character_uv_texture_binding(lineage)
        shader = lineage["shader_lineage"]
        accepted.append(
            {
                **candidate,
                "status": "accepted",
                "lineage_report_sha256": _sha256(lineage_bytes),
                "source_block": lineage["selection"]["source_block"],
                "vertex_input_attribute": shader["vertex_input_attribute"],
                "vertex_input_type": shader["vertex_input_type"],
                "vertex_input_components": shader["vertex_input_components"],
                "vertex_input_byte_offset": shader["vertex_input_byte_offset"],
                "fragment_input_name": shader["fragment_input_name"],
                "texture_family": lineage["texture_family"],
                "texture_bindings": [
                    {
                        "sampler": item["sampler"],
                        "name": item["name"],
                        "name_suffix": item["name_suffix"],
                        "runtime_prefix_sha256": item["runtime_prefix_sha256"],
                    }
                    for item in lineage["texture_bindings"]
                ],
            }
        )

    report = {
        "format": "infamous-character-material-candidate-census",
        "version": 1,
        "tool_inventory_id": "xpp-tool.character-material-candidate-census.v1",
        "status": "full-range-candidates-classified",
        "authorities": {
            "bundle_format": completion["format"],
            "texture_allowlist_sha256": allowlist_identity,
            "source_census_sha256": source_identity,
            "character_census_sha256": character_identity,
        },
        "selection": {
            "page": page_number,
            "character_side": character_side,
            "eligible_full_range_candidates": len(eligible),
            "excluded_completed_candidates": [
                {"event": event, "record_offset": record_offset}
                for event, record_offset in sorted(exclusion_set)
            ],
            "selected_candidates": len(selected),
        },
        "accepted": accepted,
        "rejected": rejected,
        "summary": {
            "accepted": len(accepted),
            "rejected": len(rejected),
            "all_selected_candidates_classified": len(accepted) + len(rejected)
            == len(selected),
        },
        "payload_bytes_serialized": False,
        "limitations": {
            "candidate_selection_requires_full_source_vertex_range": True,
            "accepted_candidate_is_complete_character": False,
            "accepted_candidate_has_complete_material_coverage": False,
            "accepted_candidate_has_authored_pbr": False,
            "accepted_candidate_is_rpcs3_mod_ready": False,
            "accepted_candidate_is_native_decomp_ready": False,
        },
        "next_gate": (
            "write each accepted full lineage with character-uv-texture-binding, "
            "export strict and preview GLBs, render immediately, and reconcile the "
            "component into the canonical completion inventory"
        ),
    }
    if len(render_character_material_candidate_census(report)) > MAX_REPORT_BYTES:
        raise CharacterMaterialCensusError(
            "candidate census report exceeds the byte bound"
        )
    return report


def render_character_material_candidate_census(report: dict) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_new_character_material_candidate_census(path: Path, report: dict) -> None:
    """Atomically publish a deterministic report without replacing evidence."""

    if path.is_symlink() or path.exists():
        raise CharacterMaterialCensusError("candidate census output already exists")
    payload = render_character_material_candidate_census(report)
    if len(payload) > MAX_REPORT_BYTES:
        raise CharacterMaterialCensusError(
            "candidate census report exceeds the byte bound"
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
            raise CharacterMaterialCensusError(
                "candidate census output appeared during publication"
            )
        os.link(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
