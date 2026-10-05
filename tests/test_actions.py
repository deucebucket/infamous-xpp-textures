from infamous_xpp_textures.actions import choose_hd_scale, inspect_bytes, pull_pictures
from test_synthetic import _minimal_xpp


def test_hd_scale_rule():
    assert choose_hd_scale(0) == 2
    assert choose_hd_scale(64) == 4
    assert choose_hd_scale(512) == 4
    assert choose_hd_scale(513) == 2
    assert choose_hd_scale(1024) == 2


def test_inspect_synthetic_texture_only():
    data = _minimal_xpp()
    info = inspect_bytes(data, path="fix.xpp", stem="fix")
    assert info.textures == 1
    assert info.static_sections == 0
    assert info.max_2d_dim == 4
    assert "no static" in info.mesh_note


def test_pull_pictures_writes_png(tmp_path):
    src = tmp_path / "fix.xpp"
    src.write_bytes(_minimal_xpp())
    out = tmp_path / "pictures"
    info, found, written, _log = pull_pictures(src, out)
    assert info.textures == 1
    assert found == 1
    assert written == 1
    assert (out / "fix.0.mip0.png").is_file()


def test_window_hd_job_opts_into_legacy_fitting(tmp_path, monkeypatch):
    from infamous_xpp_textures import actions

    src = tmp_path / "fix.xpp"
    src.write_bytes(_minimal_xpp())
    dest = tmp_path / "hd.xpp"
    real_pack = actions.pack_replacements
    calls = []

    def checked_pack(data, replacements, **options):
        calls.append(options)
        return real_pack(data, replacements, **options)

    monkeypatch.setattr(actions, "pack_replacements", checked_pack)
    info, scale, _log = actions.pack_hd(src, dest, scale=2)
    assert calls == [{"allow_resize": True, "fit_replacements": True}]
    assert scale == 2
    assert info.max_2d_dim == 4
    assert actions.inspect_path(dest).max_2d_dim == 8
