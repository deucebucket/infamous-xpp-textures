"""Checksum-pinned progress ledger for multipart character components."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Sequence


MAX_MATERIAL_REPORT_BYTES = 1024 * 1024
MAX_VISUAL_RECEIPT_BYTES = 256 * 1024
MAX_COMPONENTS = 128
MAX_OBSERVATIONS = 256
MAX_RENDERS = 256
MAX_OUTPUT_BYTES = 1024 * 1024


class CharacterComponentLedgerError(ValueError):
    """Raised when component evidence is unsafe, conflicting, or ambiguous."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _read_pinned(path: Path, expected_sha256: str, maximum: int, label: str) -> bytes:
    if not _valid_sha256(expected_sha256):
        raise CharacterComponentLedgerError(f"{label} SHA-256 pin is not canonical")
    if path.is_symlink() or not path.is_file():
        raise CharacterComponentLedgerError(
            f"{label} must be an existing regular non-symlink file"
        )
    size = path.stat().st_size
    if not 0 < size <= maximum:
        raise CharacterComponentLedgerError(
            f"{label} is empty or exceeds the {maximum}-byte bound"
        )
    payload = path.read_bytes()
    if len(payload) != size:
        raise CharacterComponentLedgerError(f"{label} changed while it was read")
    actual = _sha256(payload)
    if actual != expected_sha256:
        raise CharacterComponentLedgerError(
            f"{label} SHA-256 mismatch: expected {expected_sha256}, found {actual}"
        )
    return payload


def _load_json(payload: bytes, label: str) -> dict:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CharacterComponentLedgerError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise CharacterComponentLedgerError(f"{label} root must be an object")
    return value


def _safe_text(value: object, label: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 for character in value)
    ):
        raise CharacterComponentLedgerError(f"{label} is not bounded printable text")
    return value


def _safe_token(value: object, label: str) -> str:
    text = _safe_text(value, label, 64)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", text):
        raise CharacterComponentLedgerError(f"{label} is not a canonical token")
    return text


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CharacterComponentLedgerError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CharacterComponentLedgerError(f"{label} must be a non-negative integer")
    return value


def _bounded_int(value: object, lower: int, upper: int, label: str) -> int:
    result = _nonnegative_int(value, label)
    if not lower <= result <= upper:
        raise CharacterComponentLedgerError(f"{label} is outside its bound")
    return result


def _safe_png_name(value: object, label: str) -> str:
    text = _safe_text(value, label, 180)
    path = PurePosixPath(text)
    if path.is_absolute() or len(path.parts) != 1 or path.suffix.lower() != ".png":
        raise CharacterComponentLedgerError(f"{label} is not a safe PNG basename")
    return text


def _texture_family(textures: list[dict], declared: object) -> str:
    families: set[str] = set()
    for index, texture in enumerate(textures):
        name = _safe_text(texture.get("name"), f"texture {index} name", 256)
        match = re.fullmatch(r"(.+)_([A-Za-z0-9]+)\.psd", name)
        if not match:
            raise CharacterComponentLedgerError(
                "material report lacks a family and texture names cannot derive one"
            )
        families.add(match.group(1))
    if len(families) != 1:
        raise CharacterComponentLedgerError(
            "material report texture names do not share one exact family"
        )
    derived = families.pop()
    if declared is None:
        return derived
    declared_family = _safe_text(declared, "material texture family", 128)
    if declared_family != derived:
        raise CharacterComponentLedgerError(
            "declared material family contradicts exact texture names"
        )
    return declared_family


def _validate_material_report(value: dict, receipt_sha256: str) -> dict:
    tool_inventory_id = value.get("tool_inventory_id")
    union_export = tool_inventory_id == "xpp-tool.character-material-coverage-export.v1"
    if (
        value.get("format") != "infamous-character-material-export"
        or value.get("version") != 1
        or tool_inventory_id
        not in (
            "xpp-tool.character-material-export.v1",
            "xpp-tool.character-material-coverage-export.v1",
        )
        or value.get("status") != "retail-material-progress-glb-written"
    ):
        raise CharacterComponentLedgerError(
            "material report schema/status is unsupported"
        )
    authorities = value.get("authorities")
    selection = value.get("selection")
    glb = value.get("glb")
    textures = value.get("textures")
    proof = value.get("proof")
    limitations = value.get("limitations")
    if not all(
        isinstance(item, dict)
        for item in (authorities, selection, glb, proof, limitations)
    ) or not isinstance(textures, list):
        raise CharacterComponentLedgerError("material report structure is malformed")
    if not 1 <= len(textures) <= 16:
        raise CharacterComponentLedgerError("material texture count is invalid")
    presentation_mode = value.get("presentation_mode")
    if (union_export and presentation_mode != "observed-union") or (
        not union_export
        and presentation_mode not in (None, "observed-only", "preview-full-record")
    ):
        raise CharacterComponentLedgerError(
            "material report presentation mode is unsupported"
        )

    for key in (
        "xpp_sha256",
        "lineage_sha256",
        "texture_allowlist_sha256",
    ):
        if not _valid_sha256(authorities.get(key)):
            raise CharacterComponentLedgerError(
                f"material authority {key} is not canonical"
            )
    capture_key_sha = authorities.get("capture_key_exclusion_sha256")
    if capture_key_sha is not None and not _valid_sha256(capture_key_sha):
        raise CharacterComponentLedgerError(
            "material capture-key authority is not canonical"
        )
    if union_export and not _valid_sha256(authorities.get("coverage_union_sha256")):
        raise CharacterComponentLedgerError(
            "material coverage-union authority is not canonical"
        )

    page = _bounded_int(selection.get("page"), 1, 17, "material page")
    event = _bounded_int(selection.get("event"), 1, 16, "material event")
    record_offset = _nonnegative_int(
        selection.get("record_offset"), "material record offset"
    )
    vertices = _positive_int(selection.get("vertices"), "material vertices")
    triangles = _positive_int(selection.get("triangles"), "material triangles")
    nondegenerate = _positive_int(
        selection.get("nondegenerate_triangles"),
        "material nondegenerate triangle count",
    )
    observed = _nonnegative_int(
        selection.get("material_observed_triangles"),
        "material observed triangle count",
    )
    unobserved = _nonnegative_int(
        selection.get("material_unobserved_triangles"),
        "material unobserved triangle count",
    )
    if triangles != nondegenerate or observed + unobserved != nondegenerate:
        raise CharacterComponentLedgerError(
            "material triangle coverage does not reconcile"
        )
    for key in (
        "index_sha256",
        "material_event_index_sha256",
        "position_payload_sha256",
        "uv_payload_sha256",
    ):
        if not _valid_sha256(selection.get(key)):
            raise CharacterComponentLedgerError(
                f"material selection {key} is not canonical"
            )
    if not _valid_sha256(glb.get("sha256")):
        raise CharacterComponentLedgerError("material GLB SHA-256 is not canonical")
    glb_bytes = _positive_int(glb.get("bytes"), "material GLB byte count")

    normalized_textures: list[dict] = []
    seen_names: set[str] = set()
    seen_descriptors: set[int] = set()
    seen_suffixes: set[str] = set()
    for index, texture in enumerate(textures):
        if not isinstance(texture, dict):
            raise CharacterComponentLedgerError(f"texture {index} is not an object")
        name = _safe_text(texture.get("name"), f"texture {index} name", 256)
        descriptor = _nonnegative_int(
            texture.get("descriptor_index"), f"texture {index} descriptor"
        )
        if name.casefold() in seen_names or descriptor in seen_descriptors:
            raise CharacterComponentLedgerError(
                "material report repeats a texture identity"
            )
        seen_names.add(name.casefold())
        seen_descriptors.add(descriptor)
        suffix = _safe_text(texture.get("suffix"), f"texture {index} suffix", 16)
        if suffix in seen_suffixes:
            raise CharacterComponentLedgerError(
                "material report repeats a texture suffix"
            )
        seen_suffixes.add(suffix)
        hashes = {
            key: texture.get(key)
            for key in (
                "decoded_rgba_sha256",
                "embedded_png_sha256",
                "runtime_prefix_sha256",
            )
        }
        if not all(_valid_sha256(item) for item in hashes.values()):
            raise CharacterComponentLedgerError(
                f"texture {index} has a non-canonical hash"
            )
        normalized_textures.append(
            {
                "descriptor_index": descriptor,
                "name": name,
                "suffix": suffix,
                "width": _positive_int(texture.get("width"), f"texture {index} width"),
                "height": _positive_int(
                    texture.get("height"), f"texture {index} height"
                ),
                **hashes,
            }
        )
    normalized_textures.sort(key=lambda row: (row["suffix"], row["name"]))
    family = _texture_family(textures, selection.get("texture_family"))

    required_proof = (
        "deterministic_material_glb",
        "exact_full_vertex_range",
        "exact_retail_topology",
        "exact_uv_rows",
        "runtime_prefix_to_retail_descriptor",
        "shader_proved_texcoord_0",
    )
    if not all(proof.get(key) is True for key in required_proof):
        raise CharacterComponentLedgerError(
            "material report is missing required positive proof"
        )
    required_limits = (
        "full_character",
        "four_x_textures",
        "native_pbr",
        "rigged",
        "rpcs3_mod_round_trip",
        "native_decomp_import",
    )
    if not all(limitations.get(key) is False for key in required_limits):
        raise CharacterComponentLedgerError(
            "material report overclaims a component delivery/completion gate"
        )
    full_material_coverage = limitations.get("full_topology_material_coverage")
    if not isinstance(full_material_coverage, bool):
        raise CharacterComponentLedgerError(
            "material coverage limitation is not boolean"
        )
    if full_material_coverage != (unobserved == 0):
        raise CharacterComponentLedgerError(
            "material coverage flag contradicts triangle counts"
        )
    coverage_union = None
    if union_export:
        coverage_union = value.get("coverage_union")
        if not isinstance(coverage_union, dict):
            raise CharacterComponentLedgerError(
                "material coverage-union receipt is malformed"
            )
        if (
            coverage_union.get("receipt_sha256") != authorities["coverage_union_sha256"]
            or coverage_union.get("covered_retail_triangle_occurrences") != observed
            or coverage_union.get("unobserved_retail_triangle_occurrences")
            != unobserved
            or coverage_union.get("full_retail_material_coverage_proved")
            is not (unobserved == 0)
            or not _valid_sha256(coverage_union.get("covered_triangle_multiset_sha256"))
            or not _valid_sha256(
                coverage_union.get("unobserved_triangle_multiset_sha256")
            )
            or selection.get("material_union_index_sha256")
            != coverage_union.get("covered_triangle_multiset_sha256")
            or proof.get("coverage_union_revalidated") is not True
            or proof.get("exact_union_triangle_material_subset") is not True
        ):
            raise CharacterComponentLedgerError(
                "material coverage-union proof does not reconcile"
            )

    return {
        "tool_inventory_id": tool_inventory_id,
        "receipt_sha256": receipt_sha256,
        "page": page,
        "event": event,
        "record_offset": record_offset,
        "draw_event": _nonnegative_int(
            selection.get("draw_event"), "material draw-event identity"
        ),
        "source": {
            "xpp_bytes": _positive_int(
                authorities.get("xpp_bytes"), "source XPP bytes"
            ),
            "xpp_sha256": authorities["xpp_sha256"],
            "position_payload_sha256": selection["position_payload_sha256"],
        },
        "lineage_sha256": authorities["lineage_sha256"],
        "index_sha256": selection["index_sha256"],
        "material_event_index_sha256": selection["material_event_index_sha256"],
        **(
            {
                "material_union_index_sha256": selection["material_union_index_sha256"],
                "coverage_union": coverage_union,
            }
            if union_export
            else {}
        ),
        "texture_family": family,
        "topology": {
            "vertices": vertices,
            "triangles": triangles,
            "material_observed_triangles": observed,
            "material_unobserved_triangles": unobserved,
        },
        "uv": {
            "byte_offset": _nonnegative_int(
                selection.get("uv_byte_offset"), "material UV byte offset"
            ),
            "payload_sha256": selection["uv_payload_sha256"],
        },
        "glb": {"bytes": glb_bytes, "sha256": glb["sha256"]},
        "textures": normalized_textures,
        "proof": {
            "source_record": True,
            "runtime_topology": True,
            "exact_uv": True,
            "retail_texture_binding": True,
            "retail_material_glb": True,
            "full_material_coverage": full_material_coverage,
        },
    }


def _validate_visual_receipts(
    value: dict,
    *,
    title_id: str,
    build_id: str,
    candidate_id: str,
) -> list[dict]:
    if (
        value.get("format") != "infamous-character-visual-baseline-receipts"
        or value.get("version") != 1
        or value.get("title_id") != title_id
        or value.get("build_id") != build_id
        or value.get("candidate_id") != candidate_id
    ):
        raise CharacterComponentLedgerError(
            "visual baseline receipt scope/schema is unsupported"
        )
    rows = value.get("renders")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_RENDERS:
        raise CharacterComponentLedgerError("visual baseline receipt count is invalid")
    result: list[dict] = []
    seen: set[tuple[int, int, str, str]] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CharacterComponentLedgerError(
                f"visual receipt {index} is not an object"
            )
        page = _bounded_int(row.get("page"), 1, 17, f"visual receipt {index} page")
        record_offset = _nonnegative_int(
            row.get("record_offset"), f"visual receipt {index} record offset"
        )
        variant = _safe_token(row.get("variant"), f"visual receipt {index} variant")
        mode = row.get("material_scope")
        if mode not in {"strict-observed-only", "preview-full-record"}:
            raise CharacterComponentLedgerError(
                f"visual receipt {index} material scope is unsupported"
            )
        accepted = row.get("accepted_visual_baseline")
        if not isinstance(accepted, bool):
            raise CharacterComponentLedgerError(
                f"visual receipt {index} accepted flag is not boolean"
            )
        image = row.get("image")
        if not isinstance(image, dict) or not _valid_sha256(image.get("sha256")):
            raise CharacterComponentLedgerError(
                f"visual receipt {index} image identity is malformed"
            )
        normalized = {
            "page": page,
            "record_offset": record_offset,
            "variant": variant,
            "material_scope": mode,
            "accepted_visual_baseline": accepted,
            "acceptance_note": _safe_text(
                row.get("acceptance_note"),
                f"visual receipt {index} acceptance note",
                300,
            ),
            "image": {
                "name": _safe_png_name(
                    image.get("name"), f"visual receipt {index} image name"
                ),
                "bytes": _positive_int(
                    image.get("bytes"), f"visual receipt {index} image bytes"
                ),
                "sha256": image["sha256"],
                "width": _positive_int(
                    image.get("width"), f"visual receipt {index} image width"
                ),
                "height": _positive_int(
                    image.get("height"), f"visual receipt {index} image height"
                ),
            },
        }
        identity = (page, record_offset, variant, image["sha256"])
        if identity in seen:
            raise CharacterComponentLedgerError("visual receipts contain a duplicate")
        seen.add(identity)
        result.append(normalized)
    return sorted(
        result,
        key=lambda row: (
            row["page"],
            row["record_offset"],
            row["variant"],
            row["image"]["sha256"],
        ),
    )


def build_character_component_ledger(
    material_reports: Sequence[tuple[Path, str]],
    *,
    title_id: str,
    build_id: str,
    candidate_id: str,
    visual_receipts: tuple[Path, str] | None = None,
) -> dict:
    """Reconcile exact component material reports into one canonical ledger."""

    title_id = _safe_token(title_id, "title ID")
    build_id = _safe_token(build_id, "build ID")
    candidate_id = _safe_token(candidate_id, "candidate ID")
    if not 1 <= len(material_reports) <= MAX_OBSERVATIONS:
        raise CharacterComponentLedgerError("material report count is invalid")

    input_receipts: list[dict] = []
    observations: list[dict] = []
    seen_paths: set[Path] = set()
    seen_receipts: set[str] = set()
    for index, (path, expected_sha256) in enumerate(material_reports):
        resolved = path.resolve()
        if resolved in seen_paths:
            raise CharacterComponentLedgerError("material report path is duplicated")
        seen_paths.add(resolved)
        payload = _read_pinned(
            path,
            expected_sha256,
            MAX_MATERIAL_REPORT_BYTES,
            f"material report {index}",
        )
        receipt_sha = _sha256(payload)
        if receipt_sha in seen_receipts:
            raise CharacterComponentLedgerError("material report content is duplicated")
        seen_receipts.add(receipt_sha)
        observations.append(
            _validate_material_report(
                _load_json(payload, f"material report {index}"), receipt_sha
            )
        )
        input_receipts.append(
            {"kind": "material-report", "sha256": receipt_sha, "bytes": len(payload)}
        )

    render_rows: list[dict] = []
    if visual_receipts is not None:
        visual_path, visual_sha256 = visual_receipts
        if visual_path.resolve() in seen_paths:
            raise CharacterComponentLedgerError(
                "visual receipt path duplicates a material report"
            )
        payload = _read_pinned(
            visual_path,
            visual_sha256,
            MAX_VISUAL_RECEIPT_BYTES,
            "visual baseline receipts",
        )
        render_rows = _validate_visual_receipts(
            _load_json(payload, "visual baseline receipts"),
            title_id=title_id,
            build_id=build_id,
            candidate_id=candidate_id,
        )
        input_receipts.append(
            {
                "kind": "visual-baseline-receipts",
                "sha256": _sha256(payload),
                "bytes": len(payload),
            }
        )

    components: dict[tuple[int, int], dict] = {}
    observation_ids: set[tuple[int, int, int, str]] = set()
    for observation in observations:
        observation_id = (
            observation["page"],
            observation["event"],
            observation["record_offset"],
            observation["lineage_sha256"],
        )
        if observation_id in observation_ids:
            raise CharacterComponentLedgerError(
                "material reports repeat one event/record/lineage observation"
            )
        observation_ids.add(observation_id)
        key = (observation["page"], observation["record_offset"])
        component = components.get(key)
        immutable = {
            "xpp_bytes": observation["source"]["xpp_bytes"],
            "xpp_sha256": observation["source"]["xpp_sha256"],
            "position_payload_sha256": observation["source"]["position_payload_sha256"],
            "vertices": observation["topology"]["vertices"],
        }
        if component is None:
            component = {
                "component_id": (
                    f"{title_id}:{build_id}:{candidate_id}:p{key[0]}:r{key[1]}"
                ),
                "page": key[0],
                "record_offset": key[1],
                "source": immutable,
                "texture_families": set(),
                "observations": [],
                "renders": [],
            }
            components[key] = component
        elif component["source"] != immutable:
            raise CharacterComponentLedgerError(
                "material reports conflict on immutable component geometry"
            )
        component["texture_families"].add(observation["texture_family"])
        component["observations"].append(observation)

    unknown_render_components: list[tuple[int, int]] = []
    for render in render_rows:
        key = (render["page"], render["record_offset"])
        if key not in components:
            unknown_render_components.append(key)
            continue
        components[key]["renders"].append(render)
    if unknown_render_components:
        raise CharacterComponentLedgerError(
            "visual receipt references a component without material evidence"
        )

    normalized_components: list[dict] = []
    for key in sorted(components):
        component = components[key]
        component["observations"].sort(
            key=lambda row: (row["event"], row["lineage_sha256"])
        )
        component["renders"].sort(
            key=lambda row: (row["variant"], row["image"]["sha256"])
        )
        full_coverage = all(
            row["proof"]["full_material_coverage"] for row in component["observations"]
        )
        accepted_baseline = any(
            row["accepted_visual_baseline"] for row in component["renders"]
        )
        normalized_components.append(
            {
                **component,
                "texture_families": sorted(component["texture_families"]),
                "status": "partial-material-component",
                "completion": {
                    "source_record_proved": True,
                    "runtime_topology_proved": True,
                    "exact_uv_proved": True,
                    "retail_texture_binding_proved": True,
                    "retail_material_glb_written": True,
                    "full_material_coverage_proved": full_coverage,
                    "accepted_visual_baseline": accepted_baseline,
                    "four_x_textures_complete": False,
                    "authored_pbr_complete": False,
                    "rig_and_skin_complete": False,
                    "full_character_assembly_complete": False,
                    "rpcs3_mod_round_trip_complete": False,
                    "native_decomp_import_complete": False,
                },
            }
        )
    if len(normalized_components) > MAX_COMPONENTS:
        raise CharacterComponentLedgerError("component count exceeds the bound")

    incomplete_coverage = [
        row["component_id"]
        for row in normalized_components
        if not row["completion"]["full_material_coverage_proved"]
    ]
    accepted_baselines = sum(
        row["completion"]["accepted_visual_baseline"] for row in normalized_components
    )
    report = {
        "format": "infamous-character-component-progress-ledger",
        "version": 1,
        "tool_inventory_id": "xpp-tool.character-component-ledger.v1",
        "scope": {
            "title_id": title_id,
            "build_id": build_id,
            "candidate_id": candidate_id,
            "short_goal": "editable retail-compatible RPCS3 character mods",
            "long_goal": "the same canonical components imported by the native decomp",
            "partial_render_blocks_other_publication": False,
            "game_payload_serialized": False,
            "private_paths_serialized": False,
        },
        "input_receipts": sorted(
            input_receipts, key=lambda row: (row["kind"], row["sha256"])
        ),
        "counts": {
            "components": len(normalized_components),
            "material_observations": len(observations),
            "published_render_receipts": len(render_rows),
            "accepted_visual_baselines": accepted_baselines,
            "full_material_coverage_components": (
                len(normalized_components) - len(incomplete_coverage)
            ),
            "full_character_assemblies": 0,
            "rpcs3_mod_round_trips": 0,
            "native_decomp_imports": 0,
        },
        "components": normalized_components,
        "next_missing_evidence": {
            "class": "component-material-coverage",
            "component_ids": incomplete_coverage,
            "why": (
                "these source/runtime/UV/retail-binding components still contain "
                "triangles whose own material draw is not proved"
            ),
            "does_not_repeat_completed_components": True,
        },
        "completion_truth": {
            "full_character": False,
            "four_x_textures": False,
            "authored_pbr": False,
            "rigged_and_skinned": False,
            "rpcs3_mod_round_trip": False,
            "native_decomp_import": False,
        },
    }
    payload = render_character_component_ledger(report)
    if len(payload) > MAX_OUTPUT_BYTES:
        raise CharacterComponentLedgerError("component ledger exceeds the byte bound")
    return report


def render_character_component_ledger(report: dict) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_new_character_component_ledger(path: Path, report: dict) -> None:
    """Atomically publish a deterministic component ledger without replacement."""

    if path.is_symlink() or path.exists():
        raise CharacterComponentLedgerError("component ledger output already exists")
    payload = render_character_component_ledger(report)
    if len(payload) > MAX_OUTPUT_BYTES:
        raise CharacterComponentLedgerError("component ledger exceeds the byte bound")
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
            raise CharacterComponentLedgerError(
                "component ledger output appeared during publication"
            )
        os.link(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
