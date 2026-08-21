"""Synthetic PACK-v8 fixture. No retail bytes."""

from __future__ import annotations

import struct

from infamous_xpp_textures.decode import decode_dxt, extract_package, write_png
from infamous_xpp_textures.heap import align_up, read_records, verify_layout
from infamous_xpp_textures.xpp import parse_xpp


def _minimal_xpp(*, width=4, height=4, mips=1, fmt=0x86, data_addr=0x70, extra=b"") -> bytes:
    desc = bytearray(0x70)
    struct.pack_into(">III", desc, 0x24, width, height, mips)
    struct.pack_into(">I", desc, 0x40, data_addr)
    struct.pack_into(">I", desc, 0x44, (mips << 16) | (fmt << 8))
    desc[0x46] = fmt
    struct.pack_into(">I", desc, 0x58, (width << 16) | height)

    texel = extra or bytes(8)  # one DXT1 4x4 block
    payload = bytes(desc) + texel
    nseg, nchunk, nfix = 1, 2, 0
    data_offset = 0x88 + nseg * 28 + nchunk * 16 + nfix * 20
    data_size = len(payload)
    file_size = data_offset + data_size

    buf = bytearray(file_size)
    buf[0:4] = b"PACK"
    struct.pack_into(">HH", buf, 4, 8, 0x70)
    struct.pack_into(">I", buf, 0x18, 0x70)  # metadata_offset
    struct.pack_into(">I", buf, 0x1C, data_offset - 0x70)  # metadata_size
    struct.pack_into(">I", buf, 0x28, data_offset)
    struct.pack_into(">I", buf, 0x2C, data_size)
    struct.pack_into(">QQQ", buf, 0x70, nseg, nchunk, nfix)
    # segment: type, size, offset, r0, r1, first_chunk, chunk_count
    struct.pack_into(">7I", buf, 0x88, 0, data_size, 0, 0, 0, 0, 2)
    # chunks
    struct.pack_into(">4I", buf, 0x88 + 28, 0x03100000, 0x70, 0, 0)
    struct.pack_into(">4I", buf, 0x88 + 28 + 16, 0x0D800000, len(texel), 0x70, 0)
    buf[data_offset:] = payload
    return bytes(buf)


def test_parse_and_heap_offset():
    data = _minimal_xpp()
    pkg = parse_xpp(data)
    recs = read_records(data, pkg)
    assert len(recs) == 1
    assert recs[0].width == 4 and recs[0].height == 4
    assert recs[0].format == 0x86
    assert recs[0].heap_offset == 0
    assert recs[0].faces == 1


def test_layout_two_padded_chains():
    # two 4x4 DXT1 records; second address is +128 (align_up(8) == 128)
    desc2_addr = 0xE0 + 128
    # build by hand: two descriptors + 128+8 texel
    from infamous_xpp_textures.heap import chain_size

    assert chain_size(0x86, 4, 4, 1) == 8
    assert align_up(8) == 128

    d0 = bytearray(0x70)
    struct.pack_into(">III", d0, 0x24, 4, 4, 1)
    struct.pack_into(">I", d0, 0x40, 0xE0)
    struct.pack_into(">I", d0, 0x44, 0x00018600)
    d1 = bytearray(0x70)
    struct.pack_into(">III", d1, 0x24, 4, 4, 1)
    struct.pack_into(">I", d1, 0x40, desc2_addr)
    struct.pack_into(">I", d1, 0x44, 0x00018600)
    texel = bytes(128 + 8)
    payload = bytes(d0) + bytes(d1) + texel
    nseg, nchunk, nfix = 1, 2, 0
    data_offset = 0x88 + 28 + 32
    buf = bytearray(data_offset + len(payload))
    buf[0:4] = b"PACK"
    struct.pack_into(">HH", buf, 4, 8, 0x70)
    struct.pack_into(">I", buf, 0x18, 0x70)
    struct.pack_into(">I", buf, 0x1C, data_offset - 0x70)
    struct.pack_into(">I", buf, 0x28, data_offset)
    struct.pack_into(">I", buf, 0x2C, len(payload))
    struct.pack_into(">QQQ", buf, 0x70, nseg, nchunk, nfix)
    struct.pack_into(">7I", buf, 0x88, 0, len(payload), 0, 0, 0, 0, 2)
    struct.pack_into(">4I", buf, 0x88 + 28, 0x03100000, 0xE0, 0, 0)
    struct.pack_into(">4I", buf, 0x88 + 28 + 16, 0x0D800000, len(texel), 0xE0, 0)
    buf[data_offset:] = payload
    data = bytes(buf)
    pkg = parse_xpp(data)
    recs = read_records(data, pkg)
    assert len(recs) == 2
    ok, tot = verify_layout(recs)
    assert tot == 1 and ok == 1
    assert recs[0].heap_offset == 0
    assert recs[1].heap_offset == 128


def test_extract_writes_png(tmp_path):
    data = _minimal_xpp()
    found, written = extract_package(data, "fix", tmp_path)
    assert found == 1 and written == 1
    png = tmp_path / "fix.0.mip0.png"
    assert png.is_file()
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_dxt1_decodes():
    # opaque white-ish DXT1 block
    blk = struct.pack("<HHI", 0xFFFF, 0x0000, 0)
    rgba = decode_dxt(blk, 4, 4, 0x86)
    assert len(rgba) == 4 * 4 * 4
    assert rgba[0] == 255 and rgba[3] == 255
