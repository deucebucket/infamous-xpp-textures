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


def _skip_map(
    reader: _RrcReader, label: str, value_bytes: int
) -> tuple[int, set[int]]:
    count = reader.vle(f"{label} count")
    keys: set[int] = set()
    for index in range(count):
        key = reader.u64(f"{label} key {index}")
        if key in keys:
            raise RrcCaptureError(f"RRC {label} contains a duplicate key")
        keys.add(key)
        reader.take(value_bytes, f"{label} value {index}")
    return count, keys


def build_rrc_character_match_report(
    xpp_data: bytes,
    xpp_source_name: str,
    capture_path: Path,
) -> dict:
    """Hash captured memory and match exact, already-proved XPP index streams."""

    target = build_xpp_character_report(xpp_data, xpp_source_name)
    target_by_hash = {
        item["index_sha256"]: item for item in target["contracts"]
    }
    parsed_xpp = parse_xpp(xpp_data, len(xpp_data))
    targets_by_size: dict[int, list[tuple[dict, tuple[int, ...]]]] = defaultdict(list)
    for item in target["contracts"]:
        start = parsed_xpp.data_offset + item["index_offset"]
        raw = xpp_data[start : start + item["index_byte_count"]]
        values = struct.unpack(f">{len(raw) // 2}H", raw)
        targets_by_size[len(raw)].append((item, values))
    target_sizes = Counter(
        item["index_byte_count"] for item in target["contracts"]
    )
    file_size = capture_path.stat().st_size
    file_sha256 = _file_sha256(capture_path)

    try:
        with capture_path.open("rb") as probe:
            gzip_wrapped = probe.read(2) == b"\x1f\x8b"
        stream = (
            gzip.open(capture_path, "rb")
            if gzip_wrapped
            else capture_path.open("rb")
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
        for index in range(memory_block_count):
            key = reader.u64(f"memory-block key {index}")
            if key in memory_block_keys:
                raise RrcCaptureError("RRC memory-block map contains a duplicate key")
            memory_block_keys.add(key)
            raw = reader.take(RRC_MEMORY_BLOCK_BYTES, f"memory block {index}")
            offset, location, data_state = struct.unpack("<IIQ", raw)
            blocks_by_data_state[data_state].append(
                {"block_key": key, "offset": offset, "location": location}
            )

        memory_payload_count = reader.vle("memory-payload map count")
        memory_payload_keys: set[int] = set()
        exact_matches: list[dict] = []
        transformed_matches: list[dict] = []
        same_size_candidates: Counter[int] = Counter()
        payload_size_histogram: Counter[int] = Counter()
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
                raise RrcCaptureError("RRC memory payload total exceeds the safety bound")
            payload_size_histogram[size] += 1
            if size in target_sizes:
                same_size_candidates[size] += 1
            digest = hashlib.sha256(payload).hexdigest()
            if digest in target_by_hash:
                contract = target_by_hash[digest]
                exact_matches.append(
                    {
                        "record_offset": contract["record_offset"],
                        "triangle_count": contract["triangle_count"],
                        "vertex_count": contract["vertex_count"],
                        "index_byte_count": size,
                        "index_sha256": digest,
                        "capture_data_state": key,
                        "memory_blocks": sorted(
                            blocks_by_data_state.get(key, []),
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
                                        blocks_by_data_state.get(key, []),
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
        replay_command_count = reader.vle("replay-command count")
        memory_reference_count = 0
        for command in range(replay_command_count):
            reader.take(8, f"replay command {command} method/value")
            state_count = reader.vle(f"replay command {command} memory-state count")
            memory_reference_count += state_count
            for state in range(state_count):
                key = reader.u64(f"replay command {command} memory state {state}")
                if key not in memory_block_keys:
                    raise RrcCaptureError("RRC replay command references an absent memory block")
            tile_state = reader.u64(f"replay command {command} tile state")
            display_state = reader.u64(f"replay command {command} display state")
            if tile_state and tile_state not in tile_state_keys:
                raise RrcCaptureError("RRC replay command references an absent tile state")
            if display_state and display_state not in display_state_keys:
                raise RrcCaptureError("RRC replay command references an absent display state")

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
        "version": 1,
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
            "exact-index-match" if exact_matches else "no-exact-index-match"
        ),
        "payload_bytes_serialized": False,
        "decoded_vertex_semantics_proved": False,
        "limitations": (
            "an exact index hash binds captured guest memory to one XPP topology record; "
            "byte-order/index-delta/winding candidates are reported separately and do not "
            "prove ownership; absence does not prove the character was not visible, and "
            "vertex attributes, draw ownership, skin weights, palettes, transforms, and "
            "semantics remain unproved"
        ),
    }
