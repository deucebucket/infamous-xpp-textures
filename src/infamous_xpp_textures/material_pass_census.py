"""Exact cross-material triangle census for one retail character record."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
from collections import Counter
from dataclasses import dataclass
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
from .material_coverage import (
    MAX_MATERIAL_REPORT_BYTES,
    MAX_XPP_BYTES,
    MaterialCoverageObservation,
    _bundle_completion_receipt,
    _expand_counter,
    _triangle_bytes,
)
from .runtime_topology_export import (
    RuntimeTopologyExportError,
    _load_bundle,
    _paged_capture_metadata,
    _read_payload,
)
from .xpp import parse_xpp


MAX_OBSERVATIONS = 32
MAX_OUTPUT_BYTES = 512 * 1024


class MaterialPassCensusError(ValueError):
    """Raised when cross-material observations cannot be reconciled exactly."""


@dataclass(frozen=True)
class _CheckedObservation:
    """Private exact triangle counts plus their payload-free normalized receipt."""

    row: dict
    counts: Counter
    pass_key: tuple


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _one(values: list, label: str):
    if len(values) != 1:
        raise MaterialPassCensusError(f"expected one {label}, found {len(values)}")
    return values[0]


def _canonical_sha256(value: object, label: str) -> str:
    if not _valid_sha256(value):
        raise MaterialPassCensusError(f"{label} is not a canonical SHA-256")
    assert isinstance(value, str)
    return value


def _signature_payload(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _relationship(left: _CheckedObservation, right: _CheckedObservation) -> dict:
    overlap = left.counts & right.counts
    left_only = left.counts - right.counts
    right_only = right.counts - left.counts
    if left.counts == right.counts:
        relation = "identical"
    elif not left_only:
        relation = "left-subset"
    elif not right_only:
        relation = "left-superset"
    elif not overlap:
        relation = "disjoint"
    else:
        relation = "partial-overlap"
    return {
        "left_observation_id": left.row["observation_id"],
        "right_observation_id": right.row["observation_id"],
        "relation": relation,
        "intersection_triangle_occurrences": sum(overlap.values()),
        "left_only_triangle_occurrences": sum(left_only.values()),
        "right_only_triangle_occurrences": sum(right_only.values()),
        "union_triangle_occurrences": sum((left.counts | right.counts).values()),
        "same_pass_signature": left.row["pass_signature_sha256"]
        == right.row["pass_signature_sha256"],
        "same_runtime_index_payload": left.row["runtime_index_sha256"]
        == right.row["runtime_index_sha256"],
    }


def build_material_pass_census(
    xpp_path: Path,
    xpp_sha256: str,
    texture_allowlist: Path,
    observations: Sequence[MaterialCoverageObservation],
    *,
    record_offset: int,
) -> dict:
    """Revalidate and compare every exact material pass for one source record."""

    if (
        isinstance(record_offset, bool)
        or not isinstance(record_offset, int)
        or record_offset < 0
    ):
        raise MaterialPassCensusError("record offset is invalid")
    if not 2 <= len(observations) <= MAX_OBSERVATIONS:
        raise MaterialPassCensusError("observation count is invalid")
    _canonical_sha256(xpp_sha256, "XPP SHA-256 pin")
    try:
        xpp_data = _read_pinned(xpp_path, xpp_sha256, MAX_XPP_BYTES, "retail XPP")
        parsed = parse_xpp(xpp_data, len(xpp_data))
        contract = _one(
            [
                item
                for item in find_skinned_geometry_contracts(xpp_data, parsed)
                if item.record_offset == record_offset
            ],
            "retail character geometry contract",
        )
    except (
        CharacterComponentLedgerError,
        CharacterMaterialExportError,
        ValueError,
    ) as exc:
        raise MaterialPassCensusError(str(exc)) from exc
    index_start = parsed.data_offset + contract.index_offset
    index_end = index_start + contract.index_byte_count
    index_payload = xpp_data[index_start:index_end]
    if (
        len(index_payload) != contract.index_byte_count
        or _sha256(index_payload) != contract.index_sha256
        or contract.index_count % 3
    ):
        raise MaterialPassCensusError("retail index topology failed exact identity")
    retail_indices = struct.unpack(f">{contract.index_count}H", index_payload)
    if (
        not retail_indices
        or min(retail_indices) != 0
        or max(retail_indices) >= contract.vertex_count
    ):
        raise MaterialPassCensusError("retail index topology is invalid")
    retail_triangles = [
        tuple(retail_indices[offset : offset + 3])
        for offset in range(0, len(retail_indices), 3)
    ]
    retail_counts = Counter(retail_triangles)

    checked: list[_CheckedObservation] = []
    seen_paths: set[Path] = set()
    seen_reports: set[str] = set()
    seen_events: set[tuple[int, int, str]] = set()
    allowlist_identity: str | None = None
    for number, observation in enumerate(observations, start=1):
        resolved = observation.report.resolve()
        if resolved in seen_paths:
            raise MaterialPassCensusError("material report path is duplicated")
        seen_paths.add(resolved)
        try:
            report_payload = _read_pinned(
                observation.report,
                observation.report_sha256,
                MAX_MATERIAL_REPORT_BYTES,
                f"material report {number}",
            )
            raw_report = _load_json(report_payload, f"material report {number}")
            report = _validate_material_report(raw_report, observation.report_sha256)
        except CharacterComponentLedgerError as exc:
            raise MaterialPassCensusError(str(exc)) from exc
        if observation.report_sha256 in seen_reports:
            raise MaterialPassCensusError("material report content is duplicated")
        seen_reports.add(observation.report_sha256)
        if (
            raw_report.get("presentation_mode") != "observed-only"
            or report["tool_inventory_id"] != "xpp-tool.character-material-export.v1"
        ):
            raise MaterialPassCensusError(
                "pass census requires strict one-draw observed-only material reports"
            )
        topology = report["topology"]
        source = report["source"]
        if (
            report["record_offset"] != record_offset
            or source["xpp_sha256"] != xpp_sha256
            or source["xpp_bytes"] != len(xpp_data)
            or report["index_sha256"] != contract.index_sha256
            or topology["vertices"] != contract.vertex_count
            or topology["triangles"] != len(retail_triangles)
        ):
            raise MaterialPassCensusError(
                "material report conflicts with the pinned retail component"
            )
        try:
            completion, events, observed_allowlist = _load_bundle(
                observation.bundle,
                texture_allowlist,
                observation.capture_key_exclusion,
            )
        except RuntimeTopologyExportError as exc:
            raise MaterialPassCensusError(str(exc)) from exc
        if allowlist_identity is None:
            allowlist_identity = observed_allowlist
        elif allowlist_identity != observed_allowlist:
            raise MaterialPassCensusError(
                "runtime bundles do not share one texture allowlist"
            )
        authorities = raw_report.get("authorities")
        if not isinstance(authorities, dict):
            raise MaterialPassCensusError("material authorities are malformed")
        paging = _paged_capture_metadata(completion)
        expected_exclusion = (
            paging["exclusion_manifest_sha256"] if paging is not None else None
        )
        if (
            completion.get("format") != authorities.get("bundle_format")
            or observed_allowlist != authorities.get("texture_allowlist_sha256")
            or expected_exclusion != authorities.get("capture_key_exclusion_sha256")
        ):
            raise MaterialPassCensusError(
                "runtime bundle authorities drifted from the material report"
            )
        event = events.get(report["event"])
        if (
            event is None
            or event.draw_event != report["draw_event"]
            or event.index_sha256 != report["material_event_index_sha256"]
            or event.index_count != topology["material_observed_triangles"] * 3
            or event.index_bytes != event.index_count * 2
        ):
            raise MaterialPassCensusError(
                "runtime event identity drifted from the material report"
            )
        try:
            runtime_payload = _read_payload(
                observation.bundle,
                event.index_payload_file,
                event.index_bytes,
                event.index_sha256,
            )
            if len(runtime_payload) != event.index_bytes:
                raise MaterialPassCensusError(
                    "runtime index payload extent drifted from the event"
                )
            observed_indices = struct.unpack(f">{event.index_count}H", runtime_payload)
            _triangle_partition(retail_indices, observed_indices)
        except (RuntimeTopologyExportError, CharacterMaterialExportError) as exc:
            raise MaterialPassCensusError(str(exc)) from exc
        event_identity = (report["page"], report["event"], event.index_sha256)
        if event_identity in seen_events:
            raise MaterialPassCensusError(
                "material observations repeat one page/event/index identity"
            )
        seen_events.add(event_identity)
        observed_triangles = [
            tuple(observed_indices[offset : offset + 3])
            for offset in range(0, len(observed_indices), 3)
        ]
        counts = Counter(observed_triangles)
        vertex_program_sha256 = _canonical_sha256(
            event.vertex_program_sha256, "vertex-program identity"
        )
        fragment_program_sha256 = _canonical_sha256(
            event.fragment_program_sha256, "fragment-program identity"
        )
        textures = [
            {
                "suffix": item["suffix"],
                "name": item["name"],
                "descriptor_index": item["descriptor_index"],
                "runtime_prefix_sha256": item["runtime_prefix_sha256"],
            }
            for item in report["textures"]
        ]
        pass_authority = {
            "vertex_program_sha256": vertex_program_sha256,
            "fragment_program_sha256": fragment_program_sha256,
            "uv_payload_sha256": report["uv"]["payload_sha256"],
            "uv_byte_offset": report["uv"]["byte_offset"],
            "texture_family": report["texture_family"],
            "textures": textures,
        }
        pass_signature = _sha256(_signature_payload(pass_authority))
        observation_authority = {
            "page": report["page"],
            "event": report["event"],
            "material_report_sha256": observation.report_sha256,
            "runtime_index_sha256": event.index_sha256,
        }
        observation_id = _sha256(_signature_payload(observation_authority))
        row = {
            "observation_id": observation_id,
            "page": report["page"],
            "event": report["event"],
            "draw_event": report["draw_event"],
            "material_report_sha256": observation.report_sha256,
            "lineage_sha256": report["lineage_sha256"],
            "bundle_format": completion["format"],
            "bundle_completion": _bundle_completion_receipt(observation.bundle),
            "capture_key_exclusion_sha256": expected_exclusion,
            "position_payload_sha256": source["position_payload_sha256"],
            "runtime_index_sha256": event.index_sha256,
            "vertex_program_sha256": vertex_program_sha256,
            "fragment_program_sha256": fragment_program_sha256,
            "uv_payload_sha256": report["uv"]["payload_sha256"],
            "uv_byte_offset": report["uv"]["byte_offset"],
            "texture_family": report["texture_family"],
            "textures": textures,
            "pass_signature_sha256": pass_signature,
            "observed_triangle_occurrences": len(observed_triangles),
            "observed_triangle_multiset_sha256": _sha256(
                _triangle_bytes(observed_triangles)
            ),
        }
        pass_key = (
            pass_signature,
            report["page"],
            report["event"],
            observation.report_sha256,
        )
        checked.append(_CheckedObservation(row, counts, pass_key))

    if allowlist_identity is None:
        raise MaterialPassCensusError("pass census has no validated observations")
    checked.sort(key=lambda item: item.pass_key)
    relationships = [
        _relationship(left, right)
        for left_index, left in enumerate(checked)
        for right in checked[left_index + 1 :]
    ]
    any_pass_counts: Counter = Counter()
    for item in checked:
        any_pass_counts |= item.counts
    if any_pass_counts - retail_counts:
        raise MaterialPassCensusError("any-pass union exceeds retail topology")
    missing_counts = retail_counts - any_pass_counts
    covered_triangles = _expand_counter(retail_triangles, any_pass_counts)
    missing_triangles = _expand_counter(retail_triangles, missing_counts)

    groups: dict[str, dict] = {}
    for item in checked:
        signature = item.row["pass_signature_sha256"]
        group = groups.setdefault(
            signature,
            {
                "pass_signature_sha256": signature,
                "vertex_program_sha256": item.row["vertex_program_sha256"],
                "fragment_program_sha256": item.row["fragment_program_sha256"],
                "uv_payload_sha256": item.row["uv_payload_sha256"],
                "uv_byte_offset": item.row["uv_byte_offset"],
                "texture_family": item.row["texture_family"],
                "textures": item.row["textures"],
                "observation_ids": [],
            },
        )
        group["observation_ids"].append(item.row["observation_id"])
    pass_groups = []
    for signature in sorted(groups):
        group = groups[signature]
        group["observation_ids"].sort()
        group["observation_count"] = len(group["observation_ids"])
        pass_groups.append(group)

    coextensive_cross_signature = sum(
        row["relation"] == "identical" and not row["same_pass_signature"]
        for row in relationships
    )
    partial_cross_signature = sum(
        row["relation"] == "partial-overlap" and not row["same_pass_signature"]
        for row in relationships
    )

    full = not missing_triangles
    report = {
        "format": "infamous-character-material-pass-census",
        "version": 1,
        "tool_inventory_id": "xpp-tool.character-material-pass-census.v1",
        "status": "exact-cross-material-pass-census",
        "authorities": {
            "xpp_sha256": xpp_sha256,
            "xpp_bytes": len(xpp_data),
            "texture_allowlist_sha256": allowlist_identity,
            "retail_index_sha256": contract.index_sha256,
        },
        "component": {
            "record_offset": record_offset,
            "vertices": contract.vertex_count,
            "retail_triangle_occurrences": len(retail_triangles),
        },
        "observations": [item.row for item in checked],
        "pass_groups": pass_groups,
        "relationships": relationships,
        "any_pass_union": {
            "observation_count": len(checked),
            "pass_signature_count": len(pass_groups),
            "runtime_index_payload_count": len(
                {item.row["runtime_index_sha256"] for item in checked}
            ),
            "relationship_count": len(relationships),
            "coextensive_cross_signature_relationship_count": (
                coextensive_cross_signature
            ),
            "partial_cross_signature_relationship_count": partial_cross_signature,
            "covered_retail_triangle_occurrences": len(covered_triangles),
            "unobserved_retail_triangle_occurrences": len(missing_triangles),
            "full_retail_material_coverage_proved": full,
            "covered_triangle_multiset_sha256": _sha256(
                _triangle_bytes(covered_triangles)
            ),
            "unobserved_triangle_multiset_sha256": _sha256(
                _triangle_bytes(missing_triangles)
            ),
        },
        "payload_bytes_serialized": False,
        "limitations": {
            "pass_roles_interpreted_as_pbr": False,
            "material_compositing_order_proved": False,
            "full_character": False,
            "rigged": False,
            "four_x_textures": False,
            "rpcs3_mod_round_trip": False,
            "native_decomp_import": False,
        },
        "next_gate": (
            "preserve every exact pass signature in the canonical component ledger"
            if full
            else "capture a genuinely different draw only if it can expose the remaining any-pass triangle multiset"
        ),
    }
    payload = render_material_pass_census(report)
    if len(payload) > MAX_OUTPUT_BYTES:
        raise MaterialPassCensusError("material pass census exceeds the byte bound")
    return report


def render_material_pass_census(report: dict) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_new_material_pass_census(path: Path, report: dict) -> None:
    """Atomically publish a deterministic census without replacing evidence."""

    if path.is_symlink() or path.exists():
        raise MaterialPassCensusError("material pass census output already exists")
    payload = render_material_pass_census(report)
    if len(payload) > MAX_OUTPUT_BYTES:
        raise MaterialPassCensusError("material pass census exceeds the byte bound")
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
            raise MaterialPassCensusError(
                "material pass census output appeared during publication"
            )
        os.link(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
