"""The browser bridge (server/pyodide/), exercised under CPython.

The transport runs against a fake ``js`` module standing in for the worker's
``XMLHttpRequest``; the real thing is covered by the Pyodide Playwright test in
``test_ui.py``.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import sys
import types
from pathlib import Path

import httpx
import pendulum
import pytest

_PYODIDE_DIR = Path(__file__).resolve().parent.parent / "server" / "pyodide"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ena_bridge = _load("ena_bridge", _PYODIDE_DIR / "ena_bridge.py")
# Loaded under another name: the real pendulum must stay importable here.
pendulum_shim = _load("pendulum_shim", _PYODIDE_DIR / "shims" / "pendulum.py")


# --- call() -----------------------------------------------------------------


def test_call_runs_a_service_function_and_wraps_the_result():
    out = json.loads(ena_bridge.call("read_assign.group_files", json.dumps({"names": ["a_R1.fq.gz", "a_R2.fq.gz"]})))
    assert out["result"][0]["group"] == "a"


def test_call_turns_creds_into_credentials(monkeypatch):
    import ena_service

    seen = {}
    monkeypatch.setattr(ena_service, "validate_credentials", lambda creds, test: seen.update(creds=creds, test=test))
    kwargs = {"creds": {"username": "Webin-1", "password": "pw"}, "test": True}
    assert json.loads(ena_bridge.call("ena_service.validate_credentials", json.dumps(kwargs))) == {"result": None}
    assert seen["creds"] == ena_service.Credentials(username="Webin-1", password="pw")


@pytest.mark.parametrize("target", ["os.system", "ena_service._records", "ena_service", "ena_service.nope"])
def test_call_refuses_anything_but_public_service_functions(target):
    assert "error" in json.loads(ena_bridge.call(target, "{}"))


def test_call_reports_exceptions_as_messages():
    out = json.loads(ena_bridge.call("read_assign.group_files", json.dumps({"wrong": 1})))
    assert out["type"] == "TypeError" and "wrong" in out["error"]


# --- transport --------------------------------------------------------------


class _FakeXHR:
    """Records what the transport did; answers with ``response``."""

    last: _FakeXHR
    status = 200
    raw_headers = "Content-Type: application/json\r\nContent-Encoding: gzip\r\nContent-Length: 999\r\n"
    body = b'{"ok": true}'

    @classmethod
    def new(cls):
        cls.last = cls()
        return cls.last

    def __init__(self):
        self.headers = {}
        self.timeout = 0

    def open(self, method, url, is_async):
        self.method, self.url, self.is_async = method, url, is_async

    def setRequestHeader(self, name, value):  # noqa: N802 — the XHR API
        self.headers[name.lower()] = value

    def send(self, body):
        self.sent = body
        self.response = self.body

    def getAllResponseHeaders(self):  # noqa: N802
        return self.raw_headers


@pytest.fixture
def fake_browser(monkeypatch):
    js = types.ModuleType("js")
    js.XMLHttpRequest = _FakeXHR
    js.Uint8Array = types.SimpleNamespace(new=lambda buf: types.SimpleNamespace(to_bytes=lambda: bytes(buf)))
    ffi = types.ModuleType("pyodide.ffi")
    ffi.JsException = RuntimeError
    ffi.to_js = lambda body: body
    monkeypatch.setitem(sys.modules, "js", js)
    monkeypatch.setitem(sys.modules, "pyodide", types.ModuleType("pyodide"))
    monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi)
    monkeypatch.setattr(_FakeXHR, "status", 200)
    return _FakeXHR


def test_transport_sends_a_synchronous_request_with_auth(fake_browser):
    with httpx.Client(transport=ena_bridge.FetchTransport(retries=3), auth=("Webin-1", "pw"), timeout=5) as client:
        response = client.post("https://wwwdev.ebi.ac.uk/ena/submit/webin-v2/submit", content=b"<xml/>")

    xhr = fake_browser.last
    assert (xhr.method, xhr.is_async, xhr.sent, xhr.timeout) == ("POST", False, b"<xml/>", 5000)
    assert xhr.headers["authorization"].startswith("Basic ")
    assert "content-length" not in xhr.headers and "host" not in xhr.headers
    # The browser already decoded the body: no second gunzip, no length check.
    assert response.json() == {"ok": True}
    assert "content-encoding" not in response.headers


def test_transport_turns_a_blocked_request_into_connect_error(fake_browser):
    fake_browser.status = 0
    with httpx.Client(transport=ena_bridge.FetchTransport()) as client, pytest.raises(httpx.ConnectError):
        client.get("https://www.ebi.ac.uk/ena/portal/api/search")


def test_install_makes_default_clients_use_the_transport(monkeypatch, fake_browser):
    monkeypatch.setattr(httpx, "HTTPTransport", httpx.HTTPTransport)
    monkeypatch.setattr(httpx._client, "HTTPTransport", httpx._client.HTTPTransport)
    ena_bridge.install()
    with httpx.Client() as client:
        assert client.get("https://example.org/").status_code == 200
    assert isinstance(httpx.HTTPTransport(retries=2), ena_bridge.FetchTransport)


# --- pendulum shim ----------------------------------------------------------


def test_shim_formats_submission_aliases_like_pendulum():
    fmt = "YYYYMMDD-HHmmss"
    moment = datetime.datetime(2026, 3, 4, 5, 6, 7)
    assert pendulum_shim.DateTime(2026, 3, 4, 5, 6, 7).format(fmt) == pendulum.instance(moment).format(fmt)


@pytest.mark.parametrize("text", ["2026-02-28", "2028-02-29"])
def test_shim_parses_and_adds_years_like_pendulum(text):
    shim, real = pendulum_shim.parse(text, exact=True), pendulum.parse(text, exact=True)
    assert shim.isoformat() == real.isoformat()
    assert shim.add(years=2).isoformat() == real.add(years=2).isoformat()
    assert pendulum_shim.today().date() == pendulum.today().date()


@pytest.mark.parametrize("text", ["nope", "2026-13-01", ""])
def test_shim_rejects_bad_dates_with_parser_error(text):
    with pytest.raises(pendulum_shim.parsing.ParserError):
        pendulum_shim.parse(text, exact=True)
