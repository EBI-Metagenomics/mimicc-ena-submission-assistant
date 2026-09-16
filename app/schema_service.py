"""LinkML schema work for the schema library and the DataHarmonizer grids.

Runs in the browser (``py()``); pure — no filesystem state of its own. The
library itself lives in the browser's IndexedDB (``static/schema.js``):

* ``describe_schema`` validates YAML and names it, for saving to the library;
* ``import_build`` converts + merges ENA XML/XSD/YAML sources into a new schema;
* ``compile_for_grid`` compiles a schema for one grid's fixed template folder.
  The page puts the result in Cache Storage, where the service worker
  (``static/sw.js``) serves it in place of the bundle's ``schema.json`` —
  DataHarmonizer fetches that file at runtime, so no rebuild is needed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import _bootstrap
import yaml
from linkml_lib import dataharmonizer_compile, pipeline
from linkml_lib import io as linkml_io

# Namespace stamped into the id: and from_schema: of schemas built here, and
# the namespace the schemas committed in schemas/ carry. Passed explicitly
# rather than left to linkml_lib.io.DEFAULT_BASE_URI: these schemas belong to
# this repo, and the default would otherwise make what we build depend on which
# linkml-lib tag happens to be pinned.
SCHEMA_BASE_URI = "https://github.com/EBI-Metagenomics/mimicc-ena-submission-assistant"

# Fixed DataHarmonizer template folders the grids are pointed at
# (static/dataharmonizer.js: initDhFrames). Selecting a schema for a role
# replaces that folder's schema.json rather than registering a new folder.
ROLE_FOLDERS = {"sample": "mimicc", "experiment": "mimicc_experiment", "study": "study"}
# Each value MUST equal the class name the role's fixed template folder was
# built with (Dockerfile dh-builder stage) — sample/experiment use their
# mimicc_*.yaml tree_root class, and study uses SRA_study.yaml's ("SRA_study").
# Selecting a schema renames its class to this name; if it disagrees with the
# built template, the registry/menu desync and DataHarmonizer throws
# getColumnCoordinates on load (was "Study", which no built folder matched).
ROLE_TEMPLATE_CLASSES = {"sample": "MIMICC_Sample", "experiment": "MIMICC_Experiment", "study": "SRA_study"}

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return slug or "schema"


def describe_schema(yaml_text: str, name: str = "") -> dict[str, Any]:
    """Validate LinkML YAML and describe it as a library entry: ``id`` is
    slugified from ``name`` (or the schema's own ``name`` when blank)."""
    try:
        schema = linkml_io.load_yaml_text(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc
    schema_id = _slugify(name or schema.get("name") or "schema")
    return {
        "id": schema_id,
        "name": schema.get("name") or schema_id,
        "title": schema.get("title") or schema.get("name") or schema_id,
        "description": schema.get("description"),
    }


def list_ena_sources() -> dict[str, list[dict[str, str]]]:
    """Bundled ENA checklist XML / SRA+project XSD files importable as schema
    sources. A static host cannot list a directory, so the page reads this from
    ``assets/ena_schema/index.json`` (``scripts/build_static_indexes.py``)."""
    base = _bootstrap.xsd_dir()
    checklists: list[dict[str, str]] = []
    for sub in (base, base / "checklists"):
        if not sub.exists():
            continue
        for p in sorted(sub.glob("*.xml")):
            checklists.append({"id": str(p.relative_to(base)), "filename": p.name, "kind": "checklist"})
    xsds = [{"id": p.name, "filename": p.name, "kind": "xsd"} for p in sorted(base.glob("*.xsd"))]
    return {"checklists": checklists, "xsd": xsds}


def _resolve_source_path(source_id: str) -> Path:
    base = _bootstrap.xsd_dir().resolve()
    candidate = (base / source_id).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError(f"Invalid source id: {source_id}")
    if not candidate.exists():
        raise ValueError(f"Source not found: {source_id}")
    return candidate


def import_build(
    *,
    source_ids: list[str] | None = None,
    upload_paths: list[Path] | None = None,
    name: str | None = None,
    title: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> str:
    """Convert+merge bundled ENA XML/XSD sources and files (saved library
    schemas, uploads) into one LinkML schema (generic "import" for building a
    new schema). Priority = the order given, sources first (earlier inputs win
    on conflicts). Returns the merged schema as LinkML YAML text (not saved).
    """
    paths: list[Path] = []
    for sid in source_ids or []:
        paths.append(_resolve_source_path(sid))
    for p in upload_paths or []:
        paths.append(Path(p))
    if not paths:
        raise ValueError("No input sources given")

    schema = pipeline.build(paths, base_uri=SCHEMA_BASE_URI, name=name, title=title, include=include, exclude=exclude)
    _normalise_slot_source_annotations(schema)
    return linkml_io.dump_yaml(schema)


def _normalise_slot_source_annotations(schema: dict) -> None:
    """Move legacy generated slot source annotations to top-level provenance."""
    for slot in (schema.get("slots") or {}).values():
        annotations = slot.get("annotations")
        if not isinstance(annotations, dict) or "source" not in annotations:
            continue
        slot.setdefault("source", annotations.pop("source"))
        if not annotations:
            slot.pop("annotations", None)


def _template_class_name(schema: dict[str, Any]) -> str:
    """Return the class name DataHarmonizer should render for this schema."""
    classes = schema.get("classes") or {}
    if not isinstance(classes, dict):
        raise ValueError("Schema has no renderable classes")

    schema_name = schema.get("name")
    if isinstance(schema_name, str) and schema_name in classes and schema_name not in {"Container", "dh_interface"}:
        return schema_name

    dh_classes = [
        class_name
        for class_name, class_def in classes.items()
        if class_name not in {"Container", "dh_interface"}
        and isinstance(class_def, dict)
        and class_def.get("is_a") == "dh_interface"
    ]
    if dh_classes:
        return dh_classes[0]

    renderable_classes = [class_name for class_name in classes if class_name not in {"Container", "dh_interface"}]
    if renderable_classes:
        return renderable_classes[0]

    raise ValueError("Schema has no renderable classes")


def compile_for_grid(role: str, yaml_text: str) -> dict[str, Any]:
    """Compile a schema for one grid's fixed DataHarmonizer template folder.

    Returns the compiled ``schema_json`` plus where it belongs (``folder``) and
    the ``<folder>/<class>`` ``template`` the iframe's ``?template=`` points at.
    The renderable class is renamed to the role's fixed class, which is what
    the bundle's registry and menu were built with.
    """
    folder = ROLE_FOLDERS.get(role)
    if folder is None:
        raise ValueError(f"Unknown role: {role}. Expected one of {sorted(ROLE_FOLDERS)}")
    try:
        schema = linkml_io.load_yaml_text(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc
    source_template_name = _template_class_name(schema)
    compiled = dataharmonizer_compile.compile_schema_json(schema)
    template_name = ROLE_TEMPLATE_CLASSES[role]
    diagnostics = _adapt_compiled_schema_for_role(compiled, source_template_name, template_name)
    return {
        "role": role,
        "folder": folder,
        "template_name": template_name,
        "template": f"{folder}/{template_name}",
        "schema_json": compiled,
        "diagnostics": diagnostics,
    }


def _adapt_compiled_schema_for_role(
    compiled: dict[str, Any],
    source_template_name: str,
    target_template_name: str,
) -> list[dict[str, str]]:
    """Make a runtime schema fit the class name baked into the role's DH menu."""
    if source_template_name == target_template_name:
        return []

    classes = compiled.get("classes")
    if not isinstance(classes, dict) or source_template_name not in classes:
        raise ValueError(f'Compiled schema does not contain renderable class "{source_template_name}"')
    if target_template_name in classes:
        raise ValueError(
            f'Cannot adapt schema: both "{source_template_name}" and fixed class "{target_template_name}" exist'
        )

    class_def = classes.pop(source_template_name)
    if isinstance(class_def, dict):
        class_def["name"] = target_template_name
    classes[target_template_name] = class_def
    if compiled.get("name") == source_template_name:
        compiled["name"] = target_template_name

    for container_class in classes.values():
        attributes = container_class.get("attributes") if isinstance(container_class, dict) else None
        if not isinstance(attributes, dict):
            continue
        for attr in attributes.values():
            if isinstance(attr, dict) and attr.get("range") == source_template_name:
                attr["range"] = target_template_name

    return [
        {
            "level": "info",
            "message": (
                f'Renamed renderable class "{source_template_name}" to fixed DataHarmonizer '
                f'class "{target_template_name}" for this grid.'
            ),
        }
    ]
