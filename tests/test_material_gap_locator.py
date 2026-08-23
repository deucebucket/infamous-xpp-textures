"""Synthetic tests for the permanent strict-material face-gap locator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

import pytest

from infamous_xpp_textures.character_source_export import _pack_glb
from infamous_xpp_textures.cli import main
from infamous_xpp_textures.material_gap_locator import (
    MaterialGapLocatorError,
    locate_material_gap,
    read_bounded_regular,
    write_new_material_gap_location,
)
from infamous_xpp_textures.mesh import GlbBuilder


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fixture() -> tuple[bytes, bytes]:
    positions = [
        (0.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 2.0, 0.0),
        (0.0, 2.0, 0.0),
        (1.0, 1.0, 1.0),
        (1.5, 1.0, 1.0),
    ]
    uvs = [
        (0.0, 0.0),
        (1.0, 0.0),
        (1.0, 1.0),
        (0.0, 1.0),
        (0.5, 0.5),
        (0.75, 0.5),
    ]
    observed_indices = (0, 1, 4, 1, 2, 4, 2, 3, 4)
    gap_indices = (3, 0, 4, 4, 5, 2)
    builder = GlbBuilder()
    position_accessor = builder.add_accessor(
        b"".join(struct.pack("<3f", *row) for row in positions),
        5126,
        len(positions),
        "VEC3",
        34962,
        [0.0, 0.0, 0.0],
        [2.0, 2.0, 1.0],
    )
    uv_accessor = builder.add_accessor(
        b"".join(struct.pack("<2f", *row) for row in uvs),
        5126,
        len(uvs),
        "VEC2",
        34962,
        [0.0, 0.0],
        [1.0, 1.0],
    )
    observed_accessor = builder.add_accessor(
        struct.pack(f"<{len(observed_indices)}H", *observed_indices),
        5123,
        len(observed_indices),
        "SCALAR",
        34963,
    )
    gap_accessor = builder.add_accessor(
        struct.pack(f"<{len(gap_indices)}H", *gap_indices),
        5123,
        len(gap_indices),
        "SCALAR",
        34963,
    )
    evidence = {
        "recordOffset": 100,
        "observedMaterialTriangles": 3,
        "unobservedMaterialTriangles": 2,
        "positionSemanticProved": False,
        "uvProved": True,
        "coverageUnionRevalidated": True,
    }
    document = {
        "asset": {
            "version": "2.0",
            "extras": {"infamousMaterialEvidence": evidence},
        },
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": position_accessor,
                            "TEXCOORD_0": uv_accessor,
                        },
                        "indices": observed_accessor,
                        "mode": 4,
                        "extras": {
                            "materialBinding": "multi-observation exact triangle union"
                        },
                    },
                    {
                        "attributes": {
                            "POSITION": position_accessor,
                            "TEXCOORD_0": uv_accessor,
                        },
                        "indices": gap_accessor,
                        "mode": 4,
                        "extras": {
                            "materialBinding": "unobserved diagnostic topology only"
                        },
                    },
                ]
            }
        ],
        "bufferViews": builder.views,
        "accessors": builder.accessors,
        "buffers": [{"byteLength": len(builder.binary)}],
    }
    glb = _pack_glb(document, builder.binary)
    report = {
        "format": "infamous-character-material-export",
        "version": 1,
        "tool_inventory_id": "xpp-tool.character-material-coverage-export.v1",
        "status": "retail-material-progress-glb-written",
        "presentation_mode": "observed-union",
        "glb": {"bytes": len(glb), "sha256": _sha(glb)},
        "selection": {
            "record_offset": 100,
            "texture_family": "fixture_hair",
            "vertices": 6,
            "triangles": 5,
            "material_observed_triangles": 3,
            "material_unobserved_triangles": 2,
            "index_sha256": "a" * 64,
            "material_union_index_sha256": "b" * 64,
        },
        "limitations": {"full_topology_material_coverage": False},
        "payload_bytes_serialized_in_report": False,
    }
    report_payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    return glb, report_payload


def _locate(glb: bytes, report: bytes) -> dict:
    return locate_material_gap(
        glb,
        report,
        glb_sha256=_sha(glb),
        material_report_sha256=_sha(report),
    )


def test_locates_bounded_gap_aggregates_deterministically(tmp_path: Path):
    glb, material_report = _fixture()
    first = _locate(glb, material_report)
    second = _locate(glb, material_report)
    assert first == second
    assert first["component"]["unobserved_material_triangle_occurrences"] == 2
    assert first["gap"]["unique_vertices"] == 5
    assert first["gap"]["vertices_also_used_by_observed_faces"] == 4
    assert first["gap"]["vertices_not_used_by_observed_faces"] == 1
    assert first["gap"]["connectivity"] == {
        "edge_connected_components": 2,
        "edge_component_triangle_counts": [1, 1],
        "vertex_connected_components": 1,
        "vertex_component_triangle_counts": [2],
        "shared_boundary_edges_with_observed": 3,
        "shared_vertices_with_observed": 4,
        "gap_faces_sharing_an_edge_with_observed": 2,
        "gap_faces_sharing_a_vertex_with_observed": 2,
    }
    assert first["gap"]["diagnostic_position"]["normalized_gap_centroid"] == [
        0.45,
        0.6,
        0.4,
    ]
    assert first["proof"]["payload_lists_withheld"] is True
    assert first["limitations"]["raw_triangle_indices_serialized"] is False

    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    write_new_material_gap_location(first_path, first)
    write_new_material_gap_location(second_path, second)
    assert first_path.read_bytes() == second_path.read_bytes()
    with pytest.raises(MaterialGapLocatorError, match="already exists"):
        write_new_material_gap_location(first_path, first)


def test_rejects_input_hash_drift():
    glb, material_report = _fixture()
    with pytest.raises(MaterialGapLocatorError, match="SHA-256"):
        locate_material_gap(
            glb,
            material_report,
            glb_sha256="0" * 64,
            material_report_sha256=_sha(material_report),
        )


def test_accepts_current_runtime_observed_subset_role():
    glb, material_report = _fixture()
    document_length = struct.unpack_from("<I", glb, 12)[0]
    document = json.loads(glb[20 : 20 + document_length].rstrip(b" \x00"))
    document["meshes"][0]["primitives"][0]["extras"]["materialBinding"] = (
        "runtime-observed exact triangle subset"
    )
    binary_offset = 20 + document_length
    binary_length, binary_kind = struct.unpack_from("<I4s", glb, binary_offset)
    assert binary_kind == b"BIN\x00"
    binary = glb[binary_offset + 8 : binary_offset + 8 + binary_length]
    current_glb = _pack_glb(document, bytearray(binary))
    report = json.loads(material_report)
    report["glb"] = {"bytes": len(current_glb), "sha256": _sha(current_glb)}
    current_report = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()

    assert _locate(current_glb, current_report)["status"] == (
        "unobserved-material-faces-located"
    )


def test_rejects_report_count_drift():
    glb, material_report = _fixture()
    report = json.loads(material_report)
    report["selection"]["material_unobserved_triangles"] = 3
    drifted = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    with pytest.raises(MaterialGapLocatorError, match="counts do not reconcile"):
        _locate(glb, drifted)


def test_rejects_unknown_primitive_role():
    glb, material_report = _fixture()
    document_length = struct.unpack_from("<I", glb, 12)[0]
    document = json.loads(glb[20 : 20 + document_length].rstrip(b" \x00"))
    document["meshes"][0]["primitives"][1]["extras"]["materialBinding"] = "guess"
    binary_offset = 20 + document_length
    binary_length, binary_kind = struct.unpack_from("<I4s", glb, binary_offset)
    assert binary_kind == b"BIN\x00"
    binary = glb[binary_offset + 8 : binary_offset + 8 + binary_length]
    drifted_glb = _pack_glb(document, bytearray(binary))
    report = json.loads(material_report)
    report["glb"] = {"bytes": len(drifted_glb), "sha256": _sha(drifted_glb)}
    drifted_report = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    with pytest.raises(MaterialGapLocatorError, match="unknown primitive role"):
        _locate(drifted_glb, drifted_report)


def test_rejects_wrong_position_accessor_shape():
    glb, material_report = _fixture()
    document_length = struct.unpack_from("<I", glb, 12)[0]
    document = json.loads(glb[20 : 20 + document_length].rstrip(b" \x00"))
    position_accessor = document["meshes"][0]["primitives"][0]["attributes"]["POSITION"]
    document["accessors"][position_accessor]["type"] = "VEC2"
    binary_offset = 20 + document_length
    binary_length, binary_kind = struct.unpack_from("<I4s", glb, binary_offset)
    assert binary_kind == b"BIN\x00"
    binary = glb[binary_offset + 8 : binary_offset + 8 + binary_length]
    drifted_glb = _pack_glb(document, bytearray(binary))
    report = json.loads(material_report)
    report["glb"] = {"bytes": len(drifted_glb), "sha256": _sha(drifted_glb)}
    drifted_report = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    with pytest.raises(MaterialGapLocatorError, match="wrong component or shape"):
        _locate(drifted_glb, drifted_report)


def test_regular_input_rejects_symlink(tmp_path: Path):
    source = tmp_path / "source.glb"
    source.write_bytes(b"owned")
    link = tmp_path / "link.glb"
    link.symlink_to(source)
    with pytest.raises(MaterialGapLocatorError, match="regular non-symlink"):
        read_bounded_regular(link, limit=100, label="material GLB")


def test_cli_writes_new_report_and_refuses_overwrite(tmp_path: Path, capsys):
    glb, material_report = _fixture()
    glb_path = tmp_path / "material.glb"
    report_path = tmp_path / "material.json"
    output = tmp_path / "gap.json"
    glb_path.write_bytes(glb)
    report_path.write_bytes(material_report)
    args = [
        "character-material-gap-locator",
        "--material-glb",
        str(glb_path),
        "--material-glb-sha256",
        _sha(glb),
        "--material-report",
        str(report_path),
        "--material-report-sha256",
        _sha(material_report),
        "--output",
        str(output),
    ]
    assert main(args) == 0
    assert json.loads(output.read_text())["tool_inventory_id"] == (
        "xpp-tool.character-material-gap-locator.v1"
    )
    assert main(args) == 1
    assert "already exists" in capsys.readouterr().err


def test_rejects_full_coverage_report():
    glb, material_report = _fixture()
    report = json.loads(material_report)
    report["selection"]["material_observed_triangles"] = 5
    report["selection"]["material_unobserved_triangles"] = 0
    report["limitations"]["full_topology_material_coverage"] = True
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    with pytest.raises(MaterialGapLocatorError, match="unobserved triangle count"):
        _locate(glb, payload)
