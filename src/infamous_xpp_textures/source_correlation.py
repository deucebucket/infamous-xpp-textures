"""Bind paged RSX draw streams to exact XPP character-record byte slices."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
import struct

from .character import (
    GEOMETRY_HEAP_CHUNK,
    find_edge_geometry_envelopes,
    find_skinned_geometry_contracts,
)
from .page_correlation import PageCorrelationError, _load_page_chain
from .runtime_topology_export import _read_payload
from .xpp import parse_xpp


class SourceCorrelationError(ValueError):
    """Raised when an XPP-to-runtime source binding is not exact and bounded."""


_MAX_STREAM_ZERO_RECORD_BYTES = 64
MAX_XPP_SOURCE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_CORRELATION_REPORT_BYTES = 256 * 1024


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _triangles(payload: bytes, count: int) -> tuple[tuple[int, int, int], ...]:
    if count <= 0 or count % 3 or len(payload) != count * 2:
        raise SourceCorrelationError("index payload is not a bounded triangle list")
    indices = struct.unpack(f">{count}H", payload)
    return tuple(tuple(indices[offset : offset + 3]) for offset in range(0, count, 3))


def _ordered_multiset(
    retail: tuple[tuple[int, int, int], ...], selected: Counter
) -> tuple[tuple[int, int, int], ...]:
    remaining = selected.copy()
    result: list[tuple[int, int, int]] = []
    for triangle in retail:
        if remaining[triangle] > 0:
            result.append(triangle)
            remaining[triangle] -= 1
    if any(remaining.values()):
        raise SourceCorrelationError(
            "runtime index triangle multiset exceeds the retail record"
        )
    return tuple(result)


def _triangle_sha256(triangles: tuple[tuple[int, int, int], ...]) -> str:
    return _sha256(b"".join(struct.pack(">3H", *triangle) for triangle in triangles))


def _source_records(xpp_data: bytes) -> tuple[dict, list[dict]]:
    if not xpp_data or len(xpp_data) > MAX_XPP_SOURCE_BYTES:
        raise SourceCorrelationError("XPP source is empty or exceeds the 64 MiB bound")
    try:
        parsed = parse_xpp(xpp_data, len(xpp_data))
        envelopes = find_edge_geometry_envelopes(xpp_data, parsed)
        contracts = find_skinned_geometry_contracts(xpp_data, parsed)
    except ValueError as exc:
        raise SourceCorrelationError(str(exc)) from exc
    if not envelopes or len(contracts) != len(envelopes):
        raise SourceCorrelationError(
            "XPP character envelopes do not have complete topology-contract coverage"
        )
    if len({item.record_offset for item in envelopes}) != len(envelopes) or len(
        {item.record_offset for item in contracts}
    ) != len(contracts):
        raise SourceCorrelationError("XPP character record offsets are duplicated")
    envelope_by_record = {item.record_offset: item for item in envelopes}
    if set(envelope_by_record) != {item.record_offset for item in contracts}:
        raise SourceCorrelationError(
            "XPP character envelopes and topology contracts do not reconcile"
        )
    heaps = [chunk for chunk in parsed.chunks if chunk.type_tag == GEOMETRY_HEAP_CHUNK]
    if len(heaps) != 1:
        raise SourceCorrelationError("XPP must contain exactly one geometry heap")
    heap = heaps[0]
    known_offsets = {
        offset
        for envelope in envelopes
        for offset in (*envelope.stream_offsets, envelope.index_offset)
    }
    records: list[dict] = []
    for contract in contracts:
        envelope = envelope_by_record[contract.record_offset]
        if len(envelope.stream_offsets) != 4:
            raise SourceCorrelationError("XPP character envelope stream count changed")
        stream_offset = envelope.stream_offsets[0]
        later_offsets = sorted(
            offset for offset in known_offsets if offset > stream_offset
        )
        stream_end_limit = (
            later_offsets[0] if later_offsets else heap.offset + heap.size
        )
        if (
            stream_offset < heap.offset
            or stream_end_limit <= stream_offset
            or stream_end_limit > heap.offset + heap.size
        ):
            raise SourceCorrelationError(
                "XPP stream-zero candidate has no bounded geometry-heap extent"
            )
        absolute_start = parsed.data_offset + stream_offset
        absolute_end = parsed.data_offset + stream_end_limit
        if absolute_start < 0 or absolute_end > len(xpp_data):
            raise SourceCorrelationError("XPP stream-zero candidate is truncated")
        payload = xpp_data[absolute_start:absolute_end]
        index_start = parsed.data_offset + envelope.index_offset
        index_end = index_start + contract.index_byte_count
        index_payload = xpp_data[index_start:index_end]
        if (
            len(index_payload) != contract.index_byte_count
            or _sha256(index_payload) != contract.index_sha256
        ):
            raise SourceCorrelationError(
                "XPP character index payload failed exact identity"
            )
        retail_triangles = _triangles(index_payload, contract.index_count)
        retail_vertices = tuple(
            vertex for triangle in retail_triangles for vertex in triangle
        )
        if min(retail_vertices) != 0 or max(retail_vertices) >= contract.vertex_count:
            raise SourceCorrelationError("XPP character index topology is invalid")
        records.append(
            {
                "record_offset": contract.record_offset,
                "vertex_count": contract.vertex_count,
                "index_count": contract.index_count,
                "index_sha256": contract.index_sha256,
                "index_payload": index_payload,
                "stream_zero_offset": stream_offset,
                "stream_zero_end_limit": stream_end_limit,
                "payload": payload,
            }
        )
    return (
        {
            "source_sha256": _sha256(xpp_data),
            "source_size": len(xpp_data),
            "xpp_version": parsed.version,
            "character_record_count": len(records),
            "contract_coverage": f"{len(contracts)}/{len(envelopes)}",
        },
        sorted(records, key=lambda item: item["record_offset"]),
    )


def correlate_paged_draws_to_xpp(
    xpp_data: bytes,
    xpp_source_name: str,
    page_bundles: tuple[Path, ...],
    texture_allowlist: Path,
    page_capture_key_exclusions: tuple[Path | None, ...],
) -> dict:
    """Prove unique exact XPP stream-zero slices in an exact v3/v4 page chain."""

    if (
        not isinstance(xpp_source_name, str)
        or not xpp_source_name
        or Path(xpp_source_name).name != xpp_source_name
    ):
        raise SourceCorrelationError("XPP source label must be a plain filename")
    source, records = _source_records(xpp_data)
    record_by_offset = {record["record_offset"]: record for record in records}
    try:
        pages, page_events, allowlist_sha256 = _load_page_chain(
            page_bundles, texture_allowlist, page_capture_key_exclusions
        )
    except PageCorrelationError as exc:
        raise SourceCorrelationError(str(exc)) from exc

    events_report: list[dict] = []
    mapped_records: set[int] = set()
    full_index_records: set[int] = set()
    page_summaries: list[dict] = []
    unique_matches = ambiguous_matches = unmatched_events = 0
    runtime_index_subset_events = runtime_index_rejected_events = 0
    admitted_index_observations: dict[int, list[dict]] = {}
    for page_number, events in enumerate(page_events, start=1):
        page_unique = page_ambiguous = page_unmatched = 0
        for event_number, event in sorted(events.items()):
            candidates: list[dict] = []
            for block in event.blocks:
                if (
                    not 1 <= block.stride <= _MAX_STREAM_ZERO_RECORD_BYTES
                    or block.range_count <= 0
                    or block.payload_bytes != block.range_count * block.stride
                ):
                    continue
                payload = _read_payload(
                    page_bundles[page_number - 1],
                    block.payload_file,
                    block.payload_bytes,
                    block.payload_sha256,
                )
                for record in records:
                    range_end = block.range_first + block.range_count
                    if range_end > record["vertex_count"]:
                        continue
                    start = block.range_first * block.stride
                    end = range_end * block.stride
                    if end > len(record["payload"]):
                        continue
                    if payload != record["payload"][start:end]:
                        continue
                    candidates.append(
                        {
                            "block": block.number,
                            "record_offset": record["record_offset"],
                            "source_vertex_count": record["vertex_count"],
                            "source_index_count": record["index_count"],
                            "source_index_sha256": record["index_sha256"],
                            "range_first": block.range_first,
                            "range_count": block.range_count,
                            "range_end": range_end,
                            "stream_zero_record_bytes": block.stride,
                            "full_vertex_range": (
                                block.range_first == 0
                                and block.range_count == record["vertex_count"]
                            ),
                            "matched_stream_slice_sha256": block.payload_sha256,
                            "exact_full_index_identity": (
                                event.index_sha256 == record["index_sha256"]
                                and event.index_count == record["index_count"]
                                and event.index_bytes == record["index_count"] * 2
                            ),
                        }
                    )
            candidates.sort(key=lambda item: (item["record_offset"], item["block"]))
            if len(candidates) == 1:
                status = "unique-exact-xpp-stream-zero-slice"
                mapping = candidates[0]
                mapped_records.add(mapping["record_offset"])
                if mapping["exact_full_index_identity"]:
                    full_index_records.add(mapping["record_offset"])
                unique_matches += 1
                page_unique += 1
                record = record_by_offset[mapping["record_offset"]]
                try:
                    runtime_payload = _read_payload(
                        page_bundles[page_number - 1],
                        event.index_payload_file,
                        event.index_bytes,
                        event.index_sha256,
                    )
                    retail_triangles = _triangles(
                        record["index_payload"], record["index_count"]
                    )
                    runtime_triangles = _triangles(runtime_payload, event.index_count)
                    runtime_vertices = tuple(
                        vertex for triangle in runtime_triangles for vertex in triangle
                    )
                    runtime_min_vertex = min(runtime_vertices)
                    runtime_max_vertex = max(runtime_vertices)
                    if (
                        runtime_min_vertex < mapping["range_first"]
                        or runtime_max_vertex >= mapping["range_end"]
                    ):
                        raise SourceCorrelationError(
                            "runtime indices escape the exact mapped vertex range"
                        )
                    retail_counts = Counter(retail_triangles)
                    runtime_counts = Counter(runtime_triangles)
                    excess = runtime_counts - retail_counts
                    if excess:
                        raise SourceCorrelationError(
                            "runtime index triangle multiset exceeds the retail record"
                        )
                    ordered_runtime = _ordered_multiset(
                        retail_triangles, runtime_counts
                    )
                    missing = _ordered_multiset(
                        retail_triangles, retail_counts - runtime_counts
                    )
                    coverage = {
                        "status": "retail-triangle-subset-proved",
                        "runtime_index_sha256": event.index_sha256,
                        "runtime_triangle_occurrences": len(runtime_triangles),
                        "runtime_min_vertex_index": runtime_min_vertex,
                        "runtime_max_vertex_index": runtime_max_vertex,
                        "runtime_indices_within_mapped_vertex_range": True,
                        "covered_retail_triangle_occurrences": len(ordered_runtime),
                        "unobserved_retail_triangle_occurrences": len(missing),
                        "covered_triangle_multiset_sha256": _triangle_sha256(
                            ordered_runtime
                        ),
                        "unobserved_triangle_multiset_sha256": _triangle_sha256(
                            missing
                        ),
                        "safe_for_retail_coverage_union": True,
                    }
                    admitted_index_observations.setdefault(
                        mapping["record_offset"], []
                    ).append(
                        {
                            "page": page_number,
                            "event": event_number,
                            "runtime_index_sha256": event.index_sha256,
                            "runtime_counts": runtime_counts,
                        }
                    )
                    runtime_index_subset_events += 1
                except SourceCorrelationError as exc:
                    coverage = {
                        "status": "not-admitted",
                        "runtime_index_sha256": event.index_sha256,
                        "safe_for_retail_coverage_union": False,
                        "reason": str(exc),
                    }
                    runtime_index_rejected_events += 1
                mapping["runtime_index_coverage"] = coverage
            elif candidates:
                status = "ambiguous-exact-xpp-stream-zero-slice"
                mapping = None
                ambiguous_matches += 1
                page_ambiguous += 1
            else:
                status = "no-exact-xpp-stream-zero-slice"
                mapping = None
                unmatched_events += 1
                page_unmatched += 1
            events_report.append(
                {
                    "page": page_number,
                    "event": event_number,
                    "status": status,
                    "candidate_count": len(candidates),
                    "mapping": mapping,
                    "ambiguous_candidates": candidates if len(candidates) > 1 else [],
                    "same_xpp_source_record_proved": mapping is not None,
                    "human_component_identity_proved": False,
                }
            )
        page_summaries.append(
            {
                "page": page_number,
                "events": len(events),
                "unique_exact_source_bindings": page_unique,
                "ambiguous_source_bindings": page_ambiguous,
                "unmatched_events": page_unmatched,
            }
        )

    record_index_coverage: list[dict] = []
    for record_offset, observations in sorted(admitted_index_observations.items()):
        record = record_by_offset[record_offset]
        retail_triangles = _triangles(record["index_payload"], record["index_count"])
        retail_counts = Counter(retail_triangles)
        union_counts: Counter = Counter()
        rows: list[dict] = []
        for observation in sorted(
            observations,
            key=lambda row: (row["page"], row["event"], row["runtime_index_sha256"]),
        ):
            counts = observation.pop("runtime_counts")
            new_counts = counts - union_counts
            overlap_counts = counts & union_counts
            union_counts |= counts
            rows.append(
                {
                    **observation,
                    "new_triangle_occurrences": sum(new_counts.values()),
                    "already_covered_triangle_occurrences": sum(
                        overlap_counts.values()
                    ),
                    "cumulative_union_triangle_occurrences": sum(union_counts.values()),
                }
            )
        covered = _ordered_multiset(retail_triangles, union_counts)
        missing = _ordered_multiset(retail_triangles, retail_counts - union_counts)
        record_index_coverage.append(
            {
                "record_offset": record_offset,
                "retail_triangle_occurrences": len(retail_triangles),
                "admitted_observation_count": len(rows),
                "distinct_runtime_index_payloads": len(
                    {row["runtime_index_sha256"] for row in rows}
                ),
                "observations": rows,
                "union": {
                    "covered_retail_triangle_occurrences": len(covered),
                    "unobserved_retail_triangle_occurrences": len(missing),
                    "full_retail_triangle_coverage_proved": not missing,
                    "covered_triangle_multiset_sha256": _triangle_sha256(covered),
                    "unobserved_triangle_multiset_sha256": _triangle_sha256(missing),
                },
            }
        )

    private_record_fields = {
        "payload",
        "index_payload",
        "stream_zero_end_limit",
    }
    public_records = [
        {
            key: value
            for key, value in record.items()
            if key not in private_record_fields
        }
        for record in records
    ]
    unmapped_records = [
        record
        for record in public_records
        if record["record_offset"] not in mapped_records
    ]
    source.update({"source": xpp_source_name, "records": public_records})
    return {
        "schema_version": 1,
        "kind": "if1-rsx-paged-xpp-source-census",
        "source": source,
        "texture_allowlist_sha256": allowlist_sha256,
        "pages": pages,
        "page_summaries": page_summaries,
        "events": events_report,
        "page_count": len(page_events),
        "captured_draws": sum(len(events) for events in page_events),
        "unique_exact_source_binding_events": unique_matches,
        "ambiguous_source_binding_events": ambiguous_matches,
        "unmatched_runtime_events": unmatched_events,
        "runtime_index_subset_events": runtime_index_subset_events,
        "runtime_index_rejected_events": runtime_index_rejected_events,
        "mapped_source_record_count": len(mapped_records),
        "source_record_count": len(records),
        "source_record_coverage": f"{len(mapped_records)}/{len(records)}",
        "exact_full_index_source_record_count": len(full_index_records),
        "mapped_source_record_offsets": sorted(mapped_records),
        "exact_full_index_source_record_offsets": sorted(full_index_records),
        "unmapped_source_records": unmapped_records,
        "record_index_coverage": record_index_coverage,
        "bounds": {
            "maximum_xpp_bytes": MAX_XPP_SOURCE_BYTES,
            "maximum_report_bytes": MAX_SOURCE_CORRELATION_REPORT_BYTES,
            "maximum_stream_zero_record_bytes": _MAX_STREAM_ZERO_RECORD_BYTES,
            "network": False,
            "input_xpp_mutated": False,
            "input_bundles_mutated": False,
            "raw_payloads_in_report": False,
            "overwrite": False,
        },
        "gates": {
            "exact_xpp_identity": True,
            "complete_xpp_character_contract_coverage": True,
            "complete_page_bundle_identity": True,
            "exact_cumulative_exclusion_chain": True,
            "unique_xpp_stream_zero_slice_bindings": unique_matches > 0,
            "full_xpp_index_identity": bool(full_index_records),
            "runtime_index_retail_subset_validation": (
                runtime_index_subset_events > 0 and runtime_index_rejected_events == 0
            ),
            "human_component_identity": False,
            "full_character": False,
            "position_semantics": False,
            "uvs": False,
            "skin_weights": False,
            "skeleton": False,
            "retail_material": False,
            "mod_ready": False,
        },
        "verdict": "paged-runtime-draws-bound-to-exact-xpp-stream-zero-slices",
        "next_gate": (
            "carry admitted record-index unions forward as topology evidence; require "
            "separate UV, shader, and named-texture lineage before material promotion, "
            "and capture a genuinely different state for still-unobserved triangles"
        ),
    }
