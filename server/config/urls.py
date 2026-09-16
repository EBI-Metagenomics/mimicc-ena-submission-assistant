from __future__ import annotations

import views_core
import views_schemas
from django.urls import path, re_path

urlpatterns = [
    path("", views_core.index),
    path("api/health", views_core.health),
    # Schema library
    path("api/schemas", views_schemas.schemas_collection),
    path("api/schemas/ena-sources", views_schemas.schemas_ena_sources),
    path("api/schemas/import", views_schemas.schemas_import),
    path("api/schemas/import-file", views_schemas.schemas_import_file),
    path("api/schemas/select", views_schemas.schemas_select),
    path("api/schemas/<str:schema_id>/export", views_schemas.schemas_export),
    path("api/schemas/<str:schema_id>", views_schemas.schemas_detail),
    # Static / DataHarmonizer bundle
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
