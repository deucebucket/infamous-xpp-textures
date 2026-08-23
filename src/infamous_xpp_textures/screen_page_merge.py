"""Strict multi-page RSX screenshot-space replay assembly."""

from __future__ import annotations

import colorsys
import hashlib
from pathlib import Path
import struct

from .character_export import _cross_length_squared, _pack_glb, _write_atomic
from .mesh import GlbBuilder
from .position_replay import (
    PositionReplayError,
    _event_geometry,
    _event_payloads,
    extract_output_affine,
)
from .runtime_topology_export import (
    RuntimeTopologyExportError,
    _load_bundle,
    _parse_capture_key_exclusion,
)
from .screen_replay import ScreenReplayError, project_position_to_ndc


_MAX_PAGES = 17
_MAX_EVENTS_PER_PAGE = 16
_MAX_MERGED_VERTICES = 1_048_576
_MAX_MERGED_INDICES = 3_145_728


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _diagnostic_color(index: int) -> tuple[float, float, float, float]:
    """Return one deterministic, visually distinct color for a global draw index."""

    hue = (index * 0.6180339887498949) % 1.0
    saturation = 0.68 + 0.06 * (index % 3)
    value = 0.90 - 0.04 * ((index // 3) % 2)
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return (red, green, blue, 1.0)


def _validate_events(selected_events: tuple[int, ...]) -> None:
    if (
        not selected_events
        or len(selected_events) > _MAX_EVENTS_PER_PAGE
        or len(set(selected_events)) != len(selected_events)
        or any(
            not isinstance(number, int) or isinstance(number, bool) or number <= 0
            for number in selected_events
        )
    ):
        raise ScreenReplayError(
            "each page selection must contain 1 through 16 unique positive events"
        )


def _replay_page(
    bundle: Path, events: dict, selected_events: tuple[int, ...]
) -> list[dict]:
    if any(number not in events for number in selected_events):
        raise ScreenReplayError("selected page event is absent")
    transformed: list[dict] = []
    for number in selected_events:
        event = events[number]
        try:
            program, constants = _event_payloads(bundle, event)
            matrix = extract_output_affine(program, constants)
            _block, indices, positions = _event_geometry(bundle, event)
        except PositionReplayError as exc:
            raise ScreenReplayError(str(exc)) from exc
        ndc_positions = [project_position_to_ndc(matrix, value) for value in positions]
        nondegenerate = sum(
            _cross_length_squared(
                ndc_positions[indices[offset]],
                ndc_positions[indices[offset + 1]],
                ndc_positions[indices[offset + 2]],
            )
            > 1e-12
            for offset in range(0, len(indices), 3)
        )
        if not nondegenerate:
            raise ScreenReplayError(
                "screen-replayed page event has only degenerate triangles"
            )
        minimum = [min(value[axis] for value in ndc_positions) for axis in range(3)]
        maximum = [max(value[axis] for value in ndc_positions) for axis in range(3)]
        transformed.append(
            {
                "event": number,
                "capture_key": event.capture_key,
                "indices": indices,
                "positions": ndc_positions,
                "vertices": len(ndc_positions),
                "triangles": len(indices) // 3,
                "nondegenerate_triangles": nondegenerate,
                "ndc_bounds_min": minimum,
                "ndc_bounds_max": maximum,
                "ndc_bounds_center": [
                    (minimum[axis] + maximum[axis]) / 2.0 for axis in range(3)
                ],
                "output_matrix_sha256": _sha256(
                    struct.pack("<16d", *(item for row in matrix for item in row))
                ),
            }
        )
    return transformed


def _selected_declared_extent(
    events: dict, selected_events: tuple[int, ...]
) -> tuple[int, int]:
    vertices = 0
    indices = 0
    for number in selected_events:
        if number not in events:
            raise ScreenReplayError("selected page event is absent")
        event = events[number]
        candidates = [
            block
            for block in event.blocks
            if any(attribute["attribute"] == 0 for attribute in block.attributes)
        ]
        if len(candidates) != 1:
            raise ScreenReplayError(
                "selected page event lacks one exact attribute-zero block"
            )
        vertices += candidates[0].range_count
        indices += event.index_count
    return vertices, indices


def export_screen_replay_pages_glb(
    page_bundles: tuple[Path, ...],
    texture_allowlist: Path,
    page_events: tuple[tuple[int, ...], ...],
    page_capture_key_exclusions: tuple[Path | None, ...],
    output: Path,
) -> dict:
    """Validate and combine one base v3 page plus bounded chained v4 pages."""

    if not 2 <= len(page_bundles) <= _MAX_PAGES:
        raise ScreenReplayError("page merge requires 2 through 17 bundles")
    if not (len(page_bundles) == len(page_events) == len(page_capture_key_exclusions)):
        raise ScreenReplayError("page bundle/event/exclusion counts must match")
    for selection in page_events:
        _validate_events(selection)
    resolved_bundles = tuple(bundle.resolve() for bundle in page_bundles)
    if len(set(resolved_bundles)) != len(resolved_bundles):
        raise ScreenReplayError("page bundles must be distinct")
    output_resolved = output.resolve()
    if any(
        output_resolved == bundle or bundle in output_resolved.parents
        for bundle in resolved_bundles
    ):
        raise ScreenReplayError(
            "page-merge output must remain outside every immutable input bundle"
        )
    if output.is_symlink() or output.exists():
        raise ScreenReplayError("page-merge output exists; refusing to overwrite it")

    cumulative_keys: set[str] = set()
    allowlist_sha256: str | None = None
    page_reports: list[dict] = []
    transformed_pages: list[list[dict]] = []
    declared_vertices = 0
    declared_indices = 0
    for page_index, (bundle, selection, exclusion) in enumerate(
        zip(page_bundles, page_events, page_capture_key_exclusions), start=1
    ):
        try:
            completion, events, page_allowlist_sha256 = _load_bundle(
                bundle, texture_allowlist, exclusion
            )
        except RuntimeTopologyExportError as exc:
            raise ScreenReplayError(str(exc)) from exc
        expected_format = (
            "if1-texture-bound-topology-v3"
            if page_index == 1
            else "if1-texture-bound-topology-v4"
        )
        if completion["format"] != expected_format:
            raise ScreenReplayError(
                "page merge requires one base v3 bundle followed only by v4 bundles"
            )
        if page_index == 1:
            if exclusion is not None:
                raise ScreenReplayError("base v3 page must not have an exclusion")
        else:
            if exclusion is None:
                raise ScreenReplayError("each v4 page requires its exact exclusion")
            try:
                excluded_keys, exclusion_sha256 = _parse_capture_key_exclusion(
                    exclusion
                )
            except RuntimeTopologyExportError as exc:
                raise ScreenReplayError(str(exc)) from exc
            if excluded_keys != cumulative_keys:
                raise ScreenReplayError(
                    "v4 page exclusion is not the exact cumulative prior-page key set"
                )
            if exclusion_sha256 != completion["exclusion_manifest_sha256"]:
                raise ScreenReplayError(
                    "v4 page exclusion identity does not match completion"
                )
        if page_index < len(page_bundles) and completion["capture_limit_reached"] != 1:
            raise ScreenReplayError(
                "every non-final page must prove that its capture limit was reached"
            )
        if allowlist_sha256 is None:
            allowlist_sha256 = page_allowlist_sha256
        elif page_allowlist_sha256 != allowlist_sha256:
            raise ScreenReplayError("page texture allowlist identities do not match")
        captured_keys = {
            event.capture_key
            for event in events.values()
            if event.capture_key is not None
        }
        if len(captured_keys) != len(events) or captured_keys & cumulative_keys:
            raise ScreenReplayError(
                "page capture keys are missing, duplicated, or overlap prior pages"
            )
        page_vertices, page_indices = _selected_declared_extent(events, selection)
        declared_vertices += page_vertices
        declared_indices += page_indices
        if (
            declared_vertices > _MAX_MERGED_VERTICES
            or declared_indices > _MAX_MERGED_INDICES
        ):
            raise ScreenReplayError(
                "selected page geometry exceeds the bounded merge extent"
            )
        page_transformed = _replay_page(bundle, events, selection)
        transformed_pages.append(page_transformed)
        page_report = {
            "page": page_index,
            "bundle_format": completion["format"],
            "capture_complete_sha256": _sha256(
                (bundle / "capture.complete").read_bytes()
            ),
            "captured_draws": completion["captured_draws"],
            "capture_limit_reached": bool(completion["capture_limit_reached"]),
            "selected_events": list(selection),
            "selected_capture_keys": [item["capture_key"] for item in page_transformed],
            "excluded_capture_keys": len(cumulative_keys),
            "exclusion_manifest_sha256": (completion.get("exclusion_manifest_sha256")),
        }
        page_reports.append(page_report)
        cumulative_keys.update(captured_keys)

    flattened = [
        (page_number, item)
        for page_number, page in enumerate(transformed_pages, start=1)
        for item in page
    ]
    all_positions = [
        position for _page_number, item in flattened for position in item["positions"]
    ]
    combined_min = [min(value[axis] for value in all_positions) for axis in range(3)]
    combined_max = [max(value[axis] for value in all_positions) for axis in range(3)]
    combined_center = [
        (combined_min[axis] + combined_max[axis]) / 2.0 for axis in range(3)
    ]

    builder = GlbBuilder()
    meshes: list[dict] = []
    nodes: list[dict] = []
    materials: list[dict] = []
    event_reports: list[dict] = []
    for global_index, (page_number, item) in enumerate(flattened):
        color = _diagnostic_color(global_index)
        positions = item["positions"]
        minimum = [min(value[axis] for value in positions) for axis in range(3)]
        maximum = [max(value[axis] for value in positions) for axis in range(3)]
        position_accessor = builder.add_accessor(
            b"".join(struct.pack("<3f", *value) for value in positions),
            5126,
            len(positions),
            "VEC3",
            34962,
            minimum,
            maximum,
        )
        index_accessor = builder.add_accessor(
            struct.pack(f"<{len(item['indices'])}H", *item["indices"]),
            5123,
            len(item["indices"]),
            "SCALAR",
            34963,
        )
        label = f"Page {page_number:02d} event {item['event']:02d}"
        meshes.append(
            {
                "name": f"{label} (ownership unproved)",
                "primitives": [
                    {
                        "attributes": {"POSITION": position_accessor},
                        "indices": index_accessor,
                        "material": global_index,
                        "mode": 4,
                    }
                ],
            }
        )
        nodes.append(
            {
                "mesh": global_index,
                "name": label,
                "extras": {
                    "page": page_number,
                    "event": item["event"],
                    "captureKey": item["capture_key"],
                    "componentOwnershipProved": False,
                    "screenshotAligned": True,
                },
            }
        )
        materials.append(
            {
                "name": f"{label} diagnostic neutral",
                "doubleSided": True,
                "extensions": {"KHR_materials_unlit": {}},
                "pbrMetallicRoughness": {
                    "baseColorFactor": list(color),
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            }
        )
        event_reports.append(
            {
                "page": page_number,
                "event": item["event"],
                "capture_key": item["capture_key"],
                "vertices": item["vertices"],
                "triangles": item["triangles"],
                "nondegenerate_triangles": item["nondegenerate_triangles"],
                "ndc_bounds_min": item["ndc_bounds_min"],
                "ndc_bounds_max": item["ndc_bounds_max"],
                "ndc_bounds_center": item["ndc_bounds_center"],
                "output_matrix_sha256": item["output_matrix_sha256"],
                "diagnostic_rgba": list(color),
            }
        )

    evidence = {
        "diagnosticOnly": True,
        "pageCount": len(page_bundles),
        "selectedDraws": len(flattened),
        "exactCaptureKeyChainProved": True,
        "overlappingCaptureKeys": 0,
        "screenshotAligned": True,
        "staticShaderReferenceProved": True,
        "runtimeTextureSamplingProved": False,
        "componentOwnershipProved": False,
        "worldSpaceProved": False,
        "fullCharacterProved": False,
        "skinWeightsProved": False,
        "skeletonProved": False,
        "retailMaterialProved": False,
        "modReady": False,
    }
    document = {
        "asset": {
            "version": "2.0",
            "generator": "xpp-tool 2.17.0 bounded RSX paged screen replay",
            "extras": {"infamousPagedScreenReplay": evidence},
        },
        "extensionsUsed": ["KHR_materials_unlit"],
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "bufferViews": builder.views,
        "accessors": builder.accessors,
    }
    glb = _pack_glb(document, builder.binary)
    _write_atomic(output, glb)
    return {
        "schema_version": 1,
        "kind": "if1-rsx-paged-screen-position-replay-export",
        "texture_allowlist_sha256": allowlist_sha256,
        "page_count": len(page_bundles),
        "pages": page_reports,
        "events": event_reports,
        "selected_draws": len(flattened),
        "captured_unique_keys": len(cumulative_keys),
        "combined_ndc_bounds_min": combined_min,
        "combined_ndc_bounds_max": combined_max,
        "combined_ndc_bounds_center": combined_center,
        "vertices": sum(item["vertices"] for item in event_reports),
        "triangles": sum(item["triangles"] for item in event_reports),
        "output_size": len(glb),
        "output_sha256": _sha256(glb),
        "bounds": {
            "maximum_pages": _MAX_PAGES,
            "maximum_events_per_page": _MAX_EVENTS_PER_PAGE,
            "maximum_vertices": _MAX_MERGED_VERTICES,
            "maximum_indices": _MAX_MERGED_INDICES,
            "network": False,
            "input_bundles_mutated": False,
            "overwrite": False,
        },
        "gates": {
            "complete_page_bundle_identity": True,
            "exact_cumulative_exclusion_chain": True,
            "capture_key_overlap": False,
            "screenshot_aligned": True,
            "static_shader_reference": True,
            "component_ownership": False,
            "world_space": False,
            "full_character": False,
            "skin_weights": False,
            "skeleton": False,
            "rigging": False,
            "retail_material": False,
            "mod_ready": False,
        },
        "verdict": "paged-draws-exported-in-one-screenshot-aligned-ndc-frame",
        "next_gate": (
            "render the merged GLB against the exact foreground frame and classify "
            "new geometry only from visible agreement"
        ),
    }
