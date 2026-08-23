"""Tests for exact material coverage union across repeated character draws."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from infamous_xpp_textures import cli, material_coverage
from infamous_xpp_textures.material_coverage import (
    MaterialCoverageObservation,
    MaterialCoverageUnionError,
    build_material_coverage_union,
    render_material_coverage_union,
    write_new_material_coverage_union,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: dict) -> str:
    payload = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(payload)
    return _sha(payload)


def _material_report(
    *,
    page: int,
    event: int,
    observed: tuple[tuple[int, int, int], ...],
    xpp: bytes,
    retail_index_sha256: str,
    runtime_index_sha256: str,
    record_offset: int = 100,
    family: str = "Zeke_Jacket",
) -> dict:
    observed_count = len(observed)
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
        "presentation_mode": "observed-only",
        "authorities": {
            "xpp_bytes": len(xpp),
            "xpp_sha256": _sha(xpp),
            "lineage_sha256": f"{event:x}" * 64,
            "texture_allowlist_sha256": "2" * 64,
            "capture_key_exclusion_sha256": None,
            "bundle_format": "if1-texture-bound-topology-v3",
        },
        "selection": {
            "page": page,
            "event": event,
            "draw_event": 100 + event,
            "record_offset": record_offset,
            "vertices": 4,
            "triangles": 4,
            "nondegenerate_triangles": 4,
            "material_observed_triangles": observed_count,
            "material_unobserved_triangles": 4 - observed_count,
            "index_sha256": retail_index_sha256,
            "material_event_index_sha256": runtime_index_sha256,
            "position_payload_sha256": "3" * 64,
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
            "full_topology_material_coverage": observed_count == 4,
            "full_character": False,
            "four_x_textures": False,
            "native_pbr": False,
            "rigged": False,
            "rpcs3_mod_round_trip": False,
            "native_decomp_import": False,
        },
    }


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    triangles = ((0, 1, 2), (0, 2, 3), (0, 3, 1), (1, 2, 3))
    xpp = b"".join(struct.pack(">3H", *triangle) for triangle in triangles)
    xpp_path = tmp_path / "retail.xpp"
    xpp_path.write_bytes(xpp)
    allowlist = tmp_path / "allowlist.sha256"
    allowlist.write_text("2" * 64 + "\n")

    payloads: dict[str, bytes] = {}
    observations = []
    for page, event, selected in (
        (1, 1, triangles[:2]),
        (2, 2, triangles[2:]),
    ):
        payload = b"".join(struct.pack(">3H", *triangle) for triangle in selected)
        digest = _sha(payload)
        filename = f"indices-{page}.bin"
        payloads[filename] = payload
        report_path = tmp_path / f"report-{page}.json"
        report_sha = _write_json(
            report_path,
            _material_report(
                page=page,
                event=event,
                observed=selected,
                xpp=xpp,
                retail_index_sha256=_sha(xpp),
                runtime_index_sha256=digest,
            ),
        )
        bundle = tmp_path / f"bundle-{page}"
        bundle.mkdir()
        (bundle / "capture.complete").write_text(f"bundle {page}\n")
        observations.append(
            MaterialCoverageObservation(report_path, report_sha, bundle, None)
        )

    monkeypatch.setattr(
        material_coverage,
        "parse_xpp",
        lambda _payload, _size: SimpleNamespace(data_offset=0),
    )
    monkeypatch.setattr(
        material_coverage,
        "find_skinned_geometry_contracts",
        lambda _payload, _parsed: [
            SimpleNamespace(
                record_offset=100,
                index_offset=0,
                index_byte_count=len(xpp),
                index_count=len(xpp) // 2,
                index_sha256=_sha(xpp),
                vertex_count=4,
            )
        ],
    )

    def fake_bundle(bundle: Path, _allowlist: Path, exclusion: Path | None):
        assert exclusion is None
        page = int(bundle.name.rsplit("-", 1)[1])
        selected = triangles[:2] if page == 1 else triangles[2:]
        payload = b"".join(struct.pack(">3H", *triangle) for triangle in selected)
        event_number = page
        event = SimpleNamespace(
            draw_event=100 + event_number,
            index_sha256=_sha(payload),
            index_count=len(selected) * 3,
            index_bytes=len(payload),
            index_payload_file=f"indices-{page}.bin",
        )
        return (
            {"format": "if1-texture-bound-topology-v3"},
            {event_number: event},
            "2" * 64,
        )

    monkeypatch.setattr(material_coverage, "_load_bundle", fake_bundle)
    monkeypatch.setattr(
        material_coverage,
        "_read_payload",
        lambda _bundle, filename, _size, _sha256: payloads[filename],
    )
    return xpp_path, _sha(xpp), allowlist, observations


def test_union_proves_full_multiset_coverage_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    xpp, xpp_sha, allowlist, observations = _fixture(tmp_path, monkeypatch)
    report = build_material_coverage_union(
        xpp, xpp_sha, allowlist, observations, record_offset=100
    )
    reverse = build_material_coverage_union(
        xpp, xpp_sha, allowlist, tuple(reversed(observations)), record_offset=100
    )

    assert report == reverse
    assert report["status"] == "full-retail-material-coverage-proved"
    assert report["union"] == {
        "observation_count": 2,
        "distinct_runtime_index_payloads": 2,
        "total_observed_triangle_occurrences": 4,
        "redundant_triangle_occurrences": 0,
        "covered_retail_triangle_occurrences": 4,
        "unobserved_retail_triangle_occurrences": 0,
        "full_retail_material_coverage_proved": True,
        "covered_triangle_multiset_sha256": xpp_sha,
        "unobserved_triangle_multiset_sha256": _sha(b""),
    }
    assert report["payload_bytes_serialized"] is False
    assert b"indices-" not in render_material_coverage_union(report)


def test_union_rejects_duplicate_conflicting_and_overwritten_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    xpp, xpp_sha, allowlist, observations = _fixture(tmp_path, monkeypatch)
    with pytest.raises(MaterialCoverageUnionError, match="duplicated"):
        build_material_coverage_union(
            xpp,
            xpp_sha,
            allowlist,
            (observations[0], observations[0]),
            record_offset=100,
        )

    conflicting = json.loads(observations[1].report.read_text())
    conflicting["selection"]["texture_family"] = "Zeke_Head"
    for texture in conflicting["textures"]:
        texture["name"] = texture["name"].replace("Zeke_Jacket", "Zeke_Head")
    conflicting_sha = _write_json(observations[1].report, conflicting)
    changed = MaterialCoverageObservation(
        observations[1].report,
        conflicting_sha,
        observations[1].bundle,
        None,
    )
    with pytest.raises(MaterialCoverageUnionError, match="conflict"):
        build_material_coverage_union(
            xpp,
            xpp_sha,
            allowlist,
            (observations[0], changed),
            record_offset=100,
        )

    report = build_material_coverage_union(
        xpp, xpp_sha, allowlist, (observations[0],), record_offset=100
    )
    output = tmp_path / "union.json"
    write_new_material_coverage_union(output, report)
    with pytest.raises(MaterialCoverageUnionError, match="already exists"):
        write_new_material_coverage_union(output, report)


def test_union_rejects_runtime_triangles_outside_retail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    xpp, xpp_sha, allowlist, observations = _fixture(tmp_path, monkeypatch)
    invalid = struct.pack(">3H", 3, 3, 3) * 2
    monkeypatch.setattr(
        material_coverage,
        "_read_payload",
        lambda _bundle, _filename, _size, _sha256: invalid,
    )
    with pytest.raises(MaterialCoverageUnionError, match="not an exact subset"):
        build_material_coverage_union(
            xpp,
            xpp_sha,
            allowlist,
            (observations[0],),
            record_offset=100,
        )


def test_cli_accepts_bounded_repeatable_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    xpp = tmp_path / "retail.xpp"
    allowlist = tmp_path / "allowlist"
    report = tmp_path / "material.json"
    bundle = tmp_path / "bundle"
    for path in (xpp, allowlist, report):
        path.write_text("fixture\n")
    bundle.mkdir()
    output = tmp_path / "union.json"
    seen = {}

    def fake_build(xpp_path, xpp_sha, allowlist_path, observations, *, record_offset):
        seen.update(
            xpp=xpp_path,
            xpp_sha=xpp_sha,
            allowlist=allowlist_path,
            observations=observations,
            record_offset=record_offset,
        )
        return {
            "component": {"retail_triangle_occurrences": 4},
            "union": {
                "covered_retail_triangle_occurrences": 2,
                "observation_count": 1,
            },
        }

    monkeypatch.setattr(cli, "build_material_coverage_union", fake_build)
    monkeypatch.setattr(
        cli,
        "write_new_material_coverage_union",
        lambda path, _value: path.write_text("ok\n"),
    )
    result = cli.main(
        [
            "character-material-coverage-union",
            "--xpp",
            str(xpp),
            "--xpp-sha256",
            "1" * 64,
            "--texture-allowlist",
            str(allowlist),
            "--record-offset",
            "100",
            "--observation",
            str(report),
            "2" * 64,
            str(bundle),
            "-",
            "--output",
            str(output),
        ]
    )
    assert result == 0
    assert seen["record_offset"] == 100
    assert seen["observations"][0].capture_key_exclusion is None
    assert output.read_text() == "ok\n"
