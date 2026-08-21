"""Rewrite a PACK-v8 XPP with new texel heaps and descriptor fields."""

from __future__ import annotations

import struct
from pathlib import Path

from .decode import decode_level, iter_textures
from .encode import encode_mip_chain
from .heap import (
    DESC_DATA_PTR,
    DESC_FORMAT_WORD,
    DESC_STRIDE,
    DESC_WIDTH,
    HEAP_ALIGN,
    TEXDESC_CHUNK,
    TEXEL_CHUNK,
    align_up,
    chain_size,
    heap_chunks,
    heap_bytes,
    read_records,
)
from .pngio import read_png, scale_nearest
from .xpp import (
    CHUNK_SIZE,
    OVERLAY_CHUNK_TYPE,
    SEGMENT_SIZE,
    TABLES_OFFSET,
    parse_xpp,
)


class PackError(ValueError):
    pass


def _verify_rebuilt_layout(original, rebuilt) -> None:
    """Require every retail inter-chain gap to survive at the new chain size."""
    before = sorted(original, key=lambda record: record.data_addr)
    after = sorted(rebuilt, key=lambda record: record.data_addr)
    if [record.index for record in before] != [record.index for record in after]:
        raise PackError("texture pointer order changed while rebuilding")
    for old, old_next, new, new_next in zip(before, before[1:], after, after[1:]):
        opaque_gap = old_next.data_addr - old.data_addr - old.stride_bytes
        if opaque_gap < 0:
            raise PackError(f"retail texture {old.index} allocation overlaps its successor")
        expected = new.data_addr + new.stride_bytes + opaque_gap
        if new_next.data_addr != expected:
            raise PackError(
                f"texture {new.index} successor is 0x{new_next.data_addr:x}, "
                f"expected 0x{expected:x}"
            )


def _mip_count(width: int, height: int) -> int:
    return max(width, height).bit_length()


def _write_desc(raw: bytes, *, width: int, height: int, mips: int, data_addr: int) -> bytes:
    out = bytearray(raw)
    if len(out) < DESC_STRIDE:
        raise PackError("truncated descriptor")
    struct.pack_into(">III", out, DESC_WIDTH, width, height, mips)
    struct.pack_into(">I", out, DESC_DATA_PTR, data_addr)
    format_word = struct.unpack_from(">I", out, DESC_FORMAT_WORD)[0]
    struct.pack_into(
        ">I", out, DESC_FORMAT_WORD, (format_word & 0xFF00FFFF) | (mips << 16)
    )
    struct.pack_into(">I", out, 0x58, (width << 16) | height)
    return bytes(out)


def rebuild_xpp(data: bytes, new_descs: list[bytes], new_heap: bytes) -> bytes:
    """Splice one rebuilt heap while preserving every unrelated payload byte."""
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

    heap_chunk = texels[0]
    old_start = heap_chunk.offset
    old_end = old_start + heap_chunk.size
    delta = len(new_heap) - heap_chunk.size

    old_payload = bytearray(data[pkg.data_offset : pkg.data_offset + pkg.data_size])
    desc_cursor = 0
    for chunk in desc_chunks:
        blob = desc_blob[desc_cursor : desc_cursor + chunk.size]
        old_payload[chunk.offset : chunk.offset + chunk.size] = blob
        desc_cursor += chunk.size

    new_payload = old_payload[:old_start] + new_heap + old_payload[old_end:]
    metadata = bytearray(data[: pkg.data_offset])
    struct.pack_into(">I", metadata, 0x2C, pkg.data_size + delta)

    owning_segments = 0
    for index, segment in enumerate(pkg.segments):
        row = TABLES_OFFSET + index * SEGMENT_SIZE
        segment_end = segment.offset + segment.size
        if segment.offset <= old_start and old_end <= segment_end:
            struct.pack_into(">I", metadata, row + 4, segment.size + delta)
            owning_segments += 1
        elif segment.offset >= old_end:
            struct.pack_into(">I", metadata, row + 8, segment.offset + delta)
        elif segment_end > old_start:
            raise PackError(f"segment {index} partially overlaps the texel heap")
    if owning_segments != 1:
        raise PackError(f"expected one segment to own the texel heap, found {owning_segments}")

    chunk_start = TABLES_OFFSET + pkg.segment_count * SEGMENT_SIZE
    changed_heaps = 0
    for index, chunk in enumerate(pkg.chunks):
        row = chunk_start + index * CHUNK_SIZE
        chunk_end = chunk.offset + chunk.size
        if chunk.type_tag == TEXEL_CHUNK and (chunk.offset, chunk.size) == (
            old_start,
            heap_chunk.size,
        ):
            struct.pack_into(">I", metadata, row + 4, len(new_heap))
            changed_heaps += 1
        elif chunk.offset >= old_end:
            struct.pack_into(">I", metadata, row + 8, chunk.offset + delta)
        elif chunk_end > old_start and chunk.type_tag != OVERLAY_CHUNK_TYPE:
            raise PackError(f"chunk {index} partially overlaps the texel heap")
    if changed_heaps != 1:
        raise PackError(f"expected one texel-heap chunk, found {changed_heaps}")

    out = bytes(metadata + new_payload)
    check = parse_xpp(out, len(out))
    if check.data_offset != pkg.data_offset:
        raise PackError("metadata size changed while rebuilding")
    if data[pkg.data_offset + old_end :] != out[check.data_offset + old_start + len(new_heap) :]:
        raise PackError("non-texture payload tail changed while rebuilding")
    return out


def pack_chains(
    data: bytes,
    replacements: dict[int, tuple[int, int, int, bytes]],
) -> bytes:
    """Replace encoded chains while preserving the retail XPP's opaque layout.

    ``replacements`` maps descriptor index to ``(width, height, mips, bytes)``.
    Chain bytes are copied verbatim; no image decoding or recompression occurs.
    """
    pkg = parse_xpp(data, len(data))
    recs = read_records(data, pkg)
    if not recs:
        raise PackError("no texture descriptors")
    by_index = {r.index: r for r in recs}
    unknown = set(replacements) - set(by_index)
    if unknown:
        raise PackError(f"unknown texture indices: {sorted(unknown)}")

    texel_chunks = heap_chunks(pkg)
    if len(texel_chunks) != 1:
        raise PackError(
            f"packer needs exactly one texel heap chunk, this package has {len(texel_chunks)}"
        )
    texel_chunk = texel_chunks[0]
    texels = heap_bytes(data, pkg)

    planned: dict[int, tuple[int, int, int, bytes | None]] = {}
    for rec in recs:
        if rec.index in replacements:
            w, h, mips, encoded = replacements[rec.index]
            if rec.faces != 1:
                raise PackError(
                    f"texture {rec.index} is a cubemap; packer only replaces 2D textures"
                )
            if w <= 0 or h <= 0 or mips <= 0:
                raise PackError(
                    f"texture {rec.index} has invalid dimensions or mip count"
                )
            expected = chain_size(rec.format, w, h, mips)
            if len(encoded) != expected:
                raise PackError(
                    f"texture {rec.index} chain is {len(encoded)} bytes, expected {expected}"
                )
            planned[rec.index] = (w, h, mips, encoded)
        else:
            planned[rec.index] = (rec.width, rec.height, rec.mips, None)

    # Preserve retail pointer order and every byte not owned by a recognized
    # mip chain. Some packages retain opaque data inside the texel-heap chunk.
    ordered = sorted(recs, key=lambda rec: rec.data_addr)
    if len({rec.data_addr for rec in ordered}) != len(ordered):
        raise PackError("duplicate texture data pointers")
    first = ordered[0].data_addr
    heap_end = texel_chunk.offset + texel_chunk.size
    if not texel_chunk.offset <= first < heap_end:
        raise PackError("first texture pointer is outside the texel heap")
    heap = bytearray()
    heap.extend(texels[: first - texel_chunk.offset])
    addr_for: dict[int, int] = {}
    for position, rec in enumerate(ordered):
        span_end = ordered[position + 1].data_addr if position + 1 < len(ordered) else heap_end
        span_size = span_end - rec.data_addr
        if span_size < rec.chain_bytes:
            raise PackError(f"texture {rec.index} allocation span is short")
        source_start = rec.data_addr - texel_chunk.offset
        source_end = span_end - texel_chunk.offset
        original_span = texels[source_start:source_end]
        if len(original_span) != span_size:
            raise PackError(f"texture {rec.index} allocation span exceeds the heap")

        w, h, mips, replacement = planned[rec.index]
        addr_for[rec.index] = texel_chunk.offset + len(heap)
        if replacement is None:
            heap.extend(original_span)
            continue

        heap.extend(replacement)
        if len(replacement) == rec.chain_bytes:
            heap.extend(original_span[rec.chain_bytes:])
            continue

        opaque_start = min(rec.stride_bytes, span_size)
        opaque_suffix = original_span[opaque_start:]
        alignment = HEAP_ALIGN if position + 1 < len(ordered) or opaque_suffix else 0x10
        padded_end = align_up(len(heap), alignment)
        heap.extend(b"\x00" * (padded_end - len(heap)))
        heap.extend(opaque_suffix)

    new_descs: list[bytes] = []
    for rec in recs:
        w, h, mips, _replacement = planned[rec.index]
        new_descs.append(
            _write_desc(
                rec.raw,
                width=w,
                height=h,
                mips=mips,
                data_addr=addr_for[rec.index],
            )
        )

    packed = rebuild_xpp(data, new_descs, bytes(heap))
    again = parse_xpp(packed, len(packed))
    check = read_records(packed, again)
    if any(rec.mips != rec.embedded_mips for rec in check):
        raise PackError("packed descriptors have inconsistent mip counts")
    _verify_rebuilt_layout(recs, check)
    return packed


def pack_replacements(
    data: bytes,
    replacements: dict[int, tuple[int, int, bytes]],
    *,
    allow_resize: bool = True,
) -> bytes:
    """Encode and replace mip-0 RGBA images by descriptor index."""
    pkg = parse_xpp(data, len(data))
    recs = {rec.index: rec for rec in read_records(data, pkg)}
    unknown = set(replacements) - set(recs)
    if unknown:
        raise PackError(f"unknown texture indices: {sorted(unknown)}")

    encoded: dict[int, tuple[int, int, int, bytes]] = {}
    for index, (width, height, rgba) in replacements.items():
        rec = recs[index]
        mips = (
            rec.mips
            if (width, height) == (rec.width, rec.height) and not allow_resize
            else _mip_count(width, height)
        )
        if not allow_resize and (width, height, mips) != (
            rec.width,
            rec.height,
            rec.mips,
        ):
            raise PackError(
                f"texture {index} size changed {rec.width}x{rec.height}m{rec.mips} "
                f"-> {width}x{height}m{mips}; pass --allow-resize"
            )
        chain = encode_mip_chain(rgba, width, height, rec.format, mips)
        encoded[index] = (width, height, mips, chain)
    return pack_chains(data, encoded)


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
