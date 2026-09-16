"""Build ``server/static/py/app.zip``: the Python the browser worker runs.

Holds what micropip cannot fetch from PyPI — the pinned EBI git packages,
copied from the synced ``.venv`` so they match ``uv.lock`` exactly — plus this
app's own framework-free modules, the browser bridge and the ``pendulum``
shim. Everything else (``linkml``, ``typer``, ``pydantic-settings``, and what
Pyodide ships) is installed by ``static/py/worker.js``.

Run: ``.venv/bin/python scripts/build_py_bundle.py`` (``task build:py``).
"""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "server" / "static" / "py" / "app.zip"

GIT_PACKAGES = ("ena_api", "ena_api_handler", "ena_submission_toolkit", "linkml_lib")
APP_MODULES = [
    REPO / "server" / f"{name}.py" for name in ("_bootstrap", "ena_service", "read_assign", "schema_service")
]
BROWSER_MODULES = [REPO / "server" / "pyodide" / "ena_bridge.py", REPO / "server" / "pyodide" / "shims" / "pendulum.py"]


def _package_dir(name: str) -> Path:
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.submodule_search_locations:
        sys.exit(f"{name} is not installed — run `uv sync` first.")
    return Path(next(iter(spec.submodule_search_locations)))


def build() -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in GIT_PACKAGES:
            root = _package_dir(name)
            for path in sorted(root.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    bundle.write(path, f"{name}/{path.relative_to(root)}")
        for path in APP_MODULES + BROWSER_MODULES:
            bundle.write(path, path.name)
    return OUT


if __name__ == "__main__":
    print(build())
