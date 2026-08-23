"""Deterministic GLB export from an exact repeated-draw material union."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Sequence

from .character_material_export import (
    CharacterMaterialExportError,
    build_character_material_export,
    write_new_character_material_export,
)
from .component_ledger import CharacterComponentLedgerError, _read_pinned
from .material_coverage import (
    MAX_XPP_BYTES,
    MaterialCoverageObservation,
    PartialMaterialCoverageObservation,
    MaterialCoverageUnionError,
    build_material_coverage_union_with_indices,
    render_material_coverage_union,
)


class MaterialCoverageExportError(ValueError):
    """Raised when a material union cannot be exported without weakening proof."""


def build_material_coverage_export(
    xpp_path: Path,
    xpp_sha256: str,
    texture_allowlist: Path,
    observations: Sequence[MaterialCoverageObservation],
    *,
    record_offset: int,
    anchor_lineage: Path,
    anchor_lineage_sha256: str,
    partial_observations: Sequence[PartialMaterialCoverageObservation] = (),
) -> tuple[bytes, dict]:
    """Revalidate one union and export its exact covered triangle multiset."""

    try:
        union_report, union_indices = build_material_coverage_union_with_indices(
            xpp_path,
            xpp_sha256,
            texture_allowlist,
            observations,
            record_offset=record_offset,
            partial_observations=partial_observations,
        )
        union_payload = render_material_coverage_union(union_report)
        union_sha256 = hashlib.sha256(union_payload).hexdigest()
        anchor_rows = [
            item
            for item in union_report["observations"]
            if item.get("lineage_sha256") == anchor_lineage_sha256
        ]
        if len(anchor_rows) != 1:
            raise MaterialCoverageExportError(
                "anchor lineage must identify exactly one union observation"
            )
        anchor_report_sha256 = anchor_rows[0]["material_report_sha256"]
        anchor_observations = [
            item for item in observations if item.report_sha256 == anchor_report_sha256
        ]
        if len(anchor_observations) != 1:
            raise MaterialCoverageExportError(
                "anchor lineage does not reconcile with one supplied observation"
            )
        anchor = anchor_observations[0]
        xpp_data = _read_pinned(xpp_path, xpp_sha256, MAX_XPP_BYTES, "retail XPP")
        glb, report = build_character_material_export(
            xpp_data,
            anchor.bundle,
            texture_allowlist,
            anchor.capture_key_exclusion,
            anchor_lineage,
            anchor_lineage_sha256,
            "observed-only",
            material_indices_override=union_indices,
            material_coverage_union_report=union_report,
            material_coverage_union_sha256=union_sha256,
            tool_inventory_id="xpp-tool.character-material-coverage-export.v1",
        )
    except (
        CharacterComponentLedgerError,
        CharacterMaterialExportError,
        MaterialCoverageUnionError,
    ) as exc:
        raise MaterialCoverageExportError(str(exc)) from exc
    return glb, report


def write_new_material_coverage_export(
    glb_path: Path, report_path: Path, glb: bytes, report: dict
) -> None:
    """Publish the checked GLB/report pair with the existing atomic new-only writer."""

    try:
        write_new_character_material_export(glb_path, report_path, glb, report)
    except CharacterMaterialExportError as exc:
        raise MaterialCoverageExportError(str(exc)) from exc
