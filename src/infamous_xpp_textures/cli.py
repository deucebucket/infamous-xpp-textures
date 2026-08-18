"""Command-line interface for if1-tex."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .decode import extract_package, load_xpp_bytes
from .heap import read_records, verify_layout
from .xpp import parse_xpp


def _add_source(p: argparse.ArgumentParser) -> None:
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--xpp", type=Path, help="already-extracted .xpp file")
    src.add_argument("--psarc", type=Path, help="PSARC archive that contains the .xpp")
    p.add_argument("--entry", help="path inside the PSARC, e.g. /A16.xpp")


def _load(args: argparse.Namespace) -> tuple[bytes, str]:
    if args.psarc is not None and not args.entry:
        raise SystemExit("--psarc requires --entry")
    return load_xpp_bytes(xpp=args.xpp, psarc=args.psarc, entry=args.entry)


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
    return 0 if written or args.list else (0 if found else 1)


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="if1-tex",
        description="Extract inFAMOUS 1 (PS3) textures from PACK-v8 .xpp packages.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="describe every texture, write nothing")
    _add_source(p_list)
    p_list.add_argument("--index", type=int, help="only this descriptor index")
    p_list.set_defaults(func=cmd_list)

    p_ex = sub.add_parser("extract", help="decode one package to PNG")
    _add_source(p_ex)
    p_ex.add_argument("--outdir", type=Path, default=Path("out"))
    p_ex.add_argument("--level", type=int, default=0, help="mip level (0 = largest)")
    p_ex.add_argument("--index", type=int, help="only this descriptor index")
    p_ex.add_argument("--max", type=int, help="stop after N textures")
    p_ex.add_argument("--list", action="store_true", help=argparse.SUPPRESS)
    p_ex.set_defaults(func=cmd_extract)

    p_all = sub.add_parser("extract-all", help="walk a directory of .xpp files")
    p_all.add_argument("--xpp-dir", type=Path, required=True, help="folder of extracted .xpp")
    p_all.add_argument("--outdir", type=Path, default=Path("out"))
    p_all.add_argument("--level", type=int, default=0)
    p_all.set_defaults(func=cmd_extract_all)

    p_ver = sub.add_parser("verify", help="check 128-byte heap-pad layout on one package")
    _add_source(p_ver)
    p_ver.set_defaults(func=cmd_verify)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
