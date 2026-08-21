"""Game-data-free runtime hash index tests."""

from __future__ import annotations

import hashlib
import json

import pytest

from infamous_xpp_textures.cli import main
from infamous_xpp_textures.pack import pack_chains
from infamous_xpp_textures.heap import chain_size
from infamous_xpp_textures.runtime import (
    build_replacement_bundle,
    build_runtime_index,
    runtime_mip_count,
)
from infamous_xpp_textures.validation import validate_xpp

from test_synthetic import _minimal_xpp


def test_runtime_index_hashes_descriptor_face_and_mip_boundaries():
    payload = bytes.fromhex("0011223344556677")
    data = _minimal_xpp(extra=payload)
    report = build_runtime_index(data, "synthetic")

    expected = hashlib.sha256(payload).hexdigest()
    assert report["structural_status"] == "pass"
    assert report["descriptor_count"] == 1
    assert report["identity_count"] == 3
    assert report["unique_hash_count"] == 1
    assert report["allowlist"] == [expected]
    assert [item["kind"] for item in report["identities"]] == [
        "descriptor",
        "face-chain",
        "mip",
    ]
    assert report["descriptors"][0]["sha256"] == expected


def test_runtime_index_cli_writes_deterministic_json_and_allowlist(tmp_path):
    payload = bytes.fromhex("8899aabbccddeeff")
    source = tmp_path / "fixture.xpp"
    first_json = tmp_path / "first.json"
    second_json = tmp_path / "second.json"
    allowlist = tmp_path / "allowlist.txt"
    source.write_bytes(_minimal_xpp(extra=payload))

    first_args = [
        "runtime-index",
        "--xpp",
        str(source),
        "--label",
        "fixture",
        "--json-out",
        str(first_json),
        "--allowlist-out",
        str(allowlist),
    ]
    second_args = [
        "runtime-index",
        "--xpp",
        str(source),
        "--label",
        "fixture",
        "--json-out",
        str(second_json),
    ]
    assert main(first_args) == 0
    assert main(second_args) == 0
    assert first_json.read_bytes() == second_json.read_bytes()

    report = json.loads(first_json.read_text(encoding="utf-8"))
    hash_lines = [
        line
        for line in allowlist.read_text(encoding="ascii").splitlines()
        if line and not line.startswith("#")
    ]
    assert hash_lines == report["allowlist"]


def test_runtime_index_covers_bcn_prefix_without_sub_4x4_mips():
    payload = bytes(range(32))
    data = _minimal_xpp(width=4, height=4, mips=3, extra=payload[:24])
    report = build_runtime_index(data, "bcn-prefix")

    prefix = hashlib.sha256(payload[:8]).hexdigest()
    matches = [
        item
        for item in report["identities"]
        if item["kind"] == "descriptor-mip-prefix" and item["mip_count"] == 1
    ]
    assert len(matches) == 1
    assert matches[0]["sha256"] == prefix
    assert prefix in report["allowlist"]


def test_runtime_mip_count_stops_when_either_bcn_dimension_falls_below_four():
    chain = bytes(chain_size(0x86, 256, 8, 9))
    data = _minimal_xpp(width=256, height=8, mips=9, extra=chain)
    _summary, records = validate_xpp(data)
    assert runtime_mip_count(records[0]) == 2


def test_runtime_bundle_is_explicit_hashed_and_atomic(tmp_path):
    retail_payload = bytes(range(24))
    retail = _minimal_xpp(width=4, height=4, mips=3, extra=retail_payload)
    candidate_payload = bytes(range(56))
    candidate = pack_chains(retail, {0: (8, 8, 4, candidate_payload)})
    outdir = tmp_path / "bundle"

    report = build_replacement_bundle(
        retail, candidate, {0}, outdir, label="synthetic-2x"
    )

    record = report["records"][0]
    assert report["replacement_count"] == 1
    assert record["source"]["mipmaps"] == 1
    assert record["source"]["bytes"] == 8
    assert record["candidate"]["width"] == 8
    assert record["candidate"]["mipmaps"] == 2
    assert record["candidate"]["bytes"] == 40
    assert record["candidate"]["pitch"] == 16
    payload = (outdir / record["candidate"]["file"]).read_bytes()
    assert payload == candidate_payload[:40]
    assert hashlib.sha256(payload).hexdigest() == record["candidate"]["sha256"]
    assert (outdir / "replacements.tsv").is_file()
    assert json.loads((outdir / "bundle.json").read_text())["label"] == "synthetic-2x"


def test_runtime_bundle_rejects_unchanged_and_existing_output(tmp_path):
    retail = _minimal_xpp()
    assert main(
        [
            "runtime-bundle",
            "--retail",
            str(tmp_path / "missing-retail.xpp"),
            "--candidate",
            str(tmp_path / "missing-candidate.xpp"),
            "--index",
            "0",
            "--outdir",
            str(tmp_path / "bundle"),
        ]
    ) == 1

    with pytest.raises(ValueError, match="no runtime payload change"):
        build_replacement_bundle(retail, retail, {0}, tmp_path / "unchanged", label="x")

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        build_replacement_bundle(retail, retail, {0}, existing, label="x")
