from __future__ import annotations

import views_core
from django.urls import path, re_path

urlpatterns = [
    path("", views_core.index),
    path("api/health", views_core.health),
    # Static / DataHarmonizer bundle. The service worker is served from the root
    # so its scope covers /templates/ and /dh/ (static/sw.js).
    path("sw.js", views_core.static_serve_view, {"path": "sw.js", "document_root": str(views_core.STATIC_DIR)}),
    re_path(r"^static/(?P<path>.*)$", views_core.static_serve_view, {"document_root": str(views_core.STATIC_DIR)}),
    re_path(r"^schemas/(?P<path>.*)$", views_core.static_serve_view, {"document_root": str(views_core.SCHEMAS_DIR)}),
    re_path(
        r"^assets/ena_schema/(?P<path>.*)$",
        views_core.static_serve_view,
        {"document_root": str(views_core.ENA_SCHEMA_DIR)},
    ),
    path("dh/", views_core.serve_dh),
    re_path(r"^dh/(?P<path>.*)$", views_core.serve_dh),
    re_path(
        r"^templates/(?P<path>.*)$",
        views_core.static_serve_view,
        {"document_root": str(views_core.DH_TEMPLATES_DIR)},
    ),
]
