import hashlib
import json
import math
import struct

import pytest

from infamous_xpp_textures.character import build_xpp_character_report
from infamous_xpp_textures.character_export import (
    CharacterDiagnosticExportError,
    export_character_diagnostic_glb,
)
from infamous_xpp_textures.cli import main


def _character_xpp() -> bytes:
    payload = bytearray(0x900)
    record = 0x20
    struct.pack_into(
        ">15I",
        payload,
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
    struct.pack_into(">6H", payload, 0x600, 0, 1, 2, 2, 3, 0)
    struct.pack_into(
        ">12I",
        payload,
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
    payload[0x480:0x482] = bytes((0x05, 0x70))
    data_offset = 0x88 + 28 + 2 * 16
    result = bytearray(data_offset + len(payload))
    result[:4] = b"PACK"
    struct.pack_into(">HH", result, 4, 8, 0x70)
    words = [0] * 10
    words[4] = 0x70
    words[5] = data_offset - 0x70
    words[8] = data_offset
    words[9] = len(payload)
    struct.pack_into(">10I", result, 8, *words)
    struct.pack_into(">QQQ", result, 0x70, 1, 2, 0)
    struct.pack_into(">7I", result, 0x88, 0, len(payload), 0, 0, 0, 0, 2)
    chunk_table = 0x88 + 28
    struct.pack_into(">4I", result, chunk_table, 0x01100000, 0x180, 0, 0)
    struct.pack_into(">4I", result, chunk_table + 16, 0x0B800000, 0x400, 0x400, 0)
    result[data_offset:] = payload
    return bytes(result)


def _payload(positions=None) -> bytes:
    if positions is None:
        positions = ((10, 20, 30), (11, 20, 30), (10, 21, 30), (10, 20, 31))
    body = b"".join(struct.pack(">3f", *value) for value in positions)
    return body + bytes(12)


def _binding_report(xpp: bytes, payload: bytes) -> dict:
    contract = build_xpp_character_report(xpp, "synthetic.xpp")["contracts"][0]
    block = {
        "block_key": 1,
        "payload_size": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "role": "unclassified-draw-sibling",
    }
    exact = {
        "record_offset": contract["record_offset"],
        "vertex_count": contract["vertex_count"],
        "index_count": contract["index_count"],
        "index_min": 0,
        "index_max": contract["vertex_count"] - 1,
        "index_sha256": contract["index_sha256"],
    }
    return {
        "draw_binding_count": 1,
        "exact_matches": [exact],
        "draw_bindings": [
            {
                "rsx_draw_state": {
                    "status": "complete-vertex-binding",
                    "rsx_vertex_binding_proved": True,
                    "vertex_arrays": [
                        {
                            "attribute": 0,
                            "binding_proved": True,
                            "type_raw": 2,
                            "type_name": "float32",
                            "component_count": 3,
                            "stride": 12,
                            "frequency": 0,
                            "index_span": 4,
                            "expected_capture_size": len(payload),
                            "matching_memory_blocks": [block],
                        }
                    ],
                }
            }
        ],
    }


def _glb_document(data: bytes) -> dict:
    magic, version, total = struct.unpack_from("<III", data)
    assert (magic, version, total) == (0x46546C67, 2, len(data))
    json_size, json_type = struct.unpack_from("<II", data, 12)
    assert json_type == 0x4E4F534A
    return json.loads(data[20 : 20 + json_size])


def test_exports_exact_topology_as_explicit_diagnostic_hypothesis(tmp_path):
    xpp = _character_xpp()
    payload = _payload()
    output = tmp_path / "diagnostic.glb"
    report = export_character_diagnostic_glb(
        xpp,
        _binding_report(xpp, payload),
        payload,
        output,
        position_hypothesis_attribute=0,
    )
    assert report["vertices"] == 4
    assert report["triangles"] == 2
    assert report["nondegenerate_triangles"] == 2
    assert report["gates"]["topology"] is True
    assert report["gates"]["position_semantic"] is False
    assert report["gates"]["rigged_export"] is False
    assert report["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    document = _glb_document(output.read_bytes())
    evidence = document["asset"]["extras"]["infamousDiagnostic"]
    assert evidence["diagnosticOnly"] is True
    assert evidence["positionSemanticProved"] is False
    assert evidence["positionHypothesisAttribute"] == 0
    assert "skins" not in document
    assert set(document["meshes"][0]["primitives"][0]["attributes"]) == {"POSITION"}

    second_output = tmp_path / "diagnostic-second.glb"
    second_report = export_character_diagnostic_glb(
        xpp,
        _binding_report(xpp, payload),
        payload,
        second_output,
        position_hypothesis_attribute=0,
    )
    assert second_output.read_bytes() == output.read_bytes()
    assert second_report == report


def test_rejects_unbound_position_hypothesis_attribute(tmp_path):
    xpp = _character_xpp()
    payload = _payload()
    with pytest.raises(
        CharacterDiagnosticExportError,
        match="selected position-hypothesis attribute",
    ):
        export_character_diagnostic_glb(
            xpp,
            _binding_report(xpp, payload),
            payload,
            tmp_path / "bad.glb",
            position_hypothesis_attribute=1,
        )


def test_rejects_payload_hash_mismatch(tmp_path):
    xpp = _character_xpp()
    payload = _payload()
    with pytest.raises(CharacterDiagnosticExportError, match="SHA-256"):
        export_character_diagnostic_glb(
            xpp,
            _binding_report(xpp, payload),
            payload[:-1] + b"x",
            tmp_path / "bad.glb",
            position_hypothesis_attribute=0,
        )


def test_rejects_ambiguous_draw_report(tmp_path):
    xpp = _character_xpp()
    payload = _payload()
    report = _binding_report(xpp, payload)
    report["draw_binding_count"] = 2
    with pytest.raises(CharacterDiagnosticExportError, match="exactly one draw"):
        export_character_diagnostic_glb(
            xpp, report, payload, tmp_path / "bad.glb", position_hypothesis_attribute=0
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (("type_name", "float16"), ("component_count", 2), ("frequency", 1), ("stride", 8)),
)
def test_rejects_unusable_position_hypothesis_format(tmp_path, field, value):
    xpp = _character_xpp()
    payload = _payload()
    report = _binding_report(xpp, payload)
    report["draw_bindings"][0]["rsx_draw_state"]["vertex_arrays"][0][field] = value
    with pytest.raises(CharacterDiagnosticExportError, match="float32x3"):
        export_character_diagnostic_glb(
            xpp, report, payload, tmp_path / "bad.glb", position_hypothesis_attribute=0
        )


def test_rejects_nonfinite_position_hypothesis(tmp_path):
    xpp = _character_xpp()
    payload = _payload(((math.nan, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)))
    with pytest.raises(CharacterDiagnosticExportError, match="not finite"):
        export_character_diagnostic_glb(
            xpp,
            _binding_report(xpp, payload),
            payload,
            tmp_path / "bad.glb",
            position_hypothesis_attribute=0,
        )


def test_rejects_fully_degenerate_position_hypothesis(tmp_path):
    xpp = _character_xpp()
    payload = _payload(((0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)))
    with pytest.raises(CharacterDiagnosticExportError, match="every triangle degenerate"):
        export_character_diagnostic_glb(
            xpp,
            _binding_report(xpp, payload),
            payload,
            tmp_path / "bad.glb",
            position_hypothesis_attribute=0,
        )


def test_rejects_topology_count_drift(tmp_path):
    xpp = _character_xpp()
    payload = _payload()
    report = _binding_report(xpp, payload)
    report["exact_matches"][0]["vertex_count"] = 5
    with pytest.raises(CharacterDiagnosticExportError, match="counts do not reconcile"):
        export_character_diagnostic_glb(
            xpp, report, payload, tmp_path / "bad.glb", position_hypothesis_attribute=0
        )


def test_cli_refuses_to_overwrite_character_input(tmp_path, capsys):
    xpp = _character_xpp()
    payload = _payload()
    xpp_path = tmp_path / "character.xpp"
    binding_path = tmp_path / "binding.json"
    payload_path = tmp_path / "attribute.bin"
    xpp_path.write_bytes(xpp)
    binding_path.write_text(json.dumps(_binding_report(xpp, payload)), encoding="utf-8")
    payload_path.write_bytes(payload)

    result = main(
        [
            "character-diagnostic-export",
            "--xpp",
            str(xpp_path),
            "--binding-report",
            str(binding_path),
            "--attribute-payload",
            str(payload_path),
            "--position-hypothesis-attribute",
            "0",
            "--output",
            str(xpp_path),
        ]
    )

    assert result == 1
    assert xpp_path.read_bytes() == xpp
    assert "must not overwrite an input" in capsys.readouterr().err
