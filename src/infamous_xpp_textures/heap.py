"""Resolve texel-heap offsets from the iF1 0x70-byte texture descriptor.

The descriptor lives in chunk type 0x03100000. Byte +0x40 is the
payload-relative address of that texture's mip chain. The payload begins at
header +0x28, while the texture heap is chunk type 0x0D800000:

    file_offset = header[+0x28] + desc[+0x40]
    heap_offset = desc[+0x40] - texel_chunk.offset

Chains are stored in descending size order, each padded to 128 bytes.
Cubemaps store six padded chains back to back. Adjacent +0x40 deltas equal
align_up(chain_bytes, 128) * faces.
"""

from __future__ import annotations

import struct

from .xpp import TEXDESC_CHUNK, TEXEL_CHUNK, XppFile

DESC_STRIDE = 0x70
HEAP_ALIGN = 128
DESC_DATA_PTR = 0x40
DESC_WIDTH = 0x24
DESC_HEIGHT = 0x28
DESC_MIPS = 0x2C
DESC_FORMAT_WORD = 0x44
DESC_EMBEDDED_MIPS = DESC_FORMAT_WORD + 1
DESC_FORMAT_BYTE = 0x46

BLOCK_BYTES = {0x86: 8, 0x87: 16, 0x88: 16}
BPP = {0x84: 2, 0x85: 4, 0x8F: 2, 0x95: 2}


def level_size(fmt: int, w: int, h: int, level: int) -> int:
    lw, lh = max(1, w >> level), max(1, h >> level)
    if fmt in BLOCK_BYTES:
        return ((lw + 3) // 4) * ((lh + 3) // 4) * BLOCK_BYTES[fmt]
    return lw * lh * BPP[fmt]


def chain_size(fmt: int, w: int, h: int, mips: int) -> int:
    return sum(level_size(fmt, w, h, i) for i in range(mips))


def align_up(v: int, a: int = HEAP_ALIGN) -> int:
    return ((v + a - 1) // a) * a


class TextureRecord:
    """One 0x70-byte descriptor, with its resolved heap offset."""

    __slots__ = (
        "index",
        "raw",
        "width",
        "height",
        "mips",
        "embedded_mips",
        "format",
        "format_word",
        "data_addr",
        "heap_offset",
        "chain_bytes",
    )

    def __init__(self, index: int, raw: bytes):
        self.index = index
        self.raw = raw
        self.width, self.height, self.mips = struct.unpack_from(">III", raw, DESC_WIDTH)
        self.embedded_mips = raw[DESC_EMBEDDED_MIPS]
        self.format_word = struct.unpack_from(">I", raw, DESC_FORMAT_WORD)[0]
        self.format = raw[DESC_FORMAT_BYTE] & 0x9F
        self.data_addr = struct.unpack_from(">I", raw, DESC_DATA_PTR)[0]
        self.heap_offset = -1
        self.chain_bytes = chain_size(self.format, self.width, self.height, self.mips)

    @property
    def cubemap(self) -> bool:
        return bool(self.raw[DESC_FORMAT_WORD + 3] & 0x04)

    @property
    def faces(self) -> int:
        return 6 if self.cubemap else 1

    @property
    def stride_bytes(self) -> int:
        return align_up(self.chain_bytes) * self.faces

    @property
    def compressed(self) -> bool:
        return self.format in BLOCK_BYTES

    def __repr__(self) -> str:
        return (
            f"<tex{self.index} {self.width}x{self.height} m{self.mips} "
            f"fmt=0x{self.format:02x} @{self.heap_offset}>"
        )


def _is_pow2(v: int) -> bool:
    return v > 0 and (v & (v - 1)) == 0


def descriptor_reason(raw: bytes) -> str:
    """Empty string if usable, else a machine-readable skip reason."""
    if len(raw) < DESC_STRIDE:
        return "descriptor-truncated"
    raw_fmt = raw[DESC_FORMAT_BYTE]
    fmt = raw_fmt & 0x9F
    if fmt not in BLOCK_BYTES and fmt not in BPP:
        return f"unknown-format:0x{raw_fmt:02x}"
    w, h, mips = struct.unpack_from(">III", raw, DESC_WIDTH)
    if not (_is_pow2(w) and _is_pow2(h)) or w > 8192 or h > 8192:
        return "bad-dimensions"
    if not (1 <= mips <= 14) or mips > max(w, h).bit_length():
        return "bad-mipcount"
    return ""


def read_records(data: bytes, pkg: XppFile) -> list[TextureRecord]:
    recs: list[TextureRecord] = []
    for idx, _raw, rec, reason in read_all_descriptors(data, pkg):
        if rec is not None and not reason:
            recs.append(rec)
    return recs


def read_all_descriptors(
    data: bytes, pkg: XppFile
) -> list[tuple[int, bytes, TextureRecord | None, str]]:
    base = pkg.data_offset
    out: list[tuple[int, bytes, TextureRecord | None, str]] = []
    idx = 0
    for chunk in pkg.chunks:
        if chunk.type_tag != TEXDESC_CHUNK:
            continue
        start = base + chunk.offset
        end = min(start + chunk.size, len(data))
        for off in range(start, end - DESC_STRIDE + 1, DESC_STRIDE):
            raw = data[off : off + DESC_STRIDE]
            reason = descriptor_reason(raw)
            rec = None
            if not reason:
                try:
                    rec = TextureRecord(idx, raw)
                except struct.error:
                    rec, reason = None, "descriptor-unpack"
            out.append((idx, raw, rec, reason))
            idx += 1
    heaps = heap_chunks(pkg)
    heap_prefix = []
    cursor = 0
    for chunk in heaps:
        heap_prefix.append((chunk, cursor))
        cursor += chunk.size

    resolved: list[tuple[int, bytes, TextureRecord | None, str]] = []
    for idx, raw, rec, reason in out:
        if rec is not None:
            containing = next(
                (
                    (chunk, prefix)
                    for chunk, prefix in heap_prefix
                    if chunk.offset <= rec.data_addr
                    and rec.data_addr + rec.chain_bytes * rec.faces <= chunk.offset + chunk.size
                ),
                None,
            )
            if containing is None:
                rec = None
                reason = "data-pointer-outside-texel-heap"
            else:
                chunk, prefix = containing
                rec.heap_offset = prefix + rec.data_addr - chunk.offset
        resolved.append((idx, raw, rec, reason))
    return resolved


def heap_chunks(pkg: XppFile):
    return sorted((c for c in pkg.chunks if c.type_tag == TEXEL_CHUNK), key=lambda c: c.offset)


def heap_bytes(data: bytes, pkg: XppFile) -> bytes:
    """Whole texel heap, all 0x0D800000 chunks concatenated in offset order."""
    base = pkg.data_offset
    return b"".join(
        data[base + c.offset : base + c.offset + c.size] for c in heap_chunks(pkg)
    )


def verify_layout(recs: list[TextureRecord]) -> tuple[int, int]:
    """(matching_pairs, total_pairs) for delta == align_up(chain) * faces."""
    ordered = sorted(recs, key=lambda r: r.data_addr)
    ok = tot = 0
    for a, b in zip(ordered, ordered[1:]):
        tot += 1
        if b.data_addr - a.data_addr == a.stride_bytes:
            ok += 1
    return ok, tot
