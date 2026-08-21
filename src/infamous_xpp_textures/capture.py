"""Payload-free correlation of skinned XPP topology with RPCS3 RSX captures."""

from __future__ import annotations

import gzip
import hashlib
import struct
from collections import Counter, defaultdict
from pathlib import Path
from typing import BinaryIO

from .character import build_xpp_character_report
from .xpp import parse_xpp


RRC_MAGIC = 0x00435252
RRC_VERSION = 5
RRC_TILE_STATE_BYTES = 432
RRC_MEMORY_BLOCK_BYTES = 16
RRC_DISPLAY_STATE_BYTES = 132
RRC_MAX_MAP_ENTRIES = 2_000_000
RRC_MAX_PAYLOAD_BYTES = 2 * 1024 * 1024 * 1024
RSX_VERTEX_OFFSET_BASE = 0x1680
RSX_VERTEX_FORMAT_BASE = 0x1740
RSX_VERTEX_ARRAY_COUNT = 16
RSX_VERTEX_DATA_BASE_OFFSET = 0x1738
RSX_VERTEX_DATA_BASE_INDEX = 0x173C
RSX_SET_BEGIN_END = 0x1808
RSX_SET_INDEX_ARRAY_ADDRESS = 0x181C
RSX_SET_INDEX_ARRAY_DMA = 0x1820
RSX_DRAW_INDEX_ARRAY = 0x1824
RSX_VERTEX_TYPE_NAMES = {
    1: "snorm16",
    2: "float32",
    3: "float16",
    4: "unorm8",
    5: "sint16",
    6: "cmp32",
    7: "uint8",
}


class RrcCaptureError(ValueError):
    """Raised when an RPCS3 RSX capture does not close structurally."""


class _RrcReader:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.offset = 0
        self.digest = hashlib.sha256()

    def take(self, count: int, label: str) -> bytes:
        if count < 0 or count > RRC_MAX_PAYLOAD_BYTES:
            raise RrcCaptureError(f"RRC {label} length is outside the safety bound")
        try:
            value = self.stream.read(count)
        except OSError as error:
            raise RrcCaptureError(f"cannot decompress RRC {label}: {error}") from error
        if len(value) != count:
            raise RrcCaptureError(
                f"truncated RRC {label} at 0x{self.offset:x}: expected {count}, found {len(value)}"
            )
        self.offset += count
        self.digest.update(value)
        return value

    def u32(self, label: str) -> int:
        return struct.unpack("<I", self.take(4, label))[0]

    def u64(self, label: str) -> int:
        return struct.unpack("<Q", self.take(8, label))[0]

    def vle(self, label: str, maximum: int = RRC_MAX_MAP_ENTRIES) -> int:
        value = 0
        for shift in range(0, 70, 7):
            byte = self.take(1, f"{label} VLE")[0]
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                if value > maximum:
                    raise RrcCaptureError(f"RRC {label} exceeds the safety bound")
                return value
        raise RrcCaptureError(f"RRC {label} VLE is too long")

    def drain(self, label: str) -> int:
        total = 0
        while True:
            try:
                value = self.stream.read(1024 * 1024)
            except OSError as error:
                raise RrcCaptureError(f"cannot finish RRC {label}: {error}") from error
            if not value:
                break
            total += len(value)
            if total > RRC_MAX_PAYLOAD_BYTES:
                raise RrcCaptureError(f"RRC {label} exceeds the safety bound")
            self.offset += len(value)
            self.digest.update(value)
        return total


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _skip_map(reader: _RrcReader, label: str, value_bytes: int) -> tuple[int, set[int]]:
    count = reader.vle(f"{label} count")
    keys: set[int] = set()
    for index in range(count):
        key = reader.u64(f"{label} key {index}")
        if key in keys:
            raise RrcCaptureError(f"RRC {label} contains a duplicate key")
        keys.add(key)
        reader.take(value_bytes, f"{label} value {index}")
    return count, keys


def _rsx_vertex_element_size(type_raw: int, component_count: int) -> int | None:
    if not 1 <= component_count <= 4:
        return None
    if type_raw in (1, 5):
        return 2 * (4 if component_count == 3 else component_count)
    if type_raw == 2:
        return 4 * component_count
    if type_raw == 3:
        return 2 * (4 if component_count == 3 else component_count)
    if type_raw == 4:
        return 4 if component_count == 3 else component_count
    if type_raw == 6:
        return 4
    if type_raw == 7 and component_count == 4:
        return 4
    return None


def _register_value(registers: dict[int, dict], offset: int) -> int | None:
    item = registers.get(offset)
    return item["value"] if item is not None else None


def _build_rsx_draw_state(
    *,
    registers: dict[int, dict],
    primitive_value: int | None,
    indexed_ranges: list[dict],
    matched_records: list[dict],
    memory_blocks: list[dict],
) -> dict:
    """Bind raw RSX vertex descriptors without assigning model semantics."""

    base_offset = _register_value(registers, RSX_VERTEX_DATA_BASE_OFFSET)
    base_index = _register_value(registers, RSX_VERTEX_DATA_BASE_INDEX)
    index_address_word = _register_value(registers, RSX_SET_INDEX_ARRAY_ADDRESS)
    index_dma_word = _register_value(registers, RSX_SET_INDEX_ARRAY_DMA)
    exact_blocks = [block for block in memory_blocks if block["role"] == "exact-index"]
    index_type_raw = (
        (index_dma_word >> 4) & 0xFF if index_dma_word is not None else None
    )
    index_location = index_dma_word & 0xF if index_dma_word is not None else None
    index_offset = (
        index_address_word & 0x1FFFFFFF if index_address_word is not None else None
    )
    index_type_name = {0: "u32", 1: "u16"}.get(index_type_raw)
    index_element_size = {0: 4, 1: 2}.get(index_type_raw)
    total_index_count = sum(item["count"] for item in indexed_ranges)
    index_array_proved = bool(exact_blocks) and all(
        index_location == block["location"]
        and index_offset == block["offset"]
        and index_element_size is not None
        and total_index_count * index_element_size == block["payload_size"]
        for block in exact_blocks
    )

    target_record = matched_records[0] if len(matched_records) == 1 else None
    index_min = target_record["index_min"] if target_record is not None else None
    index_max = target_record["index_max"] if target_record is not None else None
    index_span = (
        index_max - index_min + 1
        if index_min is not None and index_max is not None
        else None
    )
    vertex_arrays: list[dict] = []
    incomplete_attributes: list[int] = []
    unbound_attributes: list[int] = []
    for attribute in range(RSX_VERTEX_ARRAY_COUNT):
        format_offset = RSX_VERTEX_FORMAT_BASE + attribute * 4
        format_state = registers.get(format_offset)
        if format_state is None:
            continue
        format_word = format_state["value"]
        component_count = (format_word >> 4) & 0xF
        if not component_count:
            continue
        type_raw = format_word & 0x7
        stride = (format_word >> 8) & 0xFF
        frequency = (format_word >> 16) & 0xFFFF
        element_size = _rsx_vertex_element_size(type_raw, component_count)
        offset_state = registers.get(RSX_VERTEX_OFFSET_BASE + attribute * 4)
        if (
            offset_state is None
            or base_offset is None
            or element_size is None
            or index_span is None
        ):
            incomplete_attributes.append(attribute)
            continue
        offset_word = offset_state["value"]
        location = offset_word >> 31
        offset = (base_offset + (offset_word & 0x7FFFFFFF)) & 0xFFFFFFFF
        live_offset = offset + index_min * stride
        expected_capture_size = stride * index_span + element_size
        matching_blocks = [
            block
            for block in memory_blocks
            if block["role"] == "unclassified-draw-sibling"
            and block["location"] == location
            and block["offset"] == live_offset
            and block["payload_size"] == expected_capture_size
        ]
        if len(matching_blocks) != 1:
            unbound_attributes.append(attribute)
        vertex_arrays.append(
            {
                "attribute": attribute,
                "semantic": None,
                "type_raw": type_raw,
                "type_name": RSX_VERTEX_TYPE_NAMES.get(type_raw),
                "component_count": component_count,
                "stride": stride,
                "frequency": frequency,
                "element_byte_count": element_size,
                "location": location,
                "offset": offset,
                "live_offset": live_offset,
                "index_span": index_span,
                "expected_capture_size": expected_capture_size,
                "format_observed_at_command": format_state["command_index"],
                "offset_observed_at_command": offset_state["command_index"],
                "matching_memory_blocks": matching_blocks,
                "binding_proved": len(matching_blocks) == 1,
            }
        )

    vertex_binding_proved = (
        index_array_proved
        and bool(vertex_arrays)
        and not incomplete_attributes
        and not unbound_attributes
    )
    return {
        "status": (
            "complete-vertex-binding"
            if vertex_binding_proved
            else "incomplete-register-evidence"
            if incomplete_attributes or not vertex_arrays
            else "unbound-vertex-memory"
        ),
        "primitive_value": primitive_value,
        "indexed_ranges": indexed_ranges,
        "total_index_count": total_index_count,
        "vertex_data_base_offset": base_offset,
        "vertex_data_base_index": base_index,
        "index_array": {
            "offset": index_offset,
            "location": index_location,
            "type_raw": index_type_raw,
            "type_name": index_type_name,
            "element_byte_count": index_element_size,
            "binding_proved": index_array_proved,
        },
        "index_min": index_min,
        "index_max": index_max,
        "index_span": index_span,
        "vertex_arrays": vertex_arrays,
        "active_vertex_attribute_count": len(vertex_arrays),
        "incomplete_vertex_attributes": incomplete_attributes,
        "unbound_vertex_attributes": unbound_attributes,
        "rsx_vertex_binding_proved": vertex_binding_proved,
        "decoded_vertex_semantics_proved": False,
    }


def build_rrc_character_match_report(
    xpp_data: bytes,
    xpp_source_name: str,
    capture_path: Path,
) -> dict:
    """Hash captured memory and match exact, already-proved XPP index streams."""

    target = build_xpp_character_report(xpp_data, xpp_source_name)
    target_by_hash = {item["index_sha256"]: item for item in target["contracts"]}
    parsed_xpp = parse_xpp(xpp_data, len(xpp_data))
    targets_by_size: dict[int, list[tuple[dict, tuple[int, ...]]]] = defaultdict(list)
    for item in target["contracts"]:
        start = parsed_xpp.data_offset + item["index_offset"]
        raw = xpp_data[start : start + item["index_byte_count"]]
        values = struct.unpack(f">{len(raw) // 2}H", raw)
        targets_by_size[len(raw)].append((item, values))
    target_sizes = Counter(item["index_byte_count"] for item in target["contracts"])
    file_size = capture_path.stat().st_size
    file_sha256 = _file_sha256(capture_path)

    try:
        with capture_path.open("rb") as probe:
            gzip_wrapped = probe.read(2) == b"\x1f\x8b"
        stream = (
            gzip.open(capture_path, "rb") if gzip_wrapped else capture_path.open("rb")
        )
    except OSError as error:
        raise RrcCaptureError(f"cannot open RRC capture: {error}") from error

    with stream:
        reader = _RrcReader(stream)
        magic = reader.u32("magic")
        version = reader.u32("version")
        little_endian = reader.u32("endianness")
        if magic != RRC_MAGIC or version != RRC_VERSION or little_endian != 1:
            raise RrcCaptureError(
                f"unsupported RRC identity magic=0x{magic:08x} version={version} little={little_endian}"
            )

        tile_state_count, tile_state_keys = _skip_map(
            reader, "tile-state map", RRC_TILE_STATE_BYTES
        )

        memory_block_count = reader.vle("memory-block map count")
        memory_block_keys: set[int] = set()
        blocks_by_data_state: dict[int, list[dict]] = defaultdict(list)
        blocks_by_key: dict[int, dict] = {}
        for index in range(memory_block_count):
            key = reader.u64(f"memory-block key {index}")
            if key in memory_block_keys:
                raise RrcCaptureError("RRC memory-block map contains a duplicate key")
            memory_block_keys.add(key)
            raw = reader.take(RRC_MEMORY_BLOCK_BYTES, f"memory block {index}")
            offset, location, data_state = struct.unpack("<IIQ", raw)
            block = {
                "block_key": key,
                "offset": offset,
                "location": location,
                "data_state": data_state,
            }
            blocks_by_data_state[data_state].append(block)
            blocks_by_key[key] = block

        memory_payload_count = reader.vle("memory-payload map count")
        memory_payload_keys: set[int] = set()
        exact_matches: list[dict] = []
        transformed_matches: list[dict] = []
        same_size_candidates: Counter[int] = Counter()
        payload_size_histogram: Counter[int] = Counter()
        payload_metadata: dict[int, dict] = {}
        memory_payload_bytes = 0
        for index in range(memory_payload_count):
            key = reader.u64(f"memory-payload key {index}")
            if key in memory_payload_keys:
                raise RrcCaptureError("RRC memory-payload map contains a duplicate key")
            memory_payload_keys.add(key)
            size = reader.vle(
                f"memory-payload {index} size", maximum=RRC_MAX_PAYLOAD_BYTES
            )
            payload = reader.take(size, f"memory payload {index}")
            memory_payload_bytes += size
            if memory_payload_bytes > RRC_MAX_PAYLOAD_BYTES:
                raise RrcCaptureError(
                    "RRC memory payload total exceeds the safety bound"
                )
            payload_size_histogram[size] += 1
            if size in target_sizes:
                same_size_candidates[size] += 1
            digest = hashlib.sha256(payload).hexdigest()
            payload_metadata[key] = {
                "payload_size": size,
                "payload_sha256": digest,
            }
            if digest in target_by_hash:
                contract = target_by_hash[digest]
                exact_indices = struct.unpack(f">{size // 2}H", payload)
                exact_matches.append(
                    {
                        "record_offset": contract["record_offset"],
                        "triangle_count": contract["triangle_count"],
                        "vertex_count": contract["vertex_count"],
                        "index_byte_count": size,
                        "index_count": len(exact_indices),
                        "index_min": min(exact_indices),
                        "index_max": max(exact_indices),
                        "index_sha256": digest,
                        "capture_data_state": key,
                        "memory_blocks": sorted(
                            (
                                {
                                    "block_key": block["block_key"],
                                    "offset": block["offset"],
                                    "location": block["location"],
                                }
                                for block in blocks_by_data_state.get(key, [])
                            ),
                            key=lambda item: (
                                item["location"],
                                item["offset"],
                                item["block_key"],
                            ),
                        ),
                    }
                )
            elif size in targets_by_size:
                value_count = size // 2
                candidate_orders = (
                    ("big-endian", struct.unpack(f">{value_count}H", payload)),
                    ("little-endian", struct.unpack(f"<{value_count}H", payload)),
                )
                for contract, target_values in targets_by_size[size]:
                    winding_values = tuple(
                        value
                        for triangle in zip(
                            target_values[0::3],
                            target_values[1::3],
                            target_values[2::3],
                        )
                        for value in (triangle[0], triangle[2], triangle[1])
                    )
                    for byte_order, candidate_values in candidate_orders:
                        transform = None
                        delta = candidate_values[0] - target_values[0]
                        if candidate_values == target_values:
                            transform = f"{byte_order}-u16-exact"
                            delta = 0
                        elif all(
                            candidate - target == delta
                            for candidate, target in zip(
                                candidate_values, target_values
                            )
                        ):
                            transform = f"{byte_order}-constant-index-delta"
                        else:
                            delta = candidate_values[0] - winding_values[0]
                            if candidate_values == winding_values:
                                transform = f"{byte_order}-winding-swap"
                                delta = 0
                            elif all(
                                candidate - target == delta
                                for candidate, target in zip(
                                    candidate_values, winding_values
                                )
                            ):
                                transform = (
                                    f"{byte_order}-winding-swap-plus-index-delta"
                                )
                        if transform is not None:
                            transformed_matches.append(
                                {
                                    "record_offset": contract["record_offset"],
                                    "triangle_count": contract["triangle_count"],
                                    "vertex_count": contract["vertex_count"],
                                    "index_byte_count": size,
                                    "target_index_sha256": contract["index_sha256"],
                                    "capture_payload_sha256": digest,
                                    "capture_data_state": key,
                                    "transform": transform,
                                    "index_delta": delta,
                                    "memory_blocks": sorted(
                                        (
                                            {
                                                "block_key": block["block_key"],
                                                "offset": block["offset"],
                                                "location": block["location"],
                                            }
                                            for block in blocks_by_data_state.get(
                                                key, []
                                            )
                                        ),
                                        key=lambda item: (
                                            item["location"],
                                            item["offset"],
                                            item["block_key"],
                                        ),
                                    ),
                                }
                            )

        absent_payload_states = sorted(
            state for state in blocks_by_data_state if state not in memory_payload_keys
        )
        if absent_payload_states:
            raise RrcCaptureError(
                f"RRC memory blocks reference {len(absent_payload_states)} absent payload states"
            )

        display_state_count, display_state_keys = _skip_map(
            reader, "display-state map", RRC_DISPLAY_STATE_BYTES
        )
        exact_records_by_block: dict[int, list[dict]] = defaultdict(list)
        for match in exact_matches:
            record = {
                "record_offset": match["record_offset"],
                "index_sha256": match["index_sha256"],
                "index_count": match["index_count"],
                "index_min": match["index_min"],
                "index_max": match["index_max"],
            }
            for block in match["memory_blocks"]:
                exact_records_by_block[block["block_key"]].append(record)

        replay_command_count = reader.vle("replay-command count")
        memory_reference_count = 0
        draw_bindings: list[dict] = []
        registers: dict[int, dict] = {}
        active_primitive: int | None = None
        active_indexed_ranges: list[dict] = []
        for command in range(replay_command_count):
            method_word, value = struct.unpack(
                "<II", reader.take(8, f"replay command {command} method/value")
            )
            state_count = reader.vle(f"replay command {command} memory-state count")
            memory_reference_count += state_count
            state_keys: list[int] = []
            for state in range(state_count):
                key = reader.u64(f"replay command {command} memory state {state}")
                if key not in memory_block_keys:
                    raise RrcCaptureError(
                        "RRC replay command references an absent memory block"
                    )
                state_keys.append(key)
            tile_state = reader.u64(f"replay command {command} tile state")
            display_state = reader.u64(f"replay command {command} display state")
            if tile_state and tile_state not in tile_state_keys:
                raise RrcCaptureError(
                    "RRC replay command references an absent tile state"
                )
            if display_state and display_state not in display_state_keys:
                raise RrcCaptureError(
                    "RRC replay command references an absent display state"
                )
            method_offset = method_word & 0xFFFF
            if (
                RSX_VERTEX_OFFSET_BASE
                <= method_offset
                < RSX_VERTEX_OFFSET_BASE + RSX_VERTEX_ARRAY_COUNT * 4
                or RSX_VERTEX_FORMAT_BASE
                <= method_offset
                < RSX_VERTEX_FORMAT_BASE + RSX_VERTEX_ARRAY_COUNT * 4
                or method_offset
                in {
                    RSX_VERTEX_DATA_BASE_OFFSET,
                    RSX_VERTEX_DATA_BASE_INDEX,
                    RSX_SET_INDEX_ARRAY_ADDRESS,
                    RSX_SET_INDEX_ARRAY_DMA,
                }
            ):
                registers[method_offset] = {
                    "value": value,
                    "command_index": command,
                }

            draw_primitive = active_primitive
            draw_ranges = list(active_indexed_ranges)
            if method_offset == RSX_SET_BEGIN_END:
                if value:
                    active_primitive = value
                    active_indexed_ranges = []
                    draw_primitive = active_primitive
                    draw_ranges = []
                else:
                    draw_primitive = active_primitive
                    draw_ranges = list(active_indexed_ranges)
            elif method_offset == RSX_DRAW_INDEX_ARRAY and active_primitive is not None:
                active_indexed_ranges.append(
                    {
                        "start": value & 0xFFFFFF,
                        "count": ((value >> 24) & 0xFF) + 1,
                        "observed_at_command": command,
                    }
                )
                draw_primitive = active_primitive
                draw_ranges = list(active_indexed_ranges)

            matching_keys = sorted(set(state_keys) & exact_records_by_block.keys())
            if matching_keys:
                matched_record_map = {
                    (record["record_offset"], record["index_sha256"]): record
                    for key in matching_keys
                    for record in exact_records_by_block[key]
                }
                memory_blocks = []
                for key in state_keys:
                    block = blocks_by_key[key]
                    payload = payload_metadata[block["data_state"]]
                    memory_blocks.append(
                        {
                            "block_key": key,
                            "location": block["location"],
                            "offset": block["offset"],
                            "payload_size": payload["payload_size"],
                            "payload_sha256": payload["payload_sha256"],
                            "role": (
                                "exact-index"
                                if key in matching_keys
                                else "unclassified-draw-sibling"
                            ),
                        }
                    )
                memory_blocks = sorted(
                    memory_blocks,
                    key=lambda item: (
                        item["location"],
                        item["offset"],
                        item["block_key"],
                    ),
                )
                matched_records = [
                    matched_record_map[key] for key in sorted(matched_record_map)
                ]
                draw_bindings.append(
                    {
                        "command_index": command,
                        "method_word": method_word,
                        "method_offset": method_offset,
                        "method_count": (method_word >> 18) & 0x7FF,
                        "method_name": (
                            "NV4097_SET_BEGIN_END" if method_offset == 0x1808 else None
                        ),
                        "value": value,
                        "draw_end_boundary": method_offset == 0x1808 and value == 0,
                        "matched_index_records": matched_records,
                        "memory_blocks": memory_blocks,
                        "rsx_draw_state": _build_rsx_draw_state(
                            registers=registers,
                            primitive_value=draw_primitive,
                            indexed_ranges=draw_ranges,
                            matched_records=matched_records,
                            memory_blocks=memory_blocks,
                        ),
                        "unclassified_sibling_count": (
                            len(memory_blocks) - len(matching_keys)
                        ),
                    }
                )
            if method_offset == RSX_SET_BEGIN_END and value == 0:
                active_primitive = None
                active_indexed_ranges = []

        register_state_bytes = reader.drain("register state")
        if not register_state_bytes:
            raise RrcCaptureError("RRC register state is absent")

        decompressed_size = reader.offset
        decompressed_sha256 = reader.digest.hexdigest()

    exact_matches.sort(
        key=lambda item: (item["record_offset"], item["capture_data_state"])
    )
    transformed_matches.sort(
        key=lambda item: (
            item["record_offset"],
            item["capture_data_state"],
            item["transform"],
        )
    )
    matched_hashes = {item["index_sha256"] for item in exact_matches}
    live_draw_binding_proved = any(item["draw_end_boundary"] for item in draw_bindings)
    rsx_vertex_binding_proved = any(
        item["rsx_draw_state"]["rsx_vertex_binding_proved"] for item in draw_bindings
    )
    unmatched = [
        {
            "record_offset": item["record_offset"],
            "triangle_count": item["triangle_count"],
            "vertex_count": item["vertex_count"],
            "index_byte_count": item["index_byte_count"],
            "index_sha256": item["index_sha256"],
        }
        for item in target["contracts"]
        if item["index_sha256"] not in matched_hashes
    ]
    return {
        "format": "infamous-rpcs3-character-capture-match",
        "version": 3,
        "target": {
            "source": target["source"],
            "source_sha256": target["source_sha256"],
            "geometry_contract_count": target["skinned_geometry_contract_count"],
            "contract_coverage": target["contract_coverage"],
        },
        "capture": {
            "source": capture_path.name,
            "file_size": file_size,
            "file_sha256": file_sha256,
            "compression": "gzip" if gzip_wrapped else "none",
            "decompressed_size": decompressed_size,
            "decompressed_sha256": decompressed_sha256,
            "rrc_version": version,
            "tile_state_count": tile_state_count,
            "memory_block_count": memory_block_count,
            "memory_payload_count": memory_payload_count,
            "memory_payload_bytes": memory_payload_bytes,
            "display_state_count": display_state_count,
            "replay_command_count": replay_command_count,
            "memory_reference_count": memory_reference_count,
            "register_state_bytes": register_state_bytes,
        },
        "target_index_sizes": {
            str(size): count for size, count in sorted(target_sizes.items())
        },
        "captured_payloads_at_target_sizes": {
            str(size): count for size, count in sorted(same_size_candidates.items())
        },
        "exact_matches": exact_matches,
        "exact_match_count": len(exact_matches),
        "matched_target_record_count": len(matched_hashes),
        "draw_bindings": draw_bindings,
        "draw_binding_count": len(draw_bindings),
        "live_draw_binding_proved": live_draw_binding_proved,
        "rsx_vertex_binding_proved": rsx_vertex_binding_proved,
        "bounded_transform_matches": transformed_matches,
        "bounded_transform_match_count": len(transformed_matches),
        "unmatched_target_records": unmatched,
        "largest_payload_size_counts": [
            {"bytes": size, "count": count}
            for size, count in sorted(
                payload_size_histogram.items(),
                key=lambda item: (-item[1], -item[0]),
            )[:20]
        ],
        "match_status": (
            "exact-index-draw-binding"
            if live_draw_binding_proved
            else "exact-index-match"
            if exact_matches
            else "no-exact-index-match"
        ),
        "payload_bytes_serialized": False,
        "decoded_vertex_semantics_proved": False,
        "limitations": (
            "an exact index hash binds captured guest memory to one XPP topology record; "
            "a draw-end binding proves that the block was attached to one captured draw but "
            "an RSX vertex binding assigns raw attribute formats and memory extents without "
            "assigning position, normal, UV, joint, or weight semantics; "
            "byte-order/index-delta/winding candidates are reported separately and do not "
            "prove ownership; absence does not prove the character was not visible, and "
            "vertex attributes, draw ownership, skin weights, palettes, transforms, and "
            "semantics remain unproved"
        ),
    }
