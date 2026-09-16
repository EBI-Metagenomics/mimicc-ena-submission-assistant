"""The static build (``scripts/build_dist.py``) and the dev server that serves it."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
from conftest import load_script

build_dist = load_script("build_dist")


def test_dist_holds_everything_the_page_fetches(dist_dir):
    for path in (
        "index.html",
        "sw.js",  # at the root: a service worker's scope is its own directory
        "config.json",
        "static/core.js",
        "static/py/worker.js",
        "static/py/versions.js",
        "static/py/app.zip",
        "static/vendor/ena-browser/ena-browser.iife.js",
        "schemas/index.json",
        "schemas/mimicc_sample.yaml",
        "assets/ena_schema/index.json",
        "assets/ena_schema/SRA.common.xsd",
    ):
        assert (dist_dir / path).is_file(), path
    assert not (dist_dir / "static" / "index.html").exists()
    assert not (dist_dir / "static" / "dh").exists()


def test_config_replaces_the_health_endpoint(dist_dir):
    config = json.loads((dist_dir / "config.json").read_text())
    assert config["helper_port"] == "9100"
    assert config["dhtb_url"] == "http://localhost:8765"
    assert config["dh_available"] is False  # no bundle in this environment
    assert config["ena_browser_available"] is True
    assert "title" in config["editable_columns"]["samples"]
    assert config["default_sample_filter"]


def test_config_takes_deployment_values_from_the_environment(tmp_path, monkeypatch):
    # The Docker image builds with placeholders and substitutes them at start.
    monkeypatch.setenv("HELPER_PORT", "${HELPER_PORT}")
    monkeypatch.setenv("DHTB_URL", "${DHTB_URL}")
    out = build_dist.build(tmp_path / "dist", dh=tmp_path / "no-bundle")
    config = json.loads((out / "config.json").read_text())
    assert (config["helper_port"], config["dhtb_url"]) == ("${HELPER_PORT}", "${DHTB_URL}")


def test_a_built_bundle_is_served_at_dh_and_its_templates_at_the_root(tmp_path):
    """DataHarmonizer fetches template schemas from /templates/, not /dh/templates/."""
    dh = tmp_path / "dh"
    (dh / "templates" / "mimicc").mkdir(parents=True)
    (dh / "index.html").write_text("<!doctype html>")
    (dh / "dh-template-registry.json").write_text('{"mimicc": "MIMICC_Sample"}')
    (dh / "templates" / "mimicc" / "schema.json").write_text("{}")

    out = build_dist.build(tmp_path / "dist", dh=dh)

    assert (out / "dh" / "dh-template-registry.json").is_file()
    assert (out / "templates" / "mimicc" / "schema.json").is_file()
    assert json.loads((out / "config.json").read_text())["dh_available"] is True


def test_fixed_grid_folders_get_the_linkml_schema_prepare_reads(tmp_path):
    """DataHarmonizer's build leaves only schema.json in a template folder; study
    Prepare reads schema.yaml, so without this it failed until a schema was
    selected (compose tests hid it: selection wrote the file into a shared volume)."""
    dh = tmp_path / "dh"
    for folder in ("mimicc", "study"):
        (dh / "templates" / folder).mkdir(parents=True)
        (dh / "templates" / folder / "schema.json").write_text("{}")
    (dh / "templates" / "mimicc" / "schema.yaml").write_text("name: already_there\n")
    (dh / "index.html").write_text("<!doctype html>")

    out = build_dist.build(tmp_path / "dist", dh=dh)

    study = (build_dist.REPO / "schemas" / "SRA_study.yaml").read_text()
    assert (out / "templates" / "study" / "schema.yaml").read_text() == study
    assert (out / "dh" / "templates" / "study" / "schema.yaml").read_text() == study
    assert (out / "templates" / "mimicc" / "schema.yaml").read_text() == "name: already_there\n"
    assert not (out / "templates" / "mimicc_experiment").exists()
    assert not (dh / "templates" / "study" / "schema.yaml").exists()  # the source bundle is untouched


@pytest.fixture
def served(dist_dir):
    server = load_script("serve_dist").make_server(dist_dir, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_dev_server_never_lets_a_script_be_cached(served):
    """A stale cached workspace.js next to a fresh records.js once broke the page
    with "applySavedGridLayout is not defined"."""
    with urllib.request.urlopen(f"{served}/static/workspace.js") as response:
        assert response.headers["Cache-Control"] == "no-cache"
        assert "javascript" in response.headers["Content-Type"]


def test_there_is_no_api(served):
    for path in ("/api/health", "/api/records/studies", "/api/schemas"):
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"{served}{path}")
        assert err.value.code == 404, path
