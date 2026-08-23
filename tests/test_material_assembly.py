"""Tests for strict same-page material-component assembly."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

import pytest

from infamous_xpp_textures.character_source_export import _pack_glb
from infamous_xpp_textures.cli import main
from infamous_xpp_textures.material_assembly import (
    MaterialAssemblyError,
    MaterialAssemblyInput,
    build_character_material_assembly,
    render_material_assembly_report,
    write_new_material_assembly,
)
from infamous_xpp_textures.material_gap_locator import _parse_glb
from infamous_xpp_textures.mesh import GlbBuilder
from infamous_xpp_textures.pngio import encode_png


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _component(record: int, center: list[float], page: int = 2) -> tuple[bytes, bytes]:
    source = [
        (center[0] - 1.0, center[1] - 1.0, center[2]),
        (center[0] + 1.0, center[1] - 1.0, center[2]),
        (center[0], center[1] + 1.0, center[2]),
    ]
    positions = [
        (row[0] - center[0], row[2] - center[2], -(row[1] - center[1]))
        for row in source
    ]
    normals = [(0.0, 1.0, 0.0)] * 3
    uvs = [(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)]
    indices = (0, 1, 2)
    png = encode_png(1, 1, bytes((80, 60, 40, 255)))
    builder = GlbBuilder()
    position_accessor = builder.add_accessor(
        b"".join(struct.pack("<3f", *row) for row in positions),
        5126,
        3,
        "VEC3",
        34962,
        [-1.0, 0.0, -1.0],
        [1.0, 0.0, 1.0],
    )
    normal_accessor = builder.add_accessor(
        b"".join(struct.pack("<3f", *row) for row in normals),
        5126,
        3,
        "VEC3",
        34962,
    )
    uv_accessor = builder.add_accessor(
        b"".join(struct.pack("<2f", *row) for row in uvs),
        5126,
        3,
        "VEC2",
        34962,
        [0.0, 0.0],
        [1.0, 1.0],
    )
    index_accessor = builder.add_accessor(
        struct.pack("<3H", *indices), 5123, 3, "SCALAR", 34963, [0], [2]
    )
    image_view = builder.add_view(png)
    family = f"Zeke_Test{record}"
    texture_name = f"{family}_C.psd"
    evidence = {
        "diagnosticOnly": True,
        "recordOffset": record,
        "topologyProved": True,
        "observedMaterialTriangles": 1,
        "unobservedMaterialTriangles": 0,
        "fullTopologyMaterialCoverageProved": True,
        "unobservedMaterialPreviewExtrapolated": False,
        "uvProved": True,
        "retailTextureIdentitiesProved": True,
        "textureFamily": family,
        "shaderBoundTextureSuffixes": ["C"],
        "unassignedTextureSuffixes": [],
        "materialRolesFromRetailNames": True,
        "extraTextureRolesAssigned": False,
        "positionHypothesisAttribute": 0,
        "positionSemanticProved": False,
        "generatedInspectionNormals": True,
        "retailNormalsProved": False,
        "nativePbrProved": False,
        "fullCharacterProved": False,
        "rpcs3RoundTripProved": False,
        "nativeImportProved": False,
    }
    document = {
        "asset": {
            "version": "2.0",
            "extras": {"infamousMaterialEvidence": evidence},
        },
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {
                "mesh": 0,
                "name": f"{family} retail-material diagnostic",
                "extras": {"infamousMaterialEvidence": evidence},
            }
        ],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": position_accessor,
                            "NORMAL": normal_accessor,
                            "TEXCOORD_0": uv_accessor,
                        },
                        "indices": index_accessor,
                        "material": 0,
                        "mode": 4,
                        "extras": {
                            "materialBinding": "runtime-observed exact triangle subset"
                        },
                    }
                ]
            }
        ],
        "materials": [
            {
                "name": f"{family} material",
                "pbrMetallicRoughness": {
                    "baseColorTexture": {"index": 0, "texCoord": 0},
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            }
        ],
        "textures": [{"sampler": 0, "source": 0, "name": texture_name}],
        "images": [
            {"bufferView": image_view, "mimeType": "image/png", "name": texture_name}
        ],
        "samplers": [{"wrapS": 10497, "wrapT": 10497}],
        "bufferViews": builder.views,
        "accessors": builder.accessors,
    }
    glb = _pack_glb(document, builder.binary)
    report = {
        "format": "infamous-character-material-export",
        "version": 1,
        "tool_inventory_id": "xpp-tool.character-material-export.v1",
        "status": "retail-material-progress-glb-written",
        "presentation_mode": "observed-only",
        "authorities": {
            "xpp_sha256": "1" * 64,
            "xpp_bytes": 1000,
            "lineage_sha256": "2" * 64,
            "texture_allowlist_sha256": "3" * 64,
            "capture_key_exclusion_sha256": None,
        },
        "selection": {
            "page": page,
            "event": record % 16 + 1,
            "draw_event": 10000 + record,
            "record_offset": record,
            "vertices": 3,
            "triangles": 1,
            "material_observed_triangles": 1,
            "material_unobserved_triangles": 0,
            "nondegenerate_triangles": 1,
            "index_sha256": "4" * 64,
            "material_event_index_sha256": "5" * 64,
            "position_payload_sha256": "6" * 64,
            "uv_payload_sha256": "7" * 64,
            "uv_byte_offset": 4,
            "texture_family": family,
            "shader_bound_texture_count": 1,
            "display_assigned_texture_suffixes": ["C"],
            "unassigned_texture_suffixes": [],
        },
        "textures": [
            {
                "descriptor_index": 0,
                "name": texture_name,
                "suffix": "C",
                "width": 1,
                "height": 1,
                "decoded_rgba_sha256": "8" * 64,
                "embedded_png_sha256": _sha(png),
                "runtime_prefix_sha256": "9" * 64,
            }
        ],
        "source_position_bounds": {
            "minimum": [center[0] - 1.0, center[1] - 1.0, center[2]],
            "maximum": [center[0] + 1.0, center[1] + 1.0, center[2]],
        },
        "recentered_position_center": center,
        "glb": {"bytes": len(glb), "sha256": _sha(glb)},
        "proof": {
            "exact_retail_topology": True,
            "exact_full_vertex_range": True,
            "shader_proved_texcoord_0": True,
            "exact_uv_rows": True,
            "runtime_prefix_to_retail_descriptor": True,
            "deterministic_material_glb": True,
        },
        "limitations": {
            "position_attribute_is_diagnostic_hypothesis": True,
            "position_semantic": False,
            "generated_inspection_normals_are_retail_normals": False,
            "full_character": False,
            "four_x_textures": False,
            "native_pbr": False,
            "rigged": False,
            "rpcs3_mod_round_trip": False,
            "native_decomp_import": False,
            "full_topology_material_coverage": True,
        },
        "payload_bytes_serialized_in_report": False,
    }
    report_payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    return glb, report_payload


def _write_component(
    root: Path, record: int, center: list[float], page: int = 2
) -> MaterialAssemblyInput:
    glb, report = _component(record, center, page)
    glb_path = root / f"{record}.glb"
    report_path = root / f"{record}.json"
    glb_path.write_bytes(glb)
    report_path.write_bytes(report)
    return MaterialAssemblyInput(report_path, _sha(report), glb_path, _sha(glb))


def _build(inputs: list[MaterialAssemblyInput]) -> tuple[bytes, dict]:
    return build_character_material_assembly(
        inputs,
        title_id="infamous-1",
        build_id="bcus98119-v0100",
        candidate_id="zeke",
        page=2,
    )


def _add_gap(component: MaterialAssemblyInput, root: Path) -> MaterialAssemblyInput:
    glb = component.glb.read_bytes()
    document, binary = _parse_glb(glb)
    primitive = document["meshes"][0]["primitives"][0]
    gap = json.loads(json.dumps(primitive))
    gap["material"] = 1
    gap["extras"]["materialBinding"] = "unobserved diagnostic topology only"
    document["meshes"][0]["primitives"].append(gap)
    document["materials"].append(
        {
            "name": "UNOBSERVED MATERIAL - diagnostic clay",
            "extensions": {"KHR_materials_unlit": {}},
            "pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.18, 0.03, 1.0]},
        }
    )
    document["extensionsUsed"] = ["KHR_materials_unlit"]
    evidence = document["asset"]["extras"]["infamousMaterialEvidence"]
    evidence["unobservedMaterialTriangles"] = 1
    evidence["fullTopologyMaterialCoverageProved"] = False
    document["nodes"][0]["extras"]["infamousMaterialEvidence"] = evidence
    changed_glb = _pack_glb(
        document, bytearray(binary[: document["buffers"][0]["byteLength"]])
    )
    report = json.loads(component.report.read_text())
    report["selection"]["triangles"] = 2
    report["selection"]["nondegenerate_triangles"] = 2
    report["selection"]["material_unobserved_triangles"] = 1
    report["limitations"]["full_topology_material_coverage"] = False
    report["glb"] = {"bytes": len(changed_glb), "sha256": _sha(changed_glb)}
    report_payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    glb_path = root / "gap.glb"
    report_path = root / "gap.json"
    glb_path.write_bytes(changed_glb)
    report_path.write_bytes(report_payload)
    return MaterialAssemblyInput(
        report_path, _sha(report_payload), glb_path, _sha(changed_glb)
    )


def test_assembles_relative_centers_deterministically(tmp_path: Path):
    first = _write_component(tmp_path, 100, [10.0, 0.0, 2.0])
    second = _write_component(tmp_path, 200, [20.0, 4.0, 6.0])

    glb, report = _build([second, first])
    reverse_glb, reverse_report = _build([first, second])
    assert glb == reverse_glb
    assert report == reverse_report
    assert report["tool_inventory_id"] == (
        "xpp-tool.character-material-assembly-export.v1"
    )
    assert report["assembly"]["vertices"] == 6
    assert report["assembly"]["retail_triangle_occurrences"] == 2
    assert report["assembly"]["material_observed_triangle_occurrences"] == 2
    assert report["assembly"]["material_unobserved_triangle_occurrences"] == 0
    assert report["assembly"]["diagnostic_bounds"] == {
        "minimum": [-6.0, -2.0, -3.0],
        "maximum": [6.0, 2.0, 3.0],
        "dimensions": [12.0, 4.0, 6.0],
    }
    assert [row["record_offset"] for row in report["authorities"]["components"]] == [
        100,
        200,
    ]
    document, _ = _parse_glb(glb)
    assert [node["translation"] for node in document["nodes"]] == [
        [-5.0, -2.0, 2.0],
        [5.0, 2.0, -2.0],
    ]
    assert len(document["meshes"]) == 2
    assert len(document["materials"]) == 2
    assert len(document["textures"]) == 2
    assert len(document["images"]) == 2
    assert document["meshes"][1]["primitives"][0]["material"] == 1
    assert document["meshes"][1]["primitives"][0]["attributes"]["POSITION"] == 4
    assert report["glb"] == {"bytes": len(glb), "sha256": _sha(glb)}
    assert report["limitations"]["full_character"] is False
    assert report["proof"]["relative_translation_uses_only_reported_centers"] is True


def test_selective_preview_fills_only_requested_gap_and_keeps_proof_false(
    tmp_path: Path,
):
    first = _add_gap(_write_component(tmp_path, 100, [10.0, 0.0, 2.0]), tmp_path)
    second = _write_component(tmp_path, 200, [20.0, 4.0, 6.0])

    strict_glb, strict_report = _build([first, second])
    preview_glb, preview_report = build_character_material_assembly(
        [second, first],
        title_id="infamous-1",
        build_id="bcus98119-v0100",
        candidate_id="zeke",
        page=2,
        preview_records=(100,),
    )
    reverse_preview_glb, reverse_preview_report = build_character_material_assembly(
        [first, second],
        title_id="infamous-1",
        build_id="bcus98119-v0100",
        candidate_id="zeke",
        page=2,
        preview_records=(100,),
    )
    assert preview_glb == reverse_preview_glb
    assert preview_report == reverse_preview_report
    strict_document, _ = _parse_glb(strict_glb)
    preview_document, _ = _parse_glb(preview_glb)

    assert strict_document["meshes"][0]["primitives"][1]["material"] == 1
    assert preview_document["meshes"][0]["primitives"][1]["material"] == 0
    assert preview_document["meshes"][0]["primitives"][1]["extras"] == {
        "materialBinding": (
            "selective assembly preview extrapolation over unresolved topology"
        )
    }
    assert strict_report["presentation"]["mode"] == "strict-observed-only"
    assert preview_report["presentation"] == {
        "mode": "selective-preview",
        "preview_extrapolated_record_offsets": [100],
        "preview_extrapolated_triangle_occurrences": 1,
        "strict_material_observation_counts_preserved": True,
        "preview_is_runtime_material_proof": False,
    }
    assert preview_report["assembly"]["material_unobserved_triangle_occurrences"] == 1
    assert preview_report["proof"]["strict_material_assignments_preserved"] is False
    assert preview_report["limitations"]["all_material_faces_proved"] is False

    cli_glb = tmp_path / "selective-preview.glb"
    cli_report = tmp_path / "selective-preview.json"
    assert (
        main(
            [
                "character-material-assembly-export",
                "--title-id",
                "infamous-1",
                "--build-id",
                "bcus98119-v0100",
                "--candidate-id",
                "zeke",
                "--page",
                "2",
                "--preview-record",
                "100",
                "--component",
                str(first.report),
                first.report_sha256,
                str(first.glb),
                first.glb_sha256,
                "--component",
                str(second.report),
                second.report_sha256,
                str(second.glb),
                second.glb_sha256,
                "--output-glb",
                str(cli_glb),
                "--output-report",
                str(cli_report),
            ]
        )
        == 0
    )
    assert cli_glb.read_bytes() == preview_glb
    assert json.loads(cli_report.read_text())["presentation"]["mode"] == (
        "selective-preview"
    )


def test_selective_preview_rejects_unknown_duplicate_and_complete_records(
    tmp_path: Path,
):
    first = _write_component(tmp_path, 100, [10.0, 0.0, 2.0])
    second = _write_component(tmp_path, 200, [20.0, 4.0, 6.0])
    kwargs = {
        "title_id": "infamous-1",
        "build_id": "bcus98119-v0100",
        "candidate_id": "zeke",
        "page": 2,
    }
    with pytest.raises(MaterialAssemblyError, match="duplicated"):
        build_character_material_assembly(
            [first, second], preview_records=(100, 100), **kwargs
        )
    with pytest.raises(MaterialAssemblyError, match="not an admitted"):
        build_character_material_assembly(
            [first, second], preview_records=(300,), **kwargs
        )
    with pytest.raises(MaterialAssemblyError, match="has no unresolved"):
        build_character_material_assembly(
            [first, second], preview_records=(100,), **kwargs
        )


def test_rejects_page_record_frame_and_glb_drift(tmp_path: Path):
    first = _write_component(tmp_path, 100, [10.0, 0.0, 2.0])
    wrong_page = _write_component(tmp_path, 200, [20.0, 0.0, 2.0], page=3)
    with pytest.raises(MaterialAssemblyError, match="requested page"):
        _build([first, wrong_page])

    duplicate_glb, duplicate_report = _component(100, [20.0, 0.0, 2.0])
    duplicate_glb_path = tmp_path / "duplicate.glb"
    duplicate_report_path = tmp_path / "duplicate.json"
    duplicate_glb_path.write_bytes(duplicate_glb)
    duplicate_report_path.write_bytes(duplicate_report)
    duplicate = MaterialAssemblyInput(
        duplicate_report_path,
        _sha(duplicate_report),
        duplicate_glb_path,
        _sha(duplicate_glb),
    )
    with pytest.raises(MaterialAssemblyError, match="repeat a source record"):
        _build([first, duplicate])

    raw = json.loads(first.report.read_text())
    raw["recentered_position_center"][0] += 1.0
    drift_report = tmp_path / "center-drift.json"
    drift_payload = (json.dumps(raw, indent=2, sort_keys=True) + "\n").encode()
    drift_report.write_bytes(drift_payload)
    drift = MaterialAssemblyInput(
        drift_report, _sha(drift_payload), first.glb, first.glb_sha256
    )
    with pytest.raises(MaterialAssemblyError, match="center contradicts"):
        _build([drift, duplicate])

    with pytest.raises(MaterialAssemblyError, match="SHA-256 mismatch"):
        _build(
            [
                MaterialAssemblyInput(
                    first.report, first.report_sha256, first.glb, "f" * 64
                ),
                duplicate,
            ]
        )


def test_rejects_pretransformed_component_node(tmp_path: Path):
    first = _write_component(tmp_path, 100, [10.0, 0.0, 2.0])
    glb, report = _component(200, [20.0, 0.0, 2.0])
    document, binary = _parse_glb(glb)
    document["nodes"][0]["translation"] = [1.0, 0.0, 0.0]
    changed_glb = _pack_glb(
        document, bytearray(binary[: document["buffers"][0]["byteLength"]])
    )
    raw = json.loads(report)
    raw["glb"] = {"bytes": len(changed_glb), "sha256": _sha(changed_glb)}
    changed_report = (json.dumps(raw, indent=2, sort_keys=True) + "\n").encode()
    glb_path = tmp_path / "transformed.glb"
    report_path = tmp_path / "transformed.json"
    glb_path.write_bytes(changed_glb)
    report_path.write_bytes(changed_report)
    transformed = MaterialAssemblyInput(
        report_path, _sha(changed_report), glb_path, _sha(changed_glb)
    )
    with pytest.raises(MaterialAssemblyError, match="node transform"):
        _build([first, transformed])


def test_atomic_writer_and_cli(tmp_path: Path, capsys):
    first = _write_component(tmp_path, 100, [10.0, 0.0, 2.0])
    second = _write_component(tmp_path, 200, [20.0, 0.0, 2.0])
    output_glb = tmp_path / "assembly.glb"
    output_report = tmp_path / "assembly.json"
    exit_code = main(
        [
            "character-material-assembly-export",
            "--title-id",
            "infamous-1",
            "--build-id",
            "bcus98119-v0100",
            "--candidate-id",
            "zeke",
            "--page",
            "2",
            "--component",
            str(second.report),
            second.report_sha256,
            str(second.glb),
            second.glb_sha256,
            "--component",
            str(first.report),
            first.report_sha256,
            str(first.glb),
            first.glb_sha256,
            "--output-glb",
            str(output_glb),
            "--output-report",
            str(output_report),
        ]
    )
    assert exit_code == 0
    assert output_glb.is_file() and output_report.is_file()
    assert "2 components / 6 vertices / 2 triangles" in capsys.readouterr().out
    old_glb = output_glb.read_bytes()
    old_report = output_report.read_bytes()
    assert (
        main(
            [
                "character-material-assembly-export",
                "--title-id",
                "infamous-1",
                "--build-id",
                "bcus98119-v0100",
                "--candidate-id",
                "zeke",
                "--page",
                "2",
                "--component",
                str(first.report),
                first.report_sha256,
                str(first.glb),
                first.glb_sha256,
                "--component",
                str(second.report),
                second.report_sha256,
                str(second.glb),
                second.glb_sha256,
                "--output-glb",
                str(output_glb),
                "--output-report",
                str(output_report),
            ]
        )
        == 1
    )
    assert output_glb.read_bytes() == old_glb
    assert output_report.read_bytes() == old_report

    glb, report = _build([first, second])
    alternate_glb = tmp_path / "alternate.glb"
    alternate_report = tmp_path / "alternate.json"
    alternate_report.write_bytes(b"occupied")
    with pytest.raises(MaterialAssemblyError, match="already exists"):
        write_new_material_assembly(alternate_glb, alternate_report, glb, report)
    assert not alternate_glb.exists()
    assert alternate_report.read_bytes() == b"occupied"
    assert render_material_assembly_report(report).endswith(b"\n")
