"""PACK-v8 / inFAMOUS 1 XPP header and chunk tables. Read-only."""

from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"PACK"
HEADER_SIZE = 0x70
TABLES_OFFSET = 0x88
SEGMENT_SIZE = 28
CHUNK_SIZE = 16
FIXUP_SIZE = 20
TEXDESC_CHUNK = 0x03100000
TEXEL_CHUNK = 0x0D800000
OVERLAY_CHUNK_TYPE = 0x0C100000


class XppError(ValueError):
    """Raised when an XPP violates a confirmed structural invariant."""


@dataclass(frozen=True)
class Segment:
    type_tag: int
    size: int
    offset: int
    reserved_0c: int
    reserved_10: int
    first_chunk: int
    chunk_count: int


@dataclass(frozen=True)
class Chunk:
    type_tag: int
    size: int
    offset: int
    reserved: int


@dataclass(frozen=True)
class Fixup:
    value_00: int
    value_04: int
    type_tag: int
    reserved: int
    sentinel: int


@dataclass(frozen=True)
class XppFile:
    version: int
    header_size: int
    data_offset: int
    data_size: int
    segment_count: int
    chunk_count: int
    fixup_count: int
    segments: tuple[Segment, ...]
    chunks: tuple[Chunk, ...]
    fixups: tuple[Fixup, ...]


def parse_xpp(data: bytes, file_size: int | None = None) -> XppFile:
    """Parse header and metadata tables. `data` may be the full file."""
    actual_size = len(data) if file_size is None else file_size
    if len(data) < TABLES_OFFSET:
        raise XppError(f"truncated XPP metadata ({len(data)} bytes)")
    if data[:4] != MAGIC:
        raise XppError(f"bad magic {data[:4]!r}")

    version, header_size = struct.unpack_from(">HH", data, 4)
    words = struct.unpack_from(">10I", data, 8)
    metadata_offset = words[4]
    metadata_size = words[5]
    data_offset = words[8]
    data_size = words[9]
    if version != 8 or header_size != HEADER_SIZE:
        raise XppError(f"unsupported version/header pair {version}/0x{header_size:x}")
    if any(data[0x30:HEADER_SIZE]):
        raise XppError("reserved header bytes 0x30..0x6f are nonzero")
    if metadata_offset != HEADER_SIZE:
        raise XppError(f"metadata offset is 0x{metadata_offset:x}, expected 0x70")
    if metadata_offset + metadata_size != data_offset:
        raise XppError("metadata offset + size does not equal payload offset")
    if data_offset + data_size != actual_size:
        raise XppError(
            f"payload ends at 0x{data_offset + data_size:x}, file ends at 0x{actual_size:x}"
        )

    segment_count, chunk_count, fixup_count = struct.unpack_from(">QQQ", data, 0x70)
    calculated = (
        TABLES_OFFSET
        + segment_count * SEGMENT_SIZE
        + chunk_count * CHUNK_SIZE
        + fixup_count * FIXUP_SIZE
    )
    if calculated != data_offset:
        raise XppError(
            f"table sizes imply payload offset 0x{calculated:x}, header says 0x{data_offset:x}"
        )
    if len(data) < data_offset:
        raise XppError(f"only {len(data)} metadata bytes available; need {data_offset}")

    segment_start = TABLES_OFFSET
    chunk_start = segment_start + segment_count * SEGMENT_SIZE
    fixup_start = chunk_start + chunk_count * CHUNK_SIZE

    segments = tuple(
        Segment(*struct.unpack_from(">7I", data, segment_start + i * SEGMENT_SIZE))
        for i in range(segment_count)
    )
    chunks = tuple(
        Chunk(*struct.unpack_from(">4I", data, chunk_start + i * CHUNK_SIZE))
        for i in range(chunk_count)
    )
    fixups = tuple(
        Fixup(*struct.unpack_from(">5I", data, fixup_start + i * FIXUP_SIZE))
        for i in range(fixup_count)
    )

    data_cursor = 0
    chunk_cursor = 0
    for index, segment in enumerate(segments):
        if segment.reserved_0c or segment.reserved_10:
            raise XppError(f"segment {index} has nonzero reserved fields")
        if segment.offset != data_cursor:
            raise XppError(f"segment {index} breaks contiguous payload partition")
        if segment.first_chunk != chunk_cursor:
            raise XppError(f"segment {index} breaks contiguous chunk-index partition")
        data_cursor += segment.size
        chunk_cursor += segment.chunk_count
    if data_cursor != data_size or chunk_cursor != chunk_count:
        raise XppError("segment table does not cover the payload and chunk table exactly")

    previous_offset = 0
    for index, chunk in enumerate(chunks):
        if chunk.reserved:
            raise XppError(f"chunk {index} has nonzero reserved field")
        if chunk.offset < previous_offset:
            raise XppError(f"chunk {index} offsets are not nondecreasing")
        if chunk.offset + chunk.size > data_size and chunk.type_tag != OVERLAY_CHUNK_TYPE:
            raise XppError(f"chunk {index} exceeds payload")
        previous_offset = chunk.offset

    for index, fixup in enumerate(fixups):
        if fixup.reserved:
            raise XppError(f"fixup {index} has nonzero reserved field")
        if fixup.sentinel != 0xFFFFFFFF:
            raise XppError(f"fixup {index} sentinel is 0x{fixup.sentinel:08x}")

    return XppFile(
        version,
        header_size,
        data_offset,
        data_size,
        segment_count,
        chunk_count,
        fixup_count,
        segments,
        chunks,
        fixups,
    )
