"""Strict structural and retail-comparison validation on synthetic XPPs."""

from __future__ import annotations

import struct

import pytest

from infamous_xpp_textures.heap import chain_size
from infamous_xpp_textures.pack import pack_chains
from infamous_xpp_textures.validation import ValidationError, compare_xpp, validate_xpp
from infamous_xpp_textures.xpp import parse_xpp

from test_synthetic import _minimal_xpp


def _retail_and_2x() -> tuple[bytes, bytes]:
    retail_chain = bytes(chain_size(0x86, 4, 4, 3))
    retail = _minimal_xpp(width=4, height=4, mips=3, extra=retail_chain)
    candidate_chain = bytes(chain_size(0x86, 8, 8, 4))
    candidate = pack_chains(retail, {0: (8, 8, 4, candidate_chain)})
    return retail, candidate


def test_validate_rejects_descriptor_that_old_reader_would_skip():
    data = bytearray(_minimal_xpp())
    descriptor = parse_xpp(data).data_offset
    data[descriptor + 0x46] = 0xFF

    with pytest.raises(ValidationError, match="descriptor 0 is invalid.*unknown-format"):
        validate_xpp(bytes(data))


def test_validate_rejects_explicit_embedded_mip_disagreement():
    data = bytearray(_minimal_xpp())
    descriptor = parse_xpp(data).data_offset
    data[descriptor + 0x45] = 2

    with pytest.raises(ValidationError, match="mip count disagrees"):
        validate_xpp(bytes(data))


def test_compare_reports_promotions_and_path_scoped_budget():
    retail, candidate = _retail_and_2x()
    report = compare_xpp(
        retail,
        candidate,
        known_pass_extra=32,
        known_fail_extra=64,
    )

    assert report["promoted_records"] == 1
    assert report["chain_delta_bytes"] == 32
    assert report["budget"]["status"] == "within-observed-startup-pass-range"
    assert report["budget"]["scene_coverage_required"] is True


def test_compare_rejects_mip_growth_that_does_not_match_scale():
    retail, candidate = _retail_and_2x()
    changed = bytearray(candidate)
    descriptor = parse_xpp(changed).data_offset
    struct.pack_into(">I", changed, descriptor + 0x2C, 3)
    changed[descriptor + 0x45] = 3

    with pytest.raises(ValidationError, match="has 3 mips at 2x; expected 4"):
        compare_xpp(retail, bytes(changed))
