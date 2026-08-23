"""Command-line interface for if1-tex."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .asset_inventory import (
    AssetInventoryError,
    build_asset_completion_inventory,
    write_new_asset_completion_inventory,
)
from .character import (
    CharacterReportError,
    build_character_compatibility_report,
    render_report,
)
from .character_asset_census import (
    CharacterAssetCensusError,
    build_character_asset_census,
    write_new_character_asset_census,
)
from .capture import RrcCaptureError, build_rrc_character_match_report
from .character_export import (
    CharacterDiagnosticExportError,
    export_character_diagnostic_glb,
)
from .character_material_export import (
    CharacterMaterialExportError,
    build_character_material_export,
    write_new_character_material_export,
)
from .character_material_census import (
    CharacterMaterialCensusError,
    build_character_material_candidate_census,
    write_new_character_material_candidate_census,
)
from .component_ledger import (
    CharacterComponentLedgerError,
    build_character_component_ledger,
    write_new_character_component_ledger,
)
from .material_coverage import (
    MaterialCoverageObservation,
    MaterialCoverageUnionError,
    PartialMaterialCoverageObservation,
    build_material_coverage_union,
    write_new_material_coverage_union,
)
from .material_coverage_export import (
    MaterialCoverageExportError,
    build_material_coverage_export,
    write_new_material_coverage_export,
)
from .material_pass_census import (
    MaterialPassCensusError,
    build_material_pass_census,
    write_new_material_pass_census,
)
from .character_source_export import (
    NUMERIC_FAMILIES,
    CharacterSourceExportError,
    export_character_source_diagnostic_glb,
)
from .character_source_correlation import (
    MAX_RUNTIME_ARRAY_BYTES,
    MAX_RUNTIME_INDEX_BYTES,
    CharacterSourceCorrelationError,
    correlate_character_source_runtime,
    read_bounded_regular_file,
    regular_file_identity,
    render_correlation_report,
    write_new_correlation_report,
)
from .cross_build import CrossBuildOracleError, build_cross_build_character_oracle
from .decode import extract_package, load_xpp_bytes
from .derive import derive_scaled
from .mesh import MeshExportError, export_glb, find_mesh_sections
from .oracle import build_profile_oracle
from .page_correlation import (
    MAX_PAGE_CORRELATION_REPORT_BYTES,
    PageCorrelationError,
    correlate_paged_draw_families,
)
from .pack import (
    PackError,
    pack_replacements,
    replacements_from_dir,
    replacements_from_scale,
)
from .pipeline import build_profile, extract_profile, validate_profile
from .pngio import read_png
from .position_replay import PositionReplayError, export_position_replay_glb
from .psarc import extract_entry, rebuild_archive
from .rebase import RebaseError, rebase_texture_edits
from .runtime import build_replacement_bundle, build_runtime_index, write_allowlist
from .runtime_topology_export import (
    RuntimeTopologyExportError,
    census_runtime_fragment_samplers,
    export_runtime_topology_glb,
    write_capture_key_exclusion,
)
from .screen_page_merge import export_screen_replay_pages_glb
from .screen_replay import ScreenReplayError, export_screen_replay_glb
from .shader_lineage import (
    ShaderLineageError,
    build_character_uv_texture_binding,
    write_new_character_uv_texture_binding,
)
from .source_correlation import (
    MAX_SOURCE_CORRELATION_REPORT_BYTES,
    MAX_XPP_SOURCE_BYTES,
    SourceCorrelationError,
    correlate_paged_draws_to_xpp,
)
from .validation import ValidationError, compare_xpp, validate_xpp
from .vertex_transform import (
    VertexTransformCensusError,
    analyze_vertex_transform_bundle,
)
from .xpp import parse_xpp


def _add_source(p: argparse.ArgumentParser, required: bool = True) -> None:
    src = p.add_mutually_exclusive_group(required=required)
    src.add_argument("--xpp", type=Path, help="already-extracted .xpp file")
    src.add_argument("--psarc", type=Path, help="PSARC archive that contains the .xpp")
    p.add_argument("--entry", help="path inside the PSARC, e.g. /A16.xpp")


def _add_capture_key_exclusion(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--capture-key-exclusion",
        type=Path,
        help="required exact prior capture-key manifest for a paged v4 bundle",
    )


def _candidate_pair(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("candidate must be EVENT:RECORD_OFFSET")
    try:
        event, record_offset = (int(part, 10) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "candidate must contain decimal integers"
        ) from exc
    if not 1 <= event <= 16 or record_offset < 0:
        raise argparse.ArgumentTypeError("candidate event or record offset is invalid")
    return event, record_offset


def _load(args: argparse.Namespace) -> tuple[bytes, str]:
    if getattr(args, "psarc", None) is not None and not args.entry:
        raise SystemExit("--psarc requires --entry")
    return load_xpp_bytes(
        xpp=args.xpp, psarc=getattr(args, "psarc", None), entry=args.entry
    )


def cmd_list(args: argparse.Namespace) -> int:
    data, stem = _load(args)
    found, _ = extract_package(data, stem, Path("."), list_only=True, index=args.index)
    return 0 if found else 1


def cmd_extract(args: argparse.Namespace) -> int:
    data, stem = _load(args)
    found, written = extract_package(
        data,
        stem,
        args.outdir,
        level=args.level,
        index=args.index,
        max_count=args.max,
    )
    return 0 if written else (0 if found else 1)


def cmd_extract_all(args: argparse.Namespace) -> int:
    root = args.xpp_dir
    paths = sorted(root.rglob("*.xpp"))
    if not paths:
        print(f"no .xpp files under {root}", file=sys.stderr)
        return 1
    total_found = total_written = 0
    for path in paths:
        print(f"\n== {path} ==")
        data = path.read_bytes()
        rel = path.relative_to(root)
        stem = str(rel.with_suffix("")).replace("/", "_")
        found, written = extract_package(data, stem, args.outdir, level=args.level)
        total_found += found
        total_written += written
    print(
        f"\nall packages: {len(paths)} files, {total_found} textures, {total_written} PNGs"
    )
    return 0 if total_written else 1


def cmd_verify(args: argparse.Namespace) -> int:
    data, _ = _load(args)
    try:
        summary, records = validate_xpp(data)
    except ValidationError as error:
        print(f"verify failed: {error}", file=sys.stderr)
        return 1
    print(f"descriptors: {summary['descriptors']}")
    print(
        f"layout pairs: {summary['layout_pairs']}/{summary['layout_pairs']}  "
        "(complete, nonoverlapping, 128-byte padded)"
    )
    print("mip counts: explicit/embedded match")
    return 0 if records else 1


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        report = compare_xpp(
            args.retail.read_bytes(),
            args.candidate.read_bytes(),
            known_pass_extra=args.known_pass_extra,
            known_fail_extra=args.known_fail_extra,
        )
    except (OSError, ValidationError) as error:
        print(f"validate failed: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("structural preflight: PASS")
        print(
            f"descriptors: {report['candidate']['descriptors']}  "
            f"promoted: {report['promoted_records']}"
        )
        print(
            f"extra texture-chain bytes: {report['chain_delta_bytes']:+,}  "
            f"padded: {report['padded_chain_delta_bytes']:+,}  "
            f"package: {report['package_delta_bytes']:+,}"
        )
        print(f"startup-path budget: {report['budget']['status']}")
        print(
            "scene coverage: REQUIRED; structural success does not prove a texture is safe when used"
        )
    if (
        args.fail_on_budget
        and report["budget"]["status"] == "at-or-above-observed-startup-fail-range"
    ):
        return 2
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    data, stem = _load(args)
    replacements: dict[int, tuple[int, int, bytes]] = {}
    if args.scale:
        replacements.update(replacements_from_scale(data, args.scale))
    if args.from_dir:
        replacements.update(replacements_from_dir(args.stem or stem, args.from_dir))
    for item in args.replace or []:
        if "=" not in item:
            raise SystemExit("--replace needs INDEX=file.png")
        idx_s, path_s = item.split("=", 1)
        w, h, rgba = read_png(Path(path_s))
        replacements[int(idx_s, 0)] = (w, h, rgba)
    if not replacements:
        raise SystemExit("nothing to pack: pass --replace, --from-dir, or --scale")
    try:
        out = pack_replacements(
            data,
            replacements,
            allow_resize=args.allow_resize or bool(args.scale),
        )
    except (PackError, ValueError) as exc:
        print(f"pack failed: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(out)
    print(f"wrote {args.out}  ({len(out):,} bytes)  replaced {sorted(replacements)}")
    return 0


def cmd_derive(args: argparse.Namespace) -> int:
    try:
        result, changed, total = derive_scaled(
            args.retail.read_bytes(),
            args.source.read_bytes(),
            target_scale=args.target_scale,
            include_indices=set(args.include_index),
            exclude_indices=set(args.exclude_index),
            max_upscaled=args.max_upscaled,
        )
    except (PackError, ValueError) as exc:
        print(f"derive failed: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(result)
    print(
        f"wrote {args.out}  ({len(result):,} bytes)  "
        f"derived {changed}/{total} texture records at {args.target_scale}x"
    )
    return 0


def cmd_texture_rebase(args: argparse.Namespace) -> int:
    try:
        protected_inputs = [args.source_retail, args.source_candidate]
        if args.target_retail is not None:
            protected_inputs.append(args.target_retail)
        if args.target_psarc is not None:
            protected_inputs.append(args.target_psarc)
        same_path = args.out.resolve() in {path.resolve() for path in protected_inputs}
        same_file = args.out.exists() and any(
            path.exists() and args.out.samefile(path) for path in protected_inputs
        )
        if same_path or same_file:
            raise RebaseError("--out must not overwrite a source or target retail XPP")
        if args.target_psarc is not None:
            if not args.target_entry:
                raise RebaseError("--target-psarc requires --target-entry")
            target_retail = extract_entry(args.target_psarc, args.target_entry)
        else:
            if args.target_entry:
                raise RebaseError("--target-entry is only valid with --target-psarc")
            target_retail = args.target_retail.read_bytes()
        output, report = rebase_texture_edits(
            args.source_retail.read_bytes(),
            args.source_candidate.read_bytes(),
            target_retail,
            include_indices=(set(args.include_index) if args.include_index else None),
            allow_zero_change=args.allow_zero_change,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{args.out.name}.", dir=args.out.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(output)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, args.out)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
    except (OSError, RebaseError, ValueError) as error:
        print(f"texture-rebase failed: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"wrote {args.out}: {report['mapped_record_count']} exact retail-identity "
            f"mapping(s), {report['target_unchanged_records_verified']} untouched target records"
        )
        print(
            "target package remained the structural base; runtime proof is still required"
        )
    return 0


def cmd_psarc_pack(args: argparse.Namespace) -> int:
    paths = sorted(args.xpp_dir.glob("*.xpp"))
    if args.include:
        wanted = set(args.include)
        paths = [path for path in paths if path.name in wanted]
        missing = wanted - {path.name for path in paths}
        if missing:
            print(f"missing replacement XPPs: {sorted(missing)}", file=sys.stderr)
            return 1
    if not paths:
        print(f"no replacement XPPs in {args.xpp_dir}", file=sys.stderr)
        return 1
    try:
        result = rebuild_archive(
            args.psarc,
            args.out,
            {path.name: path.read_bytes() for path in paths},
            compression_level=args.compression_level,
            require_all=bool(args.include),
        )
    except (OSError, ValueError) as exc:
        print(f"psarc-pack failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"wrote {args.out}  ({result['bytes']:,} bytes)  "
        f"replaced {result['replaced']}/{result['entries']} entries  "
        f"ignored {result['ignored']} replacements for other archives"
    )
    return 0


def cmd_profile_extract(args: argparse.Namespace) -> int:
    try:
        manifest = extract_profile(
            args.install1,
            args.install2,
            args.outdir,
            progress=lambda message: print(message, flush=True),
        )
    except (OSError, ValueError) as exc:
        print(f"profile-extract failed: {exc}", file=sys.stderr)
        return 1
    package_count = sum(
        1
        for archive in manifest["archives"]
        for entry in archive["entries"]
        if "extracted" in entry
    )
    print(
        f"wrote {args.outdir} with {package_count} XPP/XPPS files and "
        f"{sum(archive['entries_with_manifest'] for archive in manifest['archives'])} audited PSARC entries"
    )
    return 0


def cmd_profile_build(args: argparse.Namespace) -> int:
    try:
        manifest = build_profile(
            args.install1,
            args.install2,
            args.xpp_dir,
            args.outdir,
            compression_level=args.compression_level,
            known_pass_extra=args.known_pass_extra,
            known_fail_extra=args.known_fail_extra,
            fail_on_budget=args.fail_on_budget,
            progress=lambda message: print(message, flush=True),
        )
    except (OSError, ValueError) as exc:
        print(f"profile-build failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"wrote verified profile {args.outdir}: {manifest['replacement_count']} replacements, "
        f"{sum(archive['entries_audited'] for archive in manifest['archives'])} audited PSARC entries"
    )
    return 0


def cmd_profile_validate(args: argparse.Namespace) -> int:
    try:
        report = validate_profile(
            args.install1,
            args.install2,
            args.xpp_dir,
            known_pass_extra=args.known_pass_extra,
            known_fail_extra=args.known_fail_extra,
            progress=(
                None if args.json else lambda message: print(message, flush=True)
            ),
        )
    except (OSError, ValueError) as error:
        print(f"profile-validate failed: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"strict preflight PASS: {report['replacement_count']} replacements, "
            f"{report['promoted_records']} promoted records"
        )
        print(
            f"extra texture-chain bytes: {report['chain_delta_bytes']:+,}; "
            f"startup-path budget: {report['budget']['status']}"
        )
        print("scene coverage: REQUIRED")
    if (
        args.fail_on_budget
        and report["budget"]["status"] == "at-or-above-observed-startup-fail-range"
    ):
        return 2
    return 0


def cmd_profile_oracle(args: argparse.Namespace) -> int:
    try:
        report = build_profile_oracle(
            args.left_install1,
            args.left_install2,
            args.right_install1,
            args.right_install2,
            left_label=args.left_label,
            right_label=args.right_label,
            compare_bytes=not args.catalog_only,
            progress=(
                (lambda message: print(message, flush=True))
                if args.json_out is not None
                else None
            ),
        )
    except (OSError, ValueError) as error:
        print(f"profile-oracle failed: {error}", file=sys.stderr)
        return 1
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_out is None:
        print(rendered, end="")
        return 0
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(rendered, encoding="utf-8")
    pair = report["pair"]
    byte_summary = (
        "not compared"
        if pair["byte_identical_shared_packages"] is None
        else (
            f"{pair['byte_identical_shared_packages']} byte-identical, "
            f"{pair['changed_shared_packages']} changed"
        )
    )
    print(
        f"profile oracle: {pair['shared_full_names']} shared package names, "
        f"{byte_summary}; verdict {report['verdict']}"
    )
    print(f"wrote {args.json_out}")
    return 0


def cmd_runtime_index(args: argparse.Namespace) -> int:
    try:
        data, stem = _load(args)
        report = build_runtime_index(data, args.label or stem)
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if args.allowlist_out:
            write_allowlist(args.allowlist_out, report)
    except (OSError, ValidationError, ValueError) as error:
        print(f"runtime-index failed: {error}", file=sys.stderr)
        return 1

    if not args.json_out and not args.allowlist_out:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"runtime index: {report['descriptor_count']} descriptors, "
            f"{report['unique_hash_count']} unique exact hashes"
        )
        if args.json_out:
            print(f"wrote {args.json_out}")
        if args.allowlist_out:
            print(f"wrote {args.allowlist_out}")
    print(
        "scene coverage: exact misses may also mean the game transformed texels before upload"
    )
    return 0


def cmd_runtime_bundle(args: argparse.Namespace) -> int:
    try:
        report = build_replacement_bundle(
            args.retail.read_bytes(),
            args.candidate.read_bytes(),
            set(args.index),
            args.outdir,
            label=args.label or args.candidate.stem,
        )
    except (OSError, ValidationError, ValueError) as error:
        print(f"runtime-bundle failed: {error}", file=sys.stderr)
        return 1
    print(
        f"wrote {args.outdir}: {report['replacement_count']} hash-bound "
        "host texture replacement(s)"
    )
    print("guest XPP allocation: retail; candidate texture allocation: host GPU only")
    return 0


def _add_budget_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--known-startup-pass-extra",
        "--known-pass-extra",
        dest="known_pass_extra",
        type=int,
        help="observed startup-path passing extra chain bytes",
    )
    parser.add_argument(
        "--known-startup-fail-extra",
        "--known-fail-extra",
        dest="known_fail_extra",
        type=int,
        help="observed startup-path failing extra chain bytes",
    )
    parser.add_argument(
        "--fail-on-budget",
        action="store_true",
        help="exit 2/refuse build at or above the observed startup-fail bound",
    )


def cmd_mesh_list(args: argparse.Namespace) -> int:
    data, _ = _load(args)
    pkg = parse_xpp(data, len(data))
    try:
        sections = find_mesh_sections(data, pkg)
    except MeshExportError as exc:
        print(f"mesh-list: {exc}", file=sys.stderr)
        return 1
    if not sections:
        print(
            "no static mesh sections (character/skinned packages are not this format)"
        )
        return 1
    for s in sections:
        print(
            f"0x{s.record_offset:x}  tris={s.triangle_count} verts={s.vertex_count} "
            f"oid=0x{s.oid:x} material@0x{s.material_offset:x}"
        )
    print(f"{len(sections)} static section(s)")
    return 0


def cmd_mesh_export(args: argparse.Namespace) -> int:
    data, _ = _load(args)
    offsets = set(args.record_offset) if args.record_offset else None
    try:
        result = export_glb(
            data, args.output, record_offsets=offsets, texture_path=args.texture
        )
    except MeshExportError as exc:
        print(f"mesh-export: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_character_report(args: argparse.Namespace) -> int:
    try:
        data, stem = _load(args)
        external_data = args.external.read_bytes() if args.external else None
        report = build_character_compatibility_report(
            data,
            f"{stem}.xpp",
            external_data,
            args.external.name if args.external else None,
        )
    except (OSError, CharacterReportError, ValueError) as exc:
        print(f"character-report: {exc}", file=sys.stderr)
        return 1
    rendered = render_report(report)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


def cmd_character_oracle(args: argparse.Namespace) -> int:
    try:
        if args.left_xpp.resolve() == args.right_xpp.resolve():
            raise CrossBuildOracleError(
                "left and right XPP inputs must be different files"
            )
        if args.json_out is not None:
            output = args.json_out.resolve()
            if output in {args.left_xpp.resolve(), args.right_xpp.resolve()}:
                raise CrossBuildOracleError("JSON output must not overwrite an input")
            if args.json_out.exists():
                raise FileExistsError(f"output already exists: {args.json_out}")
        report = build_cross_build_character_oracle(
            args.left_xpp.read_bytes(),
            args.right_xpp.read_bytes(),
            left_label=args.left_label,
            right_label=args.right_label,
        )
        rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.json_out is not None:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            with args.json_out.open("x", encoding="utf-8") as output_file:
                output_file.write(rendered)
    except (OSError, CrossBuildOracleError, ValidationError, ValueError) as exc:
        print(f"character-oracle: {exc}", file=sys.stderr)
        return 1

    if args.json_out is None:
        print(rendered, end="")
    else:
        print(
            "character oracle: "
            f"{report['texture']['unique_matches']} texture descriptors, "
            f"{report['character']['unique_matches']} character contracts; "
            f"verdict {report['verdict']}"
        )
        print(f"wrote {args.json_out}")
    return 0 if report["audited_semantics_match"] else 2


def cmd_character_capture_report(args: argparse.Namespace) -> int:
    try:
        data, stem = _load(args)
        report = build_rrc_character_match_report(data, f"{stem}.xpp", args.rrc)
    except (OSError, RrcCaptureError, ValueError) as exc:
        print(f"character-capture-report: {exc}", file=sys.stderr)
        return 1
    rendered = render_report(report)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


def cmd_character_diagnostic_export(args: argparse.Namespace) -> int:
    try:
        source_paths = [
            path
            for path in (
                args.xpp,
                args.psarc,
                args.binding_report,
                args.attribute_payload,
            )
            if path is not None
        ]
        destination_paths = [args.output]
        if args.json_out is not None:
            destination_paths.append(args.json_out)
        if len({path.resolve() for path in destination_paths}) != len(
            destination_paths
        ):
            raise CharacterDiagnosticExportError(
                "--output and --json-out must be different paths"
            )
        for destination in destination_paths:
            if any(
                destination.resolve() == source.resolve()
                or (
                    destination.exists()
                    and source.exists()
                    and destination.samefile(source)
                )
                for source in source_paths
            ):
                raise CharacterDiagnosticExportError(
                    "diagnostic outputs must not overwrite an input"
                )
        data, _ = _load(args)
        binding_report = json.loads(args.binding_report.read_text(encoding="utf-8"))
        result = export_character_diagnostic_glb(
            data,
            binding_report,
            args.attribute_payload.read_bytes(),
            args.output,
            position_hypothesis_attribute=args.position_hypothesis_attribute,
        )
    except (
        OSError,
        json.JSONDecodeError,
        CharacterDiagnosticExportError,
        ValueError,
    ) as exc:
        print(f"character-diagnostic-export: {exc}", file=sys.stderr)
        return 1
    rendered = render_report(result)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


def cmd_character_source_diagnostic_export(args: argparse.Namespace) -> int:
    try:
        source_paths = [path for path in (args.xpp, args.psarc) if path is not None]
        destination_paths = [args.output]
        if args.json_out is not None:
            destination_paths.append(args.json_out)
        if len({path.resolve() for path in destination_paths}) != len(
            destination_paths
        ):
            raise CharacterSourceExportError(
                "--output and --json-out must be different paths"
            )
        for destination in destination_paths:
            if destination.is_symlink() or destination.exists():
                raise CharacterSourceExportError(
                    "diagnostic output already exists; refusing to overwrite it"
                )
            if any(
                destination.resolve() == source.resolve() for source in source_paths
            ):
                raise CharacterSourceExportError(
                    "diagnostic outputs must not overwrite an input"
                )
        data, _ = _load(args)
        result = export_character_source_diagnostic_glb(
            data,
            args.output,
            record_offset=args.record_offset,
            stream_index=args.stream_index,
            numeric_family=args.numeric_family,
        )
        rendered = render_report(result)
        if args.json_out is not None:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            with args.json_out.open("x", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
    except (OSError, CharacterSourceExportError, ValueError) as exc:
        print(f"character-source-diagnostic-export: {exc}", file=sys.stderr)
        return 1
    print(rendered, end="")
    return 0


def cmd_character_source_runtime_correlate(args: argparse.Namespace) -> int:
    try:
        source_paths = [path for path in (args.xpp, args.psarc) if path is not None]
        source_path = source_paths[0]
        source_identity = regular_file_identity(source_path, "XPP/PSARC source")
        input_paths = source_paths + [args.runtime_index, args.runtime_positions]
        if args.output.is_symlink() or args.output.exists():
            raise CharacterSourceCorrelationError(
                "correlation output already exists; refusing to overwrite it"
            )
        if any(args.output.resolve() == path.resolve() for path in input_paths):
            raise CharacterSourceCorrelationError(
                "correlation output must not overwrite an input"
            )
        runtime_index = read_bounded_regular_file(
            args.runtime_index, MAX_RUNTIME_INDEX_BYTES, "runtime index"
        )
        runtime_positions = read_bounded_regular_file(
            args.runtime_positions, MAX_RUNTIME_ARRAY_BYTES, "runtime positions"
        )
        data, _ = _load(args)
        if regular_file_identity(source_path, "XPP/PSARC source") != source_identity:
            raise CharacterSourceCorrelationError(
                "XPP/PSARC source changed while it was read"
            )
        report = correlate_character_source_runtime(
            data,
            runtime_index,
            runtime_positions,
            record_offset=args.record_offset,
            runtime_index_sha256=args.runtime_index_sha256,
            runtime_positions_sha256=args.runtime_positions_sha256,
            runtime_byte_order=args.runtime_byte_order,
            runtime_first_row=args.runtime_first_row,
        )
        write_new_correlation_report(args.output, report)
    except (OSError, CharacterSourceCorrelationError, ValueError) as exc:
        print(f"character-source-runtime-correlate: {exc}", file=sys.stderr)
        return 1
    print(render_correlation_report(report).decode("utf-8"), end="")
    return 0


def cmd_character_asset_census(args: argparse.Namespace) -> int:
    try:
        inputs = (
            args.left_profile.resolve(),
            args.right_profile.resolve(),
            args.left_oid_manifest.resolve(),
            args.right_oid_manifest.resolve(),
        )
        output = args.output.resolve()
        if any(output == source or source in output.parents for source in inputs):
            raise CharacterAssetCensusError(
                "census output must remain outside every input profile and manifest"
            )
        report = build_character_asset_census(
            args.left_profile,
            args.right_profile,
            args.left_workspace_sha256,
            args.right_workspace_sha256,
            args.left_oid_manifest,
            args.right_oid_manifest,
            args.left_oid_manifest_sha256,
            args.right_oid_manifest_sha256,
            args.left_target,
            args.right_target,
            anchor=args.anchor,
            name_token=args.name_token,
            anchor_before=args.anchor_before,
            anchor_after=args.anchor_after,
        )
        write_new_character_asset_census(args.output, report)
    except (OSError, CharacterAssetCensusError, ValidationError, ValueError) as exc:
        print(f"character-asset-census: {exc}", file=sys.stderr)
        return 1
    left = report["targets"]["left"]
    right = report["targets"]["right"]
    findings = report["findings"]
    print(
        "character asset census: "
        f"{left['texture_descriptor_count']}/{right['texture_descriptor_count']} target textures, "
        f"{left['geometry_contract_count']}/{right['geometry_contract_count']} geometry contracts"
    )
    print(
        "multipart names: "
        f"{str(findings['multipart_package_names_proved']).lower()}; "
        "geometry/name and geometry/texture bindings remain unproved"
    )
    print(f"wrote {args.output}")
    return 0


def cmd_asset_completion_inventory(args: argparse.Namespace) -> int:
    try:
        inputs = (
            args.decomp_tally.resolve(),
            args.static_glb_manifest.resolve(),
            args.gallery_snapshot.resolve(),
            args.character_census.resolve(),
        )
        output = args.output.resolve()
        if output in inputs:
            raise AssetInventoryError("inventory output must not overwrite an input")
        report = build_asset_completion_inventory(
            args.decomp_tally,
            args.decomp_tally_sha256,
            args.static_glb_manifest,
            args.static_glb_manifest_sha256,
            args.gallery_snapshot,
            args.gallery_snapshot_sha256,
            args.character_census,
            args.character_census_sha256,
            candidate_id=args.candidate_id,
        )
        write_new_asset_completion_inventory(args.output, report)
    except (OSError, AssetInventoryError, ValueError) as exc:
        print(f"asset-completion-inventory: {exc}", file=sys.stderr)
        return 1
    counts = report["counts"]
    gallery = report["reconciliation"]["gallery"]
    print(
        "asset completion inventory: "
        f"{counts['records']} records; {counts['complete']} complete, "
        f"{counts['partial']} partial, {counts['unknown']} unknown"
    )
    print(
        "existing work: "
        f"{counts['retail_static_glb_exports_to_skip']} retail GLB exports and "
        f"{counts['existing_8k_asset_renders_to_skip']} unique 8K asset renders; "
        f"{gallery['gameplay_screenshots']} gameplay screenshot is not an asset render"
    )
    print(f"first unfinished batch: {report['first_unfinished_batch']['asset_id']}")
    print(f"wrote {args.output}")
    return 0


def cmd_runtime_topology_diagnostic_export(args: argparse.Namespace) -> int:
    try:
        bundle = args.bundle.resolve()
        destination_paths = [args.output]
        if args.json_out is not None:
            destination_paths.append(args.json_out)
        if len({path.resolve() for path in destination_paths}) != len(
            destination_paths
        ):
            raise RuntimeTopologyExportError(
                "--output and --json-out must be different paths"
            )
        if any(
            destination.resolve() == bundle or bundle in destination.resolve().parents
            for destination in destination_paths
        ):
            raise RuntimeTopologyExportError(
                "diagnostic outputs must remain outside the immutable input bundle"
            )
        if any(
            destination.is_symlink() or destination.exists()
            for destination in destination_paths
        ):
            raise RuntimeTopologyExportError(
                "diagnostic output already exists; refusing to overwrite it"
            )
        result = export_runtime_topology_glb(
            args.bundle,
            args.event,
            args.output,
            position_hypothesis_attribute=args.position_hypothesis_attribute,
            texture_allowlist=args.texture_allowlist,
            capture_key_exclusion=args.capture_key_exclusion,
        )
    except (OSError, RuntimeTopologyExportError, ValueError) as exc:
        print(f"runtime-topology-diagnostic-export: {exc}", file=sys.stderr)
        return 1
    rendered = render_report(result)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


def cmd_runtime_vertex_transform_census(args: argparse.Namespace) -> int:
    try:
        bundle = args.bundle.resolve()
        output = args.json_out.resolve()
        if output == bundle or bundle in output.parents:
            raise VertexTransformCensusError(
                "census output must remain outside the immutable input bundle"
            )
        if args.json_out.is_symlink() or args.json_out.exists():
            raise VertexTransformCensusError(
                "census output already exists; refusing to overwrite it"
            )
        result = analyze_vertex_transform_bundle(
            args.bundle, args.texture_allowlist, args.capture_key_exclusion
        )
        rendered = render_report(result)
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with args.json_out.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, VertexTransformCensusError, ValueError) as exc:
        print(f"runtime-vertex-transform-census: {exc}", file=sys.stderr)
        return 1
    print(rendered, end="")
    return 0


def cmd_runtime_fragment_sampler_census(args: argparse.Namespace) -> int:
    try:
        bundle = args.bundle.resolve()
        output = args.json_out.resolve()
        if output == bundle or bundle in output.parents:
            raise RuntimeTopologyExportError(
                "census output must remain outside the immutable input bundle"
            )
        if args.json_out.is_symlink() or args.json_out.exists():
            raise RuntimeTopologyExportError(
                "census output already exists; refusing to overwrite it"
            )
        result = census_runtime_fragment_samplers(
            args.bundle, args.texture_allowlist, args.capture_key_exclusion
        )
        rendered = render_report(result)
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with args.json_out.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, RuntimeTopologyExportError, ValueError) as exc:
        print(f"runtime-fragment-sampler-census: {exc}", file=sys.stderr)
        return 1
    print(rendered, end="")
    return 0


def cmd_character_uv_texture_binding(args: argparse.Namespace) -> int:
    try:
        bundle = args.bundle.resolve()
        inputs = {
            args.texture_allowlist.resolve(),
            args.source_census.resolve(),
            args.character_census.resolve(),
        }
        if args.capture_key_exclusion is not None:
            inputs.add(args.capture_key_exclusion.resolve())
        output = args.output.resolve()
        if output in inputs or output == bundle or bundle in output.parents:
            raise ShaderLineageError(
                "shader-lineage output must be new and outside every immutable input"
            )
        report = build_character_uv_texture_binding(
            args.bundle,
            args.texture_allowlist,
            args.capture_key_exclusion,
            args.source_census,
            args.source_census_sha256,
            args.character_census,
            args.character_census_sha256,
            event_number=args.event,
            page_number=args.page,
            record_offset=args.record_offset,
            character_side=args.character_side,
        )
        write_new_character_uv_texture_binding(args.output, report)
    except (OSError, ShaderLineageError, ValueError) as exc:
        print(f"character-uv-texture-binding: {exc}", file=sys.stderr)
        return 1
    lineage = report["shader_lineage"]
    print(
        "character UV/texture binding: "
        f"source record {report['selection']['record_offset']} attribute "
        f"{lineage['vertex_input_attribute']} -> {lineage['fragment_input_name']} -> "
        f"{len(report['texture_bindings'])} named textures"
    )
    print(f"wrote {args.output}")
    return 0


def cmd_character_material_candidate_census(args: argparse.Namespace) -> int:
    try:
        bundle = args.bundle.resolve()
        inputs = {
            args.texture_allowlist.resolve(),
            args.source_census.resolve(),
            args.character_census.resolve(),
        }
        if args.capture_key_exclusion is not None:
            inputs.add(args.capture_key_exclusion.resolve())
        output = args.output.resolve()
        if output in inputs or output == bundle or bundle in output.parents:
            raise CharacterMaterialCensusError(
                "candidate census output must be new and outside every immutable input"
            )
        report = build_character_material_candidate_census(
            args.bundle,
            args.texture_allowlist,
            args.capture_key_exclusion,
            args.source_census,
            args.source_census_sha256,
            args.character_census,
            args.character_census_sha256,
            page_number=args.page,
            character_side=args.character_side,
            excluded_candidates=tuple(args.exclude_candidate or ()),
        )
        write_new_character_material_candidate_census(args.output, report)
    except (
        OSError,
        CharacterMaterialCensusError,
        ShaderLineageError,
        ValueError,
    ) as exc:
        print(f"character-material-candidate-census: {exc}", file=sys.stderr)
        return 1
    summary = report["summary"]
    print(
        "character material candidate census: "
        f"{summary['accepted']} accepted / {summary['rejected']} rejected"
    )
    print(f"wrote {args.output}")
    return 0


def cmd_character_component_ledger(args: argparse.Namespace) -> int:
    material_paths = args.material_report or []
    material_hashes = args.material_report_sha256 or []
    if len(material_paths) != len(material_hashes):
        print(
            "character-component-ledger: each material report requires one SHA-256 pin",
            file=sys.stderr,
        )
        return 1
    pass_census_paths = args.material_pass_census or []
    pass_census_hashes = args.material_pass_census_sha256 or []
    if len(pass_census_paths) != len(pass_census_hashes):
        print(
            "character-component-ledger: each material pass census requires one SHA-256 pin",
            file=sys.stderr,
        )
        return 1
    try:
        inputs = {path.resolve() for path in material_paths}
        if len(inputs) != len(material_paths):
            raise CharacterComponentLedgerError("material report path is duplicated")
        visual_receipts = None
        if (args.visual_receipts is None) != (args.visual_receipts_sha256 is None):
            raise CharacterComponentLedgerError(
                "visual receipts and their SHA-256 pin must be supplied together"
            )
        if args.visual_receipts is not None:
            if args.visual_receipts.resolve() in inputs:
                raise CharacterComponentLedgerError(
                    "visual receipt path duplicates a material report"
                )
            inputs.add(args.visual_receipts.resolve())
            visual_receipts = (
                args.visual_receipts,
                args.visual_receipts_sha256,
            )
        resolved_pass_censuses = {path.resolve() for path in pass_census_paths}
        if len(resolved_pass_censuses) != len(pass_census_paths):
            raise CharacterComponentLedgerError(
                "material pass census path is duplicated"
            )
        if inputs & resolved_pass_censuses:
            raise CharacterComponentLedgerError(
                "material pass census path duplicates another input"
            )
        inputs.update(resolved_pass_censuses)
        if args.output.resolve() in inputs:
            raise CharacterComponentLedgerError(
                "component ledger output must be new and outside every input"
            )
        report = build_character_component_ledger(
            tuple(zip(material_paths, material_hashes, strict=True)),
            title_id=args.title_id,
            build_id=args.build_id,
            candidate_id=args.candidate_id,
            visual_receipts=visual_receipts,
            material_pass_censuses=tuple(
                zip(pass_census_paths, pass_census_hashes, strict=True)
            ),
        )
        write_new_character_component_ledger(args.output, report)
    except (OSError, CharacterComponentLedgerError, ValueError) as exc:
        print(f"character-component-ledger: {exc}", file=sys.stderr)
        return 1
    counts = report["counts"]
    print(
        "character component ledger: "
        f"{counts['components']} components / "
        f"{counts['material_observations']} material observations / "
        f"{counts['material_pass_censuses']} material pass censuses / "
        f"{counts['accepted_visual_baselines']} accepted visual baselines"
    )
    print(f"wrote {args.output}")
    return 0


def cmd_character_material_coverage_union(args: argparse.Namespace) -> int:
    try:
        observations = []
        partial_observations = []
        inputs = {args.xpp.resolve(), args.texture_allowlist.resolve()}
        for report, report_sha256, bundle, exclusion in args.observation:
            report_path = Path(report)
            bundle_path = Path(bundle)
            exclusion_path = None if exclusion == "-" else Path(exclusion)
            inputs.add(report_path.resolve())
            inputs.add(bundle_path.resolve())
            if exclusion_path is not None:
                inputs.add(exclusion_path.resolve())
            observations.append(
                MaterialCoverageObservation(
                    report=report_path,
                    report_sha256=report_sha256,
                    bundle=bundle_path,
                    capture_key_exclusion=exclusion_path,
                )
            )
        for (
            lineage,
            lineage_sha256,
            bundle,
            exclusion,
            source_census,
            source_census_sha256,
            character_census,
            character_census_sha256,
        ) in args.partial_observation or []:
            lineage_path = Path(lineage)
            bundle_path = Path(bundle)
            exclusion_path = None if exclusion == "-" else Path(exclusion)
            source_census_path = Path(source_census)
            character_census_path = Path(character_census)
            inputs.update(
                (
                    lineage_path.resolve(),
                    bundle_path.resolve(),
                    source_census_path.resolve(),
                    character_census_path.resolve(),
                )
            )
            if exclusion_path is not None:
                inputs.add(exclusion_path.resolve())
            partial_observations.append(
                PartialMaterialCoverageObservation(
                    lineage=lineage_path,
                    lineage_sha256=lineage_sha256,
                    bundle=bundle_path,
                    capture_key_exclusion=exclusion_path,
                    source_census=source_census_path,
                    source_census_sha256=source_census_sha256,
                    character_census=character_census_path,
                    character_census_sha256=character_census_sha256,
                )
            )
        output = args.output.resolve()
        if output in inputs or any(
            output == bundle or bundle in output.parents
            for bundle in (
                *(item.bundle.resolve() for item in observations),
                *(item.bundle.resolve() for item in partial_observations),
            )
        ):
            raise MaterialCoverageUnionError(
                "coverage union output must be new and outside every immutable input"
            )
        report = build_material_coverage_union(
            args.xpp,
            args.xpp_sha256,
            args.texture_allowlist,
            observations,
            record_offset=args.record_offset,
            partial_observations=partial_observations,
        )
        write_new_material_coverage_union(args.output, report)
    except (OSError, MaterialCoverageUnionError, ValueError) as exc:
        print(f"character-material-coverage-union: {exc}", file=sys.stderr)
        return 1
    union = report["union"]
    print(
        "character material coverage union: "
        f"{union['covered_retail_triangle_occurrences']} / "
        f"{report['component']['retail_triangle_occurrences']} retail triangle "
        f"occurrences across {union['observation_count']} observations"
    )
    print(f"wrote {args.output}")
    return 0


def cmd_character_material_coverage_export(args: argparse.Namespace) -> int:
    try:
        observations = []
        partial_observations = []
        inputs = {
            args.xpp.resolve(),
            args.texture_allowlist.resolve(),
            args.anchor_lineage.resolve(),
        }
        for report, report_sha256, bundle, exclusion in args.observation:
            report_path = Path(report)
            bundle_path = Path(bundle)
            exclusion_path = None if exclusion == "-" else Path(exclusion)
            inputs.update((report_path.resolve(), bundle_path.resolve()))
            if exclusion_path is not None:
                inputs.add(exclusion_path.resolve())
            observations.append(
                MaterialCoverageObservation(
                    report=report_path,
                    report_sha256=report_sha256,
                    bundle=bundle_path,
                    capture_key_exclusion=exclusion_path,
                )
            )
        for (
            lineage,
            lineage_sha256,
            bundle,
            exclusion,
            source_census,
            source_census_sha256,
            character_census,
            character_census_sha256,
        ) in args.partial_observation or []:
            lineage_path = Path(lineage)
            bundle_path = Path(bundle)
            exclusion_path = None if exclusion == "-" else Path(exclusion)
            source_census_path = Path(source_census)
            character_census_path = Path(character_census)
            inputs.update(
                (
                    lineage_path.resolve(),
                    bundle_path.resolve(),
                    source_census_path.resolve(),
                    character_census_path.resolve(),
                )
            )
            if exclusion_path is not None:
                inputs.add(exclusion_path.resolve())
            partial_observations.append(
                PartialMaterialCoverageObservation(
                    lineage=lineage_path,
                    lineage_sha256=lineage_sha256,
                    bundle=bundle_path,
                    capture_key_exclusion=exclusion_path,
                    source_census=source_census_path,
                    source_census_sha256=source_census_sha256,
                    character_census=character_census_path,
                    character_census_sha256=character_census_sha256,
                )
            )
        outputs = {args.output_glb.resolve(), args.output_report.resolve()}
        bundle_paths = [
            *(item.bundle.resolve() for item in observations),
            *(item.bundle.resolve() for item in partial_observations),
        ]
        if (
            len(outputs) != 2
            or outputs & inputs
            or any(
                output == bundle or bundle in output.parents
                for output in outputs
                for bundle in bundle_paths
            )
        ):
            raise MaterialCoverageExportError(
                "coverage export outputs must differ and remain outside every immutable input"
            )
        glb, report = build_material_coverage_export(
            args.xpp,
            args.xpp_sha256,
            args.texture_allowlist,
            observations,
            record_offset=args.record_offset,
            anchor_lineage=args.anchor_lineage,
            anchor_lineage_sha256=args.anchor_lineage_sha256,
            partial_observations=partial_observations,
        )
        write_new_material_coverage_export(
            args.output_glb, args.output_report, glb, report
        )
    except (OSError, MaterialCoverageExportError, ValueError) as exc:
        print(f"character-material-coverage-export: {exc}", file=sys.stderr)
        return 1
    selection = report["selection"]
    print(
        "character material coverage export: "
        f"{selection['material_observed_triangles']} / "
        f"{selection['triangles']} retail triangle occurrences across "
        f"{report['coverage_union']['observation_count']} observations"
    )
    print(f"wrote {args.output_glb}")
    print(f"wrote {args.output_report}")
    return 0


def cmd_character_material_pass_census(args: argparse.Namespace) -> int:
    try:
        observations = []
        inputs = {args.xpp.resolve(), args.texture_allowlist.resolve()}
        for report, report_sha256, bundle, exclusion in args.observation:
            report_path = Path(report)
            bundle_path = Path(bundle)
            exclusion_path = None if exclusion == "-" else Path(exclusion)
            inputs.update((report_path.resolve(), bundle_path.resolve()))
            if exclusion_path is not None:
                inputs.add(exclusion_path.resolve())
            observations.append(
                MaterialCoverageObservation(
                    report=report_path,
                    report_sha256=report_sha256,
                    bundle=bundle_path,
                    capture_key_exclusion=exclusion_path,
                )
            )
        output = args.output.resolve()
        if output in inputs or any(
            output == observation.bundle.resolve()
            or observation.bundle.resolve() in output.parents
            for observation in observations
        ):
            raise MaterialPassCensusError(
                "pass census output must be new and outside every immutable input"
            )
        report = build_material_pass_census(
            args.xpp,
            args.xpp_sha256,
            args.texture_allowlist,
            observations,
            record_offset=args.record_offset,
        )
        write_new_material_pass_census(args.output, report)
    except (OSError, MaterialPassCensusError, ValueError) as exc:
        print(f"character-material-pass-census: {exc}", file=sys.stderr)
        return 1
    union = report["any_pass_union"]
    print(
        "character material pass census: "
        f"{union['covered_retail_triangle_occurrences']} / "
        f"{report['component']['retail_triangle_occurrences']} retail triangle "
        f"occurrences across {union['pass_signature_count']} pass signatures"
    )
    print(f"wrote {args.output}")
    return 0


def cmd_character_material_export(args: argparse.Namespace) -> int:
    try:
        bundle = args.bundle.resolve()
        inputs = {
            args.texture_allowlist.resolve(),
            args.lineage.resolve(),
        }
        if args.capture_key_exclusion is not None:
            inputs.add(args.capture_key_exclusion.resolve())
        if args.xpp is not None:
            inputs.add(args.xpp.resolve())
        if args.psarc is not None:
            inputs.add(args.psarc.resolve())
        outputs = {args.output_glb.resolve(), args.output_report.resolve()}
        if (
            len(outputs) != 2
            or outputs & inputs
            or any(output == bundle or bundle in output.parents for output in outputs)
        ):
            raise CharacterMaterialExportError(
                "material outputs must differ and remain outside every immutable input"
            )
        xpp_data, _stem = load_xpp_bytes(
            xpp=args.xpp, psarc=args.psarc, entry=args.entry
        )
        glb, report = build_character_material_export(
            xpp_data,
            args.bundle,
            args.texture_allowlist,
            args.capture_key_exclusion,
            args.lineage,
            args.lineage_sha256,
            args.material_coverage_mode,
        )
        write_new_character_material_export(
            args.output_glb, args.output_report, glb, report
        )
    except (OSError, CharacterMaterialExportError, ValueError) as exc:
        print(f"character-material-export: {exc}", file=sys.stderr)
        return 1
    selection = report["selection"]
    print(
        "character material export: "
        f"record {selection['record_offset']} {selection['vertices']} vertices / "
        f"{selection['triangles']} triangles / 1 proved UV layer / "
        f"{selection['shader_bound_texture_count']} retail images"
        f" / {report['presentation_mode']}"
    )
    print(f"wrote {args.output_glb}")
    print(f"wrote {args.output_report}")
    return 0


def cmd_runtime_capture_key_exclusion(args: argparse.Namespace) -> int:
    try:
        bundle = args.bundle.resolve()
        destinations = (args.output.resolve(), args.json_out.resolve())
        if len(set(destinations)) != 2:
            raise RuntimeTopologyExportError(
                "capture-key exclusion and JSON outputs must differ"
            )
        if any(
            destination == bundle or bundle in destination.parents
            for destination in destinations
        ):
            raise RuntimeTopologyExportError(
                "capture-key outputs must remain outside the immutable input bundle"
            )
        if any(
            path.is_symlink() or path.exists() for path in (args.output, args.json_out)
        ):
            raise RuntimeTopologyExportError(
                "capture-key output exists; refusing to overwrite it"
            )
        result = write_capture_key_exclusion(
            args.bundle,
            args.texture_allowlist,
            args.output,
            args.capture_key_exclusion,
        )
        rendered = render_report(result)
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with args.json_out.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, RuntimeTopologyExportError, ValueError) as exc:
        print(f"runtime-capture-key-exclusion: {exc}", file=sys.stderr)
        return 1
    print(rendered, end="")
    return 0


def cmd_runtime_position_replay_export(args: argparse.Namespace) -> int:
    try:
        selected_events = tuple(
            int(value, 10) for value in args.events.split(",") if value
        )
        if not selected_events or ",".join(map(str, selected_events)) != args.events:
            raise PositionReplayError(
                "--events must be unique canonical comma-separated positive integers"
            )
        bundle = args.bundle.resolve()
        destinations = (args.output.resolve(), args.json_out.resolve())
        if len(set(destinations)) != 2:
            raise PositionReplayError("GLB and JSON outputs must differ")
        if any(
            destination == bundle or bundle in destination.parents
            for destination in destinations
        ):
            raise PositionReplayError(
                "replay outputs must remain outside the immutable input bundle"
            )
        if any(
            path.is_symlink() or path.exists() for path in (args.output, args.json_out)
        ):
            raise PositionReplayError("replay output exists; refusing to overwrite it")
        result = export_position_replay_glb(
            args.bundle,
            args.texture_allowlist,
            selected_events,
            args.output,
            projection_event=args.projection_event,
            model_constant_start=args.model_constant_start,
            projection_constant_start=args.projection_constant_start,
            capture_key_exclusion=args.capture_key_exclusion,
        )
        rendered = render_report(result)
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with args.json_out.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, PositionReplayError, ValueError) as exc:
        print(f"runtime-position-replay-export: {exc}", file=sys.stderr)
        return 1
    print(rendered, end="")
    return 0


def cmd_runtime_screen_position_replay_export(args: argparse.Namespace) -> int:
    try:
        selected_events = tuple(
            int(value, 10) for value in args.events.split(",") if value
        )
        if (
            not selected_events
            or len(set(selected_events)) != len(selected_events)
            or any(value <= 0 for value in selected_events)
            or ",".join(map(str, selected_events)) != args.events
        ):
            raise ScreenReplayError(
                "--events must be unique canonical comma-separated positive integers"
            )
        bundle = args.bundle.resolve()
        destinations = (args.output.resolve(), args.json_out.resolve())
        if len(set(destinations)) != 2:
            raise ScreenReplayError("GLB and JSON outputs must differ")
        if any(
            destination == bundle or bundle in destination.parents
            for destination in destinations
        ):
            raise ScreenReplayError(
                "screen replay outputs must remain outside the immutable input bundle"
            )
        if any(
            path.is_symlink() or path.exists() for path in (args.output, args.json_out)
        ):
            raise ScreenReplayError(
                "screen replay output exists; refusing to overwrite it"
            )
        result = export_screen_replay_glb(
            args.bundle,
            args.texture_allowlist,
            selected_events,
            args.output,
            args.capture_key_exclusion,
        )
        rendered = render_report(result)
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with args.json_out.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, ScreenReplayError, ValueError) as exc:
        print(f"runtime-screen-position-replay-export: {exc}", file=sys.stderr)
        return 1
    print(rendered, end="")
    return 0


def cmd_runtime_screen_position_page_merge(args: argparse.Namespace) -> int:
    try:
        if not (
            len(args.page_bundle)
            == len(args.page_events)
            == len(args.page_capture_key_exclusion)
        ):
            raise ScreenReplayError(
                "--page-bundle, --page-events, and --page-capture-key-exclusion "
                "counts must match"
            )
        selections = []
        for raw in args.page_events:
            values = tuple(int(value, 10) for value in raw.split(",") if value)
            if (
                not values
                or len(set(values)) != len(values)
                or any(value <= 0 for value in values)
                or ",".join(map(str, values)) != raw
            ):
                raise ScreenReplayError(
                    "each --page-events value must be unique canonical "
                    "comma-separated positive integers"
                )
            selections.append(values)
        exclusions = tuple(
            None if value == "-" else Path(value)
            for value in args.page_capture_key_exclusion
        )
        bundle_roots = tuple(path.resolve() for path in args.page_bundle)
        destinations = (args.output.resolve(), args.json_out.resolve())
        if len(set(destinations)) != 2:
            raise ScreenReplayError("paged GLB and JSON outputs must differ")
        if any(
            destination == bundle or bundle in destination.parents
            for destination in destinations
            for bundle in bundle_roots
        ):
            raise ScreenReplayError(
                "paged outputs must remain outside every immutable input bundle"
            )
        if any(
            path.is_symlink() or path.exists() for path in (args.output, args.json_out)
        ):
            raise ScreenReplayError("paged output exists; refusing to overwrite it")
        result = export_screen_replay_pages_glb(
            tuple(args.page_bundle),
            args.texture_allowlist,
            tuple(selections),
            exclusions,
            args.output,
        )
        rendered = render_report(result)
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with args.json_out.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, ScreenReplayError, ValueError) as exc:
        print(f"runtime-screen-position-page-merge: {exc}", file=sys.stderr)
        return 1
    print(rendered, end="")
    return 0


def cmd_runtime_page_family_census(args: argparse.Namespace) -> int:
    try:
        if len(args.page_bundle) != len(args.page_capture_key_exclusion):
            raise PageCorrelationError(
                "--page-bundle and --page-capture-key-exclusion counts must match"
            )
        exclusions = tuple(
            None if value == "-" else Path(value)
            for value in args.page_capture_key_exclusion
        )
        bundle_roots = tuple(path.resolve() for path in args.page_bundle)
        output = args.json_out.resolve()
        if any(output == bundle or bundle in output.parents for bundle in bundle_roots):
            raise PageCorrelationError(
                "page-family output must remain outside every immutable input bundle"
            )
        if args.json_out.is_symlink() or args.json_out.exists():
            raise PageCorrelationError(
                "page-family output exists; refusing to overwrite it"
            )
        result = correlate_paged_draw_families(
            tuple(args.page_bundle), args.texture_allowlist, exclusions
        )
        rendered = render_report(result)
        if len(rendered.encode("utf-8")) > MAX_PAGE_CORRELATION_REPORT_BYTES:
            raise PageCorrelationError("page-family report exceeds its byte bound")
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with args.json_out.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, PageCorrelationError, ValueError) as exc:
        print(f"runtime-page-family-census: {exc}", file=sys.stderr)
        return 1
    print(rendered, end="")
    return 0


def cmd_runtime_xpp_source_census(args: argparse.Namespace) -> int:
    try:
        if len(args.page_bundle) != len(args.page_capture_key_exclusion):
            raise SourceCorrelationError(
                "--page-bundle and --page-capture-key-exclusion counts must match"
            )
        exclusions = tuple(
            None if value == "-" else Path(value)
            for value in args.page_capture_key_exclusion
        )
        bundle_roots = tuple(path.resolve() for path in args.page_bundle)
        source = args.xpp.resolve()
        output = args.json_out.resolve()
        if output == source or any(
            output == bundle or bundle in output.parents for bundle in bundle_roots
        ):
            raise SourceCorrelationError(
                "source-census output must not overwrite the XPP or enter an input bundle"
            )
        if args.json_out.is_symlink() or args.json_out.exists():
            raise SourceCorrelationError(
                "source-census output exists; refusing to overwrite it"
            )
        if args.xpp.is_symlink() or not args.xpp.is_file():
            raise SourceCorrelationError(
                "XPP source must be an existing regular non-symlink file"
            )
        if not 0 < args.xpp.stat().st_size <= MAX_XPP_SOURCE_BYTES:
            raise SourceCorrelationError(
                "XPP source is empty or exceeds the 64 MiB bound"
            )
        result = correlate_paged_draws_to_xpp(
            args.xpp.read_bytes(),
            args.xpp.name,
            tuple(args.page_bundle),
            args.texture_allowlist,
            exclusions,
        )
        rendered = render_report(result)
        if len(rendered.encode("utf-8")) > MAX_SOURCE_CORRELATION_REPORT_BYTES:
            raise SourceCorrelationError("source-census report exceeds its byte bound")
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with args.json_out.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, SourceCorrelationError, ValueError) as exc:
        print(f"runtime-xpp-source-census: {exc}", file=sys.stderr)
        return 1
    print(rendered, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="XPP extract/repack tools and audited inFAMOUS PSARC profile building.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="describe every texture")
    _add_source(p_list)
    p_list.add_argument("--index", type=int)
    p_list.set_defaults(func=cmd_list)

    p_ex = sub.add_parser("extract", help="decode textures to PNG")
    _add_source(p_ex)
    p_ex.add_argument("--outdir", type=Path, default=Path("out"))
    p_ex.add_argument("--level", type=int, default=0)
    p_ex.add_argument("--index", type=int)
    p_ex.add_argument("--max", type=int)
    p_ex.set_defaults(func=cmd_extract)

    p_all = sub.add_parser("extract-all", help="walk a directory of .xpp files")
    p_all.add_argument("--xpp-dir", type=Path, required=True)
    p_all.add_argument("--outdir", type=Path, default=Path("out"))
    p_all.add_argument("--level", type=int, default=0)
    p_all.set_defaults(func=cmd_extract_all)

    p_ver = sub.add_parser("verify", help="check 128-byte heap-pad layout")
    _add_source(p_ver)
    p_ver.set_defaults(func=cmd_verify)

    p_validate = sub.add_parser(
        "validate",
        help="strictly compare one candidate XPP with its retail package",
    )
    p_validate.add_argument("--retail", type=Path, required=True)
    p_validate.add_argument("--candidate", type=Path, required=True)
    p_validate.add_argument(
        "--json", action="store_true", help="print a machine-readable report"
    )
    _add_budget_options(p_validate)
    p_validate.set_defaults(func=cmd_validate)

    p_pack = sub.add_parser("pack", help="encode PNGs back into an XPP")
    _add_source(p_pack)
    p_pack.add_argument("--out", type=Path, required=True, help="output .xpp")
    p_pack.add_argument(
        "--replace", action="append", help="INDEX=file.png (repeatable)"
    )
    p_pack.add_argument(
        "--from-dir", type=Path, help="read STEM.N.mip0.png from this folder"
    )
    p_pack.add_argument(
        "--stem", help="filename stem for --from-dir (default: xpp name)"
    )
    p_pack.add_argument(
        "--scale", type=int, help="nearest-neighbor upscale every 2D texture"
    )
    p_pack.add_argument(
        "--allow-resize",
        action="store_true",
        help="allow width/height/mip count to change (implied by --scale)",
    )
    p_pack.set_defaults(func=cmd_pack)

    p_derive = sub.add_parser(
        "derive",
        help="losslessly derive a 1x/2x pack from an existing mixed 2x/4x XPP",
    )
    p_derive.add_argument(
        "--retail", type=Path, required=True, help="original retail XPP"
    )
    p_derive.add_argument(
        "--source", type=Path, required=True, help="existing 2x/4x XPP"
    )
    p_derive.add_argument("--out", type=Path, required=True, help="output XPP")
    p_derive.add_argument("--target-scale", type=int, choices=(1, 2, 4), default=2)
    selection = p_derive.add_mutually_exclusive_group()
    selection.add_argument(
        "--include-index",
        action="append",
        type=lambda value: int(value, 0),
        default=[],
        help="derive only this descriptor index (repeatable)",
    )
    selection.add_argument(
        "--exclude-index",
        action="append",
        type=lambda value: int(value, 0),
        default=[],
        help="keep this descriptor index retail (repeatable)",
    )
    selection.add_argument(
        "--max-upscaled",
        type=int,
        help="derive only the N lowest-memory-cost records",
    )
    p_derive.set_defaults(func=cmd_derive)

    p_rebase = sub.add_parser(
        "texture-rebase",
        help="apply source texture edits to a different retail build by exact identity",
    )
    p_rebase.add_argument("--source-retail", type=Path, required=True)
    p_rebase.add_argument("--source-candidate", type=Path, required=True)
    target_source = p_rebase.add_mutually_exclusive_group(required=True)
    target_source.add_argument("--target-retail", type=Path)
    target_source.add_argument("--target-psarc", type=Path)
    p_rebase.add_argument(
        "--target-entry",
        help="manifest path inside --target-psarc",
    )
    p_rebase.add_argument("--out", type=Path, required=True)
    p_rebase.add_argument(
        "--include-index",
        action="append",
        type=lambda value: int(value, 0),
        default=[],
        help="transfer only this changed source descriptor index (repeatable)",
    )
    p_rebase.add_argument(
        "--allow-zero-change",
        action="store_true",
        help="permit a no-op control and emit target retail byte-for-byte",
    )
    p_rebase.add_argument(
        "--json", action="store_true", help="print a machine-readable aggregate report"
    )
    p_rebase.set_defaults(func=cmd_texture_rebase)

    p_psarc = sub.add_parser(
        "psarc-pack",
        help="rebuild an install PSARC with replacement XPPs",
    )
    p_psarc.add_argument(
        "--psarc", type=Path, required=True, help="retail source PSARC"
    )
    p_psarc.add_argument(
        "--xpp-dir", type=Path, required=True, help="replacement XPP directory"
    )
    p_psarc.add_argument("--out", type=Path, required=True, help="output PSARC")
    p_psarc.add_argument(
        "--include",
        action="append",
        help="replace only this XPP basename (repeatable)",
    )
    p_psarc.add_argument(
        "--compression-level", type=int, choices=range(1, 10), default=9
    )
    p_psarc.set_defaults(func=cmd_psarc_pack)

    p_profile_extract = sub.add_parser(
        "profile-extract",
        help="extract an install1/install2 pair into a hashed XPP workspace",
    )
    p_profile_extract.add_argument("--install1", type=Path, required=True)
    p_profile_extract.add_argument("--install2", type=Path, required=True)
    p_profile_extract.add_argument("--outdir", type=Path, required=True)
    p_profile_extract.set_defaults(func=cmd_profile_extract)

    p_profile_build = sub.add_parser(
        "profile-build",
        help="route XPP replacements, rebuild both install PSARCs, and audit every entry",
    )
    p_profile_build.add_argument("--install1", type=Path, required=True)
    p_profile_build.add_argument("--install2", type=Path, required=True)
    p_profile_build.add_argument("--xpp-dir", type=Path, required=True)
    p_profile_build.add_argument("--outdir", type=Path, required=True)
    p_profile_build.add_argument(
        "--compression-level", type=int, choices=range(1, 10), default=9
    )
    _add_budget_options(p_profile_build)
    p_profile_build.set_defaults(func=cmd_profile_build)

    p_profile_validate = sub.add_parser(
        "profile-validate",
        help="strictly validate a replacement set without building PSARCs",
    )
    p_profile_validate.add_argument("--install1", type=Path, required=True)
    p_profile_validate.add_argument("--install2", type=Path, required=True)
    p_profile_validate.add_argument("--xpp-dir", type=Path, required=True)
    p_profile_validate.add_argument(
        "--json", action="store_true", help="print a machine-readable report"
    )
    _add_budget_options(p_profile_validate)
    p_profile_validate.set_defaults(func=cmd_profile_validate)

    p_profile_oracle = sub.add_parser(
        "profile-oracle",
        help="compare two packed install pairs without emitting package names or payloads",
    )
    p_profile_oracle.add_argument("--left-install1", type=Path, required=True)
    p_profile_oracle.add_argument("--left-install2", type=Path, required=True)
    p_profile_oracle.add_argument("--right-install1", type=Path, required=True)
    p_profile_oracle.add_argument("--right-install2", type=Path, required=True)
    p_profile_oracle.add_argument("--left-label", default="left")
    p_profile_oracle.add_argument("--right-label", default="right")
    p_profile_oracle.add_argument(
        "--catalog-only",
        action="store_true",
        help="skip slow payload hashing and withhold byte-identity claims",
    )
    p_profile_oracle.add_argument(
        "--json-out",
        type=Path,
        help="write the aggregate JSON report instead of printing it",
    )
    p_profile_oracle.set_defaults(func=cmd_profile_oracle)

    p_runtime_index = sub.add_parser(
        "runtime-index",
        help="generate exact texture hashes for emulator scene-coverage tracing",
    )
    _add_source(p_runtime_index)
    p_runtime_index.add_argument(
        "--label", help="package/profile label stored in the report"
    )
    p_runtime_index.add_argument(
        "--json-out", type=Path, help="write the full JSON index"
    )
    p_runtime_index.add_argument(
        "--allowlist-out",
        type=Path,
        help="write one SHA-256 per line for the private RPCS3 observer",
    )
    p_runtime_index.set_defaults(func=cmd_runtime_index)

    p_runtime_bundle = sub.add_parser(
        "runtime-bundle",
        help="build explicit host-GPU replacements from retail and candidate XPPs",
    )
    p_runtime_bundle.add_argument("--retail", type=Path, required=True)
    p_runtime_bundle.add_argument("--candidate", type=Path, required=True)
    p_runtime_bundle.add_argument(
        "--index",
        type=lambda value: int(value, 0),
        action="append",
        required=True,
        help="descriptor index to replace (repeatable)",
    )
    p_runtime_bundle.add_argument("--outdir", type=Path, required=True)
    p_runtime_bundle.add_argument("--label")
    p_runtime_bundle.set_defaults(func=cmd_runtime_bundle)

    p_ml = sub.add_parser("mesh-list", help="list static mesh sections")
    _add_source(p_ml)
    p_ml.set_defaults(func=cmd_mesh_list)

    p_me = sub.add_parser("mesh-export", help="export static mesh sections to GLB")
    _add_source(p_me)
    p_me.add_argument("--output", type=Path, required=True)
    p_me.add_argument(
        "--record-offset",
        action="append",
        type=lambda v: int(v, 0),
        help="include this section (repeat to assemble parts)",
    )
    p_me.add_argument(
        "--texture", type=Path, help="PNG to embed; default: decode from the package"
    )
    p_me.set_defaults(func=cmd_mesh_export)

    p_character = sub.add_parser(
        "character-report",
        help="report proven skinned-XPP contracts and external NIF compatibility",
    )
    _add_source(p_character)
    p_character.add_argument(
        "--external",
        type=Path,
        help="owned Fallout 4/76 .nif to validate and compare without converting",
    )
    p_character.add_argument(
        "--json-out",
        type=Path,
        help="also write the deterministic report to this path",
    )
    p_character.set_defaults(func=cmd_character_report)

    p_character_oracle = sub.add_parser(
        "character-oracle",
        help="compare two character XPPs by content rather than index or offset",
    )
    p_character_oracle.add_argument("--left-xpp", type=Path, required=True)
    p_character_oracle.add_argument("--right-xpp", type=Path, required=True)
    p_character_oracle.add_argument("--left-label", default="left")
    p_character_oracle.add_argument("--right-label", default="right")
    p_character_oracle.add_argument(
        "--json-out",
        type=Path,
        help="write a new deterministic payload-free report (refuses overwrite)",
    )
    p_character_oracle.set_defaults(func=cmd_character_oracle)

    p_character_capture = sub.add_parser(
        "character-capture-report",
        help="match skinned XPP index streams against an RPCS3 RSX capture",
    )
    _add_source(p_character_capture)
    p_character_capture.add_argument(
        "--rrc",
        type=Path,
        required=True,
        help="RPCS3 .rrc or .rrc.gz frame capture",
    )
    p_character_capture.add_argument(
        "--json-out",
        type=Path,
        help="also write the deterministic payload-free report to this path",
    )
    p_character_capture.set_defaults(func=cmd_character_capture_report)

    p_character_export = sub.add_parser(
        "character-diagnostic-export",
        help="export one proven character topology with an explicit position hypothesis",
    )
    _add_source(p_character_export)
    p_character_export.add_argument(
        "--binding-report",
        type=Path,
        required=True,
        help="deterministic character-capture-report JSON with one complete draw binding",
    )
    p_character_export.add_argument(
        "--attribute-payload",
        type=Path,
        required=True,
        help="owned captured payload for the explicitly selected attribute",
    )
    p_character_export.add_argument(
        "--position-hypothesis-attribute",
        type=int,
        required=True,
        help="RSX attribute to place in GLB POSITION; remains explicitly unproved",
    )
    p_character_export.add_argument("--output", type=Path, required=True)
    p_character_export.add_argument(
        "--json-out", type=Path, help="also write the deterministic export report"
    )
    p_character_export.set_defaults(func=cmd_character_diagnostic_export)

    p_character_source_export = sub.add_parser(
        "character-source-diagnostic-export",
        help="export exact XPP topology with an explicit packed-stream numeric hypothesis",
    )
    _add_source(p_character_source_export)
    p_character_source_export.add_argument(
        "--record-offset",
        type=lambda value: int(value, 0),
        required=True,
        help="exact proved character record offset (decimal or 0x-prefixed)",
    )
    p_character_source_export.add_argument(
        "--stream-index",
        type=int,
        choices=(1, 2, 3),
        required=True,
        help="descriptor-backed packed stream to inspect",
    )
    p_character_source_export.add_argument(
        "--numeric-family",
        choices=NUMERIC_FAMILIES,
        required=True,
        help="explicit unproved numeric family used only for diagnostic coordinates",
    )
    p_character_source_export.add_argument("--output", type=Path, required=True)
    p_character_source_export.add_argument(
        "--json-out", type=Path, help="also write the deterministic export report"
    )
    p_character_source_export.set_defaults(func=cmd_character_source_diagnostic_export)

    p_character_source_correlate = sub.add_parser(
        "character-source-runtime-correlate",
        help="rank exact packed streams against one topology-matched runtime float32x3 array",
    )
    _add_source(p_character_source_correlate)
    p_character_source_correlate.add_argument(
        "--record-offset",
        type=lambda value: int(value, 0),
        required=True,
        help="exact proved character record offset (decimal or 0x-prefixed)",
    )
    p_character_source_correlate.add_argument(
        "--runtime-index",
        type=Path,
        required=True,
        help="exact owned runtime u16 index bytes for topology pairing",
    )
    p_character_source_correlate.add_argument(
        "--runtime-index-sha256",
        required=True,
        help="required exact SHA-256 pin for --runtime-index",
    )
    p_character_source_correlate.add_argument(
        "--runtime-positions",
        type=Path,
        required=True,
        help="exact owned contiguous runtime float32x3 rows",
    )
    p_character_source_correlate.add_argument(
        "--runtime-positions-sha256",
        required=True,
        help="required exact SHA-256 pin for --runtime-positions",
    )
    p_character_source_correlate.add_argument(
        "--runtime-byte-order",
        choices=("big", "little"),
        required=True,
        help="byte order of the runtime float32x3 payload",
    )
    p_character_source_correlate.add_argument(
        "--runtime-first-row",
        type=int,
        default=0,
        help="first float32x3 row paired with vertex zero (default: 0)",
    )
    p_character_source_correlate.add_argument(
        "--output", type=Path, required=True, help="new payload-free JSON report"
    )
    p_character_source_correlate.set_defaults(
        func=cmd_character_source_runtime_correlate
    )

    p_character_asset_census = sub.add_parser(
        "character-asset-census",
        help="audit multipart names and texture sharing across two complete XPP profiles",
    )
    p_character_asset_census.add_argument(
        "--left-profile", type=Path, required=True, help="first extracted profile root"
    )
    p_character_asset_census.add_argument(
        "--right-profile",
        type=Path,
        required=True,
        help="second extracted profile root",
    )
    p_character_asset_census.add_argument(
        "--left-workspace-sha256", required=True, help="SHA-256 of left workspace.json"
    )
    p_character_asset_census.add_argument(
        "--right-workspace-sha256",
        required=True,
        help="SHA-256 of right workspace.json",
    )
    p_character_asset_census.add_argument(
        "--left-oid-manifest", type=Path, required=True
    )
    p_character_asset_census.add_argument(
        "--right-oid-manifest", type=Path, required=True
    )
    p_character_asset_census.add_argument("--left-oid-manifest-sha256", required=True)
    p_character_asset_census.add_argument("--right-oid-manifest-sha256", required=True)
    p_character_asset_census.add_argument(
        "--left-target",
        required=True,
        help="workspace-relative target XPP, for example xpp/install1/male_base_Zeke.xpp",
    )
    p_character_asset_census.add_argument(
        "--right-target", required=True, help="workspace-relative comparison target XPP"
    )
    p_character_asset_census.add_argument(
        "--anchor", required=True, help="unique exact manifest anchor name"
    )
    p_character_asset_census.add_argument(
        "--name-token", required=True, help="case-insensitive character-name token"
    )
    p_character_asset_census.add_argument("--anchor-before", type=int, default=96)
    p_character_asset_census.add_argument("--anchor-after", type=int, default=128)
    p_character_asset_census.add_argument(
        "--output", type=Path, required=True, help="new payload-free JSON report"
    )
    p_character_asset_census.set_defaults(func=cmd_character_asset_census)

    p_asset_inventory = sub.add_parser(
        "asset-completion-inventory",
        help="reconcile completed asset evidence and emit independent RPCS3/native gates",
    )
    p_asset_inventory.add_argument(
        "--decomp-tally",
        type=Path,
        required=True,
        help="authoritative GRAPHICS-ASSETS-TALLY.md",
    )
    p_asset_inventory.add_argument("--decomp-tally-sha256", required=True)
    p_asset_inventory.add_argument(
        "--static-glb-manifest",
        type=Path,
        required=True,
        help="exact completed retail GLB manifest",
    )
    p_asset_inventory.add_argument("--static-glb-manifest-sha256", required=True)
    p_asset_inventory.add_argument(
        "--gallery-snapshot",
        type=Path,
        required=True,
        help="exact metadata-only gallery snapshot",
    )
    p_asset_inventory.add_argument("--gallery-snapshot-sha256", required=True)
    p_asset_inventory.add_argument(
        "--character-census",
        type=Path,
        required=True,
        help="exact character/item census selected for the first unfinished batch",
    )
    p_asset_inventory.add_argument("--character-census-sha256", required=True)
    p_asset_inventory.add_argument(
        "--candidate-id",
        required=True,
        help="stable candidate token that must occur in both census target paths",
    )
    p_asset_inventory.add_argument(
        "--output", type=Path, required=True, help="new payload-free JSON inventory"
    )
    p_asset_inventory.set_defaults(func=cmd_asset_completion_inventory)

    p_runtime_topology_export = sub.add_parser(
        "runtime-topology-diagnostic-export",
        help="export one exact runtime topology event for unowned visual triage",
    )
    p_runtime_topology_export.add_argument(
        "--bundle",
        type=Path,
        required=True,
        help="complete local topology-census or texture-bound-topology output directory",
    )
    _add_capture_key_exclusion(p_runtime_topology_export)
    p_runtime_topology_export.add_argument(
        "--texture-allowlist",
        type=Path,
        help="required exact target SHA-256 allowlist for a texture-bound bundle",
    )
    p_runtime_topology_export.add_argument(
        "--event", type=int, required=True, help="captured topology event to export"
    )
    p_runtime_topology_export.add_argument(
        "--position-hypothesis-attribute",
        type=int,
        required=True,
        help="float32x3 runtime attribute to place in GLB POSITION; remains unproved",
    )
    p_runtime_topology_export.add_argument("--output", type=Path, required=True)
    p_runtime_topology_export.add_argument(
        "--json-out", type=Path, help="also write the deterministic export report"
    )
    p_runtime_topology_export.set_defaults(func=cmd_runtime_topology_diagnostic_export)

    p_vertex_transform = sub.add_parser(
        "runtime-vertex-transform-census",
        help="decode exact RSX vertex inputs/constants from a complete v2/v3/v4 bundle",
    )
    p_vertex_transform.add_argument(
        "--bundle",
        type=Path,
        required=True,
        help="complete local if1-texture-bound-topology-v2/v3/v4 output directory",
    )
    _add_capture_key_exclusion(p_vertex_transform)
    p_vertex_transform.add_argument(
        "--texture-allowlist",
        type=Path,
        required=True,
        help="exact target texture SHA-256 allowlist used by the capture",
    )
    p_vertex_transform.add_argument("--json-out", type=Path, required=True)
    p_vertex_transform.set_defaults(func=cmd_runtime_vertex_transform_census)

    p_fragment_sampler = sub.add_parser(
        "runtime-fragment-sampler-census",
        help="verify exact target sampler references in a complete v3/v4 bundle",
    )
    p_fragment_sampler.add_argument("--bundle", type=Path, required=True)
    p_fragment_sampler.add_argument(
        "--texture-allowlist",
        type=Path,
        required=True,
        help="exact target texture SHA-256 allowlist used by the capture",
    )
    _add_capture_key_exclusion(p_fragment_sampler)
    p_fragment_sampler.add_argument("--json-out", type=Path, required=True)
    p_fragment_sampler.set_defaults(func=cmd_runtime_fragment_sampler_census)

    p_shader_lineage = sub.add_parser(
        "character-uv-texture-binding",
        help="prove one packed character UV stream through both shaders to named textures",
    )
    p_shader_lineage.add_argument(
        "--bundle", type=Path, required=True, help="complete immutable v3/v4 bundle"
    )
    p_shader_lineage.add_argument("--texture-allowlist", type=Path, required=True)
    _add_capture_key_exclusion(p_shader_lineage)
    p_shader_lineage.add_argument("--page", type=int, required=True)
    p_shader_lineage.add_argument("--event", type=int, required=True)
    p_shader_lineage.add_argument("--record-offset", type=int, required=True)
    p_shader_lineage.add_argument("--source-census", type=Path, required=True)
    p_shader_lineage.add_argument("--source-census-sha256", required=True)
    p_shader_lineage.add_argument("--character-census", type=Path, required=True)
    p_shader_lineage.add_argument("--character-census-sha256", required=True)
    p_shader_lineage.add_argument(
        "--character-side", choices=("left", "right"), required=True
    )
    p_shader_lineage.add_argument(
        "--output", type=Path, required=True, help="new payload-free JSON report"
    )
    p_shader_lineage.set_defaults(func=cmd_character_uv_texture_binding)

    p_material_candidates = sub.add_parser(
        "character-material-candidate-census",
        help="classify every uncompleted full-record material candidate on one page",
    )
    p_material_candidates.add_argument(
        "--bundle", type=Path, required=True, help="complete immutable v3/v4 bundle"
    )
    p_material_candidates.add_argument("--texture-allowlist", type=Path, required=True)
    _add_capture_key_exclusion(p_material_candidates)
    p_material_candidates.add_argument("--page", type=int, required=True)
    p_material_candidates.add_argument("--source-census", type=Path, required=True)
    p_material_candidates.add_argument("--source-census-sha256", required=True)
    p_material_candidates.add_argument("--character-census", type=Path, required=True)
    p_material_candidates.add_argument("--character-census-sha256", required=True)
    p_material_candidates.add_argument(
        "--character-side", choices=("left", "right"), required=True
    )
    p_material_candidates.add_argument(
        "--exclude-candidate",
        action="append",
        type=_candidate_pair,
        help="completed EVENT:RECORD_OFFSET to omit; repeat for each exact candidate",
    )
    p_material_candidates.add_argument(
        "--output", type=Path, required=True, help="new payload-free JSON census"
    )
    p_material_candidates.set_defaults(func=cmd_character_material_candidate_census)

    p_component_ledger = sub.add_parser(
        "character-component-ledger",
        help="reconcile checksum-pinned character material and visual progress",
    )
    p_component_ledger.add_argument(
        "--title-id",
        required=True,
        help="canonical title token, for example infamous-1",
    )
    p_component_ledger.add_argument(
        "--build-id",
        required=True,
        help="canonical build token, for example bcus98119-v0100",
    )
    p_component_ledger.add_argument(
        "--candidate-id", required=True, help="canonical character/item token"
    )
    p_component_ledger.add_argument(
        "--material-report",
        type=Path,
        action="append",
        required=True,
        help="exact character-material-export JSON; repeat for each observation",
    )
    p_component_ledger.add_argument(
        "--material-report-sha256",
        action="append",
        required=True,
        help="matching SHA-256 pin; repeat in material-report order",
    )
    p_component_ledger.add_argument(
        "--visual-receipts",
        type=Path,
        help="optional payload-free visual-baseline receipt manifest",
    )
    p_component_ledger.add_argument("--visual-receipts-sha256")
    p_component_ledger.add_argument(
        "--material-pass-census",
        type=Path,
        action="append",
        help="optional exact cross-material pass census; repeat as needed",
    )
    p_component_ledger.add_argument(
        "--material-pass-census-sha256",
        action="append",
        help="matching pass-census SHA-256 pin; repeat in census order",
    )
    p_component_ledger.add_argument(
        "--output", type=Path, required=True, help="new payload-free JSON ledger"
    )
    p_component_ledger.set_defaults(func=cmd_character_component_ledger)

    p_material_coverage = sub.add_parser(
        "character-material-coverage-union",
        help="union exact material triangles across repeated draws of one source record",
    )
    p_material_coverage.add_argument("--xpp", type=Path, required=True)
    p_material_coverage.add_argument("--xpp-sha256", required=True)
    p_material_coverage.add_argument("--texture-allowlist", type=Path, required=True)
    p_material_coverage.add_argument("--record-offset", type=int, required=True)
    p_material_coverage.add_argument(
        "--observation",
        action="append",
        nargs=4,
        metavar=("REPORT", "REPORT_SHA256", "BUNDLE", "EXCLUSION_OR_DASH"),
        required=True,
        help=(
            "exact observed-only report, its SHA-256, immutable bundle, and v4 "
            "capture-key exclusion (use '-' for a nonpaged bundle); repeat as needed"
        ),
    )
    p_material_coverage.add_argument(
        "--partial-observation",
        action="append",
        nargs=8,
        metavar=(
            "LINEAGE",
            "LINEAGE_SHA256",
            "BUNDLE",
            "EXCLUSION_OR_DASH",
            "SOURCE_CENSUS",
            "SOURCE_CENSUS_SHA256",
            "CHARACTER_CENSUS",
            "CHARACTER_CENSUS_SHA256",
        ),
        help=(
            "safe partial-range shader lineage, its SHA-256, immutable bundle, "
            "optional exclusion, plus exact source- and character-census "
            "authorities and SHA-256 pins"
        ),
    )
    p_material_coverage.add_argument(
        "--output", type=Path, required=True, help="new payload-free JSON report"
    )
    p_material_coverage.set_defaults(func=cmd_character_material_coverage_union)

    p_material_coverage_export = sub.add_parser(
        "character-material-coverage-export",
        help="export one exact repeated-draw material union to a strict GLB",
    )
    p_material_coverage_export.add_argument("--xpp", type=Path, required=True)
    p_material_coverage_export.add_argument("--xpp-sha256", required=True)
    p_material_coverage_export.add_argument(
        "--texture-allowlist", type=Path, required=True
    )
    p_material_coverage_export.add_argument("--record-offset", type=int, required=True)
    p_material_coverage_export.add_argument(
        "--anchor-lineage", type=Path, required=True
    )
    p_material_coverage_export.add_argument("--anchor-lineage-sha256", required=True)
    p_material_coverage_export.add_argument(
        "--observation",
        action="append",
        nargs=4,
        metavar=("REPORT", "REPORT_SHA256", "BUNDLE", "EXCLUSION_OR_DASH"),
        required=True,
        help=(
            "exact observed-only report, SHA-256, immutable bundle, and optional "
            "capture-key exclusion ('-' for none); repeat for every union member"
        ),
    )
    p_material_coverage_export.add_argument(
        "--partial-observation",
        action="append",
        nargs=8,
        metavar=(
            "LINEAGE",
            "LINEAGE_SHA256",
            "BUNDLE",
            "EXCLUSION_OR_DASH",
            "SOURCE_CENSUS",
            "SOURCE_CENSUS_SHA256",
            "CHARACTER_CENSUS",
            "CHARACTER_CENSUS_SHA256",
        ),
        help=(
            "safe partial-range shader lineage, SHA-256, immutable bundle, "
            "optional exclusion, plus exact source- and character-census "
            "authorities and SHA-256 pins"
        ),
    )
    p_material_coverage_export.add_argument("--output-glb", type=Path, required=True)
    p_material_coverage_export.add_argument("--output-report", type=Path, required=True)
    p_material_coverage_export.set_defaults(func=cmd_character_material_coverage_export)

    p_material_pass_census = sub.add_parser(
        "character-material-pass-census",
        help="compare exact triangle coverage across different character material passes",
    )
    p_material_pass_census.add_argument("--xpp", type=Path, required=True)
    p_material_pass_census.add_argument("--xpp-sha256", required=True)
    p_material_pass_census.add_argument("--texture-allowlist", type=Path, required=True)
    p_material_pass_census.add_argument("--record-offset", type=int, required=True)
    p_material_pass_census.add_argument(
        "--observation",
        action="append",
        nargs=4,
        metavar=("REPORT", "REPORT_SHA256", "BUNDLE", "EXCLUSION_OR_DASH"),
        required=True,
        help=(
            "strict one-draw report, SHA-256, immutable bundle, and optional "
            "capture-key exclusion ('-' for none); repeat for every pass"
        ),
    )
    p_material_pass_census.add_argument(
        "--output", type=Path, required=True, help="new payload-free JSON census"
    )
    p_material_pass_census.set_defaults(func=cmd_character_material_pass_census)

    p_material_export = sub.add_parser(
        "character-material-export",
        help="export one shader-proved character UV/material component to GLB",
    )
    material_source = p_material_export.add_mutually_exclusive_group(required=True)
    material_source.add_argument("--xpp", type=Path)
    material_source.add_argument("--psarc", type=Path)
    p_material_export.add_argument("--entry")
    p_material_export.add_argument(
        "--bundle", type=Path, required=True, help="complete immutable v3/v4 bundle"
    )
    p_material_export.add_argument("--texture-allowlist", type=Path, required=True)
    _add_capture_key_exclusion(p_material_export)
    p_material_export.add_argument("--lineage", type=Path, required=True)
    p_material_export.add_argument("--lineage-sha256", required=True)
    p_material_export.add_argument(
        "--material-coverage-mode",
        choices=("observed-only", "preview-full-record"),
        default="observed-only",
        help=(
            "observed-only marks unproved faces separately; preview-full-record "
            "visually extrapolates the observed material but keeps proof false"
        ),
    )
    p_material_export.add_argument("--output-glb", type=Path, required=True)
    p_material_export.add_argument("--output-report", type=Path, required=True)
    p_material_export.set_defaults(func=cmd_character_material_export)

    p_capture_key_exclusion = sub.add_parser(
        "runtime-capture-key-exclusion",
        help="write the exact cumulative key manifest needed by the next page",
    )
    p_capture_key_exclusion.add_argument("--bundle", type=Path, required=True)
    p_capture_key_exclusion.add_argument(
        "--texture-allowlist", type=Path, required=True
    )
    _add_capture_key_exclusion(p_capture_key_exclusion)
    p_capture_key_exclusion.add_argument("--output", type=Path, required=True)
    p_capture_key_exclusion.add_argument("--json-out", type=Path, required=True)
    p_capture_key_exclusion.set_defaults(func=cmd_runtime_capture_key_exclusion)

    p_position_replay = sub.add_parser(
        "runtime-position-replay-export",
        help="replay selected draws into one shared pre-projection diagnostic GLB",
    )
    p_position_replay.add_argument("--bundle", type=Path, required=True)
    p_position_replay.add_argument("--texture-allowlist", type=Path, required=True)
    _add_capture_key_exclusion(p_position_replay)
    p_position_replay.add_argument(
        "--events",
        required=True,
        help="canonical comma-separated event numbers, for example 1,2,3",
    )
    p_position_replay.add_argument("--projection-event", type=int, required=True)
    p_position_replay.add_argument("--model-constant-start", type=int, default=256)
    p_position_replay.add_argument("--projection-constant-start", type=int, default=263)
    p_position_replay.add_argument("--output", type=Path, required=True)
    p_position_replay.add_argument("--json-out", type=Path, required=True)
    p_position_replay.set_defaults(func=cmd_runtime_position_replay_export)

    p_screen_replay = sub.add_parser(
        "runtime-screen-position-replay-export",
        help="replay selected draws into their screenshot-aligned NDC frame",
    )
    p_screen_replay.add_argument("--bundle", type=Path, required=True)
    p_screen_replay.add_argument("--texture-allowlist", type=Path, required=True)
    _add_capture_key_exclusion(p_screen_replay)
    p_screen_replay.add_argument(
        "--events",
        required=True,
        help="unique canonical comma-separated positive event numbers",
    )
    p_screen_replay.add_argument("--output", type=Path, required=True)
    p_screen_replay.add_argument("--json-out", type=Path, required=True)
    p_screen_replay.set_defaults(func=cmd_runtime_screen_position_replay_export)

    p_screen_page_merge = sub.add_parser(
        "runtime-screen-position-page-merge",
        help="combine one exact v3 page and chained v4 pages in screenshot space",
    )
    p_screen_page_merge.add_argument(
        "--page-bundle", type=Path, action="append", required=True
    )
    p_screen_page_merge.add_argument("--page-events", action="append", required=True)
    p_screen_page_merge.add_argument(
        "--page-capture-key-exclusion",
        action="append",
        required=True,
        help="exact page exclusion path, or - for the base v3 page",
    )
    p_screen_page_merge.add_argument("--texture-allowlist", type=Path, required=True)
    p_screen_page_merge.add_argument("--output", type=Path, required=True)
    p_screen_page_merge.add_argument("--json-out", type=Path, required=True)
    p_screen_page_merge.set_defaults(func=cmd_runtime_screen_position_page_merge)

    p_page_family_census = sub.add_parser(
        "runtime-page-family-census",
        help="classify persistent draw families across an exact v3/v4 page chain",
    )
    p_page_family_census.add_argument(
        "--page-bundle", type=Path, action="append", required=True
    )
    p_page_family_census.add_argument(
        "--page-capture-key-exclusion",
        action="append",
        required=True,
        help="exact page exclusion path, or - for the base v3 page",
    )
    p_page_family_census.add_argument("--texture-allowlist", type=Path, required=True)
    p_page_family_census.add_argument("--json-out", type=Path, required=True)
    p_page_family_census.set_defaults(func=cmd_runtime_page_family_census)

    p_xpp_source_census = sub.add_parser(
        "runtime-xpp-source-census",
        help="bind paged runtime draws to exact XPP stream-zero byte slices",
    )
    p_xpp_source_census.add_argument("--xpp", type=Path, required=True)
    p_xpp_source_census.add_argument(
        "--page-bundle", type=Path, action="append", required=True
    )
    p_xpp_source_census.add_argument(
        "--page-capture-key-exclusion",
        action="append",
        required=True,
        help="exact page exclusion path, or - for the base v3 page",
    )
    p_xpp_source_census.add_argument("--texture-allowlist", type=Path, required=True)
    p_xpp_source_census.add_argument("--json-out", type=Path, required=True)
    p_xpp_source_census.set_defaults(func=cmd_runtime_xpp_source_census)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
