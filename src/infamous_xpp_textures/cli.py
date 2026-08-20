"""Command-line interface for if1-tex."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .assemble import AssembleError, select as assemble_select
from .compile import MeshCompileError, compile_files
from .decode import extract_package, load_xpp_bytes
from .heap import read_records, verify_layout
from .inspect import format_report, inspect_bytes
from .edge import describe_skinned
from .mesh import MeshExportError, export_glb, find_mesh_sections
from .names import (
    contact_from_sections,
    glass_offsets_from_contact,
    load_contact,
    load_oid_names,
    offsets_from_contact,
    translations_from_contact,
)
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


def cmd_inspect(args: argparse.Namespace) -> int:
    data, stem = _load(args)
    info = inspect_bytes(data)
    info["stem"] = stem
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print(format_report(info))
    return 0 if info["textures"] or info["staticSections"] else 1


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
    data, stem = _load(args)
    pkg = parse_xpp(data, len(data))
    try:
        sections = find_mesh_sections(data, pkg)
    except MeshExportError as exc:
        print(f"mesh-list: {exc}", file=sys.stderr)
        return 1
    if not sections:
        info = describe_skinned(data, pkg)
        names = load_oid_names(args.oids) if getattr(args, "oids", None) else {}
        print(
            f"no static mesh  edge_envelopes={info['edgeEnvelopes']}  "
            f"skinned_headers={len(info['skinnedHeaders'])}  "
            f"geom_wrappers={info['geomWrappers']}  heap={info['geometryHeap']:,}"
        )
        for header in info["skinnedHeaders"]:
            label = names.get(header["oid"], "")
            named = f"  {label}" if label else ""
            print(
                f"  {header['recordOffset']}  tris={header['triangles']} "
                f"verts={header['vertices']} oid={header['oid']}{named} "
                f"material={header['material']}"
            )
        print(info["note"])
        if args.json:
            if names:
                for header in info["skinnedHeaders"]:
                    header["oidName"] = names.get(header["oid"], "")
            print(json.dumps(info, indent=2))
        return 0 if info["skinnedHeaders"] or info["edgeEnvelopes"] else 1
    names = load_oid_names(args.oids) if getattr(args, "oids", None) else {}
    if getattr(args, "contact_out", None) or getattr(args, "json", False):
        contact = contact_from_sections(sections, names, stem)
        if args.contact_out:
            args.contact_out.parent.mkdir(parents=True, exist_ok=True)
            args.contact_out.write_text(json.dumps(contact, indent=2) + "\n", encoding="utf-8")
            print(f"wrote {args.contact_out}  {len(contact['sections'])} pieces  "
                  f"include={len(contact['include'])} exclude={len(contact['exclude'])}")
        if args.json:
            print(json.dumps(contact, indent=2))
            return 0
        if args.contact_out:
            return 0
    for s in sections:
        label = names.get(s.oid, "")
        named = f"  {label}" if label else ""
        print(
            f"0x{s.record_offset:x}  tris={s.triangle_count} verts={s.vertex_count} "
            f"oid={s.oid}{named} material@0x{s.material_offset:x}"
        )
    print(f"{len(sections)} static section(s)")
    return 0


def _resolve_offsets(data: bytes, stem: str, args: argparse.Namespace) -> set[int] | None:
    if getattr(args, "contact", None):
        return offsets_from_contact(load_contact(args.contact))
    if args.record_offset:
        return set(args.record_offset)
    mode = getattr(args, "assemble", "all")
    if mode == "all" and not getattr(args, "each", False):
        return None
    pkg = parse_xpp(data, len(data))
    sections = find_mesh_sections(data, pkg)
    picked = assemble_select(sections, mode=mode, offsets=None, stem=stem)
    return {s.record_offset for s in picked}


def cmd_mesh_export(args: argparse.Namespace) -> int:
    data, stem = _load(args)
    texture = args.texture
    pkg = parse_xpp(data, len(data))
    try:
        sections = find_mesh_sections(data, pkg)
    except MeshExportError as exc:
        print(f"mesh-export: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "each", False):
        args.output.mkdir(parents=True, exist_ok=True)
        written = []
        for section in sections:
            dest = args.output / f"{stem}_0x{section.record_offset:x}.glb"
            result = export_glb(
                data,
                dest,
                record_offsets={section.record_offset},
                texture_path=texture,
                pbr=args.pbr,
                maps_dir=None,
            )
            written.append(result)
            print(f"wrote {dest}  tris={section.triangle_count} verts={section.vertex_count}")
        print(json.dumps({"pieces": len(written), "dir": str(args.output)}, indent=2))
        return 0 if written else 1
    try:
        offsets = _resolve_offsets(data, stem, args)
    except AssembleError as exc:
        print(f"mesh-export: {exc}", file=sys.stderr)
        return 1
    if texture is None and args.hd_dir and sections:
        from .mesh import bound_albedo

        _w, _h, _rgba, idx = bound_albedo(data, pkg, sections[0].material_offset)
        hits = sorted(args.hd_dir.rglob(f"{stem}.{idx}.mip0.png"))
        if hits:
            texture = hits[0]
            print(f"using HD albedo {texture}")
    glass_offsets: set[int] = set()
    translations: dict[int, tuple[float, float, float]] = {}
    if getattr(args, "contact", None):
        contact = load_contact(args.contact)
        glass_offsets = glass_offsets_from_contact(contact)
        translations = translations_from_contact(contact)
    try:
        result = export_glb(
            data,
            args.output,
            record_offsets=offsets,
            texture_path=texture,
            pbr=args.pbr,
            maps_dir=args.maps_dir,
            glass_offsets=glass_offsets,
            translations=translations or None,
        )
    except MeshExportError as exc:
        print(f"mesh-export: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    print("open that GLB in Blender")
    return 0


def cmd_mesh_compile(args: argparse.Namespace) -> int:
    try:
        info = compile_files(args.xpp, args.glb, args.out)
    except (MeshCompileError, FileNotFoundError, ValueError) as exc:
        print(f"mesh-compile: {exc}", file=sys.stderr)
        return 1
    view = args.view if args.view else args.out.with_suffix(".glb")
    try:
        data = args.out.read_bytes()
        offsets = None
        extras_off = None
        from .glbread import parse_glb

        try:
            prims = parse_glb(args.glb.read_bytes())
            extras_off = {
                int(p.extras["if1RecordOffset"])
                for p in prims
                if "if1RecordOffset" in p.extras
            }
        except Exception:
            extras_off = None
        export_glb(
            data,
            view,
            record_offsets=extras_off,
            texture_path=None,
            pbr=False,
        )
        info["blender"] = str(view)
    except MeshExportError as exc:
        print(f"compiled XPP but could not write Blender GLB: {exc}", file=sys.stderr)
    print(json.dumps(info, indent=2))
    if "blender" in info:
        print(f"open in Blender: {info['blender']}")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .ui import run_ui

    paths = [Path(p) for p in (args.paths or [])]
    if args.xpp:
        paths.append(args.xpp)
    return run_ui(paths=paths, web=args.web)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        from .ui import run_ui

        return run_ui()
    if argv[0] not in {
        "list",
        "inspect",
        "extract",
        "extract-all",
        "verify",
        "pack",
        "mesh-list",
        "mesh-export",
        "mesh-compile",
        "ui",
        "-h",
        "--help",
    } and not argv[0].startswith("-"):
        from .ui import run_ui

        return run_ui(paths=[Path(a) for a in argv])

    ap = argparse.ArgumentParser(
        prog="if1-tex",
        description="inFAMOUS 1 XPP textures (extract/pack) and static meshes. No arguments opens the window.",
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

    p_ins = sub.add_parser("inspect", help="dump package chunks, textures, and static mesh sections")
    _add_source(p_ins)
    p_ins.add_argument("--json", action="store_true")
    p_ins.set_defaults(func=cmd_inspect)

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
    p_ml.add_argument("--oids", type=Path, help="oid-names.csv so pieces print with game names")
    p_ml.add_argument("--json", action="store_true", help="dump a contact object")
    p_ml.add_argument(
        "--contact-out",
        type=Path,
        help="convert mesh-list + names into a contact JSON mesh-export can assemble",
    )
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
    p_me.add_argument(
        "--pbr",
        action="store_true",
        help="derived remaster lighting (normal + roughness + metal from the albedo)",
    )
    p_me.add_argument("--maps-dir", type=Path, help="also write albedo/normal/orm PNGs here")
    p_me.add_argument(
        "--hd-dir",
        type=Path,
        help="if set, prefer STEM.N.mip0.png from this tree as the albedo (the 4x folder)",
    )
    p_me.add_argument(
        "--assemble",
        choices=("all", "unique-largest", "unique-first", "recipe"),
        default="all",
        help="how to pick pieces: all (stacks wrecked+intact), unique-largest, "
        "unique-first, or recipe (built-in intact heli)",
    )
    p_me.add_argument(
        "--each",
        action="store_true",
        help="write one GLB per section into --output (a folder); for per-piece skinning",
    )
    p_me.add_argument(
        "--contact",
        type=Path,
        help="assemble from a contact JSON (body1-audit.json or mesh-list --contact-out)",
    )
    p_me.set_defaults(func=cmd_mesh_export)

    p_mc = sub.add_parser("mesh-compile", help="write an edited GLB back into an XPP (same topology)")
    p_mc.add_argument("--xpp", type=Path, required=True)
    p_mc.add_argument("--glb", type=Path, required=True, help="GLB from mesh-export (keeps if1 extras)")
    p_mc.add_argument("--out", type=Path, required=True, help="compiled .xpp")
    p_mc.add_argument("--view", type=Path, help="also write this GLB for Blender (default: --out with .glb)")
    p_mc.set_defaults(func=cmd_mesh_compile)

    p_ui = sub.add_parser("ui", help="open the click-and-go window")
    p_ui.add_argument("paths", nargs="*", type=Path, help="optional .xpp files or folders")
    p_ui.add_argument("--web", action="store_true", help="force the local browser page")
    _add_source(p_ui, required=False)
    p_ui.set_defaults(func=cmd_ui)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
