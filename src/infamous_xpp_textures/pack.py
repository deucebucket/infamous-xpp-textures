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


def _write_desc(raw: bytes, *, width: int, height: int, mips: int, data_addr: int) -> bytes:
    out = bytearray(raw)
    if len(out) < DESC_STRIDE:
        raise PackError("truncated descriptor")
    struct.pack_into(">III", out, DESC_WIDTH, width, height, mips)
    struct.pack_into(">I", out, DESC_DATA_PTR, data_addr)
    struct.pack_into(">I", out, 0x58, (width << 16) | height)
    return bytes(out)


def rebuild_xpp(data: bytes, new_descs: list[bytes], new_heap: bytes) -> bytes:
    pkg = parse_xpp(data, len(data))
    texels = heap_chunks(pkg)
    if len(texels) != 1:
        raise PackError(
            f"packer needs exactly one texel heap chunk, this package has {len(texels)}"
        )

    desc_chunks = [c for c in pkg.chunks if c.type_tag == TEXDESC_CHUNK]
    desc_blob = b"".join(new_descs)
    expected = sum(c.size for c in desc_chunks)
    if len(desc_blob) != expected:
        raise PackError(
            f"descriptor payload {len(desc_blob)} bytes, package table expects {expected}"
        )

    pieces: list[tuple[Chunk, bytes]] = []
    desc_cursor = 0
    heap_written = False
    for chunk in pkg.chunks:
        if chunk.type_tag == TEXDESC_CHUNK:
            blob = desc_blob[desc_cursor : desc_cursor + chunk.size]
            desc_cursor += chunk.size
        elif chunk.type_tag == TEXEL_CHUNK:
            if heap_written:
                raise PackError("multiple texel heaps")
            blob = new_heap
            heap_written = True
        else:
            start = pkg.data_offset + chunk.offset
            blob = data[start : start + chunk.size]
        pieces.append((chunk, blob))

    new_payload = bytearray()
    new_chunks: list[Chunk] = []
    delta = 0
    for chunk, blob in pieces:
        new_off = chunk.offset + delta
        if len(new_payload) < new_off:
            new_payload.extend(b"\x00" * (new_off - len(new_payload)))
        elif len(new_payload) > new_off:
            raise PackError("chunk layout overlap while rebuilding")
        new_payload.extend(blob)
        delta += len(blob) - chunk.size
        new_chunks.append(Chunk(chunk.type_tag, len(blob), new_off, 0))

    data_size = len(new_payload)
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

    new_segments = []
    for seg in pkg.segments:
        group = new_chunks[seg.first_chunk : seg.first_chunk + seg.chunk_count]
        if not group:
            raise PackError("empty segment while rebuilding")
        start = group[0].offset
        end = group[-1].offset + group[-1].size
        new_segments.append((seg.type_tag, end - start, start, 0, 0, seg.first_chunk, seg.chunk_count))
    cursor = 0
    for i, (_t, size, start, *_rest) in enumerate(new_segments):
        if start != cursor:
            raise PackError(f"segment {i} not contiguous after rebuild")
        cursor += size
    if cursor != data_size:
        raise PackError("segments do not cover rebuilt payload")

    for i, row in enumerate(new_segments):
        struct.pack_into(">7I", out, TABLES_OFFSET + i * SEGMENT_SIZE, *row)
    chunk_start = TABLES_OFFSET + pkg.segment_count * SEGMENT_SIZE
    for i, chunk in enumerate(new_chunks):
        struct.pack_into(
            ">4I", out, chunk_start + i * CHUNK_SIZE, chunk.type_tag, chunk.size, chunk.offset, 0
        )
    fixup_start = chunk_start + pkg.chunk_count * CHUNK_SIZE
    out[fixup_start:data_offset] = data[fixup_start : pkg.data_offset]
    out[data_offset:] = new_payload
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
    for rec in recs:
        if rec.index in replacements:
            w, h, rgba = replacements[rec.index]
            if rec.faces != 1:
                raise PackError(
                    f"texture {rec.index} is a cubemap; packer only replaces 2D textures"
                )
            if (w, h) == (rec.width, rec.height) and not allow_resize:
                mips = rec.mips
            else:
                mips = _mip_count(w, h)
            fmt = rec.format
            if not allow_resize and (w, h, mips) != (rec.width, rec.height, rec.mips):
                raise PackError(
                    f"texture {rec.index} size changed {rec.width}x{rec.height}m{rec.mips} "
                    f"-> {w}x{h}m{mips}; pass --allow-resize"
                )
            chain = encode_mip_chain(rgba, w, h, fmt, mips)
            planned.append((rec, w, h, mips, padded_chain(chain, 1)))
        else:
            texels = bytes(
                data[
                    pkg.data_offset
                    + heap_chunks(pkg)[0].offset : pkg.data_offset
                    + heap_chunks(pkg)[0].offset
                    + heap_chunks(pkg)[0].size
                ]
            )
            take = rec.stride_bytes
            remain = len(texels) - rec.heap_offset
            if remain <= 0:
                raise PackError(f"texture {rec.index} heap slice empty")
            # Last chain in a retail heap may omit the final 128-byte pad.
            blob = texels[rec.heap_offset : rec.heap_offset + min(take, remain)]
            if len(blob) < rec.chain_bytes:
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
