"""Regression coverage for the combined audited-tool and local UI CLI surfaces."""

import argparse
from pathlib import Path

import pytest

from infamous_xpp_textures import cli, ui
from test_synthetic import _minimal_xpp


# All 43 main commands plus the three commands added by the local UI branch.
COMMANDS = """
asset-completion-inventory character-asset-census character-capture-report
character-component-ledger character-diagnostic-export
character-material-candidate-census character-material-coverage-export
character-material-coverage-union character-material-export
character-material-gap-locator character-material-gap-oracle
character-material-pass-census character-oracle character-report
character-source-diagnostic-export character-source-runtime-correlate
character-uv-texture-binding derive extract extract-all inspect list
mesh-compile mesh-export mesh-list pack profile-build profile-extract
profile-oracle profile-validate psarc-pack runtime-bundle
runtime-capture-key-exclusion runtime-fragment-sampler-census runtime-index
runtime-page-family-census runtime-position-replay-export
runtime-screen-position-page-merge runtime-screen-position-replay-export
runtime-topology-diagnostic-export runtime-vertex-transform-census
runtime-xpp-source-census texture-rebase ui validate verify
""".split()


@pytest.fixture(autouse=True)
def forbid_real_ui(monkeypatch):
    def unexpected_ui(**kwargs):
        pytest.fail(f"CLI command was incorrectly routed to the UI: {kwargs}")

    monkeypatch.setattr(ui, "run_ui", unexpected_ui)


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_reaches_its_parser(command, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main([command, "--help"])
    assert caught.value.code == 0
    assert command in capsys.readouterr().out


def test_every_registered_command_dispatches_without_ui(monkeypatch):
    # Exercise dispatch too, without requiring private game/capture inputs.
    def dispatch_probe(parser, argv):
        subparsers = next(
            action for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        assert set(subparsers.choices) == set(COMMANDS)
        selected = subparsers.choices[argv[0]]
        assert callable(selected.get_default("func"))
        return argparse.Namespace(func=lambda args: 73)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", dispatch_probe)
    for command in COMMANDS:
        assert cli.main([command]) == 73


@pytest.mark.parametrize("command, options", [
    ("mesh-list", ["--oids", "--json", "--contact-out"]),
    ("mesh-export", ["--record-offset", "--texture", "--pbr", "--maps-dir",
                     "--hd-dir", "--assemble", "--each", "--contact"]),
    ("mesh-compile", ["--xpp", "--glb", "--out", "--view"]),
    ("ui", ["--web", "--xpp", "--psarc", "--entry"]),
    ("pack", ["--replace", "--from-dir", "--scale", "--allow-resize", "--fit-replacements"]),
    ("validate", ["--known-startup-pass-extra", "--known-pass-extra",
                  "--known-startup-fail-extra", "--known-fail-extra",
                  "--fail-on-budget"]),
])
def test_combined_options_are_reachable(command, options, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main([command, "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert all(option in help_text for option in options)


@pytest.mark.parametrize("argv, expected", [
    ([], {}),
    (["asset.xpp"], {"paths": [Path("asset.xpp")]}),
    (["ui", "folder", "--xpp", "asset.xpp", "--web"],
     {"paths": [Path("folder"), Path("asset.xpp")], "web": True}),
])
def test_ui_launch_modes_are_preserved(argv, expected, monkeypatch):
    calls = []
    monkeypatch.setattr(ui, "run_ui", lambda **kwargs: calls.append(kwargs) or 17)
    assert cli.main(argv) == 17
    assert calls == [expected]


def test_separate_ui_entrypoint(monkeypatch):
    calls = []
    monkeypatch.setattr(ui, "run_ui", lambda **kwargs: calls.append(kwargs) or 19)
    assert ui.main(["--web", "asset.xpp"]) == 19
    assert calls == [{"paths": [Path("asset.xpp")], "web": True}]


@pytest.mark.parametrize("command", ["inspect", "verify", "list"])
def test_local_and_main_handlers_share_main_texture_apis(command, tmp_path, capsys):
    source = tmp_path / "fixture.xpp"
    source.write_bytes(_minimal_xpp())
    assert cli.main([command, "--xpp", str(source)]) == 0
    assert capsys.readouterr().out


@pytest.mark.parametrize("fit", [False, True])
def test_pack_fitting_is_explicit_opt_in(fit, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_load", lambda args: (b"source", "fixture"))
    monkeypatch.setattr(cli, "replacements_from_scale", lambda data, scale: {0: (4, 4, b"rgba")})

    def pack(data, replacements, **kwargs):
        calls.append(kwargs)
        return b"packed"

    monkeypatch.setattr(cli, "pack_replacements", pack)
    output = tmp_path / "packed.xpp"
    argv = ["pack", "--xpp", "fixture.xpp", "--scale", "2", "--out", str(output)]
    if fit:
        argv.append("--fit-replacements")
    assert cli.main(argv) == 0
    assert calls == [{"allow_resize": True, "fit_replacements": fit}]
    assert output.read_bytes() == b"packed"


def test_local_mesh_commands_handle_main_texture_only_fixture(tmp_path, capsys):
    source = tmp_path / "fixture.xpp"
    source.write_bytes(_minimal_xpp())
    assert cli.main(["mesh-list", "--xpp", str(source), "--json"]) == 1
    assert "no static mesh" in capsys.readouterr().out
    assert cli.main(["mesh-export", "--xpp", str(source),
                     "--output", str(tmp_path / "fixture.glb")]) == 1
    assert "mesh-export:" in capsys.readouterr().err
