"""Pack round-trip on a synthetic XPP. No retail bytes required."""

from __future__ import annotations

from pathlib import Path

from infamous_xpp_textures.decode import extract_package
from infamous_xpp_textures.encode import encode_mip_chain
from infamous_xpp_textures.heap import read_records, verify_layout
from infamous_xpp_textures.pack import pack_replacements
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
    ok, tot = verify_layout(recs)
    assert tot == 0 or ok == tot
