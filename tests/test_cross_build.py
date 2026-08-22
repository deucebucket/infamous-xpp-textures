"""Payload-free cross-build character oracle tests."""

from __future__ import annotations

import copy
import json

import pytest

from infamous_xpp_textures.cli import main
from infamous_xpp_textures.cross_build import (
    CrossBuildOracleError,
    compare_cross_build_reports,
)


def _runtime(order: tuple[str, ...]) -> dict:
    descriptors = []
    identities = []
    for index, name in enumerate(order):
        digest = ("a" if name == "A" else "b") * 64
        width = 64 if name == "A" else 32
        descriptors.append(
            {
                "index": index,
                "format": "0x86",
                "width": width,
                "height": width,
                "faces": 1,
                "mips": 1,
                "chain_bytes_per_face": width,
                "upload_bytes": width,
                "sha256": digest,
            }
        )
        identities.append(
            {
                "kind": "descriptor",
                "descriptor": index,
                "face": None,
                "mip": None,
                "bytes": width,
                "sha256": digest,
            }
        )
    hashes = sorted({identity["sha256"] for identity in identities})
    return {
        "schema": 1,
        "label": "fixture",
        "source_sha256": "c" * 64,
        "structural_status": "pass",
        "descriptor_count": len(descriptors),
        "identity_count": len(identities),
        "unique_hash_count": len(hashes),
        "descriptors": descriptors,
        "identities": identities,
        "allowlist": hashes,
    }


def _contract(name: str, base: int) -> dict:
    count = 12 if name == "A" else 18
    digest = ("d" if name == "A" else "e") * 64
    return {
        "record_offset": base,
        "vertex_count_field_offset": base + 4,
        "index_offset": base + 100,
        "vertex_count": count,
        "index_count": count * 3,
        "triangle_count": count,
        "index_byte_count": count * 6,
        "index_sha256": digest,
        "descriptor_span_word": count,
        "first_vertex_descriptor_word": count + 1,
        "packed_vertex_streams": (
            {
                "stream_offset": base + 200,
                "parameter_offset": base + 8,
                "logical_byte_count": count,
                "aligned_byte_count": count + 4,
                "parameter_byte_count": 24,
                "stream_sha256": digest,
                "parameter_sha256": digest,
                "component_count": 3,
                "component_bit_widths": (8, 8, 8, 0),
                "bits_per_vertex": 24,
                "bit_order": "msb-first",
                "tail_padding_bit_count": 0,
                "descriptor_word": count + 2,
                "envelope_stream_index": 1,
            },
        ),
    }


def _character(order: tuple[str, ...], offset_shift: int = 0) -> dict:
    contracts = [
        _contract(name, offset_shift + (1000 if name == "A" else 2000))
        for name in order
    ]
    return {
        "format": "infamous-xpp-character-report",
        "contract_coverage": f"{len(contracts)}/{len(contracts)}",
        "topology_proved": True,
        "descriptor_local_vertex_count": sum(item["vertex_count"] for item in contracts),
        "index_count": sum(item["index_count"] for item in contracts),
        "triangle_count": sum(item["triangle_count"] for item in contracts),
        "packed_stream_count": len(contracts),
        "contracts": contracts,
    }


def _positive_report() -> dict:
    return compare_cross_build_reports(
        _runtime(("A", "B")),
        _runtime(("B", "A")),
        _character(("A", "B")),
        _character(("B", "A"), offset_shift=5000),
        left_label="build-a",
        right_label="build-b",
    )


def test_reordered_and_relocated_semantics_match_without_authorizing_export():
    report = _positive_report()
    assert report["audited_semantics_match"] is True
    assert report["verdict"] == "audited-semantics-match-descriptor-order-diverges"
    assert report["texture"]["unique_matches"] == 2
    assert report["texture"]["reordered_matches"] == 2
    assert report["texture"]["descriptor_index_portable"] is False
    assert report["texture"]["exact_texture_allowlist_reusable"] is True
    assert report["character"]["location_independent_contracts_match"] is True
    assert report["character"]["location_deltas"]["record_offset"] == [
        {"delta": 5000, "count": 2}
    ]
    assert report["cross_build_repack_authorized"] is False
    assert report["character_export_authorized"] is False
    assert report["injection_authorized"] is False


def test_changed_payload_rejects_semantic_equivalence():
    right_runtime = _runtime(("B", "A"))
    right_runtime["identities"][0]["sha256"] = "f" * 64
    right_runtime["descriptors"][0]["sha256"] = "f" * 64
    right_runtime["allowlist"] = sorted(
        {identity["sha256"] for identity in right_runtime["identities"]}
    )
    report = compare_cross_build_reports(
        _runtime(("A", "B")),
        right_runtime,
        _character(("A", "B")),
        _character(("B", "A")),
    )
    assert report["audited_semantics_match"] is False
    assert report["verdict"] == "audited-semantics-diverge"
    assert report["texture"]["missing_from_left"] == [0]
    assert report["texture"]["missing_from_right"] == [1]


def test_ambiguous_duplicate_descriptor_match_rejects():
    left = _runtime(("A", "A"))
    right = _runtime(("A", "A"))
    report = compare_cross_build_reports(
        left,
        right,
        _character(("A", "B")),
        _character(("A", "B")),
    )
    assert report["audited_semantics_match"] is False
    assert report["texture"]["unique_matches"] == 0
    assert len(report["texture"]["ambiguous_groups"]) == 1


def test_malformed_counts_fail_closed():
    malformed = _runtime(("A", "B"))
    malformed["identity_count"] = 99
    with pytest.raises(CrossBuildOracleError, match="identity count"):
        compare_cross_build_reports(
            malformed,
            _runtime(("A", "B")),
            _character(("A", "B")),
            _character(("A", "B")),
        )


def test_report_is_deterministic_and_contains_no_payload_bytes():
    first = json.dumps(_positive_report(), indent=2, sort_keys=True)
    second = json.dumps(_positive_report(), indent=2, sort_keys=True)
    assert first == second
    assert "payload" not in first.lower()


def test_cli_refuses_existing_output(tmp_path, monkeypatch, capsys):
    left = tmp_path / "left.xpp"
    right = tmp_path / "right.xpp"
    output = tmp_path / "oracle.json"
    left.write_bytes(b"left")
    right.write_bytes(b"right")
    output.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(
        "infamous_xpp_textures.cli.build_cross_build_character_oracle",
        lambda *args, **kwargs: copy.deepcopy(_positive_report()),
    )
    assert main(
        [
            "character-oracle",
            "--left-xpp",
            str(left),
            "--right-xpp",
            str(right),
            "--json-out",
            str(output),
        ]
    ) == 1
    assert output.read_text(encoding="utf-8") == "keep"
    assert "already exists" in capsys.readouterr().err
