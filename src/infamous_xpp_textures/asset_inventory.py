"""Checksum-pinned completion inventory and dual-output asset manifest."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath


MAX_TALLY_BYTES = 4 * 1024 * 1024
MAX_STATIC_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_GALLERY_SNAPSHOT_BYTES = 512 * 1024
MAX_CENSUS_BYTES = 2 * 1024 * 1024
MAX_STATIC_OBJECTS = 5000
MAX_GALLERY_ITEMS = 5000
MAX_REPORT_BYTES = 4 * 1024 * 1024


class AssetInventoryError(ValueError):
    """Raised when completion evidence is unsafe, inconsistent, or ambiguous."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _read_pinned(path: Path, expected_sha256: str, maximum: int, label: str) -> bytes:
    if not _valid_sha256(expected_sha256):
        raise AssetInventoryError(f"{label} SHA-256 pin is not canonical")
    if path.is_symlink() or not path.is_file():
        raise AssetInventoryError(
            f"{label} must be an existing regular non-symlink file"
        )
    size = path.stat().st_size
    if not 0 < size <= maximum:
        raise AssetInventoryError(
            f"{label} is empty or exceeds the {maximum}-byte bound"
        )
    data = path.read_bytes()
    if len(data) != size:
        raise AssetInventoryError(f"{label} changed while it was read")
    actual = _sha256(data)
    if actual != expected_sha256:
        raise AssetInventoryError(
            f"{label} SHA-256 mismatch: expected {expected_sha256}, found {actual}"
        )
    return data


def _load_json(data: bytes, label: str) -> dict:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssetInventoryError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise AssetInventoryError(f"{label} root must be an object")
    return value


def _safe_relative(value: object, suffix: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AssetInventoryError(f"{label} is not a non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != suffix:
        raise AssetInventoryError(f"{label} is not a safe {suffix} relative path")
    return path.as_posix()


def _safe_text(value: object, label: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 for character in value)
    ):
        raise AssetInventoryError(f"{label} is not bounded printable text")
    return value


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AssetInventoryError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AssetInventoryError(f"{label} must be a non-negative integer")
    return value


def _asset_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not token:
        raise AssetInventoryError("asset identity normalizes to an empty token")
    return token


def _render_match_key(value: str) -> str:
    stem = value[:-4] if value.lower().endswith(".png") else value
    tokens = [token for token in re.split(r"[^a-z0-9]+", stem.lower()) if token]
    while tokens and tokens[-1] in {"8k", "pbr"}:
        tokens.pop()
    return "".join(tokens)


def _model_match_key(value: str) -> str:
    stem = value[:-4] if value.lower().endswith(".glb") else value
    return "".join(re.findall(r"[a-z0-9]+", stem.lower()))


def _parse_tally(data: bytes) -> dict:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AssetInventoryError("decomp tally is not UTF-8") from error

    patterns = {
        "retail_glb_exports": r"Retail GLB modkit \| ([0-9,]+) models; ([0-9,]+) failures",
        "gallery_files": r"Finished gallery \| ([0-9,]+) 8K renders",
        "textures": (
            r"Corrected texture extraction \| ([0-9,]+) records; "
            r"([0-9]+(?:\.[0-9]+)?)% good"
        ),
        "character_renders": r"Character renders remain \*\*([0-9,]+)\*\*",
    }
    matches: dict[str, tuple[str, ...]] = {}
    for key, pattern in patterns.items():
        found = re.findall(pattern, text)
        if len(found) != 1:
            raise AssetInventoryError(
                f"decomp tally {key} authority resolves to {len(found)} rows"
            )
        raw = found[0]
        matches[key] = raw if isinstance(raw, tuple) else (raw,)

    local_4x_complete = bool(re.search(r"Local 4× texture corpus \| Complete;", text))
    if not local_4x_complete:
        raise AssetInventoryError("decomp tally does not declare the local 4x corpus")
    return {
        "retail_glb_exports": int(matches["retail_glb_exports"][0].replace(",", "")),
        "retail_glb_failures": int(matches["retail_glb_exports"][1].replace(",", "")),
        "declared_gallery_8k_files": int(matches["gallery_files"][0].replace(",", "")),
        "corrected_texture_records": int(matches["textures"][0].replace(",", "")),
        "corrected_texture_good_percent": float(matches["textures"][1]),
        "character_renders": int(matches["character_renders"][0].replace(",", "")),
        "local_4x_texture_corpus_complete": True,
    }


def _validate_static_manifest(value: dict) -> tuple[list[dict], dict]:
    objects = value.get("objects")
    if not isinstance(objects, list) or not 0 < len(objects) <= MAX_STATIC_OBJECTS:
        raise AssetInventoryError("static GLB manifest object count is invalid")
    declared_ok = _nonnegative_int(value.get("ok"), "static GLB ok count")
    declared_fail = _nonnegative_int(value.get("fail"), "static GLB fail count")
    if declared_ok != len(objects) or declared_fail != 0:
        raise AssetInventoryError("static GLB declared result does not match its rows")

    result: list[dict] = []
    seen_ids: set[tuple[str, str]] = set()
    seen_paths: set[str] = set()
    seen_asset_ids: set[str] = set()
    for index, row in enumerate(objects):
        if not isinstance(row, dict):
            raise AssetInventoryError(f"static GLB row {index} is not an object")
        name = _safe_text(row.get("name"), f"static GLB row {index} name")
        bucket = _safe_text(row.get("bucket"), f"static GLB row {index} bucket")
        glb_path = _safe_relative(
            row.get("glb"), ".glb", f"static GLB row {index} output"
        )
        identity = (bucket.casefold(), name.casefold())
        if identity in seen_ids or glb_path.casefold() in seen_paths:
            raise AssetInventoryError("static GLB manifest repeats an asset identity")
        seen_ids.add(identity)
        seen_paths.add(glb_path.casefold())
        asset_id = f"infamous-1:bcus98119:{_asset_token(bucket)}:{_asset_token(name)}"
        if asset_id in seen_asset_ids:
            raise AssetInventoryError(
                "static GLB identities collide after canonical normalization"
            )
        seen_asset_ids.add(asset_id)
        source_sha = row.get("xpp_sha256")
        output_sha = row.get("glb_sha256")
        contact_sha = row.get("contact_sha256")
        if not all(
            _valid_sha256(item) for item in (source_sha, output_sha, contact_sha)
        ):
            raise AssetInventoryError(
                f"static GLB row {index} has a non-canonical hash"
            )
        if row.get("status") != "ok":
            raise AssetInventoryError(f"static GLB row {index} is not successful")
        for flag in ("pbr", "hd"):
            if not isinstance(row.get(flag), bool):
                raise AssetInventoryError(
                    f"static GLB row {index} {flag} flag is not boolean"
                )
        source_package = _safe_text(
            row.get("xpp"), f"static GLB row {index} source package", 512
        )
        result.append(
            {
                "asset_id": asset_id,
                "kind": "static-object",
                "bucket": bucket,
                "name": name,
                "source_package": source_package,
                "source_package_sha256": source_sha,
                "retail_glb": {
                    "relative_path": glb_path,
                    "bytes": _positive_int(
                        row.get("glb_bytes"), f"static GLB row {index} byte count"
                    ),
                    "sha256": output_sha,
                    "contact_receipt_sha256": contact_sha,
                    "sections": _positive_int(
                        row.get("sections"), f"static GLB row {index} sections"
                    ),
                    "vertices": _positive_int(
                        row.get("vertices"), f"static GLB row {index} vertices"
                    ),
                    "triangles": _positive_int(
                        row.get("triangles"), f"static GLB row {index} triangles"
                    ),
                    "retail_textures": True,
                    "four_x_textures": bool(row["hd"]),
                    "pbr_materials": bool(row["pbr"]),
                },
            }
        )
    return result, {
        "title": _safe_text(value.get("title"), "static GLB title", 512),
        "build": _safe_text(value.get("disc"), "static GLB build", 512),
        "successful_exports": len(result),
        "failed_exports": declared_fail,
        "private_contact_paths_serialized": False,
    }


def _validate_gallery(value: dict) -> tuple[list[dict], list[dict], dict]:
    if (
        value.get("format") != "infamous-gallery-drive-snapshot"
        or value.get("version") != 1
    ):
        raise AssetInventoryError("gallery snapshot schema is unsupported")
    declared = value.get("declared")
    items = value.get("items")
    duplicates = value.get("duplicates")
    if not isinstance(declared, dict) or not isinstance(items, list):
        raise AssetInventoryError("gallery snapshot structure is malformed")
    if not isinstance(duplicates, list) or len(items) > MAX_GALLERY_ITEMS:
        raise AssetInventoryError("gallery snapshot duplicate/item count is invalid")

    result: list[dict] = []
    seen: set[tuple[str, str]] = set()
    counts: Counter[str] = Counter()
    for index, row in enumerate(items):
        if not isinstance(row, dict):
            raise AssetInventoryError(f"gallery row {index} is not an object")
        kind = row.get("kind")
        if kind not in {"asset-render", "gameplay-screenshot"}:
            raise AssetInventoryError(f"gallery row {index} kind is unsupported")
        bucket = _safe_text(row.get("bucket"), f"gallery row {index} bucket")
        name = _safe_text(row.get("name"), f"gallery row {index} name", 512)
        if not name.lower().endswith(".png"):
            raise AssetInventoryError(f"gallery row {index} is not a PNG")
        identity = (bucket.casefold(), name.casefold())
        if identity in seen:
            raise AssetInventoryError("gallery unique item rows repeat an identity")
        seen.add(identity)
        counts[kind] += 1
        result.append(
            {
                "kind": kind,
                "bucket": bucket,
                "name": name,
                "bytes": _positive_int(row.get("bytes"), f"gallery row {index} bytes"),
            }
        )

    unique_rows = {
        (row["bucket"].casefold(), row["name"].casefold()): row for row in result
    }
    duplicate_rows: list[dict] = []
    seen_duplicates: set[tuple[str, str]] = set()
    for index, row in enumerate(duplicates):
        if not isinstance(row, dict):
            raise AssetInventoryError(f"gallery duplicate row {index} is malformed")
        bucket = _safe_text(row.get("bucket"), f"gallery duplicate {index} bucket")
        name = _safe_text(row.get("name"), f"gallery duplicate {index} name", 512)
        byte_count = _positive_int(row.get("bytes"), f"gallery duplicate {index} bytes")
        copies = _positive_int(row.get("copies"), f"gallery duplicate {index} copies")
        identity = (bucket.casefold(), name.casefold())
        if copies < 2 or identity not in seen:
            raise AssetInventoryError(
                "gallery duplicate does not name a unique source row"
            )
        if identity in seen_duplicates:
            raise AssetInventoryError("gallery duplicate identity is repeated")
        if unique_rows[identity]["bytes"] != byte_count:
            raise AssetInventoryError(
                "gallery duplicate byte count differs from source row"
            )
        seen_duplicates.add(identity)
        duplicate_rows.append(
            {"bucket": bucket, "name": name, "bytes": byte_count, "copies": copies}
        )

    expected = {
        "unique_asset_renders": counts["asset-render"],
        "gameplay_screenshots": counts["gameplay-screenshot"],
        "duplicate_file_entries": sum(row["copies"] - 1 for row in duplicate_rows),
        "character_renders": sum(
            1
            for row in result
            if row["kind"] == "asset-render" and row["bucket"] == "characters"
        ),
    }
    for key, actual in expected.items():
        if _nonnegative_int(declared.get(key), f"gallery declared {key}") != actual:
            raise AssetInventoryError(f"gallery declared {key} does not match its rows")
    return result, duplicate_rows, expected


def _validate_census(value: dict, candidate_id: str) -> dict:
    if (
        value.get("format") != "infamous-character-asset-census"
        or value.get("version") != 1
    ):
        raise AssetInventoryError("character census schema is unsupported")
    token = _asset_token(candidate_id)
    targets = value.get("targets")
    profiles = value.get("profiles")
    if not isinstance(targets, dict) or not isinstance(profiles, dict):
        raise AssetInventoryError("character census target/profile data is missing")
    target_rows: list[dict] = []
    for side in ("left", "right"):
        row = targets.get(side)
        profile = profiles.get(side)
        if not isinstance(row, dict) or not isinstance(profile, dict):
            raise AssetInventoryError(f"character census {side} side is missing")
        relative = _safe_relative(
            row.get("relative_path"), ".xpp", f"character census {side} target"
        )
        if token.replace("-", "") not in _asset_token(relative).replace("-", ""):
            raise AssetInventoryError(
                f"candidate {candidate_id!r} is not anchored in both census targets"
            )
        digest = row.get("sha256")
        workspace_digest = profile.get("workspace_sha256")
        if not _valid_sha256(digest) or not _valid_sha256(workspace_digest):
            raise AssetInventoryError("character census contains a non-canonical hash")
        target_rows.append(
            {
                "profile": side,
                "relative_path": relative,
                "bytes": _positive_int(row.get("bytes"), f"{side} target bytes"),
                "sha256": digest,
                "texture_descriptors": _positive_int(
                    row.get("texture_descriptor_count"),
                    f"{side} target texture descriptors",
                ),
                "geometry_contracts": _positive_int(
                    row.get("geometry_contract_count"),
                    f"{side} target geometry contracts",
                ),
                "workspace_sha256": workspace_digest,
                "verified_profile_packages": _positive_int(
                    profile.get("package_count"), f"{side} profile package count"
                ),
            }
        )
    findings = value.get("findings")
    completion = value.get("completion_gates")
    delivery = value.get("delivery_gates")
    cross_build = value.get("cross_build_texture_mapping")
    if not all(
        isinstance(item, dict) for item in (findings, completion, delivery, cross_build)
    ):
        raise AssetInventoryError("character census gate data is missing")
    return {
        "asset_id": f"infamous-1:cross-build:character:{token}",
        "candidate_id": token,
        "kind": "character",
        "targets": target_rows,
        "named_texture_descriptors_proved": findings.get(
            "named_texture_descriptors_proved"
        )
        is True,
        "multipart_package_names_proved": findings.get("multipart_package_names_proved")
        is True,
        "cross_build_texture_identity_proved": findings.get(
            "cross_build_target_texture_identity_proved"
        )
        is True,
        "cross_build_unique_texture_matches": _nonnegative_int(
            cross_build.get("unique_matches"), "cross-build unique matches"
        ),
        "cross_build_reordered_texture_matches": _nonnegative_int(
            cross_build.get("reordered_matches"), "cross-build reordered matches"
        ),
        "completion_gates": {key: value is True for key, value in completion.items()},
        "delivery_gates": {key: value is True for key, value in delivery.items()},
    }


def _completion_matrix() -> dict:
    return {
        "required_piece_inventory_complete": False,
        "object_space_orientation_proved": False,
        "piece_alignment_proved": False,
        "uv_binding_proved": False,
        "material_texture_binding_proved": False,
        "no_missing_required_pieces": False,
        "no_duplicate_pieces": False,
        "no_mistextured_pieces": False,
        "lod_state_flavor_selection_proved": False,
        "skeleton_and_skinning_proved": False,
        "animation_relations_proved": False,
        "blender_asset_complete": False,
        "retail_four_x_pbr_complete": False,
        "beauty_study_complete": False,
        "turntable_complete": False,
        "rpcs3_retail_round_trip": False,
        "native_decomp_import": False,
    }


def build_asset_completion_inventory(
    tally_path: Path,
    tally_sha256: str,
    static_manifest_path: Path,
    static_manifest_sha256: str,
    gallery_snapshot_path: Path,
    gallery_snapshot_sha256: str,
    character_census_path: Path,
    character_census_sha256: str,
    *,
    candidate_id: str,
) -> dict:
    """Build one payload-free completion inventory from exact source receipts."""

    paths = [
        tally_path.resolve(),
        static_manifest_path.resolve(),
        gallery_snapshot_path.resolve(),
        character_census_path.resolve(),
    ]
    if len(set(paths)) != len(paths):
        raise AssetInventoryError("inventory inputs must be four different files")
    inputs = [
        (
            "decomp_tally",
            _read_pinned(tally_path, tally_sha256, MAX_TALLY_BYTES, "decomp tally"),
            tally_sha256,
        ),
        (
            "static_glb_manifest",
            _read_pinned(
                static_manifest_path,
                static_manifest_sha256,
                MAX_STATIC_MANIFEST_BYTES,
                "static GLB manifest",
            ),
            static_manifest_sha256,
        ),
        (
            "gallery_snapshot",
            _read_pinned(
                gallery_snapshot_path,
                gallery_snapshot_sha256,
                MAX_GALLERY_SNAPSHOT_BYTES,
                "gallery snapshot",
            ),
            gallery_snapshot_sha256,
        ),
        (
            "character_census",
            _read_pinned(
                character_census_path,
                character_census_sha256,
                MAX_CENSUS_BYTES,
                "character census",
            ),
            character_census_sha256,
        ),
    ]
    input_by_name = {name: data for name, data, _digest in inputs}
    tally = _parse_tally(input_by_name["decomp_tally"])
    static_rows, static_summary = _validate_static_manifest(
        _load_json(input_by_name["static_glb_manifest"], "static GLB manifest")
    )
    gallery_rows, gallery_duplicates, gallery_summary = _validate_gallery(
        _load_json(input_by_name["gallery_snapshot"], "gallery snapshot")
    )
    candidate = _validate_census(
        _load_json(input_by_name["character_census"], "character census"),
        candidate_id,
    )
    if tally["retail_glb_exports"] != len(static_rows):
        raise AssetInventoryError("decomp tally and GLB manifest export counts differ")
    if tally["retail_glb_failures"] != static_summary["failed_exports"]:
        raise AssetInventoryError("decomp tally and GLB manifest failure counts differ")
    if tally["declared_gallery_8k_files"] != len(gallery_rows):
        raise AssetInventoryError(
            "decomp gallery total is not reconciled by unique renders plus screenshots"
        )
    if tally["character_renders"] != gallery_summary["character_renders"]:
        raise AssetInventoryError("decomp and gallery character-render counts differ")

    static_by_match: dict[str, list[int]] = {}
    for index, row in enumerate(static_rows):
        static_by_match.setdefault(_model_match_key(row["name"]), []).append(index)

    matched_gallery: dict[int, list[dict]] = {}
    unresolved_gallery: list[dict] = []
    for row in gallery_rows:
        if row["kind"] != "asset-render":
            continue
        candidates = static_by_match.get(_render_match_key(row["name"]), [])
        if len(candidates) == 1:
            matched_gallery.setdefault(candidates[0], []).append(row)
        else:
            unresolved_gallery.append(row)

    records: list[dict] = []
    for index, static in enumerate(static_rows):
        render_rows = matched_gallery.get(index, [])
        if len(render_rows) > 1:
            raise AssetInventoryError(
                "multiple gallery subjects map to one static asset"
            )
        matrix = _completion_matrix()
        records.append(
            {
                **static,
                "status": "partial",
                "existing_gallery_render": render_rows[0] if render_rows else None,
                "completion": matrix,
                "skip_work_classes": [
                    "retail_static_glb_export",
                    *(["existing_8k_gallery_render"] if render_rows else []),
                ],
                "next_actions": [
                    "prove canonical orientation/alignment and material/UV correctness",
                    "build a reversible retail XPP/PSARC round trip",
                    "attach 4x/PBR material evidence without replacing retail truth",
                    "retain the same manifest for native-decomp import",
                ],
            }
        )

    for row in unresolved_gallery:
        records.append(
            {
                "asset_id": (
                    "infamous-1:bcus98119:gallery-unresolved:"
                    f"{_asset_token(row['bucket'])}:{_asset_token(row['name'][:-4])}"
                ),
                "kind": "unresolved-gallery-subject",
                "bucket": row["bucket"],
                "name": row["name"],
                "status": "unknown",
                "existing_gallery_render": row,
                "completion": _completion_matrix(),
                "skip_work_classes": ["existing_8k_gallery_render"],
                "next_actions": [
                    "bind this render to one exact source package and model receipt",
                    "do not rerender merely to rediscover the existing image",
                ],
            }
        )

    candidate_matrix = _completion_matrix()
    completion_aliases = {
        "blender_glb_complete": "blender_asset_complete",
        "beauty_character_study_complete": "beauty_study_complete",
    }
    for key, value in candidate["completion_gates"].items():
        canonical_key = completion_aliases.get(key, key)
        if canonical_key in candidate_matrix:
            candidate_matrix[canonical_key] = value
    candidate_matrix["rpcs3_retail_round_trip"] = candidate["delivery_gates"].get(
        "rpcs3_emulator_mod_round_trip", False
    )
    candidate_matrix["native_decomp_import"] = candidate["delivery_gates"].get(
        "native_decomp_asset_import", False
    )
    records.append(
        {
            **candidate,
            "status": "partial",
            "existing_gallery_render": None,
            "completion": candidate_matrix,
            "skip_work_classes": [
                "complete_cross_build_texture_identity_census",
                "complete_profile_integrity_scan",
            ],
            "next_actions": [
                "bind every packed geometry record to the named piece inventory",
                "prove UV and material-to-texture-family selection",
                "prove object-space orientation, alignment, LOD/state, rig, and skinning",
                "export one complete editable Blender asset",
                "round-trip one harmless edit through retail XPP/PSARC in RPCS3",
            ],
        }
    )

    record_ids = [row["asset_id"] for row in records]
    if len(record_ids) != len(set(record_ids)):
        raise AssetInventoryError("canonical record identities are not unique")
    status_counts = Counter(row["status"] for row in records)
    gameplay = [row for row in gallery_rows if row["kind"] == "gameplay-screenshot"]
    return {
        "format": "infamous-asset-completion-inventory",
        "version": 1,
        "scope": {
            "title": "inFAMOUS 1",
            "character_and_item_agnostic": True,
            "short_goal": "editable retail-compatible RPCS3 mods",
            "long_goal": "the same canonical assets imported by the native decomp",
            "native_path_blocks_emulator_path": False,
            "partial_media_blocks_other_publication": False,
            "private_paths_serialized": False,
            "game_payload_serialized": False,
        },
        "input_receipts": {
            name: {"sha256": digest, "bytes": len(data)}
            for name, data, digest in inputs
        },
        "authority": tally,
        "reconciliation": {
            "static_glb_manifest": static_summary,
            "gallery": {
                **gallery_summary,
                "declared_8k_files_reconciled_as": (
                    f"{gallery_summary['unique_asset_renders']} asset renders + "
                    f"{gallery_summary['gameplay_screenshots']} gameplay screenshot"
                ),
                "duplicates": gallery_duplicates,
                "exact_normalized_static_joins": sum(
                    len(rows) for rows in matched_gallery.values()
                ),
                "unresolved_render_subjects": len(unresolved_gallery),
                "gameplay_evidence": gameplay,
                "matching_rule": (
                    "case-insensitive exact alphanumeric stem after removing only "
                    "terminal 8k/pbr tags; ambiguous and suffix-only names stay unresolved"
                ),
            },
            "texture_corpus": {
                "corrected_records": tally["corrected_texture_records"],
                "good_percent": tally["corrected_texture_good_percent"],
                "local_4x_corpus_complete": tally["local_4x_texture_corpus_complete"],
                "known_residual": (
                    "offline extraction completeness is not per-asset material binding, "
                    "mod round-trip, or native-render proof"
                ),
            },
        },
        "counts": {
            "records": len(records),
            "complete": status_counts["complete"],
            "partial": status_counts["partial"],
            "unknown": status_counts["unknown"],
            "retail_static_glb_exports_to_skip": len(static_rows),
            "existing_8k_asset_renders_to_skip": gallery_summary[
                "unique_asset_renders"
            ],
            "character_renders": gallery_summary["character_renders"],
            "rpcs3_round_trip_complete": sum(
                row["completion"]["rpcs3_retail_round_trip"] for row in records
            ),
            "native_import_complete": sum(
                row["completion"]["native_decomp_import"] for row in records
            ),
        },
        "records": records,
        "first_unfinished_batch": {
            "asset_id": candidate["asset_id"],
            "selected_from_evidence": True,
            "why": [
                "both checksum-pinned builds expose the same named multipart target",
                "both builds prove packed geometry contracts and named texture descriptors",
                "cross-build texture identities are complete even though indices reorder",
                "character render, complete Blender asset, and both delivery gates remain false",
            ],
            "near_term_exit": (
                "one complete editable asset with proved pieces/materials/UVs/orientation "
                "and a validated retail RPCS3 round trip"
            ),
            "native_exit": (
                "the same canonical record loads through the decomp asset/runtime layer; "
                "this is independent and does not delay the retail round trip"
            ),
        },
        "dual_output_contract": {
            "canonical_record_is_shared": True,
            "rpcs3": {
                "priority": "near-term",
                "container": "retail XPP/PSARC profile",
                "ready": False,
                "requires": [
                    "lossless editable import/export",
                    "strict pack validator",
                    "foreground gameplay proof on the intended build",
                ],
            },
            "native_decomp": {
                "priority": "long-term",
                "container": "native asset importer chosen by the decomp",
                "ready": False,
                "requires": [
                    "decomp asset/resource runtime",
                    "decomp renderer/model/material consumer",
                    "canonical-manifest importer and native runtime proof",
                ],
                "expected_mod_authoring_after_runtime_exists": (
                    "simpler than retail repacking because the loader and validation "
                    "contract are source-controlled"
                ),
            },
        },
        "publication_policy": {
            "publish_each_honest_render_when_created": True,
            "publish_each_turntable_when_created": True,
            "media_never_blocks_other_evidence": True,
            "partial_never_labeled_complete": True,
            "final_character_study": [
                "full body",
                "face close-ups",
                "multiple angles",
                "360-degree turntable",
                "retail and 4x/PBR comparison",
            ],
        },
        "limitations": (
            "A successful retail static GLB export proves only that exact export work class. "
            "An 8K image proves only that render exists. Neither proves a complete asset, "
            "correct orientation/materials, reversible retail injection, or native import."
        ),
    }


def render_asset_completion_inventory(report: dict) -> bytes:
    rendered = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(rendered) > MAX_REPORT_BYTES:
        raise AssetInventoryError(
            f"asset completion inventory exceeds the {MAX_REPORT_BYTES}-byte bound"
        )
    return rendered


def write_new_asset_completion_inventory(path: Path, report: dict) -> None:
    """Publish deterministic inventory bytes without replacing any existing path."""

    if path.is_symlink() or path.exists():
        raise AssetInventoryError(
            "asset completion inventory output already exists; refusing to overwrite it"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render_asset_completion_inventory(report)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise AssetInventoryError(
                "asset completion inventory output appeared during publication"
            ) from error
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
