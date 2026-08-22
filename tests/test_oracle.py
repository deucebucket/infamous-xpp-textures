"""Synthetic cross-build oracle tests. No retail bytes required."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infamous_xpp_textures.cli import main
from infamous_xpp_textures.oracle import build_profile_oracle
from infamous_xpp_textures.psarc import build_archive


def _archive(path: Path, names: list[str], payloads: list[bytes], **kwargs) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_archive(names, payloads, **kwargs))
    return path


def _pair(root: Path, entries: tuple[tuple[list[str], list[bytes]], ...], **kwargs):
    return (
        _archive(root / "install1.psarc_s", *entries[0], **kwargs),
        _archive(root / "install2.psarc_s", *entries[1], **kwargs),
    )


def test_oracle_counts_identical_changed_and_added_packages(tmp_path: Path):
    left = _pair(
        tmp_path / "left",
        (
            (["/textures/A.xpp"], [b"same"]),
            (["/characters/B.xpps"], [b"old"]),
        ),
    )
    right = _pair(
        tmp_path / "right",
        (
            (["/textures/A.xpp", "/extra/C.xpp"], [b"same", b"new"]),
            (["/characters/B.xpps"], [b"NEW"]),
        ),
    )

    report = build_profile_oracle(*left, *right, left_label="disc", right_label="psn")
    replay = build_profile_oracle(*left, *right, left_label="disc", right_label="psn")

    assert replay == report
    assert report["kind"] == "xpp-cross-build-oracle"
    assert report["left_label"] == "disc"
    assert report["right_label"] == "psn"
    assert report["pair"]["shared_full_names"] == 2
    assert report["pair"]["right_only_full_names"] == 1
    assert report["pair"]["byte_identical_shared_packages"] == 1
    assert report["pair"]["changed_shared_packages"] == 1
    assert report["pair"]["same_size_changed_shared_packages"] == 1
    assert report["verdict"] == "shared-package-bytes-diverge"
    assert report["direct_replacement_transfer_authorized"] is False
    assert "path" not in str(report).casefold()
    assert "a.xpp" not in str(report).casefold()


def test_oracle_keeps_renamed_same_basename_out_of_full_name_identity(tmp_path: Path):
    left = _pair(
        tmp_path / "left",
        ((["/one/shared.xpp"], [b"same"]), (["/left.xpp"], [b"left"])),
    )
    right = _pair(
        tmp_path / "right",
        ((["/two/shared.xpp"], [b"same"]), (["/right.xpp"], [b"right"])),
    )

    report = build_profile_oracle(*left, *right)

    assert report["archives"][0]["shared_full_names"] == 0
    assert report["archives"][0]["shared_basenames"] == 1
    assert report["pair"]["byte_compared_shared_packages"] == 0


def test_oracle_reports_cross_archive_duplicate_basename_as_ambiguous(tmp_path: Path):
    left = _pair(
        tmp_path / "left",
        ((["/one/shared.xpp"], [b"one"]), (["/two/SHARED.XPP"], [b"two"])),
    )
    right = _pair(
        tmp_path / "right",
        ((["/one/shared.xpp"], [b"one"]), (["/other.xpp"], [b"other"])),
    )

    report = build_profile_oracle(*left, *right)

    assert report["routing_unambiguous"] is False
    assert report["left_duplicate_package_basenames"] == 1
    assert report["verdict"] == "routing-ambiguous"


def test_oracle_reports_supported_but_different_archive_contracts(tmp_path: Path):
    entries = ((["/A.xpp"], [b"same"]), (["/B.xpp"], [b"same"]))
    left = _pair(tmp_path / "left", entries, block_size=65536)
    right = _pair(tmp_path / "right", entries, block_size=32768)

    report = build_profile_oracle(*left, *right)

    assert report["archive_contracts_match"] is False
    assert report["verdict"] == "archive-contracts-differ"


def test_oracle_catalog_only_withholds_byte_claims(tmp_path: Path):
    entries = ((["/A.xpp"], [b"same"]), (["/B.xpp"], [b"same"]))
    left = _pair(tmp_path / "left", entries)
    right = _pair(tmp_path / "right", entries)

    report = build_profile_oracle(*left, *right, compare_bytes=False)

    assert report["comparison_mode"] == "catalog-only"
    assert report["pair"]["byte_compared_shared_packages"] == 0
    assert report["pair"]["byte_identical_shared_packages"] is None
    assert report["verdict"] == "catalog-only"


def test_oracle_rejects_pathlike_labels(tmp_path: Path):
    entries = ((["/A.xpp"], [b"same"]), (["/B.xpp"], [b"same"]))
    left = _pair(tmp_path / "left", entries)
    right = _pair(tmp_path / "right", entries)

    with pytest.raises(ValueError, match="cannot contain paths"):
        build_profile_oracle(*left, *right, left_label="/private/source")


def test_profile_oracle_cli_prints_aggregate_json(tmp_path: Path, capsys):
    entries = ((["/A.xpp"], [b"same"]), (["/B.xpp"], [b"same"]))
    left = _pair(tmp_path / "left", entries)
    right = _pair(tmp_path / "right", entries)

    result = main(
        [
            "profile-oracle",
            "--left-install1",
            str(left[0]),
            "--left-install2",
            str(left[1]),
            "--right-install1",
            str(right[0]),
            "--right-install2",
            str(right[1]),
            "--left-label",
            "disc",
            "--right-label",
            "psn",
        ]
    )

    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "shared-package-bytes-identical"
    assert report["direct_replacement_transfer_authorized"] is False
    assert "a.xpp" not in str(report).casefold()
