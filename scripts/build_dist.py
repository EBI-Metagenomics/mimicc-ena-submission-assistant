"""Build ``dist/``: the whole app as static files, for any static host.

Nothing in it needs an application server. The URL layout is the one the page
already uses::

    index.html, sw.js          the shell, and the service worker at the root (its scope)
    config.json                deployment configuration (replaces /api/health)
    static/                    app scripts, vendored ena-browser, py/ (worker + app.zip)
    dh/, templates/            the built DataHarmonizer bundle; DataHarmonizer fetches
                               template schemas from /templates/, not /dh/templates/
    schemas/, assets/ena_schema/   files the browser's Python reads

``config.json`` takes ``HELPER_PORT`` and ``DHTB_URL`` from the environment. The
Docker image builds with them set to ``${HELPER_PORT}``/``${DHTB_URL}`` and
substitutes the real values when the container starts.

Run: ``.venv/bin/python scripts/build_dist.py [--out dist] [--dh server/static/dh]``
(``task build:dist``).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))
sys.path.insert(0, str(REPO / "scripts"))

import build_py_bundle  # noqa: E402
import ena_service  # noqa: E402

STATIC = REPO / "server" / "static"
# The schema each fixed grid folder is built from (Dockerfile dh-builder stage).
# DataHarmonizer's build leaves only schema.json there, but Prepare reads the
# LinkML schema.yaml — so the build puts the source beside it. A grid schema the
# user selects replaces both (static/sw.js).
FIXED_TEMPLATE_SOURCES = {
    "mimicc": "mimicc_sample.yaml",
    "mimicc_experiment": "mimicc_experiment.yaml",
    "study": "SRA_study.yaml",
}
ENTITIES = ("studies", "samples", "runs", "experiments", "analyses", "files")


def config(out: Path) -> dict:
    return {
        "helper_port": os.environ.get("HELPER_PORT", "9100"),
        "dhtb_url": os.environ.get("DHTB_URL", "http://localhost:8765"),
        "dh_available": (out / "dh" / "index.html").is_file(),
        "ena_browser_available": (out / "static" / "vendor" / "ena-browser" / "ena-browser.iife.js").is_file(),
        "default_sample_filter": ena_service.DEFAULT_SAMPLE_FILTER,
        # The toolkit builds the MODIFY XML, so it is the authority on what is editable.
        "editable_columns": {entity: ena_service.editable_columns(entity) for entity in ENTITIES},
    }


def build(out: Path, dh: Path) -> Path:
    build_py_bundle.build()
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(
        STATIC,
        out / "static",
        ignore=shutil.ignore_patterns("dh", "index.html", "sw.js", "__pycache__", ".DS_Store"),
    )
    shutil.copy2(STATIC / "index.html", out / "index.html")
    shutil.copy2(STATIC / "sw.js", out / "sw.js")
    shutil.copytree(REPO / "schemas", out / "schemas")
    shutil.copytree(REPO / "assets" / "ena_schema", out / "assets" / "ena_schema")
    if (dh / "index.html").is_file():
        shutil.copytree(dh, out / "dh")
        for folder, source in FIXED_TEMPLATE_SOURCES.items():
            schema_yaml = out / "dh" / "templates" / folder / "schema.yaml"
            if schema_yaml.parent.is_dir() and not schema_yaml.exists():
                shutil.copy2(REPO / "schemas" / source, schema_yaml)
        if (out / "dh" / "templates").is_dir():
            shutil.copytree(out / "dh" / "templates", out / "templates")
    (out / "config.json").write_text(json.dumps(config(out), indent=2) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=REPO / "dist")
    parser.add_argument("--dh", type=Path, default=STATIC / "dh", help="built DataHarmonizer bundle (optional)")
    args = parser.parse_args()
    print(build(args.out, args.dh))
