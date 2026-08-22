import csv
import hashlib
import json
import struct

import pytest

from infamous_xpp_textures.cli import main
from infamous_xpp_textures.runtime_topology_export import (
    RuntimeTopologyExportError,
    _BINDING_FIELDS,
    _TEXTURE_BINDING_FIELDS,
    _parse_texture_allowlist,
    export_runtime_topology_glb,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _descriptor(attributes: tuple[tuple[int, int, int, int, int, int], ...]) -> str:
    material = "".join(
        ":".join(str(value) for value in item) + ";" for item in attributes
    )
    return _sha(material.encode("ascii"))


def _write_bundle(tmp_path, *, range_first=0, index_values=None):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    if index_values is None:
        index_values = tuple(range_first + value for value in (0, 1, 2, 2, 3, 0))
    indices = struct.pack(">6H", *index_values)
    positions = b"".join(
        struct.pack(">3f", *value)
        for value in ((10, 20, 30), (11, 20, 30), (10, 21, 30), (10, 20, 31))
    )
    attributes = bytes(56)
    payloads = {
        f"topology-01-index-{_sha(indices)[:16]}.bin": indices,
        f"topology-01-block-01-{_sha(positions)[:16]}.bin": positions,
        f"topology-01-block-02-{_sha(attributes)[:16]}.bin": attributes,
    }
    for name, payload in payloads.items():
        (bundle / name).write_bytes(payload)
    common = {
        "event": "1",
        "draw_event": "42",
        "index_sha256": _sha(indices),
        "index_bytes": str(len(indices)),
        "index_count": "6",
        "index_payload_file": next(name for name in payloads if "-index-" in name),
        "index_payload_sha256": _sha(indices),
        "index_payload_bytes": str(len(indices)),
        "vertex_program_sha256": "a" * 64,
        "fragment_program_register_sha256": "b" * 64,
        "transform_constants_sha256": "c" * 64,
    }
    block_one = ((0, 2, 3, 12, 0, 0),)
    block_two = ((2, 3, 3, 14, 0, 0), (8, 3, 4, 14, 0, 0))
    rows = []
    for block, block_attributes, stride, payload in (
        (1, block_one, 12, positions),
        (2, block_two, 14, attributes),
    ):
        payload_file = next(
            name for name, value in payloads.items() if value is payload
        )
        for (
            attribute,
            type_raw,
            components,
            array_stride,
            frequency,
            modulo,
        ) in block_attributes:
            rows.append(
                {
                    **common,
                    "block": str(block),
                    "payload_file": payload_file,
                    "payload_sha256": _sha(payload),
                    "payload_bytes": str(len(payload)),
                    "descriptor_sha256": _descriptor(block_attributes),
                    "attribute_mask": str(
                        sum(1 << item[0] for item in block_attributes)
                    ),
                    "attribute_count": str(len(block_attributes)),
                    "block_stride": str(stride),
                    "range_first": str(range_first),
                    "range_count": "4",
                    "memory_location": "0",
                    "attribute": str(attribute),
                    "type": str(type_raw),
                    "components": str(components),
                    "array_stride": str(array_stride),
                    "frequency": str(frequency),
                    "modulo": str(modulo),
                }
            )
    with (bundle / "topology-01-binding.tsv").open(
        "w", encoding="ascii", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=_BINDING_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    (bundle / "capture.complete").write_text(
        "format\tif1-topology-census-v1\n"
        "expected_targets\t2\n"
        "captured_targets\t1\n"
        "payload_files\t3\n"
        f"payload_bytes\t{sum(map(len, payloads.values()))}\n"
        "guest_memory_untouched\t1\n",
        encoding="ascii",
    )
    return bundle


def _make_texture_bound(bundle, tmp_path):
    target_hash = "d" * 64
    allowlist = tmp_path / "zeke-targets.sha256"
    allowlist.write_text(f"# exact target\n{target_hash}\n", encoding="ascii")
    binding = bundle / "topology-01-binding.tsv"
    with binding.open(encoding="ascii", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    capture_material = f"{rows[0]['index_sha256']}:3:{target_hash}"
    capture_key = _sha(capture_material.encode("ascii"))
    for row in rows:
        row.update(
            {
                "target_texture_slots": "3",
                "target_texture_sha256s": target_hash,
                "binding_scope": "enabled-fragment-texture-address",
                "shader_reference_proven": "0",
                "capture_key": capture_key,
            }
        )
    with binding.open("w", encoding="ascii", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=_TEXTURE_BINDING_FIELDS, delimiter="\t"
        )
        writer.writeheader()
        writer.writerows(rows)
    payloads = list(bundle.glob("*.bin"))
    (bundle / "capture.complete").write_text(
        "format\tif1-texture-bound-topology-v1\n"
        "target_texture_hashes\t1\n"
        "captured_draws\t1\n"
        "capture_limit\t16\n"
        "capture_limit_reached\t0\n"
        f"payload_files\t{len(payloads)}\n"
        f"payload_bytes\t{sum(path.stat().st_size for path in payloads)}\n"
        "binding_scope\tenabled-fragment-texture-address\n"
        "shader_reference_proven\t0\n"
        "observed_uploads\t5\n"
        "target_uploads\t1\n"
        "address_replacements\t0\n"
        "bound_addresses\t1\n"
        "guest_memory_untouched\t1\n",
        encoding="ascii",
    )
    return allowlist


def _glb_document(data: bytes) -> dict:
    magic, version, total = struct.unpack_from("<III", data)
    assert (magic, version, total) == (0x46546C67, 2, len(data))
    json_size, json_type = struct.unpack_from("<II", data, 12)
    assert json_type == 0x4E4F534A
    return json.loads(data[20 : 20 + json_size])


def test_exports_complete_runtime_event_deterministically(tmp_path):
    bundle = _write_bundle(tmp_path)
    output = tmp_path / "runtime.glb"
    report = export_runtime_topology_glb(
        bundle, 1, output, position_hypothesis_attribute=0
    )
    assert report["vertices"] == 4
    assert report["triangles"] == 2
    assert report["nondegenerate_triangles"] == 2
    assert report["block_count"] == 2
    assert report["attribute_count"] == 3
    assert report["gates"]["runtime_topology"] is True
    assert report["gates"]["draw_ownership"] is False
    assert report["gates"]["xpp_correlation"] is False
    document = _glb_document(output.read_bytes())
    evidence = document["asset"]["extras"]["infamousRuntimeDiagnostic"]
    assert evidence["runtimeOnly"] is True
    assert evidence["drawOwnershipProved"] is False
    assert evidence["positionSemanticProved"] is False
    assert "skins" not in document
    assert document["asset"]["generator"].startswith("xpp-tool 2.8.0")
    assert report["version"] == 1
    assert "texture_bound_correlation" not in report

    second = tmp_path / "runtime-second.glb"
    second_report = export_runtime_topology_glb(
        bundle, 1, second, position_hypothesis_attribute=0
    )
    assert second.read_bytes() == output.read_bytes()
    assert second_report == report


def test_exports_nonzero_vertex_range_with_bounded_index_rebase(tmp_path):
    bundle = _write_bundle(tmp_path, range_first=7)
    output = tmp_path / "runtime-nonzero-range.glb"
    report = export_runtime_topology_glb(
        bundle, 1, output, position_hypothesis_attribute=0
    )
    assert report["source_range_first"] == 7
    assert report["indices_rebased_for_inspection"] is True
    assert report["vertices"] == 4
    assert report["triangles"] == 2
    document = _glb_document(output.read_bytes())
    evidence = document["asset"]["extras"]["infamousRuntimeDiagnostic"]
    assert evidence["sourceRangeFirst"] == 7
    assert evidence["indicesRebasedForInspection"] is True


@pytest.mark.parametrize(
    "index_values",
    (
        (6, 8, 9, 9, 10, 7),
        (7, 8, 9, 9, 11, 12),
    ),
)
def test_rejects_indices_outside_nonzero_vertex_range(tmp_path, index_values):
    bundle = _write_bundle(
        tmp_path, range_first=7, index_values=index_values
    )
    with pytest.raises(
        RuntimeTopologyExportError,
        match="indices and selected position block do not reconcile",
    ):
        export_runtime_topology_glb(
            bundle, 1, tmp_path / "bad.glb", position_hypothesis_attribute=0
        )


def test_exports_texture_bound_event_with_exact_correlation(tmp_path):
    bundle = _write_bundle(tmp_path)
    allowlist = _make_texture_bound(bundle, tmp_path)
    output = tmp_path / "texture-bound.glb"
    report = export_runtime_topology_glb(
        bundle,
        1,
        output,
        position_hypothesis_attribute=0,
        texture_allowlist=allowlist,
    )
    assert report["version"] == 2
    assert report["bundle_format"] == "if1-texture-bound-topology-v1"
    assert report["bundle_captured_draws"] == 1
    assert report["texture_identity_correlation_proved"] is True
    assert report["target_texture_slots"] == [3]
    assert report["target_texture_sha256s"] == ["d" * 64]
    assert report["shader_reference_proved"] is False
    assert report["gates"]["draw_ownership"] is False
    document = _glb_document(output.read_bytes())
    evidence = document["asset"]["extras"]["infamousRuntimeDiagnostic"]
    assert document["asset"]["generator"].startswith("xpp-tool 2.11.0")
    assert evidence["textureIdentityCorrelationProved"] is True
    assert evidence["shaderReferenceProved"] is False

    second = tmp_path / "texture-bound-second.glb"
    second_report = export_runtime_topology_glb(
        bundle,
        1,
        second,
        position_hypothesis_attribute=0,
        texture_allowlist=allowlist,
    )
    assert second.read_bytes() == output.read_bytes()
    assert second_report == report


def test_texture_bound_bundle_requires_matching_allowlist(tmp_path):
    bundle = _write_bundle(tmp_path)
    _make_texture_bound(bundle, tmp_path)
    with pytest.raises(RuntimeTopologyExportError, match="requires --texture-allowlist"):
        export_runtime_topology_glb(
            bundle, 1, tmp_path / "bad.glb", position_hypothesis_attribute=0
        )
    wrong = tmp_path / "wrong.sha256"
    wrong.write_text("e" * 64 + "\n", encoding="ascii")
    with pytest.raises(RuntimeTopologyExportError, match="invalid slot/hash/binding"):
        export_runtime_topology_glb(
            bundle,
            1,
            tmp_path / "bad.glb",
            position_hypothesis_attribute=0,
            texture_allowlist=wrong,
        )


def test_texture_bound_bundle_rejects_capture_key_or_claim_drift(tmp_path):
    bundle = _write_bundle(tmp_path)
    allowlist = _make_texture_bound(bundle, tmp_path)
    binding = bundle / "topology-01-binding.tsv"
    with binding.open(encoding="ascii", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    for row in rows:
        row["capture_key"] = "e" * 64
    with binding.open("w", encoding="ascii", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=_TEXTURE_BINDING_FIELDS, delimiter="\t"
        )
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(RuntimeTopologyExportError, match="capture key"):
        export_runtime_topology_glb(
            bundle,
            1,
            tmp_path / "bad.glb",
            position_hypothesis_attribute=0,
            texture_allowlist=allowlist,
        )


def test_texture_bound_completion_rejects_false_shader_reference_claim(tmp_path):
    bundle = _write_bundle(tmp_path)
    allowlist = _make_texture_bound(bundle, tmp_path)
    completion = bundle / "capture.complete"
    completion.write_text(
        completion.read_text(encoding="ascii").replace(
            "shader_reference_proven\t0", "shader_reference_proven\t1"
        ),
        encoding="ascii",
    )
    with pytest.raises(RuntimeTopologyExportError, match="unsupported binding"):
        export_runtime_topology_glb(
            bundle,
            1,
            tmp_path / "bad.glb",
            position_hypothesis_attribute=0,
            texture_allowlist=allowlist,
        )


def test_texture_bound_completion_rejects_false_limit_claim(tmp_path):
    bundle = _write_bundle(tmp_path)
    allowlist = _make_texture_bound(bundle, tmp_path)
    completion = bundle / "capture.complete"
    completion.write_text(
        completion.read_text(encoding="ascii").replace(
            "capture_limit_reached\t0", "capture_limit_reached\t1"
        ),
        encoding="ascii",
    )
    with pytest.raises(RuntimeTopologyExportError, match="bounded contract"):
        export_runtime_topology_glb(
            bundle,
            1,
            tmp_path / "bad.glb",
            position_hypothesis_attribute=0,
            texture_allowlist=allowlist,
        )


@pytest.mark.parametrize(
    "payload",
    (
        "D" * 64 + "\n",
        "d" * 64 + "\n" + "d" * 64 + "\n",
        "# no hashes\n",
    ),
)
def test_texture_bound_bundle_rejects_malformed_allowlist(tmp_path, payload):
    bundle = _write_bundle(tmp_path)
    allowlist = _make_texture_bound(bundle, tmp_path)
    allowlist.write_text(payload, encoding="ascii")
    with pytest.raises(RuntimeTopologyExportError, match="allowlist"):
        export_runtime_topology_glb(
            bundle,
            1,
            tmp_path / "bad.glb",
            position_hypothesis_attribute=0,
            texture_allowlist=allowlist,
        )


def test_texture_bound_bundle_rejects_oversized_allowlist(tmp_path):
    bundle = _write_bundle(tmp_path)
    allowlist = _make_texture_bound(bundle, tmp_path)
    allowlist.write_bytes(b"#" + b"x" * (40 * 1024))
    with pytest.raises(RuntimeTopologyExportError, match="40 KiB"):
        export_runtime_topology_glb(
            bundle,
            1,
            tmp_path / "bad.glb",
            position_hypothesis_attribute=0,
            texture_allowlist=allowlist,
        )


def test_texture_allowlist_accepts_exact_512_hash_bound(tmp_path):
    allowlist = tmp_path / "full-identities.sha256"
    payload = "".join(f"{value:064x}\n" for value in range(512)).encode("ascii")
    allowlist.write_bytes(payload)
    hashes, payload_sha256 = _parse_texture_allowlist(allowlist)
    assert len(hashes) == 512
    assert payload_sha256 == _sha(payload)


def test_texture_allowlist_rejects_513th_unique_hash(tmp_path):
    allowlist = tmp_path / "too-many-identities.sha256"
    allowlist.write_text(
        "".join(f"{value:064x}\n" for value in range(513)), encoding="ascii"
    )
    with pytest.raises(RuntimeTopologyExportError, match="512-hash"):
        _parse_texture_allowlist(allowlist)


def test_texture_bound_export_refuses_overwrite(tmp_path):
    bundle = _write_bundle(tmp_path)
    allowlist = _make_texture_bound(bundle, tmp_path)
    output = tmp_path / "existing.glb"
    output.write_bytes(b"keep")
    with pytest.raises(RuntimeTopologyExportError, match="refusing to overwrite"):
        export_runtime_topology_glb(
            bundle,
            1,
            output,
            position_hypothesis_attribute=0,
            texture_allowlist=allowlist,
        )
    assert output.read_bytes() == b"keep"


def test_rejects_tampered_payload(tmp_path):
    bundle = _write_bundle(tmp_path)
    position_path = next(bundle.glob("topology-01-block-01-*.bin"))
    position_path.write_bytes(position_path.read_bytes()[:-1] + b"x")
    with pytest.raises(RuntimeTopologyExportError, match="exact size/SHA-256"):
        export_runtime_topology_glb(
            bundle, 1, tmp_path / "bad.glb", position_hypothesis_attribute=0
        )


def test_rejects_unreferenced_extra_file(tmp_path):
    bundle = _write_bundle(tmp_path)
    (bundle / "extra.bin").write_bytes(b"extra")
    with pytest.raises(RuntimeTopologyExportError, match="unreferenced extra"):
        export_runtime_topology_glb(
            bundle, 1, tmp_path / "bad.glb", position_hypothesis_attribute=0
        )


def test_rejects_ambiguous_attribute_rows(tmp_path):
    bundle = _write_bundle(tmp_path)
    binding = bundle / "topology-01-binding.tsv"
    lines = binding.read_text(encoding="ascii").splitlines()
    fields = lines[1].split("\t")
    attribute_index = _BINDING_FIELDS.index("attribute")
    fields[attribute_index] = "2"
    lines[1] = "\t".join(fields)
    binding.write_text("\n".join(lines) + "\n", encoding="ascii")
    with pytest.raises(RuntimeTopologyExportError, match="attributes must be unique"):
        export_runtime_topology_glb(
            bundle, 1, tmp_path / "bad.glb", position_hypothesis_attribute=0
        )


def test_rejects_unsupported_position_encoding(tmp_path):
    bundle = _write_bundle(tmp_path)
    binding = bundle / "topology-01-binding.tsv"
    with binding.open(encoding="ascii", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    rows[0]["type"] = "3"
    rows[0]["descriptor_sha256"] = _descriptor(((0, 3, 3, 12, 0, 0),))
    with binding.open("w", encoding="ascii", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_BINDING_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(RuntimeTopologyExportError, match="float32x3"):
        export_runtime_topology_glb(
            bundle, 1, tmp_path / "bad.glb", position_hypothesis_attribute=0
        )


def test_rejects_symlinked_payload(tmp_path):
    bundle = _write_bundle(tmp_path)
    position_path = next(bundle.glob("topology-01-block-01-*.bin"))
    replacement = tmp_path / "position-copy.bin"
    replacement.write_bytes(position_path.read_bytes())
    position_path.unlink()
    position_path.symlink_to(replacement)
    with pytest.raises(RuntimeTopologyExportError, match="non-symlink files"):
        export_runtime_topology_glb(
            bundle, 1, tmp_path / "bad.glb", position_hypothesis_attribute=0
        )


def test_cli_refuses_output_inside_input_bundle(tmp_path, capsys):
    bundle = _write_bundle(tmp_path)
    result = main(
        [
            "runtime-topology-diagnostic-export",
            "--bundle",
            str(bundle),
            "--event",
            "1",
            "--position-hypothesis-attribute",
            "0",
            "--output",
            str(bundle / "bad.glb"),
        ]
    )
    assert result == 1
    assert "outside the immutable input bundle" in capsys.readouterr().err
    assert not (bundle / "bad.glb").exists()


def test_cli_exports_texture_bound_bundle(tmp_path, capsys):
    bundle = _write_bundle(tmp_path)
    allowlist = _make_texture_bound(bundle, tmp_path)
    output = tmp_path / "texture-bound.glb"
    report = tmp_path / "texture-bound.json"
    result = main(
        [
            "runtime-topology-diagnostic-export",
            "--bundle",
            str(bundle),
            "--texture-allowlist",
            str(allowlist),
            "--event",
            "1",
            "--position-hypothesis-attribute",
            "0",
            "--output",
            str(output),
            "--json-out",
            str(report),
        ]
    )
    assert result == 0
    assert output.is_file()
    parsed = json.loads(report.read_text(encoding="utf-8"))
    assert parsed["texture_identity_correlation_proved"] is True
    assert parsed["gates"]["draw_ownership"] is False
    assert json.loads(capsys.readouterr().out) == parsed


def test_cli_refuses_existing_texture_bound_report_before_glb_write(
    tmp_path, capsys
):
    bundle = _write_bundle(tmp_path)
    allowlist = _make_texture_bound(bundle, tmp_path)
    output = tmp_path / "not-written.glb"
    report = tmp_path / "existing.json"
    report.write_text("keep", encoding="ascii")
    result = main(
        [
            "runtime-topology-diagnostic-export",
            "--bundle",
            str(bundle),
            "--texture-allowlist",
            str(allowlist),
            "--event",
            "1",
            "--position-hypothesis-attribute",
            "0",
            "--output",
            str(output),
            "--json-out",
            str(report),
        ]
    )
    assert result == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert report.read_text(encoding="ascii") == "keep"
    assert not output.exists()
