"""In-process tests of what the Django server still serves (no Docker, no network).

Everything that talks to ENA runs in the browser now; its Python is tested
directly (``test_ena_service.py``, ``test_read_assign.py``).
"""

from __future__ import annotations

import views_core
from django.test import Client, RequestFactory

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "library_presets" not in body
    assert "samples" in body["editable_columns"]
    assert body["ena_browser_available"] is True


def test_serves_the_files_the_browser_reads():
    paths = (
        "/schemas/mimicc_sample.yaml",
        "/schemas/index.json",
        "/assets/ena_schema/SRA.common.xsd",
        "/assets/ena_schema/index.json",
        "/static/py/versions.js",
    )
    for path in paths:
        assert Client().get(path).status_code == 200, path


def test_service_worker_is_served_from_the_root():
    """Its scope is its own directory: from /static/ it could not see /templates/ or /dh/."""
    response = Client().get("/sw.js")
    assert response.status_code == 200
    assert "dh-templates" in b"".join(response.streaming_content).decode()


def test_schema_endpoints_are_gone():
    for path in ("/api/schemas", "/api/schemas/select", "/api/schemas/ena-sources"):
        assert Client().get(path).status_code == 404, path


def test_mutable_dataharmonizer_assets_are_not_cached(tmp_path, monkeypatch):
    dh_dir = tmp_path / "dh"
    (dh_dir / "templates" / "mimicc").mkdir(parents=True)
    (dh_dir / "templates" / "mimicc" / "schema.json").write_text('{"name": "MIMICC_Sample"}', encoding="utf-8")
    monkeypatch.setattr(views_core, "DH_DIR", dh_dir)
    monkeypatch.setattr(views_core, "DH_TEMPLATES_DIR", dh_dir / "templates")
    request = RequestFactory().get("/templates/mimicc/schema.json")

    response = views_core.static_serve_view(request, "mimicc/schema.json", str(dh_dir / "templates"))

    assert response["Cache-Control"] == "no-store, max-age=0"


def test_app_scripts_are_served_uncached():
    """A stale cached workspace.js next to a fresh records.js used to blow up
    with "applySavedGridLayout is not defined" — app static must not be cached."""
    request = RequestFactory().get("/static/workspace.js")

    response = views_core.static_serve_view(request, "workspace.js", str(views_core.STATIC_DIR))

    assert response["Cache-Control"] == "no-store, max-age=0"


async def test_ena_endpoints_are_gone(client):
    for path in ("/api/records/studies", "/api/study/submit", "/api/reads/suggest"):
        assert (await client.get(path)).status_code == 404, path
