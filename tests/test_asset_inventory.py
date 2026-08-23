"""Tests for the permanent completion inventory and dual-output manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from infamous_xpp_textures.asset_inventory import (
    AssetInventoryError,
    build_asset_completion_inventory,
    write_new_asset_completion_inventory,
)


def _write(path: Path, value: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: dict) -> str:
    return _write(path, (json.dumps(value, sort_keys=True) + "\n").encode())


def _inputs(tmp_path: Path) -> tuple[Path, str, Path, str, Path, str, Path, str]:
    tally = tmp_path / "GRAPHICS-ASSETS-TALLY.md"
    tally_sha = _write(
        tally,
        (
            "| Retail GLB modkit | 2 models; 0 failures |\n"
            "| Finished gallery | 3 8K renders |\n"
            "| Corrected texture extraction | 10 records; 98.1% good |\n"
            "| Local 4× texture corpus | Complete; original/4x pairs |\n"
            "Character renders remain **0**; reserved.\n"
        ).encode(),
    )
    static_manifest = tmp_path / "static.json"
    static_sha = _write_json(
        static_manifest,
        {
            "title": "Synthetic retail Blender rips",
            "disc": "BCUS00000 v01.00",
            "ok": 2,
            "fail": 0,
            "objects": [
                {
                    "name": "msn_jail.heli",
                    "bucket": "vehicles",
                    "xpp": "install1/msn_jail.xpp",
                    "xpp_sha256": "1" * 64,
                    "status": "ok",
                    "glb": "vehicles/msn_jail.heli.glb",
                    "glb_bytes": 100,
                    "glb_sha256": "2" * 64,
                    "sections": 2,
                    "vertices": 20,
                    "triangles": 10,
                    "pbr": False,
                    "hd": False,
                    "contact": "/private/do/not/serialize/receipt.json",
                    "contact_sha256": "3" * 64,
                },
                {
                    "name": "crate",
                    "bucket": "world",
                    "xpp": "install2/crate.xpp",
                    "xpp_sha256": "4" * 64,
                    "status": "ok",
                    "glb": "world/crate.glb",
                    "glb_bytes": 80,
                    "glb_sha256": "5" * 64,
                    "sections": 1,
                    "vertices": 8,
                    "triangles": 12,
                    "pbr": False,
                    "hd": False,
                    "contact": "/another/private/receipt.json",
                    "contact_sha256": "6" * 64,
                },
            ],
        },
    )
    gallery = tmp_path / "gallery.json"
    gallery_sha = _write_json(
        gallery,
        {
            "format": "infamous-gallery-drive-snapshot",
            "version": 1,
            "source": "synthetic",
            "declared": {
                "unique_asset_renders": 2,
                "gameplay_screenshots": 1,
                "duplicate_file_entries": 1,
                "character_renders": 0,
            },
            "items": [
                {
                    "kind": "asset-render",
                    "bucket": "vehicles",
                    "name": "msn_jail-heli-pbr-8k.png",
                    "bytes": 1000,
                },
                {
                    "kind": "asset-render",
                    "bucket": "weapons",
                    "name": "unresolved-rifle-8k.png",
                    "bytes": 900,
                },
                {
                    "kind": "gameplay-screenshot",
                    "bucket": "root",
                    "name": "gameplay.png",
                    "bytes": 700,
                },
            ],
            "duplicates": [
                {
                    "bucket": "weapons",
                    "name": "unresolved-rifle-8k.png",
                    "bytes": 900,
                    "copies": 2,
                }
            ],
        },
    )
    census = tmp_path / "census.json"
    census_sha = _write_json(
        census,
        {
            "format": "infamous-character-asset-census",
            "version": 1,
            "profiles": {
                "left": {"workspace_sha256": "7" * 64, "package_count": 2},
                "right": {"workspace_sha256": "8" * 64, "package_count": 3},
            },
            "targets": {
                "left": {
                    "relative_path": "xpp/install1/male_base_Zeke.xpp",
                    "bytes": 200,
                    "sha256": "9" * 64,
                    "texture_descriptor_count": 31,
                    "geometry_contract_count": 16,
                },
                "right": {
                    "relative_path": "xpp/install1/male_base_Zeke.xpp",
                    "bytes": 210,
                    "sha256": "a" * 64,
                    "texture_descriptor_count": 31,
                    "geometry_contract_count": 16,
                },
            },
            "findings": {
                "named_texture_descriptors_proved": True,
                "multipart_package_names_proved": True,
                "cross_build_target_texture_identity_proved": True,
            },
            "cross_build_texture_mapping": {
                "unique_matches": 31,
                "reordered_matches": 31,
            },
            "completion_gates": {
                "required_piece_inventory_complete": False,
                "blender_glb_complete": False,
            },
            "delivery_gates": {
                "rpcs3_emulator_mod_round_trip": False,
                "native_decomp_asset_import": False,
            },
        },
    )
    return (
        tally,
        tally_sha,
        static_manifest,
        static_sha,
        gallery,
        gallery_sha,
        census,
        census_sha,
    )


def _build(tmp_path: Path, *, candidate_id: str = "zeke") -> dict:
    return build_asset_completion_inventory(
        *_inputs(tmp_path), candidate_id=candidate_id
    )


def test_inventory_reconciles_without_promoting_partial_work(tmp_path: Path):
    report = _build(tmp_path)

    assert report["format"] == "infamous-asset-completion-inventory"
    assert report["counts"] == {
        "records": 4,
        "complete": 0,
        "partial": 3,
        "unknown": 1,
        "retail_static_glb_exports_to_skip": 2,
        "existing_8k_asset_renders_to_skip": 2,
        "character_renders": 0,
        "rpcs3_round_trip_complete": 0,
        "native_import_complete": 0,
    }
    assert report["reconciliation"]["gallery"]["exact_normalized_static_joins"] == 1
    assert report["reconciliation"]["gallery"]["unresolved_render_subjects"] == 1
    assert report["first_unfinished_batch"]["selected_from_evidence"] is True
    assert report["first_unfinished_batch"]["asset_id"].endswith(":zeke")
    assert report["dual_output_contract"]["native_decomp"]["ready"] is False
    assert report["dual_output_contract"]["rpcs3"]["ready"] is False

    serialized = json.dumps(report, sort_keys=True)
    assert "/private/" not in serialized
    jail = next(row for row in report["records"] if row.get("name") == "msn_jail.heli")
    assert jail["skip_work_classes"] == [
        "retail_static_glb_export",
        "existing_8k_gallery_render",
    ]
    assert jail["completion"]["retail_four_x_pbr_complete"] is False


def test_inventory_is_deterministic_and_output_is_new_only(tmp_path: Path):
    first = _build(tmp_path / "first")
    second = _build(tmp_path / "second")
    assert first == second

    output = tmp_path / "inventory.json"
    write_new_asset_completion_inventory(output, first)
    original = output.read_bytes()
    with pytest.raises(AssetInventoryError, match="already exists"):
        write_new_asset_completion_inventory(output, first)
    assert output.read_bytes() == original


def test_inventory_rejects_hash_drift_and_unanchored_candidate(tmp_path: Path):
    inputs = list(_inputs(tmp_path))
    inputs[1] = "0" * 64
    with pytest.raises(AssetInventoryError, match="SHA-256 mismatch"):
        build_asset_completion_inventory(*inputs, candidate_id="zeke")

    with pytest.raises(AssetInventoryError, match="not anchored"):
        _build(tmp_path / "unanchored", candidate_id="cole")


def test_inventory_rejects_bad_gallery_duplicate_claim(tmp_path: Path):
    inputs = list(_inputs(tmp_path))
    gallery_path = inputs[4]
    gallery = json.loads(gallery_path.read_text())
    gallery["duplicates"][0]["copies"] = 1
    inputs[5] = _write_json(gallery_path, gallery)
    with pytest.raises(AssetInventoryError, match="duplicate"):
        build_asset_completion_inventory(*inputs, candidate_id="zeke")

    inputs = list(_inputs(tmp_path / "bytes"))
    gallery_path = inputs[4]
    gallery = json.loads(gallery_path.read_text())
    gallery["duplicates"][0]["bytes"] += 1
    inputs[5] = _write_json(gallery_path, gallery)
    with pytest.raises(AssetInventoryError, match="byte count differs"):
        build_asset_completion_inventory(*inputs, candidate_id="zeke")


def test_inventory_rejects_symlink_input_and_unsafe_glb_path(tmp_path: Path):
    inputs = list(_inputs(tmp_path / "symlink"))
    tally_link = tmp_path / "tally-link.md"
    tally_link.symlink_to(inputs[0])
    inputs[0] = tally_link
    with pytest.raises(AssetInventoryError, match="non-symlink"):
        build_asset_completion_inventory(*inputs, candidate_id="zeke")

    inputs = list(_inputs(tmp_path / "escape"))
    static_path = inputs[2]
    manifest = json.loads(static_path.read_text())
    manifest["objects"][0]["glb"] = "../escape.glb"
    inputs[3] = _write_json(static_path, manifest)
    with pytest.raises(AssetInventoryError, match="safe .glb relative path"):
        build_asset_completion_inventory(*inputs, candidate_id="zeke")
