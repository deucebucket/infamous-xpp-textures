"""Pack round-trip on a synthetic XPP. No retail bytes required."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from infamous_xpp_textures.decode import extract_package
from infamous_xpp_textures.derive import derive_scaled
from infamous_xpp_textures.encode import encode_mip_chain
from infamous_xpp_textures.heap import HEAP_ALIGN, read_records, verify_layout
from infamous_xpp_textures.pack import PackError, _fit_replacement, pack_chains, pack_replacements, rebuild_xpp
from infamous_xpp_textures.pngio import read_png, write_png
from infamous_xpp_textures.xpp import parse_xpp

from test_synthetic import _minimal_xpp


def _desc(width=4, height=4, mips=1, fmt=0x86, data_addr=0xE0) -> bytes:
    desc = bytearray(0x70)
    struct.pack_into(">III", desc, 0x24, width, height, mips)
    struct.pack_into(">I", desc, 0x40, data_addr)
    desc[0x45] = mips
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
    assert recs[0].embedded_mips == 4
    ok, tot = verify_layout(recs)
    assert tot == 0 or ok == tot


def _xpp_with_link_tail() -> tuple[bytes, bytes]:
    desc = bytearray(0x70)
    struct.pack_into(">III", desc, 0x24, 4, 4, 1)
    struct.pack_into(">I", desc, 0x40, 0x70)
    struct.pack_into(">I", desc, 0x44, 0x00018600)
    struct.pack_into(">I", desc, 0x58, (4 << 16) | 4)
    texel = struct.pack("<HHI", 0xFFFF, 0, 0)
    tail = b"ILNKsynthetic-final-link-segmentEND \x00\x00\x00\x00"
    payload = bytes(desc) + texel + tail
    nseg, nchunk, nfix = 2, 3, 0
    data_offset = 0x88 + nseg * 28 + nchunk * 16
    out = bytearray(data_offset + len(payload))
    out[:4] = b"PACK"
    struct.pack_into(">HH", out, 4, 8, 0x70)
    struct.pack_into(">I", out, 0x18, 0x70)
    struct.pack_into(">I", out, 0x1C, data_offset - 0x70)
    struct.pack_into(">I", out, 0x28, data_offset)
    struct.pack_into(">I", out, 0x2C, len(payload))
    struct.pack_into(">QQQ", out, 0x70, nseg, nchunk, nfix)
    table = 0x88
    first_size = len(desc) + len(texel)
    struct.pack_into(">7I", out, table, 0xFF00, first_size, 0, 0, 0, 0, 2)
    struct.pack_into(">7I", out, table + 28, 0xFF02, len(tail), first_size, 0, 0, 2, 1)
    chunks = table + nseg * 28
    struct.pack_into(">4I", out, chunks, 0x03100000, len(desc), 0, 0)
    struct.pack_into(">4I", out, chunks + 16, 0x0D800000, len(texel), len(desc), 0)
    struct.pack_into(">4I", out, chunks + 32, 0x02040000, len(tail), first_size, 0)
    out[data_offset:] = payload
    return bytes(out), tail


def test_resize_uses_payload_relative_pointers_and_preserves_link_tail():
    data, tail = _xpp_with_link_tail()
    payload_base = struct.unpack_from(">I", data, 0x28)[0]
    assert payload_base != 0
    assert data[payload_base + 0x70 : payload_base + 0x78] != data[0x70:0x78]

    rgba = bytes([0, 255, 0, 255] * 64)
    expected_chain = encode_mip_chain(rgba, 8, 8, 0x86, 4)
    packed = pack_replacements(data, {0: (8, 8, rgba)}, allow_resize=True)
    pkg = parse_xpp(packed)
    rec = read_records(packed, pkg)[0]
    assert (rec.width, rec.height, rec.mips, rec.embedded_mips) == (8, 8, 4, 4)

    heap_chunk = next(chunk for chunk in pkg.chunks if chunk.type_tag == 0x0D800000)
    link_chunk = next(chunk for chunk in pkg.chunks if chunk.type_tag == 0x02040000)
    file_offset = pkg.data_offset + rec.data_addr
    assert rec.data_addr == heap_chunk.offset
    assert packed[file_offset : file_offset + len(expected_chain)] == expected_chain
    assert packed[pkg.data_offset + link_chunk.offset :] == tail
    assert pkg.segments[0].size == heap_chunk.offset + heap_chunk.size
    assert pkg.segments[1].offset == link_chunk.offset
    assert pkg.data_offset + pkg.data_size == len(packed)


def test_derive_2x_copies_exact_mip_suffix():
    retail, tail = _xpp_with_link_tail()
    source_chain = bytes(range(168))
    source = pack_chains(retail, {0: (16, 16, 3, source_chain)})
    result, changed, total = derive_scaled(retail, source, target_scale=2)
    assert (changed, total) == (1, 1)

    pkg = parse_xpp(result)
    rec = read_records(result, pkg)[0]
    assert (rec.width, rec.height, rec.mips, rec.embedded_mips) == (8, 8, 2, 2)
    expected = source_chain[128:]
    start = pkg.data_offset + rec.data_addr
    assert result[start : start + rec.chain_bytes] == expected
    link = next(chunk for chunk in pkg.chunks if chunk.type_tag == 0x02040000)
    assert result[pkg.data_offset + link.offset :] == tail


def test_last_chain_omits_pad_when_copying_other_texture():
    d0 = _desc(data_addr=0xE0)
    d1 = _desc(data_addr=0xE0 + HEAP_ALIGN)
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


def test_last_chain_missing_texels_is_refused_not_zero_filled():
    d0 = _desc(data_addr=0xE0)
    d1 = _desc(data_addr=0xE0 + HEAP_ALIGN)
    heap = bytes(HEAP_ALIGN + 4)  # 4 < chain 8, missing <= 128
    data = _pack_file([(0x03100000, d0 + d1), (0x0D800000, heap)])
    rgba = bytes([0, 255, 0, 255] * 16)
    with pytest.raises(PackError, match="data-pointer-outside-texel-heap"):
        pack_replacements(data, {0: (4, 4, rgba)}, allow_resize=False)


def test_cubemap_replacement_is_left_retail():
    d0 = bytearray(_desc(data_addr=0xE0))
    d1 = bytearray(_desc(data_addr=0xE0 + HEAP_ALIGN))
    d1[0x47] |= 0x04  # cubemap bit
    heap = bytes(HEAP_ALIGN + HEAP_ALIGN * 6)
    data = _pack_file([(0x03100000, bytes(d0) + bytes(d1)), (0x0D800000, heap)])
    recs = read_records(data, parse_xpp(data))
    assert recs[1].faces == 6
    rgba = bytes([255, 0, 0, 255] * 16)
    packed = pack_replacements(
        data, {0: (4, 4, rgba), 1: (4, 4, rgba)}, allow_resize=False,
        fit_replacements=True,
    )
    again = read_records(packed, parse_xpp(packed))
    assert len(again) == 2
    assert again[1].faces == 6
    ok, tot = verify_layout(again)
    assert tot == 1 and ok == 1


def test_oversize_4x_short_buffer_is_left_retail():
    d0 = _desc(data_addr=0xE0)
    d1 = _desc(data_addr=0xE0 + HEAP_ALIGN)
    heap = bytes(HEAP_ALIGN + 8)
    data = _pack_file([(0x03100000, d0 + d1), (0x0D800000, heap)])
    rgba = bytes([255, 0, 0, 255] * 16)
    packed = pack_replacements(
        data,
        {0: (4, 4, rgba), 1: (8192, 64, b"\x00" * 16)},
        allow_resize=True, fit_replacements=True,
    )
    again = read_records(packed, parse_xpp(packed))
    assert len(again) == 2
    assert again[1].width == 4 and again[1].height == 4
    ok, tot = verify_layout(again)
    assert tot == 1 and ok == 1


def test_oversize_4x_is_clamped_to_4096():
    d0 = _desc(data_addr=0xE0)
    d1 = _desc(data_addr=0xE0 + HEAP_ALIGN)
    heap = bytes(HEAP_ALIGN + 8)
    data = _pack_file([(0x03100000, d0 + d1), (0x0D800000, heap)])
    rgba = bytes([255, 0, 0, 255] * 16)
    huge = bytes(8192 * 64 * 4)
    packed = pack_replacements(
        data, {0: (4, 4, rgba), 1: (8192, 64, huge)}, allow_resize=True,
        fit_replacements=True,
    )
    again = {r.index: r for r in read_records(packed, parse_xpp(packed))}
    assert again[1].width == 4096 and again[1].height == 32
    ok, tot = verify_layout(list(again.values()))
    assert tot == 1 and ok == 1


def test_unused_extra_heap_is_kept():
    desc = _desc(data_addr=0x70)
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
    d0 = _desc(data_addr=0xE0)
    d1 = _desc(data_addr=0xE0 + 8)
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
    desc = _desc(data_addr=256)
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
    desc = _desc(data_addr=256 + 0x70)
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


@pytest.mark.parametrize("width,height,rgba", [
    (3, 4, bytes(48)), (0, 4, b""), (-1, 4, b""),
    (4, 4, bytes(63)), (4, 4, bytes(65)), (8192, 64, bytes(16)),
])
def test_invalid_images_refused_by_default_or_left_retail_when_fitting(width, height, rgba):
    data = _minimal_xpp()
    with pytest.raises(PackError, match="invalid"):
        pack_replacements(data, {0: (width, height, rgba)})
    assert pack_replacements(
        data, {0: (width, height, rgba)}, fit_replacements=True,
    ) == pack_chains(data, {})


@pytest.mark.parametrize("width,height", [(8192, 1), (1, 8192), (16384, 2)])
def test_fit_thin_images_repeated_halving_preserves_sample_pixels(width, height):
    rgba = bytes((i % 251 for i in range(width * height * 4)))
    w, h, fitted = _fit_replacement(width, height, rgba)
    assert (w, h) == ((4096, 1) if width > height else (1, 4096))
    factor = max(width, height) // 4096
    assert len(fitted) == w * h * 4
    for x, y in [(0, 0), (w - 1, h - 1)]:
        src = (y * factor * width + x * factor) * 4
        dst = (y * w + x) * 4
        assert fitted[dst:dst + 4] == rgba[src:src + 4]


def test_fit_resize_still_requires_permission():
    with pytest.raises(PackError, match="allow-resize"):
        pack_replacements(
            _minimal_xpp(), {0: (8192, 1, bytes(8192 * 4))},
            allow_resize=False, fit_replacements=True,
        )


def test_explicit_cubemap_refused_but_fitting_keeps_all_faces_and_padding():
    desc = bytearray(_desc(data_addr=0x70))
    desc[0x47] |= 4
    heap = bytes((i % 251 for i in range(HEAP_ALIGN * 5 + 8)))
    data = _pack_file([(0x03100000, bytes(desc)), (0x0D800000, heap)])
    with pytest.raises(PackError, match="cubemap"):
        pack_replacements(data, {0: (4, 4, bytes(64))})
    with pytest.raises(PackError, match="cubemap"):
        pack_chains(data, {0: (4, 4, 1, bytes(8))})
    assert pack_replacements(data, {0: (4, 4, bytes(64))}, fit_replacements=True) == data


def test_cubemap_missing_later_face_is_refused():
    desc = bytearray(_desc(data_addr=0x70))
    desc[0x47] |= 4
    data = _pack_file([(0x03100000, bytes(desc)), (0x0D800000, bytes(48))])
    with pytest.raises(PackError, match="allocation span is short"):
        pack_chains(data, {})


@pytest.mark.parametrize("used_second", [False, True])
@pytest.mark.parametrize("width", [4, 16])
def test_unused_heap_preserved_on_either_side_of_resized_primary(used_second, width):
    unused = b"OPAQUE-UNUSED-HEAP" * 3
    primary = bytes(range(128))
    addr = 0x70 + (len(unused) if used_second else 0)
    desc = _desc(width=16, height=16, data_addr=addr)
    heaps = [unused, primary] if used_second else [primary, unused]
    data = _pack_file([(0x03100000, desc)] + [(0x0D800000, h) for h in heaps])
    chain = bytes([213]) * (8 if width == 4 else 168)
    packed = pack_chains(data, {0: (width, width, 1 if width == 4 else 3, chain)})
    pkg = parse_xpp(packed)
    chunks = [c for c in pkg.chunks if c.type_tag == 0x0D800000]
    leftover = chunks[0 if used_second else 1]
    assert leftover.size == len(unused)
    assert packed[pkg.data_offset + leftover.offset:pkg.data_offset + leftover.offset + leftover.size] == unused
    rec = read_records(packed, pkg)[0]
    assert rec.data_addr == addr
    assert packed[pkg.data_offset + rec.data_addr:pkg.data_offset + rec.data_addr + len(chain)] == chain


def test_rebuild_itself_refuses_multiple_referenced_heaps():
    descs = [_desc(data_addr=0xE0), _desc(data_addr=0xE8)]
    data = _pack_file([(0x03100000, b"".join(descs)), (0x0D800000, bytes(8)), (0x0D800000, bytes(8))])
    with pytest.raises(PackError, match="texel-heap chunks"):
        rebuild_xpp(data, descs, bytes(8))


def test_retail_order_prefix_opaque_gap_and_suffix_survive_growth_and_shrink():
    prefix, gap, suffix = b"PREFIX!", b"opaque gap!", b"opaque suffix"
    # Table order deliberately opposes pointer order; resizing reverses size order.
    first = 0xE0 + len(prefix)
    second = first + HEAP_ALIGN + len(gap)
    descs = _desc(width=16, height=16, data_addr=second) + _desc(data_addr=first)
    original_last = bytes(range(128))
    heap = prefix + bytes(HEAP_ALIGN) + gap + original_last + suffix
    data = _pack_file([(0x03100000, descs), (0x0D800000, heap)])
    for replacement in [bytes(range(168)), bytes(8)]:
        large = len(replacement) == 168
        packed = pack_chains(data, {1: (16 if large else 4, 16 if large else 4, 3 if large else 1, replacement)})
        pkg = parse_xpp(packed)
        recs = read_records(packed, pkg)
        a, b = recs[1], recs[0]
        assert a.data_addr == first
        assert b.data_addr == a.data_addr + a.stride_bytes + len(gap)
        base = pkg.data_offset
        assert packed[base + 0xE0:base + first] == prefix
        assert packed[base + a.data_addr + a.stride_bytes:base + b.data_addr] == gap
        assert packed[base + b.data_addr:] == original_last + suffix
        assert (a.mips, a.embedded_mips) == ((3, 3) if large else (1, 1))
        data = packed


def test_encoded_8192_chain_remains_supported_without_image_fitting():
    chain = bytes(8192 // 4 * 8)
    packed = pack_chains(_minimal_xpp(), {0: (8192, 1, 1, chain)})
    rec = read_records(packed, parse_xpp(packed))[0]
    assert (rec.width, rec.height, rec.mips, rec.embedded_mips) == (8192, 1, 1, 1)


@pytest.mark.parametrize("replacement,match", [
    ((0, 4, 1, b""), "invalid dimensions"),
    ((3, 4, 1, bytes(8)), "invalid dimensions"),
    ((16384, 1, 1, b""), "invalid dimensions"),
    ((4, 4, 0, b""), "mip count"),
    ((4, 4, 4, bytes(32)), "mip count"),
    ((4, 4, 1, bytes(7)), "chain is"),
])
def test_encoded_chain_validation(replacement, match):
    with pytest.raises(PackError, match=match):
        pack_chains(_minimal_xpp(), {0: replacement})


@pytest.mark.parametrize("fit", [False, True])
def test_unknown_replacement_is_refused_even_in_fitting_mode(fit):
    with pytest.raises(PackError, match="unknown texture indices"):
        pack_replacements(_minimal_xpp(), {99: (4, 4, bytes(64))}, fit_replacements=fit)


def test_duplicate_pointers_and_overlapping_allocations_are_refused():
    for offset, error in [(0, "duplicate"), (8, "overlaps")]:
        descs = _desc(data_addr=0xE0) + _desc(data_addr=0xE0 + offset)
        data = _pack_file([(0x03100000, descs), (0x0D800000, bytes(256))])
        with pytest.raises(PackError, match=error):
            pack_chains(data, {})


def test_empty_segment_is_refused():
    data = bytearray(_minimal_xpp())
    # Insert a zero-sized, zero-chunk segment before the actual owning segment.
    data[0x88:0x88] = bytes(28)
    struct.pack_into(">Q", data, 0x70, 2)
    for offset in [0x1C, 0x28]:
        struct.pack_into(">I", data, offset, struct.unpack_from(">I", data, offset)[0] + 28)
    parse_xpp(data)
    with pytest.raises(PackError, match="empty segment"):
        pack_chains(bytes(data), {})


def test_no_descriptors_and_bad_descriptor_payload_are_refused():
    with pytest.raises(PackError, match="no texture descriptors"):
        pack_chains(_pack_file([(0x0D800000, bytes(8))]), {})
    data = _minimal_xpp()
    with pytest.raises(PackError, match="descriptor payload"):
        rebuild_xpp(data, [], bytes(8))


@pytest.mark.parametrize("offset,value,reason", [
    (0x46, 0x01, "unknown-format"), (0x45, 0, None),
])
def test_unknown_format_refused_and_embedded_mips_repaired(offset, value, reason):
    data = bytearray(_minimal_xpp())
    pkg = parse_xpp(data)
    data[pkg.data_offset + offset] = value
    if reason:
        with pytest.raises(PackError, match=reason):
            pack_chains(bytes(data), {})
    else:
        packed = pack_chains(bytes(data), {})
        rec = read_records(packed, parse_xpp(packed))[0]
        assert rec.mips == rec.embedded_mips == 1


def test_payload_relative_pointer_is_not_rebased_from_an_arbitrary_minimum():
    data = _pack_file([(0x03100000, _desc(data_addr=0x1000)), (0x0D800000, bytes(8))])
    with pytest.raises(PackError, match="data-pointer-outside-texel-heap"):
        pack_chains(data, {})


def test_resize_preserves_descriptor_format_flags_and_exact_final_link_segment():
    data, tail = _xpp_with_link_tail()
    original = parse_xpp(data)
    data = bytearray(data)
    # Retain LN/UN bits, non-mip high byte and non-cubemap low-byte flags.
    struct.pack_into(">I", data, original.data_offset + 0x44, 0xAB01E6A0)
    packed = pack_chains(bytes(data), {0: (16, 16, 3, bytes(168))})
    shrunk = pack_chains(packed, {0: (4, 4, 1, bytes(8))})
    for result, mips in [(packed, 3), (shrunk, 1)]:
        pkg = parse_xpp(result)
        rec = read_records(result, pkg)[0]
        assert rec.format_word == 0xAB00E6A0 | (mips << 16)
        assert rec.mips == rec.embedded_mips == mips
        segment = pkg.segments[-1]
        assert segment.type_tag == original.segments[-1].type_tag
        assert segment.size == len(tail)
        assert (segment.first_chunk, segment.chunk_count) == (2, 1)
        assert result[pkg.data_offset + segment.offset:] == tail
    assert parse_xpp(shrunk).data_size < parse_xpp(packed).data_size


def test_segment_partially_overlapping_heap_is_refused():
    data, _tail = _xpp_with_link_tail()
    data = bytearray(data)
    pkg = parse_xpp(data)
    struct.pack_into(">I", data, 0x88 + 4, pkg.segments[0].size - 4)
    struct.pack_into(">II", data, 0x88 + 28 + 4, pkg.segments[1].size + 4, pkg.segments[1].offset - 4)
    parse_xpp(data)
    with pytest.raises(PackError, match="segment 0 partially overlaps"):
        pack_chains(bytes(data), {})


@pytest.mark.parametrize("tag,allowed", [(0x01100000, False), (0x0C100000, True)])
def test_only_documented_overlay_chunk_may_overlap_heap(tag, allowed):
    data = bytearray(_pack_file([
        (0x03100000, _desc(data_addr=0x70)), (tag, b""), (0x0D800000, bytes(128)),
    ]))
    struct.pack_into(">I", data, 0x88 + 28 + 16 + 4, 64)
    parse_xpp(data)
    if allowed:
        assert pack_chains(bytes(data), {}) == bytes(data)
    else:
        with pytest.raises(PackError, match="chunk 1 partially overlaps"):
            pack_chains(bytes(data), {})
