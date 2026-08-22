"""Bounded RSX affine position replay for diagnostic multi-draw exports."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import struct

from .character_export import _cross_length_squared, _pack_glb, _write_atomic
from .mesh import GlbBuilder
from .runtime_topology_export import (
    RuntimeTopologyExportError,
    _load_bundle,
    _read_payload,
)
from .vertex_transform import (
    VertexTransformCensusError,
    _MAX_INSTRUCTIONS,
    _SCA_SOURCE_OPS,
    _VEC_SOURCES,
    _field,
    _source_words,
    _walk_reachable,
    analyze_vertex_program_payload,
)


class PositionReplayError(ValueError):
    """Raised when a captured position path is not exactly replayable."""


_PROGRAM_BYTES = _MAX_INSTRUCTIONS * 16 + 4
_CONSTANT_BYTES = 512 * 16
_ZERO = (0.0, 0.0, 0.0, 0.0, 0.0)
_PALETTE = (
    (0.95, 0.28, 0.12, 1.0),
    (0.16, 0.65, 0.95, 1.0),
    (0.22, 0.82, 0.42, 1.0),
    (0.92, 0.68, 0.12, 1.0),
    (0.62, 0.32, 0.95, 1.0),
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _constant(value: float) -> tuple[float, float, float, float, float]:
    return (0.0, 0.0, 0.0, 0.0, value)


def _add(left, right):
    if left is None or right is None:
        return None
    return tuple(left[index] + right[index] for index in range(5))


def _scale(value, factor: float):
    if value is None:
        return None
    return tuple(component * factor for component in value)


def _multiply(left, right):
    if left is None or right is None:
        return None
    left_variable = any(left[index] != 0.0 for index in range(4))
    right_variable = any(right[index] != 0.0 for index in range(4))
    if left_variable and right_variable:
        return None
    if left_variable:
        return _scale(left, right[4])
    if right_variable:
        return _scale(right, left[4])
    return _constant(left[4] * right[4])


def _swizzle(values, raw: int):
    lanes = tuple((raw >> shift) & 3 for shift in (14, 12, 10, 8))
    return tuple(values[lane] for lane in lanes)


def _source(
    raw: int,
    d0: int,
    source_number: int,
    d1: int,
    d3: int,
    temporaries,
    constants,
):
    register_type = raw & 3
    if register_type == 1:
        values = temporaries[(raw >> 2) & 0x3F]
    elif register_type == 2:
        attribute = _field(d1, 8, 4)
        values = (
            (
                (1.0, 0.0, 0.0, 0.0, 0.0),
                (0.0, 1.0, 0.0, 0.0, 0.0),
                (0.0, 0.0, 1.0, 0.0, 0.0),
                (0.0, 0.0, 0.0, 1.0, 0.0),
            )
            if attribute == 0
            else (None, None, None, None)
        )
    elif register_type == 3:
        if _field(d3, 1, 1):
            return (None, None, None, None)
        constant_id = _field(d1, 12, 10)
        if constant_id >= 468:
            return (None, None, None, None)
        values = tuple(_constant(value) for value in constants[constant_id])
    else:
        return (None, None, None, None)
    result = list(_swizzle(values, raw))
    if _field(d0, 21 + source_number, 1):
        result = [
            _constant(abs(value[4]))
            if value is not None and not any(value[:4])
            else None
            for value in result
        ]
    if _field(raw, 16, 1):
        result = [_scale(value, -1.0) for value in result]
    return tuple(result)


def _vector_result(opcode: int, sources):
    source0, source1, source2 = sources
    if opcode == 1:
        return source0
    if opcode == 2:
        return tuple(_multiply(source0[lane], source1[lane]) for lane in range(4))
    if opcode == 3:
        return tuple(_add(source0[lane], source2[lane]) for lane in range(4))
    if opcode == 4:
        return tuple(
            _add(_multiply(source0[lane], source1[lane]), source2[lane])
            for lane in range(4)
        )
    if opcode in (5, 7):
        lanes = 3 if opcode == 5 else 4
        value = _ZERO
        for lane in range(lanes):
            value = _add(value, _multiply(source0[lane], source1[lane]))
        return (value, value, value, value)
    return (None, None, None, None)


def _write_masked(destination, value, d3: int):
    mask = tuple(bool(_field(d3, bit, 1)) for bit in (16, 15, 14, 13))
    if not any(mask):
        mask = (True, True, True, True)
    return tuple(value[lane] if mask[lane] else destination[lane] for lane in range(4))


def extract_output_affine(program: bytes, constants_payload: bytes) -> list[list[float]]:
    """Return output-register-zero as a 4x4 affine map of attribute zero."""

    try:
        census = analyze_vertex_program_payload(program, constants_payload)
    except VertexTransformCensusError as exc:
        raise PositionReplayError(str(exc)) from exc
    if census["indexed_constants"] or census["branch_instruction_count"]:
        raise PositionReplayError("position replay requires straight-line fixed constants")
    if 0 not in census["output_registers_written"]:
        raise PositionReplayError("vertex program does not write output register zero")
    constants_flat = struct.unpack("<2048f", constants_payload)
    constants = tuple(
        constants_flat[index * 4 : index * 4 + 4] for index in range(512)
    )
    words = struct.unpack(f"<{_MAX_INSTRUCTIONS * 4 + 1}I", program)[:-1]
    temporaries = [[_ZERO, _ZERO, _ZERO, _ZERO] for _ in range(64)]
    outputs = [[None, None, None, None] for _ in range(32)]

    for pc in census["reachable_instruction_indices"]:
        d0, d1, d2, d3 = words[pc * 4 : pc * 4 + 4]
        if _field(d0, 10, 3) != 7 or _field(d0, 13, 1):
            raise PositionReplayError(
                "nontrivial conditional writes are outside replay scope"
            )
        if _field(d0, 26, 1) or _field(d0, 27, 1):
            raise PositionReplayError(
                "saturated or indexed-input writes are outside replay scope"
            )
        vec = _field(d1, 22, 5)
        scalar = _field(d1, 27, 5)
        raw_sources = _source_words(d1, d2, d3)
        source_values = tuple(
            _source(raw_sources[number], d0, number, d1, d3, temporaries, constants)
            for number in range(3)
        )
        if vec:
            value = _vector_result(vec, source_values)
            temporary = _field(d0, 15, 6)
            if temporary != 63:
                temporaries[temporary] = list(
                    _write_masked(temporaries[temporary], value, d3)
                )
            if _field(d0, 30, 1):
                destination = _field(d3, 2, 5)
                if destination != 31:
                    outputs[destination] = list(
                        _write_masked(outputs[destination], value, d3)
                    )
        if scalar in _SCA_SOURCE_OPS:
            temporary = _field(d3, 7, 6)
            if temporary != 63:
                temporaries[temporary] = [None, None, None, None]
            if not _field(d0, 30, 1):
                destination = _field(d3, 2, 5)
                if destination != 31:
                    outputs[destination] = [None, None, None, None]

    result = outputs[0]
    if any(value is None or not all(math.isfinite(item) for item in value) for value in result):
        raise PositionReplayError(
            "output zero is not a finite affine function of attribute zero alone"
        )
    # Attribute zero is captured as float32x3. Its hardware-default W is one,
    # so the W coefficient folds into the homogeneous translation column.
    return [
        [value[0], value[1], value[2], value[3] + value[4]] for value in result
    ]


def _matrix_multiply(left, right):
    return [
        [sum(left[row][axis] * right[axis][column] for axis in range(4)) for column in range(4)]
        for row in range(4)
    ]


def _matrix_vector(matrix, value):
    return [sum(matrix[row][axis] * value[axis] for axis in range(4)) for row in range(4)]


def _matrix_inverse(matrix):
    augmented = [
        [float(value) for value in matrix[row]]
        + [1.0 if row == column else 0.0 for column in range(4)]
        for row in range(4)
    ]
    for column in range(4):
        pivot = max(range(column, 4), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise PositionReplayError("projection candidate is singular")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(4):
            if row == column:
                continue
            scale = augmented[row][column]
            augmented[row] = [
                augmented[row][item] - scale * augmented[column][item]
                for item in range(8)
            ]
    return [row[4:] for row in augmented]


def _matrix_residual(left, right) -> float:
    return max(
        abs(left[row][column] - right[row][column])
        for row in range(4)
        for column in range(4)
    )


def _constant_matrix(constants_payload: bytes, start: int):
    if not 0 <= start <= 464:
        raise PositionReplayError("four-vector matrix start is outside usable constants")
    values = struct.unpack_from("<16f", constants_payload, start * 16)
    return [[values[column * 4 + row] for column in range(4)] for row in range(4)]


def _event_payloads(bundle, event):
    if event.vertex_program_file is None or event.transform_constants_file is None:
        raise PositionReplayError("v2 event is missing transform payloads")
    try:
        program = _read_payload(
            bundle, event.vertex_program_file, _PROGRAM_BYTES, event.vertex_program_sha256
        )
        constants = _read_payload(
            bundle,
            event.transform_constants_file,
            _CONSTANT_BYTES,
            event.transform_constants_sha256,
        )
    except RuntimeTopologyExportError as exc:
        raise PositionReplayError(str(exc)) from exc
    return program, constants


def _event_geometry(bundle, event):
    candidates = [
        (block, attribute)
        for block in event.blocks
        for attribute in block.attributes
        if attribute["attribute"] == 0
    ]
    if len(candidates) != 1:
        raise PositionReplayError("event must have exactly one attribute-zero stream")
    block, attribute = candidates[0]
    if (
        attribute["type"] != 2
        or attribute["components"] != 3
        or attribute["frequency"] != 0
        or attribute["modulo"] != 0
        or attribute["array_stride"] != block.stride
        or block.stride < 12
        or block.payload_bytes != block.range_count * block.stride
    ):
        raise PositionReplayError("attribute zero is not bounded float32x3")
    try:
        index_payload = _read_payload(
            bundle, event.index_payload_file, event.index_bytes, event.index_sha256
        )
        position_payload = _read_payload(
            bundle, block.payload_file, block.payload_bytes, block.payload_sha256
        )
    except RuntimeTopologyExportError as exc:
        raise PositionReplayError(str(exc)) from exc
    indices = struct.unpack(f">{event.index_count}H", index_payload)
    if (
        not indices
        or min(indices) < block.range_first
        or max(indices) >= block.range_first + block.range_count
    ):
        raise PositionReplayError("attribute-zero indices leave the captured range")
    local_indices = tuple(value - block.range_first for value in indices)
    positions = [
        struct.unpack_from(">3f", position_payload, index * block.stride)
        for index in range(block.range_count)
    ]
    if not all(math.isfinite(value) for position in positions for value in position):
        raise PositionReplayError("attribute-zero stream contains non-finite values")
    return block, local_indices, positions


def export_position_replay_glb(
    bundle: Path,
    texture_allowlist: Path,
    selected_events: tuple[int, ...],
    output: Path,
    *,
    projection_event: int,
    model_constant_start: int,
    projection_constant_start: int,
) -> dict:
    """Export selected draws in one recovered pre-projection coordinate frame."""

    if output.is_symlink() or output.exists():
        raise PositionReplayError("position replay output already exists")
    if not selected_events or len(set(selected_events)) != len(selected_events):
        raise PositionReplayError("selected events must be unique and non-empty")
    try:
        completion, events, allowlist_sha256 = _load_bundle(bundle, texture_allowlist)
    except RuntimeTopologyExportError as exc:
        raise PositionReplayError(str(exc)) from exc
    if completion["format"] != "if1-texture-bound-topology-v2":
        raise PositionReplayError("position replay requires a complete v2 bundle")
    if projection_event not in events or any(item not in events for item in selected_events):
        raise PositionReplayError("selected or projection event is absent")

    matrices = {}
    constant_payloads = {}
    for number, event in sorted(events.items()):
        program, constants = _event_payloads(bundle, event)
        matrices[number] = extract_output_affine(program, constants)
        constant_payloads[number] = constants

    projection = _constant_matrix(
        constant_payloads[projection_event], projection_constant_start
    )
    projection_inverse = _matrix_inverse(projection)
    identity = _matrix_multiply(projection_inverse, projection)
    inverse_residual = _matrix_residual(
        identity,
        [[1.0 if row == column else 0.0 for column in range(4)] for row in range(4)],
    )
    validation_events = []
    validation_residual = 0.0
    for number in sorted(events):
        model = _constant_matrix(constant_payloads[number], model_constant_start)
        residual = _matrix_residual(matrices[number], _matrix_multiply(projection, model))
        if residual <= 1e-3:
            validation_events.append(number)
            validation_residual = max(validation_residual, residual)
    if projection_event not in validation_events or len(validation_events) < 2:
        raise PositionReplayError(
            "projection candidate lacks two exact output-path decompositions"
        )

    transformed = []
    all_positions = []
    for number in selected_events:
        event = events[number]
        block, indices, positions = _event_geometry(bundle, event)
        view_matrix = _matrix_multiply(projection_inverse, matrices[number])
        view_positions = []
        for position in positions:
            value = _matrix_vector(view_matrix, (*position, 1.0))
            if not all(math.isfinite(item) for item in value) or abs(value[3]) < 1e-12:
                raise PositionReplayError("replayed position is non-finite or has zero W")
            view = tuple(value[index] / value[3] for index in range(3))
            view_positions.append(view)
            all_positions.append(view)
        nondegenerate = sum(
            _cross_length_squared(
                view_positions[indices[offset]],
                view_positions[indices[offset + 1]],
                view_positions[indices[offset + 2]],
            )
            > 1e-12
            for offset in range(0, len(indices), 3)
        )
        if not nondegenerate:
            raise PositionReplayError("replayed event has only degenerate triangles")
        minimum = [min(value[axis] for value in view_positions) for axis in range(3)]
        maximum = [max(value[axis] for value in view_positions) for axis in range(3)]
        transformed.append(
            {
                "event": number,
                "event_record": event,
                "block": block,
                "indices": indices,
                "positions": view_positions,
                "vertices": len(view_positions),
                "triangles": len(indices) // 3,
                "nondegenerate_triangles": nondegenerate,
                "view_bounds_min": minimum,
                "view_bounds_max": maximum,
                "view_bounds_center": [
                    (minimum[axis] + maximum[axis]) / 2.0 for axis in range(3)
                ],
                "view_matrix_sha256": _sha256(
                    struct.pack("<16d", *(item for row in view_matrix for item in row))
                ),
            }
        )

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
        positions = [
            (
                value[0] - combined_center[0],
                value[2] - combined_center[2],
                -(value[1] - combined_center[1]),
            )
            for value in item["positions"]
        ]
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
                "extras": {"event": item["event"], "componentOwnershipProved": False},
            }
        )
        materials.append(
            {
                "name": f"Event {item['event']:02d} diagnostic neutral",
                "doubleSided": True,
                "extensions": {"KHR_materials_unlit": {}},
                "pbrMetallicRoughness": {
                    "baseColorFactor": list(_PALETTE[index % len(_PALETTE)]),
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            }
        )

    evidence = {
        "diagnosticOnly": True,
        "selectedEvents": list(selected_events),
        "projectionEvent": projection_event,
        "projectionConstantStart": projection_constant_start,
        "modelConstantStart": model_constant_start,
        "projectionValidationEvents": validation_events,
        "componentOwnershipProved": False,
        "textureSamplingProved": False,
        "fullCharacterProved": False,
        "rigged": False,
        "retailMaterialProved": False,
    }
    document = {
        "asset": {
            "version": "2.0",
            "generator": "xpp-tool 2.14.0 bounded RSX position replay",
            "extras": {"infamousPositionReplay": evidence},
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
                "view_bounds_min",
                "view_bounds_max",
                "view_bounds_center",
                "view_matrix_sha256",
            }
        }
        for item in transformed
    ]
    centers = [item["view_bounds_center"] for item in event_reports]
    max_center_distance = max(
        (
            math.dist(centers[left], centers[right])
            for left in range(len(centers))
            for right in range(left + 1, len(centers))
        ),
        default=0.0,
    )
    return {
        "schema_version": 1,
        "kind": "if1-rsx-position-replay-export",
        "bundle_format": completion["format"],
        "texture_allowlist_sha256": allowlist_sha256,
        "selected_events": list(selected_events),
        "projection_event": projection_event,
        "model_constant_start": model_constant_start,
        "projection_constant_start": projection_constant_start,
        "projection_matrix_sha256": _sha256(
            struct.pack("<16d", *(item for row in projection for item in row))
        ),
        "projection_inverse_residual_max": inverse_residual,
        "projection_validation_events": validation_events,
        "projection_decomposition_residual_max": validation_residual,
        "events": event_reports,
        "combined_view_bounds_min": combined_min,
        "combined_view_bounds_max": combined_max,
        "combined_view_bounds_center": combined_center,
        "maximum_event_center_distance": max_center_distance,
        "vertices": sum(item["vertices"] for item in event_reports),
        "triangles": sum(item["triangles"] for item in event_reports),
        "output_size": len(glb),
        "output_sha256": _sha256(glb),
        "gates": {
            "complete_v2_bundle_identity": True,
            "straight_line_fixed_constant_programs": True,
            "attribute_zero_to_output_zero_affine_path": True,
            "shared_projection_candidate": True,
            "inverse_projection_replay": True,
            "finite_nondegenerate_geometry": True,
            "attribute_zero_position_semantics": False,
            "texture_shader_sampling": False,
            "component_ownership": False,
            "full_character": False,
            "skin_weights": False,
            "skeleton": False,
            "rigging": False,
            "retail_material": False,
            "render_ready": False,
        },
        "verdict": "selected-draws-exported-in-shared-pre-projection-frame",
        "next_gate": (
            "render this neutral diagnostic immediately, then isolate which clustered "
            "draws visibly form the character before adding proven UV/material data"
        ),
    }
