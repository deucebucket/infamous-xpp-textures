import struct

import pytest

from infamous_xpp_textures.character import (
    CharacterReportError,
    build_character_compatibility_report,
    build_nif_report,
    build_xpp_character_report,
    find_edge_geometry_envelopes,
    find_skinned_geometry_contracts,
    unpack_packed_components_msb,
)
from infamous_xpp_textures.xpp import Chunk, XppFile


def _character_payload() -> tuple[bytes, XppFile]:
    data = bytearray(0x900)
    parsed = XppFile(
        version=8,
        header_size=0x70,
        data_offset=0,
        data_size=len(data),
        segment_count=0,
        chunk_count=2,
        fixup_count=0,
        segments=(),
        chunks=(
            Chunk(0x01100000, 0x180, 0, 0),
            Chunk(0x0B800000, 0x400, 0x400, 0),
        ),
        fixups=(),
    )
    record = 0x20
    struct.pack_into(
        ">15I",
        data,
        record,
        0x400,
        0x400,
        0x480,
        0x480,
        0x500,
        0x500,
        0x580,
        0x580,
        0x00020000,
        0x000C0000,
        0x600,
        0x600,
        0x00110022,
        0x00330044,
        0x0005000C,
    )
    struct.pack_into(">6H", data, 0x600, 0, 1, 2, 2, 3, 0)
    struct.pack_into(
        ">12I",
        data,
        record + 0x58,
        4,
        1,
        0,
        0,
        0x00680000,
        0,
        0x01430020,
        0x01010100,
        0x02430038,
        0x01010100,
        0x03430050,
        0x01010100,
    )
    data[0x480:0x482] = bytes((0x05, 0x70))
    return bytes(data), parsed


def _wrapped_character_xpp() -> bytes:
    payload, _ = _character_payload()
    segment_count = 1
    chunk_count = 2
    data_offset = 0x88 + segment_count * 28 + chunk_count * 16
    result = bytearray(data_offset + len(payload))
    result[:4] = b"PACK"
    struct.pack_into(">HH", result, 4, 8, 0x70)
    words = [0] * 10
    words[4] = 0x70
    words[5] = data_offset - 0x70
    words[8] = data_offset
    words[9] = len(payload)
    struct.pack_into(">10I", result, 8, *words)
    struct.pack_into(">QQQ", result, 0x70, segment_count, chunk_count, 0)
    struct.pack_into(">7I", result, 0x88, 0, len(payload), 0, 0, 0, 0, chunk_count)
    chunk_table = 0x88 + 28
    struct.pack_into(">4I", result, chunk_table, 0x01100000, 0x180, 0, 0)
    struct.pack_into(">4I", result, chunk_table + 16, 0x0B800000, 0x400, 0x400, 0)
    result[data_offset:] = payload
    return bytes(result)


def _export_string(value: str = "") -> bytes:
    raw = value.encode() + b"\0"
    return bytes((len(raw),)) + raw


def _sized_string(value: str) -> bytes:
    raw = value.encode()
    return struct.pack("<I", len(raw)) + raw


def _rigged_nif(*, mismatch_bone_data: bool = False) -> bytes:
    block_types = [
        "NiNode",
        "BSSubIndexTriShape",
        "BSSkin::Instance",
        "BSSkin::BoneData",
    ]
    type_indices = (0, 0, 1, 2, 3)
    blocks = [
        struct.pack("<I", 0),
        struct.pack("<I", 1),
        b"shape",
        struct.pack("<iiIiI", 0, 4, 1, 1, 0),
        struct.pack("<I", 2 if mismatch_bone_data else 1) + bytes(68),
    ]
    strings = ["Root", "Bone", "Materials\\Actors\\Example.bgsm"]
    result = bytearray(b"Gamebryo File Format, Version 20.2.0.7\n")
    result.extend(struct.pack("<IBII", 0x14020007, 1, 12, len(blocks)))
    result.extend(struct.pack("<I", 155))
    result.extend(_export_string())
    result.extend(struct.pack("<I", 0))
    result.extend(_export_string())
    result.extend(_export_string())
    result.extend(struct.pack("<H", len(block_types)))
    for value in block_types:
        result.extend(_sized_string(value))
    result.extend(struct.pack(f"<{len(type_indices)}H", *type_indices))
    result.extend(struct.pack(f"<{len(blocks)}I", *(len(item) for item in blocks)))
    result.extend(struct.pack("<II", len(strings), max(map(len, strings))))
    for value in strings:
        result.extend(_sized_string(value))
    result.extend(struct.pack("<I", 0))
    for block in blocks:
        result.extend(block)
    result.extend(struct.pack("<Ii", 1, 0))
    return bytes(result)


def test_proves_skinned_triangle_and_packed_stream_contracts():
    data, parsed = _character_payload()
    envelopes = find_edge_geometry_envelopes(data, parsed)
    contracts = find_skinned_geometry_contracts(data, parsed)
    assert len(envelopes) == len(contracts) == 1
    assert contracts[0].triangle_count == 2
    assert contracts[0].vertex_count == 4
    assert contracts[0].index_count == 6
    assert len(contracts[0].packed_vertex_streams) == 3
    assert all(stream.bit_order == "msb-first" for stream in contracts[0].packed_vertex_streams)


def test_msb_unpacker_rejects_nonzero_tail_padding():
    assert unpack_packed_components_msb(
        bytes((0x05, 0x70)), (1, 1, 1, 0), 4
    ) == ((0, 0, 0, 0), (0, 0, 1, 0), (0, 1, 0, 0), (1, 1, 1, 0))
    with pytest.raises(CharacterReportError, match="tail padding"):
        unpack_packed_components_msb(bytes((0x05, 0x71)), (1, 1, 1, 0), 4)


def test_xpp_character_report_withholds_unproved_semantics():
    report = build_xpp_character_report(_wrapped_character_xpp(), "character.xpp")
    assert report["contract_coverage"] == "1/1"
    assert report["topology_proved"] is True
    assert report["triangle_count"] == 2
    assert report["descriptor_local_vertex_count"] == 4
    assert report["gates"]["skin_weights"] is False
    assert report["export_authorized"] is False
    assert report["injection_authorized"] is False


def test_nif_report_closes_skin_and_bone_ownership():
    report = build_nif_report(_rigged_nif(), "owned.nif")
    assert report["recognized_game"] == "Fallout 76"
    assert report["block_count"] == 5
    assert report["shape_count"] == 1
    assert report["skin_instance_count"] == 1
    assert report["skin_bindings"][0]["bone_count"] == 1
    assert report["unique_skin_bone_node_count"] == 1
    assert report["material_reference_count"] == 1
    assert report["rigged_source_proved"] is True


def test_nif_report_rejects_bone_data_count_drift():
    with pytest.raises(CharacterReportError, match="bone-data count"):
        build_nif_report(_rigged_nif(mismatch_bone_data=True), "broken.nif")


def test_compatibility_report_refuses_conversion_despite_valid_sources():
    report = build_character_compatibility_report(
        _wrapped_character_xpp(), "target.xpp", _rigged_nif(), "source.nif"
    )
    assert report["target"]["topology_proved"] is True
    assert report["external"]["rigged_source_proved"] is True
    assert report["conversion_status"] == "blocked-unproved-target-semantics"
    assert report["injection_authorized"] is False
    assert "external-bone to target-joint" in report["blockers"][-1]
