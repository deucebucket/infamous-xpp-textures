"""Command-line interface for if1-tex."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .decode import extract_package, load_xpp_bytes
from .heap import read_records, verify_layout
from .mesh import MeshExportError, export_glb, find_mesh_sections
from .pack import PackError, pack_replacements, replacements_from_dir, replacements_from_scale
from .pngio import read_png
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
    pkg = parse_xpp(data, len(data))
    recs = read_records(data, pkg)
    ok, tot = verify_layout(recs)
    print(f"descriptors: {len(recs)}")
    print(f"layout pairs: {ok}/{tot}  (delta == align128(chain) * faces)")
    if tot and ok != tot:
        return 1
    return 0 if recs else 1


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
        out = pack_replacements(data, replacements, allow_resize=args.allow_resize or bool(args.scale))
    except (PackError, ValueError) as exc:
        print(f"pack failed: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(out)
    print(f"wrote {args.out}  ({len(out):,} bytes)  replaced {sorted(replacements)}")
    return 0


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="if1-tex",
        description="inFAMOUS 1 XPP textures (extract/pack) and static meshes.",
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

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
