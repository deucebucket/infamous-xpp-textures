import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import infamous_xpp_textures.cli as cli
import infamous_xpp_textures.page_correlation as correlation
from infamous_xpp_textures.cli import main
from infamous_xpp_textures.page_correlation import PageCorrelationError


def _sha(value: int) -> str:
    return f"{value:064x}"


def _block(
    payload: int,
    descriptor: int,
    *,
    count: int = 3,
    attribute: int = 0,
):
    return SimpleNamespace(
        payload_sha256=_sha(payload),
        payload_bytes=count * 12,
        descriptor_sha256=_sha(descriptor),
        stride=12,
        range_first=0,
        range_count=count,
        attributes=(
            {
                "attribute": attribute,
                "type": 2,
                "components": 3,
                "array_stride": 12,
                "frequency": 0,
                "modulo": 0,
            },
        ),
    )


def _event(
    number: int,
    *,
    capture_key: int,
    index: int,
    blocks: tuple,
    surface: int,
):
    return SimpleNamespace(
        number=number,
        capture_key=_sha(capture_key),
        index_sha256=_sha(index),
        index_bytes=6,
        index_count=3,
        blocks=blocks,
        target_texture_slots=(0,),
        target_texture_sha256s=(_sha(1000 + surface),),
        vertex_program_sha256=_sha(2000 + surface),
    )


def _wire(monkeypatch, tmp_path: Path, *, second_format=None, first_limit=1):
    page1 = tmp_path / "page1"
    page2 = tmp_path / "page2"
    page1.mkdir(parents=True)
    page2.mkdir()
    (page1 / "capture.complete").write_bytes(b"page-one\n")
    (page2 / "capture.complete").write_bytes(b"page-two\n")
    exclusion = tmp_path / "page1.tsv"
    exclusion.write_bytes(b"capture_key\nsynthetic\n")

    first = {
        1: _event(1, capture_key=1, index=11, blocks=(_block(101, 201),), surface=1),
        2: _event(2, capture_key=2, index=12, blocks=(_block(102, 202),), surface=2),
        3: _event(
            3,
            capture_key=3,
            index=13,
            blocks=(_block(103, 203), _block(104, 204, attribute=1)),
            surface=3,
        ),
        4: _event(4, capture_key=4, index=14, blocks=(_block(105, 205),), surface=4),
        5: _event(5, capture_key=5, index=15, blocks=(_block(106, 206),), surface=5),
    }
    second = {
        1: _event(1, capture_key=6, index=11, blocks=(_block(101, 201),), surface=1),
        2: _event(2, capture_key=7, index=22, blocks=(_block(102, 202),), surface=2),
        3: _event(
            3,
            capture_key=8,
            index=23,
            blocks=(_block(103, 203), _block(304, 204, attribute=1)),
            surface=3,
        ),
        4: _event(
            4,
            capture_key=9,
            index=24,
            blocks=(_block(305, 205, count=4),),
            surface=4,
        ),
        5: _event(5, capture_key=10, index=25, blocks=(_block(306, 206),), surface=6),
    }
    allowlist_sha = _sha(9000)
    exclusion_sha = hashlib.sha256(exclusion.read_bytes()).hexdigest()

    def fake_load(bundle, _allowlist, capture_key_exclusion=None):
        if bundle == page1:
            return (
                {
                    "format": "if1-texture-bound-topology-v3",
                    "captured_draws": len(first),
                    "capture_limit_reached": first_limit,
                },
                first,
                allowlist_sha,
            )
        assert bundle == page2
        assert capture_key_exclusion == exclusion
        return (
            {
                "format": second_format or "if1-texture-bound-topology-v4",
                "captured_draws": len(second),
                "capture_limit_reached": 0,
                "exclusion_manifest_sha256": exclusion_sha,
            },
            second,
            allowlist_sha,
        )

    monkeypatch.setattr(correlation, "_load_bundle", fake_load)
    monkeypatch.setattr(
        correlation,
        "_parse_capture_key_exclusion",
        lambda path: ({event.capture_key for event in first.values()}, exclusion_sha),
    )
    return page1, page2, exclusion, first, second


def test_classifies_strong_weak_and_novel_page_events(monkeypatch, tmp_path: Path):
    page1, page2, exclusion, _first, _second = _wire(monkeypatch, tmp_path)
    args = ((page1, page2), tmp_path / "allowlist", (None, exclusion))
    first = correlation.correlate_paged_draw_families(*args)
    second = correlation.correlate_paged_draw_families(*args)
    assert first == second
    assert first["page_count"] == 2
    assert first["captured_draws"] == 10
    assert first["pair_comparisons"] == 25
    assert first["strong_family_count"] == 3
    assert first["strong_pair_count"] == 3
    assert first["exact_geometry_pairs"] == 1
    assert first["exact_vertex_stream_pairs"] == 1
    assert first["stable_layout_partial_stream_pairs"] == 1
    assert first["weak_surface_program_pairs"] == 1
    latest = first["page_summaries"][1]
    assert latest["strong_persistent_family_candidate"] == 3
    assert latest["weak_surface_program_only"] == 1
    assert latest["novel_observed_surface_program_signature"] == 1
    assert first["gates"]["same_source_component"] is False
    assert first["gates"]["new_geometry"] is False
    rendered = json.dumps(first, sort_keys=True)
    assert "payload_sha256" not in rendered
    assert "index_sha256" not in rendered
    assert "target_texture_sha256s" not in rendered
    assert "vertex_program_sha256" not in rendered


def test_reports_ambiguous_strong_family_without_forcing_pair(monkeypatch, tmp_path):
    page1, page2, exclusion, first, second = _wire(monkeypatch, tmp_path)
    first[2] = _event(
        2,
        capture_key=2,
        index=11,
        blocks=(_block(101, 201),),
        surface=9,
    )
    second.clear()
    second[1] = _event(
        1,
        capture_key=6,
        index=11,
        blocks=(_block(101, 201),),
        surface=10,
    )
    result = correlation.correlate_paged_draw_families(
        (page1, page2), tmp_path / "allowlist", (None, exclusion)
    )
    assert result["strong_family_count"] == 1
    family = result["strong_families"][0]
    assert len(family["members"]) == 3
    assert family["one_event_per_page"] is False
    assert family["ambiguous_within_page"] is True
    assert family["component_ownership_proved"] is False


def test_rejects_invalid_page_chain(monkeypatch, tmp_path):
    page1, page2, exclusion, _first, _second = _wire(monkeypatch, tmp_path)
    with pytest.raises(PageCorrelationError, match="distinct"):
        correlation.correlate_paged_draw_families(
            (page1, page1), tmp_path / "allowlist", (None, exclusion)
        )

    monkeypatch.setattr(
        correlation,
        "_parse_capture_key_exclusion",
        lambda _path: ({_sha(999)}, hashlib.sha256(exclusion.read_bytes()).hexdigest()),
    )
    with pytest.raises(PageCorrelationError, match="exact cumulative"):
        correlation.correlate_paged_draw_families(
            (page1, page2), tmp_path / "allowlist", (None, exclusion)
        )


def test_rejects_wrong_format_and_nonfinal_short_page(monkeypatch, tmp_path):
    page1, page2, exclusion, _first, _second = _wire(
        monkeypatch, tmp_path, second_format="if1-texture-bound-topology-v3"
    )
    with pytest.raises(PageCorrelationError, match="followed only by v4"):
        correlation.correlate_paged_draw_families(
            (page1, page2), tmp_path / "allowlist", (None, exclusion)
        )

    other = tmp_path / "other"
    page1, page2, exclusion, _first, _second = _wire(monkeypatch, other, first_limit=0)
    with pytest.raises(PageCorrelationError, match="non-final page"):
        correlation.correlate_paged_draw_families(
            (page1, page2), other / "allowlist", (None, exclusion)
        )


def test_cli_writes_new_bounded_report_and_refuses_overwrite(
    monkeypatch, tmp_path, capsys
):
    page1 = tmp_path / "page1"
    page2 = tmp_path / "page2"
    page1.mkdir()
    page2.mkdir()
    exclusion = tmp_path / "page1.tsv"
    exclusion.write_bytes(b"capture_key\n")
    output = tmp_path / "families.json"

    def fake_census(bundles, allowlist, exclusions):
        assert bundles == (page1, page2)
        assert allowlist == tmp_path / "allowlist"
        assert exclusions == (None, exclusion)
        return {"kind": "test-page-family-census", "strong_family_count": 1}

    monkeypatch.setattr(cli, "correlate_paged_draw_families", fake_census)
    args = [
        "runtime-page-family-census",
        "--page-bundle",
        str(page1),
        "--page-capture-key-exclusion",
        "-",
        "--page-bundle",
        str(page2),
        "--page-capture-key-exclusion",
        str(exclusion),
        "--texture-allowlist",
        str(tmp_path / "allowlist"),
        "--json-out",
        str(output),
    ]
    assert main(args) == 0
    assert '"strong_family_count": 1' in output.read_text(encoding="utf-8")
    assert main(args) == 1
    assert "refusing to overwrite" in capsys.readouterr().err


def test_cli_rejects_report_over_byte_bound(monkeypatch, tmp_path, capsys):
    page1 = tmp_path / "page1"
    page2 = tmp_path / "page2"
    page1.mkdir()
    page2.mkdir()
    output = tmp_path / "families.json"
    monkeypatch.setattr(
        cli, "correlate_paged_draw_families", lambda *_args: {"kind": "too-large"}
    )
    monkeypatch.setattr(cli, "MAX_PAGE_CORRELATION_REPORT_BYTES", 1)
    assert (
        main(
            [
                "runtime-page-family-census",
                "--page-bundle",
                str(page1),
                "--page-capture-key-exclusion",
                "-",
                "--page-bundle",
                str(page2),
                "--page-capture-key-exclusion",
                str(tmp_path / "page1.tsv"),
                "--texture-allowlist",
                str(tmp_path / "allowlist"),
                "--json-out",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()
    assert "exceeds its byte bound" in capsys.readouterr().err
