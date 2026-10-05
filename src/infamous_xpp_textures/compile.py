"""Compile an edited static mesh GLB back into an XPP.

Same-topology only. Vertex count, triangle count, and stream layout stay
what the package already has. New topology / skinned Edge packages are
refused. Positions are written in the game's Z-up joint-local space.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

from .glbread import GlbReadError, parse_glb
from .mesh import JointBinding, MeshExportError, find_mesh_sections, joint_bindings
from .xpp import parse_xpp


class MeshCompileError(ValueError):
    pass


def gltf_to_game(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Inverse of export: game (x,y,z) → glTF (x, z, -y)."""
    return (x, -z, y)


def invert_joint(binding: JointBinding, xyz: tuple[float, float, float]) -> tuple[float, float, float]:
    """Undo apply_joint. R is treated as orthonormal (det ≈ 1)."""
    m = binding.local_to_world
    tx, ty, tz = xyz[0] - m[9], xyz[1] - m[10], xyz[2] - m[11]
    return (
        m[0] * tx + m[3] * ty + m[6] * tz,
        m[1] * tx + m[4] * ty + m[7] * tz,
        m[2] * tx + m[5] * ty + m[8] * tz,
    )


def _write_bounds(buf: bytearray, record: int, payload: int, pts: list[tuple[float, float, float]]) -> None:
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    for i, value in enumerate(lo + hi):
        struct.pack_into(">f", buf, payload + record + 16 + i * 4, value)


def compile_glb(xpp: bytes, glb: bytes) -> tuple[bytes, dict]:
    parsed = parse_xpp(xpp, len(xpp))
    sections = find_mesh_sections(xpp, parsed)
    if not sections:
        raise MeshCompileError("no static mesh sections to compile into")
    by_off = {s.record_offset: s for s in sections}
    try:
        bindings = joint_bindings(xpp, parsed)
    except MeshExportError:
        bindings = {}
    try:
        prims = parse_glb(glb)
    except GlbReadError as exc:
        raise MeshCompileError(str(exc)) from exc

    out = bytearray(xpp)
    payload = parsed.data_offset
    replaced: list[int] = []
    for prim in prims:
        raw_off = prim.extras.get("if1RecordOffset")
        if raw_off is None:
            if len(prims) == 1 and len(sections) == 1:
                section = sections[0]
            else:
                raise MeshCompileError(
                    "GLB primitive has no if1RecordOffset; export from if1-tex or pass one section"
                )
        else:
            section = by_off.get(int(raw_off))
            if section is None:
                raise MeshCompileError(f"no static section at 0x{int(raw_off):x}")
        if len(prim.positions) != section.vertex_count:
            raise MeshCompileError(
                f"0x{section.record_offset:x}: GLB has {len(prim.positions)} verts, "
                f"package has {section.vertex_count} (same-topology only)"
            )
        if len(prim.indices) != section.triangle_count * 3:
            raise MeshCompileError(
                f"0x{section.record_offset:x}: GLB has {len(prim.indices)} indices, "
                f"package has {section.triangle_count * 3}"
            )
        if prim.texcoords and len(prim.texcoords) != section.vertex_count:
            raise MeshCompileError(f"0x{section.record_offset:x}: UV count mismatch")

        pos_stride = 12 if section.split_streams else 26
        attr_stride = 14 if section.split_streams else 26
        binding = bindings.get(section.oid)
        game_pts: list[tuple[float, float, float]] = []
        for i, (px, py, pz) in enumerate(prim.positions):
            gx, gy, gz = gltf_to_game(px, py, pz)
            if binding is not None:
                gx, gy, gz = invert_joint(binding, (gx, gy, gz))
            if not all(math.isfinite(v) for v in (gx, gy, gz)):
                raise MeshCompileError(f"0x{section.record_offset:x}: non-finite vertex {i}")
            game_pts.append((gx, gy, gz))
            struct.pack_into(
                ">3f",
                out,
                payload + section.position_offset + i * pos_stride,
                gx,
                gy,
                gz,
            )
            if prim.texcoords:
                u, v = prim.texcoords[i]
                struct.pack_into(
                    ">2e",
                    out,
                    payload + section.attribute_offset + i * attr_stride + 8,
                    u,
                    v,
                )
        for i, value in enumerate(prim.indices):
            if value >= section.vertex_count:
                raise MeshCompileError(f"0x{section.record_offset:x}: index {value} out of range")
            struct.pack_into(
                ">H",
                out,
                payload + section.index_offset + i * 2,
                value,
            )
        _write_bounds(out, section.record_offset, payload, game_pts)
        replaced.append(section.record_offset)

    return bytes(out), {
        "replaced": [f"0x{o:x}" for o in replaced],
        "sections": len(replaced),
        "note": "same-topology compile; skinned/Edge packages are not this path",
    }


def compile_files(xpp_path: Path, glb_path: Path, dest: Path) -> dict:
    packed, info = compile_glb(xpp_path.read_bytes(), glb_path.read_bytes())
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(packed)
    info["output"] = str(dest)
    info["bytes"] = len(packed)
    return info
