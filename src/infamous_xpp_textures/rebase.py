"""Fail-closed texture-edit transfer between different retail XPP builds."""

from __future__ import annotations

from collections import Counter, defaultdict

from .heap import align_up, heap_bytes, read_records
from .pack import PackError, pack_chains
from .validation import ValidationError, compare_xpp, validate_xpp
from .xpp import parse_xpp


class RebaseError(ValueError):
    """Raised when a texture edit cannot be bound uniquely to the target build."""


def _records_and_chains(data: bytes) -> tuple[list, dict[int, bytes]]:
    package = parse_xpp(data, len(data))
    records = read_records(data, package)
    texels = heap_bytes(data, package)
    chains = {}
    for record in records:
        face_stride = align_up(record.chain_bytes)
        chains[record.index] = b"".join(
            texels[
                record.heap_offset
                + face * face_stride : record.heap_offset
                + face * face_stride
                + record.chain_bytes
            ]
            for face in range(record.faces)
        )
    return records, chains


def _retail_identity(record, chain: bytes) -> tuple:
    """Cross-build identity: exact retail texels plus their decoded shape."""
    return (
        record.format,
        record.faces,
        record.width,
        record.height,
        record.mips,
        chain,
    )


def rebase_texture_edits(
    source_retail: bytes,
    source_candidate: bytes,
    target_retail: bytes,
    *,
    include_indices: set[int] | None = None,
    allow_zero_change: bool = False,
) -> tuple[bytes, dict]:
    """Apply source retail-to-candidate texture changes onto a target retail XPP.

    Source descriptor ordinals are never treated as target identities. Each
    selected source retail chain must instead match exactly one target retail
    chain with the same format, face count, dimensions, and mip count. The
    target package remains the structural base.
    """
    try:
        source_comparison = compare_xpp(source_retail, source_candidate)
        target_summary, _target_validated = validate_xpp(target_retail)
        source_records, source_chains = _records_and_chains(source_retail)
        candidate_records, candidate_chains = _records_and_chains(source_candidate)
        target_records, target_chains = _records_and_chains(target_retail)
    except (PackError, ValidationError, ValueError) as error:
        raise RebaseError(str(error)) from error

    candidate_by_index = {record.index: record for record in candidate_records}
    changed_indices = {
        record.index
        for record in source_records
        if (
            source_chains[record.index] != candidate_chains[record.index]
            or (record.width, record.height, record.mips)
            != (
                candidate_by_index[record.index].width,
                candidate_by_index[record.index].height,
                candidate_by_index[record.index].mips,
            )
        )
    }

    if include_indices is None:
        selected = changed_indices
    else:
        unknown = include_indices - {record.index for record in source_records}
        if unknown:
            raise RebaseError(f"unknown source descriptor indices: {sorted(unknown)}")
        unchanged = include_indices - changed_indices
        if unchanged:
            raise RebaseError(
                "selected source descriptors have no texture edit: "
                f"{sorted(unchanged)}"
            )
        selected = set(include_indices)

    if not selected:
        if not allow_zero_change:
            raise RebaseError("source candidate has no selected texture edits")
        return target_retail, {
            "kind": "cross-build-texture-rebase",
            "structural_status": "pass",
            "identity": "exact-retail-chain-and-shape",
            "source_descriptor_count": len(source_records),
            "target_descriptor_count": len(target_records),
            "source_changed_records": len(changed_indices),
            "selected_record_count": 0,
            "mapped_record_count": 0,
            "same_ordinal_mapping_count": 0,
            "target_unchanged_records_verified": len(target_records),
            "output_byte_identical_to_target": True,
            "direct_source_package_transfer_used": False,
            "runtime_still_required": True,
            "target_validation": {
                "promoted_records": 0,
                "chain_delta_bytes": 0,
                "padded_chain_delta_bytes": 0,
                "package_delta_bytes": 0,
            },
        }

    source_identities = {
        record.index: _retail_identity(record, source_chains[record.index])
        for record in source_records
    }
    source_identity_counts = Counter(source_identities.values())
    duplicate_source = [
        index for index in sorted(selected) if source_identity_counts[source_identities[index]] != 1
    ]
    if duplicate_source:
        raise RebaseError(
            "selected source retail identities are not unique: "
            f"{duplicate_source}"
        )

    target_by_identity: dict[tuple, list] = defaultdict(list)
    for record in target_records:
        target_by_identity[_retail_identity(record, target_chains[record.index])].append(record)

    source_by_index = {record.index: record for record in source_records}
    replacements: dict[int, tuple[int, int, int, bytes]] = {}
    same_ordinal = 0
    for source_index in sorted(selected):
        source_record = source_by_index[source_index]
        candidate_record = candidate_by_index[source_index]
        if source_record.faces != 1 or candidate_record.faces != 1:
            raise RebaseError(f"source descriptor {source_index} is not a 2D texture")
        matches = target_by_identity.get(source_identities[source_index], [])
        if not matches:
            raise RebaseError(
                f"source descriptor {source_index} has no exact target retail identity"
            )
        if len(matches) != 1:
            raise RebaseError(
                f"source descriptor {source_index} has {len(matches)} target retail identities"
            )
        target_record = matches[0]
        if target_record.index in replacements:
            raise RebaseError("multiple source edits resolve to one target descriptor")
        candidate_chain = candidate_chains[source_index]
        replacements[target_record.index] = (
            candidate_record.width,
            candidate_record.height,
            candidate_record.mips,
            candidate_chain,
        )
        if target_record.index == source_index:
            same_ordinal += 1

    try:
        output = pack_chains(target_retail, replacements)
        target_comparison = compare_xpp(target_retail, output)
        output_records, output_chains = _records_and_chains(output)
    except (PackError, ValidationError, ValueError) as error:
        raise RebaseError(str(error)) from error

    output_by_index = {record.index: record for record in output_records}
    for target_record in target_records:
        output_record = output_by_index[target_record.index]
        if target_record.index in replacements:
            width, height, mips, chain = replacements[target_record.index]
            if (output_record.width, output_record.height, output_record.mips) != (
                width,
                height,
                mips,
            ) or output_chains[target_record.index] != chain:
                raise RebaseError("rebuilt target does not contain an exact selected edit")
        elif (
            (output_record.width, output_record.height, output_record.mips)
            != (target_record.width, target_record.height, target_record.mips)
            or output_chains[target_record.index] != target_chains[target_record.index]
        ):
            raise RebaseError("rebuilt target changed an unselected texture record")

    return output, {
        "kind": "cross-build-texture-rebase",
        "structural_status": "pass",
        "identity": "exact-retail-chain-and-shape",
        "source_descriptor_count": len(source_records),
        "target_descriptor_count": target_summary["descriptors"],
        "source_changed_records": len(changed_indices),
        "selected_record_count": len(selected),
        "mapped_record_count": len(replacements),
        "same_ordinal_mapping_count": same_ordinal,
        "target_unchanged_records_verified": len(target_records) - len(replacements),
        "output_byte_identical_to_target": output == target_retail,
        "direct_source_package_transfer_used": False,
        "runtime_still_required": True,
        "source_validation": {
            "promoted_records": source_comparison["promoted_records"],
            "chain_delta_bytes": source_comparison["chain_delta_bytes"],
            "padded_chain_delta_bytes": source_comparison["padded_chain_delta_bytes"],
            "package_delta_bytes": source_comparison["package_delta_bytes"],
        },
        "target_validation": {
            "promoted_records": target_comparison["promoted_records"],
            "chain_delta_bytes": target_comparison["chain_delta_bytes"],
            "padded_chain_delta_bytes": target_comparison["padded_chain_delta_bytes"],
            "package_delta_bytes": target_comparison["package_delta_bytes"],
        },
    }
