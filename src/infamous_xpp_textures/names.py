"""OID name table (oid-names.csv) and contact-sheet JSON."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .assemble import AssembleError
from .mesh import MeshSection


def load_oid_names(path: Path) -> dict[int, str]:
    """Read config/oid-names.csv → {oid: name}."""
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "oid_index" not in reader.fieldnames:
            raise AssembleError(f"{path} is not oid-names.csv (need oid_index,name)")
        names: dict[int, str] = {}
        for row in reader:
            raw = (row.get("oid_index") or "").strip()
            name = (row.get("name") or "").strip()
            if not raw or not name:
                continue
            try:
                names[int(raw, 0)] = name
            except ValueError:
                continue
    return names


def _as_offset(value: object) -> int:
    if isinstance(value, bool):
        raise AssembleError(f"bad offset {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value.strip(), 0)
    raise AssembleError(f"bad offset {value!r}")


def load_contact(path: Path) -> dict:
    """Load a contact JSON (body1-audit.json or a converted contact)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AssembleError(f"{path} is not a contact object")
    return data


def glass_offsets_from_contact(data: dict) -> set[int]:
    return {_as_offset(item) for item in data.get("glass") or []}


def translations_from_contact(data: dict) -> dict[int, tuple[float, float, float]]:
    """Optional contact.translate: {\"0x..\": [dx,dy,dz]} in game space."""
    raw = data.get("translate") or {}
    if not isinstance(raw, dict):
        raise AssembleError("contact translate must be an object")
    out: dict[int, tuple[float, float, float]] = {}
    for key, value in raw.items():
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise AssembleError(f"contact translate {key!r} needs [dx,dy,dz]")
        out[_as_offset(key)] = (float(value[0]), float(value[1]), float(value[2]))
    return out


def offsets_from_contact(data: dict) -> set[int]:
    """Offsets the contact says to assemble.

    Prefer include[] when present. Otherwise take every sections[].recordOffset
    minus exclude[]. This is how body1-audit.json converts.
    """
    exclude = {_as_offset(x) for x in data.get("exclude") or []}
    if data.get("include"):
        return {_as_offset(x) for x in data["include"]} - exclude
    sections = data.get("sections") or []
    if not sections:
        raise AssembleError("contact has no include[] or sections[]")
    offs: set[int] = set()
    for section in sections:
        if isinstance(section, dict):
            raw = section.get("recordOffset", section.get("offset"))
        else:
            raw = section
        offs.add(_as_offset(raw))
    return offs - exclude


# Later copies of these oids are the wrecked hull/rotor, not windows.
# Extra GEOM_cockpit / light / spotlight copies stay in the intact set.
WRECK_COPY_NAMES = frozenset({"CHASIS", "joint_mini_rotor", "GEOM_spy_plane"})
BREAKAWAY_NAMES = frozenset({"GEOM_tail_section", "GEOM_Broken"})
GLASS_NAMES = frozenset({"light3", "light4"})
GLASS_EXTRA_NAMES = frozenset({"GEOM_cockpit", "tailSection"})


def contact_from_sections(
    sections: list[MeshSection],
    names: dict[int, str],
    stem: str,
) -> dict:
    """Join mesh-list + oid names into a contact the exporter can consume."""
    seen: dict[int, int] = {}
    rows: list[dict] = []
    for section in sections:
        first = section.oid not in seen
        if first:
            seen[section.oid] = section.record_offset
        name = names.get(section.oid, "")
        if name in BREAKAWAY_NAMES:
            role = "breakaway"
        elif not first and name in WRECK_COPY_NAMES:
            role = "wrecked"
        elif name in GLASS_NAMES or (not first and name in GLASS_EXTRA_NAMES):
            role = "glass"
        elif not first:
            role = "extra"
        else:
            role = "intact"
        rows.append(
            {
                "recordOffset": section.record_offset,
                "offset": f"0x{section.record_offset:x}",
                "oid": section.oid,
                "oidName": name,
                "triangles": section.triangle_count,
                "vertices": section.vertex_count,
                "material": f"0x{section.material_offset:x}",
                "role": role,
            }
        )
    include = [row["offset"] for row in rows if row["role"] in {"intact", "extra", "glass"}]
    exclude = [row["offset"] for row in rows if row["role"] in {"breakaway", "wrecked"}]
    glass = [row["offset"] for row in rows if row["role"] == "glass"]
    return {
        "source": stem,
        "include": include,
        "exclude": exclude,
        "glass": glass,
        "sections": rows,
    }
