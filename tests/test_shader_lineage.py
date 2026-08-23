from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from infamous_xpp_textures.shader_lineage import (
    ShaderLineageError,
    _reconstruct_attribute_layout,
    _read_pinned_json,
    analyze_vertex_input_lineage,
    build_character_uv_texture_binding,
    write_new_character_uv_texture_binding,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: dict) -> str:
    payload = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(payload)
    return _sha(payload)


def _vp_source(register_type: int) -> int:
    return register_type | (3 << 8) | (2 << 10) | (1 << 12)


def _vertex_program(*, input_attribute=9, output=7) -> bytes:
    source = _vp_source(2)
    d0 = (7 << 10) | (1 << 30)
    d1 = ((source >> 9) & 0xFF) | (input_attribute << 8) | (1 << 22)
    d2 = (source & 0x1FF) << 23
    d3 = 1 | (output << 2) | (1 << 15) | (1 << 16)
    words = [0] * (544 * 4)
    words[:4] = (d0, d1, d2, d3)
    return struct.pack("<2177I", *words, 0)


def _captured_word(decoded: int) -> int:
    rotated = ((decoded >> 16) | ((decoded & 0xFFFF) << 16)) & 0xFFFFFFFF
    return int.from_bytes(rotated.to_bytes(4, "big"), "little")


def _fragment_program(*, target_hash: str) -> bytes:
    del target_hash
    dest = (4 << 13) | (0 << 17) | (0x31 << 24) | 1
    source = 1 | (1 << 11) | (2 << 13) | (3 << 15)
    return struct.pack("<4I", _captured_word(dest), _captured_word(source), 0, 0)


def _block_payload() -> bytes:
    return b"".join(
        b"\xff\xff\xff\xff" + struct.pack(">2e", *row)
        for row in ((0.25, 0.5), (0.75, 1.0))
    )


def _block(payload: bytes):
    return SimpleNamespace(
        number=3,
        payload_file="block.bin",
        payload_bytes=len(payload),
        payload_sha256=_sha(payload),
        stride=8,
        range_first=0,
        range_count=2,
        attributes=(
            {
                "attribute": 3,
                "type": 4,
                "components": 4,
                "array_stride": 8,
                "frequency": 0,
                "modulo": 0,
            },
            {
                "attribute": 9,
                "type": 3,
                "components": 2,
                "array_stride": 8,
                "frequency": 0,
                "modulo": 0,
            },
        ),
    )


def test_vertex_lineage_proves_component_level_input_path():
    report = analyze_vertex_input_lineage(_vertex_program())
    output = next(item for item in report["outputs"] if item["register"] == 7)
    assert output["components"][:2] == [["input-09.x"], ["input-09.y"]]
    assert report["branch_free"] is True


def test_packed_layout_has_one_finite_complete_tiling():
    payload = _block_payload()
    report = _reconstruct_attribute_layout(_block(payload), payload)
    assert report["candidate_permutations"] == 2
    assert report["finite_complete_layouts"] == 1
    assert report["byte_offsets_directly_captured"] is False
    assert report["unique_complete_layout"][1]["attribute"] == 9
    assert report["unique_complete_layout"][1]["byte_offset"] == 4
    assert report["unique_complete_layout"][1]["numeric_summary"] == {
        "finite": True,
        "minimum": [0.25, 0.5],
        "maximum": [0.75, 1.0],
    }


def test_packed_layout_uses_unpadded_half3_guest_storage():
    rows = ((0.25, 0.5, -1.0), (0.75, 1.0, 0.125))
    payload = b"".join(b"\xff\xff\xff\xff" + struct.pack(">3e", *row) for row in rows)
    block = SimpleNamespace(
        number=3,
        payload_file="block.bin",
        payload_bytes=len(payload),
        payload_sha256=_sha(payload),
        stride=10,
        range_count=2,
        attributes=(
            {
                "attribute": 3,
                "type": 4,
                "components": 4,
                "array_stride": 10,
                "frequency": 0,
                "modulo": 0,
            },
            {
                "attribute": 9,
                "type": 3,
                "components": 3,
                "array_stride": 10,
                "frequency": 0,
                "modulo": 0,
            },
        ),
    )

    report = _reconstruct_attribute_layout(block, payload)

    assert report["candidate_permutations"] == 2
    assert report["finite_complete_layouts"] == 1
    assert report["unique_complete_layout"][0]["element_bytes"] == 4
    assert report["unique_complete_layout"][1]["element_bytes"] == 6
    assert report["unique_complete_layout"][1]["byte_offset"] == 4
    assert report["unique_complete_layout"][1]["numeric_summary"] == {
        "finite": True,
        "minimum": [0.25, 0.5, -1.0],
        "maximum": [0.75, 1.0, 0.125],
    }


def test_packed_layout_uses_unpadded_unorm3_guest_storage():
    payload = b"".join(
        row + struct.pack(">2e", *uv)
        for row, uv in (
            (bytes((0, 64, 255)), (0.25, 0.5)),
            (bytes((255, 32, 0)), (0.75, 1.0)),
        )
    )
    block = SimpleNamespace(
        number=3,
        payload_file="block.bin",
        payload_bytes=len(payload),
        payload_sha256=_sha(payload),
        stride=7,
        range_count=2,
        attributes=(
            {
                "attribute": 3,
                "type": 4,
                "components": 3,
                "array_stride": 7,
                "frequency": 0,
                "modulo": 0,
            },
            {
                "attribute": 9,
                "type": 3,
                "components": 2,
                "array_stride": 7,
                "frequency": 0,
                "modulo": 0,
            },
        ),
    )

    report = _reconstruct_attribute_layout(block, payload)

    assert report["finite_complete_layouts"] == 1
    assert report["unique_complete_layout"][0]["element_bytes"] == 3
    assert report["unique_complete_layout"][1]["byte_offset"] == 3


def _authorities(tmp_path: Path, target_hash: str) -> tuple[Path, str, Path, str]:
    source = tmp_path / "source.json"
    source_sha = _write_json(
        source,
        {
            "kind": "if1-rsx-paged-xpp-source-census",
            "schema_version": 1,
            "source": {
                "source": "character.xpp",
                "source_sha256": "a" * 64,
                "source_size": 1000,
            },
            "events": [
                {
                    "page": 2,
                    "event": 1,
                    "same_xpp_source_record_proved": True,
                    "mapping": {
                        "record_offset": 100,
                        "block": 3,
                        "range_count": 2,
                        "source_vertex_count": 2,
                        "full_vertex_range": True,
                        "matched_stream_slice_sha256": "filled-later",
                        "stream_zero_record_bytes": 8,
                    },
                }
            ],
        },
    )
    character = tmp_path / "character.json"
    character_sha = _write_json(
        character,
        {
            "format": "infamous-character-asset-census",
            "targets": {
                "left": {
                    "sha256": "a" * 64,
                    "relative_path": "xpp/character.xpp",
                }
            },
            "target_texture_descriptors": {
                "left": [
                    {
                        "index": 5,
                        "name": "Character_Hair_C.psd",
                        "family": "Character_Hair",
                        "name_suffix": "C",
                        "format": "0x88",
                        "width": 256,
                        "height": 256,
                        "faces": 1,
                        "mip_rows": [
                            {
                                "level": 6,
                                "prefix_bytes": 100,
                                "prefix_sha256": target_hash,
                            }
                        ],
                    }
                ]
            },
        },
    )
    return source, source_sha, character, character_sha


def test_builds_complete_shader_lineage_from_pinned_authorities(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    payload = _block_payload()
    vertex = _vertex_program()
    target_hash = "d" * 64
    fragment = _fragment_program(target_hash=target_hash)
    (bundle / "block.bin").write_bytes(payload)
    (bundle / "vertex.bin").write_bytes(vertex)
    (bundle / "fragment.bin").write_bytes(fragment)
    block = _block(payload)
    event = SimpleNamespace(
        number=1,
        draw_event=42,
        blocks=(block,),
        vertex_program_file="vertex.bin",
        vertex_program_sha256=_sha(vertex),
        fragment_program_file="fragment.bin",
        fragment_program_bytes=len(fragment),
        fragment_program_sha256=_sha(fragment),
        target_texture_slots=(0,),
        target_texture_sha256s=(target_hash,),
    )
    monkeypatch.setattr(
        "infamous_xpp_textures.shader_lineage._load_bundle",
        lambda *args: (
            {"format": "if1-texture-bound-topology-v3"},
            {1: event},
            "e" * 64,
        ),
    )
    source, source_sha, character, character_sha = _authorities(tmp_path, target_hash)
    source_value = json.loads(source.read_text())
    source_value["events"][0]["mapping"]["matched_stream_slice_sha256"] = _sha(payload)
    source_sha = _write_json(source, source_value)

    report = build_character_uv_texture_binding(
        bundle,
        tmp_path / "unused-allowlist",
        None,
        source,
        source_sha,
        character,
        character_sha,
        event_number=1,
        page_number=2,
        record_offset=100,
        character_side="left",
    )

    assert report["proof"]["geometry_to_uv_to_texture_binding"] is True
    assert report["shader_lineage"]["vertex_input_attribute"] == 9
    assert report["shader_lineage"]["vertex_input_byte_offset"] == 4
    assert report["texture_bindings"][0]["name"] == "Character_Hair_C.psd"
    assert report["limitations"]["full_character"] is False
    assert report["payload_bytes_serialized"] is False

    output = tmp_path / "lineage.json"
    write_new_character_uv_texture_binding(output, report)
    first = output.read_bytes()
    with pytest.raises(ShaderLineageError, match="already exists"):
        write_new_character_uv_texture_binding(output, report)
    assert output.read_bytes() == first


def test_builds_safe_partial_shader_lineage_for_indices_inside_slice(
    tmp_path, monkeypatch
):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    payload = _block_payload()
    vertex = _vertex_program()
    target_hash = "d" * 64
    fragment = _fragment_program(target_hash=target_hash)
    runtime_indices = struct.pack(">3H", 0, 1, 0)
    for name, value in (
        ("block.bin", payload),
        ("vertex.bin", vertex),
        ("fragment.bin", fragment),
        ("indices.bin", runtime_indices),
    ):
        (bundle / name).write_bytes(value)
    block = _block(payload)
    event = SimpleNamespace(
        number=1,
        draw_event=42,
        blocks=(block,),
        vertex_program_file="vertex.bin",
        vertex_program_sha256=_sha(vertex),
        fragment_program_file="fragment.bin",
        fragment_program_bytes=len(fragment),
        fragment_program_sha256=_sha(fragment),
        target_texture_slots=(0,),
        target_texture_sha256s=(target_hash,),
        index_payload_file="indices.bin",
        index_sha256=_sha(runtime_indices),
        index_count=3,
        index_bytes=len(runtime_indices),
    )
    monkeypatch.setattr(
        "infamous_xpp_textures.shader_lineage._load_bundle",
        lambda *args: (
            {"format": "if1-texture-bound-topology-v3"},
            {1: event},
            "e" * 64,
        ),
    )
    source, _source_sha, character, character_sha = _authorities(tmp_path, target_hash)
    source_value = json.loads(source.read_text())
    source_value["source"]["records"] = [
        {
            "record_offset": 100,
            "vertex_count": 3,
            "index_count": 3,
            "index_sha256": "f" * 64,
        }
    ]
    source_value["events"][0].update(page=1)
    source_value["events"][0]["mapping"] = {
        "record_offset": 100,
        "block": 3,
        "range_first": 0,
        "range_count": 2,
        "range_end": 2,
        "source_vertex_count": 3,
        "full_vertex_range": False,
        "matched_stream_slice_sha256": _sha(payload),
        "stream_zero_record_bytes": 8,
        "source_index_count": 3,
        "source_index_sha256": "f" * 64,
        "runtime_index_coverage": {
            "status": "retail-triangle-subset-proved",
            "safe_for_retail_coverage_union": True,
            "runtime_indices_within_mapped_vertex_range": True,
            "runtime_index_sha256": _sha(runtime_indices),
            "runtime_triangle_occurrences": 1,
            "covered_retail_triangle_occurrences": 1,
            "unobserved_retail_triangle_occurrences": 0,
            "runtime_min_vertex_index": 0,
            "runtime_max_vertex_index": 1,
            "covered_triangle_multiset_sha256": _sha(runtime_indices),
            "unobserved_triangle_multiset_sha256": _sha(b""),
        },
    }
    source_sha = _write_json(source, source_value)

    report = build_character_uv_texture_binding(
        bundle,
        tmp_path / "unused-allowlist",
        None,
        source,
        source_sha,
        character,
        character_sha,
        event_number=1,
        page_number=1,
        record_offset=100,
        character_side="left",
    )

    assert report["status"] == (
        "exact-partial-shader-lineage-with-unique-packed-layout"
    )
    assert report["selection"]["source_vertex_count"] == 3
    assert report["selection"]["source_range_count"] == 2
    assert report["proof"]["full_source_vertex_range"] is False
    assert report["proof"]["safe_for_material_coverage_union"] is True
    assert (
        report["partial_runtime_coverage"]["covered_retail_triangle_occurrences"] == 1
    )


def test_partial_shader_lineage_rejects_index_outside_source_slice(
    tmp_path, monkeypatch
):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    payload = _block_payload()
    vertex = _vertex_program()
    target_hash = "d" * 64
    fragment = _fragment_program(target_hash=target_hash)
    runtime_indices = struct.pack(">3H", 0, 1, 2)
    for name, value in (
        ("block.bin", payload),
        ("vertex.bin", vertex),
        ("fragment.bin", fragment),
        ("indices.bin", runtime_indices),
    ):
        (bundle / name).write_bytes(value)
    event = SimpleNamespace(
        number=1,
        draw_event=42,
        blocks=(_block(payload),),
        vertex_program_file="vertex.bin",
        vertex_program_sha256=_sha(vertex),
        fragment_program_file="fragment.bin",
        fragment_program_bytes=len(fragment),
        fragment_program_sha256=_sha(fragment),
        target_texture_slots=(0,),
        target_texture_sha256s=(target_hash,),
        index_payload_file="indices.bin",
        index_sha256=_sha(runtime_indices),
        index_count=3,
        index_bytes=len(runtime_indices),
    )
    monkeypatch.setattr(
        "infamous_xpp_textures.shader_lineage._load_bundle",
        lambda *args: (
            {"format": "if1-texture-bound-topology-v3"},
            {1: event},
            "e" * 64,
        ),
    )
    source, _source_sha, character, character_sha = _authorities(tmp_path, target_hash)
    source_value = json.loads(source.read_text())
    source_value["source"]["records"] = [
        {
            "record_offset": 100,
            "vertex_count": 3,
            "index_count": 3,
            "index_sha256": "f" * 64,
        }
    ]
    source_value["events"][0].update(page=1)
    source_value["events"][0]["mapping"] = {
        "record_offset": 100,
        "block": 3,
        "range_first": 0,
        "range_count": 2,
        "range_end": 2,
        "source_vertex_count": 3,
        "full_vertex_range": False,
        "matched_stream_slice_sha256": _sha(payload),
        "stream_zero_record_bytes": 8,
        "source_index_count": 3,
        "source_index_sha256": "f" * 64,
        "runtime_index_coverage": {
            "status": "retail-triangle-subset-proved",
            "safe_for_retail_coverage_union": True,
            "runtime_indices_within_mapped_vertex_range": True,
            "runtime_index_sha256": _sha(runtime_indices),
            "runtime_triangle_occurrences": 1,
            "covered_retail_triangle_occurrences": 1,
            "unobserved_retail_triangle_occurrences": 0,
            "runtime_min_vertex_index": 0,
            "runtime_max_vertex_index": 2,
            "covered_triangle_multiset_sha256": _sha(runtime_indices),
            "unobserved_triangle_multiset_sha256": _sha(b""),
        },
    }
    source_sha = _write_json(source, source_value)

    with pytest.raises(ShaderLineageError, match="leave the captured source slice"):
        build_character_uv_texture_binding(
            bundle,
            tmp_path / "unused-allowlist",
            None,
            source,
            source_sha,
            character,
            character_sha,
            event_number=1,
            page_number=1,
            record_offset=100,
            character_side="left",
        )


def test_rejects_authority_hash_drift(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("{}\n")
    with pytest.raises(ShaderLineageError, match="SHA-256"):
        _read_pinned_json(source, "0" * 64, "source census")
