"""Human-readable XPP package dump."""

from __future__ import annotations

from .decode import iter_textures
from .heap import read_records, verify_layout
from .mesh import GEOMETRY_HEAP_CHUNK, OBJECT_CHUNK, MeshExportError, find_mesh_sections
from .xpp import TEXDESC_CHUNK, TEXEL_CHUNK, parse_xpp

CHUNK_NAME = {
    OBJECT_CHUNK: "object",
    TEXDESC_CHUNK: "texdesc",
    TEXEL_CHUNK: "texel",
    GEOMETRY_HEAP_CHUNK: "geom",
    0x02040000: "link",
    0x0C100000: "overlay",
    0x06040000: "fixup-src",
}


def inspect_bytes(data: bytes) -> dict:
    pkg = parse_xpp(data, len(data))
    recs = read_records(data, pkg)
    ok, tot = verify_layout(recs)
    try:
        sections = find_mesh_sections(data, pkg)
        mesh_note = ""
    except MeshExportError as exc:
        sections = []
        mesh_note = str(exc)
    if not sections and not mesh_note:
        mesh_note = "no static mesh sections (skinned/character packages are not this format)"
    textures = []
    for i, rec, _texels in iter_textures(data, pkg):
        textures.append(
            {
                "index": i,
                "width": rec.width,
                "height": rec.height,
                "mips": rec.mips,
                "format": rec.base_format,
                "faces": rec.faces,
            }
        )
    return {
        "version": pkg.version,
        "dataOffset": pkg.data_offset,
        "dataSize": pkg.data_size,
        "segments": [
            {"type": s.type_tag, "size": s.size, "offset": s.offset, "chunks": s.chunk_count}
            for s in pkg.segments
        ],
        "chunks": [
            {
                "type": f"0x{c.type_tag:08x}",
                "name": CHUNK_NAME.get(c.type_tag, "other"),
                "size": c.size,
                "offset": c.offset,
            }
            for c in pkg.chunks
        ],
        "textures": textures,
        "layoutOk": ok,
        "layoutTotal": tot,
        "staticSections": [
            {
                "recordOffset": f"0x{s.record_offset:x}",
                "triangles": s.triangle_count,
                "vertices": s.vertex_count,
                "oid": f"0x{s.oid:x}",
                "material": f"0x{s.material_offset:x}",
                "split": s.split_streams,
            }
            for s in sections
        ],
        "meshNote": mesh_note,
    }


def format_report(info: dict) -> str:
    lines = [
        f"PACK v{info['version']}  payload 0x{info['dataOffset']:x} + {info['dataSize']:,} bytes",
        f"segments {len(info['segments'])}  chunks {len(info['chunks'])}",
    ]
    for i, c in enumerate(info["chunks"]):
        lines.append(f"  chunk {i:3d}  {c['type']}  {c['name']:8s}  {c['size']:8d} @ 0x{c['offset']:x}")
    lines.append(f"textures {len(info['textures'])}  layout {info['layoutOk']}/{info['layoutTotal']}")
    for t in info["textures"]:
        cube = " cube" if t["faces"] == 6 else ""
        lines.append(
            f"  [{t['index']}] {t['width']}x{t['height']} m{t['mips']} fmt=0x{t['format']:02x}{cube}"
        )
    secs = info["staticSections"]
    lines.append(f"static mesh {len(secs)}")
    if info["meshNote"] and not secs:
        lines.append(f"  {info['meshNote']}")
    for s in secs:
        split = " split" if s["split"] else ""
        lines.append(
            f"  {s['recordOffset']}  tris={s['triangles']} verts={s['vertices']} "
            f"oid={s['oid']} material={s['material']}{split}"
        )
    return "\n".join(lines)
