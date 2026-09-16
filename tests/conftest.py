"""Shared pytest fixtures.

Single-user, local-only: no database, no accounts, no server-side state. Server
tests drive Django views in-process via a thin async-compatible wrapper around
``django.test.Client``. UI tests drive a real WSGI server with Playwright; the
browser holds credentials itself and talks to ENA from its Python worker, which
``test_ui.py`` stubs.
"""

from __future__ import annotations

import json as _json
import os
import sys
import threading
import time
from pathlib import Path
from wsgiref.simple_server import WSGIServer, make_server

import httpx
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "server"))
sys.path.insert(0, str(_REPO))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.test import Client as _DjangoClient  # noqa: E402


class AsyncClient:
    """Async-shaped wrapper around ``django.test.Client``.

    Lets test bodies keep ``await client.get(...)``-style calls (no real async
    I/O happens — Django's test client runs the WSGI stack synchronously).
    """

    def __init__(self, *, headers: dict | None = None, secure: bool = False):
        self._client = _DjangoClient()
        self._headers = headers or {}
        self._secure = secure

    def _merged_headers(self, headers: dict | None) -> dict:
        return {**self._headers, **(headers or {})}

    @staticmethod
    def _with_text(response):
        # django.http.HttpResponse has no .text — add it (httpx-style) so
        # callers don't need response.content.decode().
        response.text = response.content.decode()
        return response

    async def get(self, path: str, **kwargs):
        headers = kwargs.pop("headers", None)
        return self._with_text(
            self._client.get(path, secure=self._secure, headers=self._merged_headers(headers), **kwargs)
        )

    async def delete(self, path: str, **kwargs):
        headers = kwargs.pop("headers", None)
        return self._with_text(
            self._client.delete(path, secure=self._secure, headers=self._merged_headers(headers), **kwargs)
        )

    async def post(self, path: str, *, json=None, data=None, files=None, **kwargs):
        headers = kwargs.pop("headers", None)
        return self._with_text(
            self._client.post(
                path,
                **self._body_kwargs(json, data, files),
                secure=self._secure,
                headers=self._merged_headers(headers),
            )
        )

    async def put(self, path: str, *, json=None, data=None, **kwargs):
        headers = kwargs.pop("headers", None)
        return self._with_text(
            self._client.put(
                path, **self._body_kwargs(json, data, None), secure=self._secure, headers=self._merged_headers(headers)
            )
        )

    @staticmethod
    def _body_kwargs(json, data, files) -> dict:
        if json is not None:
            return {"data": _json.dumps(json), "content_type": "application/json"}
        from django.core.files.uploadedfile import SimpleUploadedFile

        merged = dict(data or {})
        for key, (filename, content, content_type) in (files or {}).items():
            merged[key] = SimpleUploadedFile(filename, content, content_type=content_type)
        return {"data": merged or None}


@pytest.fixture
def client():
    return AsyncClient()


# ---------------------------------------------------------------------------
# Mock helpers (shared)
# ---------------------------------------------------------------------------

# A webin-cli log as the local helper would stream it back to the browser; the
# browser turns the final log into a result row (read_assign.upload_result).
MOCK_READS_LOG = (
    "INFO: validating manifest\n"
    "INFO: The submission has been completed successfully.\n"
    "INFO: experiment ERX9000001 run ERR9000001\n"
)


# ---------------------------------------------------------------------------
# Live server for Playwright UI tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def live_server_url():
    import config.wsgi as wsgi_module

    server = make_server("127.0.0.1", 9911, wsgi_module.application, server_class=WSGIServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = "http://127.0.0.1:9911"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/api/health", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("live server did not start")

    yield url
    server.shutdown()
    thread.join(timeout=5)
