"""Read-only PlayStation PSARC table of contents and entry inflate."""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import zlib
from pathlib import Path


def read_toc(path: str | Path):
    path = Path(path)
    with path.open("rb") as f:
        hdr = f.read(32)
        magic, ver, comp, toc_len, ent_size, ent_count, block_size, flags = struct.unpack(
            ">4sI4sIIIII", hdr
        )
        if magic != b"PSAR":
            raise ValueError(f"not a PSARC: {magic!r}")
        info = {
            "version": f"{ver >> 16}.{ver & 0xFFFF}",
            "compression": comp.decode("ascii", "replace"),
            "toc_length": toc_len,
            "entry_size": ent_size,
            "entry_count": ent_count,
            "block_size": block_size,
            "flags": flags,
        }
        entries = []
        for _ in range(ent_count):
            raw = f.read(ent_size)
            entries.append(
                {
                    "md5": raw[0:16].hex(),
                    "zindex": struct.unpack(">I", raw[16:20])[0],
                    "length": int.from_bytes(raw[20:25], "big"),
                    "offset": int.from_bytes(raw[25:30], "big"),
                }
            )
        bw = _bsize_width(block_size)
        nblocks = (toc_len - 32 - ent_count * ent_size) // bw
        braw = f.read(nblocks * bw)
        blocks = [int.from_bytes(braw[i * bw : (i + 1) * bw], "big") for i in range(nblocks)]
        manifest = _read_entry(f, entries[0], blocks, block_size)
        names = manifest.decode("utf-8", "replace").split("\n")
    return info, entries, names, blocks


def extract_entry(psarc_path: str | Path, wanted: str) -> bytes:
    info, entries, names, blocks = read_toc(psarc_path)
    want = wanted.lstrip("/")
    with Path(psarc_path).open("rb") as f:
        for name, ent in zip(names, entries[1:]):
            if name.lstrip("/") == want or name == wanted:
                return _read_entry(f, ent, blocks, info["block_size"])
    raise FileNotFoundError(f"PSARC entry not found: {wanted}")


def iter_archive_entries(psarc_path: str | Path):
    """Yield ``(manifest_name, payload)`` pairs in archive order."""
    info, entries, names, blocks = read_toc(psarc_path)
    with Path(psarc_path).open("rb") as handle:
        for name, entry in zip(names, entries[1:]):
            yield name, _read_entry(handle, entry, blocks, info["block_size"])


def _bsize_width(block_size: int) -> int:
    if block_size <= 0x100:
        return 1
    if block_size <= 0x10000:
        return 2
    if block_size <= 0x1000000:
        return 3
    return 4


def _read_entry(f, ent: dict, blocks: list[int], block_size: int) -> bytes:
    f.seek(ent["offset"])
    out = b""
    idx = ent["zindex"]
    while len(out) < ent["length"]:
        bs = blocks[idx]
        idx += 1
        if bs == 0:
            out += f.read(block_size)
        else:
            chunk = f.read(bs)
            try:
                out += zlib.decompress(chunk)
            except zlib.error:
                out += chunk
    return out[: ent["length"]]


def build_archive(
    names: list[str],
    payloads: list[bytes],
    *,
    manifest: bytes | None = None,
    block_size: int = 65536,
    compression_level: int = 9,
    version: int = (1 << 16) | 3,
    flags: int = 3,
) -> bytes:
    """Build a zlib PSARC while retaining the supplied entry order and names."""
    if len(names) != len(payloads):
        raise ValueError("PSARC names and payload counts differ")
    if not 1 <= compression_level <= 9:
        raise ValueError("compression level must be from 1 through 9")
    if manifest is None:
        manifest = "\n".join(names).encode("utf-8")
    files = [manifest, *payloads]

    block_counts = [max(1, (len(data) + block_size - 1) // block_size) for data in files]
    zindices: list[int] = []
    block_cursor = 0
    for count in block_counts:
        zindices.append(block_cursor)
        block_cursor += count

    packed_blocks: list[bytes] = []
    block_sizes: list[int] = []
    for data, count in zip(files, block_counts):
        for index in range(count):
            chunk = data[index * block_size : (index + 1) * block_size]
            compressed = zlib.compress(chunk, compression_level)
            if len(chunk) == block_size and len(compressed) >= block_size:
                packed_blocks.append(chunk)
                block_sizes.append(0)
            elif compressed and len(compressed) < len(chunk):
                packed_blocks.append(compressed)
                block_sizes.append(len(compressed))
            else:
                packed_blocks.append(chunk)
                block_sizes.append(len(chunk))

    entry_size = 30
    width = _bsize_width(block_size)
    toc_length = 32 + len(files) * entry_size + len(block_sizes) * width
    offsets: list[int] = []
    cursor = toc_length
    block_index = 0
    for count in block_counts:
        offsets.append(cursor)
        for _ in range(count):
            cursor += len(packed_blocks[block_index])
            block_index += 1

    out = bytearray()
    out.extend(
        struct.pack(
            ">4sI4sIIIII",
            b"PSAR",
            version,
            b"zlib",
            toc_length,
            entry_size,
            len(files),
            block_size,
            flags,
        )
    )
    for index, (data, zindex, offset) in enumerate(zip(files, zindices, offsets)):
        digest = (
            bytes(16)
            if index == 0
            else hashlib.md5(names[index - 1].upper().encode()).digest()
        )
        out.extend(digest)
        out.extend(struct.pack(">I", zindex))
        out.extend(len(data).to_bytes(5, "big"))
        out.extend(offset.to_bytes(5, "big"))
    for size in block_sizes:
        out.extend(size.to_bytes(width, "big"))
    if len(out) != toc_length:
        raise ValueError("PSARC table size calculation failed")
    for block in packed_blocks:
        out.extend(block)
    return bytes(out)


def rebuild_archive(
    source: str | Path,
    destination: str | Path,
    replacements: dict[str, bytes],
    *,
    compression_level: int = 9,
    require_all: bool = False,
) -> dict[str, int]:
    """Atomically rebuild a PSARC, replacing matching entries by basename."""
    source = Path(source)
    destination = Path(destination)
    info, entries, names, blocks = read_toc(source)
    if info["compression"] != "zlib" or info["entry_size"] != 30:
        raise ValueError("only standard zlib PSARCs with 30-byte entries are supported")
    if len(names) != len(entries) - 1:
        raise ValueError("PSARC manifest and entry table lengths differ")

    manifest: bytes
    payloads: list[bytes] = []
    replaced: set[str] = set()
    with source.open("rb") as handle:
        manifest = _read_entry(handle, entries[0], blocks, info["block_size"])
        for name, entry in zip(names, entries[1:]):
            basename = Path(name).name
            if basename in replacements:
                payloads.append(replacements[basename])
                replaced.add(basename)
            else:
                payloads.append(_read_entry(handle, entry, blocks, info["block_size"]))
    missing = set(replacements) - replaced
    if require_all and missing:
        raise ValueError(f"replacement names are absent from the PSARC: {sorted(missing)}")
    if not replaced:
        raise ValueError("none of the replacement names are present in the PSARC")

    major, minor = (int(part) for part in info["version"].split(".", 1))
    rebuilt = build_archive(
        names,
        payloads,
        manifest=manifest,
        block_size=info["block_size"],
        compression_level=compression_level,
        version=(major << 16) | minor,
        flags=info["flags"],
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(rebuilt)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return {
        "replaced": len(replaced),
        "ignored": len(missing),
        "entries": len(names),
        "bytes": len(rebuilt),
    }
