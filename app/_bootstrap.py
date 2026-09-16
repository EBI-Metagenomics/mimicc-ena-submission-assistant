"""Locate the schema/XSD assets this app needs at runtime.

``ena_api``, ``linkml_lib``, and ``ena_submission_toolkit``
(``common``, ``submit_sample``, ``submit_study``, ``prepare_dh_output``) are
pinned pip dependencies (see ``pyproject.toml``) — plain ``import``s work
without any ``sys.path`` setup.

What's left is locating the non-Python assets that ship alongside them: the
MIMICC LinkML schemas (``schemas/``) and the ENA/SRA XSDs/checklists
(``assets/ena_schema/``) — both committed directly in this repo. Override
with ``ENA_DH_SCHEMA`` / ``ENA_DH_XSD`` if needed. In the browser (Pyodide) the
same paths resolve under ``/``, where the page fetches the files a call reads.

The resolved paths are exposed via ``schema_path()`` / ``xsd_dir()``, which
raise only when actually needed. The schema library is not here: it lives in
the browser (IndexedDB, ``static/schema.js``).
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _first_existing(*candidates: Path | None) -> Path | None:
    for c in candidates:
        if c and c.exists():
            return c
    return None


def _env_path(name: str) -> Path | None:
    val = os.environ.get(name)
    return Path(val) if val else None


def schema_path() -> Path:
    """The schema behind the Samples tab's DataHarmonizer grid and the Prepare step's
    field-name mapping (``ena_service.prepare_samples``) — ``mimicc_sample.yaml``, not
    the combined ``mimicc_sample_experiment.yaml`` it was filtered from."""
    found = _first_existing(
        _env_path("ENA_DH_SCHEMA"),
        _REPO_ROOT / "schemas" / "mimicc_sample.yaml",
    )
    if found is None:
        raise RuntimeError("Could not locate mimicc_sample.yaml. Set ENA_DH_SCHEMA to override.")
    return found


def xsd_dir() -> Path:
    found = _first_existing(
        _env_path("ENA_DH_XSD"),
        _REPO_ROOT / "assets" / "ena_schema",
    )
    if found is None:
        raise RuntimeError("Could not locate the ENA XSD directory. Set ENA_DH_XSD to override.")
    return found
