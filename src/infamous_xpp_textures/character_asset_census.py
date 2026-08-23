"""Bounded multipart-character name, texture, and cross-package census."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .character import CharacterReportError, build_xpp_character_report
from .heap import align_up, heap_bytes, level_size
from .validation import ValidationError, validate_xpp
from .xpp import parse_xpp


MAX_WORKSPACE_BYTES = 16 * 1024 * 1024
MAX_OID_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_OID_RECORDS = 1_000_000
MAX_PROFILE_PACKAGES = 4096
MAX_PROFILE_BYTES = 8 * 1024 * 1024 * 1024
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_ANCHOR_WINDOW = 512
MAX_DETAILED_MATCHES = 20_000
MIN_DETAILED_PARTIAL_BYTES = 4096
OBJECT_CHUNK = 0x01100000
GEOMETRY_HEAP_CHUNK = 0x0B800000


class CharacterAssetCensusError(ValueError):
    """Raised when a profile or census request is unsafe or ambiguous."""


@dataclass(frozen=True)
class ProfileEntry:
    archive_slot: str
    manifest_name: str
    relative_path: str
    byte_count: int
    sha256: str
    path: Path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _valid_sha256(value: str) -> bool:
    return (
        len(value) == 64
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _read_regular(path: Path, maximum: int, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise CharacterAssetCensusError(
            f"{label} must be an existing regular non-symlink file"
        )
    size = path.stat().st_size
    if not 0 < size <= maximum:
        raise CharacterAssetCensusError(
            f"{label} is empty or exceeds the {maximum}-byte bound"
        )
    data = path.read_bytes()
    if len(data) != size:
        raise CharacterAssetCensusError(f"{label} changed while it was read")
    return data


def _read_pinned(path: Path, expected_sha256: str, maximum: int, label: str) -> bytes:
    if not _valid_sha256(expected_sha256):
        raise CharacterAssetCensusError(f"{label} SHA-256 pin is not canonical")
    data = _read_regular(path, maximum, label)
    actual = _sha256(data)
    if actual != expected_sha256:
        raise CharacterAssetCensusError(
            f"{label} SHA-256 mismatch: expected {expected_sha256}, found {actual}"
        )
    return data


def load_oid_manifest(path: Path, expected_sha256: str) -> tuple[list[str], dict]:
    """Load the proven newline/ordinal OID schema without guessing delimiters."""

    data = _read_pinned(path, expected_sha256, MAX_OID_MANIFEST_BYTES, "OID manifest")
    if not data.endswith(b"\n"):
        raise CharacterAssetCensusError("OID manifest is not LF terminated")
    if any(byte != 0x0A and not 0x20 <= byte <= 0x7E for byte in data):
        raise CharacterAssetCensusError(
            "OID manifest contains bytes outside printable ASCII and LF"
        )
    rows = data[:-1].split(b"\n")
    if not rows or len(rows) > MAX_OID_RECORDS or any(not row for row in rows):
        raise CharacterAssetCensusError(
            "OID manifest has no rows or contains an empty row"
        )
    names = [row.decode("ascii") for row in rows]
    return names, {
        "sha256": expected_sha256,
        "bytes": len(data),
        "records": len(names),
        "ordinal_oid_schema": True,
    }


def _workspace_entries(
    root: Path, expected_sha256: str, label: str
) -> tuple[list[ProfileEntry], dict]:
    workspace_path = root / "workspace.json"
    raw = _read_pinned(
        workspace_path,
        expected_sha256,
        MAX_WORKSPACE_BYTES,
        f"{label} workspace manifest",
    )
    try:
        workspace = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CharacterAssetCensusError(
            f"{label} workspace manifest is not valid JSON"
        ) from error
    if workspace.get("kind") != "xpp-workspace" or workspace.get("schema_version") != 1:
        raise CharacterAssetCensusError(f"{label} workspace schema is unsupported")
    archives = workspace.get("archives")
    if not isinstance(archives, list) or not archives:
        raise CharacterAssetCensusError(f"{label} workspace has no archives")

    root_resolved = root.resolve()
    entries: list[ProfileEntry] = []
    seen: set[str] = set()
    total_bytes = 0
    for archive in archives:
        slot = archive.get("slot")
        raw_entries = archive.get("entries")
        if not isinstance(slot, str) or not isinstance(raw_entries, list):
            raise CharacterAssetCensusError(f"{label} workspace archive is malformed")
        for raw_entry in raw_entries:
            extracted = raw_entry.get("extracted")
            if extracted is None:
                continue
            if not isinstance(extracted, str):
                raise CharacterAssetCensusError(
                    f"{label} workspace extracted path is not a string"
                )
            relative = PurePosixPath(extracted)
            if relative.is_absolute() or ".." in relative.parts:
                raise CharacterAssetCensusError(
                    f"{label} workspace extracted path escapes the profile"
                )
            canonical = relative.as_posix()
            if canonical in seen:
                raise CharacterAssetCensusError(
                    f"{label} workspace repeats extracted path {canonical}"
                )
            seen.add(canonical)
            byte_count = raw_entry.get("bytes")
            digest = raw_entry.get("sha256")
            manifest_name = raw_entry.get("name")
            if (
                not isinstance(byte_count, int)
                or not 0 < byte_count <= MAX_PACKAGE_BYTES
                or not isinstance(digest, str)
                or not _valid_sha256(digest)
                or not isinstance(manifest_name, str)
            ):
                raise CharacterAssetCensusError(
                    f"{label} workspace entry {canonical} has invalid metadata"
                )
            path = (root / Path(*relative.parts)).resolve()
            if root_resolved not in path.parents:
                raise CharacterAssetCensusError(
                    f"{label} workspace entry {canonical} escapes the profile"
                )
            entries.append(
                ProfileEntry(
                    archive_slot=slot,
                    manifest_name=manifest_name,
                    relative_path=canonical,
                    byte_count=byte_count,
                    sha256=digest,
                    path=path,
                )
            )
            total_bytes += byte_count
    if not entries or len(entries) > MAX_PROFILE_PACKAGES:
        raise CharacterAssetCensusError(
            f"{label} profile count is zero or exceeds {MAX_PROFILE_PACKAGES}"
        )
    if total_bytes > MAX_PROFILE_BYTES:
        raise CharacterAssetCensusError(
            f"{label} profile exceeds the {MAX_PROFILE_BYTES}-byte bound"
        )
    return entries, {
        "label": label,
        "workspace_sha256": expected_sha256,
        "workspace_bytes": len(raw),
        "package_count": len(entries),
        "declared_package_bytes": total_bytes,
    }


def _entry_by_relative(entries: list[ProfileEntry], relative_path: str) -> ProfileEntry:
    matches = [entry for entry in entries if entry.relative_path == relative_path]
    if len(matches) != 1:
        raise CharacterAssetCensusError(
            f"target {relative_path!r} resolves to {len(matches)} workspace entries"
        )
    return matches[0]


def _read_profile_entry(entry: ProfileEntry, label: str) -> bytes:
    data = _read_regular(entry.path, MAX_PACKAGE_BYTES, label)
    if len(data) != entry.byte_count:
        raise CharacterAssetCensusError(
            f"{label} byte count differs from the workspace manifest"
        )
    digest = _sha256(data)
    if digest != entry.sha256:
        raise CharacterAssetCensusError(
            f"{label} SHA-256 differs from the workspace manifest"
        )
    return data


def _descriptor_parts(data: bytes, names: list[str]) -> list[dict]:
    parsed = parse_xpp(data, len(data))
    _summary, records = validate_xpp(data)
    texels = heap_bytes(data, parsed)
    result: list[dict] = []
    for record in records:
        oid = struct.unpack_from(">I", record.raw, 0x20)[0]
        name = names[oid] if oid < len(names) else None
        face_stride = align_up(record.chain_bytes)
        face_payloads: list[bytes] = []
        mip_rows: list[dict] = []
        for face in range(record.faces):
            face_start = record.heap_offset + face * face_stride
            face_payload = texels[face_start : face_start + record.chain_bytes]
            face_payloads.append(face_payload)
            cursor = 0
            prefix = bytearray()
            for level in range(record.mips):
                byte_count = level_size(
                    record.format, record.width, record.height, level
                )
                mip = face_payload[cursor : cursor + byte_count]
                cursor += byte_count
                prefix.extend(mip)
                mip_rows.append(
                    {
                        "face": face,
                        "level": level,
                        "width": max(1, record.width >> level),
                        "height": max(1, record.height >> level),
                        "bytes": len(mip),
                        "sha256": _sha256(mip),
                        "prefix_bytes": len(prefix),
                        "prefix_sha256": _sha256(bytes(prefix)),
                    }
                )
        payload = b"".join(face_payloads)
        stem = name.rsplit(".", 1)[0] if name else None
        suffix = None
        family = stem
        if stem and "_" in stem:
            candidate = stem.rsplit("_", 1)[1]
            if candidate.upper() in {"A", "C", "N", "S", "GM", "RM", "GRAMP"}:
                suffix = candidate
                family = stem[: -(len(candidate) + 1)]
        result.append(
            {
                "index": record.index,
                "oid": oid,
                "name": name,
                "family": family,
                "name_suffix": suffix,
                "format": f"0x{record.format:02x}",
                "width": record.width,
                "height": record.height,
                "mips": record.mips,
                "faces": record.faces,
                "payload_bytes": len(payload),
                "payload_sha256": _sha256(payload),
                "mip_rows": mip_rows,
                "atlas_name_evidence": bool(name and "atlas" in name.casefold()),
                "compound_surface_name_evidence": bool(
                    family and family.count("_") >= 4
                ),
                "strip_shaped": min(record.width, record.height) <= 8,
            }
        )
    return result


def _descriptor_signature(descriptor: dict) -> tuple:
    return (
        descriptor["format"],
        descriptor["width"],
        descriptor["height"],
        descriptor["mips"],
        descriptor["faces"],
        descriptor["payload_sha256"],
    )


def _target_maps(descriptors: list[dict]) -> dict:
    exact: dict[tuple, list[int]] = defaultdict(list)
    prefixes: dict[tuple, list[tuple[int, int, int]]] = defaultdict(list)
    mips: dict[tuple, list[tuple[int, int, int]]] = defaultdict(list)
    for descriptor in descriptors:
        index = descriptor["index"]
        exact[_descriptor_signature(descriptor)].append(index)
        for row in descriptor["mip_rows"]:
            mip_key = (
                descriptor["format"],
                row["width"],
                row["height"],
                row["bytes"],
                row["sha256"],
            )
            mips[mip_key].append((index, row["face"], row["level"]))
            if row["level"] + 1 < descriptor["mips"]:
                prefix_key = (
                    descriptor["format"],
                    descriptor["width"],
                    descriptor["height"],
                    row["face"],
                    row["level"] + 1,
                    row["prefix_bytes"],
                    row["prefix_sha256"],
                )
                prefixes[prefix_key].append((index, row["face"], row["level"] + 1))
    return {"exact": exact, "prefixes": prefixes, "mips": mips}


def _record_match(
    matches: list[dict],
    *,
    build: str,
    entry: ProfileEntry,
    descriptor: dict,
    target_descriptor: int,
    match_kind: str,
    matched_bytes: int,
    target_level: int | None = None,
    candidate_level: int | None = None,
) -> None:
    if len(matches) >= MAX_DETAILED_MATCHES:
        raise CharacterAssetCensusError(
            f"detailed texture matches exceed {MAX_DETAILED_MATCHES}; refine the census"
        )
    row = {
        "build": build,
        "package": entry.relative_path,
        "package_sha256": entry.sha256,
        "descriptor": descriptor["index"],
        "descriptor_name": descriptor["name"],
        "target_descriptor": target_descriptor,
        "match_kind": match_kind,
        "matched_bytes": matched_bytes,
    }
    if target_level is not None:
        row["target_level"] = target_level
    if candidate_level is not None:
        row["candidate_level"] = candidate_level
    matches.append(row)


def _scan_profile(
    entries: list[ProfileEntry],
    names: list[str],
    target_maps: dict,
    *,
    build: str,
) -> tuple[dict, list[dict]]:
    matches: list[dict] = []
    texture_descriptors = 0
    packages_with_textures = 0
    verified_bytes = 0
    partial_counts = Counter()
    for entry in entries:
        data = _read_profile_entry(entry, f"{build} package {entry.relative_path}")
        verified_bytes += len(data)
        try:
            descriptors = _descriptor_parts(data, names)
        except (ValidationError, ValueError) as error:
            raise CharacterAssetCensusError(
                f"{build} package {entry.relative_path} failed texture validation: {error}"
            ) from error
        texture_descriptors += len(descriptors)
        packages_with_textures += bool(descriptors)
        for descriptor in descriptors:
            exact_targets = target_maps["exact"].get(
                _descriptor_signature(descriptor), []
            )
            for target in exact_targets:
                _record_match(
                    matches,
                    build=build,
                    entry=entry,
                    descriptor=descriptor,
                    target_descriptor=target,
                    match_kind="exact-descriptor",
                    matched_bytes=descriptor["payload_bytes"],
                )
            exact_for_target = set(exact_targets)
            for row in descriptor["mip_rows"]:
                mip_key = (
                    descriptor["format"],
                    row["width"],
                    row["height"],
                    row["bytes"],
                    row["sha256"],
                )
                for target, _face, target_level in target_maps["mips"].get(mip_key, []):
                    if target in exact_for_target:
                        continue
                    bucket = (
                        "detailed"
                        if row["bytes"] >= MIN_DETAILED_PARTIAL_BYTES
                        else "small"
                    )
                    partial_counts[(bucket, row["bytes"])] += 1
                    if row["bytes"] >= MIN_DETAILED_PARTIAL_BYTES:
                        _record_match(
                            matches,
                            build=build,
                            entry=entry,
                            descriptor=descriptor,
                            target_descriptor=target,
                            match_kind="shared-mip",
                            matched_bytes=row["bytes"],
                            target_level=target_level,
                            candidate_level=row["level"],
                        )
                if row["level"] + 1 >= descriptor["mips"]:
                    continue
                prefix_key = (
                    descriptor["format"],
                    descriptor["width"],
                    descriptor["height"],
                    row["face"],
                    row["level"] + 1,
                    row["prefix_bytes"],
                    row["prefix_sha256"],
                )
                for target, _face, _levels in target_maps["prefixes"].get(
                    prefix_key, []
                ):
                    if target in exact_for_target:
                        continue
                    partial_counts[("prefix", row["prefix_bytes"])] += 1
                    if row["prefix_bytes"] >= MIN_DETAILED_PARTIAL_BYTES:
                        _record_match(
                            matches,
                            build=build,
                            entry=entry,
                            descriptor=descriptor,
                            target_descriptor=target,
                            match_kind="shared-leading-mip-prefix",
                            matched_bytes=row["prefix_bytes"],
                        )
    return (
        {
            "build": build,
            "verified_packages": len(entries),
            "verified_package_bytes": verified_bytes,
            "packages_with_textures": packages_with_textures,
            "texture_descriptors": texture_descriptors,
            "detailed_match_count": len(matches),
            "partial_match_histogram": [
                {"class": kind, "bytes": size, "count": count}
                for (kind, size), count in sorted(partial_counts.items())
            ],
        },
        matches,
    )


def _chunk_type_for_offset(parsed, offset: int) -> list[str]:
    return [
        f"0x{chunk.type_tag:08x}"
        for chunk in parsed.chunks
        if chunk.offset <= offset < chunk.offset + chunk.size
    ]


def _name_references(
    data: bytes,
    names: list[str],
    *,
    anchor: str,
    token: str,
    window_before: int,
    window_after: int,
    descriptors: list[dict],
) -> dict:
    if (
        not 0 <= window_before <= MAX_ANCHOR_WINDOW
        or not 0 <= window_after <= MAX_ANCHOR_WINDOW
    ):
        raise CharacterAssetCensusError(
            f"anchor windows must be between zero and {MAX_ANCHOR_WINDOW}"
        )
    anchors = [index for index, name in enumerate(names) if name == anchor]
    if len(anchors) != 1:
        raise CharacterAssetCensusError(
            f"manifest anchor {anchor!r} resolves to {len(anchors)} rows"
        )
    anchor_index = anchors[0]
    window_start = max(0, anchor_index - window_before)
    window_end = min(len(names), anchor_index + window_after + 1)
    parsed = parse_xpp(data, len(data))
    payload = data[parsed.data_offset : parsed.data_offset + parsed.data_size]
    hits: Counter[int] = Counter()
    hit_offsets: dict[int, list[int]] = defaultdict(list)
    for offset in range(0, len(payload) - 3, 4):
        value = struct.unpack_from(">I", payload, offset)[0]
        if value < len(names):
            hits[value] += 1
            hit_offsets[value].append(offset)
    descriptor_indices: dict[int, list[int]] = defaultdict(list)
    for descriptor in descriptors:
        descriptor_indices[descriptor["oid"]].append(descriptor["index"])
    folded_token = token.casefold()
    selected = sorted(
        oid
        for oid in hits
        if window_start <= oid < window_end or folded_token in names[oid].casefold()
    )
    rows = []
    for oid in selected:
        chunk_counts = Counter(
            chunk_type
            for offset in hit_offsets[oid]
            for chunk_type in _chunk_type_for_offset(parsed, offset)
        )
        rows.append(
            {
                "oid": oid,
                "name": names[oid],
                "aligned_reference_count": hits[oid],
                "chunk_type_counts": dict(sorted(chunk_counts.items())),
                "descriptor_indices": descriptor_indices.get(oid, []),
                "in_anchor_window": window_start <= oid < window_end,
                "token_match": folded_token in names[oid].casefold(),
            }
        )
    return {
        "anchor": anchor,
        "anchor_oid": anchor_index,
        "window_before": window_before,
        "window_after": window_after,
        "window_start_oid": window_start,
        "window_end_oid_exclusive": window_end,
        "token": token,
        "aligned_referenced_names": rows,
        "aligned_referenced_name_count": len(rows),
        "object_chunk_name_count": sum(
            OBJECT_CHUNK in {int(key, 16) for key in row["chunk_type_counts"]}
            for row in rows
        ),
        "geometry_heap_name_count": sum(
            GEOMETRY_HEAP_CHUNK in {int(key, 16) for key in row["chunk_type_counts"]}
            for row in rows
        ),
        "descriptor_named_count": sum(bool(row["descriptor_indices"]) for row in rows),
        "relationship_limit": (
            "an aligned OID word proves a package/chunk reference, not that a named object owns "
            "one of the packed geometry records or samples one texture descriptor"
        ),
    }


def _cross_build_mapping(left: list[dict], right: list[dict]) -> dict:
    left_groups: dict[tuple, list[int]] = defaultdict(list)
    right_groups: dict[tuple, list[int]] = defaultdict(list)
    for descriptor in left:
        left_groups[_descriptor_signature(descriptor)].append(descriptor["index"])
    for descriptor in right:
        right_groups[_descriptor_signature(descriptor)].append(descriptor["index"])
    mapping = []
    ambiguous = []
    missing_left = []
    missing_right = []
    for signature in sorted(set(left_groups) | set(right_groups), key=repr):
        left_indices = left_groups.get(signature, [])
        right_indices = right_groups.get(signature, [])
        if len(left_indices) == len(right_indices) == 1:
            mapping.append({"left": left_indices[0], "right": right_indices[0]})
        elif not left_indices:
            missing_left.extend(right_indices)
        elif not right_indices:
            missing_right.extend(left_indices)
        else:
            ambiguous.append(
                {"left_indices": left_indices, "right_indices": right_indices}
            )
    mapping.sort(key=lambda row: row["left"])
    complete = (
        len(mapping) == len(left) == len(right)
        and not ambiguous
        and not missing_left
        and not missing_right
    )
    return {
        "complete_unique_match": complete,
        "unique_matches": len(mapping),
        "reordered_matches": sum(row["left"] != row["right"] for row in mapping),
        "mapping": mapping,
        "ambiguous_groups": ambiguous,
        "missing_from_left": sorted(missing_left),
        "missing_from_right": sorted(missing_right),
    }


def _character_contract_summary(data: bytes, source_name: str) -> dict:
    """Keep the census reusable for texture-only/static items as well as characters."""

    try:
        report = build_xpp_character_report(data, source_name)
    except CharacterReportError as error:
        return {
            "status": "not-a-proved-skinned-character-envelope",
            "geometry_contract_count": 0,
            "topology_proved": False,
            "reason": str(error),
        }
    return {
        "status": "proved-skinned-character-envelope"
        if report["topology_proved"]
        else "no-proved-skinned-geometry-contract",
        "geometry_contract_count": len(report["contracts"]),
        "topology_proved": report["topology_proved"],
    }


def build_character_asset_census(
    left_profile: Path,
    right_profile: Path,
    left_workspace_sha256: str,
    right_workspace_sha256: str,
    left_oid_manifest: Path,
    right_oid_manifest: Path,
    left_oid_manifest_sha256: str,
    right_oid_manifest_sha256: str,
    left_target: str,
    right_target: str,
    *,
    anchor: str,
    name_token: str,
    anchor_before: int,
    anchor_after: int,
) -> dict:
    """Audit one multipart character across two complete extracted profiles."""

    if left_profile.resolve() == right_profile.resolve():
        raise CharacterAssetCensusError(
            "left and right profiles must be different roots"
        )
    if (
        not name_token
        or not name_token.isascii()
        or any(character.isspace() for character in name_token)
    ):
        raise CharacterAssetCensusError(
            "name token must be nonempty ASCII without whitespace"
        )

    left_names, left_manifest = load_oid_manifest(
        left_oid_manifest, left_oid_manifest_sha256
    )
    right_names, right_manifest = load_oid_manifest(
        right_oid_manifest, right_oid_manifest_sha256
    )
    left_entries, left_profile_summary = _workspace_entries(
        left_profile, left_workspace_sha256, "left"
    )
    right_entries, right_profile_summary = _workspace_entries(
        right_profile, right_workspace_sha256, "right"
    )
    left_entry = _entry_by_relative(left_entries, left_target)
    right_entry = _entry_by_relative(right_entries, right_target)
    left_data = _read_profile_entry(left_entry, "left target XPP")
    right_data = _read_profile_entry(right_entry, "right target XPP")
    left_descriptors = _descriptor_parts(left_data, left_names)
    right_descriptors = _descriptor_parts(right_data, right_names)
    if not left_descriptors or not right_descriptors:
        raise CharacterAssetCensusError("both target packages must contain textures")
    left_character = _character_contract_summary(left_data, Path(left_target).name)
    right_character = _character_contract_summary(right_data, Path(right_target).name)
    left_names_report = _name_references(
        left_data,
        left_names,
        anchor=anchor,
        token=name_token,
        window_before=anchor_before,
        window_after=anchor_after,
        descriptors=left_descriptors,
    )
    right_names_report = _name_references(
        right_data,
        right_names,
        anchor=anchor,
        token=name_token,
        window_before=anchor_before,
        window_after=anchor_after,
        descriptors=right_descriptors,
    )
    target_maps = _target_maps(left_descriptors)
    left_scan, left_matches = _scan_profile(
        left_entries, left_names, target_maps, build="left"
    )
    right_scan, right_matches = _scan_profile(
        right_entries, right_names, target_maps, build="right"
    )
    matches = sorted(
        left_matches + right_matches,
        key=lambda row: (
            row["target_descriptor"],
            row["match_kind"],
            row["build"],
            row["package"],
            row["descriptor"],
            row.get("candidate_level", -1),
        ),
    )
    cross_build = _cross_build_mapping(left_descriptors, right_descriptors)
    exact_non_target = [
        row
        for row in matches
        if row["match_kind"] == "exact-descriptor"
        and not (row["build"] == "left" and row["package"] == left_entry.relative_path)
        and not (
            row["build"] == "right" and row["package"] == right_entry.relative_path
        )
    ]
    substantive_exact_non_target = [
        row
        for row in exact_non_target
        if row["matched_bytes"] >= MIN_DETAILED_PARTIAL_BYTES
    ]
    substantive_partial = [
        row
        for row in matches
        if row["match_kind"] != "exact-descriptor"
        and row["matched_bytes"] >= MIN_DETAILED_PARTIAL_BYTES
    ]
    return {
        "format": "infamous-character-asset-census",
        "version": 1,
        "scope": {
            "character_or_item_agnostic": True,
            "single_target_pair_per_report": True,
            "first_audit_target_only": True,
            "corpus_batching_requires_completion_inventory": True,
            "existing_completion_inventory_consumed": False,
        },
        "profiles": {"left": left_profile_summary, "right": right_profile_summary},
        "oid_manifests": {"left": left_manifest, "right": right_manifest},
        "targets": {
            "left": {
                "relative_path": left_entry.relative_path,
                "bytes": left_entry.byte_count,
                "sha256": left_entry.sha256,
                "texture_descriptor_count": len(left_descriptors),
                "geometry_contract_count": left_character["geometry_contract_count"],
                "geometry_contract_status": left_character["status"],
            },
            "right": {
                "relative_path": right_entry.relative_path,
                "bytes": right_entry.byte_count,
                "sha256": right_entry.sha256,
                "texture_descriptor_count": len(right_descriptors),
                "geometry_contract_count": right_character["geometry_contract_count"],
                "geometry_contract_status": right_character["status"],
            },
        },
        "target_texture_descriptors": {
            "left": left_descriptors,
            "right": right_descriptors,
        },
        "target_name_references": {
            "left": left_names_report,
            "right": right_names_report,
        },
        "cross_build_texture_mapping": cross_build,
        "profile_scans": {"left": left_scan, "right": right_scan},
        "texture_matches": matches,
        "exact_descriptor_matches_outside_cross_build_target": exact_non_target,
        "substantive_exact_descriptor_matches_outside_cross_build_target": (
            substantive_exact_non_target
        ),
        "substantive_partial_texture_matches": substantive_partial,
        "findings": {
            "multipart_package_names_proved": bool(
                left_names_report["object_chunk_name_count"]
                and right_names_report["object_chunk_name_count"]
            ),
            "named_texture_descriptors_proved": all(
                descriptor["name"] is not None
                for descriptor in left_descriptors + right_descriptors
            ),
            "cross_build_target_texture_identity_proved": cross_build[
                "complete_unique_match"
            ],
            "exact_texture_sharing_outside_target": bool(exact_non_target),
            "substantive_exact_texture_sharing_outside_target": bool(
                substantive_exact_non_target
            ),
            "substantive_partial_texture_sharing": bool(substantive_partial),
            "only_small_exact_utility_sharing_outside_target": bool(exact_non_target)
            and not substantive_exact_non_target,
            "atlas_semantics_proved": False,
            "compound_surface_naming_present": any(
                descriptor["compound_surface_name_evidence"]
                for descriptor in left_descriptors
            ),
            "geometry_to_name_binding_proved": False,
            "geometry_to_texture_binding_proved": False,
            "clothing_state_lod_package_binding_proved": False,
            "complete_character_proved": False,
            "renderable_model_proved": False,
            "safe_injection_proved": False,
        },
        "completion_gates": {
            "required_piece_inventory_complete": False,
            "object_space_orientation_proved": False,
            "piece_alignment_proved": False,
            "uv_binding_proved": False,
            "material_texture_binding_proved": False,
            "no_missing_required_pieces": False,
            "no_duplicate_pieces": False,
            "no_mistextured_pieces": False,
            "lod_state_flavor_selection_proved": False,
            "blender_glb_complete": False,
            "beauty_character_study_complete": False,
        },
        "delivery_gates": {
            "rpcs3_emulator_mod_round_trip": False,
            "native_decomp_asset_import": False,
            "canonical_asset_manifest_shared": False,
        },
        "limitations": (
            "manifest names, aligned package/chunk OID references, descriptor OID names, "
            "texture-byte identities, and proved character topology counts are separate evidence; "
            "the census does not bind one named object to one geometry record, bind geometry to "
            "materials/textures, assign suffix semantics, prove atlas UV regions, identify a "
            "clothing/state/LOD loader relation, assemble a full character, or authorize injection"
        ),
    }


def render_character_asset_census(report: dict) -> bytes:
    rendered = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(rendered) > MAX_REPORT_BYTES:
        raise CharacterAssetCensusError(
            f"character asset census exceeds the {MAX_REPORT_BYTES}-byte report bound"
        )
    return rendered


def write_new_character_asset_census(path: Path, report: dict) -> None:
    """Publish a deterministic report without overwriting a concurrent file."""

    if path.is_symlink() or path.exists():
        raise CharacterAssetCensusError(
            "character asset census output already exists; refusing to overwrite it"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_character_asset_census(report)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise CharacterAssetCensusError(
                "character asset census output appeared during publication"
            ) from error
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
