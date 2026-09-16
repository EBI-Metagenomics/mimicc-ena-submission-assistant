"""Unit tests for server/schema_service.py.

Exercises what the browser's schema library calls through ``py()``: naming a
schema for the library, the ENA XML/XSD import pipeline, and the compile that
backs schema selection for the DataHarmonizer grids — plus the committed
indexes the page reads instead of listing directories. Skipped automatically
when the heavy ``linkml`` dependency is unavailable.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

pytest.importorskip("linkml")

import schema_service  # noqa: E402

_REPO = pathlib.Path(__file__).resolve().parent.parent


def _bundled(name: str) -> str:
    return (_REPO / "schemas" / f"{name}.yaml").read_text(encoding="utf-8")


def test_describe_schema_names_a_library_entry():
    yaml_text = "name: my_schema\nid: https://example.org/my_schema\ntitle: Mine\nclasses: {}\n"
    assert schema_service.describe_schema(yaml_text, "My Schema!") == {
        "id": "my-schema",  # slugified
        "name": "my_schema",
        "title": "Mine",
        "description": None,
    }
    # A blank name falls back to the schema's own.
    assert schema_service.describe_schema(yaml_text)["id"] == "my_schema"


@pytest.mark.parametrize("yaml_text", ["not: [a, mapping, root: oops", "- just\n- a list\n"])
def test_describe_schema_rejects_invalid_yaml(yaml_text):
    with pytest.raises(ValueError):
        schema_service.describe_schema(yaml_text, "bad")


def _load_index_builder():
    spec = importlib.util.spec_from_file_location("build_static_indexes", _REPO / "scripts" / "build_static_indexes.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_indexes_match_the_files():
    """The page lists schemas/ and assets/ena_schema/ from these; a static host
    cannot list a directory. Run scripts/build_static_indexes.py to fix."""
    builder = _load_index_builder()
    assert builder.SCHEMAS_INDEX.read_text() == builder.render(builder.schemas_index())
    assert builder.ENA_SOURCES_INDEX.read_text() == builder.render(schema_service.list_ena_sources())


def test_every_bundled_schema_id_is_its_slug():
    # Regression: upper-case-named bundled schemas (SRA_study.yaml, ERC*.yaml)
    # were once listed under an id that lookup then missed, so they could not
    # be selected in any grid dropdown.
    ids = {entry["id"] for entry in json.loads((_REPO / "schemas" / "index.json").read_text())}
    assert {"mimicc_sample", "mimicc_experiment", "sra_study"} <= ids
    assert all(entry_id == schema_service._slugify(entry_id) for entry_id in ids)


def test_list_ena_sources_includes_top_level_and_checklists_subdir():
    sources = schema_service.list_ena_sources()
    checklist_ids = {c["id"] for c in sources["checklists"]}
    xsd_ids = {x["id"] for x in sources["xsd"]}
    assert "ERC000025.xml" in checklist_ids  # top-level vendored checklist
    assert "SRA.sample.xsd" in xsd_ids


def test_import_build_merges_checklist_and_xsd_sources():
    yaml_text = schema_service.import_build(source_ids=["ERC000025.xml", "SRA.sample.xsd"])
    assert "ERC000025" in yaml_text or "GSC MIxS" in yaml_text
    # Both inputs contributed slots to the merged schema.
    import yaml as _yaml

    schema = _yaml.safe_load(yaml_text)
    assert schema.get("slots")
    assert all("source" not in (slot.get("annotations") or {}) for slot in schema["slots"].values())


def test_import_build_namespaces_under_this_repo():
    """The base URI is ours, not whatever linkml-lib's default happens to be.

    linkml-lib is pinned by tag, so leaving base_uri unset would make the
    namespace of what we build depend on which tag is installed — and disagree
    with the schemas committed in schemas/.
    """
    import yaml as _yaml

    schema = _yaml.safe_load(schema_service.import_build(source_ids=["ERC000025.xml"]))
    assert schema["id"].startswith(schema_service.SCHEMA_BASE_URI)
    assert "ena-submission-dataharmonizer" not in schema["id"]


def test_committed_schemas_share_the_import_namespace():
    """schemas/ and anything rebuilt from it must not drift apart."""
    import yaml as _yaml

    schemas_dir = pathlib.Path(__file__).resolve().parent.parent / "schemas"
    for path in sorted(schemas_dir.glob("*.yaml")):
        schema = _yaml.safe_load(path.read_text())
        assert schema["id"].startswith(schema_service.SCHEMA_BASE_URI), path.name


def test_import_build_raises_without_any_inputs():
    with pytest.raises(ValueError):
        schema_service.import_build()


def test_import_build_can_include_a_saved_schema_file(tmp_path):
    saved = tmp_path / "seed.yaml"
    saved.write_text("name: seed\nid: https://example.org/seed\nclasses: {}\nslots: {}\n")
    assert schema_service.import_build(source_ids=["ERC000025.xml"], upload_paths=[saved])


def test_compile_for_grid_targets_the_roles_fixed_folder():
    result = schema_service.compile_for_grid("sample", _bundled("mimicc_sample"))

    assert result["template"] == "mimicc/MIMICC_Sample"
    assert (result["role"], result["folder"]) == ("sample", "mimicc")
    assert result["schema_json"]["name"]
    assert "MIMICC_Sample" in result["schema_json"]["classes"]
    json.dumps(result)  # crosses to the page as JSON


def test_compile_for_grid_adapts_renderable_class_to_fixed_role_template():
    yaml_text = """
name: merged_schema
id: https://example.org/merged_schema
classes:
  dh_interface:
    description: A DataHarmonizer interface
  RenderableTable:
    is_a: dh_interface
    slots:
      - alias
slots:
  alias:
    title: Alias
"""
    result = schema_service.compile_for_grid("sample", yaml_text)

    assert result["template"] == "mimicc/MIMICC_Sample"
    assert "MIMICC_Sample" in result["schema_json"]["classes"]
    assert "RenderableTable" not in result["schema_json"]["classes"]
    assert result["diagnostics"]


def test_compile_for_grid_rejects_schema_without_renderable_classes():
    with pytest.raises(ValueError, match="no renderable classes"):
        schema_service.compile_for_grid("sample", "name: classless\nid: https://example.org/classless\nclasses: {}\n")


def test_compile_for_grid_rejects_unknown_role():
    with pytest.raises(ValueError, match="Unknown role"):
        schema_service.compile_for_grid("bogus", "name: x\n")


@pytest.mark.parametrize(("role", "folder"), [("experiment", "mimicc_experiment"), ("study", "study")])
def test_compile_for_grid_uses_each_roles_own_folder(role, folder):
    source = "mimicc_experiment" if role == "experiment" else "SRA_study"
    result = schema_service.compile_for_grid(role, _bundled(source))
    assert result["folder"] == folder
    assert result["template"] == f"{folder}/{schema_service.ROLE_TEMPLATE_CLASSES[role]}"
