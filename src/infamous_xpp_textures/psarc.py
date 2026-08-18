"""Read-only PlayStation PSARC table of contents and entry inflate."""

from __future__ import annotations

import struct
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
