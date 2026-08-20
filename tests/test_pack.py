"""Pack round-trip on a synthetic XPP. No retail bytes required."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from infamous_xpp_textures.decode import extract_package
from infamous_xpp_textures.encode import encode_mip_chain
from infamous_xpp_textures.heap import HEAP_ALIGN, read_records, verify_layout
from infamous_xpp_textures.pack import PackError, pack_replacements
from infamous_xpp_textures.pngio import read_png, write_png
from infamous_xpp_textures.xpp import parse_xpp

from test_synthetic import _minimal_xpp


def _desc(width=4, height=4, mips=1, fmt=0x86, data_addr=0x1000) -> bytes:
    desc = bytearray(0x70)
    struct.pack_into(">III", desc, 0x24, width, height, mips)
    struct.pack_into(">I", desc, 0x40, data_addr)
    desc[0x46] = fmt
    struct.pack_into(">I", desc, 0x58, (width << 16) | height)
    return bytes(desc)


def _pack_file(chunks: list[tuple[int, bytes]], *, nseg: int = 1) -> bytes:
    payload = b"".join(blob for _tag, blob in chunks)
    nchunk = len(chunks)
    data_offset = 0x88 + nseg * 28 + nchunk * 16
    buf = bytearray(data_offset + len(payload))
    buf[0:4] = b"PACK"
    struct.pack_into(">HH", buf, 4, 8, 0x70)
    struct.pack_into(">I", buf, 0x18, 0x70)
    struct.pack_into(">I", buf, 0x1C, data_offset - 0x70)
    struct.pack_into(">I", buf, 0x28, data_offset)
    struct.pack_into(">I", buf, 0x2C, len(payload))
    struct.pack_into(">QQQ", buf, 0x70, nseg, nchunk, 0)
    if nseg == 1:
        struct.pack_into(">7I", buf, 0x88, 0, len(payload), 0, 0, 0, 0, nchunk)
    off = 0
    table = 0x88 + nseg * 28
    for tag, blob in chunks:
        struct.pack_into(">4I", buf, table, tag, len(blob), off, 0)
        table += 16
        off += len(blob)
    buf[data_offset:] = payload
    return bytes(buf)


def test_encode_dxt1_size():
    rgba = bytes([255, 0, 0, 255] * 16)
    chain = encode_mip_chain(rgba, 4, 4, 0x86, 1)
    assert len(chain) == 8


def test_round_trip_replace(tmp_path: Path):
    data = _minimal_xpp()
    extract_package(data, "fix", tmp_path)
    png = tmp_path / "fix.0.mip0.png"
    assert png.is_file()
    w, h, rgba = read_png(png)
    packed = pack_replacements(data, {0: (w, h, rgba)}, allow_resize=False)
    pkg = parse_xpp(packed)
    recs = read_records(packed, pkg)
    assert len(recs) == 1
    ok, tot = verify_layout(recs)
    assert tot == 0 or ok == tot
    out2 = tmp_path / "round"
    found, written = extract_package(packed, "round", out2)
    assert found == 1 and written == 1
    w2, h2, _ = read_png(out2 / "round.0.mip0.png")
    assert (w2, h2) == (w, h)


def test_scale_rebuilds_header(tmp_path: Path):
    data = _minimal_xpp()
    rgba = bytes([0, 255, 0, 255] * 16)
    write_png(tmp_path / "big.png", 8, 8, bytes([0, 255, 0, 255] * 64))
    w, h, pix = read_png(tmp_path / "big.png")
    packed = pack_replacements(data, {0: (w, h, pix)}, allow_resize=True)
    recs = read_records(packed, parse_xpp(packed))
    assert recs[0].width == 8 and recs[0].height == 8
    assert recs[0].mips == 4
    ok, tot = verify_layout(recs)
    assert tot == 0 or ok == tot


def test_last_chain_omits_pad_when_copying_other_texture():
    d0 = _desc(data_addr=0x1000)
    d1 = _desc(data_addr=0x1000 + HEAP_ALIGN)
    heap = bytes(HEAP_ALIGN + 8)  # first padded, last chain unpadded
    data = _pack_file([(0x03100000, d0 + d1), (0x0D800000, heap)])
    recs = read_records(data, parse_xpp(data))
    assert recs[1].chain_bytes == 8
    assert recs[1].stride_bytes == HEAP_ALIGN
    assert recs[1].heap_offset == HEAP_ALIGN
    rgba = bytes([255, 0, 0, 255] * 16)
    packed = pack_replacements(data, {0: (4, 4, rgba)}, allow_resize=False)
    again = read_records(packed, parse_xpp(packed))
    ok, tot = verify_layout(again)
    assert tot == 1 and ok == 1


def test_last_chain_short_by_one_pad_is_accepted():
    d0 = _desc(data_addr=0x1000)
    d1 = _desc(data_addr=0x1000 + HEAP_ALIGN)
    heap = bytes(HEAP_ALIGN + 4)  # 4 < chain 8, missing <= 128
    data = _pack_file([(0x03100000, d0 + d1), (0x0D800000, heap)])
    rgba = bytes([0, 255, 0, 255] * 16)
    packed = pack_replacements(data, {0: (4, 4, rgba)}, allow_resize=False)
    ok, tot = verify_layout(read_records(packed, parse_xpp(packed)))
    assert tot == 1 and ok == 1


def test_cubemap_replacement_is_left_retail():
    d0 = bytearray(_desc(data_addr=0x1000))
    d1 = bytearray(_desc(data_addr=0x1000 + HEAP_ALIGN))
    d1[0x47] |= 0x04  # cubemap bit
    heap = bytes(HEAP_ALIGN + HEAP_ALIGN * 6)
    data = _pack_file([(0x03100000, bytes(d0) + bytes(d1)), (0x0D800000, heap)])
    recs = read_records(data, parse_xpp(data))
    assert recs[1].faces == 6
    rgba = bytes([255, 0, 0, 255] * 16)
    packed = pack_replacements(
        data, {0: (4, 4, rgba), 1: (4, 4, rgba)}, allow_resize=False
    )
    again = read_records(packed, parse_xpp(packed))
    assert len(again) == 2
    assert again[1].faces == 6
    ok, tot = verify_layout(again)
    assert tot == 1 and ok == 1


def test_oversize_4x_short_buffer_is_left_retail():
    d0 = _desc(data_addr=0x1000)
    d1 = _desc(data_addr=0x1000 + HEAP_ALIGN)
    heap = bytes(HEAP_ALIGN + 8)
    data = _pack_file([(0x03100000, d0 + d1), (0x0D800000, heap)])
    rgba = bytes([255, 0, 0, 255] * 16)
    packed = pack_replacements(
        data,
        {0: (4, 4, rgba), 1: (8192, 64, b"\x00" * 16)},
        allow_resize=True,
    )
    again = read_records(packed, parse_xpp(packed))
    assert len(again) == 2
    assert again[1].width == 4 and again[1].height == 4
    ok, tot = verify_layout(again)
    assert tot == 1 and ok == 1


def test_oversize_4x_is_clamped_to_4096():
    d0 = _desc(data_addr=0x1000)
    d1 = _desc(data_addr=0x1000 + HEAP_ALIGN)
    heap = bytes(HEAP_ALIGN + 8)
    data = _pack_file([(0x03100000, d0 + d1), (0x0D800000, heap)])
    rgba = bytes([255, 0, 0, 255] * 16)
    huge = bytes(8192 * 64 * 4)
    packed = pack_replacements(
        data, {0: (4, 4, rgba), 1: (8192, 64, huge)}, allow_resize=True
    )
    again = {r.index: r for r in read_records(packed, parse_xpp(packed))}
    assert again[1].width == 4096 and again[1].height == 32
    ok, tot = verify_layout(list(again.values()))
    assert tot == 1 and ok == 1


def test_unused_extra_heap_is_kept():
    desc = _desc(data_addr=0x1000)
    heap0 = bytes(8)
    heap1 = b"\x11" * 64
    data = _pack_file(
        [(0x03100000, desc), (0x0D800000, heap0), (0x0D800000, heap1)]
    )
    rgba = bytes([0, 0, 255, 255] * 16)
    packed = pack_replacements(data, {0: (4, 4, rgba)}, allow_resize=False)
    pkg = parse_xpp(packed)
    heaps = [c for c in pkg.chunks if c.type_tag == 0x0D800000]
    assert len(heaps) == 2
    leftover = packed[pkg.data_offset + heaps[1].offset : pkg.data_offset + heaps[1].offset + heaps[1].size]
    assert leftover == heap1
    ok, tot = verify_layout(read_records(packed, pkg))
    assert tot == 0 or ok == tot


def test_used_extra_heap_is_refused():
    d0 = _desc(data_addr=0x1000)
    d1 = _desc(data_addr=0x1000 + 8)
    heap0 = bytes(8)
    heap1 = bytes(8)
    data = _pack_file(
        [(0x03100000, d0 + d1), (0x0D800000, heap0), (0x0D800000, heap1)]
    )
    rgba = bytes([255, 255, 0, 255] * 16)
    with pytest.raises(PackError, match="texel-heap chunks"):
        pack_replacements(data, {0: (4, 4, rgba)}, allow_resize=False)


def test_district_table_order_overlap_does_not_fail_rebuild():
    """Chunk table can view overlapping ranges; rebuild must not overlap."""
    dummy = bytes(256)
    desc = _desc(data_addr=0x2000)
    heap = bytes(8)
    payload = dummy + heap
    # desc chunk offset sits inside the dummy range (table order != tight layout).
    nseg, nchunk = 1, 3
    data_offset = 0x88 + nseg * 28 + nchunk * 16
    buf = bytearray(data_offset + len(payload))
    buf[0:4] = b"PACK"
    struct.pack_into(">HH", buf, 4, 8, 0x70)
    struct.pack_into(">I", buf, 0x18, 0x70)
    struct.pack_into(">I", buf, 0x1C, data_offset - 0x70)
    struct.pack_into(">I", buf, 0x28, data_offset)
    struct.pack_into(">I", buf, 0x2C, len(payload))
    struct.pack_into(">QQQ", buf, 0x70, nseg, nchunk, 0)
    struct.pack_into(">7I", buf, 0x88, 0, len(payload), 0, 0, 0, 0, nchunk)
    table = 0x88 + 28
    struct.pack_into(">4I", buf, table, 0x01100000, 256, 0, 0)
    struct.pack_into(">4I", buf, table + 16, 0x03100000, 0x70, 128, 0)
    struct.pack_into(">4I", buf, table + 32, 0x0D800000, 8, 256, 0)
    buf[data_offset : data_offset + 256] = dummy
    buf[data_offset + 128 : data_offset + 128 + 0x70] = desc
    buf[data_offset + 256 :] = heap
    data = bytes(buf)
    parse_xpp(data)
    rgba = bytes([255, 0, 255, 255] * 16)
    packed = pack_replacements(data, {0: (4, 4, rgba)}, allow_resize=False)
    recs = read_records(packed, parse_xpp(packed))
    assert recs[0].width == 4
    ok, tot = verify_layout(recs)
    assert tot == 0 or ok == tot


def test_growing_heap_keeps_preheap_bytes():
    marker = b"MESHREC" + bytes(249)
    desc = _desc(data_addr=0x2000)
    heap = bytes(8)
    data = _pack_file([(0x01100000, marker), (0x03100000, desc), (0x0D800000, heap)])
    pkg = parse_xpp(data)
    obj = next(c for c in pkg.chunks if c.type_tag == 0x01100000)
    before = data[pkg.data_offset + obj.offset : pkg.data_offset + obj.offset + 7]
    assert before == b"MESHREC"
    rgba = bytes([10, 20, 30, 255] * 64)  # 8x8 grows the heap
    packed = pack_replacements(data, {0: (8, 8, rgba)}, allow_resize=True)
    again = parse_xpp(packed)
    obj2 = next(c for c in again.chunks if c.type_tag == 0x01100000)
    assert obj2.offset == obj.offset
    after = packed[again.data_offset + obj2.offset : again.data_offset + obj2.offset + 7]
    assert after == b"MESHREC"
