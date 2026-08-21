"""Synthetic PSARC rebuild tests. No retail bytes required."""

from __future__ import annotations

import hashlib
from pathlib import Path

from infamous_xpp_textures.psarc import build_archive, extract_entry, read_toc, rebuild_archive


def test_rebuild_archive_replaces_only_selected_entry(tmp_path: Path):
    names = ["/A1.xpp", "/notes/readme.txt"]
    source = tmp_path / "retail.psarc_s"
    source.write_bytes(build_archive(names, [b"retail-xpp", b"untouched"]))
    output = tmp_path / "modded.psarc_s"
    result = rebuild_archive(
        source,
        output,
        {"A1.xpp": b"hd-xpp", "belongs-to-other-archive.xpp": b"ignored"},
    )

    assert result["replaced"] == 1
    assert result["ignored"] == 1
    assert extract_entry(output, "/A1.xpp") == b"hd-xpp"
    assert extract_entry(output, "/notes/readme.txt") == b"untouched"
    _info, entries, rebuilt_names, _blocks = read_toc(output)
    assert rebuilt_names == names
    assert entries[1]["md5"] == hashlib.md5(b"/A1.XPP").hexdigest()


def test_rebuild_archive_can_require_every_replacement(tmp_path: Path):
    source = tmp_path / "retail.psarc_s"
    source.write_bytes(build_archive(["/A1.xpp"], [b"retail-xpp"]))

    try:
        rebuild_archive(
            source,
            tmp_path / "modded.psarc_s",
            {"missing.xpp": b"hd-xpp"},
            require_all=True,
        )
    except ValueError as exc:
        assert "absent from the PSARC" in str(exc)
    else:
        raise AssertionError("missing explicit replacement was accepted")
