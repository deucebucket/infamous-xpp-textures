"""Audited two-PSARC XPP extraction and profile building."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Callable

from .psarc import iter_archive_entries, read_manifest, read_toc, rebuild_archive
from .validation import ValidationError, validate_replacement_set


SCHEMA_VERSION = 1
ARCHIVE_SLOTS = (("install1", "infamous1.psarc_s"), ("install2", "infamous2.psarc_s"))


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_profile(
    install1: str | Path,
    install2: str | Path,
    output_directory: str | Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Extract every XPP/XPPS from a retail install pair plus a hash manifest."""
    sources = {"install1": Path(install1), "install2": Path(install2)}
    output_directory = Path(output_directory)
    _require_new_directory(output_directory)
    _validate_complete_sources(sources)
    _report(progress, "Reading both PSARC catalogs and checking package ownership...")
    catalogs = _catalog_sources(sources)
    _require_unique_package_basenames(catalogs)

    temporary = Path(tempfile.mkdtemp(prefix=f".{output_directory.name}.", dir=output_directory.parent))
    try:
        archives = []
        for slot, _filename in ARCHIVE_SLOTS:
            source = sources[slot]
            _report(progress, f"Extracting and hashing {slot}: {source.name}...")
            info, entries, names, _blocks = read_toc(source)
            entry_records = []
            for name, data in iter_archive_entries(source):
                record = {
                    "name": name,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                if name.lower().endswith((".xpp", ".xpps")):
                    relative = _safe_manifest_path(name)
                    destination = temporary / "xpp" / slot / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                    record["extracted"] = destination.relative_to(temporary).as_posix()
                entry_records.append(record)
            archives.append(
                {
                    "slot": slot,
                    "file_name": source.name,
                    "bytes": source.stat().st_size,
                    "sha256": file_sha256(source),
                    "version": info["version"],
                    "compression": info["compression"],
                    "block_size": info["block_size"],
                    "entries_with_manifest": len(entries),
                    "manifest_entries": len(names),
                    "entries": entry_records,
                }
            )

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "kind": "xpp-workspace",
            "archives": archives,
        }
        _write_json(temporary / "workspace.json", manifest)
        _report(progress, "Publishing the complete workspace atomically...")
        os.replace(temporary, output_directory)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def build_profile(
    install1: str | Path,
    install2: str | Path,
    replacement_directory: str | Path,
    output_directory: str | Path,
    *,
    compression_level: int = 9,
    known_pass_extra: int | None = None,
    known_fail_extra: int | None = None,
    fail_on_budget: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Route replacement XPPs, build both PSARCs, and byte-audit every entry."""
    sources = {"install1": Path(install1), "install2": Path(install2)}
    replacement_directory = Path(replacement_directory)
    output_directory = Path(output_directory)
    _require_new_directory(output_directory)
    routed, replacement_records, preflight = _prepare_replacements(
        sources,
        replacement_directory,
        known_pass_extra=known_pass_extra,
        known_fail_extra=known_fail_extra,
        progress=progress,
    )
    if (
        fail_on_budget
        and preflight["budget"]["status"] == "at-or-above-observed-startup-fail-range"
    ):
        raise ValueError(
            "strict preflight refused a profile at or above the observed startup-fail bound"
        )

    temporary = Path(tempfile.mkdtemp(prefix=f".{output_directory.name}.", dir=output_directory.parent))
    try:
        archive_records = []
        for slot, output_name in ARCHIVE_SLOTS:
            source = sources[slot]
            destination = temporary / output_name
            if routed[slot]:
                _report(progress, f"Building {slot} with {len(routed[slot])} replacements...")
                rebuild_archive(
                    source,
                    destination,
                    routed[slot],
                    compression_level=compression_level,
                    require_all=True,
                )
            else:
                _report(progress, f"Copying unchanged {slot}...")
                _copy_atomic(source, destination)
            _report(progress, f"Auditing every {slot} entry against retail and replacements...")
            audit = audit_archive(source, destination, routed[slot])
            _report(progress, f"Hashing source and output {slot} archives...")
            archive_records.append(
                {
                    "slot": slot,
                    "file_name": output_name,
                    "source_bytes": source.stat().st_size,
                    "source_sha256": file_sha256(source),
                    "output_bytes": destination.stat().st_size,
                    "output_sha256": file_sha256(destination),
                    **audit,
                }
            )

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "kind": "xpp-psarc-profile",
            "archives": archive_records,
            "replacements": sorted(replacement_records, key=lambda record: (record["slot"], record["file_name"].lower())),
            "replacement_count": len(replacement_records),
            "preflight": preflight,
        }
        _write_json(temporary / "profile.json", manifest)
        _report(progress, "Publishing the verified two-archive profile atomically...")
        os.replace(temporary, output_directory)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def validate_profile(
    install1: str | Path,
    install2: str | Path,
    replacement_directory: str | Path,
    *,
    known_pass_extra: int | None = None,
    known_fail_extra: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Route and strictly validate a replacement set without building PSARCs."""
    sources = {"install1": Path(install1), "install2": Path(install2)}
    _routed, replacement_records, preflight = _prepare_replacements(
        sources,
        Path(replacement_directory),
        known_pass_extra=known_pass_extra,
        known_fail_extra=known_fail_extra,
        progress=progress,
    )
    return {
        "kind": "xpp-profile-preflight",
        "replacement_count": len(replacement_records),
        **preflight,
    }


def _prepare_replacements(
    sources: dict[str, Path],
    replacement_directory: Path,
    *,
    known_pass_extra: int | None,
    known_fail_extra: int | None,
    progress: Callable[[str], None] | None,
) -> tuple[dict[str, dict[str, bytes]], list[dict], dict]:
    _validate_complete_sources(sources)
    _report(progress, "Reading both retail catalogs and routing replacement basenames...")
    catalogs = _catalog_sources(sources)
    owners = _require_unique_package_basenames(catalogs)
    replacements = _read_replacements(replacement_directory)

    routed: dict[str, dict[str, bytes]] = {"install1": {}, "install2": {}}
    replacement_records = []
    for folded_name, replacement in replacements.items():
        if folded_name not in owners:
            raise ValueError(f"replacement is absent from both retail PSARCs: {replacement.name}")
        slot, retail_name = owners[folded_name]
        data = replacement.read_bytes()
        routed[slot][retail_name] = data
        replacement_records.append(
            {
                "file_name": retail_name,
                "slot": slot,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )

    try:
        preflight = validate_replacement_set(
            sources,
            routed,
            known_pass_extra=known_pass_extra,
            known_fail_extra=known_fail_extra,
            progress=progress,
        )
    except ValidationError as error:
        # Keep the original public error phrase while adding the strict reason.
        raise ValueError(f"replacement is not a valid XPP/profile match: {error}") from error
    return routed, replacement_records, preflight


def audit_archive(
    source: str | Path,
    rebuilt: str | Path,
    replacements: dict[str, bytes],
) -> dict[str, int]:
    """Verify manifest identity plus every changed and unchanged payload byte."""
    source = Path(source)
    rebuilt = Path(rebuilt)
    source_info, source_entries, source_names, _source_blocks = read_toc(source)
    rebuilt_info, rebuilt_entries, rebuilt_names, _rebuilt_blocks = read_toc(rebuilt)
    for key in ("version", "compression", "entry_size", "block_size", "flags"):
        if source_info[key] != rebuilt_info[key]:
            raise ValueError(f"PSARC audit failed: {key} changed")
    if source_names != rebuilt_names or len(source_entries) != len(rebuilt_entries):
        raise ValueError("PSARC audit failed: manifest order or entry count changed")
    if read_manifest(source) != read_manifest(rebuilt):
        raise ValueError("PSARC audit failed: exact manifest bytes changed")
    if any(
        source_entry["md5"] != rebuilt_entry["md5"]
        for source_entry, rebuilt_entry in zip(source_entries, rebuilt_entries, strict=True)
    ):
        raise ValueError("PSARC audit failed: entry name digest changed")

    changed = unchanged = 0
    for (name, source_payload), (rebuilt_name, rebuilt_payload) in zip(
        iter_archive_entries(source), iter_archive_entries(rebuilt), strict=True
    ):
        if name != rebuilt_name:
            raise ValueError("PSARC audit failed: entry iteration order changed")
        expected = replacements.get(Path(name).name, source_payload)
        if rebuilt_payload != expected:
            state = "replacement" if Path(name).name in replacements else "unchanged"
            raise ValueError(f"PSARC audit failed: {state} entry differs: {name}")
        if Path(name).name in replacements:
            changed += 1
        else:
            unchanged += 1
    if changed != len(replacements):
        raise ValueError("PSARC audit failed: not every replacement was found")
    return {
        "entries_audited": len(source_entries),
        "manifest_entries": len(source_names),
        "replaced_entries": changed,
        "unchanged_entries": unchanged,
    }


def _catalog_sources(sources: dict[str, Path]) -> dict[str, dict]:
    catalogs = {}
    for slot, source in sources.items():
        info, entries, names, _blocks = read_toc(source)
        catalogs[slot] = {"info": info, "entries": entries, "names": names}
    return catalogs


def _require_unique_package_basenames(catalogs: dict[str, dict]) -> dict[str, tuple[str, str]]:
    owners: dict[str, tuple[str, str]] = {}
    duplicates = set()
    for slot, catalog in catalogs.items():
        for name in catalog["names"]:
            if not name.lower().endswith((".xpp", ".xpps")):
                continue
            basename = Path(name).name
            folded = basename.casefold()
            if folded in owners:
                duplicates.add(basename)
            else:
                owners[folded] = (slot, basename)
    if duplicates:
        raise ValueError(f"duplicate package basenames make routing ambiguous: {sorted(duplicates)}")
    return owners


def _read_replacements(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        raise NotADirectoryError(f"replacement directory was not found: {root}")
    replacements: dict[str, Path] = {}
    duplicates = set()
    paths = (
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in (".xpp", ".xpps")
    )
    for path in sorted(paths):
        folded = path.name.casefold()
        if folded in replacements:
            duplicates.add(path.name)
        else:
            replacements[folded] = path
    if duplicates:
        raise ValueError(f"duplicate replacement basenames: {sorted(duplicates)}")
    if not replacements:
        raise ValueError(f"no replacement XPP/XPPS files were found under {root}")
    return replacements


def _validate_complete_sources(sources: dict[str, Path]) -> None:
    missing = [f"{slot}: {path}" for slot, path in sources.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"retail PSARC pair is incomplete: {', '.join(missing)}")


def _safe_manifest_path(name: str) -> Path:
    portable = PurePosixPath(name.replace("\\", "/").lstrip("/"))
    if portable.is_absolute() or ".." in portable.parts or not portable.parts:
        raise ValueError(f"unsafe PSARC manifest path: {name!r}")
    return Path(*portable.parts)


def _require_new_directory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"output directory already exists: {path}")


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_handle:
            shutil.copyfileobj(input_handle, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _report(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)
