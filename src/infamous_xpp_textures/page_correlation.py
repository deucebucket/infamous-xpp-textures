"""Payload-free correlation of exact RSX capture pages."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .runtime_topology_export import (
    RuntimeTopologyExportError,
    _load_bundle,
    _parse_capture_key_exclusion,
)


class PageCorrelationError(ValueError):
    """Raised when a paged correlation input or claim is not exact."""


_MAX_PAGES = 17
_MAX_EVENTS_PER_PAGE = 16
_MAX_PAIR_COMPARISONS = 34_816
MAX_PAGE_CORRELATION_REPORT_BYTES = 256 * 1024
_STRONG_TIERS = (
    "exact_geometry_bytes",
    "exact_vertex_stream_bytes",
    "stable_layout_partial_stream",
)
_TIER_RANK = {name: rank for rank, name in enumerate(_STRONG_TIERS, start=1)}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _block_layout(event) -> tuple:
    return tuple(
        (
            block.descriptor_sha256,
            block.stride,
            block.range_first,
            block.range_count,
            tuple(
                (
                    item["attribute"],
                    item["type"],
                    item["components"],
                    item["array_stride"],
                    item["frequency"],
                    item["modulo"],
                )
                for item in block.attributes
            ),
        )
        for block in event.blocks
    )


def _vertex_stream_identity(event) -> tuple:
    return tuple(
        (
            block.payload_sha256,
            block.payload_bytes,
            block.descriptor_sha256,
            block.stride,
            block.range_first,
            block.range_count,
        )
        for block in event.blocks
    )


def _target_texture_vertex_program_identity(event) -> tuple:
    return (
        event.target_texture_slots,
        event.target_texture_sha256s,
        event.vertex_program_sha256,
    )


def _exact_ordered_blocks(left, right) -> tuple[int, ...]:
    if len(left.blocks) != len(right.blocks):
        return ()
    return tuple(
        number
        for number, (left_block, right_block) in enumerate(
            zip(left.blocks, right.blocks), start=1
        )
        if (
            left_block.payload_sha256 == right_block.payload_sha256
            and left_block.payload_bytes == right_block.payload_bytes
            and left_block.descriptor_sha256 == right_block.descriptor_sha256
        )
    )


def _relationship(left, right) -> tuple[str | None, tuple[int, ...]]:
    exact_blocks = _exact_ordered_blocks(left, right)
    same_layout = _block_layout(left) == _block_layout(right)
    same_streams = same_layout and _vertex_stream_identity(
        left
    ) == _vertex_stream_identity(right)
    same_target_program = _target_texture_vertex_program_identity(
        left
    ) == _target_texture_vertex_program_identity(right)
    if (
        same_streams
        and left.index_sha256 == right.index_sha256
        and left.index_bytes == right.index_bytes
        and left.index_count == right.index_count
    ):
        return "exact_geometry_bytes", exact_blocks
    if same_streams:
        return "exact_vertex_stream_bytes", exact_blocks
    if same_layout and same_target_program and exact_blocks:
        return "stable_layout_partial_stream", exact_blocks
    if same_target_program:
        return "weak_target_texture_vertex_program", exact_blocks
    return None, exact_blocks


class _DisjointSet:
    def __init__(self, nodes: tuple[tuple[int, int], ...]):
        self.parent = {node: node for node in nodes}

    def find(self, node: tuple[int, int]) -> tuple[int, int]:
        parent = self.parent[node]
        if parent != node:
            self.parent[node] = self.find(parent)
        return self.parent[node]

    def union(self, left: tuple[int, int], right: tuple[int, int]) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root


def _load_page_chain(
    page_bundles: tuple[Path, ...],
    texture_allowlist: Path,
    page_capture_key_exclusions: tuple[Path | None, ...],
) -> tuple[list[dict], list[dict], str]:
    if not 2 <= len(page_bundles) <= _MAX_PAGES:
        raise PageCorrelationError("page census requires 2 through 17 bundles")
    if len(page_bundles) != len(page_capture_key_exclusions):
        raise PageCorrelationError("page bundle and exclusion counts must match")
    resolved_bundles = tuple(bundle.resolve() for bundle in page_bundles)
    if len(set(resolved_bundles)) != len(resolved_bundles):
        raise PageCorrelationError("page bundles must be distinct")

    cumulative_keys: set[str] = set()
    allowlist_sha256: str | None = None
    page_records: list[dict] = []
    page_events: list[dict] = []
    for page_number, (bundle, exclusion) in enumerate(
        zip(page_bundles, page_capture_key_exclusions), start=1
    ):
        try:
            completion, events, page_allowlist_sha256 = _load_bundle(
                bundle, texture_allowlist, exclusion
            )
        except RuntimeTopologyExportError as exc:
            raise PageCorrelationError(str(exc)) from exc
        expected_format = (
            "if1-texture-bound-topology-v3"
            if page_number == 1
            else "if1-texture-bound-topology-v4"
        )
        if completion["format"] != expected_format:
            raise PageCorrelationError(
                "page census requires one base v3 bundle followed only by v4 bundles"
            )
        if page_number == 1:
            if exclusion is not None:
                raise PageCorrelationError("base v3 page must not have an exclusion")
        else:
            if exclusion is None:
                raise PageCorrelationError("each v4 page requires its exact exclusion")
            try:
                excluded_keys, exclusion_sha256 = _parse_capture_key_exclusion(
                    exclusion
                )
            except RuntimeTopologyExportError as exc:
                raise PageCorrelationError(str(exc)) from exc
            if excluded_keys != cumulative_keys:
                raise PageCorrelationError(
                    "v4 page exclusion is not the exact cumulative prior-page key set"
                )
            if exclusion_sha256 != completion["exclusion_manifest_sha256"]:
                raise PageCorrelationError(
                    "v4 page exclusion identity does not match completion"
                )
        if page_number < len(page_bundles) and completion["capture_limit_reached"] != 1:
            raise PageCorrelationError(
                "every non-final page must prove that its capture limit was reached"
            )
        if allowlist_sha256 is None:
            allowlist_sha256 = page_allowlist_sha256
        elif page_allowlist_sha256 != allowlist_sha256:
            raise PageCorrelationError("page texture allowlist identities do not match")
        if not 1 <= len(events) <= _MAX_EVENTS_PER_PAGE:
            raise PageCorrelationError(
                "page event count is outside the bounded contract"
            )
        captured_keys = {
            event.capture_key
            for event in events.values()
            if event.capture_key is not None
        }
        if len(captured_keys) != len(events) or captured_keys & cumulative_keys:
            raise PageCorrelationError(
                "page capture keys are missing, duplicated, or overlap prior pages"
            )
        page_records.append(
            {
                "page": page_number,
                "bundle_format": completion["format"],
                "capture_complete_sha256": _sha256(
                    (bundle / "capture.complete").read_bytes()
                ),
                "captured_draws": completion["captured_draws"],
                "capture_limit_reached": bool(completion["capture_limit_reached"]),
                "excluded_capture_keys": len(cumulative_keys),
                "exclusion_manifest_sha256": completion.get(
                    "exclusion_manifest_sha256"
                ),
            }
        )
        page_events.append(events)
        cumulative_keys.update(captured_keys)
    if allowlist_sha256 is None:
        raise PageCorrelationError("page texture allowlist identity is missing")
    return page_records, page_events, allowlist_sha256


def correlate_paged_draw_families(
    page_bundles: tuple[Path, ...],
    texture_allowlist: Path,
    page_capture_key_exclusions: tuple[Path | None, ...],
) -> dict:
    """Validate an exact page chain and classify cross-page draw reuse."""

    page_records, page_events, allowlist_sha256 = _load_page_chain(
        page_bundles, texture_allowlist, page_capture_key_exclusions
    )
    nodes = tuple(
        (page_number, event_number)
        for page_number, events in enumerate(page_events, start=1)
        for event_number in sorted(events)
    )
    pair_comparisons = sum(
        len(page_events[left]) * len(page_events[right])
        for left in range(len(page_events))
        for right in range(left + 1, len(page_events))
    )
    if pair_comparisons > _MAX_PAIR_COMPARISONS:
        raise PageCorrelationError("page-pair comparison count exceeds its bound")

    disjoint = _DisjointSet(nodes)
    strong_edges: list[tuple] = []
    strong_prior_matches = {node: 0 for node in nodes}
    weak_prior_matches = {node: 0 for node in nodes}
    best_prior_tier: dict[tuple[int, int], str | None] = {node: None for node in nodes}
    weak_surface_pairs = 0
    unrelated_pairs = 0
    for left_page in range(len(page_events)):
        for right_page in range(left_page + 1, len(page_events)):
            for left_number, left_event in sorted(page_events[left_page].items()):
                for right_number, right_event in sorted(
                    page_events[right_page].items()
                ):
                    left_node = (left_page + 1, left_number)
                    right_node = (right_page + 1, right_number)
                    tier, exact_blocks = _relationship(left_event, right_event)
                    if tier in _TIER_RANK:
                        disjoint.union(left_node, right_node)
                        strong_edges.append(
                            (left_node, right_node, tier, len(exact_blocks))
                        )
                        strong_prior_matches[right_node] += 1
                        current = best_prior_tier[right_node]
                        if current is None or _TIER_RANK[tier] < _TIER_RANK[current]:
                            best_prior_tier[right_node] = tier
                    elif tier == "weak_target_texture_vertex_program":
                        weak_prior_matches[right_node] += 1
                        weak_surface_pairs += 1
                    else:
                        unrelated_pairs += 1

    grouped: dict[tuple[int, int], list[tuple[int, int]]] = {}
    strong_nodes = {node for edge in strong_edges for node in edge[:2]}
    for node in sorted(strong_nodes):
        grouped.setdefault(disjoint.find(node), []).append(node)
    ordered_groups = sorted(grouped.values(), key=lambda members: tuple(members))
    family_by_node: dict[tuple[int, int], int] = {}
    families: list[dict] = []
    for family_number, members in enumerate(ordered_groups, start=1):
        for node in members:
            family_by_node[node] = family_number
        roots = set(members)
        edges = [edge for edge in strong_edges if edge[0] in roots and edge[1] in roots]
        evidence_counts = {
            tier: sum(edge[2] == tier for edge in edges) for tier in _STRONG_TIERS
        }
        per_page: dict[int, int] = {}
        for page, _event in members:
            per_page[page] = per_page.get(page, 0) + 1
        families.append(
            {
                "family": family_number,
                "members": [{"page": page, "event": event} for page, event in members],
                "pages": sorted(per_page),
                "cross_page_pairs": len(edges),
                "evidence_pairs": evidence_counts,
                "exact_blocks_min": min(edge[3] for edge in edges),
                "exact_blocks_max": max(edge[3] for edge in edges),
                "one_event_per_page": all(count == 1 for count in per_page.values()),
                "ambiguous_within_page": any(count > 1 for count in per_page.values()),
                "component_ownership_proved": False,
            }
        )

    events_report: list[dict] = []
    page_summaries: list[dict] = []
    for page_number, events in enumerate(page_events, start=1):
        classifications = {
            "baseline_event": 0,
            "strong_persistent_family_candidate": 0,
            "weak_target_texture_vertex_program_only": 0,
            "novel_observed_target_texture_vertex_program_signature": 0,
        }
        for event_number in sorted(events):
            node = (page_number, event_number)
            if page_number == 1:
                classification = "baseline_event"
            elif strong_prior_matches[node]:
                classification = "strong_persistent_family_candidate"
            elif weak_prior_matches[node]:
                classification = "weak_target_texture_vertex_program_only"
            else:
                classification = (
                    "novel_observed_target_texture_vertex_program_signature"
                )
            classifications[classification] += 1
            events_report.append(
                {
                    "page": page_number,
                    "event": event_number,
                    "classification": classification,
                    "strong_family": family_by_node.get(node),
                    "strong_prior_matches": strong_prior_matches[node],
                    "best_strong_evidence": best_prior_tier[node],
                    "weak_target_texture_vertex_program_prior_matches": (
                        weak_prior_matches[node]
                    ),
                    "same_source_component_proved": False,
                    "new_geometry_proved": False,
                }
            )
        page_summaries.append(
            {
                "page": page_number,
                "events": len(events),
                **classifications,
            }
        )

    exact_geometry_pairs = sum(
        edge[2] == "exact_geometry_bytes" for edge in strong_edges
    )
    exact_vertex_stream_pairs = sum(
        edge[2] == "exact_vertex_stream_bytes" for edge in strong_edges
    )
    stable_partial_pairs = sum(
        edge[2] == "stable_layout_partial_stream" for edge in strong_edges
    )
    return {
        "schema_version": 1,
        "kind": "if1-rsx-paged-draw-family-census",
        "texture_allowlist_sha256": allowlist_sha256,
        "pages": page_records,
        "page_summaries": page_summaries,
        "events": events_report,
        "strong_families": families,
        "page_count": len(page_events),
        "captured_draws": len(nodes),
        "pair_comparisons": pair_comparisons,
        "strong_family_count": len(families),
        "strong_pair_count": len(strong_edges),
        "exact_geometry_pairs": exact_geometry_pairs,
        "exact_vertex_stream_pairs": exact_vertex_stream_pairs,
        "stable_layout_partial_stream_pairs": stable_partial_pairs,
        "weak_target_texture_vertex_program_pairs": weak_surface_pairs,
        "unrelated_pairs": unrelated_pairs,
        "bounds": {
            "maximum_pages": _MAX_PAGES,
            "maximum_events_per_page": _MAX_EVENTS_PER_PAGE,
            "maximum_pair_comparisons": _MAX_PAIR_COMPARISONS,
            "maximum_report_bytes": MAX_PAGE_CORRELATION_REPORT_BYTES,
            "network": False,
            "input_bundles_mutated": False,
            "raw_payloads_in_report": False,
            "overwrite": False,
        },
        "gates": {
            "complete_page_bundle_identity": True,
            "exact_cumulative_exclusion_chain": True,
            "capture_key_overlap": False,
            "payload_free": True,
            "persistent_family_candidates": bool(families),
            "same_source_component": False,
            "new_geometry": False,
            "component_ownership": False,
            "full_character": False,
            "uvs": False,
            "skin_weights": False,
            "skeleton": False,
            "retail_material": False,
            "mod_ready": False,
        },
        "verdict": "paged-draw-families-classified-without-component-ownership",
        "next_gate": (
            "use the strong families to avoid counting changed capture keys as new "
            "components, then capture a scene or angle that exposes missing geometry"
        ),
    }
