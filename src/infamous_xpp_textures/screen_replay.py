"""Bounded RSX clip/NDC replay for screenshot-aligned draw classification."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import struct

from .character_export import _cross_length_squared, _pack_glb, _write_atomic
from .mesh import GlbBuilder
from .position_replay import (
    PositionReplayError,
    _event_geometry,
    _event_payloads,
    _matrix_vector,
    extract_output_affine,
)
from .runtime_topology_export import RuntimeTopologyExportError, _load_bundle


class ScreenReplayError(ValueError):
    """Raised when exact captured clip coordinates cannot be replayed safely."""


_SCREEN_PALETTE = (
    (0.90, 0.20, 0.16, 1.0),
    (0.10, 0.55, 0.92, 1.0),
    (0.15, 0.75, 0.32, 1.0),
    (0.95, 0.62, 0.10, 1.0),
    (0.55, 0.28, 0.88, 1.0),
    (0.05, 0.72, 0.75, 1.0),
    (0.92, 0.30, 0.62, 1.0),
    (0.62, 0.72, 0.12, 1.0),
    (0.32, 0.38, 0.92, 1.0),
    (0.92, 0.42, 0.12, 1.0),
    (0.18, 0.62, 0.48, 1.0),
    (0.72, 0.22, 0.30, 1.0),
    (0.38, 0.70, 0.92, 1.0),
    (0.78, 0.50, 0.82, 1.0),
    (0.88, 0.78, 0.18, 1.0),
    (0.55, 0.68, 0.68, 1.0),
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def project_position_to_ndc(
    matrix: list[list[float]], position: tuple[float, float, float]
) -> tuple[float, float, float]:
    """Replay one attribute-zero value through output zero and divide by W."""

    clip = _matrix_vector(matrix, (*position, 1.0))
    if not all(math.isfinite(value) for value in clip):
        raise ScreenReplayError("clip-space position is non-finite")
    if abs(clip[3]) < 1e-12:
        raise ScreenReplayError("clip-space position has zero W")
    ndc = tuple(clip[axis] / clip[3] for axis in range(3))
    if not all(math.isfinite(value) for value in ndc):
        raise ScreenReplayError("NDC position is non-finite")
    return ndc


def export_screen_replay_glb(
    bundle: Path,
    texture_allowlist: Path,
    selected_events: tuple[int, ...],
    output: Path,
) -> dict:
    """Export selected draws in their exact per-draw normalized-device frame."""

    if output.is_symlink() or output.exists():
        raise ScreenReplayError("screen replay output already exists")
    if not selected_events or len(set(selected_events)) != len(selected_events):
        raise ScreenReplayError("selected events must be unique and non-empty")
    if any(
        not isinstance(number, int) or isinstance(number, bool) or number <= 0
        for number in selected_events
    ):
        raise ScreenReplayError("selected events must be positive integers")
    try:
        completion, events, allowlist_sha256 = _load_bundle(bundle, texture_allowlist)
    except RuntimeTopologyExportError as exc:
        raise ScreenReplayError(str(exc)) from exc
    if completion["format"] not in (
        "if1-texture-bound-topology-v2",
        "if1-texture-bound-topology-v3",
    ):
        raise ScreenReplayError("screen replay requires a complete v2 or v3 bundle")
    if any(number not in events for number in selected_events):
        raise ScreenReplayError("selected event is absent")

    transformed = []
    all_positions = []
    for number in selected_events:
        event = events[number]
        try:
            program, constants = _event_payloads(bundle, event)
            matrix = extract_output_affine(program, constants)
            _block, indices, positions = _event_geometry(bundle, event)
        except PositionReplayError as exc:
            raise ScreenReplayError(str(exc)) from exc
        ndc_positions = [project_position_to_ndc(matrix, position) for position in positions]
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
            raise ScreenReplayError("screen-replayed event has only degenerate triangles")
        minimum = [min(value[axis] for value in ndc_positions) for axis in range(3)]
        maximum = [max(value[axis] for value in ndc_positions) for axis in range(3)]
        center = [(minimum[axis] + maximum[axis]) / 2.0 for axis in range(3)]
        transformed.append(
            {
                "event": number,
                "indices": indices,
                "positions": ndc_positions,
                "vertices": len(ndc_positions),
                "triangles": len(indices) // 3,
                "nondegenerate_triangles": nondegenerate,
                "ndc_bounds_min": minimum,
                "ndc_bounds_max": maximum,
                "ndc_bounds_center": center,
                "output_matrix_sha256": _sha256(
                    struct.pack("<16d", *(item for row in matrix for item in row))
                ),
                "diagnostic_rgba": list(
                    _SCREEN_PALETTE[(number - 1) % len(_SCREEN_PALETTE)]
                ),
            }
        )
        all_positions.extend(ndc_positions)

    combined_min = [min(value[axis] for value in all_positions) for axis in range(3)]
    combined_max = [max(value[axis] for value in all_positions) for axis in range(3)]
    combined_center = [
        (combined_min[axis] + combined_max[axis]) / 2.0 for axis in range(3)
    ]
    builder = GlbBuilder()
    meshes = []
    nodes = []
    materials = []
    for index, item in enumerate(transformed):
        # glTF is Y-up. Preserve NDC X/Y directly so a normal front view shows
        # the captured screen plane; retain NDC Z as depth without recentering.
        positions = [(value[0], value[1], value[2]) for value in item["positions"]]
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
        meshes.append(
            {
                "name": f"Runtime event {item['event']:02d} (ownership unproved)",
                "primitives": [
                    {
                        "attributes": {"POSITION": position_accessor},
                        "indices": index_accessor,
                        "material": index,
                        "mode": 4,
                    }
                ],
            }
        )
        nodes.append(
            {
                "mesh": index,
                "name": f"Runtime event {item['event']:02d}",
                "extras": {
                    "event": item["event"],
                    "componentOwnershipProved": False,
                    "screenshotAligned": True,
                },
            }
        )
        materials.append(
            {
                "name": f"Event {item['event']:02d} diagnostic neutral",
                "doubleSided": True,
                "extensions": {"KHR_materials_unlit": {}},
                "pbrMetallicRoughness": {
                    "baseColorFactor": list(
                        _SCREEN_PALETTE[(item["event"] - 1) % len(_SCREEN_PALETTE)]
                    ),
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            }
        )

    static_reference = completion["format"] == "if1-texture-bound-topology-v3"
    evidence = {
        "diagnosticOnly": True,
        "selectedEvents": list(selected_events),
        "screenshotAligned": True,
        "staticShaderReferenceProved": static_reference,
        "runtimeTextureSamplingProved": False,
        "attributeZeroPositionSemanticsProved": False,
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
            "generator": "xpp-tool 2.16.0 bounded RSX screen replay",
            "extras": {"infamousScreenReplay": evidence},
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
    event_reports = [
        {
            key: value
            for key, value in item.items()
            if key
            in {
                "event",
                "vertices",
                "triangles",
                "nondegenerate_triangles",
                "ndc_bounds_min",
                "ndc_bounds_max",
                "ndc_bounds_center",
                "output_matrix_sha256",
                "diagnostic_rgba",
            }
        }
        for item in transformed
    ]
    return {
        "schema_version": 1,
        "kind": "if1-rsx-screen-position-replay-export",
        "bundle_format": completion["format"],
        "texture_allowlist_sha256": allowlist_sha256,
        "selected_events": list(selected_events),
        "events": event_reports,
        "combined_ndc_bounds_min": combined_min,
        "combined_ndc_bounds_max": combined_max,
        "combined_ndc_bounds_center": combined_center,
        "vertices": sum(item["vertices"] for item in event_reports),
        "triangles": sum(item["triangles"] for item in event_reports),
        "output_size": len(glb),
        "output_sha256": _sha256(glb),
        "gates": {
            "complete_transform_bundle_identity": True,
            "straight_line_fixed_constant_programs": True,
            "attribute_zero_to_output_zero_affine_path": True,
            "homogeneous_divide": True,
            "finite_nondegenerate_geometry": True,
            "screenshot_aligned": True,
            "attribute_zero_position_semantics": False,
            "static_shader_reference": static_reference,
            "runtime_texture_sampling": False,
            "component_ownership": False,
            "world_space": False,
            "full_character": False,
            "skin_weights": False,
            "skeleton": False,
            "rigging": False,
            "retail_material": False,
            "mod_ready": False,
        },
        "verdict": "selected-draws-exported-in-screenshot-aligned-ndc-frame",
        "next_gate": (
            "render and compare each event against the exact foreground frame, then "
            "classify prop versus character before any world-space or mod-ready claim"
        ),
    }
