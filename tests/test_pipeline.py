"""Synthetic end-to-end XPP/PSARC profile tests. No retail bytes required."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infamous_xpp_textures.pipeline import (
    audit_archive,
    build_profile,
    extract_profile,
    validate_profile,
)
from infamous_xpp_textures.heap import chain_size
from infamous_xpp_textures.pack import pack_chains
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
    assert manifest["preflight"]["structural_status"] == "pass"
    assert manifest["preflight"]["chain_delta_bytes"] == 0
    assert [archive["entries_audited"] for archive in manifest["archives"]] == [3, 2]
    assert [archive["replaced_entries"] for archive in manifest["archives"]] == [1, 0]
    stored = json.loads((profile / "profile.json").read_text())
    assert stored["archives"][0]["output_sha256"] == manifest["archives"][0]["output_sha256"]


def test_profile_validate_checks_replacements_without_building(tmp_path: Path):
    install1, install2, _retail1_xpp, _retail2_xpp = _write_pair(tmp_path)
    replacements = tmp_path / "replacements"
    replacements.mkdir()
    (replacements / "A1.xpp").write_bytes(_minimal_xpp(extra=bytes([7]) * 8))

    report = validate_profile(install1, install2, replacements)

    assert report["kind"] == "xpp-profile-preflight"
    assert report["replacement_count"] == 1
    assert report["structural_status"] == "pass"
    assert report["budget"]["scene_coverage_required"] is True


def test_profile_build_can_refuse_observed_startup_fail_bound(tmp_path: Path):
    install1, install2, retail1_xpp, _retail2_xpp = _write_pair(tmp_path)
    replacements = tmp_path / "replacements"
    replacements.mkdir()
    promoted = pack_chains(
        retail1_xpp,
        {0: (8, 8, 2, bytes(chain_size(0x86, 8, 8, 2)))},
    )
    (replacements / "A1.xpp").write_bytes(promoted)
    profile = tmp_path / "profile"

    with pytest.raises(ValueError, match="observed startup-fail bound"):
        build_profile(
            install1,
            install2,
            replacements,
            profile,
            known_pass_extra=16,
            known_fail_extra=32,
            fail_on_budget=True,
        )

    assert not profile.exists()


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


def test_profile_extract_keeps_cross_slot_duplicate_basenames_separate(tmp_path: Path):
    package1 = _minimal_xpp(extra=bytes([1]) * 8)
    package2 = _minimal_xpp(extra=bytes([2]) * 8)
    install1 = tmp_path / "infamous1.psarc_s"
    install2 = tmp_path / "infamous2.psarc_s"
    install1.write_bytes(build_archive(["/one/shared.xpp"], [package1]))
    install2.write_bytes(build_archive(["/two/SHARED.XPP"], [package2]))
    workspace = tmp_path / "workspace"

    manifest = extract_profile(install1, install2, workspace)

    assert manifest["kind"] == "xpp-workspace"
    assert (workspace / "xpp/install1/one/shared.xpp").read_bytes() == package1
    assert (workspace / "xpp/install2/two/SHARED.XPP").read_bytes() == package2


def test_profile_build_rejects_unqualified_cross_slot_duplicate(tmp_path: Path):
    package = _minimal_xpp()
    install1 = tmp_path / "infamous1.psarc_s"
    install2 = tmp_path / "infamous2.psarc_s"
    install1.write_bytes(build_archive(["/one/shared.xpp"], [package]))
    install2.write_bytes(build_archive(["/two/shared.xpp"], [package]))
    replacements = tmp_path / "replacements"
    replacements.mkdir()
    (replacements / "shared.xpp").write_bytes(package)

    with pytest.raises(ValueError, match="2 retail owners"):
        build_profile(install1, install2, replacements, tmp_path / "profile")


def test_profile_build_routes_same_basename_to_both_explicit_slots(tmp_path: Path):
    retail1 = _minimal_xpp(extra=bytes([1]) * 8)
    retail2 = _minimal_xpp(extra=bytes([2]) * 8)
    candidate1 = _minimal_xpp(extra=bytes([3]) * 8)
    candidate2 = _minimal_xpp(extra=bytes([4]) * 8)
    install1 = tmp_path / "infamous1.psarc_s"
    install2 = tmp_path / "infamous2.psarc_s"
    install1.write_bytes(build_archive(["/one/shared.xpp"], [retail1]))
    install2.write_bytes(build_archive(["/two/shared.xpp"], [retail2]))
    replacements = tmp_path / "replacements"
    (replacements / "install1/one").mkdir(parents=True)
    (replacements / "install2/two").mkdir(parents=True)
    (replacements / "install1/one/shared.xpp").write_bytes(candidate1)
    (replacements / "install2/two/shared.xpp").write_bytes(candidate2)

    manifest = build_profile(install1, install2, replacements, tmp_path / "profile")

    assert manifest["replacement_count"] == 2
    assert extract_entry(tmp_path / "profile/infamous1.psarc_s", "/one/shared.xpp") == candidate1
    assert extract_entry(tmp_path / "profile/infamous2.psarc_s", "/two/shared.xpp") == candidate2


def test_profile_build_rejects_wrong_explicit_slot(tmp_path: Path):
    install1, install2, _retail1, retail2 = _write_pair(tmp_path)
    replacements = tmp_path / "replacements"
    (replacements / "install1").mkdir(parents=True)
    (replacements / "install1/A2.xpp").write_bytes(retail2)

    with pytest.raises(ValueError, match="absent from retail install1"):
        build_profile(install1, install2, replacements, tmp_path / "profile")


def test_profile_build_rejects_two_inputs_for_one_explicit_target(tmp_path: Path):
    install1, install2, retail1, _retail2 = _write_pair(tmp_path)
    replacements = tmp_path / "replacements"
    (replacements / "install1/textures").mkdir(parents=True)
    (replacements / "install1/alias").mkdir(parents=True)
    (replacements / "install1/textures/A1.xpp").write_bytes(retail1)
    (replacements / "install1/alias/A1.xpp").write_bytes(retail1)

    with pytest.raises(ValueError, match="same retail target"):
        build_profile(install1, install2, replacements, tmp_path / "profile")


def test_profile_build_uses_exact_path_for_within_slot_duplicate_basename(tmp_path: Path):
    retail1 = _minimal_xpp(extra=bytes([1]) * 8)
    retail2 = _minimal_xpp(extra=bytes([2]) * 8)
    candidate = _minimal_xpp(extra=bytes([3]) * 8)
    install1 = tmp_path / "infamous1.psarc_s"
    install2 = tmp_path / "infamous2.psarc_s"
    install1.write_bytes(
        build_archive(["/one/shared.xpp", "/two/shared.xpp"], [retail1, retail2])
    )
    install2.write_bytes(build_archive(["/other.xpp"], [_minimal_xpp()]))
    replacements = tmp_path / "replacements"
    (replacements / "install1/one").mkdir(parents=True)
    (replacements / "install1/one/shared.xpp").write_bytes(candidate)

    build_profile(install1, install2, replacements, tmp_path / "profile")

    rebuilt = tmp_path / "profile/infamous1.psarc_s"
    assert extract_entry(rebuilt, "/one/shared.xpp") == candidate
    assert extract_entry(rebuilt, "/two/shared.xpp") == retail2


def test_profile_build_rejects_inexact_within_slot_duplicate_basename(tmp_path: Path):
    package = _minimal_xpp()
    install1 = tmp_path / "infamous1.psarc_s"
    install2 = tmp_path / "infamous2.psarc_s"
    install1.write_bytes(
        build_archive(["/one/shared.xpp", "/two/shared.xpp"], [package, package])
    )
    install2.write_bytes(build_archive(["/other.xpp"], [package]))
    replacements = tmp_path / "replacements"
    (replacements / "install1/alias").mkdir(parents=True)
    (replacements / "install1/alias/shared.xpp").write_bytes(package)

    with pytest.raises(ValueError, match="2 owners inside install1"):
        build_profile(install1, install2, replacements, tmp_path / "profile")


def test_archive_audit_rejects_changed_entry_name_digest(tmp_path: Path):
    source = tmp_path / "source.psarc_s"
    rebuilt = tmp_path / "rebuilt.psarc_s"
    source.write_bytes(build_archive(["/A1.xpp"], [_minimal_xpp()]))
    changed = bytearray(source.read_bytes())
    changed[32 + 30] ^= 0x01  # first byte of the named entry's MD5 descriptor
    rebuilt.write_bytes(changed)

    with pytest.raises(ValueError, match="name digest changed"):
        audit_archive(source, rebuilt, {})
