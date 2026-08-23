"""Synthetic tests for the permanent character material candidate census."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from infamous_xpp_textures import cli
from infamous_xpp_textures.character_material_census import (
    CharacterMaterialCensusError,
    build_character_material_candidate_census,
    render_character_material_candidate_census,
    write_new_character_material_candidate_census,
)
from infamous_xpp_textures.shader_lineage import ShaderLineageError


def _write_json(path: Path, value: dict) -> str:
    payload = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _authorities(tmp_path: Path) -> tuple[Path, str, Path, str]:
    source = tmp_path / "source.json"
    source_sha = _write_json(
        source,
        {
            "kind": "if1-rsx-paged-xpp-source-census",
            "schema_version": 1,
            "events": [
                {
                    "page": 2,
                    "event": 1,
                    "same_xpp_source_record_proved": True,
                    "mapping": {
                        "full_vertex_range": True,
                        "range_count": 10,
                        "source_vertex_count": 10,
                        "record_offset": 100,
                        "stream_zero_record_bytes": 8,
                    },
                },
                {
                    "page": 2,
                    "event": 2,
                    "same_xpp_source_record_proved": True,
                    "mapping": {
                        "full_vertex_range": True,
                        "range_count": 20,
                        "source_vertex_count": 20,
                        "record_offset": 200,
                        "stream_zero_record_bytes": 10,
                    },
                },
                {
                    "page": 2,
                    "event": 3,
                    "same_xpp_source_record_proved": True,
                    "mapping": {
                        "full_vertex_range": False,
                        "range_count": 29,
                        "source_vertex_count": 30,
                        "record_offset": 300,
                        "stream_zero_record_bytes": 10,
                    },
                },
                {
                    "page": 2,
                    "event": 4,
                    "same_xpp_source_record_proved": True,
                    "mapping": {
                        "full_vertex_range": True,
                        "range_count": 40,
                        "source_vertex_count": 40,
                        "record_offset": 400,
                        "stream_zero_record_bytes": 12,
                    },
                },
                {"page": 1, "event": 1, "same_xpp_source_record_proved": False},
            ],
        },
    )
    character = tmp_path / "character.json"
    character_sha = _write_json(
        character,
        {"format": "infamous-character-asset-census"},
    )
    return source, source_sha, character, character_sha


def _accepted_lineage(event: int, record_offset: int) -> dict:
    return {
        "status": "exact-shader-lineage-with-unique-packed-layout",
        "selection": {"source_block": 3},
        "shader_lineage": {
            "vertex_input_attribute": 9,
            "vertex_input_type": 3,
            "vertex_input_components": 2,
            "vertex_input_byte_offset": 4,
            "fragment_input_name": "TEX0",
        },
        "texture_family": f"Character_{record_offset}",
        "texture_bindings": [
            {
                "sampler": 0,
                "name": f"Character_{record_offset}_C.psd",
                "name_suffix": "C",
                "runtime_prefix_sha256": f"{event:x}" * 64,
            }
        ],
    }


def test_batch_classifies_full_range_candidates_and_honors_exact_exclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, source_sha, character, character_sha = _authorities(tmp_path)
    monkeypatch.setattr(
        "infamous_xpp_textures.character_material_census._load_bundle",
        lambda *args: (
            {"format": "if1-texture-bound-topology-v4"},
            {},
            "a" * 64,
        ),
    )

    def fake_lineage(*args, event_number: int, record_offset: int, **kwargs):
        del args, kwargs
        if event_number == 2:
            raise ShaderLineageError(
                "sampled vertex output has ambiguous input lineage"
            )
        return _accepted_lineage(event_number, record_offset)

    monkeypatch.setattr(
        "infamous_xpp_textures.character_material_census.build_character_uv_texture_binding",
        fake_lineage,
    )
    report = build_character_material_candidate_census(
        tmp_path / "bundle",
        tmp_path / "allowlist",
        None,
        source,
        source_sha,
        character,
        character_sha,
        page_number=2,
        character_side="left",
        excluded_candidates=((4, 400),),
    )

    assert report["selection"] == {
        "page": 2,
        "character_side": "left",
        "eligible_full_range_candidates": 3,
        "excluded_completed_candidates": [{"event": 4, "record_offset": 400}],
        "selected_candidates": 2,
    }
    assert [item["record_offset"] for item in report["accepted"]] == [100]
    assert report["accepted"][0]["texture_family"] == "Character_100"
    assert [item["record_offset"] for item in report["rejected"]] == [200]
    assert report["rejected"][0]["reason"] == (
        "sampled vertex output has ambiguous input lineage"
    )
    assert report["summary"] == {
        "accepted": 1,
        "rejected": 1,
        "all_selected_candidates_classified": True,
    }
    assert report["payload_bytes_serialized"] is False
    assert render_character_material_candidate_census(report) == (
        render_character_material_candidate_census(report)
    )

    output = tmp_path / "candidate-census.json"
    write_new_character_material_candidate_census(output, report)
    first = output.read_bytes()
    with pytest.raises(CharacterMaterialCensusError, match="already exists"):
        write_new_character_material_candidate_census(output, report)
    assert output.read_bytes() == first


def test_batch_rejects_unknown_duplicate_or_empty_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, source_sha, character, character_sha = _authorities(tmp_path)
    monkeypatch.setattr(
        "infamous_xpp_textures.character_material_census._load_bundle",
        lambda *args: (
            {"format": "if1-texture-bound-topology-v4"},
            {},
            "a" * 64,
        ),
    )
    common = (
        tmp_path / "bundle",
        tmp_path / "allowlist",
        None,
        source,
        source_sha,
        character,
        character_sha,
    )
    with pytest.raises(CharacterMaterialCensusError, match="duplicates"):
        build_character_material_candidate_census(
            *common,
            page_number=2,
            character_side="left",
            excluded_candidates=((1, 100), (1, 100)),
        )
    with pytest.raises(CharacterMaterialCensusError, match="not eligible"):
        build_character_material_candidate_census(
            *common,
            page_number=2,
            character_side="left",
            excluded_candidates=((3, 300),),
        )
    with pytest.raises(CharacterMaterialCensusError, match="selected no new"):
        build_character_material_candidate_census(
            *common,
            page_number=2,
            character_side="left",
            excluded_candidates=((1, 100), (2, 200), (4, 400)),
        )


def test_cli_registers_candidate_census_and_parses_exact_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.json"
    observed: dict = {}

    def fake_build(*args, **kwargs):
        del args
        observed.update(kwargs)
        return {"summary": {"accepted": 3, "rejected": 0}}

    monkeypatch.setattr(cli, "build_character_material_candidate_census", fake_build)
    monkeypatch.setattr(
        cli,
        "write_new_character_material_candidate_census",
        lambda path, report: path.write_text(json.dumps(report)),
    )
    exit_code = cli.main(
        [
            "character-material-candidate-census",
            "--bundle",
            str(tmp_path / "bundle"),
            "--texture-allowlist",
            str(tmp_path / "allowlist"),
            "--page",
            "2",
            "--source-census",
            str(tmp_path / "source.json"),
            "--source-census-sha256",
            "a" * 64,
            "--character-census",
            str(tmp_path / "character.json"),
            "--character-census-sha256",
            "b" * 64,
            "--character-side",
            "left",
            "--exclude-candidate",
            "5:536488",
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    assert observed["excluded_candidates"] == ((5, 536488),)
    assert "3 accepted / 0 rejected" in capsys.readouterr().out
