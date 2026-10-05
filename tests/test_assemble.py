from infamous_xpp_textures.assemble import (
    BUS_INTACT,
    SPY_DRONE_INTACT,
    recipe_for,
    unique_first,
    unique_largest,
)
from infamous_xpp_textures.compile import gltf_to_game
from infamous_xpp_textures.mesh import MeshSection
from infamous_xpp_textures.names import contact_from_sections, offsets_from_contact


def _sec(off: int, oid: int, tris: int, material: int = 0) -> MeshSection:
    return MeshSection(
        record_offset=off,
        oid=oid,
        triangle_count=tris,
        material_offset=material,
        attribute_count=5,
        bounds=(0, 0, 0, 1, 1, 1),
        position_offset=0,
        attribute_offset=0,
        index_offset=0,
        vertex_count=tris,
        split_streams=False,
    )


def test_spy_drone_recipe_is_single_flight_body():
    assert recipe_for("msn_spy_drones.sprig") == (0x20480,)
    assert recipe_for("msn_spy_drones") == SPY_DRONE_INTACT


def test_bus_recipe_is_monster_bridge_hull_and_wheels():
    assert recipe_for("msn_monster_bridge.sprig") == BUS_INTACT
    assert recipe_for("msn_monster_bridge") == BUS_INTACT
    assert 0x4d230 in BUS_INTACT
    assert 0x4dbf0 in BUS_INTACT
    assert 0xa000 not in BUS_INTACT  # origin-local FRONT_BUMPER1


def test_unique_largest_keeps_intact_chassis():
    # heli-shaped: same oid, intact first then wrecked smaller/larger
    sections = [
        _sec(0x1450, 0x191cb, 4342),
        _sec(0x14b0, 0x25e6b, 400),
        _sec(0x1b10, 0x191cb, 3854),
        _sec(0x1b70, 0x25e6b, 46),
    ]
    picked = unique_largest(sections)
    assert [s.record_offset for s in picked] == [0x1450, 0x14b0]


def test_unique_first_keeps_package_order():
    sections = [
        _sec(0x1450, 1, 10),
        _sec(0x14b0, 2, 10),
        _sec(0x1b10, 1, 99),
    ]
    picked = unique_first(sections)
    assert [s.record_offset for s in picked] == [0x1450, 0x14b0]


def test_axis_roundtrip():
    gx, gy, gz = 1.5, -4.0, 9.0
    # export: (x, z, -y)
    tx, ty, tz = gx, gz, -gy
    back = gltf_to_game(tx, ty, tz)
    assert back == (gx, gy, gz)


def test_contact_audit_converts_record_offsets():
    data = {
        "source": "infamous1__wf_helicopter_transport.xpp",
        "sections": [
            {"recordOffset": 5200, "oidName": "CHASIS"},
            {"recordOffset": 5488, "oidName": "GEOM_tail_section"},
        ],
        "exclude": ["0x1570"],
    }
    assert offsets_from_contact(data) == {0x1450}


def test_contact_include_wins():
    data = {
        "include": ["0x1450", "0x1690"],
        "exclude": ["0x1450"],
        "sections": [{"recordOffset": 1}],
    }
    assert offsets_from_contact(data) == {0x1690}


def test_contact_from_sections_drops_wrecked_and_breakaway():
    sections = [
        _sec(0x1450, 102859, 4342, 0x94B90),
        _sec(0x1570, 155211, 425, 0x94B90),
        _sec(0x1690, 155212, 871, 0x94B90),
        _sec(0x19f0, 15695, 18, 0x94A30),
        _sec(0x1b10, 102859, 3854, 0x94E30),
        _sec(0x1bd0, 155212, 160, 0x94E30),
        _sec(0x1c90, 155212, 213, 0x94CE0),
    ]
    names = {
        102859: "CHASIS",
        155211: "GEOM_tail_section",
        155212: "GEOM_cockpit",
        15695: "light4",
    }
    contact = contact_from_sections(sections, names, "wf_helicopter_transport")
    assert contact["include"] == ["0x1450", "0x1690", "0x19f0", "0x1bd0", "0x1c90"]
    assert contact["glass"] == ["0x19f0", "0x1bd0", "0x1c90"]
    assert "0x1570" in contact["exclude"]
    assert "0x1b10" in contact["exclude"]
    assert contact["sections"][0]["oidName"] == "CHASIS"
