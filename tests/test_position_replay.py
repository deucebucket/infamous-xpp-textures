import struct

import pytest

from infamous_xpp_textures.position_replay import (
    PositionReplayError,
    _matrix_inverse,
    _matrix_multiply,
    _matrix_residual,
    extract_output_affine,
)


def _source(register_type: int, index: int = 0, component: int | None = None) -> int:
    if component is None:
        swizzle = (0, 1, 2, 3)
    else:
        swizzle = (component,) * 4
    return (
        register_type
        | (index << 2)
        | (swizzle[3] << 8)
        | (swizzle[2] << 10)
        | (swizzle[1] << 12)
        | (swizzle[0] << 14)
    )


def _instruction(
    *,
    vec: int,
    src0: int,
    src1: int = 1,
    src2: int = 1,
    input_id: int = 0,
    constant_id: int = 0,
    temporary: int = 0,
    output: bool = False,
    end: bool = False,
    conditional: bool = False,
):
    d0 = (7 << 10) | (temporary << 15) | (int(output) << 30)
    if conditional:
        d0 |= 1 << 13
    d1 = (
        ((src0 >> 9) & 0xFF)
        | (input_id << 8)
        | (constant_id << 12)
        | (vec << 22)
    )
    d2 = (
        ((src2 >> 11) & 0x3F)
        | ((src1 & 0x1FFFF) << 6)
        | ((src0 & 0x1FF) << 23)
    )
    d3 = int(end) | ((src2 & 0x7FF) << 21)
    return d0, d1, d2, d3


def _program(instructions):
    words = [0] * (544 * 4)
    for index, instruction in enumerate(instructions):
        words[index * 4 : index * 4 + 4] = instruction
    return struct.pack("<2177I", *words, 0)


def _affine_program(*, conditional=False):
    temporary = _source(1, 0)
    attribute_x = _source(2, component=0)
    attribute_y = _source(2, component=1)
    attribute_z = _source(2, component=2)
    constant = _source(3)
    return _program(
        (
            _instruction(
                vec=2,
                src0=attribute_y,
                src1=constant,
                constant_id=257,
                conditional=conditional,
            ),
            _instruction(
                vec=4,
                src0=attribute_x,
                src1=constant,
                src2=temporary,
                constant_id=256,
            ),
            _instruction(
                vec=4,
                src0=attribute_z,
                src1=constant,
                src2=temporary,
                constant_id=258,
            ),
            _instruction(
                vec=3,
                src0=constant,
                src2=temporary,
                constant_id=259,
                temporary=63,
                output=True,
                end=True,
            ),
        )
    )


def test_extracts_exact_affine_output_matrix():
    constants = bytearray(8192)
    columns = (
        (2.0, 0.0, 0.0, 0.0),
        (0.0, 3.0, 0.0, 0.0),
        (0.0, 0.0, 4.0, 0.0),
        (10.0, 20.0, 30.0, 1.0),
    )
    for offset, values in enumerate(columns, 256):
        struct.pack_into("<4f", constants, offset * 16, *values)

    matrix = extract_output_affine(_affine_program(), bytes(constants))

    assert matrix == [
        [2.0, 0.0, 0.0, 10.0],
        [0.0, 3.0, 0.0, 20.0],
        [0.0, 0.0, 4.0, 30.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def test_rejects_conditional_position_path():
    with pytest.raises(PositionReplayError, match="conditional writes"):
        extract_output_affine(_affine_program(conditional=True), bytes(8192))


def test_matrix_inverse_has_bounded_identity_residual():
    matrix = [
        [2.0, 0.0, 0.0, 10.0],
        [0.0, 3.0, 0.0, 20.0],
        [0.0, 0.0, 4.0, 30.0],
        [0.0, 0.0, 0.5, 1.0],
    ]
    identity = _matrix_multiply(_matrix_inverse(matrix), matrix)
    expected = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    assert _matrix_residual(identity, expected) < 1e-12


def test_rejects_singular_projection_candidate():
    with pytest.raises(PositionReplayError, match="singular"):
        _matrix_inverse([[0.0] * 4 for _ in range(4)])


def test_rejects_non_affine_attribute_product():
    attribute = _source(2)
    program = _program(
        (
            _instruction(
                vec=2,
                src0=attribute,
                src1=attribute,
                temporary=63,
                output=True,
                end=True,
            ),
        )
    )
    with pytest.raises(PositionReplayError, match="not a finite affine function"):
        extract_output_affine(program, bytes(8192))
