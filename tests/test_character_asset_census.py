"""Synthetic tests for the permanent multipart character asset census."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from infamous_xpp_textures.character_asset_census import (
    CharacterAssetCensusError,
    build_character_asset_census,
    load_oid_manifest,
    write_new_character_asset_census,
)

from test_synthetic import _minimal_xpp


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _named_xpp(oid: int, payload: bytes) -> bytes:
    data = bytearray(_minimal_xpp(extra=payload))
    data_offset = struct.unpack_from(">I", data, 0x28)[0]
    struct.pack_into(">I", data, data_offset + 0x20, oid)
    return bytes(data)


def _profile(root: Path, xpp: bytes) -> str:
    relative = "xpp/install1/character.xpp"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(xpp)
    workspace = {
        "kind": "xpp-workspace",
        "schema_version": 1,
        "archives": [
            {
                "slot": "install1",
                "entries": [
                    {
                        "name": "/character.xpp",
                        "extracted": relative,
                        "bytes": len(xpp),
                        "sha256": _sha256(xpp),
                    }
                ],
            }
        ],
    }
    rendered = (json.dumps(workspace, sort_keys=True) + "\n").encode()
    (root / "workspace.json").write_bytes(rendered)
    return _sha256(rendered)


def _manifest(path: Path) -> str:
    payload = (
        b"unused\nCharacter_Surface_C.psd\nmale_base_Character.xml\ncharacter_hat\n"
    )
    path.write_bytes(payload)
    return _sha256(payload)


def test_character_asset_census_maps_names_and_cross_profile_texture_identity(
    tmp_path: Path,
):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left_xpp = _named_xpp(1, struct.pack("<HHI", 0xFFFF, 0, 0))
    right_xpp = _named_xpp(1, struct.pack("<HHI", 0xFFFF, 0, 0))
    left_workspace = _profile(left, left_xpp)
    right_workspace = _profile(right, right_xpp)
    left_manifest = tmp_path / "left-oids.txt"
    right_manifest = tmp_path / "right-oids.txt"
    left_oid_sha = _manifest(left_manifest)
    right_oid_sha = _manifest(right_manifest)

    report = build_character_asset_census(
        left,
        right,
        left_workspace,
        right_workspace,
        left_manifest,
        right_manifest,
        left_oid_sha,
        right_oid_sha,
        "xpp/install1/character.xpp",
        "xpp/install1/character.xpp",
        anchor="male_base_Character.xml",
        name_token="character",
        anchor_before=2,
        anchor_after=1,
    )

    assert report["format"] == "infamous-character-asset-census"
    assert report["profiles"]["left"]["package_count"] == 1
    assert report["targets"]["left"]["texture_descriptor_count"] == 1
    descriptor = report["target_texture_descriptors"]["left"][0]
    assert descriptor["name"] == "Character_Surface_C.psd"
    assert descriptor["family"] == "Character_Surface"
    assert descriptor["name_suffix"] == "C"
    assert report["cross_build_texture_mapping"] == {
        "complete_unique_match": True,
        "unique_matches": 1,
        "reordered_matches": 0,
        "mapping": [{"left": 0, "right": 0}],
        "ambiguous_groups": [],
        "missing_from_left": [],
        "missing_from_right": [],
    }
    assert report["findings"]["named_texture_descriptors_proved"] is True
    assert report["findings"]["geometry_to_name_binding_proved"] is False
    assert report["findings"]["complete_character_proved"] is False
    assert report["scope"]["character_or_item_agnostic"] is True
    assert report["scope"]["existing_completion_inventory_consumed"] is False
    assert report["completion_gates"]["no_mistextured_pieces"] is False
    assert report["delivery_gates"]["rpcs3_emulator_mod_round_trip"] is False
    assert report["delivery_gates"]["native_decomp_asset_import"] is False
    assert report["profile_scans"]["left"]["verified_packages"] == 1


def test_manifest_pin_and_atomic_new_output_are_fail_closed(tmp_path: Path):
    manifest = tmp_path / "oids.txt"
    digest = _manifest(manifest)
    names, summary = load_oid_manifest(manifest, digest)
    assert names[1] == "Character_Surface_C.psd"
    assert summary["ordinal_oid_schema"] is True
    with pytest.raises(CharacterAssetCensusError, match="SHA-256 mismatch"):
        load_oid_manifest(manifest, "0" * 64)

    output = tmp_path / "report.json"
    report = {"format": "synthetic-character-asset-census"}
    write_new_character_asset_census(output, report)
    original = output.read_bytes()
    with pytest.raises(CharacterAssetCensusError, match="already exists"):
        write_new_character_asset_census(output, report)
    assert output.read_bytes() == original


def test_profile_rejects_workspace_entry_hash_drift(tmp_path: Path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    xpp = _named_xpp(1, bytes(8))
    left_workspace = _profile(left, xpp)
    right_workspace = _profile(right, xpp)
    (left / "xpp/install1/character.xpp").write_bytes(xpp + b"drift")
    left_manifest = tmp_path / "left-oids.txt"
    right_manifest = tmp_path / "right-oids.txt"
    left_oid_sha = _manifest(left_manifest)
    right_oid_sha = _manifest(right_manifest)

    with pytest.raises(CharacterAssetCensusError, match="byte count differs"):
        build_character_asset_census(
            left,
            right,
            left_workspace,
            right_workspace,
            left_manifest,
            right_manifest,
            left_oid_sha,
            right_oid_sha,
            "xpp/install1/character.xpp",
            "xpp/install1/character.xpp",
            anchor="male_base_Character.xml",
            name_token="character",
            anchor_before=2,
            anchor_after=1,
        )


def test_census_rejects_same_profile_and_unsafe_name_token(tmp_path: Path):
    profile = tmp_path / "profile"
    xpp = _named_xpp(1, bytes(8))
    workspace = _profile(profile, xpp)
    manifest = tmp_path / "oids.txt"
    manifest_sha = _manifest(manifest)
    common = (
        profile,
        profile,
        workspace,
        workspace,
        manifest,
        manifest,
        manifest_sha,
        manifest_sha,
        "xpp/install1/character.xpp",
        "xpp/install1/character.xpp",
    )
    with pytest.raises(CharacterAssetCensusError, match="different roots"):
        build_character_asset_census(
            *common,
            anchor="male_base_Character.xml",
            name_token="character",
            anchor_before=2,
            anchor_after=1,
        )

    other = tmp_path / "other"
    other_workspace = _profile(other, xpp)
    with pytest.raises(CharacterAssetCensusError, match="without whitespace"):
        build_character_asset_census(
            profile,
            other,
            workspace,
            other_workspace,
            manifest,
            manifest,
            manifest_sha,
            manifest_sha,
            "xpp/install1/character.xpp",
            "xpp/install1/character.xpp",
            anchor="male_base_Character.xml",
            name_token="bad token",
            anchor_before=2,
            anchor_after=1,
        )
