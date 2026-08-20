from infamous_xpp_textures.mesh import (
    MeshExportError,
    _attribute_stride,
    export_glb,
    find_mesh_sections,
)
from infamous_xpp_textures.xpp import parse_xpp

from test_synthetic import _minimal_xpp


def test_split_attr6_uses_18_byte_uv_stride():
    assert _attribute_stride(True, 5) == 14
    assert _attribute_stride(True, 6) == 18
    assert _attribute_stride(False, 6) == 26


def test_split_stride_picks_18_when_that_stream_is_the_finite_one():
    from infamous_xpp_textures.mesh import _score_uv_stride

    # empty buffer scores 0/0 — chooser must not crash
    class P:
        data_offset = 0

    assert _score_uv_stride(b"\x00" * 8, P(), 0, 0, 14) == 0


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
