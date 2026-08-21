"""Pack round-trip on a synthetic XPP. No retail bytes required."""

from __future__ import annotations

import struct
from pathlib import Path

from infamous_xpp_textures.decode import extract_package
from infamous_xpp_textures.derive import derive_scaled
from infamous_xpp_textures.encode import encode_mip_chain
from infamous_xpp_textures.heap import read_records, verify_layout
from infamous_xpp_textures.pack import pack_chains, pack_replacements
from infamous_xpp_textures.pngio import read_png, write_png
from infamous_xpp_textures.xpp import parse_xpp

from test_synthetic import _minimal_xpp


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
