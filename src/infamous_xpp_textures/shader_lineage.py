"""Permanent geometry-to-UV-to-texture lineage for captured character draws."""

from __future__ import annotations

from itertools import permutations
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import tempfile

from .fragment_sampler import analyze_fragment_program_payload
from .runtime_topology_export import (
    RuntimeTopologyExportError,
    _load_bundle,
    _paged_capture_metadata,
    _read_payload,
)
from .vertex_transform import (
    _BRANCH_OPS,
    _SCA_SOURCE_OPS,
    _VEC_SOURCES,
    _field,
    _source_words,
    _walk_reachable,
)


class ShaderLineageError(ValueError):
    """Raised when one proposed character shader lineage is not exact."""


MAX_AUTHORITY_BYTES = 2 * 1024 * 1024
MAX_REPORT_BYTES = 256 * 1024
_COMPONENTS = "xyzw"
_COMPONENTWISE_VEC_OPS = frozenset(
    (1, 2, 3, 4, 9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 25)
)
_FRAGMENT_TEXCOORD_TO_VERTEX_OUTPUT = {
    4: 7,
    5: 8,
    6: 9,
    7: 10,
    8: 11,
    9: 12,
    10: 13,
    11: 14,
    12: 15,
    13: 6,
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _read_pinned_json(path: Path, expected_sha256: str, label: str) -> tuple[dict, str]:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_AUTHORITY_BYTES
    ):
        raise ShaderLineageError(f"{label} must be a regular file at most 2 MiB")
    payload = path.read_bytes()
    actual_sha256 = _sha256(payload)
    if actual_sha256 != expected_sha256:
        raise ShaderLineageError(f"{label} failed its exact SHA-256 pin")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShaderLineageError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ShaderLineageError(f"{label} root must be an object")
    return value, actual_sha256


def _source_tokens(
    raw_source: int,
    input_attribute: int,
    temporary: list[list[set[str]]],
) -> list[set[str]]:
    register_type = raw_source & 3
    swizzle = [_field(raw_source, shift, 2) for shift in (14, 12, 10, 8)]
    if register_type == 1:
        register = _field(raw_source, 2, 6)
        base = temporary[register]
    elif register_type == 2:
        base = [
            {f"input-{input_attribute:02d}.{component}"} for component in _COMPONENTS
        ]
    elif register_type == 3:
        base = [set() for _ in _COMPONENTS]
    else:
        raise ShaderLineageError("reachable vertex source has invalid register type")
    return [set(base[index]) for index in swizzle]


def _vector_dependencies(opcode: int, sources: list[list[set[str]]]) -> list[set[str]]:
    result = [set() for _ in _COMPONENTS]
    if opcode == 0:
        return result
    if opcode in _COMPONENTWISE_VEC_OPS:
        for component in range(4):
            for source in sources:
                result[component].update(source[component])
        return result
    if opcode in (5, 6, 7):
        component_count = {5: 3, 6: 4, 7: 4}[opcode]
        dependencies: set[str] = set()
        for source in sources:
            for component in range(component_count):
                dependencies.update(source[component])
        return [set(dependencies) for _ in _COMPONENTS]
    if opcode == 8:
        # DST: (1, src0.y*src1.y, src0.z, src1.w)
        result[1].update(sources[0][1])
        result[1].update(sources[1][1])
        result[2].update(sources[0][2])
        result[3].update(sources[1][3])
        return result
    raise ShaderLineageError("reachable vertex opcode lacks a lineage rule")


def _write_components(
    destination: list[set[str]],
    values: list[set[str]],
    mask: tuple[bool, bool, bool, bool],
    *,
    conditional: bool,
) -> None:
    for component, enabled in enumerate(mask):
        if enabled:
            if conditional:
                destination[component].update(values[component])
            else:
                destination[component] = set(values[component])


def analyze_vertex_input_lineage(program: bytes) -> dict:
    """Propagate component-level input lineage through one branch-free VP."""

    if len(program) != 544 * 16 + 4:
        raise ShaderLineageError("vertex program has the wrong fixed size")
    unpacked = struct.unpack("<2177I", program)
    words = unpacked[:-1]
    reachable = _walk_reachable(words, unpacked[-1])
    if reachable != tuple(range(reachable[0], reachable[-1] + 1)):
        raise ShaderLineageError("vertex lineage requires one contiguous path")
    temporary = [
        [
            {f"uninitialized-temp-{register:02d}.{component}"}
            for component in _COMPONENTS
        ]
        for register in range(64)
    ]
    outputs = [
        [
            {f"uninitialized-output-{register:02d}.{component}"}
            for component in _COMPONENTS
        ]
        for register in range(32)
    ]

    for pc in reachable:
        d0, d1, d2, d3 = words[pc * 4 : pc * 4 + 4]
        vector_opcode = _field(d1, 22, 5)
        scalar_opcode = _field(d1, 27, 5)
        if scalar_opcode in _BRANCH_OPS or scalar_opcode == 12:
            raise ShaderLineageError(
                "vertex lineage rejects branch/call/return programs"
            )
        condition = _field(d0, 10, 3)
        if condition == 0:
            continue
        conditional = bool(_field(d0, 13, 1)) and condition != 7
        raw_sources = _source_words(d1, d2, d3)
        used = _VEC_SOURCES.get(vector_opcode)
        if used is None:
            raise ShaderLineageError("reachable vertex opcode is not supported")
        input_attribute = _field(d1, 8, 4)
        vector_sources = [
            _source_tokens(raw_sources[number], input_attribute, temporary)
            for number in used
        ]
        vector_values = _vector_dependencies(vector_opcode, vector_sources)
        vector_mask = tuple(bool(_field(d3, bit, 1)) for bit in (16, 15, 14, 13))
        if vector_opcode and any(vector_mask):
            vector_temp = _field(d0, 15, 6)
            if vector_temp != 63:
                _write_components(
                    temporary[vector_temp],
                    vector_values,
                    vector_mask,
                    conditional=conditional,
                )
            if _field(d0, 30, 1):
                output = _field(d3, 2, 5)
                if output != 31:
                    _write_components(
                        outputs[output],
                        vector_values,
                        vector_mask,
                        conditional=conditional,
                    )

        if scalar_opcode in _SCA_SOURCE_OPS:
            scalar_source = _source_tokens(raw_sources[2], input_attribute, temporary)
            scalar_dependencies = set().union(*scalar_source)
            scalar_values = [set(scalar_dependencies) for _ in _COMPONENTS]
            scalar_mask = tuple(bool(_field(d3, bit, 1)) for bit in (20, 19, 18, 17))
            scalar_temp = _field(d3, 7, 6)
            if scalar_temp != 63:
                _write_components(
                    temporary[scalar_temp],
                    scalar_values,
                    scalar_mask,
                    conditional=conditional,
                )
            if not _field(d0, 30, 1):
                output = _field(d3, 2, 5)
                if output != 31:
                    _write_components(
                        outputs[output],
                        scalar_values,
                        scalar_mask,
                        conditional=conditional,
                    )

    return {
        "entry_instruction": unpacked[-1],
        "reachable_instruction_count": len(reachable),
        "branch_free": True,
        "outputs": [
            {
                "register": register,
                "components": [sorted(component) for component in values],
            }
            for register, values in enumerate(outputs)
            if any(
                not all(token.startswith("uninitialized-output-") for token in value)
                for value in values
            )
        ],
        "_outputs": outputs,
    }


def _element_byte_count(attribute: dict) -> int:
    """Return bytes occupied in the packed guest source stream.

    This is deliberately not the padded host-upload size used by a renderer.
    The source-binding path proves that three-component half-float and unorm8
    arrays occupy exactly three source components; any host-side fourth
    component is synthesized after upload. Other formats retain their prior
    conservative size rules until independently proved.
    """

    type_raw = attribute["type"]
    components = attribute["components"]
    if not 1 <= components <= 4:
        raise ShaderLineageError("attribute component count is invalid")
    if type_raw in (1, 5):
        return 2 * (4 if components == 3 else components)
    if type_raw == 2:
        return 4 * components
    if type_raw == 3:
        return 2 * components
    if type_raw == 4:
        return components
    if type_raw == 6:
        return 4
    if type_raw == 7 and components == 4:
        return 4
    raise ShaderLineageError("attribute has an unsupported element layout")


def _finite_numeric_summary(
    payload: bytes, stride: int, offset: int, attribute: dict, count: int
) -> dict | None:
    type_raw = attribute["type"]
    components = attribute["components"]
    if type_raw not in (2, 3):
        return {"finite": True, "minimum": None, "maximum": None}
    values: list[tuple[float, ...]] = []
    try:
        for vertex in range(count):
            start = vertex * stride + offset
            if type_raw == 2:
                row = struct.unpack_from(f">{components}f", payload, start)
            else:
                row = struct.unpack_from(f">{components}e", payload, start)
            if not all(math.isfinite(value) for value in row):
                return None
            values.append(row)
    except struct.error:
        return None
    return {
        "finite": True,
        "minimum": [min(row[index] for row in values) for index in range(components)],
        "maximum": [max(row[index] for row in values) for index in range(components)],
    }


def _reconstruct_attribute_layout(block, payload: bytes) -> dict:
    attributes = list(block.attributes)
    if not 1 <= len(attributes) <= 6:
        raise ShaderLineageError(
            "layout reconstruction accepts one through six attributes"
        )
    if any(item["frequency"] or item["modulo"] for item in attributes):
        raise ShaderLineageError("layout reconstruction requires per-vertex attributes")
    sizes = {item["attribute"]: _element_byte_count(item) for item in attributes}
    if sum(sizes.values()) != block.stride:
        raise ShaderLineageError(
            "attribute element sizes do not completely tile the captured stride"
        )
    candidates = []
    for ordered in permutations(attributes):
        cursor = 0
        layout = []
        valid = True
        for attribute in ordered:
            summary = _finite_numeric_summary(
                payload, block.stride, cursor, attribute, block.range_count
            )
            if summary is None:
                valid = False
                break
            layout.append(
                {
                    "attribute": attribute["attribute"],
                    "type": attribute["type"],
                    "components": attribute["components"],
                    "byte_offset": cursor,
                    "element_bytes": sizes[attribute["attribute"]],
                    "numeric_summary": summary,
                }
            )
            cursor += sizes[attribute["attribute"]]
        if valid:
            candidates.append(layout)
    return {
        "candidate_permutations": math.factorial(len(attributes)),
        "finite_complete_layouts": len(candidates),
        "unique_complete_layout": candidates[0] if len(candidates) == 1 else None,
        "byte_offsets_directly_captured": False,
        "byte_offsets_uniquely_reconstructed": len(candidates) == 1,
        "reconstruction_rule": (
            "packed guest-storage widths tile one captured stride without gaps "
            "or overlap; host-upload padding is not counted; "
            "float payload components must be finite"
        ),
    }


def _texture_matches(
    character_census: dict, side: str, hashes: tuple[str, ...]
) -> list[dict]:
    if character_census.get("format") != "infamous-character-asset-census":
        raise ShaderLineageError("character census has the wrong format")
    descriptors_by_side = character_census.get("target_texture_descriptors")
    if not isinstance(descriptors_by_side, dict) or side not in descriptors_by_side:
        raise ShaderLineageError("character census target side is missing")
    descriptors = descriptors_by_side[side]
    if not isinstance(descriptors, list) or len(descriptors) > 512:
        raise ShaderLineageError("character census descriptors exceed the bound")
    result = []
    for target_hash in hashes:
        matches = []
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                raise ShaderLineageError("character census descriptor is invalid")
            for mip in descriptor.get("mip_rows", []):
                if isinstance(mip, dict) and mip.get("prefix_sha256") == target_hash:
                    matches.append((descriptor, mip))
        if len(matches) != 1:
            raise ShaderLineageError(
                "runtime texture identity does not select one named descriptor"
            )
        descriptor, mip = matches[0]
        result.append(
            {
                "runtime_prefix_sha256": target_hash,
                "descriptor_index": descriptor["index"],
                "name": descriptor["name"],
                "family": descriptor["family"],
                "name_suffix": descriptor["name_suffix"],
                "format": descriptor["format"],
                "width": descriptor["width"],
                "height": descriptor["height"],
                "faces": descriptor["faces"],
                "matched_mip_level": mip["level"],
                "matched_prefix_bytes": mip["prefix_bytes"],
            }
        )
    return result


def build_character_uv_texture_binding(
    bundle: Path,
    texture_allowlist: Path,
    capture_key_exclusion: Path | None,
    source_census_path: Path,
    source_census_sha256: str,
    character_census_path: Path,
    character_census_sha256: str,
    *,
    event_number: int,
    page_number: int,
    record_offset: int,
    character_side: str,
) -> dict:
    """Build one bounded payload-free shader lineage from immutable authorities."""

    if (
        isinstance(event_number, bool)
        or not 1 <= event_number <= 16
        or isinstance(page_number, bool)
        or not 1 <= page_number <= 17
        or isinstance(record_offset, bool)
        or record_offset < 0
        or character_side not in ("left", "right")
    ):
        raise ShaderLineageError("event, page, record offset, or side is invalid")
    try:
        completion, events, allowlist_sha256 = _load_bundle(
            bundle, texture_allowlist, capture_key_exclusion
        )
    except RuntimeTopologyExportError as exc:
        raise ShaderLineageError(str(exc)) from exc
    if completion["format"] not in (
        "if1-texture-bound-topology-v3",
        "if1-texture-bound-topology-v4",
    ):
        raise ShaderLineageError("shader lineage requires a complete v3/v4 bundle")
    event = events.get(event_number)
    if event is None:
        raise ShaderLineageError("selected event is absent from the complete bundle")
    if event.vertex_program_file is None or event.fragment_program_file is None:
        raise ShaderLineageError("selected event lacks exact shader payloads")
    try:
        vertex_program = _read_payload(
            bundle, event.vertex_program_file, 544 * 16 + 4, event.vertex_program_sha256
        )
        fragment_program = _read_payload(
            bundle,
            event.fragment_program_file,
            event.fragment_program_bytes,
            event.fragment_program_sha256,
        )
    except RuntimeTopologyExportError as exc:
        raise ShaderLineageError(str(exc)) from exc

    source_census, source_census_identity = _read_pinned_json(
        source_census_path, source_census_sha256, "source census"
    )
    character_census, character_census_identity = _read_pinned_json(
        character_census_path, character_census_sha256, "character census"
    )
    if (
        source_census.get("kind") != "if1-rsx-paged-xpp-source-census"
        or source_census.get("schema_version") != 1
    ):
        raise ShaderLineageError("source census has the wrong schema")
    source_events = [
        item
        for item in source_census.get("events", [])
        if isinstance(item, dict)
        and item.get("page") == page_number
        and item.get("event") == event_number
    ]
    if len(source_events) != 1:
        raise ShaderLineageError("source census does not select one page/event")
    source_event = source_events[0]
    mapping = source_event.get("mapping")
    if (
        not source_event.get("same_xpp_source_record_proved")
        or not isinstance(mapping, dict)
        or mapping.get("record_offset") != record_offset
    ):
        raise ShaderLineageError("source census does not prove the requested record")
    range_first = mapping.get(
        "range_first", 0 if mapping.get("full_vertex_range") is True else None
    )
    range_count = mapping.get("range_count")
    range_end = mapping.get(
        "range_end",
        range_count
        if mapping.get("full_vertex_range") is True and range_first == 0
        else None,
    )
    source_vertex_count = mapping.get("source_vertex_count")
    full_source_range = (
        mapping.get("full_vertex_range") is True
        and range_first == 0
        and range_count == source_vertex_count
        and range_end == source_vertex_count
    )
    runtime_coverage = mapping.get("runtime_index_coverage")
    partial_source_range = (
        not full_source_range
        and mapping.get("full_vertex_range") is False
        and isinstance(range_first, int)
        and not isinstance(range_first, bool)
        and isinstance(range_count, int)
        and not isinstance(range_count, bool)
        and isinstance(range_end, int)
        and not isinstance(range_end, bool)
        and isinstance(source_vertex_count, int)
        and not isinstance(source_vertex_count, bool)
        and 0 <= range_first < range_end <= source_vertex_count
        and range_count == range_end - range_first
        and isinstance(runtime_coverage, dict)
        and runtime_coverage.get("status") == "retail-triangle-subset-proved"
        and runtime_coverage.get("safe_for_retail_coverage_union") is True
        and runtime_coverage.get("runtime_indices_within_mapped_vertex_range") is True
    )
    if not full_source_range and not partial_source_range:
        raise ShaderLineageError(
            "source census proves neither a full record nor a safe partial range"
        )
    block_number = mapping.get("block")
    matching_blocks = [item for item in event.blocks if item.number == block_number]
    if len(matching_blocks) != 1:
        raise ShaderLineageError("source mapping does not select one captured block")
    block = matching_blocks[0]
    if (
        mapping.get("matched_stream_slice_sha256") != block.payload_sha256
        or mapping.get("stream_zero_record_bytes") != block.stride
        or mapping.get("range_count") != block.range_count
        or (partial_source_range and getattr(block, "range_first", None) != range_first)
    ):
        raise ShaderLineageError("source mapping and captured block identity drifted")
    block_payload = _read_payload(
        bundle, block.payload_file, block.payload_bytes, block.payload_sha256
    )
    packed_layout = _reconstruct_attribute_layout(block, block_payload)

    partial_runtime_receipt = None
    if partial_source_range:
        source_records = source_census.get("source", {}).get("records", [])
        source_record_matches = [
            item
            for item in source_records
            if isinstance(item, dict) and item.get("record_offset") == record_offset
        ]
        if len(source_record_matches) != 1:
            raise ShaderLineageError(
                "source census does not select one partial-range retail record"
            )
        source_record = source_record_matches[0]
        if (
            source_record.get("vertex_count") != source_vertex_count
            or source_record.get("index_count") != mapping.get("source_index_count")
            or source_record.get("index_sha256") != mapping.get("source_index_sha256")
            or not _valid_sha256(source_record.get("index_sha256"))
            or source_record.get("index_count", 0) % 3
        ):
            raise ShaderLineageError(
                "partial-range retail record identity does not reconcile"
            )
        try:
            runtime_index_payload = _read_payload(
                bundle,
                event.index_payload_file,
                event.index_bytes,
                event.index_sha256,
            )
        except RuntimeTopologyExportError as exc:
            raise ShaderLineageError(str(exc)) from exc
        if (
            event.index_bytes != event.index_count * 2
            or event.index_count <= 0
            or event.index_count % 3
            or len(runtime_index_payload) != event.index_bytes
            or runtime_coverage.get("runtime_index_sha256") != event.index_sha256
            or runtime_coverage.get("runtime_triangle_occurrences")
            != event.index_count // 3
            or runtime_coverage.get("covered_retail_triangle_occurrences")
            != event.index_count // 3
            or runtime_coverage.get("unobserved_retail_triangle_occurrences")
            != source_record["index_count"] // 3 - event.index_count // 3
            or not _valid_sha256(
                runtime_coverage.get("covered_triangle_multiset_sha256")
            )
            or not _valid_sha256(
                runtime_coverage.get("unobserved_triangle_multiset_sha256")
            )
        ):
            raise ShaderLineageError(
                "partial-range runtime coverage receipt does not reconcile"
            )
        runtime_indices = struct.unpack(f">{event.index_count}H", runtime_index_payload)
        if (
            not runtime_indices
            or min(runtime_indices) != runtime_coverage.get("runtime_min_vertex_index")
            or max(runtime_indices) != runtime_coverage.get("runtime_max_vertex_index")
            or min(runtime_indices) < range_first
            or max(runtime_indices) >= range_end
        ):
            raise ShaderLineageError(
                "partial-range runtime indices leave the captured source slice"
            )
        partial_runtime_receipt = {
            "runtime_index_sha256": event.index_sha256,
            "runtime_triangle_occurrences": event.index_count // 3,
            "covered_retail_triangle_occurrences": runtime_coverage[
                "covered_retail_triangle_occurrences"
            ],
            "unobserved_retail_triangle_occurrences": runtime_coverage[
                "unobserved_retail_triangle_occurrences"
            ],
            "runtime_min_vertex_index": min(runtime_indices),
            "runtime_max_vertex_index": max(runtime_indices),
            "covered_triangle_multiset_sha256": runtime_coverage[
                "covered_triangle_multiset_sha256"
            ],
            "unobserved_triangle_multiset_sha256": runtime_coverage[
                "unobserved_triangle_multiset_sha256"
            ],
            "safe_for_material_coverage_union": True,
        }

    targets = character_census.get("targets", {})
    target = targets.get(character_side) if isinstance(targets, dict) else None
    source = source_census.get("source")
    if (
        not isinstance(target, dict)
        or not isinstance(source, dict)
        or target.get("sha256") != source.get("source_sha256")
    ):
        raise ShaderLineageError(
            "character and source census target identities disagree"
        )
    named_textures = _texture_matches(
        character_census, character_side, event.target_texture_sha256s
    )
    if any(
        item["faces"] != 1 or not item["width"] or not item["height"]
        for item in named_textures
    ):
        raise ShaderLineageError("target texture is not one proved 2D descriptor")

    fragment = analyze_fragment_program_payload(fragment_program)
    target_instructions = [
        item
        for item in fragment["texture_instructions"]
        if item["sampler"] in event.target_texture_slots
    ]
    if {item["sampler"] for item in target_instructions} != set(
        event.target_texture_slots
    ) or any(not item["coordinate_source_is_input"] for item in target_instructions):
        raise ShaderLineageError(
            "target samplers do not all use one direct fragment input"
        )
    fragment_inputs = {item["fragment_input_attribute"] for item in target_instructions}
    if len(fragment_inputs) != 1:
        raise ShaderLineageError("target samplers use different fragment inputs")
    fragment_input = fragment_inputs.pop()
    if fragment_input not in _FRAGMENT_TEXCOORD_TO_VERTEX_OUTPUT:
        raise ShaderLineageError(
            "target sampler source is not a texture-coordinate input"
        )
    vertex_output = _FRAGMENT_TEXCOORD_TO_VERTEX_OUTPUT[fragment_input]
    vertex = analyze_vertex_input_lineage(vertex_program)
    vertex_outputs = vertex.pop("_outputs")
    swizzles = {tuple(item["coordinate_swizzle"][:2]) for item in target_instructions}
    if len(swizzles) != 1:
        raise ShaderLineageError("target samplers use different 2D coordinate swizzles")
    sampled_output_components = swizzles.pop()
    sampled_lineage = [
        sorted(vertex_outputs[vertex_output][component])
        for component in sampled_output_components
    ]
    expected_input_tokens = []
    for component_lineage in sampled_lineage:
        if len(component_lineage) != 1 or not component_lineage[0].startswith("input-"):
            raise ShaderLineageError(
                "sampled vertex output has ambiguous input lineage"
            )
        expected_input_tokens.append(component_lineage[0])
    input_attributes = {
        int(token.removeprefix("input-").split(".", 1)[0])
        for token in expected_input_tokens
    }
    if len(input_attributes) != 1:
        raise ShaderLineageError(
            "sampled components derive from different vertex inputs"
        )
    vertex_input = input_attributes.pop()
    block_attributes = {
        item["attribute"]: item
        for item in packed_layout["unique_complete_layout"] or []
    }
    selected_layout = block_attributes.get(vertex_input)
    if selected_layout is None or selected_layout["components"] < 2:
        raise ShaderLineageError(
            "sampled vertex input is absent from the source-bound block"
        )

    texture_bindings = []
    by_hash = {item["runtime_prefix_sha256"]: item for item in named_textures}
    for slot, target_hash in zip(
        event.target_texture_slots, event.target_texture_sha256s
    ):
        instruction = [item for item in target_instructions if item["sampler"] == slot]
        if len(instruction) != 1:
            raise ShaderLineageError("target sampler does not have one instruction")
        texture_bindings.append(
            {
                "sampler": slot,
                "instruction": instruction[0]["instruction"],
                "opcode": instruction[0]["opcode"],
                "fragment_input_name": instruction[0]["fragment_input_name"],
                **by_hash[target_hash],
            }
        )
    families = {item["family"] for item in texture_bindings}
    result = {
        "format": "infamous-character-uv-texture-binding",
        "version": 1,
        "tool_inventory_id": "xpp-tool.character-uv-texture-binding.v1",
        "status": (
            "exact-shader-lineage-with-unique-packed-layout"
            if full_source_range
            else "exact-partial-shader-lineage-with-unique-packed-layout"
        ),
        "authorities": {
            "bundle_format": completion["format"],
            "texture_allowlist_sha256": allowlist_sha256,
            "source_census_sha256": source_census_identity,
            "character_census_sha256": character_census_identity,
            "source_xpp_sha256": source["source_sha256"],
            "source_xpp_bytes": source["source_size"],
            "source_xpp_name": source["source"],
            "character_target": target["relative_path"],
        },
        "selection": {
            "page": page_number,
            "event": event_number,
            "draw_event": event.draw_event,
            "record_offset": record_offset,
            "source_stream_index": 0,
            "source_block": block.number,
            "vertex_count": block.range_count,
            "source_stream_stride": block.stride,
            "source_stream_sha256": block.payload_sha256,
            "vertex_program_sha256": event.vertex_program_sha256,
            "fragment_program_sha256": event.fragment_program_sha256,
        },
        "packed_layout": packed_layout,
        "shader_lineage": {
            "fragment_input_attribute": fragment_input,
            "fragment_input_name": target_instructions[0]["fragment_input_name"],
            "vertex_output_register": vertex_output,
            "sampled_output_components": list(sampled_output_components),
            "component_lineage": sampled_lineage,
            "vertex_input_attribute": vertex_input,
            "vertex_input_type": selected_layout["type"],
            "vertex_input_components": selected_layout["components"],
            "vertex_input_byte_offset": selected_layout["byte_offset"],
            "vertex_input_element_bytes": selected_layout["element_bytes"],
            "vertex_input_numeric_summary": selected_layout["numeric_summary"],
            "vertex_program": vertex,
        },
        "texture_bindings": texture_bindings,
        "texture_family": families.pop() if len(families) == 1 else None,
        "proof": {
            "same_xpp_source_record": True,
            "full_source_vertex_range": full_source_range,
            "exact_source_stream_bytes": True,
            "exact_shader_payloads": True,
            "target_sampler_coordinate_input": True,
            "component_level_vertex_lineage": True,
            "named_texture_identity": True,
            "two_dimensional_texture_coordinate_semantic": True,
            "packed_layout_uniquely_reconstructed": True,
            "geometry_to_uv_to_texture_binding": True,
        },
        "limitations": {
            "packed_byte_offsets_directly_captured": False,
            "packed_byte_offsets_derived_by_unique_valid_complete_tiling": True,
            "texture_role_from_name_suffix_only": True,
            "full_character": False,
            "all_material_slots": False,
            "retail_material_export": False,
            "four_x_material_export": False,
            "authored_pbr": False,
            "blender_render": False,
            "rpcs3_mod_round_trip": False,
            "native_decomp_import": False,
        },
        "payload_bytes_serialized": False,
        "next_gate": (
            "admit this safe partial-range lineage to a strict material coverage union "
            "anchored by one compatible full-range material export"
            if partial_source_range
            else (
                "decode the uniquely bound half-float UV rows into a deterministic GLB "
                "TEXCOORD_0 accessor, bind the exact retail color/normal family, and "
                "publish the first material progress render without calling one hair "
                "piece full Zeke"
            )
        ),
    }
    if partial_source_range:
        result["selection"].update(
            {
                "source_vertex_count": source_vertex_count,
                "source_range_first": range_first,
                "source_range_count": range_count,
                "source_range_end": range_end,
            }
        )
        result["proof"].update(
            {
                "partial_source_vertex_range": True,
                "runtime_indices_within_source_range": True,
                "runtime_retail_triangle_subset": True,
                "safe_for_material_coverage_union": True,
            }
        )
        result["partial_runtime_coverage"] = partial_runtime_receipt
    paging = _paged_capture_metadata(completion)
    if paging is not None:
        result["paging"] = paging
    rendered = render_character_uv_texture_binding(result)
    if len(rendered) > MAX_REPORT_BYTES:
        raise ShaderLineageError("shader-lineage report exceeds the 256 KiB bound")
    return result


def render_character_uv_texture_binding(report: dict) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_new_character_uv_texture_binding(path: Path, report: dict) -> None:
    """Publish deterministic lineage bytes without replacing existing evidence."""

    if path.is_symlink() or path.exists():
        raise ShaderLineageError("shader-lineage output already exists")
    payload = render_character_uv_texture_binding(report)
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
            raise ShaderLineageError(
                "shader-lineage output appeared during publication"
            )
        os.link(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
