"""Bounded packed-source/runtime affine correlation for one character record."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile

from .character import find_skinned_geometry_contracts, unpack_packed_components_msb
from .character_source_export import (
    NUMERIC_FAMILIES,
    _decode_hypothesis,
    _parameter_vectors,
)
from .xpp import parse_xpp


MAX_XPP_SOURCE_BYTES = 64 * 1024 * 1024
MAX_RUNTIME_ARRAY_BYTES = 16 * 1024 * 1024
MAX_RUNTIME_INDEX_BYTES = 16 * 1024 * 1024
MAX_CORRELATION_REPORT_BYTES = 256 * 1024
_HEX = frozenset("0123456789abcdef")
_RANK_TOLERANCE = 1e-12
_FAMILY_METRIC_TOLERANCE = 1e-9


class CharacterSourceCorrelationError(ValueError):
    """Raised when a packed-source/runtime comparison is incomplete or unsafe."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha256(value: str, label: str) -> str:
    lowered = value.lower() if isinstance(value, str) else ""
    if len(lowered) != 64 or any(character not in _HEX for character in lowered):
        raise CharacterSourceCorrelationError(f"{label} must be an exact SHA-256")
    return lowered


def regular_file_identity(path: Path, label: str) -> tuple[int, int, int, int]:
    """Return a comparison identity for one regular non-symlink input file."""

    if path.is_symlink() or not path.is_file():
        raise CharacterSourceCorrelationError(
            f"{label} must be a regular non-symlink file"
        )
    state = path.stat()
    return (state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns)


def read_bounded_regular_file(path: Path, maximum: int, label: str) -> bytes:
    """Read one immutable regular file while rejecting links, drift, and over-bounds."""

    identity_before = regular_file_identity(path, label)
    before = path.stat()
    if before.st_size <= 0 or before.st_size > maximum:
        raise CharacterSourceCorrelationError(
            f"{label} is empty or exceeds its byte bound"
        )
    payload = path.read_bytes()
    after = path.stat()
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or len(payload) != before.st_size:
        raise CharacterSourceCorrelationError(f"{label} changed while it was read")
    return payload


def _matrix_rank(matrix: list[list[float]]) -> int:
    rows = [row[:] for row in matrix]
    if not rows:
        return 0
    scale = max((abs(value) for row in rows for value in row), default=0.0)
    if scale == 0.0:
        return 0
    pivot_row = 0
    for column in range(len(rows[0])):
        pivot = max(range(pivot_row, len(rows)), key=lambda row: abs(rows[row][column]))
        if abs(rows[pivot][column]) <= scale * _RANK_TOLERANCE:
            continue
        rows[pivot_row], rows[pivot] = rows[pivot], rows[pivot_row]
        divisor = rows[pivot_row][column]
        rows[pivot_row] = [value / divisor for value in rows[pivot_row]]
        for row in range(len(rows)):
            if row == pivot_row:
                continue
            factor = rows[row][column]
            rows[row] = [
                rows[row][index] - factor * rows[pivot_row][index]
                for index in range(len(rows[row]))
            ]
        pivot_row += 1
        if pivot_row == len(rows):
            break
    return pivot_row


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    scale = max((abs(value) for row in matrix for value in row), default=0.0)
    if scale == 0.0:
        return None
    width = len(matrix)
    for column in range(width):
        pivot = max(range(column, width), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) <= scale * _RANK_TOLERANCE:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(width):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                augmented[row][index] - factor * augmented[column][index]
                for index in range(width + 1)
            ]
    return [augmented[row][-1] for row in range(width)]


def _affine_fit(
    source: tuple[tuple[float, float, float], ...],
    runtime: tuple[tuple[float, float, float], ...],
) -> dict:
    if len(source) != len(runtime) or len(source) < 4:
        raise CharacterSourceCorrelationError(
            "affine correlation requires matching arrays with at least four rows"
        )
    source_mean = [sum(row[axis] for row in source) / len(source) for axis in range(3)]
    source_scale = [
        math.sqrt(
            sum((row[axis] - source_mean[axis]) ** 2 for row in source) / len(source)
        )
        for axis in range(3)
    ]
    if any(value <= _RANK_TOLERANCE for value in source_scale):
        return {"status": "rank-deficient", "source_rank": 0}
    standardized = [
        tuple((row[axis] - source_mean[axis]) / source_scale[axis] for axis in range(3))
        for row in source
    ]
    covariance = [
        [sum(row[left] * row[right] for row in standardized) for right in range(3)]
        for left in range(3)
    ]
    rank = _matrix_rank(covariance)
    if rank != 3:
        return {"status": "rank-deficient", "source_rank": rank}

    runtime_mean = [
        sum(row[axis] for row in runtime) / len(runtime) for axis in range(3)
    ]
    coefficients = []
    for output_axis in range(3):
        cross = [
            sum(
                row[input_axis]
                * (runtime[index][output_axis] - runtime_mean[output_axis])
                for index, row in enumerate(standardized)
            )
            for input_axis in range(3)
        ]
        solved = _solve(covariance, cross)
        if solved is None:
            return {"status": "rank-deficient", "source_rank": rank}
        coefficients.append(solved)

    squared_error = 0.0
    maximum_residual = 0.0
    for index, row in enumerate(standardized):
        predicted = [
            runtime_mean[axis]
            + sum(
                coefficients[axis][component] * row[component] for component in range(3)
            )
            for axis in range(3)
        ]
        point_error = sum(
            (predicted[axis] - runtime[index][axis]) ** 2 for axis in range(3)
        )
        squared_error += point_error
        maximum_residual = max(maximum_residual, math.sqrt(point_error))

    total_variance = sum(
        (row[axis] - runtime_mean[axis]) ** 2 for row in runtime for axis in range(3)
    )
    if total_variance <= _RANK_TOLERANCE:
        return {"status": "runtime-low-diversity", "source_rank": rank}
    runtime_min = [min(row[axis] for row in runtime) for axis in range(3)]
    runtime_max = [max(row[axis] for row in runtime) for axis in range(3)]
    diagonal = math.sqrt(
        sum((runtime_max[axis] - runtime_min[axis]) ** 2 for axis in range(3))
    )
    if diagonal <= _RANK_TOLERANCE:
        return {"status": "runtime-low-diversity", "source_rank": rank}
    rmse = math.sqrt(squared_error / (len(runtime) * 3))
    return {
        "status": "fit-complete",
        "source_rank": rank,
        "r_squared": 1.0 - squared_error / total_variance,
        "rmse": rmse,
        "normalized_rmse": rmse / diagonal,
        "maximum_point_residual": maximum_residual,
        "runtime_bounds_diagonal": diagonal,
    }


def _runtime_rows(
    payload: bytes, vertex_count: int, byte_order: str, first_row: int
) -> tuple[tuple[tuple[float, float, float], ...], int]:
    if byte_order not in ("big", "little"):
        raise CharacterSourceCorrelationError(
            "runtime byte order must be big or little"
        )
    if isinstance(first_row, bool) or not isinstance(first_row, int) or first_row < 0:
        raise CharacterSourceCorrelationError(
            "runtime first row must be a nonnegative integer"
        )
    if len(payload) % 12:
        raise CharacterSourceCorrelationError(
            "runtime float32x3 array must contain whole contiguous rows"
        )
    total_rows = len(payload) // 12
    if first_row + vertex_count > total_rows:
        raise CharacterSourceCorrelationError(
            "runtime row window does not cover the selected topology"
        )
    prefix = ">" if byte_order == "big" else "<"
    values = struct.unpack(f"{prefix}{total_rows * 3}f", payload)
    if not all(math.isfinite(value) for value in values):
        raise CharacterSourceCorrelationError(
            "runtime float32x3 array contains nonfinite values"
        )
    rows = tuple(tuple(values[index : index + 3]) for index in range(0, len(values), 3))
    return rows[first_row : first_row + vertex_count], total_rows


def correlate_character_source_runtime(
    xpp_data: bytes,
    runtime_index: bytes,
    runtime_positions: bytes,
    *,
    record_offset: int,
    runtime_index_sha256: str,
    runtime_positions_sha256: str,
    runtime_byte_order: str,
    runtime_first_row: int = 0,
) -> dict:
    """Return payload-free affine correlation metrics for all packed streams."""

    if not xpp_data or len(xpp_data) > MAX_XPP_SOURCE_BYTES:
        raise CharacterSourceCorrelationError("XPP source is empty or exceeds 64 MiB")
    if not runtime_index or len(runtime_index) > MAX_RUNTIME_INDEX_BYTES:
        raise CharacterSourceCorrelationError(
            "runtime index is empty or exceeds 16 MiB"
        )
    if not runtime_positions or len(runtime_positions) > MAX_RUNTIME_ARRAY_BYTES:
        raise CharacterSourceCorrelationError(
            "runtime array is empty or exceeds 16 MiB"
        )
    if (
        isinstance(record_offset, bool)
        or not isinstance(record_offset, int)
        or record_offset < 0
    ):
        raise CharacterSourceCorrelationError(
            "record offset must be a nonnegative integer"
        )
    expected_index_sha256 = _require_sha256(runtime_index_sha256, "runtime index hash")
    expected_positions_sha256 = _require_sha256(
        runtime_positions_sha256, "runtime positions hash"
    )
    if _sha256(runtime_index) != expected_index_sha256:
        raise CharacterSourceCorrelationError("runtime index SHA-256 mismatch")
    if _sha256(runtime_positions) != expected_positions_sha256:
        raise CharacterSourceCorrelationError("runtime positions SHA-256 mismatch")

    parsed = parse_xpp(xpp_data, len(xpp_data))
    contracts = [
        item
        for item in find_skinned_geometry_contracts(xpp_data, parsed)
        if item.record_offset == record_offset
    ]
    if len(contracts) != 1:
        raise CharacterSourceCorrelationError(
            f"record offset selects {len(contracts)} proved character contracts"
        )
    contract = contracts[0]
    source_index_start = parsed.data_offset + contract.index_offset
    source_index = xpp_data[
        source_index_start : source_index_start + contract.index_byte_count
    ]
    if source_index != runtime_index or contract.index_sha256 != expected_index_sha256:
        raise CharacterSourceCorrelationError(
            "runtime index does not byte-match the selected XPP topology"
        )
    runtime, runtime_total_rows = _runtime_rows(
        runtime_positions,
        contract.vertex_count,
        runtime_byte_order,
        runtime_first_row,
    )

    stream_reports = []
    for stream in contract.packed_vertex_streams:
        stream_start = parsed.data_offset + stream.stream_offset
        stream_bytes = xpp_data[stream_start : stream_start + stream.logical_byte_count]
        if (
            len(stream_bytes) != stream.logical_byte_count
            or _sha256(stream_bytes) != stream.stream_sha256
        ):
            raise CharacterSourceCorrelationError("packed stream failed exact identity")
        if stream.component_count != 3 or any(stream.component_bit_widths[3:]):
            stream_reports.append(
                {
                    "stream_index": stream.envelope_stream_index,
                    "stream_sha256": stream.stream_sha256,
                    "status": "unsupported-component-shape",
                }
            )
            continue
        unpacked = unpack_packed_components_msb(
            stream_bytes, stream.component_bit_widths, contract.vertex_count
        )
        first, second = _parameter_vectors(
            xpp_data,
            parsed.data_offset,
            stream.parameter_offset,
            stream.parameter_byte_count,
            stream.component_count,
        )
        families = {}
        for family in NUMERIC_FAMILIES:
            source = _decode_hypothesis(
                unpacked, stream.component_bit_widths, first, second, family
            )
            families[family] = _affine_fit(source, runtime)
        complete = [
            item for item in families.values() if item["status"] == "fit-complete"
        ]
        r_squared_spread = (
            max(item["r_squared"] for item in complete)
            - min(item["r_squared"] for item in complete)
            if complete
            else None
        )
        normalized_rmse_spread = (
            max(item["normalized_rmse"] for item in complete)
            - min(item["normalized_rmse"] for item in complete)
            if complete
            else None
        )
        representative = (
            max(
                complete, key=lambda item: (item["r_squared"], -item["normalized_rmse"])
            )
            if complete
            else None
        )
        stream_reports.append(
            {
                "stream_index": stream.envelope_stream_index,
                "stream_sha256": stream.stream_sha256,
                "component_bit_widths": list(stream.component_bit_widths),
                "parameter_sha256": stream.parameter_sha256,
                "status": "fit-complete"
                if representative is not None
                else "no-complete-fit",
                "numeric_families": families,
                "family_r_squared_spread": r_squared_spread,
                "family_normalized_rmse_spread": normalized_rmse_spread,
                "families_affine_indistinguishable": bool(
                    len(complete) == len(NUMERIC_FAMILIES)
                    and r_squared_spread is not None
                    and normalized_rmse_spread is not None
                    and r_squared_spread <= _FAMILY_METRIC_TOLERANCE
                    and normalized_rmse_spread <= _FAMILY_METRIC_TOLERANCE
                ),
                "representative_fit": representative,
            }
        )

    ranked = [
        item for item in stream_reports if item.get("representative_fit") is not None
    ]
    ranked.sort(
        key=lambda item: (
            -item["representative_fit"]["r_squared"],
            item["representative_fit"]["normalized_rmse"],
            item["stream_index"],
        )
    )
    top_stream_indices = []
    if ranked:
        best = ranked[0]["representative_fit"]["r_squared"]
        top_stream_indices = [
            item["stream_index"]
            for item in ranked
            if best - item["representative_fit"]["r_squared"]
            <= _FAMILY_METRIC_TOLERANCE
        ]
    return {
        "format": "infamous-character-source-runtime-affine-correlation",
        "version": 1,
        "record_offset": contract.record_offset,
        "vertex_count": contract.vertex_count,
        "triangle_count": contract.triangle_count,
        "xpp_sha256": _sha256(xpp_data),
        "index_sha256": contract.index_sha256,
        "runtime_positions_sha256": expected_positions_sha256,
        "runtime_byte_order": runtime_byte_order,
        "runtime_total_row_count": runtime_total_rows,
        "runtime_selected_first_row": runtime_first_row,
        "runtime_selected_row_count": contract.vertex_count,
        "topology_pair_proved": True,
        "runtime_array_identity_proved": True,
        "payload_values_serialized": False,
        "stream_ranking": [item["stream_index"] for item in ranked],
        "top_stream_indices": top_stream_indices,
        "streams": stream_reports,
        "gates": {
            "numeric_family_selected": False,
            "position_semantic": False,
            "component_identity": False,
            "uv": False,
            "material": False,
            "rigged": False,
            "complete_character": False,
            "injection": False,
        },
        "limitations": (
            "topology and supplied runtime/source identities plus direct-order affine metrics only; "
            "the report does not prove capture provenance, a numeric family, position meaning, "
            "skinning, ownership, completeness, materials, or safe injection"
        ),
    }


def render_correlation_report(report: dict) -> bytes:
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > MAX_CORRELATION_REPORT_BYTES:
        raise CharacterSourceCorrelationError("correlation report exceeds 256 KiB")
    return payload


def write_new_correlation_report(path: Path, report: dict) -> None:
    """Atomically publish a deterministic report without replacing any existing path."""

    if path.is_symlink() or path.exists():
        raise CharacterSourceCorrelationError(
            "correlation output already exists; refusing to overwrite it"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(render_correlation_report(report))
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        os.unlink(temporary)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
