import csv
import hashlib
import json
import struct

import pytest

from infamous_xpp_textures.cli import main
from infamous_xpp_textures.runtime_topology_export import (
    RuntimeTopologyExportError,
    _BINDING_FIELDS,
    export_runtime_topology_glb,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _descriptor(attributes: tuple[tuple[int, int, int, int, int, int], ...]) -> str:
    material = "".join(
        ":".join(str(value) for value in item) + ";" for item in attributes
    )
    return _sha(material.encode("ascii"))


def _write_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    indices = struct.pack(">6H", 0, 1, 2, 2, 3, 0)
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
                    "range_first": "0",
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

    second = tmp_path / "runtime-second.glb"
    second_report = export_runtime_topology_glb(
        bundle, 1, second, position_hypothesis_attribute=0
    )
    assert second.read_bytes() == output.read_bytes()
    assert second_report == report


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
