"""Edge / skinned XPP geometry — list what is proven, do not invent verts.

Static props (helicopter chassis) store float positions in the package.
Characters and most vehicle hulls use Sony EDGE / Ice skinning: the XPP keeps
envelopes, indices, and compressed streams; an SPU job writes the GPU buffers
at draw time. This module recognizes those records. It does not decompress
positions or apply bone weights — that contract is still open.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from .mesh import GEOMETRY_HEAP_CHUNK, MATERIAL_CLASS, OBJECT_CHUNK
from .xpp import XppFile


@dataclass(frozen=True)
class EdgeEnvelope:
    record_offset: int
    stream_offsets: tuple[int, ...]
    index_offset: int
    packed_stream_words: tuple[int, int]


@dataclass(frozen=True)
class SkinnedHeader:
    """Hollow mesh header: counts + AABB exist, GPU pointers are empty."""

    record_offset: int
    oid: int
    triangle_count: int
    vertex_count: int
    material_offset: int
    bounds: tuple[float, float, float, float, float, float]


@dataclass(frozen=True)
class GeomWrapper:
    """Class 0x161 table seen in vehicle packages (not a vertex buffer)."""

    file_offset: int
    blob_offset: int


def _contains(offset: int, size: int, address: int, length: int = 1) -> bool:
    return length >= 0 and offset <= address and address + length <= offset + size


def find_edge_envelopes(data: bytes, parsed: XppFile) -> list[EdgeEnvelope]:
    """Character-style paired-stream envelopes (research 99)."""
    heaps = [chunk for chunk in parsed.chunks if chunk.type_tag == GEOMETRY_HEAP_CHUNK]
    if len(heaps) != 1:
        return []
    heap = heaps[0]
    payload = parsed.data_offset
    found: list[EdgeEnvelope] = []
    for chunk in parsed.chunks:
        if chunk.type_tag != OBJECT_CHUNK or chunk.size < 60:
            continue
        for relative in range(0, chunk.size - 60 + 1, 4):
            offset = chunk.offset + relative
            words = struct.unpack_from(">15I", data, payload + offset)
            required = (0, 2, 4, 10)
            if not all(
                words[index] == words[index + 1]
                and _contains(heap.offset, heap.size, words[index], 2)
                for index in required
            ):
                continue
            if words[6] != words[7] or (
                words[6] != 0 and not _contains(heap.offset, heap.size, words[6], 2)
            ):
                continue
            if not words[8] or not words[9] or words[8] & 0xFFFF or words[9] & 0xFFFF:
                continue
            if words[14] & 0xFFFF != 0x000C or not 1 <= (words[14] >> 16) <= 16:
                continue
            streams = tuple(words[index] for index in (0, 2, 4, 6) if words[index])
            if len(streams) not in (3, 4) or len(set(streams)) != len(streams):
                continue
            if words[10] in streams:
                continue
            found.append(
                EdgeEnvelope(
                    record_offset=offset,
                    stream_offsets=streams,
                    index_offset=words[10],
                    packed_stream_words=(words[8], words[9]),
                )
            )
    return found


def header_from_words(words: tuple[int, ...], record_offset: int = 0) -> SkinnedHeader | None:
    if len(words) < 24:
        return None
    triangle_count = words[1] >> 16
    flags = words[1] & 0xFFFF
    vertex_count = words[21]
    if flags != 3 or not 0 < triangle_count < 20000 or not 0 < vertex_count < 20000:
        return None
    if words[13] not in (0, 0xFFFFFFFF) or words[19] not in (0, 0xFFFFFFFF):
        return None
    bounds = []
    for word in words[4:10]:
        value = struct.unpack(">f", word.to_bytes(4, "big"))[0]
        if not math.isfinite(value):
            return None
        bounds.append(value)
    if any(bounds[axis] > bounds[axis + 3] for axis in range(3)):
        return None
    return SkinnedHeader(
        record_offset=record_offset,
        oid=words[0],
        triangle_count=triangle_count,
        vertex_count=vertex_count,
        material_offset=words[2],
        bounds=tuple(bounds),
    )


def find_skinned_headers(data: bytes, parsed: XppFile) -> list[SkinnedHeader]:
    """Vehicle records with triangle/vertex counts and AABB, but no bind-pose streams."""
    found: list[SkinnedHeader] = []
    payload = parsed.data_offset
    for chunk in parsed.chunks:
        if chunk.type_tag != OBJECT_CHUNK or chunk.size < 0x60:
            continue
        for relative in range(0, chunk.size - 0x60 + 1, 0x10):
            record = chunk.offset + relative
            words = struct.unpack_from(">24I", data, payload + record)
            header = header_from_words(words, record)
            if header is not None:
                found.append(header)
    return found


def find_geom_wrappers(data: bytes) -> list[GeomWrapper]:
    found: list[GeomWrapper] = []
    for offset in range(0, len(data) - 16, 4):
        if struct.unpack_from(">I", data, offset)[0] != 0x00000161:
            continue
        blob = struct.unpack_from(">I", data, offset + 12)[0]
        found.append(GeomWrapper(file_offset=offset, blob_offset=blob))
    return found


def describe_skinned(data: bytes, parsed: XppFile) -> dict:
    envelopes = find_edge_envelopes(data, parsed)
    headers = find_skinned_headers(data, parsed)
    wrappers = find_geom_wrappers(data)
    heaps = [chunk for chunk in parsed.chunks if chunk.type_tag == GEOMETRY_HEAP_CHUNK]
    return {
        "edgeEnvelopes": len(envelopes),
        "skinnedHeaders": [
            {
                "recordOffset": f"0x{header.record_offset:x}",
                "oid": header.oid,
                "triangles": header.triangle_count,
                "vertices": header.vertex_count,
                "material": f"0x{header.material_offset:x}",
                "bounds": [round(value, 3) for value in header.bounds],
            }
            for header in headers
        ],
        "geomWrappers": len(wrappers),
        "geometryHeap": heaps[0].size if len(heaps) == 1 else 0,
        "materials": sum(
            1
            for offset in range(0, len(data) - 4, 4)
            if struct.unpack_from(">I", data, offset)[0] == MATERIAL_CLASS
        ),
        "note": (
            "Edge/Ice skinned hull: counts and AABBs are in the package; "
            "bind-pose vertices are decompressed at runtime. No GLB until that stream is proven."
        ),
    }
