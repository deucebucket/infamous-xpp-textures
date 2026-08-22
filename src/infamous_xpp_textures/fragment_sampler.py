"""Bounded RSX fragment-program sampler census."""

from __future__ import annotations

import struct


class FragmentSamplerCensusError(ValueError):
    """Raised when a captured fragment program is structurally invalid."""


_TEXTURE_OPCODES = {
    0x17: "TEX",
    0x18: "TXP",
    0x19: "TXD",
    0x2F: "TXL",
    0x31: "TXB",
    0x33: "TEXBEM",
    0x34: "TXPBEM",
}
_MAX_FRAGMENT_PROGRAM_BYTES = 64 * 1024


def analyze_fragment_program_payload(payload: bytes) -> dict:
    """Reproduce RPCS3's bounded static texture-reference walk independently."""

    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > _MAX_FRAGMENT_PROGRAM_BYTES
        or len(payload) % 16
    ):
        raise FragmentSamplerCensusError(
            "fragment program must be a non-empty 16-byte-aligned payload at most 64 KiB"
        )

    offset = 0
    instruction_count = 0
    constant_slots = 0
    branch_instructions = 0
    texture_instructions: list[dict[str, int | str]] = []
    end_seen = False
    while offset < len(payload):
        if len(payload) - offset < 16:
            raise FragmentSamplerCensusError("fragment instruction is truncated")
        word0, word1, word2, word3 = struct.unpack_from("<4I", payload, offset)
        instruction_offset = offset
        instruction_count += 1
        branch = bool(word2 & (1 << 23))
        if branch:
            branch_instructions += 1
        else:
            opcode = (word0 >> 16) & 0x3F
            if opcode in _TEXTURE_OPCODES:
                texture_instructions.append(
                    {
                        "instruction": instruction_count - 1,
                        "byte_offset": instruction_offset,
                        "opcode": _TEXTURE_OPCODES[opcode],
                        "sampler": (word0 >> 25) & 0xF,
                    }
                )
            has_constant = any(
                ((word >> 8) & 0x3) == 2 for word in (word1, word2, word3)
            )
            if has_constant:
                constant_slots += 1
                offset += 16
                if offset + 16 > len(payload):
                    raise FragmentSamplerCensusError(
                        "fragment constant slot extends beyond the payload"
                    )

        offset += 16
        if (word0 >> 8) & 1:
            end_seen = True
            break

    if not end_seen or offset != len(payload):
        raise FragmentSamplerCensusError(
            "fragment program end marker does not reconcile with its exact extent"
        )

    sampler_slots = sorted({int(item["sampler"]) for item in texture_instructions})
    referenced_mask = sum(1 << slot for slot in sampler_slots)
    return {
        "payload_bytes": len(payload),
        "instruction_count": instruction_count,
        "embedded_constant_slots": constant_slots,
        "branch_instruction_count": branch_instructions,
        "texture_instruction_count": len(texture_instructions),
        "texture_instructions": texture_instructions,
        "sampler_slots": sampler_slots,
        "referenced_textures_mask": referenced_mask,
        "runtime_branch_execution_proved": False,
    }
