"""Shared pytest fixtures.

The app is static files (``scripts/build_dist.py``) plus Python that runs in the
browser. Python tests import that Python directly; UI tests build ``dist/`` once
per session and drive it with Playwright over ``scripts/serve_dist.py``. The
browser holds credentials itself and talks to ENA from its Python worker, which
``test_ui.py`` stubs.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))


def load_script(name: str):
    """Import ``scripts/<name>.py`` (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
# The built site, served, for Playwright UI tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def dist_dir(tmp_path_factory):
    # No --dh: this environment has no DataHarmonizer bundle (compose tests do).
    return load_script("build_dist").build(tmp_path_factory.mktemp("dist"), dh=Path("/nonexistent"))


@pytest.fixture(scope="session")
def live_server_url(dist_dir):
    server = load_script("serve_dist").make_server(dist_dir, port=9911)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:9911"
    server.shutdown()
    thread.join(timeout=5)
