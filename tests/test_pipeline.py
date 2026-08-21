"""Synthetic end-to-end XPP/PSARC profile tests. No retail bytes required."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infamous_xpp_textures.pipeline import build_profile, extract_profile
from infamous_xpp_textures.psarc import build_archive, extract_entry

from test_synthetic import _minimal_xpp


def _write_pair(tmp_path: Path) -> tuple[Path, Path, bytes, bytes]:
    retail1_xpp = _minimal_xpp(extra=bytes([1]) * 8)
    retail2_xpp = _minimal_xpp(extra=bytes([2]) * 8)
    install1 = tmp_path / "retail" / "install1" / "infamous1.psarc_s"
    install2 = tmp_path / "retail" / "install2" / "infamous2.psarc_s"
    install1.parent.mkdir(parents=True)
    install2.parent.mkdir(parents=True)
    install1.write_bytes(
        build_archive(
            ["/textures/A1.xpp", "/notes/readme.txt"],
            [retail1_xpp, b"keep-install1"],
        )
    )
    install2.write_bytes(build_archive(["/characters/A2.xpp"], [retail2_xpp]))
    return install1, install2, retail1_xpp, retail2_xpp


def test_profile_extract_writes_packages_and_machine_manifest(tmp_path: Path):
    install1, install2, retail1_xpp, retail2_xpp = _write_pair(tmp_path)
    workspace = tmp_path / "workspace"

    manifest = extract_profile(install1, install2, workspace)

    assert manifest["kind"] == "xpp-workspace"
    assert (workspace / "xpp/install1/textures/A1.xpp").read_bytes() == retail1_xpp
    assert (workspace / "xpp/install2/characters/A2.xpp").read_bytes() == retail2_xpp
    stored = json.loads((workspace / "workspace.json").read_text())
    assert stored["schema_version"] == 1
    assert [archive["entries_with_manifest"] for archive in stored["archives"]] == [3, 2]


def test_profile_build_routes_replacements_and_audits_complete_pair(tmp_path: Path):
    install1, install2, _retail1_xpp, retail2_xpp = _write_pair(tmp_path)
    replacements = tmp_path / "replacements"
    replacements.mkdir()
    modded = _minimal_xpp(extra=bytes([9]) * 8)
    (replacements / "A1.xpp").write_bytes(modded)
    profile = tmp_path / "profile"

    manifest = build_profile(install1, install2, replacements, profile)

    assert extract_entry(profile / "infamous1.psarc_s", "/textures/A1.xpp") == modded
    assert extract_entry(profile / "infamous1.psarc_s", "/notes/readme.txt") == b"keep-install1"
    assert extract_entry(profile / "infamous2.psarc_s", "/characters/A2.xpp") == retail2_xpp
    assert (profile / "infamous2.psarc_s").read_bytes() == install2.read_bytes()
    assert manifest["replacement_count"] == 1
    assert [archive["entries_audited"] for archive in manifest["archives"]] == [3, 2]
    assert [archive["replaced_entries"] for archive in manifest["archives"]] == [1, 0]
    stored = json.loads((profile / "profile.json").read_text())
    assert stored["archives"][0]["output_sha256"] == manifest["archives"][0]["output_sha256"]


def test_profile_build_rejects_unknown_replacement_without_partial_output(tmp_path: Path):
    install1, install2, _retail1_xpp, _retail2_xpp = _write_pair(tmp_path)
    replacements = tmp_path / "replacements"
    replacements.mkdir()
    (replacements / "missing.xpp").write_bytes(_minimal_xpp())
    profile = tmp_path / "profile"

    with pytest.raises(ValueError, match="absent from both retail PSARCs"):
        build_profile(install1, install2, replacements, profile)

    assert not profile.exists()


def test_profile_build_rejects_malformed_replacement(tmp_path: Path):
    install1, install2, _retail1_xpp, _retail2_xpp = _write_pair(tmp_path)
    replacements = tmp_path / "replacements"
    replacements.mkdir()
    (replacements / "A1.xpp").write_bytes(b"not-an-xpp")

    with pytest.raises(ValueError, match="not a valid XPP"):
        build_profile(install1, install2, replacements, tmp_path / "profile")


def test_profile_extract_rejects_duplicate_package_basenames(tmp_path: Path):
    package = _minimal_xpp()
    install1 = tmp_path / "infamous1.psarc_s"
    install2 = tmp_path / "infamous2.psarc_s"
    install1.write_bytes(build_archive(["/one/shared.xpp"], [package]))
    install2.write_bytes(build_archive(["/two/SHARED.XPP"], [package]))

    with pytest.raises(ValueError, match="routing ambiguous"):
        extract_profile(install1, install2, tmp_path / "workspace")
