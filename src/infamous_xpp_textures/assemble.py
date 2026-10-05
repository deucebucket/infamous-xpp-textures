"""Pick which static sections belong to one object.

Vehicle packages often store an intact set and a wrecked set in the same
XPP. Dumping every section stacks both. These selectors match the helicopter
work: one piece per joint/oid, plus an explicit intact recipe when we have
measured one.
"""

from __future__ import annotations

from .mesh import MeshSection

# Intact transport heli from the named contact. Keep every GEOM_cockpit
# and glass piece (windows/interior). Drop only wrecked hull/rotor copies
# and the breakaway tail. There is no heli_wrecked tag.
HELI_INTACT = (
    0x1450,  # CHASIS
    0x14b0,  # joint_mini_rotor
    0x1510,  # joint_main_rotor
    0x15d0,  # tailSection
    0x1630,  # spotlight_spin
    0x1690,  # GEOM_cockpit
    0x16f0,  # GEOM_blade_1
    0x1750,  # GEOM_blade_2
    0x17b0,  # spotlight_spin
    0x1810,  # motorBase
    0x1870,  # spotlight_spin
    0x19f0,  # light4
    0x1a50,  # tailSection glass
    0x1ab0,  # light3
    0x1bd0,  # GEOM_cockpit windows
    0x1c90,  # GEOM_cockpit windows
    0x1cf0,  # spotlight_spin
)

# Intact TMW-108: GEOM_spy_plane 0x20480 is the whole flight airframe
# (nose, wings, V-tail already in one mesh). spy_head / spy_wing* /
# spy_plate* are a second posed set; 0x204e0 is the spinner.
SPY_DRONE_INTACT = (
    0x20480,  # GEOM_spy_plane flight body
)

# Intact metro bus from msn_monster_bridge. World-space BUS hull +
# roof light + mudflap + windows + mirrors + 4 wheels. Hospital /
# terror-bus XPPs are chassis+wreck only (0 joints) — not this set.
# Drop origin-local bumpers/doors/pCube604.
BUS_INTACT = (
    0x4ccf0,  # BUS trim
    0x4cd60,  # BUS trim
    0x4ce40,  # BUS trim
    0x4ceb0,  # SIDE_MIRROR_1
    0x4d0e0,  # ROOF_LIGHT_2
    0x4d1c0,  # MUD_FLAP_3
    0x4d230,  # BUS hull 1167t
    0x4d2a0,  # WINDOW_DOOR_1
    0x4d310,  # WINDOW_LEFT_
    0x4d380,  # BUS
    0x4d3f0,  # SIDE_MIRROR_1
    0x4dbf0,  # wheel_trailer1
    0x4dc50,  # wheel_trailer1
    0x4df60,  # wheel_trailer1
    0x4dfc0,  # wheel_trailer1
    0x4e2d0,  # wheel_trailer1
    0x4e330,  # wheel_trailer1
    0x4e640,  # wheel_trailer1
    0x4e6a0,  # wheel_trailer1
)

RECIPES: dict[str, tuple[int, ...]] = {
    "wf_helicopter_transport": HELI_INTACT,
    "msn_spy_drones.sprig": SPY_DRONE_INTACT,
    "msn_spy_drones": SPY_DRONE_INTACT,
    "msn_monster_bridge.sprig": BUS_INTACT,
    "msn_monster_bridge": BUS_INTACT,
}


class AssembleError(ValueError):
    pass


def unique_largest(sections: list[MeshSection]) -> list[MeshSection]:
    """One section per oid: the one with the most triangles."""
    best: dict[int, MeshSection] = {}
    for section in sections:
        prev = best.get(section.oid)
        if prev is None or section.triangle_count > prev.triangle_count:
            best[section.oid] = section
    return [s for s in sections if best.get(s.oid) is s]


def unique_first(sections: list[MeshSection]) -> list[MeshSection]:
    """One section per oid: the first in package order (often the intact set)."""
    seen: set[int] = set()
    out: list[MeshSection] = []
    for section in sections:
        if section.oid in seen:
            continue
        seen.add(section.oid)
        out.append(section)
    return out


def by_offsets(sections: list[MeshSection], offsets: set[int]) -> list[MeshSection]:
    selected = [s for s in sections if s.record_offset in offsets]
    missing = offsets - {s.record_offset for s in selected}
    if missing:
        formatted = ", ".join(f"0x{o:x}" for o in sorted(missing))
        raise AssembleError(f"records not found: {formatted}")
    return selected


def recipe_for(stem: str) -> tuple[int, ...] | None:
    return RECIPES.get(stem)


def select(
    sections: list[MeshSection],
    *,
    mode: str,
    offsets: set[int] | None,
    stem: str = "",
) -> list[MeshSection]:
    if offsets:
        return by_offsets(sections, offsets)
    if mode == "all":
        return list(sections)
    if mode == "unique-largest":
        return unique_largest(sections)
    if mode == "unique-first":
        return unique_first(sections)
    if mode == "recipe":
        rec = recipe_for(stem)
        if rec is None:
            raise AssembleError(
                f"no built-in recipe for {stem!r}; use --assemble unique-largest "
                "or --record-offset"
            )
        return by_offsets(sections, set(rec))
    raise AssembleError(f"unknown assemble mode {mode!r}")
