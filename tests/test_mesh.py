from infamous_xpp_textures.mesh import MeshExportError, export_glb, find_mesh_sections
from infamous_xpp_textures.xpp import parse_xpp

from test_synthetic import _minimal_xpp


def test_texture_only_package_has_no_static_mesh(tmp_path):
    data = _minimal_xpp()
    pkg = parse_xpp(data)
    sections = find_mesh_sections(data, pkg)
    assert sections == []
    try:
        export_glb(data, tmp_path / "nope.glb", record_offsets=None, texture_path=None)
    except MeshExportError as exc:
        assert "no static mesh" in str(exc).lower() or "character" in str(exc).lower()
    else:
        raise AssertionError("expected MeshExportError")
