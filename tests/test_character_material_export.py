from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from infamous_xpp_textures.character_material_export import (
    CharacterMaterialExportError,
    build_character_material_export,
    write_new_character_material_export,
)
from infamous_xpp_textures.pngio import read_png


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _glb_document(payload: bytes) -> dict:
    magic, version, total = struct.unpack_from("<3I", payload)
    assert (magic, version, total) == (0x46546C67, 2, len(payload))
    json_bytes, kind = struct.unpack_from("<2I", payload, 12)
    assert kind == 0x4E4F534A
    return json.loads(payload[20 : 20 + json_bytes])


def _fixture(tmp_path: Path, monkeypatch, *, four_maps: bool = False):
    xpp_data = bytearray(128)
    indices = (0, 1, 2, 0, 2, 3)
    index_bytes = struct.pack(">6H", *indices)
    xpp_data[: len(index_bytes)] = index_bytes
    xpp_data = bytes(xpp_data)
    position_payload = b"".join(
        struct.pack(">3f", *row)
        for row in (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (1.0, 1.0, 0.0),
        )
    )
    uv_rows = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0))
    uv_payload = b"".join(
        b"\xff\xff\xff\xff"
        + (
            struct.pack(">3e", row[0], row[1], -1.0)
            if four_maps
            else struct.pack(">2e", *row)
        )
        for row in uv_rows
    )
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "position.bin").write_bytes(position_payload)
    (bundle / "uv.bin").write_bytes(uv_payload)
    runtime_indices = struct.pack(">6H", *indices) if four_maps else struct.pack(">3H", 0, 1, 2)
    (bundle / "indices.bin").write_bytes(runtime_indices)

    texture_rows = [(20, "N", b"NNNN", 0x86)]
    if four_maps:
        texture_rows.extend(
            ((21, "A", b"AAAA", 0x88), (22, "S", b"SSSS", 0x86))
        )
    texture_rows.append((8, "C", b"CCCC", 0x88))
    binding_rows = list(enumerate(texture_rows))
    if four_maps:
        binding_rows.reverse()
    texture_hashes = {role: _sha(prefix) for _, role, prefix, _ in texture_rows}
    position_block = SimpleNamespace(
        number=1,
        payload_file="position.bin",
        payload_bytes=len(position_payload),
        payload_sha256=_sha(position_payload),
        stride=12,
        range_first=0,
        range_count=4,
        attributes=(
            {
                "attribute": 0,
                "type": 2,
                "components": 3,
                "array_stride": 12,
                "frequency": 0,
                "modulo": 0,
            },
        ),
    )
    uv_block = SimpleNamespace(
        number=3,
        payload_file="uv.bin",
        payload_bytes=len(uv_payload),
        payload_sha256=_sha(uv_payload),
        stride=10 if four_maps else 8,
        range_first=0,
        range_count=4,
        attributes=(),
    )
    event = SimpleNamespace(
        draw_event=42,
        blocks=(position_block, uv_block),
        vertex_program_sha256="v" * 64,
        fragment_program_sha256="f" * 64,
        target_texture_sha256s=tuple(
            texture_hashes[role] for _, (_, role, _, _) in binding_rows
        ),
        index_payload_file="indices.bin",
        index_bytes=len(runtime_indices),
        index_count=len(runtime_indices) // 2,
        index_sha256=_sha(runtime_indices),
    )
    monkeypatch.setattr(
        "infamous_xpp_textures.character_material_export._load_bundle",
        lambda *args: (
            {
                "format": "if1-texture-bound-topology-v4",
                "excluded_capture_keys": 1,
                "exclusion_manifest_sha256": "e" * 64,
                "observed_excluded_capture_keys": 1,
            },
            {16: event},
            "a" * 64,
        ),
    )
    monkeypatch.setattr(
        "infamous_xpp_textures.character_material_export.parse_xpp",
        lambda *args: SimpleNamespace(data_offset=0),
    )
    contract = SimpleNamespace(
        record_offset=100,
        vertex_count=4,
        triangle_count=2,
        index_offset=0,
        index_byte_count=len(index_bytes),
        index_count=6,
        index_sha256=_sha(index_bytes),
    )
    monkeypatch.setattr(
        "infamous_xpp_textures.character_material_export.find_skinned_geometry_contracts",
        lambda *args: [contract],
    )
    texture_records = [
        (
            index,
            SimpleNamespace(
                reason=None,
                faces=1,
                format_byte=format_byte,
                width=4,
                height=4,
                heap_offset=0,
                role=role,
            ),
            prefix,
        )
        for index, role, prefix, format_byte in texture_rows
    ]
    monkeypatch.setattr(
        "infamous_xpp_textures.character_material_export.iter_textures",
        lambda *args: iter(texture_records),
    )
    monkeypatch.setattr(
        "infamous_xpp_textures.character_material_export.decode_level",
        lambda record, *_args: (
            4,
            4,
            bytes(
                (
                    [80, 60, 40, 255]
                    if record.role == "C"
                    else [128, 128, 255, 255]
                    if record.role == "N"
                    else [64, 64, 64, 255]
                )
                * 16
            ),
            "synthetic",
        ),
    )

    texture_bindings = [
        {
            "sampler": sampler,
            "descriptor_index": index,
            "name": f"{'Jacket' if four_maps else 'Hair'}_{role}.psd",
            "family": "Zeke_Jacket" if four_maps else "Zeke_Hair",
            "name_suffix": role,
            "format": f"0x{format_byte:02x}",
            "width": 4,
            "height": 4,
            "matched_prefix_bytes": 4,
            "runtime_prefix_sha256": texture_hashes[role],
        }
        for sampler, (index, role, _prefix, format_byte) in binding_rows
    ]

    lineage = {
        "format": "infamous-character-uv-texture-binding",
        "version": 1,
        "status": "exact-shader-lineage-with-unique-packed-layout",
        "authorities": {
            "bundle_format": "if1-texture-bound-topology-v4",
            "texture_allowlist_sha256": "a" * 64,
            "source_xpp_sha256": _sha(xpp_data),
            "source_xpp_bytes": len(xpp_data),
        },
        "selection": {
            "page": 2,
            "event": 16,
            "draw_event": 42,
            "record_offset": 100,
            "vertex_count": 4,
            "source_block": 3,
            "source_stream_stride": 10 if four_maps else 8,
            "source_stream_sha256": _sha(uv_payload),
            "vertex_program_sha256": "v" * 64,
            "fragment_program_sha256": "f" * 64,
        },
        "shader_lineage": {
            "vertex_input_attribute": 9,
            "vertex_input_type": 3,
            "vertex_input_components": 3 if four_maps else 2,
            "vertex_input_byte_offset": 4,
            "fragment_input_name": "TEX0",
        },
        "texture_bindings": texture_bindings,
        "proof": {"geometry_to_uv_to_texture_binding": True},
        "paging": {
            "excluded_capture_keys": 1,
            "exclusion_manifest_sha256": "e" * 64,
            "observed_excluded_capture_keys": 1,
        },
    }
    lineage_path = tmp_path / "lineage.json"
    lineage_payload = (json.dumps(lineage, sort_keys=True) + "\n").encode()
    lineage_path.write_bytes(lineage_payload)
    return xpp_data, bundle, lineage_path, _sha(lineage_payload)


def test_builds_deterministic_material_glb_and_atomic_pair(tmp_path, monkeypatch):
    xpp_data, bundle, lineage, lineage_sha = _fixture(tmp_path, monkeypatch)
    glb, report = build_character_material_export(
        xpp_data,
        bundle,
        tmp_path / "unused-allowlist",
        None,
        lineage,
        lineage_sha,
    )

    document = _glb_document(glb)
    primitives = document["meshes"][0]["primitives"]
    assert len(primitives) == 2
    assert set(primitives[0]["attributes"]) == {
        "POSITION",
        "NORMAL",
        "TEXCOORD_0",
    }
    assert primitives[0]["material"] == 0
    assert primitives[1]["material"] == 1
    assert len(document["images"]) == 2
    assert len(document["materials"]) == 2
    assert document["materials"][1]["extensions"] == {"KHR_materials_unlit": {}}
    assert document["materials"][0]["normalTexture"]["index"] == 0
    assert report["selection"]["vertices"] == 4
    assert report["selection"]["triangles"] == 2
    assert report["selection"]["material_observed_triangles"] == 1
    assert report["selection"]["material_unobserved_triangles"] == 1
    assert report["proof"]["exact_uv_rows"] is True
    assert report["proof"]["exact_observed_triangle_material_subset"] is True
    assert report["limitations"]["position_semantic"] is False
    assert report["limitations"]["full_topology_material_coverage"] is False
    assert report["limitations"]["unobserved_material_preview_extrapolated"] is False
    assert report["glb"]["sha256"] == _sha(glb)

    glb_path = tmp_path / "material.glb"
    report_path = tmp_path / "material.json"
    write_new_character_material_export(glb_path, report_path, glb, report)
    assert glb_path.read_bytes() == glb
    assert json.loads(report_path.read_text())["glb"]["sha256"] == _sha(glb)
    with pytest.raises(CharacterMaterialExportError, match="already exists"):
        write_new_character_material_export(glb_path, report_path, glb, report)


def test_preview_mode_extrapolates_without_promoting_proof(tmp_path, monkeypatch):
    xpp_data, bundle, lineage, lineage_sha = _fixture(tmp_path, monkeypatch)
    glb, report = build_character_material_export(
        xpp_data,
        bundle,
        tmp_path / "unused-allowlist",
        None,
        lineage,
        lineage_sha,
        "preview-full-record",
    )

    document = _glb_document(glb)
    assert len(document["meshes"][0]["primitives"]) == 1
    assert len(document["materials"]) == 1
    assert "PROVISIONAL" in document["materials"][0]["name"]
    assert report["presentation_mode"] == "preview-full-record"
    assert report["selection"]["material_observed_triangles"] == 1
    assert report["selection"]["material_unobserved_triangles"] == 1
    assert report["limitations"]["full_topology_material_coverage"] is False
    assert report["limitations"]["unobserved_material_preview_extrapolated"] is True


def test_embeds_four_map_family_without_assigning_extra_roles(tmp_path, monkeypatch):
    xpp_data, bundle, lineage, lineage_sha = _fixture(
        tmp_path, monkeypatch, four_maps=True
    )

    glb, report = build_character_material_export(
        xpp_data,
        bundle,
        tmp_path / "unused-allowlist",
        None,
        lineage,
        lineage_sha,
    )

    document = _glb_document(glb)
    assert [item["extras"]["retailNameSuffix"] for item in document["textures"]] == [
        "N",
        "A",
        "S",
        "C",
    ]
    assert [item["extras"]["displayRole"] for item in document["textures"]] == [
        "normal",
        None,
        None,
        "baseColor",
    ]
    assert len(document["images"]) == 4
    assert document["materials"][0]["normalTexture"]["index"] == 0
    assert document["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"][
        "index"
    ] == 3
    assert report["selection"]["texture_family"] == "Zeke_Jacket"
    assert report["selection"]["material_observed_triangles"] == 2
    assert report["selection"]["material_unobserved_triangles"] == 0
    assert report["selection"]["unassigned_texture_suffixes"] == ["A", "S"]
    assert report["proof"]["embedded_all_shader_bound_textures"] is True
    assert report["proof"]["all_extra_texture_roles_left_unassigned"] is True
    assert report["limitations"]["native_pbr"] is False


def test_rejects_duplicate_suffix_and_mixed_family(tmp_path, monkeypatch):
    xpp_data, bundle, lineage, _lineage_sha = _fixture(
        tmp_path, monkeypatch, four_maps=True
    )
    value = json.loads(lineage.read_text())

    duplicate_value = json.loads(json.dumps(value))
    duplicate_value["texture_bindings"][1]["name_suffix"] = "N"
    duplicate_path = tmp_path / "duplicate-lineage.json"
    duplicate_payload = (json.dumps(duplicate_value, sort_keys=True) + "\n").encode()
    duplicate_path.write_bytes(duplicate_payload)
    with pytest.raises(CharacterMaterialExportError, match="bounded texture family"):
        build_character_material_export(
            xpp_data,
            bundle,
            tmp_path / "unused-allowlist",
            None,
            duplicate_path,
            _sha(duplicate_payload),
        )

    mixed_value = json.loads(json.dumps(value))
    mixed_value["texture_bindings"][1]["family"] = "Other_Family"
    mixed_path = tmp_path / "mixed-lineage.json"
    mixed_payload = (json.dumps(mixed_value, sort_keys=True) + "\n").encode()
    mixed_path.write_bytes(mixed_payload)
    with pytest.raises(CharacterMaterialExportError, match="bounded texture family"):
        build_character_material_export(
            xpp_data,
            bundle,
            tmp_path / "unused-allowlist",
            None,
            mixed_path,
            _sha(mixed_payload),
        )


def test_rejects_lineage_hash_and_retail_xpp_drift(tmp_path, monkeypatch):
    xpp_data, bundle, lineage, lineage_sha = _fixture(tmp_path, monkeypatch)
    with pytest.raises(CharacterMaterialExportError, match="SHA-256"):
        build_character_material_export(
            xpp_data,
            bundle,
            tmp_path / "unused-allowlist",
            None,
            lineage,
            "0" * 64,
        )
    with pytest.raises(CharacterMaterialExportError, match="XPP"):
        build_character_material_export(
            xpp_data + b"drift",
            bundle,
            tmp_path / "unused-allowlist",
            None,
            lineage,
            lineage_sha,
        )


def test_in_memory_png_rejects_mismatched_rgba(tmp_path):
    from infamous_xpp_textures.pngio import encode_png

    with pytest.raises(ValueError, match="do not reconcile"):
        encode_png(4, 4, b"short")
    payload = encode_png(1, 1, b"\x01\x02\x03\xff")
    path = tmp_path / "one.png"
    path.write_bytes(payload)
    assert read_png(path) == (1, 1, bytearray(b"\x01\x02\x03\xff"))
