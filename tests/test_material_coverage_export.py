"""Tests for the permanent repeated-draw material-union GLB exporter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from infamous_xpp_textures import cli, material_coverage_export
from infamous_xpp_textures.material_coverage import MaterialCoverageObservation
from infamous_xpp_textures.material_coverage_export import (
    MaterialCoverageExportError,
    build_material_coverage_export,
    write_new_material_coverage_export,
)


def _observations(tmp_path: Path) -> tuple[MaterialCoverageObservation, ...]:
    result = []
    for index, digest in enumerate(("b" * 64, "c" * 64), start=1):
        bundle = tmp_path / f"bundle-{index}"
        bundle.mkdir()
        result.append(
            MaterialCoverageObservation(
                report=tmp_path / f"report-{index}.json",
                report_sha256=digest,
                bundle=bundle,
                capture_key_exclusion=None,
            )
        )
    return tuple(result)


def _union(anchor_sha256: str) -> dict:
    return {
        "observations": [
            {
                "lineage_sha256": anchor_sha256,
                "material_report_sha256": "b" * 64,
            },
            {
                "lineage_sha256": "d" * 64,
                "material_report_sha256": "c" * 64,
            },
        ]
    }


def test_wrapper_selects_exact_anchor_and_is_deterministic(tmp_path, monkeypatch):
    observations = _observations(tmp_path)
    anchor_sha = "a" * 64
    union = _union(anchor_sha)
    indices = (0, 1, 2, 0, 2, 3)
    seen = []

    monkeypatch.setattr(
        material_coverage_export,
        "build_material_coverage_union_with_indices",
        lambda *args, **kwargs: (union, indices),
    )
    monkeypatch.setattr(
        material_coverage_export,
        "render_material_coverage_union",
        lambda value: (json.dumps(value, sort_keys=True) + "\n").encode(),
    )
    monkeypatch.setattr(material_coverage_export, "_read_pinned", lambda *args: b"xpp")

    def fake_export(*args, **kwargs):
        seen.append((args, kwargs))
        return b"glb", {"selection": {"material_observed_triangles": 2}}

    monkeypatch.setattr(
        material_coverage_export, "build_character_material_export", fake_export
    )
    parameters = dict(
        xpp_path=tmp_path / "retail.xpp",
        xpp_sha256="e" * 64,
        texture_allowlist=tmp_path / "allowlist",
        observations=observations,
        record_offset=100,
        anchor_lineage=tmp_path / "anchor.json",
        anchor_lineage_sha256=anchor_sha,
    )
    assert build_material_coverage_export(
        **parameters
    ) == build_material_coverage_export(**parameters)
    assert len(seen) == 2
    assert all(call[0][1] == observations[0].bundle for call in seen)
    assert all(call[1]["material_indices_override"] == indices for call in seen)
    assert all(
        call[1]["tool_inventory_id"] == "xpp-tool.character-material-coverage-export.v1"
        for call in seen
    )


def test_wrapper_rejects_ambiguous_anchor(tmp_path, monkeypatch):
    observations = _observations(tmp_path)
    anchor_sha = "a" * 64
    union = _union(anchor_sha)
    union["observations"][1]["lineage_sha256"] = anchor_sha
    monkeypatch.setattr(
        material_coverage_export,
        "build_material_coverage_union_with_indices",
        lambda *args, **kwargs: (union, (0, 1, 2)),
    )
    monkeypatch.setattr(
        material_coverage_export,
        "render_material_coverage_union",
        lambda _value: b"union\n",
    )
    with pytest.raises(MaterialCoverageExportError, match="exactly one"):
        build_material_coverage_export(
            tmp_path / "retail.xpp",
            "e" * 64,
            tmp_path / "allowlist",
            observations,
            record_offset=100,
            anchor_lineage=tmp_path / "anchor.json",
            anchor_lineage_sha256=anchor_sha,
        )


def test_atomic_writer_and_cli_preserve_outputs(tmp_path, monkeypatch):
    glb_path = tmp_path / "union.glb"
    report_path = tmp_path / "union.json"
    report = {
        "selection": {"material_observed_triangles": 2},
        "glb": {"bytes": 3, "sha256": hashlib.sha256(b"glb").hexdigest()},
    }
    write_new_material_coverage_export(glb_path, report_path, b"glb", report)
    original = (glb_path.read_bytes(), report_path.read_bytes())
    with pytest.raises(MaterialCoverageExportError, match="already exists"):
        write_new_material_coverage_export(glb_path, report_path, b"changed", report)
    assert (glb_path.read_bytes(), report_path.read_bytes()) == original

    observations = _observations(tmp_path)
    output_glb = tmp_path / "cli.glb"
    output_report = tmp_path / "cli.json"
    seen = {}

    def fake_build(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return b"cli", {
            "selection": {
                "material_observed_triangles": 2,
                "triangles": 4,
            },
            "coverage_union": {"observation_count": 2},
        }

    monkeypatch.setattr(cli, "build_material_coverage_export", fake_build)
    monkeypatch.setattr(
        cli,
        "write_new_material_coverage_export",
        lambda glb, json_path, *_args: (
            glb.write_bytes(b"cli"),
            json_path.write_text("{}\n"),
        ),
    )
    argv = [
        "character-material-coverage-export",
        "--xpp",
        str(tmp_path / "retail.xpp"),
        "--xpp-sha256",
        "e" * 64,
        "--texture-allowlist",
        str(tmp_path / "allowlist"),
        "--record-offset",
        "100",
        "--anchor-lineage",
        str(tmp_path / "anchor.json"),
        "--anchor-lineage-sha256",
        "a" * 64,
    ]
    for item in observations:
        argv.extend(
            [
                "--observation",
                str(item.report),
                item.report_sha256,
                str(item.bundle),
                "-",
            ]
        )
    partial_lineage = tmp_path / "partial.json"
    partial_bundle = tmp_path / "partial-bundle"
    partial_source = tmp_path / "source.json"
    partial_character = tmp_path / "character.json"
    partial_bundle.mkdir()
    argv.extend(
        [
            "--partial-observation",
            str(partial_lineage),
            "1" * 64,
            str(partial_bundle),
            "-",
            str(partial_source),
            "2" * 64,
            str(partial_character),
            "3" * 64,
        ]
    )
    argv.extend(
        ["--output-glb", str(output_glb), "--output-report", str(output_report)]
    )
    assert cli.main(argv) == 0
    assert len(seen["args"][3]) == 2
    assert len(seen["kwargs"]["partial_observations"]) == 1
    partial = seen["kwargs"]["partial_observations"][0]
    assert partial.lineage == partial_lineage
    assert partial.character_census == partial_character
    assert output_glb.read_bytes() == b"cli"
