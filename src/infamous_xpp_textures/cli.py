"""Command-line interface for if1-tex."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .character import (
    CharacterReportError,
    build_character_compatibility_report,
    render_report,
)
from .decode import extract_package, load_xpp_bytes
from .derive import derive_scaled
from .mesh import MeshExportError, export_glb, find_mesh_sections
from .pack import PackError, pack_replacements, replacements_from_dir, replacements_from_scale
from .pipeline import build_profile, extract_profile, validate_profile
from .pngio import read_png
from .psarc import rebuild_archive
from .runtime import build_replacement_bundle, build_runtime_index, write_allowlist
from .validation import ValidationError, compare_xpp, validate_xpp
from .xpp import parse_xpp


def _add_source(p: argparse.ArgumentParser, required: bool = True) -> None:
    src = p.add_mutually_exclusive_group(required=required)
    src.add_argument("--xpp", type=Path, help="already-extracted .xpp file")
    src.add_argument("--psarc", type=Path, help="PSARC archive that contains the .xpp")
    p.add_argument("--entry", help="path inside the PSARC, e.g. /A16.xpp")


def _load(args: argparse.Namespace) -> tuple[bytes, str]:
    if getattr(args, "psarc", None) is not None and not args.entry:
        raise SystemExit("--psarc requires --entry")
    return load_xpp_bytes(xpp=args.xpp, psarc=getattr(args, "psarc", None), entry=args.entry)


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
    print(f"\nall packages: {len(paths)} files, {total_found} textures, {total_written} PNGs")
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
        print("scene coverage: REQUIRED; structural success does not prove a texture is safe when used")
    if args.fail_on_budget and report["budget"]["status"] == "at-or-above-observed-startup-fail-range":
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
            progress=(None if args.json else lambda message: print(message, flush=True)),
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
    if args.fail_on_budget and report["budget"]["status"] == "at-or-above-observed-startup-fail-range":
        return 2
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
    print("scene coverage: exact misses may also mean the game transformed texels before upload")
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
        print("no static mesh sections (character/skinned packages are not this format)")
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
        result = export_glb(data, args.output, record_offsets=offsets, texture_path=args.texture)
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
    p_validate.add_argument("--json", action="store_true", help="print a machine-readable report")
    _add_budget_options(p_validate)
    p_validate.set_defaults(func=cmd_validate)

    p_pack = sub.add_parser("pack", help="encode PNGs back into an XPP")
    _add_source(p_pack)
    p_pack.add_argument("--out", type=Path, required=True, help="output .xpp")
    p_pack.add_argument("--replace", action="append", help="INDEX=file.png (repeatable)")
    p_pack.add_argument("--from-dir", type=Path, help="read STEM.N.mip0.png from this folder")
    p_pack.add_argument("--stem", help="filename stem for --from-dir (default: xpp name)")
    p_pack.add_argument("--scale", type=int, help="nearest-neighbor upscale every 2D texture")
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
    p_derive.add_argument("--retail", type=Path, required=True, help="original retail XPP")
    p_derive.add_argument("--source", type=Path, required=True, help="existing 2x/4x XPP")
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

    p_psarc = sub.add_parser(
        "psarc-pack",
        help="rebuild an install PSARC with replacement XPPs",
    )
    p_psarc.add_argument("--psarc", type=Path, required=True, help="retail source PSARC")
    p_psarc.add_argument("--xpp-dir", type=Path, required=True, help="replacement XPP directory")
    p_psarc.add_argument("--out", type=Path, required=True, help="output PSARC")
    p_psarc.add_argument(
        "--include",
        action="append",
        help="replace only this XPP basename (repeatable)",
    )
    p_psarc.add_argument("--compression-level", type=int, choices=range(1, 10), default=9)
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

    p_runtime_index = sub.add_parser(
        "runtime-index",
        help="generate exact texture hashes for emulator scene-coverage tracing",
    )
    _add_source(p_runtime_index)
    p_runtime_index.add_argument("--label", help="package/profile label stored in the report")
    p_runtime_index.add_argument("--json-out", type=Path, help="write the full JSON index")
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
    p_me.add_argument("--texture", type=Path, help="PNG to embed; default: decode from the package")
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

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
