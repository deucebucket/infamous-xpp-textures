"""Compare owned character XPPs without trusting descriptor order or offsets."""

from __future__ import annotations

import copy
import hashlib
import json
import string
from collections import defaultdict
from typing import Callable

from .character import build_xpp_character_report
from .runtime import build_runtime_index


class CrossBuildOracleError(ValueError):
    """Raised when an input cannot support a bounded cross-build comparison."""


_LOCATION_KEYS = {
    "record_offset",
    "index_offset",
    "vertex_count_field_offset",
    "parameter_offset",
    "stream_offset",
}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _without_location(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_location(item)
            for key, item in value.items()
            if key not in _LOCATION_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_without_location(item) for item in value]
    return copy.deepcopy(value)


def _match_unique(
    left_count: int,
    right_count: int,
    left_fingerprint: Callable[[int], str],
    right_fingerprint: Callable[[int], str],
) -> dict:
    left_groups: dict[str, list[int]] = defaultdict(list)
    right_groups: dict[str, list[int]] = defaultdict(list)
    for index in range(left_count):
        left_groups[left_fingerprint(index)].append(index)
    for index in range(right_count):
        right_groups[right_fingerprint(index)].append(index)

    mappings: list[dict] = []
    missing_left: list[int] = []
    missing_right: list[int] = []
    ambiguous: list[dict] = []
    for fingerprint in sorted(set(left_groups) | set(right_groups)):
        left = left_groups.get(fingerprint, [])
        right = right_groups.get(fingerprint, [])
        if len(left) == len(right) == 1:
            mappings.append({"left": left[0], "right": right[0]})
        elif not left:
            missing_left.extend(right)
        elif not right:
            missing_right.extend(left)
        else:
            ambiguous.append(
                {
                    "left_indices": left,
                    "right_indices": right,
                    "left_count": len(left),
                    "right_count": len(right),
                }
            )

    mappings.sort(key=lambda item: (item["left"], item["right"]))
    return {
        "unique_matches": len(mappings),
        "reordered_matches": sum(item["left"] != item["right"] for item in mappings),
        "missing_from_left": sorted(missing_left),
        "missing_from_right": sorted(missing_right),
        "ambiguous_groups": ambiguous,
        "mapping": mappings,
        "complete_unique_match": (
            len(mappings) == left_count == right_count
            and not missing_left
            and not missing_right
            and not ambiguous
        ),
    }


def _descriptor_fingerprints(report: dict) -> list[str]:
    descriptors = report.get("descriptors")
    identities = report.get("identities")
    if not isinstance(descriptors, list) or not descriptors:
        raise CrossBuildOracleError("texture report has no descriptors")
    if not isinstance(identities, list) or not identities:
        raise CrossBuildOracleError("texture report has no identities")
    if report.get("structural_status") != "pass":
        raise CrossBuildOracleError("texture report did not pass structural validation")
    if report.get("descriptor_count") != len(descriptors):
        raise CrossBuildOracleError("texture descriptor count does not reconcile")
    if report.get("identity_count") != len(identities):
        raise CrossBuildOracleError("texture identity count does not reconcile")

    by_descriptor: dict[int, list[dict]] = defaultdict(list)
    for identity in identities:
        if not isinstance(identity, dict) or not isinstance(identity.get("descriptor"), int):
            raise CrossBuildOracleError("texture identity has no integer descriptor")
        descriptor_index = identity["descriptor"]
        if not 0 <= descriptor_index < len(descriptors):
            raise CrossBuildOracleError("texture identity descriptor is out of range")
        digest = identity.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in string.hexdigits for character in digest)
            or digest != digest.lower()
        ):
            raise CrossBuildOracleError("texture identity has an invalid SHA-256")
        normalized = {key: value for key, value in identity.items() if key != "descriptor"}
        by_descriptor[descriptor_index].append(normalized)

    unique_hashes = sorted({identity["sha256"] for identity in identities})
    if report.get("unique_hash_count") != len(unique_hashes):
        raise CrossBuildOracleError("texture unique-hash count does not reconcile")
    if report.get("allowlist") != unique_hashes:
        raise CrossBuildOracleError("texture allowlist does not match the identity set")

    fingerprints: list[str] = []
    for expected_index, descriptor in enumerate(descriptors):
        if not isinstance(descriptor, dict) or descriptor.get("index") != expected_index:
            raise CrossBuildOracleError("texture descriptors are not dense and ordered")
        shape = {
            key: descriptor.get(key)
            for key in (
                "format",
                "width",
                "height",
                "faces",
                "mips",
                "chain_bytes_per_face",
                "upload_bytes",
            )
        }
        descriptor_identities = sorted(
            by_descriptor.get(expected_index, []), key=_canonical
        )
        if not descriptor_identities:
            raise CrossBuildOracleError("texture descriptor has no identities")
        fingerprints.append(_canonical({"shape": shape, "identities": descriptor_identities}))
    return fingerprints


def _character_fingerprints(report: dict) -> list[str]:
    contracts = report.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        raise CrossBuildOracleError("character report has no geometry contracts")
    if report.get("contract_coverage") != f"{len(contracts)}/{len(contracts)}":
        raise CrossBuildOracleError("character contract coverage is incomplete")
    if report.get("topology_proved") is not True:
        raise CrossBuildOracleError("character topology is not proved")
    return [_canonical(_without_location(contract)) for contract in contracts]


def _histogram(values: list[int]) -> list[dict]:
    counts: dict[int, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return [{"delta": value, "count": counts[value]} for value in sorted(counts)]


def _location_deltas(left: dict, right: dict, mapping: list[dict]) -> dict:
    record_deltas: list[int] = []
    index_deltas: list[int] = []
    vertex_field_deltas: list[int] = []
    parameter_deltas: list[int] = []
    stream_deltas: list[int] = []
    for match in mapping:
        left_contract = left["contracts"][match["left"]]
        right_contract = right["contracts"][match["right"]]
        record_deltas.append(right_contract["record_offset"] - left_contract["record_offset"])
        index_deltas.append(right_contract["index_offset"] - left_contract["index_offset"])
        vertex_field_deltas.append(
            right_contract["vertex_count_field_offset"]
            - left_contract["vertex_count_field_offset"]
        )
        left_streams = left_contract["packed_vertex_streams"]
        right_streams = right_contract["packed_vertex_streams"]
        if len(left_streams) != len(right_streams):
            raise CrossBuildOracleError("matched character contracts disagree on stream count")
        for left_stream, right_stream in zip(left_streams, right_streams, strict=True):
            parameter_deltas.append(
                right_stream["parameter_offset"] - left_stream["parameter_offset"]
            )
            stream_deltas.append(
                right_stream["stream_offset"] - left_stream["stream_offset"]
            )
    return {
        "record_offset": _histogram(record_deltas),
        "index_offset": _histogram(index_deltas),
        "vertex_count_field_offset": _histogram(vertex_field_deltas),
        "parameter_offset": _histogram(parameter_deltas),
        "stream_offset": _histogram(stream_deltas),
    }


def compare_cross_build_reports(
    left_runtime: dict,
    right_runtime: dict,
    left_character: dict,
    right_character: dict,
    *,
    left_label: str = "left",
    right_label: str = "right",
) -> dict:
    """Reconcile two generated reports using content rather than locations."""
    left_descriptor_fingerprints = _descriptor_fingerprints(left_runtime)
    right_descriptor_fingerprints = _descriptor_fingerprints(right_runtime)
    texture_match = _match_unique(
        len(left_descriptor_fingerprints),
        len(right_descriptor_fingerprints),
        left_descriptor_fingerprints.__getitem__,
        right_descriptor_fingerprints.__getitem__,
    )

    left_contract_fingerprints = _character_fingerprints(left_character)
    right_contract_fingerprints = _character_fingerprints(right_character)
    character_match = _match_unique(
        len(left_contract_fingerprints),
        len(right_contract_fingerprints),
        left_contract_fingerprints.__getitem__,
        right_contract_fingerprints.__getitem__,
    )

    texture_match.update(
        {
            "left_descriptor_count": len(left_descriptor_fingerprints),
            "right_descriptor_count": len(right_descriptor_fingerprints),
            "left_identity_count": left_runtime.get("identity_count"),
            "right_identity_count": right_runtime.get("identity_count"),
            "left_unique_hash_count": left_runtime.get("unique_hash_count"),
            "right_unique_hash_count": right_runtime.get("unique_hash_count"),
            "left_semantic_sha256": _sha256_json(sorted(left_descriptor_fingerprints)),
            "right_semantic_sha256": _sha256_json(sorted(right_descriptor_fingerprints)),
        }
    )
    texture_match["semantic_multiset_equal"] = (
        sorted(left_descriptor_fingerprints) == sorted(right_descriptor_fingerprints)
    )
    texture_match["descriptor_index_portable"] = (
        texture_match["complete_unique_match"]
        and texture_match["reordered_matches"] == 0
    )
    texture_match["exact_texture_allowlist_reusable"] = (
        texture_match["complete_unique_match"]
        and texture_match["semantic_multiset_equal"]
    )

    character_match.update(
        {
            "left_contract_count": len(left_contract_fingerprints),
            "right_contract_count": len(right_contract_fingerprints),
            "left_semantic_sha256": _sha256_json(sorted(left_contract_fingerprints)),
            "right_semantic_sha256": _sha256_json(sorted(right_contract_fingerprints)),
            "left_totals": {
                key: left_character.get(key)
                for key in (
                    "descriptor_local_vertex_count",
                    "index_count",
                    "triangle_count",
                    "packed_stream_count",
                )
            },
            "right_totals": {
                key: right_character.get(key)
                for key in (
                    "descriptor_local_vertex_count",
                    "index_count",
                    "triangle_count",
                    "packed_stream_count",
                )
            },
        }
    )
    character_match["semantic_multiset_equal"] = (
        sorted(left_contract_fingerprints) == sorted(right_contract_fingerprints)
    )
    character_match["location_deltas"] = _location_deltas(
        left_character, right_character, character_match["mapping"]
    )
    character_match["location_independent_contracts_match"] = (
        character_match["complete_unique_match"]
        and character_match["semantic_multiset_equal"]
    )

    audited_match = (
        texture_match["exact_texture_allowlist_reusable"]
        and character_match["location_independent_contracts_match"]
    )
    reordered = texture_match["reordered_matches"] > 0
    if audited_match and reordered:
        verdict = "audited-semantics-match-descriptor-order-diverges"
    elif audited_match:
        verdict = "audited-semantics-match"
    else:
        verdict = "audited-semantics-diverge"

    return {
        "format": "infamous-xpp-cross-build-character-oracle",
        "version": 1,
        "left_label": left_label,
        "right_label": right_label,
        "left_source_sha256": left_runtime.get("source_sha256"),
        "right_source_sha256": right_runtime.get("source_sha256"),
        "texture": texture_match,
        "character": character_match,
        "audited_semantics_match": audited_match,
        "cross_build_repack_authorized": False,
        "character_export_authorized": False,
        "injection_authorized": False,
        "verdict": verdict,
        "limitations": [
            "descriptor indices are build-local whenever reordered_matches is nonzero",
            "exact encoded texture hashes do not prove runtime visibility or scene coverage",
            "packed character streams still lack proved position, normal, UV, weight, joint, and material semantics",
            "matching audited contracts do not authorize copying a rebuilt XPP between builds",
        ],
        "next_gate": (
            "capture and hash one complete decoded retail character vertex stream, then "
            "prove its numeric rule and semantic binding against the matching packed stream"
        ),
    }


def build_cross_build_character_oracle(
    left_data: bytes,
    right_data: bytes,
    *,
    left_label: str = "left",
    right_label: str = "right",
) -> dict:
    """Build both bounded reports and reconcile them without exporting payloads."""
    left_runtime = build_runtime_index(left_data, left_label)
    right_runtime = build_runtime_index(right_data, right_label)
    left_character = build_xpp_character_report(left_data, left_label)
    right_character = build_xpp_character_report(right_data, right_label)
    return compare_cross_build_reports(
        left_runtime,
        right_runtime,
        left_character,
        right_character,
        left_label=left_label,
        right_label=right_label,
    )
