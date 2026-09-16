"""Run the app's Python in the browser (Pyodide, inside a Web Worker).

Two pieces:

* ``FetchTransport`` — an ``httpx`` transport backed by the browser's
  synchronous ``XMLHttpRequest``. ``WebinClient`` and ``ENAClient`` are
  synchronous ``httpx.Client``s, so an async ``fetch`` cannot sit under them;
  sync XHR can, and in a worker it is allowed, supports binary bodies, and does
  not block the page. ENA's APIs are CORS-enabled (STATIC_BROWSER_PLAN.md §0.1).
  ``install()`` swaps it in for ``httpx.HTTPTransport`` globally, so neither
  client needs editing.

* ``call()`` — the one entry point the page uses: ``"module.function"`` plus
  JSON kwargs, answered with a JSON envelope. The functions are the existing
  ``ena_service`` / ``read_assign`` / ``schema_service`` ones, unchanged.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
from pathlib import Path
from typing import Any

import httpx

# The browser sets these itself and refuses (or ignores) them from script.
_UNSETTABLE_REQUEST_HEADERS = frozenset({"host", "content-length", "accept-encoding", "connection", "user-agent"})
# The browser has already decoded the body, so passing these on would make
# httpx decode it a second time (or check the wrong length).
_STALE_RESPONSE_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})


class FetchTransport(httpx.BaseTransport):
    """``httpx`` over synchronous ``XMLHttpRequest``. Worker-only."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        # Stands in for HTTPTransport(verify=..., retries=..., limits=...): the
        # browser owns TLS, pooling and retries, so every option is ignored.
        pass

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        from js import Uint8Array, XMLHttpRequest  # type: ignore[import-not-found]
        from pyodide.ffi import JsException, to_js  # type: ignore[import-not-found]

        xhr = XMLHttpRequest.new()
        xhr.open(request.method, str(request.url), False)
        xhr.responseType = "arraybuffer"
        timeout = (request.extensions.get("timeout") or {}).get("read")
        if timeout:
            xhr.timeout = int(timeout * 1000)
        for name, value in request.headers.items():
            if name.lower() not in _UNSETTABLE_REQUEST_HEADERS:
                xhr.setRequestHeader(name, value)
        body = request.read()
        try:
            xhr.send(to_js(body) if body else None)
        except JsException as exc:
            raise httpx.ConnectError(f"{request.method} {request.url}: {exc}", request=request) from None
        if xhr.status == 0:
            # CORS refusal, DNS failure and offline all look like this to script.
            raise httpx.ConnectError(f"{request.method} {request.url}: network error", request=request)
        return httpx.Response(
            xhr.status,
            headers=parse_headers(xhr.getAllResponseHeaders()),
            content=Uint8Array.new(xhr.response).to_bytes() if xhr.response else b"",
            request=request,
        )


def parse_headers(raw: str) -> list[tuple[str, str]]:
    """``getAllResponseHeaders()`` text as header pairs, minus the stale ones."""
    pairs = []
    for line in raw.strip().splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip().lower() not in _STALE_RESPONSE_HEADERS:
            pairs.append((name.strip(), value.strip()))
    return pairs


def install() -> None:
    """Make every ``httpx.Client`` built from now on use ``FetchTransport``.

    ``httpx.Client`` constructs its default transport through the name bound in
    ``httpx._client``; ``ENAClient`` uses ``httpx.HTTPTransport(retries=...)``.
    Patching both covers every client in the stack.
    """
    httpx.HTTPTransport = FetchTransport  # type: ignore[misc,assignment]
    httpx._client.HTTPTransport = FetchTransport  # type: ignore[misc,assignment]


_CALLABLE_MODULES = frozenset({"ena_service", "read_assign", "schema_service"})


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    if isinstance(value, Path | bytes):
        return value.decode() if isinstance(value, bytes) else str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def call(target: str, kwargs_json: str) -> str:
    """Call ``module.function(**kwargs)``; answer ``{"result": ...}`` or ``{"error": ...}``.

    A ``creds`` kwarg arrives as ``{"username", "password"}`` and is turned into
    the ``Credentials`` the service functions take.
    """
    try:
        module_name, _, function_name = target.partition(".")
        if module_name not in _CALLABLE_MODULES or not function_name or function_name.startswith("_"):
            raise ValueError(f"Not callable from the page: {target!r}")
        function = getattr(importlib.import_module(module_name), function_name)
        kwargs = json.loads(kwargs_json or "{}")
        if "creds" in kwargs:
            import ena_service

            kwargs["creds"] = ena_service.Credentials(**kwargs["creds"])
        return json.dumps({"result": function(**kwargs)}, default=_jsonable)
    except Exception as exc:  # noqa: BLE001 — every failure goes back to the page as a message
        return json.dumps({"error": str(exc) or type(exc).__name__, "type": type(exc).__name__})
