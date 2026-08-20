"""Minimal GLB reader. Enough to compile positions/UVs/indices back into XPP."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass


class GlbReadError(ValueError):
    pass


@dataclass(frozen=True)
class GlbPrimitive:
    positions: list[tuple[float, float, float]]
    texcoords: list[tuple[float, float]]
    indices: list[int]
    extras: dict


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def parse_glb(data: bytes) -> list[GlbPrimitive]:
    if len(data) < 12 or _u32(data, 0) != 0x46546C67:
        raise GlbReadError("not a GLB")
    if _u32(data, 4) != 2:
        raise GlbReadError("need glTF 2")
    pos = 12
    json_blob = None
    bin_blob = b""
    while pos + 8 <= len(data):
        length = _u32(data, pos)
        tag = _u32(data, pos + 4)
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 8 + length
        if tag == 0x4E4F534A:
            json_blob = json.loads(chunk.decode("utf-8"))
        elif tag == 0x004E4942:
            bin_blob = chunk
    if json_blob is None:
        raise GlbReadError("missing JSON chunk")
    views = json_blob.get("bufferViews", [])
    accessors = json_blob.get("accessors", [])

    def acc(index: int) -> tuple[bytes, dict]:
        a = accessors[index]
        view = views[a["bufferView"]]
        start = view.get("byteOffset", 0) + a.get("byteOffset", 0)
        return bin_blob[start:], a

    def vec3(index: int) -> list[tuple[float, float, float]]:
        blob, a = acc(index)
        n = a["count"]
        return [struct.unpack_from("<3f", blob, i * 12) for i in range(n)]

    def vec2(index: int) -> list[tuple[float, float]]:
        blob, a = acc(index)
        n = a["count"]
        return [struct.unpack_from("<2f", blob, i * 8) for i in range(n)]

    def idx(index: int) -> list[int]:
        blob, a = acc(index)
        n = a["count"]
        fmt = {5123: "<H", 5125: "<I", 5121: "<B"}[a["componentType"]]
        size = {"<H": 2, "<I": 4, "<B": 1}[fmt]
        return [struct.unpack_from(fmt, blob, i * size)[0] for i in range(n)]

    out: list[GlbPrimitive] = []
    for mesh in json_blob.get("meshes", []):
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            if "POSITION" not in attrs or "indices" not in prim:
                continue
            extras = prim.get("extras") or mesh.get("extras") or {}
            uvs = vec2(attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else []
            out.append(
                GlbPrimitive(
                    positions=vec3(attrs["POSITION"]),
                    texcoords=uvs,
                    indices=idx(prim["indices"]),
                    extras=dict(extras),
                )
            )
    if not out:
        raise GlbReadError("no mesh primitives with POSITION + indices")
    return out
