"""Decode RSX surfaces from an iF1 XPP to RGBA8 and write PNG."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from . import heap as HL
from .xpp import TEXDESC_CHUNK, TEXEL_CHUNK, XppFile, parse_xpp

GCM_R5G6B5 = 0x84
GCM_A8R8G8B8 = 0x85
GCM_DXT1 = 0x86
GCM_DXT3 = 0x87
GCM_DXT5 = 0x88
GCM_R6G5B5 = 0x8F
GCM_HILO8 = 0x95

GCM_NAMES = {
    GCM_R5G6B5: "R5G6B5",
    GCM_A8R8G8B8: "X8R8G8B8/A8R8G8B8",
    GCM_DXT1: "DXT1",
    GCM_DXT3: "DXT3",
    GCM_DXT5: "DXT5",
    GCM_R6G5B5: "R6G5B5",
    GCM_HILO8: "HILO8",
}

FMT_LN = 0x20
FMT_BASE_MASK = 0x9F
BLOCK_FORMATS = {GCM_DXT1: 8, GCM_DXT3: 16, GCM_DXT5: 16}


def level_dims(width: int, height: int, level: int) -> tuple[int, int]:
    return max(1, width >> level), max(1, height >> level)


def bytes_per_pixel(fmt: int) -> int:
    if fmt in (GCM_R5G6B5, GCM_R6G5B5, GCM_HILO8):
        return 2
    if fmt == GCM_A8R8G8B8:
        return 4
    raise ValueError(f"unsupported uncompressed format 0x{fmt:02x}")


def level_size(fmt: int, width: int, height: int, level: int) -> int:
    w, h = level_dims(width, height, level)
    if fmt in BLOCK_FORMATS:
        return ((w + 3) // 4) * ((h + 3) // 4) * BLOCK_FORMATS[fmt]
    return w * h * bytes_per_pixel(fmt)


def _rgb565(v: int) -> tuple[int, int, int]:
    r = (v >> 11) & 0x1F
    g = (v >> 5) & 0x3F
    b = v & 0x1F
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _dxt_colour_block(
    block: bytes, out: bytearray, ox: int, oy: int, width: int, height: int, punchthrough: bool
) -> None:
    c0, c1, bits = struct.unpack_from("<HHI", block, 0)
    r0, g0, b0 = _rgb565(c0)
    r1, g1, b1 = _rgb565(c1)
    if c0 > c1 or not punchthrough:
        pal = [
            (r0, g0, b0, 255),
            (r1, g1, b1, 255),
            ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255),
            ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255),
        ]
    else:
        pal = [
            (r0, g0, b0, 255),
            (r1, g1, b1, 255),
            ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255),
            (0, 0, 0, 0),
        ]
    for y in range(4):
        py = oy + y
        if py >= height:
            break
        row = py * width * 4
        for x in range(4):
            px = ox + x
            if px >= width:
                continue
            idx = (bits >> (2 * (4 * y + x))) & 3
            r, g, b, a = pal[idx]
            o = row + px * 4
            out[o] = r
            out[o + 1] = g
            out[o + 2] = b
            out[o + 3] = a


def decode_dxt(data: bytes, width: int, height: int, fmt: int) -> bytearray:
    out = bytearray(width * height * 4)
    bw, bh = (width + 3) // 4, (height + 3) // 4
    stride = BLOCK_FORMATS[fmt]
    p = 0
    for by in range(bh):
        for bx in range(bw):
            blk = data[p : p + stride]
            p += stride
            if len(blk) < stride:
                return out
            ox, oy = bx * 4, by * 4
            if fmt == GCM_DXT1:
                _dxt_colour_block(blk, out, ox, oy, width, height, True)
            else:
                _dxt_colour_block(blk[8:], out, ox, oy, width, height, False)
                if fmt == GCM_DXT3:
                    alpha = int.from_bytes(blk[:8], "little")
                    for y in range(4):
                        for x in range(4):
                            px, py = ox + x, oy + y
                            if px >= width or py >= height:
                                continue
                            a = (alpha >> (4 * (4 * y + x))) & 0xF
                            out[(py * width + px) * 4 + 3] = a * 17
                else:
                    a0, a1 = blk[0], blk[1]
                    if a0 > a1:
                        at = [a0, a1] + [((6 - i) * a0 + (i + 1) * a1) // 7 for i in range(6)]
                    else:
                        at = [a0, a1] + [((4 - i) * a0 + (i + 1) * a1) // 5 for i in range(4)] + [
                            0,
                            255,
                        ]
                    abits = int.from_bytes(blk[2:8], "little")
                    for y in range(4):
                        for x in range(4):
                            px, py = ox + x, oy + y
                            if px >= width or py >= height:
                                continue
                            out[(py * width + px) * 4 + 3] = at[(abits >> (3 * (4 * y + x))) & 7]
    return out


def decode_linear(data: bytes, width: int, height: int, fmt: int) -> bytearray:
    out = bytearray(width * height * 4)
    n = width * height
    if fmt == GCM_A8R8G8B8:
        for i in range(n):
            a, r, g, b = data[i * 4 : i * 4 + 4]
            o = i * 4
            out[o], out[o + 1], out[o + 2], out[o + 3] = r, g, b, a
    elif fmt == GCM_R5G6B5:
        for i in range(n):
            r, g, b = _rgb565(struct.unpack_from(">H", data, i * 2)[0])
            o = i * 4
            out[o], out[o + 1], out[o + 2], out[o + 3] = r, g, b, 255
    elif fmt == GCM_R6G5B5:
        for i in range(n):
            v = struct.unpack_from(">H", data, i * 2)[0]
            r, g, b = (v >> 10) & 0x3F, (v >> 5) & 0x1F, v & 0x1F
            o = i * 4
            out[o] = (r << 2) | (r >> 4)
            out[o + 1] = (g << 3) | (g >> 2)
            out[o + 2] = (b << 3) | (b >> 2)
            out[o + 3] = 255
    elif fmt == GCM_HILO8:
        for i in range(n):
            hi, lo = data[i * 2], data[i * 2 + 1]
            o = i * 4
            out[o], out[o + 1], out[o + 2], out[o + 3] = hi, lo, 0, 255
    else:
        raise ValueError(f"unsupported format 0x{fmt:02x}")
    return out


def is_coverage_only(surf: bytes) -> bool:
    if len(surf) < 4:
        return False
    mv = memoryview(surf)
    return not any(mv[0::4]) and not any(mv[1::4]) and not any(mv[2::4]) and any(mv[3::4])


def render_coverage(surf: bytes, width: int, height: int) -> bytearray:
    out = bytearray(width * height * 4)
    for i in range(width * height):
        c = surf[i * 4 + 3]
        o = i * 4
        out[o] = out[o + 1] = out[o + 2] = c
        out[o + 3] = 255
    return out


def unswizzle(data: bytes, width: int, height: int, bpp: int) -> bytes:
    if width & (width - 1) or height & (height - 1):
        return data
    out = bytearray(len(data))
    log_w, log_h = width.bit_length() - 1, height.bit_length() - 1
    for y in range(height):
        for x in range(width):
            m = 0
            bit = 0
            xb, yb = x, y
            for i in range(max(log_w, log_h)):
                if i < log_w:
                    m |= (xb & 1) << bit
                    bit += 1
                    xb >>= 1
                if i < log_h:
                    m |= (yb & 1) << bit
                    bit += 1
                    yb >>= 1
            src = m * bpp
            dst = (y * width + x) * bpp
            out[dst : dst + bpp] = data[src : src + bpp]
    return bytes(out)


def write_png(path: Path, width: int, height: int, rgba: bytes) -> None:
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

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


class SurfaceRecord:
    def __init__(self, raw: bytes, heap_offset: int, faces: int, reason: str):
        self.raw = raw
        self.width = struct.unpack_from(">I", raw, 0x24)[0]
        self.height = struct.unpack_from(">I", raw, 0x28)[0]
        self.mips = struct.unpack_from(">I", raw, 0x2C)[0]
        self.format_word = struct.unpack_from(">I", raw, 0x44)[0]
        self.format_byte = raw[0x46]
        self.heap_offset = heap_offset
        self.faces = faces
        self.reason = reason

    @property
    def base_format(self) -> int:
        return self.format_byte & FMT_BASE_MASK & ~FMT_LN

    @property
    def linear(self) -> bool:
        return bool(self.format_byte & FMT_LN) or self.base_format in BLOCK_FORMATS

    def describe(self) -> str:
        f = self.base_format
        return (
            f"{self.width}x{self.height} mips={self.mips} "
            f"format=0x{self.format_byte:02x} ({GCM_NAMES.get(f, 'UNKNOWN')})"
        )


def iter_textures(data: bytes, xpp: XppFile):
    texels = HL.heap_bytes(data, xpp)
    if not texels:
        return
    for idx, raw, hrec, reason in HL.read_all_descriptors(data, xpp):
        rec = SurfaceRecord(
            raw,
            hrec.heap_offset if hrec is not None else -1,
            hrec.faces if hrec is not None else 1,
            reason,
        )
        yield idx, rec, texels


def decode_level(rec: SurfaceRecord, texels: bytes, level: int, base: int):
    fmt = rec.base_format
    off = base + sum(level_size(fmt, rec.width, rec.height, i) for i in range(level))
    w, h = level_dims(rec.width, rec.height, level)
    n = level_size(fmt, rec.width, rec.height, level)
    surf = texels[off : off + n]
    if len(surf) < n:
        raise ValueError(
            f"payload too short for level {level}: need {n} bytes at +{off}, have {len(surf)}"
        )
    if fmt in BLOCK_FORMATS:
        return w, h, decode_dxt(surf, w, h, fmt), "dxt"
    if not rec.linear:
        surf = unswizzle(surf, w, h, bytes_per_pixel(fmt))
    rgba = decode_linear(surf, w, h, fmt)
    note = "uncompressed"
    if fmt == GCM_A8R8G8B8 and is_coverage_only(surf):
        rgba = render_coverage(surf, w, h)
        note = "uncompressed,coverage-only"
    return w, h, rgba, note


def load_xpp_bytes(*, xpp: Path | None = None, psarc: Path | None = None, entry: str | None = None) -> tuple[bytes, str]:
    if xpp is not None:
        data = Path(xpp).read_bytes()
        return data, Path(xpp).stem
    if psarc is None or not entry:
        raise ValueError("provide --xpp, or --psarc and --entry")
    from .psarc import extract_entry

    data = extract_entry(psarc, entry)
    stem = entry.strip("/").replace("/", "_")
    if stem.endswith(".xpp"):
        stem = stem[:-4]
    return data, stem


def extract_package(
    data: bytes,
    stem: str,
    outdir: Path,
    *,
    level: int = 0,
    index: int | None = None,
    list_only: bool = False,
    max_count: int | None = None,
) -> tuple[int, int]:
    xpp = parse_xpp(data, len(data))
    found = written = 0
    for i, rec, texels in iter_textures(data, xpp):
        if index is not None and i != index:
            continue
        if max_count is not None and found >= max_count:
            break
        found += 1
        cube = "  CUBEMAP(6 faces)" if rec.faces == 6 else ""
        print(f"[{i}] {rec.describe()}  heap+{rec.heap_offset:,}{cube}")
        if list_only:
            continue
        if rec.reason:
            print(f"     SKIP: {rec.reason}")
            continue
        w, h, rgba, note = decode_level(rec, texels, level, rec.heap_offset)
        out = outdir / f"{stem}.{i}.mip{level}.png"
        write_png(out, w, h, bytes(rgba))
        written += 1
        print(f"     wrote {out}  ({w}x{h}) {note}")
    if found == 0:
        print(
            f"no texture chunks found "
            f"(need a 0x{TEXDESC_CHUNK:08x} record and a 0x{TEXEL_CHUNK:08x} heap)"
        )
    else:
        print(f"\n{found} texture(s) described, {written} PNG(s) written")
    return found, written
