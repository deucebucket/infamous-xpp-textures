"""Derive lower-memory XPPs losslessly from an existing 2x/4x XPP."""

from __future__ import annotations

from .heap import chain_size, heap_chunks, level_size, read_records
from .pack import PackError, pack_chains
from .xpp import parse_xpp


def _scale_of(retail, source) -> int:
    for scale, mip_growth in ((2, 1), (4, 2)):
        if (
            source.width == retail.width * scale
            and source.height == retail.height * scale
            and source.mips == retail.mips + mip_growth
            and source.format == retail.format
            and source.faces == retail.faces == 1
        ):
            return scale
    if (
        source.width,
        source.height,
        source.mips,
        source.format,
        source.faces,
    ) == (
        retail.width,
        retail.height,
        retail.mips,
        retail.format,
        retail.faces,
    ):
        return 1
    return 0


def derive_scaled(
    retail: bytes,
    source: bytes,
    *,
    target_scale: int = 2,
    include_indices: set[int] | None = None,
    exclude_indices: set[int] | None = None,
    max_upscaled: int | None = None,
) -> tuple[bytes, int, int]:
    """Copy exact mip suffixes from a mixed 2x/4x source into a retail XPP.

    A target scale of 1 keeps the enhanced texels at retail dimensions. A
    target scale of 2 drops the largest mip from a 4x source. No BCn data is
    decoded or recompressed.
    """
    if target_scale not in (1, 2, 4):
        raise PackError("target scale must be 1, 2, or 4")
    retail_pkg = parse_xpp(retail, len(retail))
    source_pkg = parse_xpp(source, len(source))
    if len(heap_chunks(retail_pkg)) != 1 or len(heap_chunks(source_pkg)) != 1:
        raise PackError("retail and source XPPs must each contain one texel heap")

    retail_records = {record.index: record for record in read_records(retail, retail_pkg)}
    source_records = {record.index: record for record in read_records(source, source_pkg)}
    if not retail_records or retail_records.keys() != source_records.keys():
        raise PackError("retail and source descriptor sets differ")

    source_scales: dict[int, int] = {}
    changed: set[int] = set()
    for index, original in retail_records.items():
        scale = _scale_of(original, source_records[index])
        if not scale:
            raise PackError(
                f"texture {index} is neither unchanged nor an exact 2x/4x replacement"
            )
        source_scales[index] = scale
        if scale > 1:
            changed.add(index)
    if not changed:
        raise PackError("source XPP contains no exact 2x/4x replacements")

    include_indices = include_indices or set()
    exclude_indices = exclude_indices or set()
    if include_indices and exclude_indices:
        raise PackError("include and exclude selections are mutually exclusive")
    unknown = (include_indices | exclude_indices) - changed
    if unknown:
        raise PackError(f"selected indices are not upscaled source records: {sorted(unknown)}")
    selected = set(include_indices) if include_indices else changed - exclude_indices

    if max_upscaled is not None:
        if include_indices or exclude_indices:
            raise PackError("max_upscaled cannot be combined with include/exclude selections")
        if max_upscaled < 1:
            raise PackError("max_upscaled must be at least 1")
        ranked = sorted(
            changed,
            key=lambda index: (
                chain_size(
                    retail_records[index].format,
                    retail_records[index].width * target_scale,
                    retail_records[index].height * target_scale,
                    retail_records[index].mips
                    + (0 if target_scale == 1 else target_scale.bit_length() - 1),
                )
                - retail_records[index].chain_bytes,
                index,
            ),
        )
        selected = set(ranked[:max_upscaled])

    replacements: dict[int, tuple[int, int, int, bytes]] = {}
    expected_chains: dict[int, bytes] = {}
    for index in sorted(selected):
        original = retail_records[index]
        current = source_records[index]
        source_scale = source_scales[index]
        if target_scale > source_scale:
            raise PackError(
                f"texture {index} has only a {source_scale}x source; cannot emit {target_scale}x"
            )
        source_file_offset = source_pkg.data_offset + current.data_addr
        chain = source[source_file_offset : source_file_offset + current.chain_bytes]
        if len(chain) != current.chain_bytes:
            raise PackError(f"texture {index} source chain is truncated")

        width, height, mips = current.width, current.height, current.mips
        source_growth = {2: 1, 4: 2}[source_scale]
        target_growth = {1: 0, 2: 1, 4: 2}[target_scale]
        for _ in range(source_growth - target_growth):
            skip = level_size(current.format, width, height, 0)
            chain = chain[skip:]
            width //= 2
            height //= 2
            mips -= 1
        expected = chain_size(current.format, width, height, mips)
        if len(chain) != expected:
            raise PackError(f"texture {index} derived chain has the wrong size")
        if (width, height) != (
            original.width * target_scale,
            original.height * target_scale,
        ):
            raise PackError(f"texture {index} derived dimensions do not match target scale")
        replacements[index] = (width, height, mips, chain)
        expected_chains[index] = chain

    result = pack_chains(retail, replacements)
    result_pkg = parse_xpp(result, len(result))
    result_records = {record.index: record for record in read_records(result, result_pkg)}
    for index, expected in expected_chains.items():
        record = result_records[index]
        start = result_pkg.data_offset + record.data_addr
        if result[start : start + record.chain_bytes] != expected:
            raise PackError(f"texture {index} output chain differs from its source mip suffix")
    return result, len(selected), len(retail_records)
