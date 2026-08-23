"""Tests for the permanent exact cross-material pass census."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace
from collections import Counter

import pytest

from infamous_xpp_textures import cli, material_pass_census
from infamous_xpp_textures.material_coverage import MaterialCoverageObservation
from infamous_xpp_textures.material_pass_census import (
    MAX_OUTPUT_BYTES,
    MaterialPassCensusError,
    _CheckedObservation,
    _relationship,
    build_material_pass_census,
    render_material_pass_census,
    write_new_material_pass_census,
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
    selected: tuple[tuple[int, int, int], ...],
    xpp: bytes,
    runtime_index_sha256: str,
    suffixes: tuple[str, ...],
    fragment_program_sha256: str,
) -> dict:
    family = "Zeke_Hair"
    textures = [
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
        for descriptor, suffix in enumerate(suffixes)
    ]
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
            "record_offset": 100,
            "vertices": 4,
            "triangles": 4,
            "nondegenerate_triangles": 4,
            "material_observed_triangles": len(selected),
            "material_unobserved_triangles": 4 - len(selected),
            "index_sha256": _sha(xpp),
            "material_event_index_sha256": runtime_index_sha256,
            "position_payload_sha256": f"{event + 9:x}" * 64,
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
            "full_topology_material_coverage": len(selected) == 4,
            "full_character": False,
            "four_x_textures": False,
            "native_pbr": False,
            "rigged": False,
            "rpcs3_mod_round_trip": False,
            "native_decomp_import": False,
        },
        "test_fragment_program_sha256": fragment_program_sha256,
    }


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    triangles = ((0, 1, 2), (0, 2, 3), (0, 3, 1), (1, 2, 3))
    xpp = b"".join(struct.pack(">3H", *triangle) for triangle in triangles)
    xpp_path = tmp_path / "retail.xpp"
    xpp_path.write_bytes(xpp)
    allowlist = tmp_path / "allowlist.sha256"
    allowlist.write_text("2" * 64 + "\n")
    definitions = (
        (1, triangles[:2], ("C", "N"), "a" * 64),
        (2, triangles[:2], ("N", "A", "S", "C"), "b" * 64),
        (3, triangles[:1], ("C", "N"), "a" * 64),
        (4, triangles[1:3], ("C", "N"), "a" * 64),
        (5, triangles[3:], ("C", "N"), "c" * 64),
    )
    payloads: dict[str, bytes] = {}
    fragments: dict[int, str] = {}
    observations = []
    for event, selected, suffixes, fragment in definitions:
        payload = b"".join(struct.pack(">3H", *triangle) for triangle in selected)
        payload_name = f"indices-{event}.bin"
        payloads[payload_name] = payload
        fragments[event] = fragment
        report_path = tmp_path / f"report-{event}.json"
        report_sha = _write_json(
            report_path,
            _material_report(
                page=event,
                event=event,
                selected=selected,
                xpp=xpp,
                runtime_index_sha256=_sha(payload),
                suffixes=suffixes,
                fragment_program_sha256=fragment,
            ),
        )
        bundle = tmp_path / f"bundle-{event}"
        bundle.mkdir()
        (bundle / "capture.complete").write_text(f"bundle {event}\n")
        observations.append(
            MaterialCoverageObservation(report_path, report_sha, bundle, None)
        )

    monkeypatch.setattr(
        material_pass_census,
        "parse_xpp",
        lambda _payload, _size: SimpleNamespace(data_offset=0),
    )
    monkeypatch.setattr(
        material_pass_census,
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
        event_number = int(bundle.name.rsplit("-", 1)[1])
        payload = payloads[f"indices-{event_number}.bin"]
        event = SimpleNamespace(
            draw_event=100 + event_number,
            index_sha256=_sha(payload),
            index_count=len(payload) // 2,
            index_bytes=len(payload),
            index_payload_file=f"indices-{event_number}.bin",
            vertex_program_sha256="d" * 64,
            fragment_program_sha256=fragments[event_number],
        )
        return (
            {"format": "if1-texture-bound-topology-v3"},
            {event_number: event},
            "2" * 64,
        )

    monkeypatch.setattr(material_pass_census, "_load_bundle", fake_bundle)
    monkeypatch.setattr(
        material_pass_census,
        "_read_payload",
        lambda _bundle, filename, _size, _sha256: payloads[filename],
    )
    return xpp_path, _sha(xpp), allowlist, tuple(observations)


def test_census_is_deterministic_and_finds_layered_identical_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    xpp, xpp_sha, allowlist, observations = _fixture(tmp_path, monkeypatch)
    report = build_material_pass_census(
        xpp, xpp_sha, allowlist, observations, record_offset=100
    )
    reverse = build_material_pass_census(
        xpp, xpp_sha, allowlist, tuple(reversed(observations)), record_offset=100
    )

    assert report == reverse
    assert report["any_pass_union"]["covered_retail_triangle_occurrences"] == 4
    assert report["any_pass_union"]["unobserved_retail_triangle_occurrences"] == 0
    assert report["any_pass_union"]["pass_signature_count"] == 3
    assert report["any_pass_union"]["runtime_index_payload_count"] == 4
    assert report["any_pass_union"]["relationship_count"] == 10
    assert (
        report["any_pass_union"]["coextensive_cross_signature_relationship_count"] == 1
    )
    assert report["any_pass_union"]["partial_cross_signature_relationship_count"] == 1
    identical = [
        row
        for row in report["relationships"]
        if row["relation"] == "identical"
        and row["same_runtime_index_payload"] is True
        and row["same_pass_signature"] is False
    ]
    assert len(identical) == 1
    assert report["payload_bytes_serialized"] is False
    assert b"indices-" not in render_material_pass_census(report)


def test_relationship_classifies_every_multiset_shape():
    def item(name: str, triangles: tuple[tuple[int, int, int], ...]):
        indices = tuple(vertex for triangle in triangles for vertex in triangle)
        row = {
            "observation_id": name,
            "pass_signature_sha256": name * 64,
            "runtime_index_sha256": _sha(struct.pack(f">{len(indices)}H", *indices)),
        }
        return _CheckedObservation(row, Counter(triangles), (name,))

    a = (0, 1, 2)
    b = (0, 2, 3)
    c = (0, 3, 1)
    full = item("a", (a, b))
    same = item("b", (a, b))
    subset = item("c", (a,))
    partial = item("d", (b, c))
    disjoint = item("e", (c,))
    assert _relationship(full, same)["relation"] == "identical"
    assert _relationship(subset, full)["relation"] == "left-subset"
    assert _relationship(full, subset)["relation"] == "left-superset"
    assert _relationship(full, partial)["relation"] == "partial-overlap"
    assert _relationship(subset, disjoint)["relation"] == "disjoint"


def test_census_rejects_duplicates_bounds_and_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    xpp, xpp_sha, allowlist, observations = _fixture(tmp_path, monkeypatch)
    with pytest.raises(MaterialPassCensusError, match="duplicated"):
        build_material_pass_census(
            xpp,
            xpp_sha,
            allowlist,
            (observations[0], observations[0]),
            record_offset=100,
        )
    with pytest.raises(MaterialPassCensusError, match="count"):
        build_material_pass_census(
            xpp,
            xpp_sha,
            allowlist,
            observations * 7,
            record_offset=100,
        )

    original_bundle_loader = material_pass_census._load_bundle
    with monkeypatch.context() as context:

        def bad_program_bundle(*args, **kwargs):
            completion, events, allowlist_identity = original_bundle_loader(
                *args, **kwargs
            )
            next(iter(events.values())).fragment_program_sha256 = "Z" * 64
            return completion, events, allowlist_identity

        context.setattr(material_pass_census, "_load_bundle", bad_program_bundle)
        with pytest.raises(MaterialPassCensusError, match="fragment-program"):
            build_material_pass_census(
                xpp,
                xpp_sha,
                allowlist,
                observations[:2],
                record_offset=100,
            )

    with monkeypatch.context() as context:
        context.setattr(
            material_pass_census,
            "_read_payload",
            lambda *_args: struct.pack(">3H", 3, 3, 3) * 2,
        )
        with pytest.raises(MaterialPassCensusError, match="not an exact subset"):
            build_material_pass_census(
                xpp,
                xpp_sha,
                allowlist,
                observations[:2],
                record_offset=100,
            )

    report = build_material_pass_census(
        xpp, xpp_sha, allowlist, observations, record_offset=100
    )
    output = tmp_path / "census.json"
    write_new_material_pass_census(output, report)
    original = output.read_bytes()
    with pytest.raises(MaterialPassCensusError, match="already exists"):
        write_new_material_pass_census(output, report)
    assert output.read_bytes() == original
    with pytest.raises(MaterialPassCensusError, match="byte bound"):
        write_new_material_pass_census(
            tmp_path / "oversized.json", {"padding": "x" * MAX_OUTPUT_BYTES}
        )

    conflicting = json.loads(observations[1].report.read_text())
    conflicting["authorities"]["xpp_sha256"] = "f" * 64
    conflicting_sha = _write_json(observations[1].report, conflicting)
    changed = MaterialCoverageObservation(
        observations[1].report,
        conflicting_sha,
        observations[1].bundle,
        None,
    )
    with pytest.raises(MaterialPassCensusError, match="conflicts"):
        build_material_pass_census(
            xpp,
            xpp_sha,
            allowlist,
            (observations[0], changed),
            record_offset=100,
        )


def test_cli_wires_repeatable_pass_observations(tmp_path: Path, monkeypatch):
    xpp = tmp_path / "retail.xpp"
    allowlist = tmp_path / "allowlist"
    report = tmp_path / "material.json"
    bundle = tmp_path / "bundle"
    for path in (xpp, allowlist, report):
        path.write_text("fixture\n")
    bundle.mkdir()
    output = tmp_path / "pass-census.json"
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
            "any_pass_union": {
                "covered_retail_triangle_occurrences": 2,
                "pass_signature_count": 2,
            },
        }

    monkeypatch.setattr(cli, "build_material_pass_census", fake_build)
    monkeypatch.setattr(
        cli,
        "write_new_material_pass_census",
        lambda path, _value: path.write_text("ok\n"),
    )
    argv = [
        "character-material-pass-census",
        "--xpp",
        str(xpp),
        "--xpp-sha256",
        "1" * 64,
        "--texture-allowlist",
        str(allowlist),
        "--record-offset",
        "100",
    ]
    for _ in range(2):
        argv.extend(["--observation", str(report), "2" * 64, str(bundle), "-"])
    argv.extend(("--output", str(output)))
    assert cli.main(argv) == 0
    assert len(seen["observations"]) == 2
    assert seen["record_offset"] == 100
    assert output.read_text() == "ok\n"
