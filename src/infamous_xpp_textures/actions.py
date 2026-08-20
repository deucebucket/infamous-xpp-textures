"""Shared jobs used by the window and the CLI. No GUI imports."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass
from pathlib import Path

from .decode import extract_package, load_xpp_bytes
from .heap import read_records, verify_layout
from .mesh import MeshExportError, export_glb, find_mesh_sections
from .pack import PackError, pack_replacements, replacements_from_dir, replacements_from_scale
from .xpp import parse_xpp


@dataclass(frozen=True)
class Inspection:
    path: str
    stem: str
    textures: int
    skipped: int
    max_2d_dim: int
    cubemaps: int
    static_sections: int
    layout_ok: int
    layout_total: int
    mesh_note: str

    def summary(self) -> str:
        bits = [f"{self.textures} picture(s)"]
        if self.cubemaps:
            bits.append(f"{self.cubemaps} cube map(s) (those stay as-is when packing HD)")
        if self.skipped:
            bits.append(f"{self.skipped} skipped")
        bits.append(f"{self.static_sections} 3D piece(s)")
        if self.mesh_note:
            bits.append(self.mesh_note)
        if self.layout_total:
            bits.append(f"layout {self.layout_ok}/{self.layout_total}")
        return " · ".join(bits)


def choose_hd_scale(max_2d_dim: int) -> int:
    """4× when the biggest 2D texture is 512 or smaller, else 2×."""
    if max_2d_dim <= 0:
        return 2
    return 4 if max_2d_dim <= 512 else 2


def default_work_dir(src: Path) -> Path:
    return src.with_name(src.stem + "_if1tex")


def inspect_bytes(data: bytes, *, path: str = "", stem: str = "") -> Inspection:
    pkg = parse_xpp(data, len(data))
    recs = read_records(data, pkg)
    ok, tot = verify_layout(recs)
    twod = [r for r in recs if r.faces == 1]
    cubes = [r for r in recs if r.faces == 6]
    max_dim = max((max(r.width, r.height) for r in twod), default=0)
    try:
        sections = find_mesh_sections(data, pkg)
        note = "" if sections else "no static 3D mesh (character packages look like this)"
    except MeshExportError as exc:
        sections = []
        note = str(exc)
    from .heap import read_all_descriptors

    all_desc = read_all_descriptors(data, pkg)
    skipped = sum(1 for _i, _raw, rec, reason in all_desc if rec is None or reason)
    return Inspection(
        path=path,
        stem=stem,
        textures=len(recs),
        skipped=skipped,
        max_2d_dim=max_dim,
        cubemaps=len(cubes),
        static_sections=len(sections),
        layout_ok=ok,
        layout_total=tot,
        mesh_note=note,
    )


def inspect_path(path: Path) -> Inspection:
    data, stem = load_xpp_bytes(xpp=path)
    return inspect_bytes(data, path=str(path), stem=stem)


def _capture(fn) -> tuple[object, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn()
    return result, buf.getvalue()


def pull_pictures(src: Path, outdir: Path) -> tuple[Inspection, int, int, str]:
    data, stem = load_xpp_bytes(xpp=src)
    info = inspect_bytes(data, path=str(src), stem=stem)
    outdir.mkdir(parents=True, exist_ok=True)
    (found, written), log = _capture(lambda: extract_package(data, stem, outdir))
    return info, found, written, log


def pack_hd(src: Path, dest: Path, *, scale: int | None = None, from_dir: Path | None = None) -> tuple[Inspection, int, str]:
    data, stem = load_xpp_bytes(xpp=src)
    info = inspect_bytes(data, path=str(src), stem=stem)
    replacements: dict[int, tuple[int, int, bytes]] = {}
    used_scale = scale if scale is not None else choose_hd_scale(info.max_2d_dim)
    # Scale first, then --from-dir wins on the same index (CLI order).
    if from_dir is None or scale is not None:
        replacements.update(replacements_from_scale(data, used_scale))
    if from_dir is not None:
        replacements.update(replacements_from_dir(stem, from_dir))
    if not replacements:
        raise PackError("nothing to pack")
    try:
        out = pack_replacements(data, replacements, allow_resize=True)
    except (PackError, ValueError) as exc:
        raise PackError(str(exc)) from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(out)
    return info, used_scale, f"wrote {dest} ({len(out):,} bytes) replaced {sorted(replacements)}"


def find_hd_albedo(stem: str, index: int, hd_dir: Path) -> Path | None:
    hits = sorted(hd_dir.rglob(f"{stem}.{index}.mip0.png"))
    return hits[0] if hits else None


def save_model(
    src: Path,
    dest: Path,
    *,
    remaster: bool = False,
    hd_dir: Path | None = None,
    assemble: str = "unique-largest",
) -> tuple[Inspection, dict]:
    from .assemble import select as assemble_select

    data, stem = load_xpp_bytes(xpp=src)
    info = inspect_bytes(data, path=str(src), stem=stem)
    dest.parent.mkdir(parents=True, exist_ok=True)
    texture = None
    maps_dir = dest.parent / "pbr" if remaster else None
    pkg = parse_xpp(data, len(data))
    sections = find_mesh_sections(data, pkg)
    picked = assemble_select(sections, mode=assemble, offsets=None, stem=stem)
    offsets = {s.record_offset for s in picked} if picked else None
    search = hd_dir
    if remaster and search is not None and picked:
        from .mesh import bound_albedo

        try:
            _w, _h, _rgba, idx = bound_albedo(data, pkg, picked[0].material_offset)
            texture = find_hd_albedo(stem, idx, search)
        except MeshExportError:
            texture = None
    result = export_glb(
        data,
        dest,
        record_offsets=offsets,
        texture_path=texture,
        pbr=remaster,
        maps_dir=maps_dir,
    )
    if texture is not None:
        result["albedo"] = str(texture)
    return info, result


def as_plain(info: Inspection) -> dict:
    return asdict(info)
