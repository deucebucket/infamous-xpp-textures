"""The web UI's local server refuses requests not made by the page it served."""

from infamous_xpp_textures.ui import _WEB_HTML, _request_is_trusted

HOST = "127.0.0.1:43210"
TOKEN = "t0ken"


def ok(**extra):
    headers = {"Host": HOST, "Origin": f"http://{HOST}", "X-If1-Token": TOKEN}
    headers.update(extra)
    return {k: v for k, v in headers.items() if v is not None}


def test_page_request_is_trusted():
    assert _request_is_trusted(ok(), TOKEN, HOST)
    assert _request_is_trusted(ok(Origin=None), TOKEN, HOST)


def test_missing_or_wrong_token_refused():
    assert not _request_is_trusted(ok(**{"X-If1-Token": None}), TOKEN, HOST)
    assert not _request_is_trusted(ok(**{"X-If1-Token": "guess"}), TOKEN, HOST)


def test_foreign_origin_refused():
    assert not _request_is_trusted(ok(Origin="https://evil.example"), TOKEN, HOST)


def test_dns_rebinding_host_refused():
    assert not _request_is_trusted(ok(Host="evil.example:43210"), TOKEN, HOST)


def test_page_carries_token_placeholder():
    assert "__IF1_TOKEN__" in _WEB_HTML
