"""Remaster PBR maps derived from an albedo. Not extracted from the game.

Infamous 1 stores DXT color (and sometimes a bump/spec). It does not store
metalness, roughness, or tangent-space normals. These maps are invented so a
modern viewer can light the mesh. They are labeled derived, not retail.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class DerivedPbr:
    width: int
    height: int
    albedo: bytes
    normal: bytes
    orm: bytes  # R=AO G=roughness B=metalness, glTF-friendly


def _luma(r: int, g: int, b: int) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def derive_pbr(width: int, height: int, rgba: bytes, *, strength: float = 0.7) -> DerivedPbr:
    if width < 1 or height < 1:
        raise ValueError("empty image")
    if len(rgba) < width * height * 4:
        raise ValueError("rgba short")
    pix = memoryview(rgba)
    luma = [0.0] * (width * height)
    for i in range(width * height):
        o = i * 4
        luma[i] = _luma(pix[o], pix[o + 1], pix[o + 2])

    normal = bytearray(width * height * 4)
    orm = bytearray(width * height * 4)
    inv = 1.0 / max(strength, 0.01)
    for y in range(height):
        yu = 0 if y == 0 else y - 1
        yd = height - 1 if y == height - 1 else y + 1
        for x in range(width):
            xl = 0 if x == 0 else x - 1
            xr = width - 1 if x == width - 1 else x + 1
            i = y * width + x
            dx = (
                luma[yu * width + xr]
                + 2.0 * luma[y * width + xr]
                + luma[yd * width + xr]
                - luma[yu * width + xl]
                - 2.0 * luma[y * width + xl]
                - luma[yd * width + xl]
            ) * inv
            dy = (
                luma[yd * width + xl]
                + 2.0 * luma[yd * width + x]
                + luma[yd * width + xr]
                - luma[yu * width + xl]
                - 2.0 * luma[yu * width + x]
                - luma[yu * width + xr]
            ) * inv
            nx, ny, nz = -dx / 255.0, -dy / 255.0, 1.0
            length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            nx, ny, nz = nx / length, ny / length, nz / length
            o = i * 4
            normal[o] = int(max(0, min(255, (nx * 0.5 + 0.5) * 255.0 + 0.5)))
            normal[o + 1] = int(max(0, min(255, (ny * 0.5 + 0.5) * 255.0 + 0.5)))
            normal[o + 2] = int(max(0, min(255, (nz * 0.5 + 0.5) * 255.0 + 0.5)))
            normal[o + 3] = 255

            r, g, b = pix[o], pix[o + 1], pix[o + 2]
            lo = min(r, g, b)
            hi = max(r, g, b)
            sat = hi - lo
            yv = luma[i]
            metal = 0.0
            # Near-white low-sat is painted body (police/ambulance), not chrome.
            # Bare mid-gray metal may pick up a little metalness.
            if yv >= 190:
                metal = 0.0
            elif 100.0 <= yv <= 175.0 and sat < 30:
                metal = min(0.45, (1.0 - sat / 30.0) * ((yv - 100.0) / 75.0))
            detail = min(1.0, (abs(dx) + abs(dy)) / 80.0)
            # Vehicle paint is dielectric + clearcoat, not chrome. Keep metal
            # for actual bright metal; paint stays mid-gloss so lighting reads.
            rough = 0.30 + 0.22 * detail - 0.22 * metal
            if yv < 28:
                rough = min(0.72, rough + 0.10)
            rough = max(0.16, min(0.78, rough))
            ao = 0.62 + 0.38 * (yv / 255.0)
            orm[o] = int(ao * 255.0 + 0.5)
            orm[o + 1] = int(rough * 255.0 + 0.5)
            orm[o + 2] = int(metal * 255.0 + 0.5)
            orm[o + 3] = 255

    return DerivedPbr(width, height, bytes(rgba[: width * height * 4]), bytes(normal), bytes(orm))
