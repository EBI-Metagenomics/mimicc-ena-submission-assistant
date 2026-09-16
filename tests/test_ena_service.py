"""``ena_service`` — the functions the page calls through ``py()`` — tested as
plain Python, with the toolkit and ENA stubbed."""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager

import ena_service


def test_modify_uses_this_apps_submission_alias_by_default(monkeypatch):
    seen = {}

    class _Records:
        @staticmethod
        def modify_records(creds, entity, records, *, test, submission_alias):
            seen.update(alias=submission_alias)
            return {"success": True}

        preview_modify_records = modify_records

    creds = ena_service.Credentials("Webin-1", "pw")
    monkeypatch.setattr(ena_service, "_records", lambda: _Records)
    ena_service.modify_records(creds, "samples", [], test=True)
    assert seen["alias"] == ena_service.MODIFY_ALIAS == "mimicc-assistant-modify"
    ena_service.preview_modify_records(creds, "samples", [], test=True)
    assert seen["alias"] == ena_service.MODIFY_ALIAS


def test_suggest_samples_matches_groups_against_the_account(monkeypatch):
    samples = [{"accession": "ERS1", "alias": "MIMICC_A_1"}]
    seen = {}
    monkeypatch.setattr(
        ena_service, "list_records", lambda creds, entity, **k: seen.update(entity=entity, **k) or samples
    )
    groups = [{"group": "MIMICC_A_1", "files": ["MIMICC_A_1_R1.fastq.gz"], "paired": False}]

    out = ena_service.suggest_samples(ena_service.Credentials("u", "p"), groups, test=False)

    assert (seen["entity"], seen["test"]) == ("samples", False)
    assert out["samples"] == samples
    assert out["groups"][0]["suggested_sample"] == "ERS1"


def test_submit_studies_adds_stage_logs_when_toolkit_returns_failure(monkeypatch):
    toolkit = types.ModuleType("ena_submission_toolkit")

    class FakeCommon:
        @staticmethod
        def validate_hold_until(_hold_until):
            return None

        @staticmethod
        def find_duplicates_by_alias_title(*_args, **_kwargs):
            return {}

        @staticmethod
        def classify_duplicates(records, *_args, **_kwargs):
            return [], records, []

    toolkit.common = FakeCommon()
    study_xml = b'<WEBIN><PROJECT alias="study-a"><TITLE>Study A</TITLE></PROJECT></WEBIN>'
    toolkit.submit_study = types.SimpleNamespace(
        build_manifest=lambda *_args, **_kwargs: study_xml,
        validate_manifest=lambda *_args, **_kwargs: (
            True,
            ["XML is well-formed", "OK: PROJECT 'study-a' has required elements"],
        ),
        submit_manifest=lambda *_args, **_kwargs: (False, [], ["Study title is not unique"]),
    )
    monkeypatch.setitem(sys.modules, "ena_submission_toolkit", toolkit)

    @contextmanager
    def fake_webin_client(*_args, **_kwargs):
        yield object()

    monkeypatch.setattr(ena_service, "webin_client", fake_webin_client)
    monkeypatch.setattr(ena_service._bootstrap, "xsd_dir", lambda: "/tmp/fake-xsd")

    result = ena_service.submit_studies(
        ena_service.Credentials(username="Webin-test", password="secret"),
        [{"alias": "study-a", "TITLE": "Study A"}],
        test=True,
    )

    assert result["success"] is False
    assert result["error"] == "ENA rejected the study submission; see receipt messages in the log."
    assert "INFO: Building ENA study XML manifest: action=ADD, records=1" in result["logs"]
    assert f"INFO: Built ENA study XML manifest: bytes={len(study_xml)}" in result["logs"]
    assert (
        "INFO: Built native ENA project identity: "
        "PROJECT[1]: alias='study-a', TITLE='Study A', NAME_present=False, DESCRIPTION_present=False"
    ) in result["logs"]
    assert "INFO: Validating study XML manifest against ENA.project.xsd" in result["logs"]
    assert "INFO:   XML is well-formed" in result["logs"]
    assert "INFO: XSD validation result: valid=True" in result["logs"]
    assert "INFO: Pre-validation passed; submitting study XML to ENA (TEST)" in result["logs"]
    assert "INFO: ENA receipt parsed: success=False, accession_records=0, message_count=1" in result["logs"]
    assert "ERROR: Receipt: Study title is not unique" in result["logs"]
    assert "ERROR: ENA receipt reported failure for the study submission" in result["logs"]


def test_submit_studies_surfaces_warning_only_receipt_rejection(monkeypatch):
    toolkit = types.ModuleType("ena_submission_toolkit")

    class FakeCommon:
        @staticmethod
        def validate_hold_until(_hold_until):
            return None

        @staticmethod
        def find_duplicates_by_alias_title(*_args, **_kwargs):
            return {}

        @staticmethod
        def classify_duplicates(records, *_args, **_kwargs):
            return [], records, []

    toolkit.common = FakeCommon()
    study_xml = b'<WEBIN><PROJECT alias="study-a"><TITLE>MIMICC</TITLE></PROJECT></WEBIN>'
    toolkit.submit_study = types.SimpleNamespace(
        build_manifest=lambda *_args, **_kwargs: study_xml,
        validate_manifest=lambda *_args, **_kwargs: (True, []),
        submit_manifest=lambda *_args, **_kwargs: (
            False,
            [],
            ["WARNING: Study title 'MIMICC' is not sufficiently unique"],
        ),
    )
    monkeypatch.setitem(sys.modules, "ena_submission_toolkit", toolkit)

    @contextmanager
    def fake_webin_client(*_args, **_kwargs):
        yield object()

    monkeypatch.setattr(ena_service, "webin_client", fake_webin_client)
    monkeypatch.setattr(ena_service._bootstrap, "xsd_dir", lambda: "/tmp/fake-xsd")

    result = ena_service.submit_studies(
        ena_service.Credentials(username="Webin-test", password="secret"),
        [{"alias": "study-a", "TITLE": "MIMICC"}],
        test=True,
    )

    assert result["success"] is False
    assert "WARNING: Receipt: Study title 'MIMICC' is not sufficiently unique" in result["logs"]
    assert result["error"] == "WARNING: Study title 'MIMICC' is not sufficiently unique"


def test_submit_studies_reports_local_xsd_validation_failure(monkeypatch):
    toolkit = types.ModuleType("ena_submission_toolkit")

    class FakeCommon:
        @staticmethod
        def validate_hold_until(_hold_until):
            return None

    toolkit.common = FakeCommon()
    toolkit.submit_study = types.SimpleNamespace(
        build_manifest=lambda *_args, **_kwargs: b"<WEBIN></WEBIN>",
        validate_manifest=lambda *_args, **_kwargs: (False, ["ERROR: PROJECT 'study-a' missing TITLE"]),
    )
    monkeypatch.setitem(sys.modules, "ena_submission_toolkit", toolkit)

    @contextmanager
    def fake_webin_client(*_args, **_kwargs):
        yield object()

    monkeypatch.setattr(ena_service, "webin_client", fake_webin_client)
    monkeypatch.setattr(ena_service._bootstrap, "xsd_dir", lambda: "/tmp/fake-xsd")

    result = ena_service.submit_studies(
        ena_service.Credentials(username="Webin-test", password="secret"),
        [{"alias": "study-a", "STUDY_TITLE": "Legacy title that should not be used"}],
        test=True,
    )

    assert result["success"] is False
    assert result["error"] == "Study XML failed local XSD validation; it was not submitted to ENA."
    assert "WARNING:   record 1: missing recommended/required field(s): TITLE" in result["logs"]
    assert "ERROR:   ERROR: PROJECT 'study-a' missing TITLE" not in result["logs"]
    assert "ERROR: PROJECT 'study-a' missing TITLE" in result["logs"]
    assert "ERROR: Study XML failed local XSD validation; it was not submitted to ENA." in result["logs"]
