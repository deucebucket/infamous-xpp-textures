"""Build exact texel identities for opt-in emulator runtime tracing."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from .heap import BPP, BLOCK_BYTES, align_up, heap_bytes, level_size
from .validation import compare_xpp, validate_xpp
from .xpp import parse_xpp


def _identity(kind: str, payload: bytes, **location: int | None) -> dict:
    return {
        "kind": kind,
        **location,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def build_runtime_index(data: bytes, label: str) -> dict:
    """Index exact on-disk chains at the boundaries RPCS3 may upload.

    This intentionally hashes encoded texels, not decoded images. A runtime
    miss does not prove a texture was unused: the game may have transformed,
    repitched, or partially copied it before the RSX upload boundary.
    """
    summary, records = validate_xpp(data)
    texels = heap_bytes(data, parse_xpp(data, len(data)))
    identities: list[dict] = []
    descriptor_entries: list[dict] = []

    for record in records:
        face_stride = align_up(record.chain_bytes)
        face_payloads: list[bytes] = []
        face_prefixes: list[list[bytes]] = []
        descriptor_identities: list[dict] = []
        for face in range(record.faces):
            face_start = record.heap_offset + face * face_stride
            face_payload = texels[face_start : face_start + record.chain_bytes]
            face_payloads.append(face_payload)
            mip_payloads: list[bytes] = []
            descriptor_identities.append(
                _identity(
                    "face-chain",
                    face_payload,
                    descriptor=record.index,
                    face=face,
                    mip=None,
                )
            )

            mip_offset = 0
            for mip in range(record.mips):
                mip_bytes = level_size(record.format, record.width, record.height, mip)
                mip_payload = face_payload[mip_offset : mip_offset + mip_bytes]
                mip_payloads.append(mip_payload)
                descriptor_identities.append(
                    _identity(
                        "mip",
                        mip_payload,
                        descriptor=record.index,
                        face=face,
                        mip=mip,
                    )
                )
                mip_offset += mip_bytes

            prefixes: list[bytes] = []
            for mip_count in range(1, record.mips):
                prefix_payload = b"".join(mip_payloads[:mip_count])
                prefixes.append(prefix_payload)
                descriptor_identities.append(
                    _identity(
                        "face-mip-prefix",
                        prefix_payload,
                        descriptor=record.index,
                        face=face,
                        mip=None,
                        mip_count=mip_count,
                    )
                )
            face_prefixes.append(prefixes)

        descriptor_payload = b"".join(face_payloads)
        descriptor_identity = _identity(
            "descriptor",
            descriptor_payload,
            descriptor=record.index,
            face=None,
            mip=None,
        )
        identities.append(descriptor_identity)
        identities.extend(descriptor_identities)
        for mip_count in range(1, record.mips):
            identities.append(
                _identity(
                    "descriptor-mip-prefix",
                    b"".join(prefixes[mip_count - 1] for prefixes in face_prefixes),
                    descriptor=record.index,
                    face=None,
                    mip=None,
                    mip_count=mip_count,
                )
            )
        descriptor_entries.append(
            {
                "index": record.index,
                "width": record.width,
                "height": record.height,
                "mips": record.mips,
                "faces": record.faces,
                "format": f"0x{record.format:02x}",
                "chain_bytes_per_face": record.chain_bytes,
                "upload_bytes": len(descriptor_payload),
                "sha256": descriptor_identity["sha256"],
            }
        )

    hashes = sorted({identity["sha256"] for identity in identities})
    return {
        "schema": 1,
        "label": label,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "structural_status": summary["structural_status"],
        "descriptor_count": len(records),
        "identity_count": len(identities),
        "unique_hash_count": len(hashes),
        "hash_scope": [
            "descriptor",
            "face-chain",
            "mip",
            "descriptor-mip-prefix",
            "face-mip-prefix",
        ],
        "runtime_limit": (
            "A miss means no exact encoded-byte match at this upload boundary; "
            "the texture may be unused or transformed before upload."
        ),
        "descriptors": descriptor_entries,
        "identities": identities,
        "allowlist": hashes,
    }


def write_allowlist(path: Path, report: dict) -> None:
    lines = [
        "# xpp-tool runtime texture SHA-256 allowlist",
        f"# label: {report['label']}",
        f"# source_sha256: {report['source_sha256']}",
        *report["allowlist"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def runtime_mip_count(record) -> int:
    """Mip levels RPCS3 exposes for an encoded RSX texture upload."""
    if record.format not in BLOCK_BYTES:
        return record.mips

    width, height = record.width, record.height
    count = 0
    while count < record.mips:
        if count and (width < 4 or height < 4):
            break
        count += 1
        width = max(1, width // 2)
        height = max(1, height // 2)
    return count


def runtime_payload(texels: bytes, record) -> tuple[bytes, int]:
    mip_count = runtime_mip_count(record)
    length = sum(
        level_size(record.format, record.width, record.height, level)
        for level in range(mip_count)
    )
    return texels[record.heap_offset : record.heap_offset + length], mip_count


def runtime_pitch(record) -> int:
    if record.format in BLOCK_BYTES:
        return ((record.width + 3) // 4) * BLOCK_BYTES[record.format]
    return record.width * BPP[record.format]


def build_replacement_bundle(
    retail_data: bytes,
    candidate_data: bytes,
    indices: set[int],
    outdir: Path,
    *,
    label: str,
) -> dict:
    """Atomically emit explicit, hash-bound host texture replacements."""
    if not indices:
        raise ValueError("select at least one descriptor index")
    if outdir.exists():
        raise FileExistsError(f"output directory already exists: {outdir}")

    comparison = compare_xpp(retail_data, candidate_data)
    retail_summary, retail_records = validate_xpp(retail_data)
    candidate_summary, candidate_records = validate_xpp(candidate_data)
    retail_by_index = {record.index: record for record in retail_records}
    candidate_by_index = {record.index: record for record in candidate_records}
    unknown = indices - retail_by_index.keys()
    if unknown:
        raise ValueError(f"unknown descriptor indices: {sorted(unknown)}")

    retail_texels = heap_bytes(retail_data, parse_xpp(retail_data, len(retail_data)))
    candidate_texels = heap_bytes(
        candidate_data, parse_xpp(candidate_data, len(candidate_data))
    )
    records: list[dict] = []
    source_hashes: set[str] = set()
    payloads: list[tuple[str, bytes]] = []

    for index in sorted(indices):
        retail = retail_by_index[index]
        candidate = candidate_by_index[index]
        if retail.faces != 1 or candidate.faces != 1:
            raise ValueError(f"descriptor {index} is a cubemap; host bundles require 2D")

        source_payload, source_mips = runtime_payload(retail_texels, retail)
        replacement_payload, replacement_mips = runtime_payload(
            candidate_texels, candidate
        )
        source_sha256 = hashlib.sha256(source_payload).hexdigest()
        candidate_sha256 = hashlib.sha256(replacement_payload).hexdigest()
        if source_sha256 == candidate_sha256:
            raise ValueError(f"descriptor {index} has no runtime payload change")
        if source_sha256 in source_hashes:
            raise ValueError(
                f"descriptor {index} duplicates a selected retail runtime identity"
            )
        source_hashes.add(source_sha256)

        filename = f"texture-{index:04d}-{source_sha256[:16]}.bin"
        payloads.append((filename, replacement_payload))
        records.append(
            {
                "descriptor": index,
                "source": {
                    "sha256": source_sha256,
                    "width": retail.width,
                    "height": retail.height,
                    "mipmaps": source_mips,
                    "bytes": len(source_payload),
                    "format": f"0x{retail.format:02x}",
                },
                "candidate": {
                    "sha256": candidate_sha256,
                    "width": candidate.width,
                    "height": candidate.height,
                    "mipmaps": replacement_mips,
                    "bytes": len(replacement_payload),
                    "pitch": runtime_pitch(candidate),
                    "format": f"0x{candidate.format:02x}",
                    "file": filename,
                },
            }
        )

    report = {
        "schema": 1,
        "label": label,
        "retail_xpp_sha256": hashlib.sha256(retail_data).hexdigest(),
        "candidate_xpp_sha256": hashlib.sha256(candidate_data).hexdigest(),
        "retail": retail_summary,
        "candidate": candidate_summary,
        "comparison": comparison,
        "replacement_count": len(records),
        "records": records,
    }

    outdir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{outdir.name}.", dir=outdir.parent)
    )
    try:
        for filename, payload in payloads:
            (staging / filename).write_bytes(payload)

        header = (
            "source_sha256\tsource_width\tsource_height\tsource_mipmaps\t"
            "source_bytes\tformat\tcandidate_sha256\tcandidate_width\t"
            "candidate_height\tcandidate_mipmaps\tcandidate_bytes\t"
            "candidate_pitch\tfile"
        )
        rows = ["# xpp-tool host texture replacements v1", header]
        for record in records:
            source = record["source"]
            candidate = record["candidate"]
            rows.append(
                "\t".join(
                    str(value)
                    for value in (
                        source["sha256"],
                        source["width"],
                        source["height"],
                        source["mipmaps"],
                        source["bytes"],
                        source["format"],
                        candidate["sha256"],
                        candidate["width"],
                        candidate["height"],
                        candidate["mipmaps"],
                        candidate["bytes"],
                        candidate["pitch"],
                        candidate["file"],
                    )
                )
            )
        (staging / "replacements.tsv").write_text(
            "\n".join(rows) + "\n", encoding="ascii"
        )
        (staging / "bundle.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(staging, outdir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return report
