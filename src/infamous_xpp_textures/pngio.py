"""Minimal PNG read/write. 8-bit RGB/RGBA only."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    """Encode one deterministic non-interlaced RGBA8 PNG in memory."""

    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
        or len(rgba) != width * height * 4
    ):
        raise ValueError("PNG dimensions and RGBA byte count do not reconcile")
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride : (y + 1) * stride]

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def write_png(path: Path, width: int, height: int, rgba: bytes) -> None:
    png = encode_png(width, height, rgba)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def read_png(path: Path) -> tuple[int, int, bytearray]:
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG")
    pos = 8
    width = height = bit_depth = color_type = None
    idat = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        tag = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, bit_depth, color_type, comp, filt, inter = struct.unpack(
                ">IIBBBBB", payload
            )
            if bit_depth != 8 or color_type not in (2, 6) or inter != 0 or comp != 0:
                raise ValueError(f"{path}: need 8-bit RGB/RGBA non-interlaced PNG")
        elif tag == b"IDAT":
            idat.extend(payload)
        elif tag == b"IEND":
            break
    if width is None:
        raise ValueError(f"{path}: missing IHDR")
    raw = zlib.decompress(bytes(idat))
    bpp = 4 if color_type == 6 else 3
    stride = width * bpp
    rows: list[bytearray] = []
    src = 0
    prev = bytearray(stride)
    for _ in range(height):
        ftype = raw[src]
        src += 1
        row = bytearray(raw[src : src + stride])
        src += stride
        _paeth_unfilter(ftype, row, prev, bpp)
        prev = row
        rows.append(row)
    rgba = bytearray(width * height * 4)
    for y, row in enumerate(rows):
        for x in range(width):
            o = (y * width + x) * 4
            if bpp == 4:
                rgba[o : o + 4] = row[x * 4 : x * 4 + 4]
            else:
                rgba[o : o + 3] = row[x * 3 : x * 3 + 3]
                rgba[o + 3] = 255
    return width, height, rgba


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _paeth_unfilter(ftype: int, row: bytearray, prev: bytearray, bpp: int) -> None:
    for i, v in enumerate(row):
        left = row[i - bpp] if i >= bpp else 0
        up = prev[i]
        ul = prev[i - bpp] if i >= bpp else 0
        if ftype == 0:
            continue
        if ftype == 1:
            row[i] = (v + left) & 255
        elif ftype == 2:
            row[i] = (v + up) & 255
        elif ftype == 3:
            row[i] = (v + (left + up) // 2) & 255
        elif ftype == 4:
            row[i] = (v + _paeth(left, up, ul)) & 255
        else:
            raise ValueError(f"unsupported PNG filter {ftype}")


def scale_nearest(rgba: bytes, width: int, height: int, scale: int) -> tuple[int, int, bytearray]:
    if scale < 1:
        raise ValueError("scale must be >= 1")
    nw, nh = width * scale, height * scale
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        sy = y // scale
        for x in range(nw):
            sx = x // scale
            si = (sy * width + sx) * 4
            di = (y * nw + x) * 4
            out[di : di + 4] = rgba[si : si + 4]
    return nw, nh, out
