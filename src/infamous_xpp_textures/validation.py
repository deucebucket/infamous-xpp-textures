"""Strict XPP structure and retail-to-candidate texture-budget validation."""

from __future__ import annotations

from pathlib import Path

from .heap import DESC_STRIDE, heap_chunks, read_all_descriptors
from .xpp import TEXDESC_CHUNK, parse_xpp


class ValidationError(ValueError):
    """Raised when an XPP is structurally unsafe or incompatible with retail."""


def validate_xpp(data: bytes) -> tuple[dict, list]:
    """Validate every texture descriptor and every resolved texel-heap range."""
    try:
        package = parse_xpp(data, len(data))
    except (ValueError, OSError) as error:
        raise ValidationError(str(error)) from error

    descriptor_chunks = [
        chunk for chunk in package.chunks if chunk.type_tag == TEXDESC_CHUNK
    ]
    for index, chunk in enumerate(descriptor_chunks):
        if chunk.size % DESC_STRIDE:
            raise ValidationError(
                f"texture descriptor chunk {index} size {chunk.size} is not a multiple "
                f"of {DESC_STRIDE}"
            )

    descriptors = read_all_descriptors(data, package)
    invalid = [(index, reason) for index, _raw, record, reason in descriptors if record is None or reason]
    if invalid:
        index, reason = invalid[0]
        raise ValidationError(
            f"descriptor {index} is invalid ({reason}); {len(invalid)} invalid descriptor(s)"
        )

    records = [record for _index, _raw, record, _reason in descriptors if record is not None]
    for record in records:
        if record.mips != record.embedded_mips:
            raise ValidationError(
                f"descriptor {record.index} mip count disagrees: "
                f"explicit {record.mips}, embedded {record.embedded_mips}"
            )

    heaps = heap_chunks(package)
    grouped: list[list] = [[] for _heap in heaps]
    for record in records:
        owners = [
            index
            for index, heap in enumerate(heaps)
            if heap.offset <= record.data_addr
            and record.data_addr + record.chain_bytes * record.faces <= heap.offset + heap.size
        ]
        if len(owners) != 1:
            raise ValidationError(
                f"descriptor {record.index} resolves to {len(owners)} texel heaps instead of one"
            )
        grouped[owners[0]].append(record)

    layout_pairs = 0
    for heap_index, heap_records in enumerate(grouped):
        ordered = sorted(heap_records, key=lambda record: record.data_addr)
        for current, following in zip(ordered, ordered[1:]):
            layout_pairs += 1
            expected = current.data_addr + current.stride_bytes
            if following.data_addr != expected:
                relation = "overlaps" if following.data_addr < expected else "leaves an unexpected gap before"
                raise ValidationError(
                    f"descriptor {following.index} {relation} the previous chain in texel heap "
                    f"{heap_index}: expected 0x{expected:x}, found 0x{following.data_addr:x}"
                )

    return (
        {
            "structural_status": "pass",
            "bytes": len(data),
            "descriptors": len(records),
            "descriptor_chunks": len(descriptor_chunks),
            "texel_heaps": len(heaps),
            "layout_pairs": layout_pairs,
            "chain_bytes": sum(record.chain_bytes * record.faces for record in records),
            "padded_chain_bytes": sum(record.stride_bytes for record in records),
        },
        records,
    )


def compare_xpp(
    retail_data: bytes,
    candidate_data: bytes,
    *,
    known_pass_extra: int | None = None,
    known_fail_extra: int | None = None,
) -> dict:
    """Strictly compare a candidate XPP with the retail package it replaces."""
    retail_summary, retail_records = validate_xpp(retail_data)
    candidate_summary, candidate_records = validate_xpp(candidate_data)
    if len(retail_records) != len(candidate_records):
        raise ValidationError(
            f"descriptor count changed from {len(retail_records)} to {len(candidate_records)}"
        )

    changes = []
    promoted = 0
    for retail, candidate in zip(retail_records, candidate_records, strict=True):
        if retail.index != candidate.index:
            raise ValidationError("descriptor index order changed")
        if retail.format != candidate.format:
            raise ValidationError(
                f"descriptor {retail.index} format changed from 0x{retail.format:02x} "
                f"to 0x{candidate.format:02x}"
            )
        if retail.faces != candidate.faces:
            raise ValidationError(f"descriptor {retail.index} cubemap/face count changed")
        if candidate.width < retail.width or candidate.height < retail.height:
            raise ValidationError(f"descriptor {retail.index} was downscaled below retail")
        if candidate.width % retail.width or candidate.height % retail.height:
            raise ValidationError(f"descriptor {retail.index} has a fractional retail scale")
        width_scale = candidate.width // retail.width
        height_scale = candidate.height // retail.height
        if width_scale != height_scale or width_scale < 1 or width_scale & (width_scale - 1):
            raise ValidationError(
                f"descriptor {retail.index} width/height do not use one power-of-two scale"
            )
        expected_mips = retail.mips + (width_scale.bit_length() - 1)
        if candidate.mips != expected_mips:
            raise ValidationError(
                f"descriptor {retail.index} has {candidate.mips} mips at {width_scale}x; "
                f"expected {expected_mips}"
            )

        chain_delta = candidate.chain_bytes * candidate.faces - retail.chain_bytes * retail.faces
        padded_delta = candidate.stride_bytes - retail.stride_bytes
        if width_scale > 1:
            promoted += 1
        if width_scale > 1 or chain_delta or padded_delta:
            changes.append(
                {
                    "index": retail.index,
                    "scale": width_scale,
                    "retail_dimensions": [retail.width, retail.height],
                    "candidate_dimensions": [candidate.width, candidate.height],
                    "retail_mips": retail.mips,
                    "candidate_mips": candidate.mips,
                    "chain_delta_bytes": chain_delta,
                    "padded_delta_bytes": padded_delta,
                }
            )

    chain_delta = candidate_summary["chain_bytes"] - retail_summary["chain_bytes"]
    padded_delta = candidate_summary["padded_chain_bytes"] - retail_summary["padded_chain_bytes"]
    return {
        "structural_status": "pass",
        "retail": retail_summary,
        "candidate": candidate_summary,
        "promoted_records": promoted,
        "changed_layout_records": len(changes),
        "chain_delta_bytes": chain_delta,
        "padded_chain_delta_bytes": padded_delta,
        "package_delta_bytes": len(candidate_data) - len(retail_data),
        "budget": classify_budget(chain_delta, known_pass_extra, known_fail_extra),
        "changes": changes,
    }


def classify_budget(
    extra_bytes: int,
    known_pass_extra: int | None,
    known_fail_extra: int | None,
) -> dict:
    """Classify memory growth without pretending that structure proves runtime success."""
    if known_pass_extra is not None and known_pass_extra < 0:
        raise ValidationError("known pass extra bytes cannot be negative")
    if known_fail_extra is not None and known_fail_extra < 0:
        raise ValidationError("known fail extra bytes cannot be negative")
    if (
        known_pass_extra is not None
        and known_fail_extra is not None
        and known_pass_extra >= known_fail_extra
    ):
        raise ValidationError("known pass extra bytes must be below known fail extra bytes")

    if known_pass_extra is None and known_fail_extra is None:
        status = "not-calibrated"
    elif known_pass_extra is not None and extra_bytes <= known_pass_extra:
        status = "within-observed-startup-pass-range"
    elif known_fail_extra is not None and extra_bytes >= known_fail_extra:
        status = "at-or-above-observed-startup-fail-range"
    else:
        status = "unproven-between-known-bounds"
    return {
        "status": status,
        "extra_chain_bytes": extra_bytes,
        "known_pass_extra_bytes": known_pass_extra,
        "known_fail_extra_bytes": known_fail_extra,
        "runtime_still_required": True,
        "scene_coverage_required": True,
        "evidence_scope": "startup-path-only",
    }


def validate_replacement_set(
    sources: dict[str, Path],
    routed: dict[str, dict[str, bytes]],
    *,
    known_pass_extra: int | None = None,
    known_fail_extra: int | None = None,
    progress=None,
) -> dict:
    """Validate every routed replacement against its retail PSARC payload."""
    from .psarc import iter_archive_entries

    package_reports = []
    found: set[tuple[str, str]] = set()
    for slot, source in sources.items():
        if progress is not None:
            progress(f"Strictly validating {len(routed[slot])} {slot} replacement XPPs...")
        if not routed[slot]:
            continue
        for manifest_name, retail_data in iter_archive_entries(source):
            replacement_key = _replacement_key(manifest_name, routed[slot])
            if replacement_key is None:
                continue
            basename = Path(manifest_name).name
            try:
                report = compare_xpp(retail_data, routed[slot][replacement_key])
            except ValidationError as error:
                raise ValidationError(f"{slot}/{basename}: {error}") from error
            found.add((slot, replacement_key))
            package_reports.append(
                {
                    "slot": slot,
                    "file_name": basename,
                    "manifest_name": manifest_name,
                    **report,
                }
            )

    expected = {(slot, name) for slot, replacements in routed.items() for name in replacements}
    missing = expected - found
    if missing:
        raise ValidationError(f"replacement payloads were not found in retail: {sorted(missing)}")

    extra_chain_bytes = sum(report["chain_delta_bytes"] for report in package_reports)
    return {
        "structural_status": "pass",
        "replacement_count": len(package_reports),
        "promoted_records": sum(report["promoted_records"] for report in package_reports),
        "chain_delta_bytes": extra_chain_bytes,
        "padded_chain_delta_bytes": sum(
            report["padded_chain_delta_bytes"] for report in package_reports
        ),
        "package_delta_bytes": sum(report["package_delta_bytes"] for report in package_reports),
        "budget": classify_budget(extra_chain_bytes, known_pass_extra, known_fail_extra),
        "packages": sorted(
            package_reports, key=lambda report: (report["slot"], report["file_name"].casefold())
        ),
    }


def _replacement_key(name: str, replacements: dict[str, bytes]) -> str | None:
    if name in replacements:
        return name
    portable = name.lstrip("/")
    if portable in replacements:
        return portable
    basename = Path(name).name
    if basename in replacements:
        return basename
    return None
