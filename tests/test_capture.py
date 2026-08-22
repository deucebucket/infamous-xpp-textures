import gzip
import struct

import pytest

from infamous_xpp_textures.capture import (
    RRC_MAGIC,
    RRC_VERSION,
    RrcCaptureError,
    build_rrc_character_match_report,
    summarize_rsx_vertex_payload_numeric,
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


def _rrc(
    index_payload: bytes,
    *,
    missing_block_reference: bool = False,
    sibling_payload: bytes | None = None,
    sibling_location: int = 1,
    described_draw: bool = False,
) -> bytes:
    block_key = 0x1111222233334444
    data_state = 0xAAAABBBBCCCCDDDD
    blocks = [(block_key, 0x123400, 0, data_state, index_payload)]
    if sibling_payload is not None:
        blocks.append(
            (
                block_key + 1,
                0x123500,
                sibling_location,
                data_state + 1,
                sibling_payload,
            )
        )
    result = bytearray(struct.pack("<III", RRC_MAGIC, RRC_VERSION, 1))
    result.extend(_vle(0))
    result.extend(_vle(len(blocks)))
    for key, offset, location, state, _payload in blocks:
        result.extend(struct.pack("<QIIQ", key, offset, location, state))
    result.extend(_vle(len(blocks)))
    for _key, _offset, _location, state, payload in blocks:
        result.extend(struct.pack("<Q", state))
        result.extend(_vle(len(payload)))
        result.extend(payload)
    result.extend(_vle(0))
    state_keys = [block[0] for block in blocks]
    commands = (
        [
            (0x1680, 0x00123500, []),
            (0x1738, 0, []),
            (0x173C, 0, []),
            (0x1740, 0x00000C32, []),
            (0x181C, 0x00123400, []),
            (0x1820, 0x10, []),
            (0x1808, 5, []),
            (0x1824, 0x05000000, []),
            (0x1808, 0, state_keys),
        ]
        if described_draw
        else [(0x1808, 0, state_keys)]
    )
    result.extend(_vle(len(commands)))
    for command_index, (method_offset, value, command_states) in enumerate(commands):
        result.extend(struct.pack("<II", 0x00040000 | method_offset, value))
        result.extend(_vle(len(command_states)))
        for state_index, key in enumerate(command_states):
            if (
                command_index == len(commands) - 1
                and state_index == 0
                and missing_block_reference
            ):
                key += len(blocks) + 1
            result.extend(struct.pack("<Q", key))
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
    assert report["match_status"] == "exact-index-draw-binding"
    assert report["live_draw_binding_proved"] is True
    assert report["draw_binding_count"] == 1
    assert report["draw_bindings"][0]["command_index"] == 0
    assert report["draw_bindings"][0]["method_offset"] == 0x1808
    assert report["draw_bindings"][0]["draw_end_boundary"] is True
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


def test_capture_report_bounds_unclassified_draw_siblings(tmp_path):
    xpp = _wrapped_character_xpp()
    index_payload = struct.pack(">6H", 0, 1, 2, 2, 3, 0)
    sibling_payload = b"decoded-vertex-candidate"
    capture = tmp_path / "draw.rrc.gz"
    capture.write_bytes(_rrc(index_payload, sibling_payload=sibling_payload))
    report = build_rrc_character_match_report(xpp, "target.xpp", capture)
    binding = report["draw_bindings"][0]
    assert binding["unclassified_sibling_count"] == 1
    assert [item["role"] for item in binding["memory_blocks"]] == [
        "exact-index",
        "unclassified-draw-sibling",
    ]
    sibling = binding["memory_blocks"][1]
    assert sibling["location"] == 1
    assert sibling["offset"] == 0x123500
    assert sibling["payload_size"] == len(sibling_payload)
    assert "payload" not in sibling


def test_capture_report_decodes_and_binds_rsx_vertex_array(tmp_path):
    xpp = _wrapped_character_xpp()
    index_payload = struct.pack(">6H", 0, 1, 2, 2, 3, 0)
    sibling_payload = bytes(60)
    capture = tmp_path / "described-draw.rrc.gz"
    capture.write_bytes(
        _rrc(
            index_payload,
            sibling_payload=sibling_payload,
            sibling_location=0,
            described_draw=True,
        )
    )
    report = build_rrc_character_match_report(xpp, "target.xpp", capture)
    assert report["rsx_vertex_binding_proved"] is True
    binding = report["draw_bindings"][0]
    assert binding["command_index"] == 8
    state = binding["rsx_draw_state"]
    assert state["status"] == "complete-vertex-binding"
    assert state["primitive_value"] == 5
    assert state["indexed_ranges"] == [
        {"start": 0, "count": 6, "observed_at_command": 7}
    ]
    assert state["total_index_count"] == 6
    assert state["index_array"] == {
        "offset": 0x123400,
        "location": 0,
        "type_raw": 1,
        "type_name": "u16",
        "element_byte_count": 2,
        "binding_proved": True,
    }
    assert state["index_min"] == 0
    assert state["index_max"] == 3
    assert state["index_span"] == 4
    assert state["active_vertex_attribute_count"] == 1
    attribute = state["vertex_arrays"][0]
    assert attribute["attribute"] == 0
    assert attribute["semantic"] is None
    assert attribute["type_name"] == "float32"
    assert attribute["component_count"] == 3
    assert attribute["stride"] == 12
    assert attribute["expected_capture_size"] == 60
    assert attribute["binding_proved"] is True
    assert attribute["matching_memory_blocks"][0]["payload_size"] == 60
    assert attribute["numeric_decode"]["status"] == "exact-byte-round-trip"
    assert attribute["numeric_decode"]["byte_order"] == "big-endian"
    assert attribute["numeric_decode"]["element_count"] == 4
    assert attribute["numeric_decode"]["component_minimum"] == [0.0, 0.0, 0.0]
    assert attribute["numeric_decode"]["component_maximum"] == [0.0, 0.0, 0.0]
    assert attribute["numeric_decode"]["source_sha256"] == attribute["numeric_decode"][
        "reencoded_sha256"
    ]
    assert report["numeric_round_trip_attribute_count"] == 1
    assert report["unsupported_numeric_attribute_count"] == 0
    assert report["partial_numeric_round_trip_proved"] is True
    assert report["complete_numeric_round_trip_proved"] is True
    assert report["export_authorized"] is False
    assert report["injection_authorized"] is False
    assert state["decoded_vertex_semantics_proved"] is False


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


def _numeric_attribute(
    type_raw: int,
    type_name: str,
    component_count: int,
    stride: int,
    element_byte_count: int,
    index_span: int,
) -> dict:
    return {
        "type_raw": type_raw,
        "type_name": type_name,
        "component_count": component_count,
        "stride": stride,
        "element_byte_count": element_byte_count,
        "index_span": index_span,
        "expected_capture_size": stride * index_span + element_byte_count,
    }


def test_numeric_vertex_decoder_round_trips_half_and_unorm_padding():
    half_attribute = _numeric_attribute(3, "float16", 3, 8, 8, 2)
    half_payload = (
        struct.pack(">4e", 1.0, -2.0, 0.5, 7.0)
        + struct.pack(">4e", 3.0, 4.0, -1.0, 8.0)
        + bytes(8)
    )
    half = summarize_rsx_vertex_payload_numeric(half_attribute, half_payload)
    assert half["component_minimum"] == [1.0, -2.0, -1.0]
    assert half["component_maximum"] == [3.0, 4.0, 0.5]
    assert half["source_sha256"] == half["reencoded_sha256"]

    unorm_attribute = _numeric_attribute(4, "unorm8", 4, 4, 4, 2)
    unorm_payload = bytes((0, 64, 128, 255, 255, 128, 64, 0)) + bytes(4)
    unorm = summarize_rsx_vertex_payload_numeric(unorm_attribute, unorm_payload)
    assert unorm["component_minimum"] == [0.0, 64 / 255, 64 / 255, 0.0]
    assert unorm["component_maximum"] == [1.0, 128 / 255, 128 / 255, 1.0]
    assert unorm["exact_byte_round_trip"] is True


def test_numeric_vertex_decoder_fails_closed_for_cmp32_and_nonfinite_float():
    cmp_attribute = _numeric_attribute(6, "cmp32", 1, 4, 4, 2)
    unsupported = summarize_rsx_vertex_payload_numeric(cmp_attribute, b"")
    assert unsupported["status"] == "unsupported-format"
    assert unsupported["exact_byte_round_trip"] is False

    float_attribute = _numeric_attribute(2, "float32", 3, 12, 12, 1)
    float_payload = struct.pack(">3f", 0.0, float("nan"), 1.0) + bytes(12)
    with pytest.raises(RrcCaptureError, match="non-finite"):
        summarize_rsx_vertex_payload_numeric(float_attribute, float_payload)
