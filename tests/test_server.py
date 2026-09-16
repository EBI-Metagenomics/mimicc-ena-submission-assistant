"""In-process tests of what the Django server still serves (no Docker, no network).

Everything that talks to ENA runs in the browser now; its Python is tested
directly (``test_ena_service.py``, ``test_read_assign.py``).
"""

from __future__ import annotations

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


def test_serves_the_files_the_browser_python_reads():
    from django.test import Client

    for path in ("/schemas/mimicc_sample.yaml", "/assets/ena_schema/SRA.common.xsd"):
        assert Client().get(path).status_code == 200, path


async def test_ena_endpoints_are_gone(client):
    for path in ("/api/records/studies", "/api/study/submit", "/api/reads/suggest"):
        assert (await client.get(path)).status_code == 404, path
