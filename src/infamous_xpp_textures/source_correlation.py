"""Bind paged RSX draw streams to exact XPP character-record byte slices."""

from __future__ import annotations

import hashlib
from pathlib import Path

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
        records.append(
            {
                "record_offset": contract.record_offset,
                "vertex_count": contract.vertex_count,
                "index_count": contract.index_count,
                "index_sha256": contract.index_sha256,
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

    private_record_fields = {"payload", "stream_zero_end_limit"}
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
        "mapped_source_record_count": len(mapped_records),
        "source_record_count": len(records),
        "source_record_coverage": f"{len(mapped_records)}/{len(records)}",
        "exact_full_index_source_record_count": len(full_index_records),
        "mapped_source_record_offsets": sorted(mapped_records),
        "exact_full_index_source_record_offsets": sorted(full_index_records),
        "unmapped_source_records": unmapped_records,
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
            "assemble only uniquely source-bound draws, carry prior independently "
            "proved records forward, and classify alternate runtime layouts for the "
            "remaining source records without guessing component names"
        ),
    }
