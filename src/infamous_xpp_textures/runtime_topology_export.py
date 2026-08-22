"""Fail-closed runtime topology bundle export for visual draw triage."""

from __future__ import annotations

import csv
import hashlib
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from .character_export import _cross_length_squared, _pack_glb, _write_atomic
from .mesh import GlbBuilder


class RuntimeTopologyExportError(ValueError):
    """Raised when a runtime topology bundle is incomplete or ambiguous."""


_BINDING_FIELDS = (
    "event",
    "draw_event",
    "index_sha256",
    "index_bytes",
    "index_count",
    "index_payload_file",
    "index_payload_sha256",
    "index_payload_bytes",
    "block",
    "payload_file",
    "payload_sha256",
    "payload_bytes",
    "descriptor_sha256",
    "attribute_mask",
    "attribute_count",
    "block_stride",
    "range_first",
    "range_count",
    "memory_location",
    "attribute",
    "type",
    "components",
    "array_stride",
    "frequency",
    "modulo",
    "vertex_program_sha256",
    "fragment_program_register_sha256",
    "transform_constants_sha256",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BINDING_NAME = re.compile(r"topology-(\d+)-binding\.tsv")
_PAYLOAD_NAME = re.compile(r"topology-(\d+)-(?:index|block-(\d+))-[0-9a-f]{16}\.bin")
_MAX_TARGETS = 16
_MAX_BUNDLE_FILES = 1 + _MAX_TARGETS + _MAX_TARGETS * 17
_MAX_INDEX_BYTES = 4 * 1024 * 1024
_MAX_BLOCK_BYTES = 8 * 1024 * 1024
_MAX_PAYLOAD_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class _Block:
    number: int
    payload_file: str
    payload_sha256: str
    payload_bytes: int
    descriptor_sha256: str
    attribute_mask: int
    attribute_count: int
    stride: int
    range_first: int
    range_count: int
    attributes: tuple[dict[str, int], ...]


@dataclass(frozen=True)
class _Event:
    number: int
    draw_event: int
    index_sha256: str
    index_bytes: int
    index_count: int
    index_payload_file: str
    blocks: tuple[_Block, ...]
    vertex_program_sha256: str
    fragment_program_register_sha256: str
    transform_constants_sha256: str


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _integer(value: str, label: str, *, allow_zero: bool = True) -> int:
    if not isinstance(value, str) or not value or not value.isdecimal():
        raise RuntimeTopologyExportError(f"{label} must be a decimal integer")
    result = int(value, 10)
    if result < 0 or (not allow_zero and result == 0):
        raise RuntimeTopologyExportError(f"{label} is outside the accepted range")
    return result


def _sha(value: str, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RuntimeTopologyExportError(f"{label} must be a lowercase SHA-256")
    return value


def _plain_filename(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value:
        raise RuntimeTopologyExportError(f"{label} must be a plain filename")
    return value


def _payload_filename(value: str, event: int, block: int | None) -> str:
    value = _plain_filename(value, "payload filename")
    match = _PAYLOAD_NAME.fullmatch(value)
    captured_block = int(match.group(2), 10) if match and match.group(2) else None
    if match is None or int(match.group(1), 10) != event or captured_block != block:
        raise RuntimeTopologyExportError(
            "payload filename does not match its event/block"
        )
    return value


def _read_payload(
    bundle: Path, filename: str, expected_size: int, expected_sha: str
) -> bytes:
    path = bundle / _plain_filename(filename, "payload filename")
    if path.is_symlink() or not path.is_file():
        raise RuntimeTopologyExportError(
            f"payload {filename} is missing or is not a regular file"
        )
    if expected_size > _MAX_PAYLOAD_BYTES or path.stat().st_size != expected_size:
        raise RuntimeTopologyExportError(
            f"payload {filename} failed exact size/SHA-256 identity"
        )
    payload = path.read_bytes()
    if _sha256(payload) != expected_sha:
        raise RuntimeTopologyExportError(
            f"payload {filename} failed exact size/SHA-256 identity"
        )
    return payload


def _parse_completion(path: Path) -> dict[str, int | str]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeTopologyExportError(
            "capture.complete is missing or is not a regular file"
        )
    rows: dict[str, str] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        fields = line.split("\t")
        if len(fields) != 2 or not fields[0] or fields[0] in rows:
            raise RuntimeTopologyExportError(
                "capture.complete has a malformed or duplicate row"
            )
        rows[fields[0]] = fields[1]
    expected = {
        "format",
        "expected_targets",
        "captured_targets",
        "payload_files",
        "payload_bytes",
        "guest_memory_untouched",
    }
    if set(rows) != expected or rows["format"] != "if1-topology-census-v1":
        raise RuntimeTopologyExportError("capture.complete schema or format is invalid")
    result: dict[str, int | str] = {"format": rows["format"]}
    for name in expected - {"format"}:
        result[name] = _integer(rows[name], name, allow_zero=False)
    if result["guest_memory_untouched"] != 1:
        raise RuntimeTopologyExportError(
            "capture does not prove guest memory remained untouched"
        )
    if result["captured_targets"] > result["expected_targets"]:
        raise RuntimeTopologyExportError(
            "captured target count exceeds the expected target count"
        )
    if (
        result["expected_targets"] > _MAX_TARGETS
        or result["payload_files"] > _MAX_TARGETS * 17
        or result["payload_bytes"] > _MAX_PAYLOAD_BYTES
    ):
        raise RuntimeTopologyExportError(
            "completion totals exceed the bounded capture contract"
        )
    return result


def _parse_binding(path: Path) -> _Event:
    match = _BINDING_NAME.fullmatch(path.name)
    if path.is_symlink() or not path.is_file() or match is None:
        raise RuntimeTopologyExportError(
            "binding is not a regular topology-N-binding.tsv file"
        )
    with path.open("r", encoding="ascii", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != _BINDING_FIELDS:
            raise RuntimeTopologyExportError(
                f"{path.name} has an invalid binding schema"
            )
        rows = list(reader)
    if (
        not rows
        or len(rows) > 16
        or any(None in row or None in row.values() for row in rows)
    ):
        raise RuntimeTopologyExportError(
            f"{path.name} has an invalid attribute-row count or shape"
        )

    common_names = (
        "event",
        "draw_event",
        "index_sha256",
        "index_bytes",
        "index_count",
        "index_payload_file",
        "index_payload_sha256",
        "index_payload_bytes",
        "vertex_program_sha256",
        "fragment_program_register_sha256",
        "transform_constants_sha256",
    )
    common = {name: rows[0][name] for name in common_names}
    if any(row[name] != value for row in rows for name, value in common.items()):
        raise RuntimeTopologyExportError(
            f"{path.name} has conflicting event-level fields"
        )
    event = _integer(common["event"], "event", allow_zero=False)
    if event != int(match.group(1), 10):
        raise RuntimeTopologyExportError(
            f"{path.name} event does not match its filename"
        )
    index_sha256 = _sha(common["index_sha256"], "index SHA-256")
    if _sha(common["index_payload_sha256"], "index payload SHA-256") != index_sha256:
        raise RuntimeTopologyExportError("index manifest and payload hashes disagree")
    index_bytes = _integer(common["index_bytes"], "index bytes", allow_zero=False)
    index_count = _integer(common["index_count"], "index count", allow_zero=False)
    if (
        index_bytes != index_count * 2
        or _integer(
            common["index_payload_bytes"], "index payload bytes", allow_zero=False
        )
        != index_bytes
        or index_count % 3
        or index_bytes > _MAX_INDEX_BYTES
    ):
        raise RuntimeTopologyExportError(
            "index extent is not one bounded u16 triangle list"
        )

    grouped: dict[int, list[dict[str, str]]] = {}
    seen_attributes: set[int] = set()
    for row in rows:
        block_number = _integer(row["block"], "block number", allow_zero=False)
        attribute = _integer(row["attribute"], "attribute")
        if attribute > 15 or attribute in seen_attributes:
            raise RuntimeTopologyExportError(
                "attributes must be unique values from 0 through 15"
            )
        seen_attributes.add(attribute)
        grouped.setdefault(block_number, []).append(row)
    if sorted(grouped) != list(range(1, len(grouped) + 1)):
        raise RuntimeTopologyExportError("block numbers must be contiguous from one")

    blocks: list[_Block] = []
    for number, block_rows in sorted(grouped.items()):
        invariant_names = (
            "payload_file",
            "payload_sha256",
            "payload_bytes",
            "descriptor_sha256",
            "attribute_mask",
            "attribute_count",
            "block_stride",
            "range_first",
            "range_count",
            "memory_location",
        )
        invariants = {name: block_rows[0][name] for name in invariant_names}
        if any(
            row[name] != value
            for row in block_rows
            for name, value in invariants.items()
        ):
            raise RuntimeTopologyExportError(f"block {number} has conflicting metadata")
        attributes = tuple(
            {
                "attribute": _integer(row["attribute"], "attribute"),
                "type": _integer(row["type"], "attribute type"),
                "components": _integer(
                    row["components"], "attribute components", allow_zero=False
                ),
                "array_stride": _integer(row["array_stride"], "attribute stride"),
                "frequency": _integer(row["frequency"], "attribute frequency"),
                "modulo": _integer(row["modulo"], "attribute modulo"),
            }
            for row in block_rows
        )
        if any(item["components"] > 4 or item["modulo"] > 1 for item in attributes):
            raise RuntimeTopologyExportError(
                f"block {number} has an invalid attribute shape"
            )
        descriptor_material = "".join(
            f"{item['attribute']}:{item['type']}:{item['components']}:"
            f"{item['array_stride']}:{item['frequency']}:{item['modulo']};"
            for item in attributes
        ).encode("ascii")
        descriptor_sha256 = _sha(invariants["descriptor_sha256"], "descriptor SHA-256")
        attribute_mask = _integer(invariants["attribute_mask"], "attribute mask")
        if (
            len(attributes)
            != _integer(
                invariants["attribute_count"], "attribute count", allow_zero=False
            )
            or attribute_mask != sum(1 << item["attribute"] for item in attributes)
            or _sha256(descriptor_material) != descriptor_sha256
        ):
            raise RuntimeTopologyExportError(
                f"block {number} descriptor rows do not reconcile"
            )
        payload_bytes = _integer(
            invariants["payload_bytes"], "payload bytes", allow_zero=False
        )
        block_stride = _integer(
            invariants["block_stride"], "block stride", allow_zero=False
        )
        range_count = _integer(
            invariants["range_count"], "range count", allow_zero=False
        )
        memory_location = _integer(invariants["memory_location"], "memory location")
        if (
            memory_location > 1
            or payload_bytes != block_stride * range_count
            or payload_bytes > _MAX_BLOCK_BYTES
            or any(
                item["type"] > 7
                or item["array_stride"] != block_stride
                or item["frequency"] > 0xFFFF
                for item in attributes
            )
        ):
            raise RuntimeTopologyExportError(
                f"block {number} extent or attribute range is invalid"
            )
        blocks.append(
            _Block(
                number=number,
                payload_file=_payload_filename(
                    invariants["payload_file"], event, number
                ),
                payload_sha256=_sha(invariants["payload_sha256"], "payload SHA-256"),
                payload_bytes=payload_bytes,
                descriptor_sha256=descriptor_sha256,
                attribute_mask=attribute_mask,
                attribute_count=len(attributes),
                stride=block_stride,
                range_first=_integer(invariants["range_first"], "range first"),
                range_count=range_count,
                attributes=attributes,
            )
        )
    return _Event(
        number=event,
        draw_event=_integer(common["draw_event"], "draw event", allow_zero=False),
        index_sha256=index_sha256,
        index_bytes=index_bytes,
        index_count=index_count,
        index_payload_file=_payload_filename(common["index_payload_file"], event, None),
        blocks=tuple(blocks),
        vertex_program_sha256=_sha(
            common["vertex_program_sha256"], "vertex program SHA-256"
        ),
        fragment_program_register_sha256=_sha(
            common["fragment_program_register_sha256"],
            "fragment program register SHA-256",
        ),
        transform_constants_sha256=_sha(
            common["transform_constants_sha256"], "transform constants SHA-256"
        ),
    )


def _load_bundle(bundle: Path) -> tuple[dict[str, int | str], dict[int, _Event]]:
    if bundle.is_symlink() or not bundle.is_dir():
        raise RuntimeTopologyExportError(
            "bundle must be an existing non-symlink directory"
        )
    entries = list(bundle.iterdir())
    if len(entries) > _MAX_BUNDLE_FILES or any(
        entry.is_symlink() or not entry.is_file() for entry in entries
    ):
        raise RuntimeTopologyExportError(
            "bundle entries must be regular non-symlink files"
        )
    completion = _parse_completion(bundle / "capture.complete")
    binding_paths = sorted(
        entry for entry in entries if _BINDING_NAME.fullmatch(entry.name)
    )
    events = [_parse_binding(path) for path in binding_paths]
    if len({event.number for event in events}) != len(events):
        raise RuntimeTopologyExportError("bundle has duplicate event numbers")
    by_event = {event.number: event for event in events}
    referenced = {"capture.complete", *(path.name for path in binding_paths)}
    payload_sizes: dict[str, int] = {}
    for event in events:
        if event.index_payload_file in payload_sizes:
            raise RuntimeTopologyExportError(
                "one payload file is referenced more than once"
            )
        index_payload = _read_payload(
            bundle, event.index_payload_file, event.index_bytes, event.index_sha256
        )
        payload_sizes[event.index_payload_file] = len(index_payload)
        for block in event.blocks:
            if block.payload_file in payload_sizes:
                raise RuntimeTopologyExportError(
                    "one payload file is referenced more than once"
                )
            payload = _read_payload(
                bundle, block.payload_file, block.payload_bytes, block.payload_sha256
            )
            payload_sizes[block.payload_file] = len(payload)
        referenced.add(event.index_payload_file)
        referenced.update(block.payload_file for block in event.blocks)
    if {entry.name for entry in entries} != referenced:
        raise RuntimeTopologyExportError(
            "bundle has missing or unreferenced extra files"
        )
    if (
        completion["captured_targets"] != len(events)
        or completion["payload_files"] != len(payload_sizes)
        or completion["payload_bytes"] != sum(payload_sizes.values())
    ):
        raise RuntimeTopologyExportError(
            "completion totals do not reconcile with the bundle"
        )
    return completion, by_event


def export_runtime_topology_glb(
    bundle: Path,
    event_number: int,
    output: Path,
    *,
    position_hypothesis_attribute: int,
) -> dict:
    """Validate one complete runtime bundle and export one selected event for inspection."""

    bundle_resolved = bundle.resolve()
    output_resolved = output.resolve()
    if output_resolved == bundle_resolved or bundle_resolved in output_resolved.parents:
        raise RuntimeTopologyExportError(
            "diagnostic output must remain outside the immutable input bundle"
        )
    if (
        not isinstance(event_number, int)
        or isinstance(event_number, bool)
        or event_number <= 0
    ):
        raise RuntimeTopologyExportError("event must be a positive integer")
    if (
        not isinstance(position_hypothesis_attribute, int)
        or isinstance(position_hypothesis_attribute, bool)
        or not 0 <= position_hypothesis_attribute <= 15
    ):
        raise RuntimeTopologyExportError(
            "position hypothesis attribute must be from 0 through 15"
        )
    completion, events = _load_bundle(bundle)
    if event_number not in events:
        raise RuntimeTopologyExportError(
            f"event {event_number} is not present in the bundle"
        )
    event = events[event_number]
    index_payload = _read_payload(
        bundle, event.index_payload_file, event.index_bytes, event.index_sha256
    )
    indices = struct.unpack(f">{event.index_count}H", index_payload)

    candidates = [
        (block, attribute)
        for block in event.blocks
        for attribute in block.attributes
        if attribute["attribute"] == position_hypothesis_attribute
    ]
    if len(candidates) != 1:
        raise RuntimeTopologyExportError(
            f"expected one selected position attribute, found {len(candidates)}"
        )
    block, attribute = candidates[0]
    if (
        attribute["type"] != 2
        or attribute["components"] != 3
        or attribute["frequency"] != 0
        or attribute["modulo"] != 0
        or attribute["array_stride"] != block.stride
        or block.stride < 12
        or block.range_first != 0
    ):
        raise RuntimeTopologyExportError(
            "selected attribute is not a bounded zero-frequency float32x3 stream from vertex zero"
        )
    if (
        max(indices) >= block.range_count
        or block.payload_bytes != block.range_count * block.stride
    ):
        raise RuntimeTopologyExportError(
            "indices and selected position block do not reconcile"
        )
    position_payload = _read_payload(
        bundle, block.payload_file, block.payload_bytes, block.payload_sha256
    )
    source_positions = [
        struct.unpack_from(">3f", position_payload, vertex * block.stride)
        for vertex in range(block.range_count)
    ]
    if not all(math.isfinite(value) for xyz in source_positions for value in xyz):
        raise RuntimeTopologyExportError("position-hypothesis payload is not finite")
    if len(set(source_positions)) < 3:
        raise RuntimeTopologyExportError(
            "position-hypothesis payload lacks distinct vertices"
        )
    nondegenerate = sum(
        _cross_length_squared(
            source_positions[indices[offset]],
            source_positions[indices[offset + 1]],
            source_positions[indices[offset + 2]],
        )
        > 1e-12
        for offset in range(0, len(indices), 3)
    )
    if not nondegenerate:
        raise RuntimeTopologyExportError(
            "position hypothesis makes every triangle degenerate"
        )

    source_min = [min(value[axis] for value in source_positions) for axis in range(3)]
    source_max = [max(value[axis] for value in source_positions) for axis in range(3)]
    center = [(source_min[axis] + source_max[axis]) / 2.0 for axis in range(3)]
    positions = [
        (value[0] - center[0], value[2] - center[2], -(value[1] - center[1]))
        for value in source_positions
    ]
    position_min = [min(value[axis] for value in positions) for axis in range(3)]
    position_max = [max(value[axis] for value in positions) for axis in range(3)]

    builder = GlbBuilder()
    position_accessor = builder.add_accessor(
        b"".join(struct.pack("<3f", *value) for value in positions),
        5126,
        len(positions),
        "VEC3",
        34962,
        position_min,
        position_max,
    )
    index_accessor = builder.add_accessor(
        struct.pack(f"<{len(indices)}H", *indices), 5123, len(indices), "SCALAR", 34963
    )
    evidence = {
        "diagnosticOnly": True,
        "runtimeOnly": True,
        "drawOwnershipProved": False,
        "xppCorrelationProved": False,
        "positionHypothesisAttribute": position_hypothesis_attribute,
        "positionSemanticProved": False,
        "recenteredForInspection": True,
        "rigged": False,
        "uvProved": False,
        "materialProved": False,
        "injectionAuthorized": False,
        "indexSha256": event.index_sha256,
        "positionPayloadSha256": block.payload_sha256,
    }
    document = {
        "asset": {
            "version": "2.0",
            "generator": "xpp-tool 2.9.0 runtime topology diagnostic exporter",
            "extras": {"infamousRuntimeDiagnostic": evidence},
        },
        "extensionsUsed": ["KHR_materials_unlit"],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {
                "mesh": 0,
                "name": "Runtime draw candidate (ownership and semantics unproved)",
                "extras": {"infamousRuntimeDiagnostic": evidence},
            }
        ],
        "meshes": [
            {
                "name": "Exact runtime topology with explicit position hypothesis",
                "primitives": [
                    {
                        "attributes": {"POSITION": position_accessor},
                        "indices": index_accessor,
                        "material": 0,
                        "mode": 4,
                    }
                ],
            }
        ],
        "materials": [
            {
                "name": "Runtime diagnostic neutral (retail material unproved)",
                "doubleSided": True,
                "extensions": {"KHR_materials_unlit": {}},
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.78, 0.48, 0.24, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 1.0,
                },
            }
        ],
        "bufferViews": builder.views,
        "accessors": builder.accessors,
    }
    glb = _pack_glb(document, builder.binary)
    _write_atomic(output, glb)
    return {
        "format": "infamous-runtime-topology-diagnostic-export",
        "version": 1,
        "status": "diagnostic-glb-written",
        "event": event.number,
        "vertices": len(positions),
        "triangles": len(indices) // 3,
        "nondegenerate_triangles": nondegenerate,
        "block_count": len(event.blocks),
        "attribute_count": sum(block.attribute_count for block in event.blocks),
        "position_hypothesis_attribute": position_hypothesis_attribute,
        "position_payload_sha256": block.payload_sha256,
        "index_sha256": event.index_sha256,
        "source_bounds_min": source_min,
        "source_bounds_max": source_max,
        "source_bounds_center": center,
        "recentered_for_inspection": True,
        "bundle_captured_targets": completion["captured_targets"],
        "output_size": len(glb),
        "output_sha256": _sha256(glb),
        "gates": {
            "runtime_topology": True,
            "payload_identity": True,
            "finite_float3_hypothesis": True,
            "draw_ownership": False,
            "xpp_correlation": False,
            "position_semantic": False,
            "uv": False,
            "skin_weights": False,
            "skeleton": False,
            "material": False,
            "rigged_export": False,
            "injection": False,
        },
    }
