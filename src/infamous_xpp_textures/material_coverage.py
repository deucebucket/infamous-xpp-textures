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


def _build_material_coverage_union(
    xpp_path: Path,
    xpp_sha256: str,
    texture_allowlist: Path,
    observations: Sequence[MaterialCoverageObservation],
    *,
    record_offset: int,
) -> tuple[dict, tuple[int, ...]]:
    """Union exact runtime material triangles for one retail source record."""

    if (
        isinstance(record_offset, bool)
        or not isinstance(record_offset, int)
        or record_offset < 0
    ):
        raise MaterialCoverageUnionError("record offset is invalid")
    if not 1 <= len(observations) <= MAX_OBSERVATIONS:
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
        report_common = {
            "xpp_sha256": report["source"]["xpp_sha256"],
            "xpp_bytes": report["source"]["xpp_bytes"],
            "vertices": report["topology"]["vertices"],
            "retail_triangles": report["topology"]["triangles"],
            "retail_index_sha256": report["index_sha256"],
            "uv_payload_sha256": report["uv"]["payload_sha256"],
            "uv_byte_offset": report["uv"]["byte_offset"],
            "texture_family": report["texture_family"],
            "textures": report["textures"],
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
                "observed_counts": observed_counts,
                "observed_triangles": len(observed_triangles),
            }
        )

    if common is None or allowlist_identity is None:
        raise MaterialCoverageUnionError("coverage union has no validated observations")
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
) -> dict:
    """Build the public payload-free union receipt."""

    report, _covered_indices = _build_material_coverage_union(
        xpp_path,
        xpp_sha256,
        texture_allowlist,
        observations,
        record_offset=record_offset,
    )
    return report


def build_material_coverage_union_with_indices(
    xpp_path: Path,
    xpp_sha256: str,
    texture_allowlist: Path,
    observations: Sequence[MaterialCoverageObservation],
    *,
    record_offset: int,
) -> tuple[dict, tuple[int, ...]]:
    """Return the checked union plus private in-memory indices for a GLB export."""

    return _build_material_coverage_union(
        xpp_path,
        xpp_sha256,
        texture_allowlist,
        observations,
        record_offset=record_offset,
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
