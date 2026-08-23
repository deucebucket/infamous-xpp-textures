import hashlib
import json
import math
import struct

import pytest

import infamous_xpp_textures.character_source_export as character_source_export
import infamous_xpp_textures.character_source_correlation as character_source_correlation
from infamous_xpp_textures.character import (
    build_xpp_character_report,
    find_skinned_geometry_contracts,
)
from infamous_xpp_textures.character_export import (
    CharacterDiagnosticExportError,
    export_character_diagnostic_glb,
)
from infamous_xpp_textures.character_source_export import (
    CharacterSourceExportError,
    export_character_source_diagnostic_glb,
)
from infamous_xpp_textures.character_source_correlation import (
    CharacterSourceCorrelationError,
    _similarity_fit,
    correlate_character_source_runtime,
    write_new_correlation_report,
)
from infamous_xpp_textures.cli import main
from infamous_xpp_textures.xpp import parse_xpp


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
    for offset in (0xA8, 0xC0, 0xD8):
        struct.pack_into(">6f", payload, offset, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
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


def _correlation_inputs():
    xpp = _character_xpp()
    parsed = parse_xpp(xpp, len(xpp))
    contract = find_skinned_geometry_contracts(xpp, parsed)[0]
    start = parsed.data_offset + contract.index_offset
    index = xpp[start : start + contract.index_byte_count]
    positions = _payload()[: contract.vertex_count * 12]
    return xpp, index, positions


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
    ("mutation", "message"),
    (
        (lambda report: report.update(draw_binding_count=True), "draw binding count"),
        (lambda report: report.update(exact_matches={}), "exact matches"),
        (
            lambda report: report["draw_bindings"][0]["rsx_draw_state"].update(
                vertex_arrays={}
            ),
            "vertex arrays",
        ),
    ),
)
def test_rejects_malformed_binding_schema(tmp_path, mutation, message):
    xpp = _character_xpp()
    payload = _payload()
    report = _binding_report(xpp, payload)
    mutation(report)
    with pytest.raises(CharacterDiagnosticExportError, match=message):
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
    with pytest.raises(
        CharacterDiagnosticExportError, match="every triangle degenerate"
    ):
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


def test_exports_exact_packed_source_topology_as_diagnostic(tmp_path):
    xpp = _character_xpp()
    output = tmp_path / "source.glb"
    report = export_character_source_diagnostic_glb(
        xpp,
        output,
        record_offset=0x20,
        stream_index=1,
        numeric_family="endpoint-unsigned",
    )
    assert report["vertices"] == 4
    assert report["triangles"] == 2
    assert report["nondegenerate_triangles"] == 2
    assert report["stream_index"] == 1
    assert report["numeric_family"] == "endpoint-unsigned"
    assert report["gates"]["topology"] is True
    assert report["gates"]["numeric_family"] is False
    assert report["gates"]["position_semantic"] is False
    assert report["gates"]["rigged_export"] is False
    assert report["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    document = _glb_document(output.read_bytes())
    evidence = document["asset"]["extras"]["infamousDiagnostic"]
    assert evidence["topologyProved"] is True
    assert evidence["packedStreamIdentityProved"] is True
    assert evidence["numericFamily"] == "endpoint-unsigned-hypothesis"
    assert evidence["positionSemanticProved"] is False
    assert "skins" not in document

    second = tmp_path / "source-second.glb"
    second_report = export_character_source_diagnostic_glb(
        xpp,
        second,
        record_offset=0x20,
        stream_index=1,
        numeric_family="endpoint-unsigned",
    )
    assert second.read_bytes() == output.read_bytes()
    assert second_report == report


def test_packed_source_export_refuses_existing_output(tmp_path):
    xpp = _character_xpp()
    output = tmp_path / "existing.glb"
    output.write_bytes(b"preserve")
    with pytest.raises(CharacterSourceExportError, match="refusing to overwrite"):
        export_character_source_diagnostic_glb(
            xpp,
            output,
            record_offset=0x20,
            stream_index=1,
            numeric_family="endpoint-unsigned",
        )
    assert output.read_bytes() == b"preserve"


def test_packed_source_export_refuses_publication_race(tmp_path, monkeypatch):
    xpp = _character_xpp()
    output = tmp_path / "raced.glb"
    real_link = character_source_export.os.link

    def race_link(source, destination):
        destination.write_bytes(b"racer")
        real_link(source, destination)

    monkeypatch.setattr(character_source_export.os, "link", race_link)
    with pytest.raises(FileExistsError):
        export_character_source_diagnostic_glb(
            xpp,
            output,
            record_offset=0x20,
            stream_index=1,
            numeric_family="endpoint-unsigned",
        )
    assert output.read_bytes() == b"racer"
    assert list(tmp_path.iterdir()) == [output]


def test_packed_source_export_refuses_unknown_record_and_family(tmp_path):
    xpp = _character_xpp()
    with pytest.raises(CharacterSourceExportError, match="selects 0"):
        export_character_source_diagnostic_glb(
            xpp,
            tmp_path / "unknown-record.glb",
            record_offset=0x24,
            stream_index=1,
            numeric_family="endpoint-unsigned",
        )
    with pytest.raises(CharacterSourceExportError, match="unsupported numeric family"):
        export_character_source_diagnostic_glb(
            xpp,
            tmp_path / "unknown-family.glb",
            record_offset=0x20,
            stream_index=1,
            numeric_family="guess",
        )


def test_packed_source_export_enforces_input_bound(tmp_path, monkeypatch):
    xpp = _character_xpp()
    monkeypatch.setattr(character_source_export, "MAX_XPP_SOURCE_BYTES", len(xpp) - 1)
    with pytest.raises(CharacterSourceExportError, match="64 MiB bound"):
        export_character_source_diagnostic_glb(
            xpp,
            tmp_path / "too-large.glb",
            record_offset=0x20,
            stream_index=1,
            numeric_family="endpoint-unsigned",
        )


def test_packed_source_cli_writes_new_report_and_refuses_alias(tmp_path, capsys):
    xpp_path = tmp_path / "character.xpp"
    xpp_path.write_bytes(_character_xpp())
    output = tmp_path / "source.glb"
    report = tmp_path / "source.json"
    result = main(
        [
            "character-source-diagnostic-export",
            "--xpp",
            str(xpp_path),
            "--record-offset",
            "0x20",
            "--stream-index",
            "1",
            "--numeric-family",
            "endpoint-unsigned",
            "--output",
            str(output),
            "--json-out",
            str(report),
        ]
    )
    assert result == 0
    assert output.is_file()
    assert (
        json.loads(report.read_text(encoding="utf-8"))["gates"]["position_semantic"]
        is False
    )
    assert "diagnostic-glb-written" in capsys.readouterr().out

    alias = tmp_path / "same-output"
    result = main(
        [
            "character-source-diagnostic-export",
            "--xpp",
            str(xpp_path),
            "--record-offset",
            "0x20",
            "--stream-index",
            "1",
            "--numeric-family",
            "endpoint-unsigned",
            "--output",
            str(alias),
            "--json-out",
            str(alias),
        ]
    )
    assert result == 1
    assert "must be different paths" in capsys.readouterr().err


def test_correlates_exact_source_and_runtime_without_assigning_semantics():
    xpp, index, positions = _correlation_inputs()
    report = correlate_character_source_runtime(
        xpp,
        index,
        positions,
        record_offset=0x20,
        runtime_index_sha256=hashlib.sha256(index).hexdigest(),
        runtime_positions_sha256=hashlib.sha256(positions).hexdigest(),
        runtime_byte_order="big",
    )
    assert report["topology_pair_proved"] is True
    assert report["runtime_array_identity_proved"] is True
    assert report["payload_values_serialized"] is False
    assert 1 in report["stream_ranking"]
    stream = next(item for item in report["streams"] if item["stream_index"] == 1)
    assert stream["status"] == "fit-complete"
    assert stream["representative_fit"]["source_rank"] == 3
    assert math.isclose(stream["representative_fit"]["r_squared"], 1.0)
    assert stream["proper_similarity_family_ranking"]
    assert stream["mirrored_similarity_family_ranking"]
    assert stream["top_proper_similarity_families"]
    assert all(
        result["transform_coefficients_serialized"] is False
        for result in stream["proper_similarity_families"].values()
        if result["status"] == "fit-complete"
    )
    assert report["gates"]["numeric_family_selected"] is False
    assert report["gates"]["position_semantic"] is False
    assert report["gates"]["complete_character"] is False


def test_similarity_fit_separates_proper_and_mirrored_orientation_classes():
    source = (
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (0.0, 3.0, 0.0),
        (0.0, 0.0, 5.0),
        (1.0, 2.0, 4.0),
    )

    def transformed(row, *, mirrored):
        x_axis = -row[0] if mirrored else row[0]
        rotated = (-row[1], x_axis, row[2])
        return tuple(2.5 * value + offset for value, offset in zip(rotated, (7, -4, 9)))

    proper_runtime = tuple(transformed(row, mirrored=False) for row in source)
    mirrored_runtime = tuple(transformed(row, mirrored=True) for row in source)
    proper = _similarity_fit(source, proper_runtime, mirrored=False)
    wrong_mirrored = _similarity_fit(source, proper_runtime, mirrored=True)
    mirrored = _similarity_fit(source, mirrored_runtime, mirrored=True)
    wrong_proper = _similarity_fit(source, mirrored_runtime, mirrored=False)
    assert math.isclose(proper["r_squared"], 1.0, abs_tol=1e-12)
    assert math.isclose(mirrored["r_squared"], 1.0, abs_tol=1e-12)
    assert proper["orientation"] == "proper"
    assert mirrored["orientation"] == "mirrored"
    assert wrong_mirrored["r_squared"] < 0.99
    assert wrong_proper["r_squared"] < 0.99


def test_source_runtime_correlation_rejects_hash_shape_and_topology_mismatch():
    xpp, index, positions = _correlation_inputs()
    kwargs = {
        "record_offset": 0x20,
        "runtime_index_sha256": hashlib.sha256(index).hexdigest(),
        "runtime_positions_sha256": hashlib.sha256(positions).hexdigest(),
        "runtime_byte_order": "big",
    }
    with pytest.raises(CharacterSourceCorrelationError, match="positions SHA-256"):
        correlate_character_source_runtime(
            xpp, index, positions, **{**kwargs, "runtime_positions_sha256": "0" * 64}
        )
    with pytest.raises(CharacterSourceCorrelationError, match="whole contiguous rows"):
        correlate_character_source_runtime(
            xpp,
            index,
            positions[:-4],
            **{
                **kwargs,
                "runtime_positions_sha256": hashlib.sha256(positions[:-4]).hexdigest(),
            },
        )
    changed_index = bytes((index[0] ^ 1,)) + index[1:]
    with pytest.raises(CharacterSourceCorrelationError, match="runtime index SHA-256"):
        correlate_character_source_runtime(xpp, changed_index, positions, **kwargs)


def test_source_runtime_correlation_records_an_explicit_row_window():
    xpp, index, positions = _correlation_inputs()
    prefixed = struct.pack(">3f", -20.0, -30.0, -40.0) + positions
    report = correlate_character_source_runtime(
        xpp,
        index,
        prefixed,
        record_offset=0x20,
        runtime_index_sha256=hashlib.sha256(index).hexdigest(),
        runtime_positions_sha256=hashlib.sha256(prefixed).hexdigest(),
        runtime_byte_order="big",
        runtime_first_row=1,
    )
    assert report["runtime_total_row_count"] == 5
    assert report["runtime_selected_first_row"] == 1
    assert report["runtime_selected_row_count"] == 4
    stream = next(item for item in report["streams"] if item["stream_index"] == 1)
    assert math.isclose(stream["representative_fit"]["r_squared"], 1.0)

    with pytest.raises(CharacterSourceCorrelationError, match="does not cover"):
        correlate_character_source_runtime(
            xpp,
            index,
            prefixed,
            record_offset=0x20,
            runtime_index_sha256=hashlib.sha256(index).hexdigest(),
            runtime_positions_sha256=hashlib.sha256(prefixed).hexdigest(),
            runtime_byte_order="big",
            runtime_first_row=2,
        )


def test_source_runtime_correlation_publication_race_preserves_racer(
    tmp_path, monkeypatch
):
    xpp, index, positions = _correlation_inputs()
    report = correlate_character_source_runtime(
        xpp,
        index,
        positions,
        record_offset=0x20,
        runtime_index_sha256=hashlib.sha256(index).hexdigest(),
        runtime_positions_sha256=hashlib.sha256(positions).hexdigest(),
        runtime_byte_order="big",
    )
    output = tmp_path / "correlation.json"
    real_link = character_source_correlation.os.link

    def racing_link(source, destination):
        output.write_bytes(b"racer")
        return real_link(source, destination)

    monkeypatch.setattr(character_source_correlation.os, "link", racing_link)
    with pytest.raises(FileExistsError):
        write_new_correlation_report(output, report)
    assert output.read_bytes() == b"racer"


def test_source_runtime_correlation_cli_is_deterministic_and_refuses_overwrite(
    tmp_path, capsys
):
    xpp, index, positions = _correlation_inputs()
    xpp_path = tmp_path / "character.xpp"
    index_path = tmp_path / "runtime-index.bin"
    positions_path = tmp_path / "runtime-positions.bin"
    xpp_path.write_bytes(xpp)
    index_path.write_bytes(index)
    positions_path.write_bytes(positions)

    def arguments(output):
        return [
            "character-source-runtime-correlate",
            "--xpp",
            str(xpp_path),
            "--record-offset",
            "0x20",
            "--runtime-index",
            str(index_path),
            "--runtime-index-sha256",
            hashlib.sha256(index).hexdigest(),
            "--runtime-positions",
            str(positions_path),
            "--runtime-positions-sha256",
            hashlib.sha256(positions).hexdigest(),
            "--runtime-byte-order",
            "big",
            "--output",
            str(output),
        ]

    first = tmp_path / "correlation-a.json"
    second = tmp_path / "correlation-b.json"
    assert main(arguments(first)) == 0
    capsys.readouterr()
    assert main(arguments(second)) == 0
    capsys.readouterr()
    assert first.read_bytes() == second.read_bytes()
    preserved = first.read_bytes()
    assert main(arguments(first)) == 1
    assert first.read_bytes() == preserved
    assert "refusing to overwrite" in capsys.readouterr().err

    linked_xpp = tmp_path / "linked-character.xpp"
    linked_xpp.symlink_to(xpp_path)
    linked_arguments = arguments(tmp_path / "linked-output.json")
    linked_arguments[linked_arguments.index(str(xpp_path))] = str(linked_xpp)
    assert main(linked_arguments) == 1
    assert "regular non-symlink" in capsys.readouterr().err
