"""Unit tests for the read-to-sample assignment subsystem."""

from __future__ import annotations

import pytest
import read_assign


def _touch(d, *names):
    for n in names:
        (d / n).write_text("x")


def test_scan_groups_paired_end(tmp_path):
    _touch(tmp_path, "MIMICC_A_1_R1.fastq.gz", "MIMICC_A_1_R2.fastq.gz", "notes.txt")
    groups = read_assign.scan_reads(tmp_path)
    assert len(groups) == 1
    g = groups[0]
    assert g["paired"] is True
    assert g["files_by_mate"] == {"1": "MIMICC_A_1_R1.fastq.gz", "2": "MIMICC_A_1_R2.fastq.gz"}


def test_scan_single_end(tmp_path):
    _touch(tmp_path, "sampleX.fastq.gz")
    groups = read_assign.scan_reads(tmp_path)
    assert len(groups) == 1
    assert groups[0]["paired"] is False


def test_scan_underscore_mate_tokens(tmp_path):
    _touch(tmp_path, "run5_1.fq.gz", "run5_2.fq.gz")
    groups = read_assign.scan_reads(tmp_path)
    assert groups[0]["group"] == "run5"
    assert groups[0]["paired"] is True


def test_scan_missing_dir(tmp_path):
    assert read_assign.scan_reads(tmp_path / "nope") == []


# group_files is the pure core scan_reads delegates to; the manual/CLI reads
# mode feeds it names from the browser's directory picker instead of a listing.


def test_group_files_pairs_and_drops_non_reads():
    groups = read_assign.group_files(["runA_R2.fastq.gz", "runA_R1.fastq.gz", "README.md", ".DS_Store", "md5sums.txt"])
    assert len(groups) == 1
    assert groups[0]["group"] == "runA"
    assert groups[0]["paired"] is True
    assert groups[0]["files"] == ["runA_R1.fastq.gz", "runA_R2.fastq.gz"]


def test_group_files_mixed_paired_and_single():
    groups = read_assign.group_files(["b_1.fq.gz", "b_2.fq.gz", "a.bam"])
    assert [g["group"] for g in groups] == ["a", "b"]  # sorted by stem
    assert [g["paired"] for g in groups] == [False, True]


def test_group_files_half_a_pair_is_not_paired():
    groups = read_assign.group_files(["lonely_R1.fastq.gz"])
    assert groups[0]["paired"] is False
    assert groups[0]["files_by_mate"] == {"1": "lonely_R1.fastq.gz"}


def test_group_files_empty():
    assert read_assign.group_files([]) == []


def test_suggest_matches_alias_in_filename():
    groups = [{"group": "MIMICC_A_1_R", "files": ["MIMICC_A_1_R1.fastq.gz"]}]
    samples = [
        {"alias": "MIMICC_A_1", "accession": "ERS111"},
        {"alias": "MIMICC_B_2", "accession": "ERS222"},
    ]
    out = read_assign.suggest(groups, samples)
    assert out[0]["suggested_sample"] == "ERS111"
    assert out[0]["confidence"] == "high"


def test_suggest_no_match():
    groups = [{"group": "unrelated", "files": ["unrelated.fastq.gz"]}]
    out = read_assign.suggest(groups, [{"alias": "MIMICC_A_1", "accession": "ERS111"}])
    assert out[0]["suggested_sample"] == ""
    assert out[0]["confidence"] == "none"


def test_suggest_prefers_longest_alias():
    groups = [{"group": "MIMICC_A_10", "files": []}]
    samples = [
        {"alias": "MIMICC_A_1", "accession": "ERS1"},
        {"alias": "MIMICC_A_10", "accession": "ERS10"},
    ]
    out = read_assign.suggest(groups, samples)
    assert out[0]["suggested_sample"] == "ERS10"


def test_build_manifest_paired(tmp_path):
    record = {
        "NAME": "run1",
        "STUDY": "ERP1",
        "SAMPLE": "ERS1",
        "PLATFORM": "ILLUMINA",
        "INSTRUMENT": "Illumina MiSeq",
        "LIBRARY_SOURCE": "METAGENOMIC",
        "LIBRARY_SELECTION": "PCR",
        "LIBRARY_STRATEGY": "AMPLICON",
        "FASTQ1": "run1_R1.fastq.gz",
        "FASTQ2": "run1_R2.fastq.gz",
    }
    alias, path = read_assign.build_manifest(record, tmp_path)
    assert alias.startswith("run1_")
    text = path.read_text()
    assert "STUDY\tERP1" in text
    assert "SAMPLE\tERS1" in text
    assert text.count("FASTQ\t") == 2
    assert f"NAME\t{alias}" in text


def test_build_manifest_validates_required(tmp_path):
    with pytest.raises(ValueError):
        read_assign.build_manifest({"NAME": "x"}, tmp_path)


def test_validate_record_reports_missing():
    problems = read_assign.validate_record({"NAME": "x", "STUDY": "ERP1"})
    assert any("SAMPLE" in p for p in problems)
    assert any("read file" in p for p in problems)


def test_parse_accessions():
    lines = ["INFO: created", "experiment ERX123 and run ERR456 done"]
    assert read_assign.parse_accessions(lines) == {
        "experiment_accession": "ERX123",
        "run_accession": "ERR456",
    }


# ---------------------------------------------------------------------------
# Upload plan (resume) and helper outcomes — run in the browser via py()
# ---------------------------------------------------------------------------

_RUN = {
    "NAME": "MIMICC_A_1",
    "STUDY": "ERP1",
    "SAMPLE": "ERS1",
    "PLATFORM": "ILLUMINA",
    "INSTRUMENT": "Illumina MiSeq",
    "LIBRARY_SOURCE": "METAGENOMIC",
    "LIBRARY_SELECTION": "PCR",
    "LIBRARY_STRATEGY": "AMPLICON",
    "FASTQ1": "MIMICC_A_1_R1.fastq.gz",
    "FASTQ2": "MIMICC_A_1_R2.fastq.gz",
}


def _plan(runs, *, prefix="batch 7", ledger=None, existing=None, force_reupload=False):
    return read_assign.plan_reads(
        runs, prefix=prefix, ledger=ledger or {}, existing=existing or {}, force_reupload=force_reupload
    )


def test_run_alias_is_stable_and_alias_safe():
    assert read_assign.run_alias("batch 7", "MIMICC/A 1") == "batch-7_MIMICC-A-1"


def test_plan_submits_with_stable_alias_and_manifest_text():
    (entry,) = _plan([_RUN])
    assert entry["action"] == "submit"
    assert entry["alias"] == entry["stable_alias"] == "batch-7_MIMICC_A_1"
    assert entry["manifest_filename"] == "batch-7_MIMICC_A_1.manifest"
    assert "STUDY\tERP1" in entry["manifest_text"]
    assert "FASTQ\tMIMICC_A_1_R1.fastq.gz" in entry["manifest_text"]


def test_plan_without_prefix_is_one_off():
    (entry,) = _plan([_RUN], prefix=None)
    assert entry["stable_alias"] is None
    assert entry["alias"].startswith("MIMICC_A_1_")
    assert read_assign.resume_candidates([_RUN], prefix=None) == set()


def test_plan_marks_invalid_run_as_skip():
    (entry,) = _plan([{"NAME": "broken"}])
    assert (entry["action"], entry["reason"], entry["success"]) == ("skip", "invalid", False)


def test_plan_skips_runs_the_ledger_already_has():
    ledger = {"MIMICC_A_1": {"status": "done", "run_accession": "ERR1"}}
    (entry,) = _plan([_RUN], ledger=ledger)
    assert (entry["action"], entry["reason"], entry["run_accession"]) == ("skip", "cached", "ERR1")


def test_plan_skips_runs_already_in_ena():
    existing = {"batch-7_MIMICC_A_1": {"experiment_accession": "ERX1", "run_accession": "ERR1"}}
    assert read_assign.resume_candidates([_RUN], prefix="batch 7") == {"batch-7_MIMICC_A_1"}
    (entry,) = _plan([_RUN], existing=existing)
    assert (entry["action"], entry["reason"]) == ("skip", "already_in_ena")


def test_forced_reupload_gets_a_fresh_alias_and_skips_the_lookup():
    existing = {"batch-7_MIMICC_A_1": {"run_accession": "ERR1"}}
    (entry,) = _plan([{**_RUN, "reupload": True}], existing=existing)
    assert entry["action"] == "submit"
    assert entry["alias"].startswith("batch-7_MIMICC_A_1_") and entry["stable_alias"] == "batch-7_MIMICC_A_1"
    assert read_assign.resume_candidates([_RUN], prefix="batch 7", force_reupload=True) == set()


def test_upload_result_parses_the_log_and_prefers_helper_accessions():
    from conftest import MOCK_READS_LOG

    result = read_assign.upload_result(name="MIMICC_A_1", alias="a", exit_code=0, log=MOCK_READS_LOG)
    assert (result["success"], result["run_accession"], result["experiment_accession"]) == (
        True,
        "ERR9000001",
        "ERX9000001",
    )
    override = read_assign.upload_result(name="x", exit_code=1, log=MOCK_READS_LOG, run_accession="ERR2")
    assert (override["success"], override["run_accession"]) == (False, "ERR2")


# --- ena_service composition ------------------------------------------------


def test_service_plan_looks_up_candidates_once(monkeypatch):
    import ena_service

    seen = []

    def lookup(creds, aliases, *, test):
        seen.append((aliases, test))
        return {"batch-7_MIMICC_A_1": {"run_accession": "ERR1"}}

    monkeypatch.setattr(ena_service, "lookup_existing_runs", lookup)
    creds = ena_service.Credentials(username="Webin-1", password="pw")
    out = ena_service.plan_reads(creds, [_RUN], test=False, prefix="batch 7")
    assert seen == [({"batch-7_MIMICC_A_1"}, False)]
    assert out["plan"][0]["reason"] == "already_in_ena" and out["warnings"] == []


def test_service_plan_turns_a_failed_lookup_into_a_warning(monkeypatch):
    import ena_service

    def lookup(*_a, **_k):
        raise RuntimeError("Reports API down")

    monkeypatch.setattr(ena_service, "lookup_existing_runs", lookup)
    out = ena_service.plan_reads(ena_service.Credentials("u", "p"), [_RUN], prefix="batch 7")
    assert out["plan"][0]["action"] == "submit"
    assert "Reports API down" in out["warnings"][0]


def test_service_plan_needs_runs():
    import ena_service

    with pytest.raises(ValueError, match="No runs"):
        ena_service.plan_reads(ena_service.Credentials("u", "p"), [])


def test_service_prepare_sample_records_flattens_the_container(monkeypatch):
    import ena_service

    container = {"Container": {"MIMICC_SampleExperiments": [{"alias": "s1"}]}}
    monkeypatch.setattr(ena_service, "prepare_samples", lambda export, where=None: container)
    assert ena_service.prepare_sample_records({"any": "thing"}) == {"records": [{"alias": "s1"}], "count": 1}


def test_service_prepare_study_records_needs_a_selected_schema(tmp_path):
    import ena_service

    with pytest.raises(ValueError, match="No study schema selected"):
        ena_service.prepare_study_records({}, dh_dir=tmp_path)
