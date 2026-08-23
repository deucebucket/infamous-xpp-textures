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
    PartialMaterialCoverageObservation,
    build_material_coverage_union,
    build_material_coverage_union_with_indices,
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

    with_indices, covered_indices = build_material_coverage_union_with_indices(
        xpp, xpp_sha, allowlist, observations, record_offset=100
    )
    assert with_indices == report
    assert covered_indices == (0, 1, 2, 0, 2, 3, 0, 3, 1, 1, 2, 3)


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


def _partial_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    xpp: Path,
    xpp_sha: str,
    base_loader,
    base_reader,
) -> PartialMaterialCoverageObservation:
    retail_indices = xpp.read_bytes()
    observed_indices = retail_indices[:6]
    uv_payload = b"\x00" * 24
    source_census = tmp_path / "partial-source.json"
    source_value = {
        "kind": "if1-rsx-paged-xpp-source-census",
        "schema_version": 1,
        "source": {
            "source": "retail.xpp",
            "source_sha256": xpp_sha,
            "source_size": len(retail_indices),
            "records": [
                {
                    "record_offset": 100,
                    "vertex_count": 4,
                    "index_count": 12,
                    "index_sha256": _sha(retail_indices),
                }
            ],
        },
        "events": [
            {
                "page": 1,
                "event": 3,
                "same_xpp_source_record_proved": True,
                "mapping": {
                    "record_offset": 100,
                    "block": 3,
                    "range_first": 0,
                    "range_count": 3,
                    "range_end": 3,
                    "source_vertex_count": 4,
                    "full_vertex_range": False,
                    "matched_stream_slice_sha256": _sha(uv_payload),
                    "stream_zero_record_bytes": 8,
                    "runtime_index_coverage": {
                        "status": "retail-triangle-subset-proved",
                        "safe_for_retail_coverage_union": True,
                        "runtime_indices_within_mapped_vertex_range": True,
                        "runtime_index_sha256": _sha(observed_indices),
                        "runtime_triangle_occurrences": 1,
                        "runtime_min_vertex_index": 0,
                        "runtime_max_vertex_index": 2,
                        "covered_retail_triangle_occurrences": 1,
                        "unobserved_retail_triangle_occurrences": 3,
                        "covered_triangle_multiset_sha256": _sha(observed_indices),
                        "unobserved_triangle_multiset_sha256": _sha(retail_indices[6:]),
                    },
                },
            }
        ],
    }
    source_sha = _write_json(source_census, source_value)
    lineage = tmp_path / "partial-lineage.json"
    bindings = [
        {
            "descriptor_index": descriptor,
            "name": f"Zeke_Jacket_{suffix}.psd",
            "family": "Zeke_Jacket",
            "name_suffix": suffix,
            "sampler": descriptor,
            "format": "0x86",
            "width": 256,
            "height": 256,
            "faces": 1,
            "matched_mip_level": 0,
            "matched_prefix_bytes": 100 + descriptor,
            "runtime_prefix_sha256": f"{descriptor + 8:x}" * 64,
        }
        for descriptor, suffix in enumerate(("C", "N"))
    ]
    character_census = tmp_path / "partial-character.json"
    character_value = {
        "format": "infamous-character-asset-census",
        "version": 1,
        "targets": {
            "left": {
                "relative_path": "retail.xpp",
                "sha256": xpp_sha,
                "bytes": len(retail_indices),
            }
        },
        "target_texture_descriptors": {
            "left": [
                {
                    "index": binding["descriptor_index"],
                    "name": binding["name"],
                    "family": binding["family"],
                    "name_suffix": binding["name_suffix"],
                    "format": binding["format"],
                    "width": binding["width"],
                    "height": binding["height"],
                    "faces": binding["faces"],
                    "mip_rows": [
                        {
                            "level": binding["matched_mip_level"],
                            "prefix_bytes": binding["matched_prefix_bytes"],
                            "prefix_sha256": binding["runtime_prefix_sha256"],
                        }
                    ],
                }
                for binding in bindings
            ]
        },
    }
    character_sha = _write_json(character_census, character_value)
    lineage_value = {
        "format": "infamous-character-uv-texture-binding",
        "version": 1,
        "tool_inventory_id": "xpp-tool.character-uv-texture-binding.v1",
        "status": "exact-partial-shader-lineage-with-unique-packed-layout",
        "authorities": {
            "bundle_format": "if1-texture-bound-topology-v3",
            "texture_allowlist_sha256": "2" * 64,
            "source_census_sha256": source_sha,
            "character_census_sha256": character_sha,
            "source_xpp_sha256": xpp_sha,
            "source_xpp_bytes": len(retail_indices),
            "character_target": "retail.xpp",
        },
        "selection": {
            "page": 1,
            "event": 3,
            "draw_event": 103,
            "record_offset": 100,
            "source_stream_index": 0,
            "source_block": 3,
            "source_stream_sha256": _sha(uv_payload),
            "source_stream_stride": 8,
            "vertex_count": 3,
            "source_vertex_count": 4,
            "source_range_first": 0,
            "source_range_count": 3,
            "source_range_end": 3,
            "vertex_program_sha256": "b" * 64,
            "fragment_program_sha256": "c" * 64,
        },
        "shader_lineage": {
            "vertex_input_attribute": 9,
            "vertex_input_type": 3,
            "vertex_input_components": 2,
            "vertex_input_byte_offset": 4,
            "fragment_input_name": "TEX0",
        },
        "proof": {
            key: True
            for key in (
                "same_xpp_source_record",
                "exact_source_stream_bytes",
                "exact_shader_payloads",
                "target_sampler_coordinate_input",
                "component_level_vertex_lineage",
                "named_texture_identity",
                "two_dimensional_texture_coordinate_semantic",
                "packed_layout_uniquely_reconstructed",
                "geometry_to_uv_to_texture_binding",
                "partial_source_vertex_range",
                "runtime_indices_within_source_range",
                "runtime_retail_triangle_subset",
                "safe_for_material_coverage_union",
            )
        }
        | {"full_source_vertex_range": False},
        "partial_runtime_coverage": {
            "safe_for_material_coverage_union": True,
            "runtime_index_sha256": _sha(observed_indices),
            "runtime_triangle_occurrences": 1,
            "covered_retail_triangle_occurrences": 1,
            "unobserved_retail_triangle_occurrences": 3,
            "runtime_min_vertex_index": 0,
            "runtime_max_vertex_index": 2,
            "covered_triangle_multiset_sha256": _sha(observed_indices),
            "unobserved_triangle_multiset_sha256": _sha(retail_indices[6:]),
        },
        "texture_family": "Zeke_Jacket",
        "texture_bindings": bindings,
    }
    lineage_sha = _write_json(lineage, lineage_value)
    bundle = tmp_path / "bundle-partial"
    bundle.mkdir()
    (bundle / "capture.complete").write_text("partial bundle\n")
    block = SimpleNamespace(
        number=3,
        payload_file="partial-uv.bin",
        payload_bytes=len(uv_payload),
        payload_sha256=_sha(uv_payload),
        stride=8,
        range_first=0,
        range_count=3,
    )
    event = SimpleNamespace(
        draw_event=103,
        index_sha256=_sha(observed_indices),
        index_count=3,
        index_bytes=len(observed_indices),
        index_payload_file="partial-indices.bin",
        vertex_program_sha256="b" * 64,
        fragment_program_sha256="c" * 64,
        target_texture_slots=(0, 1),
        target_texture_sha256s=("8" * 64, "9" * 64),
        blocks=(block,),
    )

    def partial_loader(bundle_path, *args):
        if bundle_path == bundle:
            return (
                {"format": "if1-texture-bound-topology-v3"},
                {3: event},
                "2" * 64,
            )
        return base_loader(bundle_path, *args)

    def partial_reader(bundle_path, filename, *args):
        if bundle_path == bundle:
            return {
                "partial-uv.bin": uv_payload,
                "partial-indices.bin": observed_indices,
            }[filename]
        return base_reader(bundle_path, filename, *args)

    monkeypatch.setattr(material_coverage, "_load_bundle", partial_loader)
    monkeypatch.setattr(material_coverage, "_read_payload", partial_reader)
    return PartialMaterialCoverageObservation(
        lineage=lineage,
        lineage_sha256=lineage_sha,
        bundle=bundle,
        capture_key_exclusion=None,
        source_census=source_census,
        source_census_sha256=source_sha,
        character_census=character_census,
        character_census_sha256=character_sha,
    )


def test_safe_partial_lineage_advances_union_without_becoming_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    xpp, xpp_sha, allowlist, observations = _fixture(tmp_path, monkeypatch)
    base_loader = material_coverage._load_bundle
    base_reader = material_coverage._read_payload
    partial = _partial_fixture(
        tmp_path,
        monkeypatch,
        xpp=xpp,
        xpp_sha=xpp_sha,
        base_loader=base_loader,
        base_reader=base_reader,
    )

    report = build_material_coverage_union(
        xpp,
        xpp_sha,
        allowlist,
        (observations[1],),
        record_offset=100,
        partial_observations=(partial,),
    )

    assert report["union"]["covered_retail_triangle_occurrences"] == 3
    assert report["union"]["unobserved_retail_triangle_occurrences"] == 1
    assert report["union"]["full_range_observation_count"] == 1
    assert report["union"]["partial_range_observation_count"] == 1
    partial_row = next(
        item
        for item in report["observations"]
        if item["evidence_kind"] == "safe-partial-range-shader-lineage"
    )
    assert partial_row["source_range_count"] == 3
    assert partial_row["new_triangle_occurrences"] == 1


def test_partial_lineage_rejects_texture_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    xpp, xpp_sha, allowlist, observations = _fixture(tmp_path, monkeypatch)
    base_loader = material_coverage._load_bundle
    base_reader = material_coverage._read_payload
    partial = _partial_fixture(
        tmp_path,
        monkeypatch,
        xpp=xpp,
        xpp_sha=xpp_sha,
        base_loader=base_loader,
        base_reader=base_reader,
    )
    value = json.loads(partial.lineage.read_text())
    value["texture_bindings"][0]["name"] = "Zeke_Head_C.psd"
    changed_sha = _write_json(partial.lineage, value)
    changed = PartialMaterialCoverageObservation(
        lineage=partial.lineage,
        lineage_sha256=changed_sha,
        bundle=partial.bundle,
        capture_key_exclusion=None,
        source_census=partial.source_census,
        source_census_sha256=partial.source_census_sha256,
        character_census=partial.character_census,
        character_census_sha256=partial.character_census_sha256,
    )

    with pytest.raises(MaterialCoverageUnionError, match="character census"):
        build_material_coverage_union(
            xpp,
            xpp_sha,
            allowlist,
            (observations[1],),
            record_offset=100,
            partial_observations=(changed,),
        )


def test_partial_lineage_rejects_dishonest_triangle_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    xpp, xpp_sha, allowlist, observations = _fixture(tmp_path, monkeypatch)
    base_loader = material_coverage._load_bundle
    base_reader = material_coverage._read_payload
    partial = _partial_fixture(
        tmp_path,
        monkeypatch,
        xpp=xpp,
        xpp_sha=xpp_sha,
        base_loader=base_loader,
        base_reader=base_reader,
    )
    source_value = json.loads(partial.source_census.read_text())
    dishonest_hash = "f" * 64
    source_value["events"][0]["mapping"]["runtime_index_coverage"][
        "covered_triangle_multiset_sha256"
    ] = dishonest_hash
    source_sha = _write_json(partial.source_census, source_value)
    lineage_value = json.loads(partial.lineage.read_text())
    lineage_value["authorities"]["source_census_sha256"] = source_sha
    lineage_value["partial_runtime_coverage"]["covered_triangle_multiset_sha256"] = (
        dishonest_hash
    )
    lineage_sha = _write_json(partial.lineage, lineage_value)
    changed = PartialMaterialCoverageObservation(
        lineage=partial.lineage,
        lineage_sha256=lineage_sha,
        bundle=partial.bundle,
        capture_key_exclusion=None,
        source_census=partial.source_census,
        source_census_sha256=source_sha,
        character_census=partial.character_census,
        character_census_sha256=partial.character_census_sha256,
    )

    with pytest.raises(MaterialCoverageUnionError, match="retail triangle partition"):
        build_material_coverage_union(
            xpp,
            xpp_sha,
            allowlist,
            (observations[1],),
            record_offset=100,
            partial_observations=(changed,),
        )


def test_cli_accepts_bounded_repeatable_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    xpp = tmp_path / "retail.xpp"
    allowlist = tmp_path / "allowlist"
    report = tmp_path / "material.json"
    bundle = tmp_path / "bundle"
    partial_lineage = tmp_path / "partial.json"
    partial_source = tmp_path / "source.json"
    partial_character = tmp_path / "character.json"
    partial_bundle = tmp_path / "partial-bundle"
    for path in (
        xpp,
        allowlist,
        report,
        partial_lineage,
        partial_source,
        partial_character,
    ):
        path.write_text("fixture\n")
    bundle.mkdir()
    partial_bundle.mkdir()
    output = tmp_path / "union.json"
    seen = {}

    def fake_build(
        xpp_path,
        xpp_sha,
        allowlist_path,
        observations,
        *,
        record_offset,
        partial_observations=(),
    ):
        seen.update(
            xpp=xpp_path,
            xpp_sha=xpp_sha,
            allowlist=allowlist_path,
            observations=observations,
            record_offset=record_offset,
            partial_observations=partial_observations,
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
            "--partial-observation",
            str(partial_lineage),
            "3" * 64,
            str(partial_bundle),
            "-",
            str(partial_source),
            "4" * 64,
            str(partial_character),
            "5" * 64,
            "--output",
            str(output),
        ]
    )
    assert result == 0
    assert seen["record_offset"] == 100
    assert seen["observations"][0].capture_key_exclusion is None
    assert len(seen["partial_observations"]) == 1
    partial = seen["partial_observations"][0]
    assert partial.lineage == partial_lineage
    assert partial.source_census == partial_source
    assert partial.character_census == partial_character
    assert partial.capture_key_exclusion is None
    assert output.read_text() == "ok\n"
