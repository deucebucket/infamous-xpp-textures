"""Synthetic tests for the payload-free cross-build material-gap oracle."""

from __future__ import annotations

import copy
import json

import pytest

from infamous_xpp_textures.cli import main
from infamous_xpp_textures.material_gap_oracle import (
    MaterialGapOracleError,
    compare_cross_build_material_gap,
    read_bounded_regular,
    write_new_material_gap_oracle,
)


LEFT_SHA = "a" * 64
RIGHT_SHA = "b" * 64
UNION_SHA = "c" * 64
INDEX_SHA = "d" * 64


def _contract(offset: int, *, index_sha256: str = INDEX_SHA) -> dict:
    return {
        "record_offset": offset,
        "vertex_count": 8,
        "index_count": 18,
        "index_byte_count": 36,
        "triangle_count": 6,
        "index_sha256": index_sha256,
    }


def _character(offset: int, *, index_sha256: str = INDEX_SHA) -> dict:
    return {"contracts": [_contract(offset, index_sha256=index_sha256)]}


def _cross_build() -> dict:
    return {
        "format": "infamous-xpp-cross-build-character-oracle",
        "version": 1,
        "left_source_sha256": LEFT_SHA,
        "right_source_sha256": RIGHT_SHA,
        "audited_semantics_match": True,
        "character": {"mapping": [{"left": 0, "right": 0}]},
        "texture": {
            "exact_texture_allowlist_reusable": True,
            "descriptor_index_portable": False,
        },
    }


def _coverage_union() -> dict:
    return {
        "format": "infamous-character-material-coverage-union",
        "version": 1,
        "tool_inventory_id": "xpp-tool.character-material-coverage-union.v1",
        "status": "partial-retail-material-coverage-proved",
        "authorities": {
            "xpp_bytes": 100,
            "xpp_sha256": LEFT_SHA,
            "texture_allowlist_sha256": "e" * 64,
            "retail_index_sha256": INDEX_SHA,
        },
        "component": {
            "record_offset": 1000,
            "vertices": 8,
            "retail_triangle_occurrences": 6,
            "texture_family": "fixture_hair",
            "texture_names": ["fixture_hair_C.psd", "fixture_hair_N.psd"],
        },
        "observations": [{"fixture": 1}, {"fixture": 2}],
        "union": {
            "observation_count": 2,
            "covered_retail_triangle_occurrences": 5,
            "unobserved_retail_triangle_occurrences": 1,
            "full_retail_material_coverage_proved": False,
            "covered_triangle_multiset_sha256": "f" * 64,
            "unobserved_triangle_multiset_sha256": "1" * 64,
        },
        "payload_bytes_serialized": False,
    }


def _report() -> dict:
    return compare_cross_build_material_gap(
        _character(1000),
        _character(9000),
        _cross_build(),
        _coverage_union(),
        coverage_union_sha256=UNION_SHA,
        coverage_union_bytes=500,
        left_source_sha256=LEFT_SHA,
        left_source_bytes=100,
        right_source_sha256=RIGHT_SHA,
        right_source_bytes=120,
        left_label="disc",
        right_label="digital",
    )


def test_exact_gap_maps_without_promoting_target_material_binding():
    report = _report()
    assert report["status"] == (
        "exact-topology-gap-portable-runtime-material-binding-unproved"
    )
    assert report["component"]["left_record_offset"] == 1000
    assert report["component"]["right_record_offset"] == 9000
    assert report["source_coverage"]["covered_retail_triangle_occurrences"] == 5
    assert report["source_coverage"]["unobserved_retail_triangle_occurrences"] == 1
    assert report["cross_build"]["exact_retail_index_stream_identical"] is True
    assert report["cross_build"]["topology_gap_identity_portable"] is True
    assert report["cross_build"]["descriptor_index_portable"] is False
    assert report["cross_build"]["target_runtime_material_binding_proved"] is False
    assert report["payload_bytes_serialized"] is False


def test_output_is_deterministic_and_contains_no_paths_or_payloads():
    first = json.dumps(_report(), sort_keys=True)
    second = json.dumps(_report(), sort_keys=True)
    assert first == second
    lowered = first.lower()
    assert "/home/" not in lowered
    assert 'payload_bytes_serialized": false' in lowered
    assert "index_payload" not in lowered


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("xpp_sha256", "9" * 64, "source authorities"),
        ("xpp_bytes", 99, "source authorities"),
        ("retail_index_sha256", "bad", "source authorities"),
    ],
)
def test_source_authority_drift_fails_closed(field, value, message):
    union = _coverage_union()
    union["authorities"][field] = value
    with pytest.raises(MaterialGapOracleError, match=message):
        compare_cross_build_material_gap(
            _character(1000),
            _character(9000),
            _cross_build(),
            union,
            coverage_union_sha256=UNION_SHA,
            coverage_union_bytes=500,
            left_source_sha256=LEFT_SHA,
            left_source_bytes=100,
            right_source_sha256=RIGHT_SHA,
            right_source_bytes=120,
        )


def test_coverage_counts_must_reconcile():
    union = _coverage_union()
    union["union"]["unobserved_retail_triangle_occurrences"] = 2
    with pytest.raises(MaterialGapOracleError, match="counts do not reconcile"):
        compare_cross_build_material_gap(
            _character(1000),
            _character(9000),
            _cross_build(),
            union,
            coverage_union_sha256=UNION_SHA,
            coverage_union_bytes=500,
            left_source_sha256=LEFT_SHA,
            left_source_bytes=100,
            right_source_sha256=RIGHT_SHA,
            right_source_bytes=120,
        )


def test_non_object_observation_and_oversized_declared_input_reject():
    union = _coverage_union()
    union["observations"][0] = "not-an-object"
    with pytest.raises(MaterialGapOracleError, match="counts do not reconcile"):
        compare_cross_build_material_gap(
            _character(1000),
            _character(9000),
            _cross_build(),
            union,
            coverage_union_sha256=UNION_SHA,
            coverage_union_bytes=500,
            left_source_sha256=LEFT_SHA,
            left_source_bytes=100,
            right_source_sha256=RIGHT_SHA,
            right_source_bytes=120,
        )
    with pytest.raises(MaterialGapOracleError, match="coverage union exceeds"):
        compare_cross_build_material_gap(
            _character(1000),
            _character(9000),
            _cross_build(),
            _coverage_union(),
            coverage_union_sha256=UNION_SHA,
            coverage_union_bytes=256 * 1024 + 1,
            left_source_sha256=LEFT_SHA,
            left_source_bytes=100,
            right_source_sha256=RIGHT_SHA,
            right_source_bytes=120,
        )


def test_cross_build_semantic_failure_rejects():
    cross_build = _cross_build()
    cross_build["audited_semantics_match"] = False
    with pytest.raises(MaterialGapOracleError, match="semantics are not proved"):
        compare_cross_build_material_gap(
            _character(1000),
            _character(9000),
            cross_build,
            _coverage_union(),
            coverage_union_sha256=UNION_SHA,
            coverage_union_bytes=500,
            left_source_sha256=LEFT_SHA,
            left_source_bytes=100,
            right_source_sha256=RIGHT_SHA,
            right_source_bytes=120,
        )


def test_output_labels_and_texture_names_cannot_serialize_paths():
    with pytest.raises(MaterialGapOracleError, match="unsafe character"):
        compare_cross_build_material_gap(
            _character(1000),
            _character(9000),
            _cross_build(),
            _coverage_union(),
            coverage_union_sha256=UNION_SHA,
            coverage_union_bytes=500,
            left_source_sha256=LEFT_SHA,
            left_source_bytes=100,
            right_source_sha256=RIGHT_SHA,
            right_source_bytes=120,
            left_label="/private/build",
        )
    union = _coverage_union()
    union["component"]["texture_names"][0] = "private/path.psd"
    with pytest.raises(MaterialGapOracleError, match="texture family is malformed"):
        compare_cross_build_material_gap(
            _character(1000),
            _character(9000),
            _cross_build(),
            union,
            coverage_union_sha256=UNION_SHA,
            coverage_union_bytes=500,
            left_source_sha256=LEFT_SHA,
            left_source_bytes=100,
            right_source_sha256=RIGHT_SHA,
            right_source_bytes=120,
        )
    union = _coverage_union()
    union["component"]["texture_family"] = "private/family"
    with pytest.raises(MaterialGapOracleError, match="texture family is malformed"):
        compare_cross_build_material_gap(
            _character(1000),
            _character(9000),
            _cross_build(),
            union,
            coverage_union_sha256=UNION_SHA,
            coverage_union_bytes=500,
            left_source_sha256=LEFT_SHA,
            left_source_bytes=100,
            right_source_sha256=RIGHT_SHA,
            right_source_bytes=120,
        )


def test_missing_unique_contract_mapping_rejects():
    cross_build = _cross_build()
    cross_build["character"]["mapping"] = []
    with pytest.raises(MaterialGapOracleError, match="no unique target mapping"):
        compare_cross_build_material_gap(
            _character(1000),
            _character(9000),
            cross_build,
            _coverage_union(),
            coverage_union_sha256=UNION_SHA,
            coverage_union_bytes=500,
            left_source_sha256=LEFT_SHA,
            left_source_bytes=100,
            right_source_sha256=RIGHT_SHA,
            right_source_bytes=120,
        )


def test_different_target_index_stream_rejects():
    with pytest.raises(MaterialGapOracleError, match="identical retail index stream"):
        compare_cross_build_material_gap(
            _character(1000),
            _character(9000, index_sha256="2" * 64),
            _cross_build(),
            _coverage_union(),
            coverage_union_sha256=UNION_SHA,
            coverage_union_bytes=500,
            left_source_sha256=LEFT_SHA,
            left_source_bytes=100,
            right_source_sha256=RIGHT_SHA,
            right_source_bytes=120,
        )


def test_writer_refuses_existing_output(tmp_path):
    output = tmp_path / "oracle.json"
    output.write_text("keep", encoding="utf-8")
    with pytest.raises(MaterialGapOracleError, match="already exists"):
        write_new_material_gap_oracle(output, _report())
    assert output.read_text(encoding="utf-8") == "keep"


def test_reader_rejects_symlink(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"source")
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(MaterialGapOracleError, match="non-symlink"):
        read_bounded_regular(link, limit=100, label="fixture")


def test_cli_refuses_existing_output(tmp_path, monkeypatch, capsys):
    left = tmp_path / "left.xpp"
    right = tmp_path / "right.xpp"
    union = tmp_path / "union.json"
    output = tmp_path / "oracle.json"
    left.write_bytes(b"left")
    right.write_bytes(b"right")
    union.write_text("{}", encoding="utf-8")
    output.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(
        "infamous_xpp_textures.cli.build_cross_build_material_gap_oracle",
        lambda *args, **kwargs: copy.deepcopy(_report()),
    )
    assert (
        main(
            [
                "character-material-gap-oracle",
                "--left-xpp",
                str(left),
                "--right-xpp",
                str(right),
                "--coverage-union",
                str(union),
                "--json-out",
                str(output),
            ]
        )
        == 1
    )
    assert output.read_text(encoding="utf-8") == "keep"
    assert "already exists" in capsys.readouterr().err
