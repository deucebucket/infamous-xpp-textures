"""Game-data-free runtime hash index tests."""

from __future__ import annotations

import hashlib
import json

from infamous_xpp_textures.cli import main
from infamous_xpp_textures.runtime import build_runtime_index

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
