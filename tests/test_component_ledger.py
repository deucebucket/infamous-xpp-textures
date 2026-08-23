"""Tests for the permanent multipart character component ledger."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from infamous_xpp_textures import cli
from infamous_xpp_textures.component_ledger import (
    CharacterComponentLedgerError,
    build_character_component_ledger,
    render_character_component_ledger,
    write_new_character_component_ledger,
)


def _write_json(path: Path, value: dict) -> str:
    payload = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _material(
    *,
    event: int,
    record_offset: int,
    family: str = "Zeke_Hair",
    position_sha: str = "3" * 64,
    observed: int = 8,
    unobserved: int = 2,
) -> dict:
    textures = []
    for descriptor, suffix in enumerate(("C", "N")):
        textures.append(
            {
                "descriptor_index": descriptor,
                "name": f"{family}_{suffix}.psd",
                "suffix": suffix,
                "width": 256,
                "height": 256,
                "decoded_rgba_sha256": f"{descriptor + 4:x}" * 64,
                "embedded_png_sha256": f"{descriptor + 6:x}" * 64,
                "runtime_prefix_sha256": f"{descriptor + 8:x}" * 64,
            }
        )
    return {
        "format": "infamous-character-material-export",
        "version": 1,
        "tool_inventory_id": "xpp-tool.character-material-export.v1",
        "status": "retail-material-progress-glb-written",
        "authorities": {
            "xpp_bytes": 1000,
            "xpp_sha256": "1" * 64,
            "lineage_sha256": f"{event:x}" * 64,
            "texture_allowlist_sha256": "2" * 64,
        },
        "selection": {
            "page": 2,
            "event": event,
            "draw_event": 100 + event,
            "record_offset": record_offset,
            "vertices": 12,
            "triangles": observed + unobserved,
            "nondegenerate_triangles": observed + unobserved,
            "material_observed_triangles": observed,
            "material_unobserved_triangles": unobserved,
            "index_sha256": f"{event + 1:x}" * 64,
            "material_event_index_sha256": f"{event + 2:x}" * 64,
            "position_payload_sha256": position_sha,
            "uv_payload_sha256": "4" * 64,
            "uv_byte_offset": 4,
            "texture_family": family,
        },
        "glb": {"bytes": 400, "sha256": f"{event + 3:x}" * 64},
        "textures": textures,
        "proof": {
            "deterministic_material_glb": True,
            "exact_full_vertex_range": True,
            "exact_retail_topology": True,
            "exact_uv_rows": True,
            "runtime_prefix_to_retail_descriptor": True,
            "shader_proved_texcoord_0": True,
        },
        "limitations": {
            "full_topology_material_coverage": unobserved == 0,
            "full_character": False,
            "four_x_textures": False,
            "native_pbr": False,
            "rigged": False,
            "rpcs3_mod_round_trip": False,
            "native_decomp_import": False,
        },
    }


def _receipts() -> dict:
    return {
        "format": "infamous-character-visual-baseline-receipts",
        "version": 1,
        "title_id": "infamous-1",
        "build_id": "bcus98119-v0100",
        "candidate_id": "zeke",
        "renders": [
            {
                "page": 2,
                "record_offset": 100,
                "variant": "accepted-unlit-neg58",
                "material_scope": "preview-full-record",
                "accepted_visual_baseline": True,
                "acceptance_note": "Approved matte base color; no fake shine.",
                "image": {
                    "name": "zeke-hair-unlit-neg58.png",
                    "bytes": 1234,
                    "sha256": "f" * 64,
                    "width": 1600,
                    "height": 1200,
                },
            }
        ],
    }


def test_ledger_merges_runtime_aliases_and_keeps_completion_false(tmp_path: Path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    visual = tmp_path / "visual.json"
    first_sha = _write_json(first, _material(event=1, record_offset=100))
    second_sha = _write_json(second, _material(event=2, record_offset=100))
    visual_sha = _write_json(visual, _receipts())

    report = build_character_component_ledger(
        ((first, first_sha), (second, second_sha)),
        title_id="infamous-1",
        build_id="bcus98119-v0100",
        candidate_id="zeke",
        visual_receipts=(visual, visual_sha),
    )
    reverse = build_character_component_ledger(
        ((second, second_sha), (first, first_sha)),
        title_id="infamous-1",
        build_id="bcus98119-v0100",
        candidate_id="zeke",
        visual_receipts=(visual, visual_sha),
    )

    assert report["format"] == "infamous-character-component-progress-ledger"
    assert report == reverse
    assert report["counts"] == {
        "components": 1,
        "material_observations": 2,
        "published_render_receipts": 1,
        "accepted_visual_baselines": 1,
        "full_material_coverage_components": 0,
        "full_character_assemblies": 0,
        "rpcs3_mod_round_trips": 0,
        "native_decomp_imports": 0,
    }
    component = report["components"][0]
    assert component["component_id"].endswith(":p2:r100")
    assert [row["event"] for row in component["observations"]] == [1, 2]
    assert component["texture_families"] == ["Zeke_Hair"]
    assert component["completion"]["accepted_visual_baseline"] is True
    assert component["completion"]["full_character_assembly_complete"] is False
    assert report["completion_truth"] == {
        "full_character": False,
        "four_x_textures": False,
        "authored_pbr": False,
        "rigged_and_skinned": False,
        "rpcs3_mod_round_trip": False,
        "native_decomp_import": False,
    }
    serialized = render_character_component_ledger(report)
    assert b"/private/" not in serialized
    assert serialized == render_character_component_ledger(report)


def test_ledger_rejects_hash_drift_conflicts_and_unknown_render(tmp_path: Path):
    first = tmp_path / "first.json"
    conflicting = tmp_path / "conflict.json"
    visual = tmp_path / "visual.json"
    first_sha = _write_json(first, _material(event=1, record_offset=100))
    conflict_sha = _write_json(
        conflicting,
        _material(event=2, record_offset=100, position_sha="a" * 64),
    )
    with pytest.raises(CharacterComponentLedgerError, match="immutable"):
        build_character_component_ledger(
            ((first, first_sha), (conflicting, conflict_sha)),
            title_id="infamous-1",
            build_id="bcus98119-v0100",
            candidate_id="zeke",
        )
    with pytest.raises(CharacterComponentLedgerError, match="mismatch"):
        build_character_component_ledger(
            ((first, "0" * 64),),
            title_id="infamous-1",
            build_id="bcus98119-v0100",
            candidate_id="zeke",
        )

    receipts = _receipts()
    receipts["renders"][0]["record_offset"] = 999
    visual_sha = _write_json(visual, receipts)
    with pytest.raises(CharacterComponentLedgerError, match="without material"):
        build_character_component_ledger(
            ((first, first_sha),),
            title_id="infamous-1",
            build_id="bcus98119-v0100",
            candidate_id="zeke",
            visual_receipts=(visual, visual_sha),
        )


def test_ledger_requires_truthful_triangle_coverage_and_new_output(tmp_path: Path):
    material = _material(event=1, record_offset=100)
    material["limitations"]["full_topology_material_coverage"] = True
    path = tmp_path / "material.json"
    digest = _write_json(path, material)
    with pytest.raises(CharacterComponentLedgerError, match="contradicts"):
        build_character_component_ledger(
            ((path, digest),),
            title_id="infamous-1",
            build_id="bcus98119-v0100",
            candidate_id="zeke",
        )

    wrong_family = _material(event=2, record_offset=200)
    wrong_family["selection"]["texture_family"] = "Zeke_Head"
    wrong_path = tmp_path / "wrong-family.json"
    wrong_sha = _write_json(wrong_path, wrong_family)
    with pytest.raises(CharacterComponentLedgerError, match="family contradicts"):
        build_character_component_ledger(
            ((wrong_path, wrong_sha),),
            title_id="infamous-1",
            build_id="bcus98119-v0100",
            candidate_id="zeke",
        )

    good = tmp_path / "good.json"
    good_sha = _write_json(good, _material(event=1, record_offset=100))
    report = build_character_component_ledger(
        ((good, good_sha),),
        title_id="infamous-1",
        build_id="bcus98119-v0100",
        candidate_id="zeke",
    )
    output = tmp_path / "ledger.json"
    write_new_character_component_ledger(output, report)
    original = output.read_bytes()
    with pytest.raises(CharacterComponentLedgerError, match="already exists"):
        write_new_character_component_ledger(output, report)
    assert output.read_bytes() == original


def test_ledger_accepts_revalidated_union_export_and_rejects_drift(tmp_path: Path):
    material = _material(event=1, record_offset=100, observed=10, unobserved=0)
    material["tool_inventory_id"] = "xpp-tool.character-material-coverage-export.v1"
    material["presentation_mode"] = "observed-union"
    material["authorities"]["coverage_union_sha256"] = "a" * 64
    material["selection"]["material_union_index_sha256"] = "b" * 64
    material["coverage_union"] = {
        "receipt_sha256": "a" * 64,
        "observation_count": 3,
        "covered_retail_triangle_occurrences": 10,
        "unobserved_retail_triangle_occurrences": 0,
        "full_retail_material_coverage_proved": True,
        "covered_triangle_multiset_sha256": "b" * 64,
        "unobserved_triangle_multiset_sha256": "c" * 64,
    }
    material["proof"]["coverage_union_revalidated"] = True
    material["proof"]["exact_union_triangle_material_subset"] = True
    path = tmp_path / "union-material.json"
    digest = _write_json(path, material)
    report = build_character_component_ledger(
        ((path, digest),),
        title_id="infamous-1",
        build_id="bcus98119-v0100",
        candidate_id="zeke",
    )
    observation = report["components"][0]["observations"][0]
    assert observation["tool_inventory_id"].endswith("coverage-export.v1")
    assert observation["proof"]["full_material_coverage"] is True

    material["coverage_union"]["covered_retail_triangle_occurrences"] = 9
    drift = tmp_path / "union-drift.json"
    drift_sha = _write_json(drift, material)
    with pytest.raises(CharacterComponentLedgerError, match="does not reconcile"):
        build_character_component_ledger(
            ((drift, drift_sha),),
            title_id="infamous-1",
            build_id="bcus98119-v0100",
            candidate_id="zeke",
        )


def test_cli_requires_paired_reports_and_builds_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    output = tmp_path / "ledger.json"
    observed: dict = {}

    def fake_build(material_reports, **kwargs):
        observed["material_reports"] = material_reports
        observed.update(kwargs)
        return {
            "counts": {
                "components": 5,
                "material_observations": 5,
                "accepted_visual_baselines": 1,
            }
        }

    monkeypatch.setattr(cli, "build_character_component_ledger", fake_build)
    monkeypatch.setattr(
        cli,
        "write_new_character_component_ledger",
        lambda path, report: path.write_text(json.dumps(report)),
    )
    exit_code = cli.main(
        [
            "character-component-ledger",
            "--title-id",
            "infamous-1",
            "--build-id",
            "bcus98119-v0100",
            "--candidate-id",
            "zeke",
            "--material-report",
            str(tmp_path / "material.json"),
            "--material-report-sha256",
            "a" * 64,
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    assert observed["title_id"] == "infamous-1"
    assert observed["candidate_id"] == "zeke"
    assert len(observed["material_reports"]) == 1
    assert "5 components" in capsys.readouterr().out

    assert (
        cli.main(
            [
                "character-component-ledger",
                "--title-id",
                "infamous-1",
                "--build-id",
                "bcus98119-v0100",
                "--candidate-id",
                "zeke",
                "--material-report",
                str(tmp_path / "material.json"),
                "--material-report",
                str(tmp_path / "second.json"),
                "--material-report-sha256",
                "a" * 64,
                "--output",
                str(tmp_path / "other.json"),
            ]
        )
        == 1
    )
