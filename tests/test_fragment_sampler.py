import struct

import pytest

from infamous_xpp_textures.fragment_sampler import (
    FragmentSamplerCensusError,
    analyze_fragment_program_payload,
)


def _captured_word(decoded):
    rotated = ((decoded >> 16) | ((decoded & 0xFFFF) << 16)) & 0xFFFFFFFF
    return int.from_bytes(rotated.to_bytes(4, "big"), "little")


def _instruction(
    *,
    opcode=1,
    sampler=0,
    branch=False,
    constant=False,
    end=False,
    source_type=0,
    source_attribute=0,
):
    decoded_dest = (
        int(end) | (source_attribute << 13) | (sampler << 17) | (opcode << 24)
    )
    decoded_source0 = source_type | (1 << 11) | (2 << 13) | (3 << 15)
    word0 = _captured_word(decoded_dest)
    word1 = _captured_word(decoded_source0)
    if constant:
        word1 = (word1 & ~(3 << 8)) | (2 << 8)
    word2 = int(branch) << 23
    return struct.pack("<4I", word0, word1, word2, 0)


def test_decodes_texture_opcodes_sampler_mask_and_constant_slot():
    payload = b"".join(
        (
            _instruction(
                opcode=0x17, sampler=3, source_type=1, source_attribute=4
            ),
            _instruction(
                opcode=0x31,
                sampler=7,
                constant=True,
                source_type=1,
                source_attribute=4,
            ),
            bytes(16),
            _instruction(opcode=0x2F, sampler=3, end=True),
        )
    )
    report = analyze_fragment_program_payload(payload)
    assert report["instruction_count"] == 3
    assert report["embedded_constant_slots"] == 1
    assert report["texture_instruction_count"] == 3
    assert report["sampler_slots"] == [3, 7]
    assert report["referenced_textures_mask"] == (1 << 3) | (1 << 7)
    assert [item["opcode"] for item in report["texture_instructions"]] == [
        "TEX",
        "TXB",
        "TXL",
    ]
    assert report["texture_instructions"][0]["coordinate_source_is_input"] is True
    assert report["texture_instructions"][0]["fragment_input_attribute"] == 4
    assert report["texture_instructions"][0]["fragment_input_name"] == "TEX0"
    assert report["texture_instructions"][0]["coordinate_swizzle"] == [0, 1, 2, 3]
    assert report["runtime_branch_execution_proved"] is False


def test_branch_word_does_not_create_false_texture_reference():
    payload = _instruction(opcode=0x17, sampler=9, branch=True) + _instruction(
        end=True
    )
    report = analyze_fragment_program_payload(payload)
    assert report["branch_instruction_count"] == 1
    assert report["texture_instruction_count"] == 0
    assert report["referenced_textures_mask"] == 0


@pytest.mark.parametrize("payload", (b"", bytes(15), bytes(64 * 1024 + 16)))
def test_rejects_unbounded_or_unaligned_payload(payload):
    with pytest.raises(FragmentSamplerCensusError, match="16-byte-aligned"):
        analyze_fragment_program_payload(payload)


def test_rejects_trailing_data_after_end_marker():
    payload = _instruction(end=True) + _instruction(end=True)
    with pytest.raises(FragmentSamplerCensusError, match="end marker"):
        analyze_fragment_program_payload(payload)


def test_rejects_truncated_constant_slot():
    payload = _instruction(constant=True, end=True)
    with pytest.raises(FragmentSamplerCensusError, match="constant slot"):
        analyze_fragment_program_payload(payload)
