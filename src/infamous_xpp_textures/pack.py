"""Rewrite a PACK-v8 XPP with new texel heaps and descriptor fields."""

from __future__ import annotations

import struct
from pathlib import Path

from .decode import (
    FMT_BASE_MASK,
    FMT_LN,
    GCM_NAMES,
    decode_level,
    iter_textures,
)
from .encode import encode_mip_chain, padded_chain
from .heap import (
    DESC_DATA_PTR,
    DESC_HEIGHT,
    DESC_MIPS,
    DESC_STRIDE,
    DESC_WIDTH,
    HEAP_ALIGN,
    TEXDESC_CHUNK,
    TEXEL_CHUNK,
    TextureRecord,
    align_up,
    chain_size,
    heap_bytes,
    heap_chunks,
    read_records,
    verify_layout,
)
from .pngio import read_png, scale_nearest
from .xpp import (
    CHUNK_SIZE,
    FIXUP_SIZE,
    HEADER_SIZE,
    SEGMENT_SIZE,
    TABLES_OFFSET,
    Chunk,
    XppError,
    parse_xpp,
)


class PackError(ValueError):
    pass


def _mip_count(width: int, height: int) -> int:
    return max(width, height).bit_length()


def _legal_2d(width: int, height: int) -> bool:
    """Same cap descriptor_reason uses: pow2, 1..4096."""
    if width <= 0 or height <= 0 or width > 4096 or height > 4096:
        return False
    return (width & (width - 1)) == 0 and (height & (height - 1)) == 0


def _fit_replacement(
    width: int, height: int, rgba: bytes
) -> tuple[int, int, bytes] | None:
    """Legal 2D desc size, or None to keep retail.

    RSX 2D max is 4096. A 4x of a 2048-wide strip is 8192 — illegal.
    Halve those until they fit (8192x64 → 4096x32). Non-pow2 / short
    buffers cannot be fitted; caller keeps the retail slot.
    """
    if _legal_2d(width, height):
        return width, height, rgba
    if width <= 0 or height <= 0:
        return None
    if (width & (width - 1)) != 0 or (height & (height - 1)) != 0:
        return None
    if len(rgba) != width * height * 4:
        return None
    w, h, pix = width, height, rgba
    while w > 4096 or h > 4096:
        nw, nh = max(1, w // 2), max(1, h // 2)
        out = bytearray(nw * nh * 4)
        for y in range(nh):
            row = (y * 2) * w
            for x in range(nw):
                si = (row + x * 2) * 4
                di = (y * nw + x) * 4
                out[di : di + 4] = pix[si : si + 4]
        w, h, pix = nw, nh, bytes(out)
    if not _legal_2d(w, h):
        return None
    return w, h, pix


def _write_desc(raw: bytes, *, width: int, height: int, mips: int, data_addr: int) -> bytes:
    out = bytearray(raw)
    if len(out) < DESC_STRIDE:
        raise PackError("truncated descriptor")
    struct.pack_into(">III", out, DESC_WIDTH, width, height, mips)
    struct.pack_into(">I", out, DESC_DATA_PTR, data_addr)
    struct.pack_into(">I", out, 0x58, (width << 16) | height)
    return bytes(out)


def referenced_heap_indices(data: bytes, pkg, recs: list[TextureRecord]) -> list[int]:
    """Which 0x0D800000 chunks overlap a descriptor's chain (concatenated heap space)."""
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for chunk in heap_chunks(pkg):
        ranges.append((cursor, cursor + chunk.size))
        cursor += chunk.size
    used: set[int] = set()
    for rec in recs:
        start = rec.heap_offset
        end = rec.heap_offset + max(rec.chain_bytes, 1)
        for index, (lo, hi) in enumerate(ranges):
            if start < hi and end > lo:
                used.add(index)
    return sorted(used)


def rebuild_xpp(data: bytes, new_descs: list[bytes], new_heap: bytes) -> bytes:
    pkg = parse_xpp(data, len(data))
    recs = read_records(data, pkg)
    texels = heap_chunks(pkg)
    if not texels:
        raise PackError("packer needs a texel heap chunk, this package has 0")
    used = referenced_heap_indices(data, pkg, recs)
    if len(used) > 1:
        raise PackError(
            f"textures reference {len(used)} texel-heap chunks (indices {used}); "
            "packer will not merge or drop texels"
        )
    primary = used[0] if used else 0

    desc_chunks = [c for c in pkg.chunks if c.type_tag == TEXDESC_CHUNK]
    desc_blob = b"".join(new_descs)
    expected = sum(c.size for c in desc_chunks)
    if len(desc_blob) != expected:
        raise PackError(
            f"descriptor payload {len(desc_blob)} bytes, package table expects {expected}"
        )

    # Keep pre-heap payload offsets. Mesh records point into that range.
    # Only the used texel heap may change size; later chunks shift by that delta.
    primary_chunk = texels[primary]
    old_heap_off = primary_chunk.offset
    old_heap_size = primary_chunk.size
    grown = bytes(new_heap)
    if len(grown) < old_heap_size:
        grown = grown + b"\x00" * (old_heap_size - len(grown))
    delta = len(grown) - old_heap_size
    heap_end = old_heap_off + old_heap_size

    payload = bytearray(data[pkg.data_offset : pkg.data_offset + pkg.data_size])
    if delta:
        payload[heap_end:heap_end] = b"\x00" * delta
    payload[old_heap_off : old_heap_off + len(grown)] = grown

    desc_cursor = 0
    for chunk in pkg.chunks:
        if chunk.type_tag != TEXDESC_CHUNK:
            continue
        blob = desc_blob[desc_cursor : desc_cursor + chunk.size]
        desc_cursor += chunk.size
        dest = chunk.offset + (delta if chunk.offset >= heap_end else 0)
        payload[dest : dest + len(blob)] = blob

    new_chunks = []
    for chunk in pkg.chunks:
        off = chunk.offset + (delta if chunk.offset >= heap_end else 0)
        size = len(grown) if chunk == primary_chunk else chunk.size
        new_chunks.append(Chunk(chunk.type_tag, size, off, 0))

    new_segments = []
    for seg in pkg.segments:
        if not seg.chunk_count:
            raise PackError("empty segment while rebuilding")
        seg_end = seg.offset + seg.size
        if seg.offset >= heap_end:
            new_segments.append(
                (seg.type_tag, seg.size, seg.offset + delta, 0, 0, seg.first_chunk, seg.chunk_count)
            )
        elif seg_end <= old_heap_off:
            new_segments.append(
                (seg.type_tag, seg.size, seg.offset, 0, 0, seg.first_chunk, seg.chunk_count)
            )
        else:
            new_segments.append(
                (seg.type_tag, seg.size + delta, seg.offset, 0, 0, seg.first_chunk, seg.chunk_count)
            )
    cursor = 0
    for i, (_t, size, start, *_rest) in enumerate(new_segments):
        if start != cursor:
            raise PackError(f"segment {i} not contiguous after rebuild")
        cursor += size
    if cursor != len(payload):
        raise PackError("segments do not cover rebuilt payload")

    data_size = len(payload)
    data_offset = (
        TABLES_OFFSET
        + pkg.segment_count * SEGMENT_SIZE
        + pkg.chunk_count * CHUNK_SIZE
        + pkg.fixup_count * FIXUP_SIZE
    )
    out = bytearray(data_offset + data_size)
    out[0:HEADER_SIZE] = data[0:HEADER_SIZE]
    struct.pack_into(">I", out, 0x18, HEADER_SIZE)
    struct.pack_into(">I", out, 0x1C, data_offset - HEADER_SIZE)
    struct.pack_into(">I", out, 0x28, data_offset)
    struct.pack_into(">I", out, 0x2C, data_size)
    struct.pack_into(">QQQ", out, 0x70, pkg.segment_count, pkg.chunk_count, pkg.fixup_count)

    for i, row in enumerate(new_segments):
        struct.pack_into(">7I", out, TABLES_OFFSET + i * SEGMENT_SIZE, *row)
    chunk_start = TABLES_OFFSET + pkg.segment_count * SEGMENT_SIZE
    for i, chunk in enumerate(new_chunks):
        struct.pack_into(
            ">4I", out, chunk_start + i * CHUNK_SIZE, chunk.type_tag, chunk.size, chunk.offset, 0
        )
    fixup_start = chunk_start + pkg.chunk_count * CHUNK_SIZE
    out[fixup_start:data_offset] = data[fixup_start : pkg.data_offset]
    out[data_offset:] = payload
    parse_xpp(bytes(out), len(out))
    return bytes(out)


def pack_replacements(
    data: bytes,
    replacements: dict[int, tuple[int, int, bytes]],
    *,
    allow_resize: bool = True,
) -> bytes:
    """replacements: index -> (width, height, rgba8 of mip 0)."""
    pkg = parse_xpp(data, len(data))
    recs = read_records(data, pkg)
    if not recs:
        raise PackError("no texture descriptors")
    by_index = {r.index: r for r in recs}

    planned: list[tuple[TextureRecord, int, int, int, bytes]] = []
    texels = heap_bytes(data, pkg)
    last_addr = max(item.data_addr for item in recs)
    for rec in recs:
        repl = replacements.get(rec.index) if rec.faces == 1 else None
        fitted = _fit_replacement(*repl) if repl is not None else None
        if fitted is not None:
            w, h, rgba = fitted
            if (w, h) == (rec.width, rec.height) and not allow_resize:
                mips = rec.mips
            else:
                mips = _mip_count(w, h)
            if not allow_resize and (w, h, mips) != (rec.width, rec.height, rec.mips):
                raise PackError(
                    f"texture {rec.index} size changed {rec.width}x{rec.height}m{rec.mips} "
                    f"-> {w}x{h}m{mips}; pass --allow-resize"
                )
            chain = encode_mip_chain(rgba, w, h, rec.format, mips)
            planned.append((rec, w, h, mips, padded_chain(chain, 1)))
            continue
        # Cubemap, illegal 4x size (>4096 / non-pow2), or no replacement: keep retail.
        take = rec.stride_bytes
        remain = len(texels) - rec.heap_offset
        if remain <= 0:
            raise PackError(f"texture {rec.index} heap slice empty")
        # Last chain in a retail heap may omit the final 128-byte pad.
        blob = texels[rec.heap_offset : rec.heap_offset + min(take, remain)]
        if len(blob) < rec.chain_bytes:
            missing = rec.chain_bytes - len(blob)
            if rec.data_addr == last_addr and 0 < missing <= HEAP_ALIGN:
                blob = blob + b"\x00" * missing
            else:
                raise PackError(f"texture {rec.index} heap slice short")
        planned.append((rec, rec.width, rec.height, rec.mips, blob))

    # Retail stores chains largest-first. Rebuild in that order, then map back
    # to descriptor-table order for the 0x70 records.
    ordered = sorted(planned, key=lambda item: -len(item[4]))
    base_addr = min(r.data_addr for r in recs)
    heap = bytearray()
    addr_for: dict[int, int] = {}
    for rec, _w, _h, _m, blob in ordered:
        if len(blob) % HEAP_ALIGN:
            blob = blob + b"\x00" * (align_up(len(blob)) - len(blob))
        addr_for[rec.index] = base_addr + len(heap)
        heap.extend(blob)

    new_descs: list[bytes] = []
    for rec in recs:
        match = next(item for item in planned if item[0].index == rec.index)
        _, w, h, mips, _blob = match
        new_descs.append(_write_desc(rec.raw, width=w, height=h, mips=mips, data_addr=addr_for[rec.index]))

    packed = rebuild_xpp(data, new_descs, bytes(heap))
    again = parse_xpp(packed, len(packed))
    check = read_records(packed, again)
    ok, tot = verify_layout(check)
    if tot and ok != tot:
        raise PackError(f"packed layout failed verify {ok}/{tot}")
    return packed


def replacements_from_dir(stem: str, directory: Path) -> dict[int, tuple[int, int, bytes]]:
    found: dict[int, tuple[int, int, bytes]] = {}
    for path in sorted(directory.glob(f"{stem}.*.mip0.png")):
        try:
            idx = int(path.name.split(".")[1])
        except (IndexError, ValueError):
            continue
        w, h, rgba = read_png(path)
        found[idx] = (w, h, rgba)
    return found


def replacements_from_scale(data: bytes, scale: int) -> dict[int, tuple[int, int, bytes]]:
    pkg = parse_xpp(data, len(data))
    out: dict[int, tuple[int, int, bytes]] = {}
    for idx, rec, texels in iter_textures(data, pkg):
        if rec.reason or rec.faces != 1:
            continue
        _w, _h, rgba, _note = decode_level(rec, texels, 0, rec.heap_offset)
        nw, nh, scaled = scale_nearest(rgba, rec.width, rec.height, scale)
        out[idx] = (nw, nh, scaled)
    if not out:
        raise PackError("no 2D textures available to scale")
    return out
