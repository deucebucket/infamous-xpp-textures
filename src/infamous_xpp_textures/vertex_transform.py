"""Strict offline census of captured RSX vertex-transform programs."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import math
from pathlib import Path
import struct

from .runtime_topology_export import (
    RuntimeTopologyExportError,
    _load_bundle,
    _read_payload,
)


class VertexTransformCensusError(ValueError):
    """Raised when a captured transform bundle cannot be decoded exactly."""


_MAX_INSTRUCTIONS = 544
_PROGRAM_BYTES = _MAX_INSTRUCTIONS * 16 + 4
_CONSTANT_BYTES = 512 * 16
_INPUT_NAMES = (
    "position",
    "weight",
    "normal",
    "diffuse_color",
    "specular_color",
    "fog",
    "point_size",
    "attribute_7",
    "texcoord_0",
    "texcoord_1",
    "texcoord_2",
    "texcoord_3",
    "texcoord_4",
    "texcoord_5",
    "texcoord_6",
    "texcoord_7",
)
_VEC_NAMES = (
    "NOP",
    "MOV",
    "MUL",
    "ADD",
    "MAD",
    "DP3",
    "DPH",
    "DP4",
    "DST",
    "MIN",
    "MAX",
    "SLT",
    "SGE",
    "ARL",
    "FRC",
    "FLR",
    "SEQ",
    "SFL",
    "SGT",
    "SLE",
    "SNE",
    "STR",
    "SSG",
    None,
    None,
    "TXL",
)
_SCA_NAMES = (
    "NOP",
    "MOV",
    "RCP",
    "RCC",
    "RSQ",
    "EXP",
    "LOG",
    "LIT",
    "BRA",
    "BRI",
    "CAL",
    "CLI",
    "RET",
    "LG2",
    "EX2",
    "SIN",
    "COS",
    "BRB",
    "CLB",
    "PSH",
    "POP",
)
_VEC_SOURCES = {
    0: (),
    1: (0,),
    2: (0, 1),
    3: (0, 2),
    4: (0, 1, 2),
    5: (0, 1),
    6: (0, 1),
    7: (0, 1),
    8: (0, 1),
    9: (0, 1),
    10: (0, 1),
    11: (0, 1),
    12: (0, 1),
    13: (0,),
    14: (0,),
    15: (0,),
    16: (0, 1),
    17: (0,),
    18: (0, 1),
    19: (0, 1),
    20: (0, 1),
    21: (0,),
    22: (0,),
    25: (0,),
}
_SCA_SOURCE_OPS = frozenset((*range(1, 8), *range(13, 17)))
_BRANCH_OPS = frozenset((8, 9, 10, 11, 17, 18))
_CALL_OPS = frozenset((10, 11, 18))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _field(word: int, offset: int, bits: int) -> int:
    return (word >> offset) & ((1 << bits) - 1)


def _source_words(d1: int, d2: int, d3: int) -> tuple[int, int, int]:
    return (
        _field(d2, 23, 9) | (_field(d1, 0, 8) << 9),
        _field(d2, 6, 17),
        _field(d3, 21, 11) | (_field(d2, 0, 6) << 11),
    )


def _jump_target(d0: int, d2: int, d3: int) -> int:
    return (_field(d0, 23, 1) << 9) | (_field(d2, 0, 6) << 3) | _field(
        d3, 29, 3
    )


def _walk_reachable(words: tuple[int, ...], entry: int) -> tuple[int, ...]:
    if entry >= _MAX_INSTRUCTIONS:
        raise VertexTransformCensusError("vertex-program entry is outside 544 instructions")
    reached: set[int] = set()
    pending: list[tuple[int, bool]] = [(entry, False)]
    has_branch = False
    low = _MAX_INSTRUCTIONS
    high = 0
    total_steps = 0

    while pending:
        start, fast_exit = pending.pop()
        pc = start
        call_stack: list[int] = []
        conditional_targets: set[int] = set()
        while True:
            total_steps += 1
            if total_steps > _MAX_INSTRUCTIONS * 16:
                raise VertexTransformCensusError("vertex-program control flow exceeded bound")
            if pc >= _MAX_INSTRUCTIONS:
                raise VertexTransformCensusError(
                    "vertex-program control flow leaves the captured register image"
                )
            if pc in reached:
                break
            reached.add(pc)
            low = min(low, pc)
            high = max(high, pc)
            d0, d1, d2, d3 = words[pc * 4 : pc * 4 + 4]
            sca = _field(d1, 27, 5)
            if sca >= len(_SCA_NAMES):
                raise VertexTransformCensusError("unknown reachable scalar opcode")

            if sca in _BRANCH_OPS:
                has_branch = True
                target = _jump_target(d0, d2, d3)
                if target >= _MAX_INSTRUCTIONS:
                    raise VertexTransformCensusError("vertex-program jump is out of range")
                if sca in _CALL_OPS:
                    call_stack.append(pc + 1)
                    pc = target
                    continue
                static_jump = sca == 9 and _field(d0, 10, 3) == 7
                if static_jump:
                    pc = target
                    continue
                conditional_targets.add(target)
                high = max(high, target)
            elif sca == 12:
                if call_stack:
                    pc = call_stack.pop()
                    continue

            if d3 & 1 and (not has_branch or fast_exit or pc >= high):
                break
            if pc + 1 == _MAX_INSTRUCTIONS:
                break
            pc += 1

        for target in sorted(conditional_targets, reverse=True):
            if target not in reached:
                pending.append((target, True))

    if not reached:
        raise VertexTransformCensusError("vertex program has no reachable instructions")
    return tuple(sorted(reached))


def _ranges(values: set[int]) -> list[dict[str, int]]:
    if not values:
        return []
    result: list[dict[str, int]] = []
    start = previous = min(values)
    for value in sorted(values)[1:]:
        if value == previous + 1:
            previous = value
            continue
        result.append({"start": start, "end": previous, "count": previous - start + 1})
        start = previous = value
    result.append({"start": start, "end": previous, "count": previous - start + 1})
    return result


def analyze_vertex_program_payload(program: bytes, constants: bytes) -> dict:
    """Decode one exact captured program and constant bank without executing it."""

    if len(program) != _PROGRAM_BYTES or len(constants) != _CONSTANT_BYTES:
        raise VertexTransformCensusError("transform payload has the wrong fixed size")
    unpacked = struct.unpack(f"<{_MAX_INSTRUCTIONS * 4 + 1}I", program)
    words = unpacked[:-1]
    entry = unpacked[-1]
    reachable = _walk_reachable(words, entry)
    explicit_inputs: set[int] = set()
    fixed_constants: set[int] = set()
    indexed_constants = False
    address_writes = 0
    branch_instructions = 0
    vec_counts: Counter[str] = Counter()
    sca_counts: Counter[str] = Counter()
    output_registers: set[int] = set()

    for pc in reachable:
        d0, d1, d2, d3 = words[pc * 4 : pc * 4 + 4]
        vec = _field(d1, 22, 5)
        sca = _field(d1, 27, 5)
        if vec >= len(_VEC_NAMES) or _VEC_NAMES[vec] is None:
            raise VertexTransformCensusError("unknown reachable vector opcode")
        vec_counts[_VEC_NAMES[vec]] += 1
        sca_counts[_SCA_NAMES[sca]] += 1
        if sca in _BRANCH_OPS or sca == 12:
            branch_instructions += 1
        if vec == 13:
            address_writes += 1

        used_sources = set(_VEC_SOURCES[vec])
        if sca in _SCA_SOURCE_OPS:
            used_sources.add(2)
        raw_sources = _source_words(d1, d2, d3)
        for source_number in used_sources:
            register_type = raw_sources[source_number] & 3
            if register_type == 0:
                raise VertexTransformCensusError("reachable source has invalid register type")
            if register_type == 2:
                explicit_inputs.add(_field(d1, 8, 4))
            elif register_type == 3:
                constant_id = _field(d1, 12, 10)
                if constant_id >= 468:
                    raise VertexTransformCensusError(
                        "reachable fixed constant is outside RPCS3's usable bank"
                    )
                fixed_constants.add(constant_id)
                indexed_constants |= bool(_field(d3, 1, 1))

        destination = _field(d3, 2, 5)
        if destination != 31:
            vector_writes_result = bool(_field(d0, 30, 1))
            scalar_writes_result = not vector_writes_result
            if (vec != 0 and vector_writes_result) or (
                sca in _SCA_SOURCE_OPS and scalar_writes_result
            ):
                output_registers.add(destination)

    vector_payloads = [constants[item * 16 : (item + 1) * 16] for item in sorted(fixed_constants)]
    floats = [
        value
        for payload in vector_payloads
        for value in struct.unpack("<4f", payload)
    ]
    ranges = _ranges(fixed_constants)
    return {
        "entry_instruction": entry,
        "reachable_instruction_count": len(reachable),
        "reachable_instruction_first": reachable[0],
        "reachable_instruction_last": reachable[-1],
        "reachable_instruction_indices": list(reachable),
        "input_attributes": [
            {"index": item, "name": _INPUT_NAMES[item]}
            for item in sorted(explicit_inputs | {0})
        ],
        "explicit_input_attributes": [
            {"index": item, "name": _INPUT_NAMES[item]}
            for item in sorted(explicit_inputs)
        ],
        "position_input_mandatory_in_rpcs3_mask": True,
        "fixed_constant_ids": sorted(fixed_constants),
        "fixed_constant_ranges": ranges,
        "four_plus_vector_ranges": [item for item in ranges if item["count"] >= 4],
        "indexed_constants": indexed_constants,
        "address_register_write_count": address_writes,
        "branch_instruction_count": branch_instructions,
        "vector_opcode_census": dict(sorted(vec_counts.items())),
        "scalar_opcode_census": dict(sorted(sca_counts.items())),
        "output_registers_written": sorted(output_registers),
        "referenced_constant_vector_count": len(vector_payloads),
        "referenced_constant_vectors_sha256": _sha256(b"".join(vector_payloads)),
        "referenced_constant_finite_components": sum(math.isfinite(value) for value in floats),
        "referenced_constant_nonzero_components": sum(value != 0.0 for value in floats),
        "_constant_vectors": {
            item: constants[item * 16 : (item + 1) * 16] for item in fixed_constants
        },
    }


def analyze_vertex_transform_bundle(bundle: Path, texture_allowlist: Path) -> dict:
    """Validate a complete v2/v3 bundle and return a payload-free transform census."""

    try:
        completion, events, allowlist_sha256 = _load_bundle(bundle, texture_allowlist)
    except RuntimeTopologyExportError as exc:
        raise VertexTransformCensusError(str(exc)) from exc
    if completion["format"] not in (
        "if1-texture-bound-topology-v2",
        "if1-texture-bound-topology-v3",
    ):
        raise VertexTransformCensusError(
            "vertex-transform census requires if1-texture-bound-topology-v2 or v3"
        )

    event_reports: list[dict] = []
    for number, event in sorted(events.items()):
        if event.vertex_program_file is None or event.transform_constants_file is None:
            raise VertexTransformCensusError("v2 event is missing transform payloads")
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
            raise VertexTransformCensusError(str(exc)) from exc
        analysis = analyze_vertex_program_payload(program, constants)
        event_reports.append(
            {
                "event": number,
                "draw_event": event.draw_event,
                "vertex_program_sha256": event.vertex_program_sha256,
                "transform_constants_sha256": event.transform_constants_sha256,
                "target_texture_slot_count": len(event.target_texture_slots),
                "target_texture_identity_count": len(event.target_texture_sha256s),
                **analysis,
            }
        )

    grouped: defaultdict[str, list[dict]] = defaultdict(list)
    for report in event_reports:
        grouped[report["vertex_program_sha256"]].append(report)
    program_groups = []
    for program_sha, reports in sorted(grouped.items()):
        first_ids = reports[0]["fixed_constant_ids"]
        if any(report["fixed_constant_ids"] != first_ids for report in reports[1:]):
            raise VertexTransformCensusError(
                "identical vertex programs decoded to different fixed constant IDs"
            )
        varying = []
        stable = []
        for constant_id in first_ids:
            values = {
                report["_constant_vectors"][constant_id] for report in reports
            }
            (stable if len(values) == 1 else varying).append(constant_id)
        program_groups.append(
            {
                "vertex_program_sha256": program_sha,
                "events": [report["event"] for report in reports],
                "event_count": len(reports),
                "input_attribute_indices": [
                    item["index"] for item in reports[0]["input_attributes"]
                ],
                "explicit_input_attribute_indices": [
                    item["index"]
                    for item in reports[0]["explicit_input_attributes"]
                ],
                "fixed_constant_ids": first_ids,
                "indexed_constants": reports[0]["indexed_constants"],
                "distinct_transform_constant_banks": len(
                    {report["transform_constants_sha256"] for report in reports}
                ),
                "varying_referenced_constant_ids": varying,
                "stable_referenced_constant_ids": stable,
            }
        )

    for report in event_reports:
        report.pop("_constant_vectors")
    indexed_events = [
        report["event"] for report in event_reports if report["indexed_constants"]
    ]
    return {
        "schema_version": 1,
        "kind": "if1-rsx-vertex-transform-census",
        "bundle_format": completion["format"],
        "event_count": len(event_reports),
        "texture_allowlist_sha256": allowlist_sha256,
        "constant_bank_decode": (
            "native little-endian float32 bit patterns; RPCS3 stores byte-swapped FIFO "
            "arguments in native u32 registers and uploads the same 128-bit vectors"
        ),
        "events": event_reports,
        "program_groups": program_groups,
        "indexed_constant_events": indexed_events,
        "gates": {
            "complete_transform_bundle_identity": True,
            "reachable_instruction_census": True,
            "vertex_input_references": True,
            "fixed_constant_references": True,
            "indexed_constant_use": True,
            "constant_value_identity": True,
            "position_semantics": False,
            "matrix_semantics": False,
            "bone_palette": False,
            "skinning": False,
            "draw_ownership": False,
            "assembled_character": False,
            "rigging": False,
            "render_ready": False,
        },
        "verdict": (
            "indexed-constant-use-observed"
            if indexed_events
            else "fixed-constant-programs-observed-no-indexed-constant-use"
        ),
        "next_gate": (
            "reproduce the captured vertex-program arithmetic for one bounded draw, "
            "prove which output is clip position, and compare transformed geometry "
            "without claiming bones or character ownership"
        ),
    }
