"""Fail-closed cross-build oracle for one character material-coverage gap."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .character import build_xpp_character_report
from .cross_build import compare_cross_build_reports
from .runtime import build_runtime_index


MAX_XPP_BYTES = 64 * 1024 * 1024
MAX_COVERAGE_UNION_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 256 * 1024


class MaterialGapOracleError(ValueError):
    """Raised when evidence cannot prove one portable cross-build topology gap."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MaterialGapOracleError(f"{label} is not a bounded integer")
    return value


def _object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise MaterialGapOracleError(f"{label} is not an object")
    return value


def _array(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise MaterialGapOracleError(f"{label} is not an array")
    return value


def _label(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise MaterialGapOracleError(f"{name} must be 1 to 64 safe characters")
    if any(
        not (character.isascii() and (character.isalnum() or character in "._-"))
        for character in value
    ):
        raise MaterialGapOracleError(f"{name} contains an unsafe character")
    return value


def _validate_coverage_union(
    report: dict,
    *,
    left_source_sha256: str,
    left_source_bytes: int,
) -> dict:
    if (
        report.get("format") != "infamous-character-material-coverage-union"
        or report.get("version") != 1
        or report.get("tool_inventory_id")
        != "xpp-tool.character-material-coverage-union.v1"
        or report.get("payload_bytes_serialized") is not False
    ):
        raise MaterialGapOracleError("coverage union has the wrong schema")

    authorities = _object(report.get("authorities"), "coverage union authorities")
    component = _object(report.get("component"), "coverage union component")
    union = _object(report.get("union"), "coverage union result")
    observations = _array(report.get("observations"), "coverage union observations")

    if (
        authorities.get("xpp_sha256") != left_source_sha256
        or authorities.get("xpp_bytes") != left_source_bytes
        or not _valid_sha256(authorities.get("retail_index_sha256"))
        or not _valid_sha256(authorities.get("texture_allowlist_sha256"))
    ):
        raise MaterialGapOracleError("coverage union source authorities drifted")

    record_offset = _integer(component.get("record_offset"), "record offset")
    vertices = _integer(component.get("vertices"), "vertex count", minimum=1)
    triangles = _integer(
        component.get("retail_triangle_occurrences"),
        "retail triangle count",
        minimum=1,
    )
    texture_family = component.get("texture_family")
    texture_names = _array(component.get("texture_names"), "texture names")
    if (
        not isinstance(texture_family, str)
        or not texture_family
        or len(texture_family) > 256
        or not 1 <= len(texture_names) <= 32
        or any(
            not isinstance(name, str)
            or not name
            or len(name) > 256
            or "/" in name
            or "\\" in name
            or any(ord(character) < 0x20 for character in name)
            for name in texture_names
        )
        or len(set(texture_names)) != len(texture_names)
    ):
        raise MaterialGapOracleError("coverage union texture family is malformed")

    observation_count = _integer(
        union.get("observation_count"), "observation count", minimum=1
    )
    covered = _integer(
        union.get("covered_retail_triangle_occurrences"), "covered triangle count"
    )
    unobserved = _integer(
        union.get("unobserved_retail_triangle_occurrences"),
        "unobserved triangle count",
    )
    full = union.get("full_retail_material_coverage_proved")
    status = report.get("status")
    if (
        observation_count != len(observations)
        or observation_count > 16
        or covered + unobserved != triangles
        or covered > triangles
        or full is not (unobserved == 0)
        or status
        != (
            "full-retail-material-coverage-proved"
            if full
            else "partial-retail-material-coverage-proved"
        )
        or not _valid_sha256(union.get("covered_triangle_multiset_sha256"))
        or not _valid_sha256(union.get("unobserved_triangle_multiset_sha256"))
    ):
        raise MaterialGapOracleError("coverage union counts do not reconcile")

    return {
        "record_offset": record_offset,
        "vertices": vertices,
        "triangles": triangles,
        "texture_family": texture_family,
        "texture_names": list(texture_names),
        "retail_index_sha256": authorities["retail_index_sha256"],
        "texture_allowlist_sha256": authorities["texture_allowlist_sha256"],
        "observation_count": observation_count,
        "covered": covered,
        "unobserved": unobserved,
        "full": full,
        "covered_sha256": union["covered_triangle_multiset_sha256"],
        "unobserved_sha256": union["unobserved_triangle_multiset_sha256"],
    }


def compare_cross_build_material_gap(
    left_character: dict,
    right_character: dict,
    cross_build: dict,
    coverage_union: dict,
    *,
    coverage_union_sha256: str,
    coverage_union_bytes: int,
    left_source_sha256: str,
    left_source_bytes: int,
    right_source_sha256: str,
    right_source_bytes: int,
    left_label: str = "left",
    right_label: str = "right",
) -> dict:
    """Prove that one source-build material gap selects identical target topology."""

    left_label = _label(left_label, "left label")
    right_label = _label(right_label, "right label")
    if left_label == right_label:
        raise MaterialGapOracleError("left and right labels must be distinct")
    if not all(
        _valid_sha256(value)
        for value in (
            coverage_union_sha256,
            left_source_sha256,
            right_source_sha256,
        )
    ):
        raise MaterialGapOracleError("an input SHA-256 is invalid")
    _integer(coverage_union_bytes, "coverage union byte count", minimum=1)
    _integer(left_source_bytes, "left XPP byte count", minimum=1)
    _integer(right_source_bytes, "right XPP byte count", minimum=1)

    union = _validate_coverage_union(
        coverage_union,
        left_source_sha256=left_source_sha256,
        left_source_bytes=left_source_bytes,
    )
    if (
        cross_build.get("format") != "infamous-xpp-cross-build-character-oracle"
        or cross_build.get("version") != 1
        or cross_build.get("audited_semantics_match") is not True
        or cross_build.get("left_source_sha256") != left_source_sha256
        or cross_build.get("right_source_sha256") != right_source_sha256
    ):
        raise MaterialGapOracleError("cross-build character semantics are not proved")

    left_contracts = _array(left_character.get("contracts"), "left contracts")
    right_contracts = _array(right_character.get("contracts"), "right contracts")
    left_matches = [
        (index, contract)
        for index, contract in enumerate(left_contracts)
        if isinstance(contract, dict)
        and contract.get("record_offset") == union["record_offset"]
    ]
    if len(left_matches) != 1:
        raise MaterialGapOracleError(
            "coverage union does not select one left geometry contract"
        )
    left_index, left_contract = left_matches[0]
    mappings = _array(
        _object(cross_build.get("character"), "cross-build character result").get(
            "mapping"
        ),
        "cross-build character mapping",
    )
    mapping = [row for row in mappings if row.get("left") == left_index]
    if len(mapping) != 1:
        raise MaterialGapOracleError(
            "coverage union geometry contract has no unique target mapping"
        )
    right_index = _integer(mapping[0].get("right"), "right contract index")
    if right_index >= len(right_contracts) or not isinstance(
        right_contracts[right_index], dict
    ):
        raise MaterialGapOracleError("right geometry mapping is out of range")
    right_contract = right_contracts[right_index]

    for label, contract in (("left", left_contract), ("right", right_contract)):
        if (
            contract.get("triangle_count") != union["triangles"]
            or contract.get("vertex_count") != union["vertices"]
            or contract.get("index_count") != union["triangles"] * 3
            or contract.get("index_byte_count") != union["triangles"] * 6
            or not _valid_sha256(contract.get("index_sha256"))
        ):
            raise MaterialGapOracleError(
                f"{label} geometry contract conflicts with the coverage union"
            )
    if left_contract.get("index_sha256") != union["retail_index_sha256"]:
        raise MaterialGapOracleError("left retail index identity drifted")

    exact_index_stream = (
        left_contract["index_sha256"] == right_contract["index_sha256"]
        and left_contract["index_byte_count"] == right_contract["index_byte_count"]
    )
    if not exact_index_stream:
        raise MaterialGapOracleError(
            "target build does not contain the identical retail index stream"
        )

    texture = _object(cross_build.get("texture"), "cross-build texture result")
    target_binding_proved = False
    report = {
        "format": "infamous-character-material-gap-cross-build-oracle",
        "version": 1,
        "tool_inventory_id": "xpp-tool.character-material-gap-oracle.v1",
        "status": "exact-topology-gap-portable-runtime-material-binding-unproved",
        "authorities": {
            "coverage_union_bytes": coverage_union_bytes,
            "coverage_union_sha256": coverage_union_sha256,
            "left_label": left_label,
            "left_xpp_bytes": left_source_bytes,
            "left_xpp_sha256": left_source_sha256,
            "right_label": right_label,
            "right_xpp_bytes": right_source_bytes,
            "right_xpp_sha256": right_source_sha256,
        },
        "component": {
            "texture_family": union["texture_family"],
            "texture_names": union["texture_names"],
            "left_record_offset": left_contract["record_offset"],
            "right_record_offset": right_contract["record_offset"],
            "vertices": union["vertices"],
            "retail_triangle_occurrences": union["triangles"],
            "retail_index_sha256": union["retail_index_sha256"],
        },
        "source_coverage": {
            "observation_count": union["observation_count"],
            "covered_retail_triangle_occurrences": union["covered"],
            "unobserved_retail_triangle_occurrences": union["unobserved"],
            "full_retail_material_coverage_proved": union["full"],
            "covered_triangle_multiset_sha256": union["covered_sha256"],
            "unobserved_triangle_multiset_sha256": union["unobserved_sha256"],
        },
        "cross_build": {
            "character_contract_mapping": {
                "left": left_index,
                "right": right_index,
            },
            "character_contracts_match": True,
            "exact_retail_index_stream_identical": True,
            "topology_gap_identity_portable": True,
            "texture_allowlist_reusable": texture.get(
                "exact_texture_allowlist_reusable"
            )
            is True,
            "descriptor_index_portable": texture.get("descriptor_index_portable")
            is True,
            "target_runtime_material_observation_present": False,
            "target_runtime_material_binding_proved": target_binding_proved,
        },
        "proof": {
            "coverage_union_schema_and_counts_revalidated": True,
            "coverage_source_xpp_identity_revalidated": True,
            "coverage_record_maps_uniquely": True,
            "exact_index_stream_matches_target": True,
            "covered_and_unobserved_topology_identities_apply_to_target": True,
        },
        "payload_bytes_serialized": False,
        "limitations": {
            "coverage_union_observations_replayed": False,
            "target_runtime_material_binding_proved": target_binding_proved,
            "descriptor_indices_portable": texture.get("descriptor_index_portable")
            is True,
            "full_character": False,
            "rigged": False,
            "four_x_textures": False,
            "authored_pbr": False,
            "rpcs3_mod_round_trip": False,
            "native_decomp_import": False,
        },
        "verdict": (
            "the target build has the identical component topology and the same "
            "unresolved face identity; it does not supply new material-binding "
            "evidence for those faces"
        ),
        "next_gate": (
            "capture a target-build draw only if deployment is separately authorized, "
            "or resolve the same remaining face multiset from another source-build draw"
        ),
    }
    if len(render_material_gap_oracle(report)) > MAX_OUTPUT_BYTES:
        raise MaterialGapOracleError("material-gap oracle exceeds the output bound")
    return report


def build_cross_build_material_gap_oracle(
    left_data: bytes,
    right_data: bytes,
    coverage_union_payload: bytes,
    *,
    left_label: str = "left",
    right_label: str = "right",
) -> dict:
    """Build the bounded reports and reconcile one coverage gap without payloads."""

    if not 0 < len(left_data) <= MAX_XPP_BYTES:
        raise MaterialGapOracleError("left XPP exceeds the byte bound")
    if not 0 < len(right_data) <= MAX_XPP_BYTES:
        raise MaterialGapOracleError("right XPP exceeds the byte bound")
    if not 0 < len(coverage_union_payload) <= MAX_COVERAGE_UNION_BYTES:
        raise MaterialGapOracleError("coverage union exceeds the byte bound")
    try:
        coverage_union = json.loads(coverage_union_payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterialGapOracleError("coverage union is not valid JSON") from exc
    if not isinstance(coverage_union, dict):
        raise MaterialGapOracleError("coverage union root is not an object")

    left_sha256 = _sha256(left_data)
    right_sha256 = _sha256(right_data)
    left_runtime = build_runtime_index(left_data, left_label)
    right_runtime = build_runtime_index(right_data, right_label)
    left_character = build_xpp_character_report(left_data, left_label)
    right_character = build_xpp_character_report(right_data, right_label)
    cross_build = compare_cross_build_reports(
        left_runtime,
        right_runtime,
        left_character,
        right_character,
        left_label=left_label,
        right_label=right_label,
    )
    return compare_cross_build_material_gap(
        left_character,
        right_character,
        cross_build,
        coverage_union,
        coverage_union_sha256=_sha256(coverage_union_payload),
        coverage_union_bytes=len(coverage_union_payload),
        left_source_sha256=left_sha256,
        left_source_bytes=len(left_data),
        right_source_sha256=right_sha256,
        right_source_bytes=len(right_data),
        left_label=left_label,
        right_label=right_label,
    )


def read_bounded_regular(path: Path, *, limit: int, label: str) -> bytes:
    """Read one immutable regular non-symlink input under an explicit byte cap."""

    if path.is_symlink() or not path.is_file():
        raise MaterialGapOracleError(f"{label} must be a regular non-symlink file")
    size = path.stat().st_size
    if not 0 < size <= limit:
        raise MaterialGapOracleError(f"{label} exceeds the byte bound")
    payload = path.read_bytes()
    if len(payload) != size:
        raise MaterialGapOracleError(f"{label} changed while it was read")
    return payload


def render_material_gap_oracle(report: dict) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_new_material_gap_oracle(path: Path, report: dict) -> None:
    """Atomically publish a deterministic report without replacing evidence."""

    if path.is_symlink() or path.exists():
        raise MaterialGapOracleError("material-gap oracle output already exists")
    payload = render_material_gap_oracle(report)
    if len(payload) > MAX_OUTPUT_BYTES:
        raise MaterialGapOracleError("material-gap oracle exceeds the output bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise MaterialGapOracleError(
                "material-gap oracle output appeared during publication"
            )
        os.link(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
