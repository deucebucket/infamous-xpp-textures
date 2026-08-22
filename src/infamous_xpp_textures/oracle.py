"""Aggregate, payload-free comparison of two packed inFAMOUS profiles."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Callable

from .psarc import iter_archive_entries, read_toc


SCHEMA_VERSION = 1
ARCHIVE_SLOTS = ("install1", "install2")
PACKAGE_SUFFIXES = (".xpp", ".xpps")


def build_profile_oracle(
    left_install1: str | Path,
    left_install2: str | Path,
    right_install1: str | Path,
    right_install2: str | Path,
    *,
    left_label: str = "left",
    right_label: str = "right",
    compare_bytes: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Compare two archive pairs without emitting paths, names, or payload hashes."""
    left_label = _safe_label(left_label)
    right_label = _safe_label(right_label)
    left_paths = {
        "install1": Path(left_install1),
        "install2": Path(left_install2),
    }
    right_paths = {
        "install1": Path(right_install1),
        "install2": Path(right_install2),
    }
    for paths in (left_paths, right_paths):
        for path in paths.values():
            if not path.is_file():
                raise FileNotFoundError(f"profile archive was not found: {path.name}")

    archive_reports = []
    left_pair_records = []
    right_pair_records = []
    for slot in ARCHIVE_SLOTS:
        action = "Hashing" if compare_bytes else "Reading"
        _report(progress, f"{action} aggregate {left_label} {slot} evidence...")
        left = _scan_archive(left_paths[slot], slot=slot, compare_bytes=compare_bytes)
        _report(progress, f"{action} aggregate {right_label} {slot} evidence...")
        right = _scan_archive(right_paths[slot], slot=slot, compare_bytes=compare_bytes)
        left_records = left.pop("records")
        right_records = right.pop("records")
        left_pair_records.extend(left_records)
        right_pair_records.extend(right_records)
        archive_reports.append(
            {
                "slot": slot,
                "left": left,
                "right": right,
                **_compare_records(
                    left_records,
                    right_records,
                    compare_bytes=compare_bytes,
                ),
            }
        )

    pair_comparison = _compare_records(
        left_pair_records,
        right_pair_records,
        compare_bytes=compare_bytes,
    )
    left_basenames = Counter(record["basename"] for record in left_pair_records)
    right_basenames = Counter(record["basename"] for record in right_pair_records)
    left_duplicate_basenames = sum(count > 1 for count in left_basenames.values())
    right_duplicate_basenames = sum(count > 1 for count in right_basenames.values())
    contracts_match = all(
        archive["left"]["contract"] == archive["right"]["contract"]
        for archive in archive_reports
    )
    routing_unambiguous = left_duplicate_basenames == right_duplicate_basenames == 0

    if not routing_unambiguous:
        verdict = "routing-ambiguous"
    elif not contracts_match:
        verdict = "archive-contracts-differ"
    elif not compare_bytes:
        verdict = "catalog-only"
    elif pair_comparison["changed_shared_packages"]:
        verdict = "shared-package-bytes-diverge"
    else:
        verdict = "shared-package-bytes-identical"

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "xpp-cross-build-oracle",
        "left_label": left_label,
        "right_label": right_label,
        "comparison_mode": "catalog-and-bytes" if compare_bytes else "catalog-only",
        "name_identity": "archive-slot-and-casefolded-full-manifest-name",
        "archive_contracts_match": contracts_match,
        "routing_unambiguous": routing_unambiguous,
        "left_duplicate_package_basenames": left_duplicate_basenames,
        "right_duplicate_package_basenames": right_duplicate_basenames,
        "archives": archive_reports,
        "pair": pair_comparison,
        "verdict": verdict,
        "direct_replacement_transfer_authorized": False,
        "next_gate": (
            "validate or rebuild every replacement against the target profile; "
            "matching names, sizes, or retail bytes do not prove a modified package is safe"
        ),
    }


def _scan_archive(path: Path, *, slot: str, compare_bytes: bool) -> dict:
    info, entries, names, _blocks = read_toc(path)
    if info["compression"] != "zlib" or info["entry_size"] != 30:
        raise ValueError("only standard zlib PSARCs with 30-byte entries are supported")
    if len(names) != len(entries) - 1:
        raise ValueError("PSARC manifest and entry table lengths differ")

    package_metadata = []
    for name, entry in zip(names, entries[1:], strict=True):
        if not name.casefold().endswith(PACKAGE_SUFFIXES):
            continue
        package_metadata.append(
            {
                "slot": slot,
                "full_name": name.casefold(),
                "basename": PurePosixPath(name).name.casefold(),
                "bytes": entry["length"],
                "sha256": None,
            }
        )

    if compare_bytes:
        payload_index = 0
        for name, payload in iter_archive_entries(path):
            if not name.casefold().endswith(PACKAGE_SUFFIXES):
                continue
            record = package_metadata[payload_index]
            if record["full_name"] != name.casefold() or record["bytes"] != len(payload):
                raise ValueError("PSARC package iteration differs from its catalog")
            record["sha256"] = hashlib.sha256(payload).digest()
            payload_index += 1
        if payload_index != len(package_metadata):
            raise ValueError("PSARC package iteration ended before its catalog")

    return {
        "archive_bytes": path.stat().st_size,
        "entries_with_manifest": len(entries),
        "manifest_entries": len(names),
        "package_entries": len(package_metadata),
        "contract": {
            "version": info["version"],
            "compression": info["compression"],
            "entry_size": info["entry_size"],
            "block_size": info["block_size"],
            "flags": info["flags"],
        },
        "records": package_metadata,
    }


def _compare_records(left: list[dict], right: list[dict], *, compare_bytes: bool) -> dict:
    left_by_name = _group_by_name(left)
    right_by_name = _group_by_name(right)
    left_names = set(left_by_name)
    right_names = set(right_by_name)
    shared_names = left_names & right_names
    unambiguous_shared = [
        name
        for name in shared_names
        if len(left_by_name[name]) == len(right_by_name[name]) == 1
    ]
    ambiguous_shared = len(shared_names) - len(unambiguous_shared)

    left_basenames = {record["basename"] for record in left}
    right_basenames = {record["basename"] for record in right}
    result = {
        "left_package_entries": len(left),
        "right_package_entries": len(right),
        "shared_full_names": len(shared_names),
        "shared_basenames": len(left_basenames & right_basenames),
        "left_only_full_names": len(left_names - right_names),
        "right_only_full_names": len(right_names - left_names),
        "ambiguous_shared_full_names": ambiguous_shared,
        "byte_compared_shared_packages": 0,
        "byte_identical_shared_packages": None,
        "changed_shared_packages": None,
        "same_size_changed_shared_packages": None,
        "shared_uncompressed_bytes_left": sum(
            left_by_name[name][0]["bytes"] for name in unambiguous_shared
        ),
        "shared_uncompressed_bytes_right": sum(
            right_by_name[name][0]["bytes"] for name in unambiguous_shared
        ),
    }
    if not compare_bytes:
        return result

    identical = changed = same_size_changed = 0
    for name in unambiguous_shared:
        left_record = left_by_name[name][0]
        right_record = right_by_name[name][0]
        if (
            left_record["bytes"] == right_record["bytes"]
            and left_record["sha256"] == right_record["sha256"]
        ):
            identical += 1
        else:
            changed += 1
            same_size_changed += left_record["bytes"] == right_record["bytes"]
    result.update(
        {
            "byte_compared_shared_packages": len(unambiguous_shared),
            "byte_identical_shared_packages": identical,
            "changed_shared_packages": changed,
            "same_size_changed_shared_packages": same_size_changed,
        }
    )
    return result


def _group_by_name(records: list[dict]) -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        grouped[(record["slot"], record["full_name"])].append(record)
    return grouped


def _safe_label(label: str) -> str:
    label = label.strip()
    if not label or len(label) > 64:
        raise ValueError("profile labels must contain 1 to 64 characters")
    if any(character in label for character in ("/", "\\", "\n", "\r", "\0")):
        raise ValueError("profile labels cannot contain paths or control characters")
    return label


def _report(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)
