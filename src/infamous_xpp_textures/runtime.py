"""Build exact texel identities for opt-in emulator runtime tracing."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .heap import align_up, heap_bytes, level_size
from .validation import validate_xpp
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
