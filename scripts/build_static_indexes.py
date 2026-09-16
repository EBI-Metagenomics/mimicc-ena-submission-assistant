"""Write the directory indexes a static host cannot generate.

* ``schemas/index.json`` — the bundled LinkML schemas the browser seeds its
  schema library from, with the name/title/description the dropdowns show (so
  listing them needs no Python in the browser);
* ``assets/ena_schema/index.json`` — the ENA checklists/XSDs importable as
  schema sources (``schema_service.list_ena_sources``).

Committed; ``tests/test_schema_service.py`` fails when they drift from the files.
Run: ``.venv/bin/python scripts/build_static_indexes.py`` (``task build:indexes``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "app"))

import schema_service  # noqa: E402

SCHEMAS_INDEX = REPO / "schemas" / "index.json"
ENA_SOURCES_INDEX = REPO / "assets" / "ena_schema" / "index.json"


def schemas_index() -> list[dict]:
    return [
        {**schema_service.describe_schema(path.read_text(encoding="utf-8"), path.stem), "file": path.name}
        for path in sorted((REPO / "schemas").glob("*.yaml"))
    ]


def render(data) -> str:
    return json.dumps(data, indent=2) + "\n"


def build() -> None:
    SCHEMAS_INDEX.write_text(render(schemas_index()), encoding="utf-8")
    ENA_SOURCES_INDEX.write_text(render(schema_service.list_ena_sources()), encoding="utf-8")


if __name__ == "__main__":
    build()
    print(SCHEMAS_INDEX, ENA_SOURCES_INDEX)
