"""Encode RGBA8 to the RSX formats this tool decodes."""

from __future__ import annotations

from .decode import (
    GCM_A8R8G8B8,
    GCM_DXT1,
    GCM_DXT3,
    GCM_DXT5,
    GCM_HILO8,
    GCM_R5G6B5,
    GCM_R6G5B5,
    level_dims,
    level_size,
)
from .heap import align_up, chain_size


def _rgb565_pack(r: int, g: int, b: int) -> int:
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _block_colors(rgba: bytes, width: int, height: int, ox: int, oy: int) -> list[tuple[int, int, int, int]]:
    pix = []
    for y in range(4):
        for x in range(4):
            px, py = ox + x, oy + y
            if px >= width or py >= height:
                pix.append((0, 0, 0, 0))
                continue
            o = (py * width + px) * 4
            pix.append((rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]))
    return pix


def _encode_dxt1_block(pix: list[tuple[int, int, int, int]], punchthrough: bool) -> bytes:
    opaque = [p for p in pix if p[3] > 16]
    src = opaque or pix
    c0 = min(src, key=lambda p: p[0] + p[1] + p[2])
    c1 = max(src, key=lambda p: p[0] + p[1] + p[2])
    e0 = _rgb565_pack(*c0[:3])
    e1 = _rgb565_pack(*c1[:3])
    if punchthrough and any(p[3] <= 16 for p in pix):
        if e0 > e1:
            e0, e1 = e1, e0
            c0, c1 = c1, c0
        pal = [
            c0[:3],
            c1[:3],
            ((c0[0] + c1[0]) // 2, (c0[1] + c1[1]) // 2, (c0[2] + c1[2]) // 2),
            (0, 0, 0),
        ]
    else:
        if e0 < e1:
            e0, e1 = e1, e0
            c0, c1 = c1, c0
        if e0 == e1:
            e0 = (e0 + 1) & 0xFFFF
        pal = [
            c0[:3],
            c1[:3],
            ((2 * c0[0] + c1[0]) // 3, (2 * c0[1] + c1[1]) // 3, (2 * c0[2] + c1[2]) // 3),
            ((c0[0] + 2 * c1[0]) // 3, (c0[1] + 2 * c1[1]) // 3, (c0[2] + 2 * c1[2]) // 3),
        ]
    bits = 0
    for i, p in enumerate(pix):
        if punchthrough and p[3] <= 16 and e0 <= e1:
            idx = 3
        else:
            idx = min(
                range(4),
                key=lambda k: (p[0] - pal[k][0]) ** 2
                + (p[1] - pal[k][1]) ** 2
                + (p[2] - pal[k][2]) ** 2,
            )
        bits |= idx << (2 * i)
    return e0.to_bytes(2, "little") + e1.to_bytes(2, "little") + bits.to_bytes(4, "little")


def _encode_dxt5_alpha(pix: list[tuple[int, int, int, int]]) -> bytes:
    alphas = [p[3] for p in pix]
    a0, a1 = max(alphas), min(alphas)
    if a0 == a1:
        a0 = min(255, a0 + 1)
    if a0 < a1:
        a0, a1 = a1, a0
    table = [a0, a1] + [((6 - i) * a0 + (i + 1) * a1) // 7 for i in range(6)]
    bits = 0
    for i, a in enumerate(alphas):
        idx = min(range(8), key=lambda k: abs(a - table[k]))
        bits |= idx << (3 * i)
    return bytes([a0, a1]) + bits.to_bytes(6, "little")


def encode_dxt(rgba: bytes, width: int, height: int, fmt: int) -> bytes:
    out = bytearray()
    punch = fmt == GCM_DXT1
    bw, bh = (width + 3) // 4, (height + 3) // 4
    for by in range(bh):
        for bx in range(bw):
            pix = _block_colors(rgba, width, height, bx * 4, by * 4)
            color = _encode_dxt1_block(pix, punch)
            if fmt == GCM_DXT1:
                out.extend(color)
            elif fmt == GCM_DXT5:
                out.extend(_encode_dxt5_alpha(pix) + color)
            else:  # DXT3
                alpha = 0
                for i, p in enumerate(pix):
                    alpha |= (p[3] >> 4) << (4 * i)
                out.extend(alpha.to_bytes(8, "little") + color)
    return bytes(out)


def encode_linear(rgba: bytes, width: int, height: int, fmt: int) -> bytes:
    n = width * height
    if fmt == GCM_A8R8G8B8:
        out = bytearray(n * 4)
        for i in range(n):
            r, g, b, a = rgba[i * 4 : i * 4 + 4]
            out[i * 4 : i * 4 + 4] = bytes((a, r, g, b))
        return bytes(out)
    if fmt == GCM_R5G6B5:
        out = bytearray()
        for i in range(n):
            r, g, b = rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2]
            out.extend(_rgb565_pack(r, g, b).to_bytes(2, "big"))
        return bytes(out)
    if fmt == GCM_R6G5B5:
        out = bytearray()
        for i in range(n):
            r, g, b = rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2]
            word = ((r >> 2) << 10) | ((g >> 3) << 5) | (b >> 3)
            out.extend(word.to_bytes(2, "big"))
        return bytes(out)
    if fmt == GCM_HILO8:
        out = bytearray()
        for i in range(n):
            out.extend((rgba[i * 4], rgba[i * 4 + 1]))
        return bytes(out)
    raise ValueError(f"unsupported encode format 0x{fmt:02x}")


def box_mip(rgba: bytes, width: int, height: int) -> tuple[int, int, bytearray]:
    nw, nh = max(1, width // 2), max(1, height // 2)
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        for x in range(nw):
            acc = [0, 0, 0, 0]
            count = 0
            for oy in (0, 1):
                for ox in (0, 1):
                    sx, sy = min(width - 1, x * 2 + ox), min(height - 1, y * 2 + oy)
                    o = (sy * width + sx) * 4
                    for c in range(4):
                        acc[c] += rgba[o + c]
                    count += 1
            d = (y * nw + x) * 4
            for c in range(4):
                out[d + c] = acc[c] // count
    return nw, nh, out


def encode_mip_chain(rgba: bytes, width: int, height: int, fmt: int, mips: int) -> bytes:
    out = bytearray()
    w, h, surf = width, height, bytearray(rgba)
    for level in range(mips):
        lw, lh = level_dims(width, height, level)
        if (w, h) != (lw, lh):
            raise ValueError("mip chain desynced")
        if fmt in (GCM_DXT1, GCM_DXT3, GCM_DXT5):
            block = encode_dxt(surf, w, h, fmt)
        else:
            block = encode_linear(surf, w, h, fmt)
        if len(block) != level_size(fmt, width, height, level):
            raise ValueError(
                f"encoded level {level} is {len(block)} bytes, expected "
                f"{level_size(fmt, width, height, level)}"
            )
        out.extend(block)
        if level + 1 < mips:
            w, h, surf = box_mip(surf, w, h)
    if len(out) != chain_size(fmt, width, height, mips):
        raise ValueError("encoded chain length mismatch")
    return bytes(out)


def padded_chain(chain: bytes, faces: int) -> bytes:
    one = chain + b"\x00" * (align_up(len(chain)) - len(chain))
    return one * faces
