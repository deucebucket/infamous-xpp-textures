"""Tests for the permanent multipart character component ledger."""

from __future__ import annotations

import copy
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
    page: int = 2,
    family: str = "Zeke_Hair",
    position_sha: str = "3" * 64,
    index_sha: str | None = None,
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
            "page": page,
            "event": event,
            "draw_event": 100 + event,
            "record_offset": record_offset,
            "vertices": 12,
            "triangles": observed + unobserved,
            "nondegenerate_triangles": observed + unobserved,
            "material_observed_triangles": observed,
            "material_unobserved_triangles": unobserved,
            "index_sha256": index_sha or f"{event + 1:x}" * 64,
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


def _pass_census() -> dict:
    textures = [
        {
            "descriptor_index": descriptor,
            "name": f"Zeke_Hair_{suffix}.psd",
            "suffix": suffix,
            "runtime_prefix_sha256": f"{descriptor + 8:x}" * 64,
        }
        for descriptor, suffix in enumerate(("C", "N"))
    ]
    observations = []
    for page, event, runtime_index, fragment in (
        (2, 1, "a" * 64, "e" * 64),
        (3, 2, "b" * 64, "f" * 64),
    ):
        pass_authority = {
            "vertex_program_sha256": "d" * 64,
            "fragment_program_sha256": fragment,
            "uv_payload_sha256": "4" * 64,
            "uv_byte_offset": 4,
            "texture_family": "Zeke_Hair",
            "textures": textures,
        }
        report_sha = f"{event + 6:x}" * 64
        observation_authority = {
            "page": page,
            "event": event,
            "material_report_sha256": report_sha,
            "runtime_index_sha256": runtime_index,
        }
        observations.append(
            {
                "observation_id": hashlib.sha256(
                    json.dumps(
                        observation_authority, separators=(",", ":"), sort_keys=True
                    ).encode()
                ).hexdigest(),
                "page": page,
                "event": event,
                "draw_event": 100 + event,
                "material_report_sha256": report_sha,
                "lineage_sha256": f"{event + 2:x}" * 64,
                "bundle_format": "if1-texture-bound-topology-v4",
                "bundle_completion": {"bytes": 500, "sha256": f"{event:x}" * 64},
                "capture_key_exclusion_sha256": None,
                "position_payload_sha256": f"{event + 2:x}" * 64,
                "runtime_index_sha256": runtime_index,
                "vertex_program_sha256": "d" * 64,
                "fragment_program_sha256": fragment,
                "uv_payload_sha256": "4" * 64,
                "uv_byte_offset": 4,
                "texture_family": "Zeke_Hair",
                "textures": textures,
                "pass_signature_sha256": hashlib.sha256(
                    json.dumps(
                        pass_authority, separators=(",", ":"), sort_keys=True
                    ).encode()
                ).hexdigest(),
                "observed_triangle_occurrences": 8,
                "observed_triangle_multiset_sha256": runtime_index,
            }
        )
    observations.sort(
        key=lambda row: (
            row["pass_signature_sha256"],
            row["page"],
            row["event"],
            row["material_report_sha256"],
        )
    )
    groups = []
    for row in observations:
        groups.append(
            {
                "pass_signature_sha256": row["pass_signature_sha256"],
                "vertex_program_sha256": row["vertex_program_sha256"],
                "fragment_program_sha256": row["fragment_program_sha256"],
                "uv_payload_sha256": row["uv_payload_sha256"],
                "uv_byte_offset": row["uv_byte_offset"],
                "texture_family": row["texture_family"],
                "textures": row["textures"],
                "observation_ids": [row["observation_id"]],
                "observation_count": 1,
            }
        )
    relationships = [
        {
            "left_observation_id": observations[0]["observation_id"],
            "right_observation_id": observations[1]["observation_id"],
            "relation": "partial-overlap",
            "intersection_triangle_occurrences": 6,
            "left_only_triangle_occurrences": 2,
            "right_only_triangle_occurrences": 2,
            "union_triangle_occurrences": 10,
            "same_pass_signature": False,
            "same_runtime_index_payload": False,
        }
    ]
    return {
        "format": "infamous-character-material-pass-census",
        "version": 1,
        "tool_inventory_id": "xpp-tool.character-material-pass-census.v1",
        "status": "exact-cross-material-pass-census",
        "authorities": {
            "xpp_sha256": "1" * 64,
            "xpp_bytes": 1000,
            "texture_allowlist_sha256": "2" * 64,
            "retail_index_sha256": "2" * 64,
        },
        "component": {
            "record_offset": 100,
            "vertices": 12,
            "retail_triangle_occurrences": 10,
        },
        "observations": observations,
        "pass_groups": groups,
        "relationships": relationships,
        "any_pass_union": {
            "observation_count": 2,
            "pass_signature_count": 2,
            "runtime_index_payload_count": 2,
            "relationship_count": 1,
            "coextensive_cross_signature_relationship_count": 0,
            "partial_cross_signature_relationship_count": 1,
            "covered_retail_triangle_occurrences": 10,
            "unobserved_retail_triangle_occurrences": 0,
            "full_retail_material_coverage_proved": True,
            "covered_triangle_multiset_sha256": "b" * 64,
            "unobserved_triangle_multiset_sha256": "c" * 64,
        },
        "payload_bytes_serialized": False,
        "limitations": {
            "pass_roles_interpreted_as_pbr": False,
            "material_compositing_order_proved": False,
            "full_character": False,
            "rigged": False,
            "four_x_textures": False,
            "rpcs3_mod_round_trip": False,
            "native_decomp_import": False,
        },
        "next_gate": "preserve every exact pass signature",
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
        "material_pass_censuses": 0,
        "material_pass_signatures": 0,
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


def test_v2_groups_exact_cross_page_source_record_and_preserves_poses(
    tmp_path: Path,
):
    first = tmp_path / "page1.json"
    second = tmp_path / "page2.json"
    visual = tmp_path / "visual.json"
    first_sha = _write_json(
        first,
        _material(event=1, page=1, record_offset=100),
    )
    second_sha = _write_json(
        second,
        _material(
            event=2,
            page=2,
            record_offset=100,
            position_sha="a" * 64,
            index_sha="2" * 64,
            observed=10,
            unobserved=0,
        ),
    )
    receipts = _receipts()
    page_one_render = copy.deepcopy(receipts["renders"][0])
    page_one_render["page"] = 1
    page_one_render["variant"] = "published-page1-unlit-neg58"
    page_one_render["accepted_visual_baseline"] = False
    page_one_render["image"]["name"] = "zeke-hair-page1-unlit-neg58.png"
    page_one_render["image"]["sha256"] = "e" * 64
    receipts["renders"].append(page_one_render)
    visual_sha = _write_json(visual, receipts)

    grouped = build_character_component_ledger(
        ((first, first_sha), (second, second_sha)),
        title_id="infamous-1",
        build_id="bcus98119-v0100",
        candidate_id="zeke",
        visual_receipts=(visual, visual_sha),
        group_cross_page_source_records=True,
    )
    reverse = build_character_component_ledger(
        ((second, second_sha), (first, first_sha)),
        title_id="infamous-1",
        build_id="bcus98119-v0100",
        candidate_id="zeke",
        visual_receipts=(visual, visual_sha),
        group_cross_page_source_records=True,
    )
    legacy = build_character_component_ledger(
        ((first, first_sha), (second, second_sha)),
        title_id="infamous-1",
        build_id="bcus98119-v0100",
        candidate_id="zeke",
    )

    assert grouped == reverse
    assert grouped["version"] == 2
    assert grouped["tool_inventory_id"] == "xpp-tool.character-component-ledger.v2"
    assert grouped["scope"]["cross_page_source_records_grouped"] is True
    assert grouped["scope"]["runtime_pose_observations_preserved"] is True
    assert grouped["counts"]["components"] == 1
    assert grouped["counts"]["material_observations"] == 2
    assert grouped["counts"]["published_render_receipts"] == 2
    assert grouped["counts"]["full_material_coverage_components"] == 1
    component = grouped["components"][0]
    assert component["component_id"] == "infamous-1:bcus98119-v0100:zeke:r100"
    assert component["runtime_pages"] == [1, 2]
    assert "page" not in component
    assert component["source"]["position_payload_sha256s"] == ["3" * 64, "a" * 64]
    assert [row["page"] for row in component["observations"]] == [1, 2]
    assert {row["page"] for row in component["renders"]} == {1, 2}
    assert component["completion"]["full_material_coverage_proved"] is True

    assert legacy["version"] == 1
    assert legacy["tool_inventory_id"] == "xpp-tool.character-component-ledger.v1"
    assert [row["component_id"] for row in legacy["components"]] == [
        "infamous-1:bcus98119-v0100:zeke:p1:r100",
        "infamous-1:bcus98119-v0100:zeke:p2:r100",
    ]


def test_v2_rejects_cross_page_source_drift_and_unknown_render_page(tmp_path: Path):
    first_value = _material(event=1, page=1, record_offset=100)
    second_template = _material(
        event=2,
        page=2,
        record_offset=100,
        position_sha="a" * 64,
        index_sha="2" * 64,
    )
    first = tmp_path / "page1.json"
    first_sha = _write_json(first, first_value)
    with pytest.raises(CharacterComponentLedgerError, match="flag is not boolean"):
        build_character_component_ledger(
            ((first, first_sha),),
            title_id="infamous-1",
            build_id="bcus98119-v0100",
            candidate_id="zeke",
            group_cross_page_source_records="yes",  # type: ignore[arg-type]
        )
    drifted_values = []
    vertex_drift = copy.deepcopy(second_template)
    vertex_drift["selection"]["vertices"] = 13
    drifted_values.append(("vertices", vertex_drift))
    index_drift = copy.deepcopy(second_template)
    index_drift["selection"]["index_sha256"] = "f" * 64
    drifted_values.append(("index", index_drift))
    uv_drift = copy.deepcopy(second_template)
    uv_drift["selection"]["uv_payload_sha256"] = "e" * 64
    drifted_values.append(("uv", uv_drift))
    for label, drifted_value in drifted_values:
        second = tmp_path / f"page2-{label}-drift.json"
        second_sha = _write_json(second, drifted_value)
        with pytest.raises(CharacterComponentLedgerError, match="immutable source"):
            build_character_component_ledger(
                ((first, first_sha), (second, second_sha)),
                title_id="infamous-1",
                build_id="bcus98119-v0100",
                candidate_id="zeke",
                group_cross_page_source_records=True,
            )

    valid_second = tmp_path / "valid-page2.json"
    valid_second_sha = _write_json(valid_second, second_template)
    receipts = _receipts()
    receipts["renders"][0]["page"] = 3
    visual = tmp_path / "unknown-page-visual.json"
    visual_sha = _write_json(visual, receipts)
    with pytest.raises(CharacterComponentLedgerError, match="without material"):
        build_character_component_ledger(
            ((first, first_sha), (valid_second, valid_second_sha)),
            title_id="infamous-1",
            build_id="bcus98119-v0100",
            candidate_id="zeke",
            visual_receipts=(visual, visual_sha),
            group_cross_page_source_records=True,
        )


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


def test_ledger_attaches_exact_material_pass_census_without_merging_pages(
    tmp_path: Path,
):
    material = _material(event=1, record_offset=100, observed=10, unobserved=0)
    material["tool_inventory_id"] = "xpp-tool.character-material-coverage-export.v1"
    material["presentation_mode"] = "observed-union"
    material["authorities"]["coverage_union_sha256"] = "a" * 64
    material["selection"]["material_union_index_sha256"] = "b" * 64
    material["coverage_union"] = {
        "receipt_sha256": "a" * 64,
        "observation_count": 2,
        "covered_retail_triangle_occurrences": 10,
        "unobserved_retail_triangle_occurrences": 0,
        "full_retail_material_coverage_proved": True,
        "covered_triangle_multiset_sha256": "b" * 64,
        "unobserved_triangle_multiset_sha256": "c" * 64,
    }
    material["proof"]["coverage_union_revalidated"] = True
    material["proof"]["exact_union_triangle_material_subset"] = True
    material_path = tmp_path / "union-material.json"
    material_sha = _write_json(material_path, material)
    census_path = tmp_path / "pass-census.json"
    census_sha = _write_json(census_path, _pass_census())

    report = build_character_component_ledger(
        ((material_path, material_sha),),
        title_id="infamous-1",
        build_id="bcus98119-v0100",
        candidate_id="zeke",
        material_pass_censuses=((census_path, census_sha),),
    )

    assert report["counts"]["material_pass_censuses"] == 1
    assert report["counts"]["material_pass_signatures"] == 2
    receipt = report["material_pass_censuses"][0]
    assert receipt["any_pass_union"]["covered_retail_triangle_occurrences"] == 10
    assert receipt["linked_component_ids"] == [
        "infamous-1:bcus98119-v0100:zeke:p2:r100"
    ]
    assert report["components"][0]["material_pass_census_receipts"] == [census_sha]

    grouped = build_character_component_ledger(
        ((material_path, material_sha),),
        title_id="infamous-1",
        build_id="bcus98119-v0100",
        candidate_id="zeke",
        material_pass_censuses=((census_path, census_sha),),
        group_cross_page_source_records=True,
    )
    assert grouped["material_pass_censuses"][0]["linked_component_ids"] == [
        "infamous-1:bcus98119-v0100:zeke:r100"
    ]
    assert grouped["components"][0]["material_pass_census_receipts"] == [census_sha]

    drifted = _pass_census()
    drifted["any_pass_union"]["covered_triangle_multiset_sha256"] = "f" * 64
    drift_path = tmp_path / "drifted-pass-census.json"
    drift_sha = _write_json(drift_path, drifted)
    with pytest.raises(CharacterComponentLedgerError, match="does not reconcile"):
        build_character_component_ledger(
            ((material_path, material_sha),),
            title_id="infamous-1",
            build_id="bcus98119-v0100",
            candidate_id="zeke",
            material_pass_censuses=((drift_path, drift_sha),),
        )

    malformed = _pass_census()
    malformed["relationships"][0]["left_only_triangle_occurrences"] = 3
    malformed_path = tmp_path / "malformed-pass-census.json"
    malformed_sha = _write_json(malformed_path, malformed)
    with pytest.raises(CharacterComponentLedgerError, match="counts or identity"):
        build_character_component_ledger(
            ((material_path, material_sha),),
            title_id="infamous-1",
            build_id="bcus98119-v0100",
            candidate_id="zeke",
            material_pass_censuses=((malformed_path, malformed_sha),),
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
                "material_pass_censuses": 0,
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
            "--group-cross-page-source-records",
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
    assert observed["group_cross_page_source_records"] is True
    assert len(observed["material_reports"]) == 1
    assert observed["material_pass_censuses"] == ()
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
                "--material-report-sha256",
                "a" * 64,
                "--material-pass-census",
                str(tmp_path / "pass-census.json"),
                "--output",
                str(tmp_path / "unpaired.json"),
            ]
        )
        == 1
    )
