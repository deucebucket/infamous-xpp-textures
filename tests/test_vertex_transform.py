import struct

import pytest

from infamous_xpp_textures.vertex_transform import (
    VertexTransformCensusError,
    analyze_vertex_program_payload,
)


def _source(register_type: int) -> int:
    # Identity xyzw swizzle in the RSX SRC bit layout.
    return register_type | (3 << 8) | (2 << 10) | (1 << 12)


def _instruction(
    *,
    vec: int = 0,
    sca: int = 0,
    src0: int = 1,
    src1: int = 1,
    src2: int = 1,
    input_id: int = 0,
    constant_id: int = 0,
    destination: int = 0,
    vector_result: bool = True,
    indexed: bool = False,
    end: bool = False,
    condition: int = 0,
    jump: int = 0,
) -> tuple[int, int, int, int]:
    d0 = (condition << 10) | (int(vector_result) << 30)
    d1 = (
        ((src0 >> 9) & 0xFF)
        | (input_id << 8)
        | (constant_id << 12)
        | (vec << 22)
        | (sca << 27)
    )
    d2 = (
        ((src2 >> 11) & 0x3F)
        | ((src1 & 0x1FFFF) << 6)
        | ((src0 & 0x1FF) << 23)
    )
    d3 = (
        int(end)
        | (int(indexed) << 1)
        | (destination << 2)
        | ((src2 & 0x7FF) << 21)
    )
    d0 |= ((jump >> 9) & 1) << 23
    d2 |= ((jump >> 3) & 0x3F)
    d3 |= (jump & 7) << 29
    return d0, d1, d2, d3


def _payload(instructions, *, entry=0):
    words = [0] * (544 * 4)
    for index, instruction in instructions.items():
        words[index * 4 : index * 4 + 4] = instruction
    return struct.pack("<2177I", *words, entry)


def test_decodes_exact_inputs_constants_and_indexed_flag():
    program = _payload(
        {
            0: _instruction(
                vec=2,
                src0=_source(2),
                src1=_source(3),
                input_id=8,
                constant_id=256,
                destination=1,
                indexed=True,
            ),
            1: _instruction(
                vec=7,
                src0=_source(1),
                src1=_source(3),
                constant_id=257,
                destination=0,
                end=True,
            ),
        }
    )
    constants = bytearray(8192)
    struct.pack_into("<4f", constants, 256 * 16, 1.0, 0.0, 0.0, 0.0)
    struct.pack_into("<4f", constants, 257 * 16, 0.0, 1.0, 0.0, 0.0)

    report = analyze_vertex_program_payload(program, bytes(constants))

    assert report["reachable_instruction_indices"] == [0, 1]
    assert [item["index"] for item in report["input_attributes"]] == [0, 8]
    assert [item["index"] for item in report["explicit_input_attributes"]] == [8]
    assert report["fixed_constant_ids"] == [256, 257]
    assert report["indexed_constants"] is True
    assert report["vector_opcode_census"] == {"DP4": 1, "MUL": 1}
    assert report["output_registers_written"] == [0, 1]
    assert report["referenced_constant_finite_components"] == 8
    assert report["referenced_constant_nonzero_components"] == 2


def test_static_branch_skips_unreachable_unknown_instruction():
    program = _payload(
        {
            0: _instruction(sca=9, condition=7, jump=2),
            1: _instruction(vec=24, end=True),
            2: _instruction(vec=1, src0=_source(2), input_id=10, end=True),
        }
    )
    report = analyze_vertex_program_payload(program, bytes(8192))
    assert report["reachable_instruction_indices"] == [0, 2]
    assert [item["index"] for item in report["input_attributes"]] == [0, 10]
    assert [item["index"] for item in report["explicit_input_attributes"]] == [10]
    assert report["branch_instruction_count"] == 1


@pytest.mark.parametrize("program_size,constant_size", [(8707, 8192), (8708, 8191)])
def test_rejects_wrong_fixed_payload_size(program_size, constant_size):
    with pytest.raises(VertexTransformCensusError, match="wrong fixed size"):
        analyze_vertex_program_payload(bytes(program_size), bytes(constant_size))


def test_rejects_used_invalid_register_type():
    program = _payload({0: _instruction(vec=1, src0=_source(0), end=True)})
    with pytest.raises(VertexTransformCensusError, match="invalid register type"):
        analyze_vertex_program_payload(program, bytes(8192))
