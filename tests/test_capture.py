import gzip
import struct

import pytest

from infamous_xpp_textures.capture import (
    RRC_MAGIC,
    RRC_VERSION,
    RrcCaptureError,
    build_rrc_character_match_report,
)
from test_character import _wrapped_character_xpp


def _vle(value: int) -> bytes:
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        result.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(result)


def _rrc(index_payload: bytes, *, missing_block_reference: bool = False) -> bytes:
    block_key = 0x1111222233334444
    data_state = 0xAAAABBBBCCCCDDDD
    result = bytearray(struct.pack("<III", RRC_MAGIC, RRC_VERSION, 1))
    result.extend(_vle(0))
    result.extend(_vle(1))
    result.extend(
        struct.pack("<QIIQ", block_key, 0x123400, 0, data_state)
    )
    result.extend(_vle(1))
    result.extend(struct.pack("<Q", data_state))
    result.extend(_vle(len(index_payload)))
    result.extend(index_payload)
    result.extend(_vle(0))
    result.extend(_vle(1))
    result.extend(struct.pack("<II", 0x00001810, 0x00000003))
    result.extend(_vle(1))
    result.extend(
        struct.pack(
            "<Q", block_key + 1 if missing_block_reference else block_key
        )
    )
    result.extend(struct.pack("<QQ", 0, 0))
    result.extend(b"synthetic-register-state")
    return gzip.compress(bytes(result), mtime=0)


def test_capture_report_binds_exact_xpp_index_stream(tmp_path):
    xpp = _wrapped_character_xpp()
    index_payload = struct.pack(">6H", 0, 1, 2, 2, 3, 0)
    capture = tmp_path / "frame.rrc.gz"
    capture.write_bytes(_rrc(index_payload))
    report = build_rrc_character_match_report(xpp, "target.xpp", capture)
    assert report["capture"]["memory_block_count"] == 1
    assert report["capture"]["memory_payload_count"] == 1
    assert report["capture"]["replay_command_count"] == 1
    assert report["exact_match_count"] == 1
    assert report["matched_target_record_count"] == 1
    assert report["exact_matches"][0]["record_offset"] == 0x20
    assert report["exact_matches"][0]["memory_blocks"][0]["offset"] == 0x123400
    assert report["match_status"] == "exact-index-match"
    assert report["payload_bytes_serialized"] is False
    assert report["decoded_vertex_semantics_proved"] is False


def test_capture_report_accepts_uncompressed_rrc(tmp_path):
    xpp = _wrapped_character_xpp()
    index_payload = struct.pack(">6H", 0, 1, 2, 2, 3, 0)
    capture = tmp_path / "frame.rrc"
    capture.write_bytes(gzip.decompress(_rrc(index_payload)))
    report = build_rrc_character_match_report(xpp, "target.xpp", capture)
    assert report["capture"]["compression"] == "none"
    assert report["exact_match_count"] == 1


def test_capture_report_records_same_size_negative_without_promoting_it(tmp_path):
    xpp = _wrapped_character_xpp()
    capture = tmp_path / "unrelated.rrc.gz"
    capture.write_bytes(_rrc(bytes(12)))
    report = build_rrc_character_match_report(xpp, "target.xpp", capture)
    assert report["captured_payloads_at_target_sizes"] == {"12": 1}
    assert report["exact_match_count"] == 0
    assert report["match_status"] == "no-exact-index-match"


def test_capture_report_separates_byte_swapped_candidate_from_exact_match(tmp_path):
    xpp = _wrapped_character_xpp()
    capture = tmp_path / "byte-swapped.rrc.gz"
    capture.write_bytes(_rrc(struct.pack("<6H", 0, 1, 2, 2, 3, 0)))
    report = build_rrc_character_match_report(xpp, "target.xpp", capture)
    assert report["exact_match_count"] == 0
    assert report["bounded_transform_match_count"] == 1
    assert report["bounded_transform_matches"][0]["transform"] == (
        "little-endian-u16-exact"
    )
    assert report["decoded_vertex_semantics_proved"] is False


def test_capture_report_rejects_absent_replay_memory_block(tmp_path):
    capture = tmp_path / "broken.rrc.gz"
    capture.write_bytes(
        _rrc(
            struct.pack(">6H", 0, 1, 2, 2, 3, 0),
            missing_block_reference=True,
        )
    )
    with pytest.raises(RrcCaptureError, match="absent memory block"):
        build_rrc_character_match_report(
            _wrapped_character_xpp(), "target.xpp", capture
        )
