"""Fail-closed runtime topology bundle export for visual draw triage."""

from __future__ import annotations

import csv
import hashlib
import math
import re
import struct
from dataclasses import dataclass, replace
from pathlib import Path

from .character_export import _cross_length_squared, _pack_glb, _write_atomic
from .fragment_sampler import (
    FragmentSamplerCensusError,
    analyze_fragment_program_payload,
)
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
_TEXTURE_BINDING_FIELDS = _BINDING_FIELDS + (
    "target_texture_slots",
    "target_texture_sha256s",
    "binding_scope",
    "shader_reference_proven",
    "capture_key",
)
_TEXTURE_TRANSFORM_BINDING_FIELDS = _TEXTURE_BINDING_FIELDS + (
    "vertex_program_file",
    "vertex_program_bytes",
    "transform_constants_file",
    "transform_constants_bytes",
)
_TEXTURE_FRAGMENT_BINDING_FIELDS = _TEXTURE_TRANSFORM_BINDING_FIELDS + (
    "fragment_program_sha256",
    "fragment_program_file",
    "fragment_program_bytes",
    "fragment_referenced_textures_mask",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_BINDING_NAME = re.compile(r"topology-(\d+)-binding\.tsv")
_PAYLOAD_NAME = re.compile(r"topology-(\d+)-(?:index|block-(\d+))-[0-9a-f]{16}\.bin")
_VERTEX_PROGRAM_NAME = re.compile(
    r"topology-(\d+)-vertex-program-([0-9a-f]{16})\.bin"
)
_TRANSFORM_CONSTANTS_NAME = re.compile(
    r"topology-(\d+)-transform-constants-([0-9a-f]{16})\.bin"
)
_FRAGMENT_PROGRAM_NAME = re.compile(
    r"topology-(\d+)-fragment-program-([0-9a-f]{16})\.bin"
)
_MAX_TARGETS = 16
_MAX_TEXTURE_HASHES = 512
_MAX_BOUND_ADDRESSES = 256
_MAX_TEXTURE_ALLOWLIST_BYTES = 40 * 1024
_MAX_BUNDLE_FILES = 1 + _MAX_TARGETS + _MAX_TARGETS * 20
_MAX_INDEX_BYTES = 4 * 1024 * 1024
_MAX_BLOCK_BYTES = 8 * 1024 * 1024
_MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
_VERTEX_PROGRAM_BYTES = 544 * 4 * 4 + 4
_TRANSFORM_CONSTANTS_BYTES = 512 * 4 * 4
_MAX_FRAGMENT_PROGRAM_BYTES = 64 * 1024


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
    vertex_program_file: str | None = None
    transform_constants_file: str | None = None
    fragment_program_sha256: str | None = None
    fragment_program_file: str | None = None
    fragment_program_bytes: int = 0
    fragment_referenced_textures_mask: int = 0
    fragment_sampler_slots: tuple[int, ...] = ()
    fragment_texture_instruction_count: int = 0
    fragment_branch_instruction_count: int = 0
    target_texture_slots: tuple[int, ...] = ()
    target_texture_sha256s: tuple[str, ...] = ()
    binding_scope: str | None = None
    shader_reference_proven: bool = False
    capture_key: str | None = None


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


def _auxiliary_filename(
    value: str, event: int, expected_sha: str, *, constants: bool
) -> str:
    value = _plain_filename(value, "auxiliary payload filename")
    pattern = _TRANSFORM_CONSTANTS_NAME if constants else _VERTEX_PROGRAM_NAME
    match = pattern.fullmatch(value)
    if (
        match is None
        or int(match.group(1), 10) != event
        or match.group(2) != expected_sha[:16]
    ):
        raise RuntimeTopologyExportError(
            "auxiliary payload filename does not match its event/SHA-256"
        )
    return value


def _fragment_program_filename(value: str, event: int, expected_sha: str) -> str:
    value = _plain_filename(value, "fragment program filename")
    match = _FRAGMENT_PROGRAM_NAME.fullmatch(value)
    if (
        match is None
        or int(match.group(1), 10) != event
        or match.group(2) != expected_sha[:16]
    ):
        raise RuntimeTopologyExportError(
            "fragment program filename does not match its event/SHA-256"
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


def _parse_texture_allowlist(path: Path) -> tuple[set[str], str]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeTopologyExportError(
            "texture allowlist is missing or is not a regular file"
        )
    if path.stat().st_size > _MAX_TEXTURE_ALLOWLIST_BYTES:
        raise RuntimeTopologyExportError(
            "texture allowlist exceeds the 40 KiB input bound"
        )
    payload = path.read_bytes()
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise RuntimeTopologyExportError("texture allowlist must be ASCII") from exc
    hashes: set[str] = set()
    for line in lines:
        value = line.partition("#")[0].strip()
        if not value:
            continue
        if _SHA256.fullmatch(value) is None or value in hashes:
            raise RuntimeTopologyExportError(
                "texture allowlist has an invalid or duplicate SHA-256 row"
            )
        hashes.add(value)
        if len(hashes) > _MAX_TEXTURE_HASHES:
            raise RuntimeTopologyExportError(
                "texture allowlist exceeds the 512-hash bound"
            )
    if not hashes:
        raise RuntimeTopologyExportError("texture allowlist is empty")
    return hashes, _sha256(payload)


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
    census_fields = {
        "format",
        "expected_targets",
        "captured_targets",
        "payload_files",
        "payload_bytes",
        "guest_memory_untouched",
    }
    texture_fields = {
        "format",
        "target_texture_hashes",
        "captured_draws",
        "capture_limit",
        "capture_limit_reached",
        "payload_files",
        "payload_bytes",
        "binding_scope",
        "shader_reference_proven",
        "observed_uploads",
        "target_uploads",
        "address_replacements",
        "bound_addresses",
        "guest_memory_untouched",
    }
    capture_format = rows.get("format")
    if capture_format == "if1-topology-census-v1":
        if set(rows) != census_fields:
            raise RuntimeTopologyExportError(
                "capture.complete schema or format is invalid"
            )
        result: dict[str, int | str] = {"format": capture_format}
        for name in census_fields - {"format"}:
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

    if capture_format not in (
        "if1-texture-bound-topology-v1",
        "if1-texture-bound-topology-v2",
        "if1-texture-bound-topology-v3",
    ) or set(rows) != texture_fields:
        raise RuntimeTopologyExportError("capture.complete schema or format is invalid")
    result = {"format": capture_format, "binding_scope": rows["binding_scope"]}
    nonzero_names = {"target_texture_hashes", "capture_limit"}
    numeric_names = texture_fields - {"format", "binding_scope"}
    for name in numeric_names:
        result[name] = _integer(rows[name], name, allow_zero=name not in nonzero_names)
    fragment_bound = capture_format == "if1-texture-bound-topology-v3"
    expected_scope = (
        "fragment-program-static-texture-reference"
        if fragment_bound
        else "enabled-fragment-texture-address"
    )
    expected_reference_proof = 1 if fragment_bound else 0
    if (
        result["guest_memory_untouched"] != 1
        or result["shader_reference_proven"] != expected_reference_proof
        or result["binding_scope"] != expected_scope
    ):
        raise RuntimeTopologyExportError(
            "texture-bound capture has an unsupported binding or mutation claim"
        )
    if (
        result["target_texture_hashes"] > _MAX_TEXTURE_HASHES
        or result["captured_draws"] > _MAX_TARGETS
        or result["capture_limit"] != _MAX_TARGETS
        or result["capture_limit_reached"] not in (0, 1)
        or (
            result["capture_limit_reached"] == 1
            and result["captured_draws"] != _MAX_TARGETS
        )
        or result["payload_files"] > _MAX_TARGETS * (20 if fragment_bound else 19)
        or result["payload_bytes"] > _MAX_PAYLOAD_BYTES
        or result["bound_addresses"] > _MAX_BOUND_ADDRESSES
        or result["target_uploads"] > result["observed_uploads"]
        or result["address_replacements"] > result["observed_uploads"]
    ):
        raise RuntimeTopologyExportError(
            "texture-bound completion totals exceed the bounded contract"
        )
    if (
        (result["captured_draws"] == 0)
        != (result["payload_files"] == 0 and result["payload_bytes"] == 0)
        or (result["captured_draws"] > 0 and result["target_uploads"] == 0)
    ):
        raise RuntimeTopologyExportError(
            "texture-bound capture counters do not reconcile"
        )
    return result


def _parse_binding(
    path: Path,
    *,
    capture_format: str,
    allowed_texture_hashes: set[str] | None,
) -> _Event:
    match = _BINDING_NAME.fullmatch(path.name)
    if path.is_symlink() or not path.is_file() or match is None:
        raise RuntimeTopologyExportError(
            "binding is not a regular topology-N-binding.tsv file"
        )
    with path.open("r", encoding="ascii", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        texture_bound = capture_format in (
            "if1-texture-bound-topology-v1",
            "if1-texture-bound-topology-v2",
            "if1-texture-bound-topology-v3",
        )
        transform_bound = capture_format in (
            "if1-texture-bound-topology-v2",
            "if1-texture-bound-topology-v3",
        )
        fragment_bound = capture_format == "if1-texture-bound-topology-v3"
        expected_fields = (
            _TEXTURE_FRAGMENT_BINDING_FIELDS
            if fragment_bound
            else _TEXTURE_TRANSFORM_BINDING_FIELDS
            if transform_bound
            else _TEXTURE_BINDING_FIELDS
            if texture_bound
            else _BINDING_FIELDS
        )
        if tuple(reader.fieldnames or ()) != expected_fields:
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
    if texture_bound:
        common_names += (
            "target_texture_slots",
            "target_texture_sha256s",
            "binding_scope",
            "shader_reference_proven",
            "capture_key",
        )
    if transform_bound:
        common_names += (
            "vertex_program_file",
            "vertex_program_bytes",
            "transform_constants_file",
            "transform_constants_bytes",
        )
    if fragment_bound:
        common_names += (
            "fragment_program_sha256",
            "fragment_program_file",
            "fragment_program_bytes",
            "fragment_referenced_textures_mask",
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

    target_texture_slots: tuple[int, ...] = ()
    target_texture_sha256s: tuple[str, ...] = ()
    binding_scope: str | None = None
    capture_key: str | None = None
    vertex_program_file: str | None = None
    transform_constants_file: str | None = None
    fragment_program_sha256: str | None = None
    fragment_program_file: str | None = None
    fragment_program_bytes = 0
    fragment_referenced_textures_mask = 0
    if texture_bound:
        if allowed_texture_hashes is None:
            raise RuntimeTopologyExportError(
                "texture-bound bundle requires an explicit texture allowlist"
            )
        slot_fields = common["target_texture_slots"].split(",")
        hash_fields = common["target_texture_sha256s"].split(",")
        target_texture_slots = tuple(
            _integer(value, "target texture slot") for value in slot_fields
        )
        target_texture_sha256s = tuple(
            _sha(value, "target texture SHA-256") for value in hash_fields
        )
        binding_scope = common["binding_scope"]
        capture_key = _sha(common["capture_key"], "capture key")
        if (
            not 1 <= len(target_texture_slots) <= 16
            or len(target_texture_slots) != len(target_texture_sha256s)
            or any(slot > 15 for slot in target_texture_slots)
            or tuple(sorted(set(target_texture_slots))) != target_texture_slots
            or any(value not in allowed_texture_hashes for value in target_texture_sha256s)
            or binding_scope
            != (
                "fragment-program-static-texture-reference"
                if fragment_bound
                else "enabled-fragment-texture-address"
            )
            or _integer(common["shader_reference_proven"], "shader reference proof")
            != (1 if fragment_bound else 0)
        ):
            raise RuntimeTopologyExportError(
                "texture-bound event has an invalid slot/hash/binding claim"
            )
        capture_material = index_sha256 + "".join(
            f":{slot}:{value}"
            for slot, value in zip(target_texture_slots, target_texture_sha256s)
        )
        if transform_bound:
            if (
                _integer(
                    common["vertex_program_bytes"],
                    "vertex program bytes",
                    allow_zero=False,
                )
                != _VERTEX_PROGRAM_BYTES
                or _integer(
                    common["transform_constants_bytes"],
                    "transform constants bytes",
                    allow_zero=False,
                )
                != _TRANSFORM_CONSTANTS_BYTES
            ):
                raise RuntimeTopologyExportError(
                    "vertex transform payload extent is outside the fixed contract"
                )
            vertex_program_file = _auxiliary_filename(
                common["vertex_program_file"],
                event,
                common["vertex_program_sha256"],
                constants=False,
            )
            transform_constants_file = _auxiliary_filename(
                common["transform_constants_file"],
                event,
                common["transform_constants_sha256"],
                constants=True,
            )
        if fragment_bound:
            fragment_program_sha256 = _sha(
                common["fragment_program_sha256"], "fragment program SHA-256"
            )
            fragment_program_bytes = _integer(
                common["fragment_program_bytes"],
                "fragment program bytes",
                allow_zero=False,
            )
            fragment_referenced_textures_mask = _integer(
                common["fragment_referenced_textures_mask"],
                "fragment referenced textures mask",
                allow_zero=False,
            )
            if (
                fragment_program_bytes > _MAX_FRAGMENT_PROGRAM_BYTES
                or fragment_program_bytes % 16
                or fragment_referenced_textures_mask > 0xFFFF
                or any(
                    not fragment_referenced_textures_mask & (1 << slot)
                    for slot in target_texture_slots
                )
            ):
                raise RuntimeTopologyExportError(
                    "fragment sampler proof is outside the bounded contract"
                )
            fragment_program_file = _fragment_program_filename(
                common["fragment_program_file"], event, fragment_program_sha256
            )
            capture_material += f":{fragment_program_sha256}"
        if _sha256(capture_material.encode("ascii")) != capture_key:
            raise RuntimeTopologyExportError(
                "texture-bound event capture key does not reconcile"
            )
    elif allowed_texture_hashes is not None:
        raise RuntimeTopologyExportError(
            "texture allowlist was supplied for a census-only bundle"
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
        vertex_program_file=vertex_program_file,
        transform_constants_file=transform_constants_file,
        fragment_program_sha256=fragment_program_sha256,
        fragment_program_file=fragment_program_file,
        fragment_program_bytes=fragment_program_bytes,
        fragment_referenced_textures_mask=fragment_referenced_textures_mask,
        target_texture_slots=target_texture_slots,
        target_texture_sha256s=target_texture_sha256s,
        binding_scope=binding_scope,
        shader_reference_proven=fragment_bound,
        capture_key=capture_key,
    )


def _load_bundle(
    bundle: Path, texture_allowlist: Path | None
) -> tuple[dict[str, int | str], dict[int, _Event], str | None]:
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
    texture_bound = completion["format"] in (
        "if1-texture-bound-topology-v1",
        "if1-texture-bound-topology-v2",
        "if1-texture-bound-topology-v3",
    )
    transform_bound = completion["format"] in (
        "if1-texture-bound-topology-v2",
        "if1-texture-bound-topology-v3",
    )
    fragment_bound = completion["format"] == "if1-texture-bound-topology-v3"
    allowed_texture_hashes: set[str] | None = None
    allowlist_sha256: str | None = None
    if texture_bound:
        if texture_allowlist is None:
            raise RuntimeTopologyExportError(
                "texture-bound bundle requires --texture-allowlist"
            )
        allowed_texture_hashes, allowlist_sha256 = _parse_texture_allowlist(
            texture_allowlist
        )
        if len(allowed_texture_hashes) != completion["target_texture_hashes"]:
            raise RuntimeTopologyExportError(
                "texture allowlist count does not match capture.complete"
            )
    elif texture_allowlist is not None:
        raise RuntimeTopologyExportError(
            "--texture-allowlist is only valid for a texture-bound bundle"
        )
    binding_paths = sorted(
        entry for entry in entries if _BINDING_NAME.fullmatch(entry.name)
    )
    events = [
        _parse_binding(
            path,
            capture_format=str(completion["format"]),
            allowed_texture_hashes=allowed_texture_hashes,
        )
        for path in binding_paths
    ]
    if len({event.number for event in events}) != len(events):
        raise RuntimeTopologyExportError("bundle has duplicate event numbers")
    if texture_bound and (
        [event.number for event in events] != list(range(1, len(events) + 1))
        or len({event.capture_key for event in events}) != len(events)
    ):
        raise RuntimeTopologyExportError(
            "texture-bound events must be contiguous with unique capture keys"
        )
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
        if transform_bound:
            if (
                event.vertex_program_file is None
                or event.transform_constants_file is None
            ):
                raise RuntimeTopologyExportError(
                    "v2 event is missing vertex transform payload references"
                )
            for filename, expected_size, expected_sha in (
                (
                    event.vertex_program_file,
                    _VERTEX_PROGRAM_BYTES,
                    event.vertex_program_sha256,
                ),
                (
                    event.transform_constants_file,
                    _TRANSFORM_CONSTANTS_BYTES,
                    event.transform_constants_sha256,
                ),
            ):
                if filename in payload_sizes:
                    raise RuntimeTopologyExportError(
                        "one payload file is referenced more than once"
                    )
                payload = _read_payload(bundle, filename, expected_size, expected_sha)
                payload_sizes[filename] = len(payload)
        if fragment_bound:
            if (
                event.fragment_program_file is None
                or event.fragment_program_sha256 is None
            ):
                raise RuntimeTopologyExportError(
                    "v3 event is missing fragment program payload references"
                )
            if event.fragment_program_file in payload_sizes:
                raise RuntimeTopologyExportError(
                    "one payload file is referenced more than once"
                )
            fragment_payload = _read_payload(
                bundle,
                event.fragment_program_file,
                event.fragment_program_bytes,
                event.fragment_program_sha256,
            )
            try:
                fragment_report = analyze_fragment_program_payload(fragment_payload)
            except FragmentSamplerCensusError as exc:
                raise RuntimeTopologyExportError(str(exc)) from exc
            if (
                fragment_report["referenced_textures_mask"]
                != event.fragment_referenced_textures_mask
                or any(
                    slot not in fragment_report["sampler_slots"]
                    for slot in event.target_texture_slots
                )
            ):
                raise RuntimeTopologyExportError(
                    "fragment sampler decode does not reconcile with captured metadata"
                )
            payload_sizes[event.fragment_program_file] = len(fragment_payload)
            events[events.index(event)] = replace(
                event,
                fragment_sampler_slots=tuple(fragment_report["sampler_slots"]),
                fragment_texture_instruction_count=int(
                    fragment_report["texture_instruction_count"]
                ),
                fragment_branch_instruction_count=int(
                    fragment_report["branch_instruction_count"]
                ),
            )
        referenced.add(event.index_payload_file)
        referenced.update(block.payload_file for block in event.blocks)
        if transform_bound:
            referenced.add(event.vertex_program_file)
            referenced.add(event.transform_constants_file)
        if fragment_bound:
            referenced.add(event.fragment_program_file)
    if {entry.name for entry in entries} != referenced:
        raise RuntimeTopologyExportError(
            "bundle has missing or unreferenced extra files"
        )
    captured_name = "captured_draws" if texture_bound else "captured_targets"
    if (
        completion[captured_name] != len(events)
        or completion["payload_files"] != len(payload_sizes)
        or completion["payload_bytes"] != sum(payload_sizes.values())
    ):
        raise RuntimeTopologyExportError(
            "completion totals do not reconcile with the bundle"
        )
    return completion, {event.number: event for event in events}, allowlist_sha256


def census_runtime_fragment_samplers(
    bundle: Path, texture_allowlist: Path
) -> dict:
    """Validate a complete v3 bundle and emit a payload-free sampler census."""

    completion, events, allowlist_sha256 = _load_bundle(bundle, texture_allowlist)
    if completion["format"] != "if1-texture-bound-topology-v3":
        raise RuntimeTopologyExportError(
            "fragment sampler census requires if1-texture-bound-topology-v3"
        )
    rows = []
    for event in events.values():
        rows.append(
            {
                "event": event.number,
                "draw_event": event.draw_event,
                "index_sha256": event.index_sha256,
                "target_texture_slots": list(event.target_texture_slots),
                "target_texture_sha256s": list(event.target_texture_sha256s),
                "fragment_program_sha256": event.fragment_program_sha256,
                "fragment_program_bytes": event.fragment_program_bytes,
                "referenced_textures_mask": event.fragment_referenced_textures_mask,
                "sampler_slots": list(event.fragment_sampler_slots),
                "texture_instruction_count": event.fragment_texture_instruction_count,
                "branch_instruction_count": event.fragment_branch_instruction_count,
                "target_slots_statically_referenced": True,
                "runtime_branch_execution_proved": False,
                "draw_ownership_proved": False,
                "material_semantic_proved": False,
            }
        )
    return {
        "format": "infamous-runtime-fragment-sampler-census",
        "version": 1,
        "status": "fragment-sampler-census-complete",
        "bundle_format": completion["format"],
        "captured_draws": completion["captured_draws"],
        "texture_allowlist_sha256": allowlist_sha256,
        "events": rows,
        "totals": {
            "events": len(rows),
            "target_slots": sum(len(row["target_texture_slots"]) for row in rows),
            "texture_instructions": sum(
                int(row["texture_instruction_count"]) for row in rows
            ),
            "events_with_branches": sum(
                int(row["branch_instruction_count"]) > 0 for row in rows
            ),
        },
        "gates": {
            "bundle_complete": True,
            "fragment_payload_identity": True,
            "independent_sampler_decode": True,
            "captured_mask_reconciled": True,
            "target_slots_statically_referenced": True,
            "runtime_branch_execution": False,
            "draw_ownership": False,
            "material_semantic": False,
            "full_character": False,
        },
    }


def export_runtime_topology_glb(
    bundle: Path,
    event_number: int,
    output: Path,
    *,
    position_hypothesis_attribute: int,
    texture_allowlist: Path | None = None,
) -> dict:
    """Validate one complete runtime bundle and export one selected event for inspection."""

    bundle_resolved = bundle.resolve()
    output_resolved = output.resolve()
    if output_resolved == bundle_resolved or bundle_resolved in output_resolved.parents:
        raise RuntimeTopologyExportError(
            "diagnostic output must remain outside the immutable input bundle"
        )
    if output.is_symlink() or output.exists():
        raise RuntimeTopologyExportError(
            "diagnostic output already exists; refusing to overwrite it"
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
    completion, events, allowlist_sha256 = _load_bundle(bundle, texture_allowlist)
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
    ):
        raise RuntimeTopologyExportError(
            "selected attribute is not a bounded zero-frequency float32x3 stream"
        )
    range_end = block.range_first + block.range_count
    if (
        min(indices) < block.range_first
        or max(indices) >= range_end
        or block.payload_bytes != block.range_count * block.stride
    ):
        raise RuntimeTopologyExportError(
            "indices and selected position block do not reconcile"
        )
    local_indices = tuple(index - block.range_first for index in indices)
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
            source_positions[local_indices[offset]],
            source_positions[local_indices[offset + 1]],
            source_positions[local_indices[offset + 2]],
        )
        > 1e-12
        for offset in range(0, len(local_indices), 3)
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
        struct.pack(f"<{len(local_indices)}H", *local_indices),
        5123,
        len(local_indices),
        "SCALAR",
        34963,
    )
    texture_bound = completion["format"] in (
        "if1-texture-bound-topology-v1",
        "if1-texture-bound-topology-v2",
        "if1-texture-bound-topology-v3",
    )
    transform_bound = completion["format"] in (
        "if1-texture-bound-topology-v2",
        "if1-texture-bound-topology-v3",
    )
    fragment_bound = completion["format"] == "if1-texture-bound-topology-v3"
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
        "sourceRangeFirst": block.range_first,
        "indicesRebasedForInspection": block.range_first != 0,
    }
    if texture_bound:
        evidence.update(
            {
                "textureBoundCorrelation": True,
                "textureIdentityCorrelationProved": True,
                "targetTextureSlots": list(event.target_texture_slots),
                "targetTextureSha256s": list(event.target_texture_sha256s),
                "bindingScope": event.binding_scope,
                "shaderReferenceProved": event.shader_reference_proven,
                "captureKey": event.capture_key,
                "textureAllowlistSha256": allowlist_sha256,
                "vertexTransformPayloadIdentityProved": transform_bound,
                "fragmentProgramPayloadIdentityProved": fragment_bound,
                "fragmentReferencedTexturesMask": event.fragment_referenced_textures_mask,
                "fragmentSamplerSlots": list(event.fragment_sampler_slots),
                "runtimeBranchExecutionProved": False,
            }
        )
    document = {
        "asset": {
            "version": "2.0",
            "generator": (
                "xpp-tool 2.15.0 fragment-referenced runtime topology diagnostic exporter"
                if fragment_bound
                else "xpp-tool 2.12.0 transform-bound runtime topology diagnostic exporter"
                if transform_bound
                else "xpp-tool 2.11.0 texture-bound runtime topology diagnostic exporter"
                if texture_bound
                else "xpp-tool 2.8.0 runtime topology diagnostic exporter"
            ),
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
    report = {
        "format": "infamous-runtime-topology-diagnostic-export",
        "version": 4 if fragment_bound else 3 if transform_bound else 2 if texture_bound else 1,
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
        "source_range_first": block.range_first,
        "indices_rebased_for_inspection": block.range_first != 0,
        "source_bounds_min": source_min,
        "source_bounds_max": source_max,
        "source_bounds_center": center,
        "recentered_for_inspection": True,
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
    if texture_bound:
        report.update(
            {
                "bundle_format": completion["format"],
                "bundle_captured_draws": completion["captured_draws"],
                "texture_bound_correlation": True,
                "texture_identity_correlation_proved": True,
                "target_texture_slots": list(event.target_texture_slots),
                "target_texture_sha256s": list(event.target_texture_sha256s),
                "binding_scope": event.binding_scope,
                "shader_reference_proved": event.shader_reference_proven,
                "capture_key": event.capture_key,
                "texture_allowlist_sha256": allowlist_sha256,
                "vertex_transform_payloads_proved": transform_bound,
                "fragment_program_payload_proved": fragment_bound,
                "fragment_referenced_textures_mask": event.fragment_referenced_textures_mask,
                "fragment_sampler_slots": list(event.fragment_sampler_slots),
                "fragment_texture_instruction_count": event.fragment_texture_instruction_count,
                "fragment_branch_instruction_count": event.fragment_branch_instruction_count,
                "runtime_branch_execution_proved": False,
            }
        )
        report["gates"]["texture_identity_correlation"] = True
        report["gates"]["vertex_transform_payload_identity"] = transform_bound
        report["gates"]["fragment_program_payload_identity"] = fragment_bound
        report["gates"]["static_shader_reference"] = fragment_bound
    else:
        report["bundle_captured_targets"] = completion["captured_targets"]
    return report
