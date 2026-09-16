"""Assign sequencing read files to ENA samples and build webin-cli manifests.

The genuinely new subsystem of this app. Flow:

  1. ``scan_reads``  -> discover FASTQ/BAM/CRAM files in the reads workspace and
     group paired-end mates.
  2. ``suggest``     -> match each read group to a sample by filename.
  3. ``build_manifest`` -> write a webin-cli "reads" manifest for one run.

Manifests are written *into the reads workspace* (a directory mounted
read-write into this container and by host path into the webin-cli sibling
container) so that the manifest and its FASTQ files share one ``-inputDir``.
FASTQ values are stored as basenames, resolved by webin-cli inside ``/data``.

The manifest field set + alias timestamping is ported from
``ena-submission-dataharmonizer/scripts/submit_reads.py:build_manifest`` (which
we do not import, to avoid its mgnify-toolkit/JAR dependency).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Final

# Recognised read-file extensions (lower-cased).
_READ_SUFFIXES: Final = (".fastq.gz", ".fq.gz", ".fastq", ".fq", ".bam", ".cram")

# Mate tokens for paired-end grouping, tried in order.
_MATE_RE: Final = re.compile(r"(.+?)[._](?:R)?([12])$")

_REQUIRED_FIELDS: Final = (
    "STUDY",
    "SAMPLE",
    "NAME",
    "PLATFORM",
    "INSTRUMENT",
    "LIBRARY_SOURCE",
    "LIBRARY_SELECTION",
    "LIBRARY_STRATEGY",
)
_OPTIONAL_FIELDS: Final = ("INSERT_SIZE", "LIBRARY_NAME", "DESCRIPTION")


# ---------------------------------------------------------------------------
# Scanning / grouping
# ---------------------------------------------------------------------------


def _read_suffix(name: str) -> str | None:
    lower = name.lower()
    for suffix in _READ_SUFFIXES:
        if lower.endswith(suffix):
            return suffix
    return None


def _stem_and_mate(name: str, suffix: str) -> tuple[str, str | None]:
    """Return (group_stem, mate) for a read filename; mate is '1', '2' or None."""
    base = name[: -len(suffix)]
    m = _MATE_RE.match(base)
    if m:
        return m.group(1), m.group(2)
    return base, None


def group_files(names: Iterable[str]) -> list[dict[str, Any]]:
    """Group read *filenames* into runs by paired-end mate token.

    Pure: takes basenames, touches no filesystem. Shared by the local-helper
    scan (which lists the directory itself) and the manual/CLI mode, where the
    browser's directory picker supplies the names. Non-read files are dropped,
    so callers may pass a whole directory listing.

    Returns a list of read groups, each::

        {"group": <stem>, "paired": bool, "files": [<basename>, ...],
         "files_by_mate": {"1": ..., "2": ...} | {}}
    """
    groups: dict[str, dict[str, Any]] = {}
    for name in names:
        suffix = _read_suffix(name)
        if suffix is None:
            continue
        stem, mate = _stem_and_mate(name, suffix)
        group = groups.setdefault(stem, {"group": stem, "files": [], "files_by_mate": {}})
        group["files"].append(name)
        if mate:
            group["files_by_mate"][mate] = name

    result = []
    for group in groups.values():
        group["files"].sort()
        group["paired"] = set(group["files_by_mate"]) >= {"1", "2"}
        result.append(group)
    result.sort(key=lambda g: g["group"])
    return result


def scan_reads(reads_dir: Path) -> list[dict[str, Any]]:
    """Discover read files under ``reads_dir`` and group paired-end mates.

    File names are basenames relative to ``reads_dir`` (the webin-cli ``/data``).
    """
    if not reads_dir.is_dir():
        return []
    return group_files(path.name for path in sorted(reads_dir.iterdir()) if path.is_file())


# ---------------------------------------------------------------------------
# Auto-suggest sample <- read group
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def suggest(groups: list[dict[str, Any]], samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Suggest a sample for each read group by matching the alias in the filename.

    ``samples`` items expose ``alias`` and ``accession``. Returns the groups
    annotated with ``suggested_sample`` (accession or "") and ``suggested_alias``
    and a ``confidence`` of "high" (alias token found in stem) or "none".
    """
    indexed = [(s, _normalise(s.get("alias") or "")) for s in samples if (s.get("alias") or "").strip()]
    out = []
    for group in groups:
        stem_norm = _normalise(group["group"])
        match = None
        # Prefer the longest alias that appears in the stem (most specific).
        for sample, alias_norm in sorted(indexed, key=lambda x: len(x[1]), reverse=True):
            if alias_norm and alias_norm in stem_norm:
                match = sample
                break
        annotated = dict(group)
        annotated["suggested_sample"] = (match or {}).get("accession", "")
        annotated["suggested_alias"] = (match or {}).get("alias", "")
        annotated["confidence"] = "high" if match else "none"
        out.append(annotated)
    return out


# ---------------------------------------------------------------------------
# Manifest building
# ---------------------------------------------------------------------------


def validate_record(record: dict[str, Any]) -> list[str]:
    """Return a list of validation problems for a run record (empty == valid)."""
    problems = [f"missing {f}" for f in _REQUIRED_FIELDS if not str(record.get(f, "")).strip()]
    files = (
        record.get("FASTQ") or record.get("FASTQ1") or record.get("FASTQ2") or record.get("BAM") or record.get("CRAM")
    )
    if not files:
        problems.append("no read file(s): need FASTQ, FASTQ1+FASTQ2, BAM, or CRAM")
    return problems


def build_manifest(record: dict[str, Any], workdir: Path, *, alias: str | None = None) -> tuple[str, Path]:
    """Write a tab-delimited webin-cli "reads" manifest for one run into ``workdir``.

    If ``alias`` is given it is used verbatim as the run's NAME/alias (the
    session-aware caller passes a stable, account-unique alias so the run can
    be detected in ENA on a later resume). If ``alias`` is None, a timestamp is
    appended to ``NAME`` so re-submitting the same run yields a distinct alias
    (the original behaviour; avoids ENA duplicate-alias rejections). Returns
    (alias, manifest_path). Raises ValueError if the record is invalid.
    """
    problems = validate_record(record)
    if problems:
        raise ValueError(f"Run {record.get('NAME', '<unknown>')!r}: " + "; ".join(problems))

    alias, text = build_manifest_text(record, alias=alias)
    workdir.mkdir(parents=True, exist_ok=True)
    manifest_path = workdir / f"{alias}.manifest"
    manifest_path.write_text(text)
    return alias, manifest_path


def build_manifest_text(record: dict[str, Any], *, alias: str | None = None) -> tuple[str, str]:
    """Build the webin-cli "reads" manifest text for one run, without writing it.

    Used by the hosted server to hand the manifest to the browser/local helper
    (reads upload happens on the user's machine). Manifests reference read files
    by basename only — the helper supplies the local input directory. Returns
    (alias, manifest_text). Raises ValueError if the record is invalid.
    """
    problems = validate_record(record)
    if problems:
        raise ValueError(f"Run {record.get('NAME', '<unknown>')!r}: " + "; ".join(problems))

    if alias is None:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        alias = f"{record['NAME']}_{timestamp}"

    fields: list[tuple[str, str]] = [
        ("STUDY", record["STUDY"]),
        ("SAMPLE", record["SAMPLE"]),
        ("NAME", alias),
        ("PLATFORM", record["PLATFORM"]),
        ("INSTRUMENT", record["INSTRUMENT"]),
        ("LIBRARY_SOURCE", record["LIBRARY_SOURCE"]),
        ("LIBRARY_SELECTION", record["LIBRARY_SELECTION"]),
        ("LIBRARY_STRATEGY", record["LIBRARY_STRATEGY"]),
    ]
    for key in _OPTIONAL_FIELDS:
        if record.get(key):
            fields.append((key, str(record[key])))
    for key in ("FASTQ1", "FASTQ2", "FASTQ"):
        if record.get(key):
            fields.append(("FASTQ", record[key]))
    for key in ("BAM", "CRAM"):
        if record.get(key):
            fields.append((key, record[key]))

    text = "".join(f"{key}\t{value}\n" for key, value in fields)
    return alias, text


# ---------------------------------------------------------------------------
# Parse webin-cli output for accessions
# ---------------------------------------------------------------------------


def parse_accessions(log_lines: list[str]) -> dict[str, str]:
    """Best-effort extraction of experiment/run accessions from webin-cli output."""
    text = "\n".join(log_lines)
    result: dict[str, str] = {}
    if exp := re.search(r"\bERX\d+\b", text):
        result["experiment_accession"] = exp.group(0)
    if run := re.search(r"\bERR\d+\b", text):
        result["run_accession"] = run.group(0)
    return result


# ---------------------------------------------------------------------------
# Upload plan (resume) and helper outcomes
# ---------------------------------------------------------------------------

# Resume-ledger status values. The browser stores the ledger; these only read it.
STATUS_DONE: Final = "done"
STATUS_ALREADY_IN_ENA: Final = "already_in_ena"


def _slug(text: str) -> str:
    """Alias-safe slug: keep word chars, collapse the rest to '-'."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text).strip()).strip("-")
    return s or "x"


def run_alias(prefix: str, run_name: str) -> str:
    """Stable per-run alias, ``<prefix>_<run name>``. Identical across
    re-submits with the same prefix — which is what lets a resume detect a run
    already in ENA."""
    return f"{_slug(prefix)}_{_slug(run_name)}"


def _run_name(run: dict[str, Any], idx: int) -> str:
    return run.get("NAME", f"run{idx}")


def resume_candidates(runs: list[dict[str, Any]], *, prefix: str | None, force_reupload: bool = False) -> set[str]:
    """The stable aliases worth looking up in ENA before planning: none
    without a prefix, and none for runs being forced up again."""
    if not prefix or force_reupload:
        return set()
    return {
        run_alias(prefix, _run_name(run, idx))
        for idx, run in enumerate(runs, start=1)
        if not run.get("reupload", False)
    }


def _skip_entry(run: dict[str, Any], name: str, alias: str, accs: dict[str, Any], reason: str) -> dict[str, Any]:
    """A plan row for a run skipped during resume (already submitted/in ENA)."""
    return {
        "name": name,
        "action": "skip",
        "alias": alias,
        "sample": run.get("SAMPLE", ""),
        "study": run.get("STUDY", ""),
        "exit_code": 0,
        "success": True,
        "skipped": True,
        "reason": reason,
        "experiment_accession": accs.get("experiment_accession", ""),
        "run_accession": accs.get("run_accession", ""),
    }


def plan_reads(
    runs: list[dict[str, Any]],
    *,
    prefix: str | None,
    ledger: dict[str, dict[str, Any]],
    existing: dict[str, dict[str, str]],
    force_reupload: bool = False,
) -> list[dict[str, Any]]:
    """Decide, per run, whether to submit (with its manifest text) or skip.

    ``ledger`` is the browser's resume ledger (run name -> row); ``existing``
    is what ENA already holds, keyed by stable alias (``resume_candidates``).
    Without a prefix every run is a one-off with a timestamped alias. A forced
    re-upload gets a fresh timestamped alias, since ENA aliases are permanent.
    """
    plan: list[dict[str, Any]] = []
    for idx, run in enumerate(runs, start=1):
        name = _run_name(run, idx)
        stable = run_alias(prefix, name) if prefix else None
        run_forced = force_reupload or run.get("reupload", False)

        if stable and not run_forced:
            row = ledger.get(name)
            if (
                row
                and row.get("status") in (STATUS_DONE, STATUS_ALREADY_IN_ENA)
                and (row.get("run_accession") or row.get("experiment_accession"))
            ):
                plan.append(_skip_entry(run, name, stable, row, "cached"))
                continue
            if stable in existing:
                plan.append(_skip_entry(run, name, stable, existing[stable], "already_in_ena"))
                continue

        manifest_alias = stable
        if stable and run_forced:
            manifest_alias = f"{stable}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        try:
            alias, manifest_text = build_manifest_text(run, alias=manifest_alias)
        except ValueError as exc:
            plan.append(
                {
                    "name": name,
                    "action": "skip",
                    "success": False,
                    "skipped": True,
                    "reason": "invalid",
                    "messages": str(exc),
                }
            )
            continue
        plan.append(
            {
                "name": name,
                "action": "submit",
                "alias": alias,
                "stable_alias": stable,
                "manifest_filename": f"{alias}.manifest",
                "manifest_text": manifest_text,
                "sample": run.get("SAMPLE", ""),
                "study": run.get("STUDY", ""),
            }
        )
    return plan


def upload_result(
    *,
    name: str,
    alias: str | None = None,
    stable_alias: str | None = None,
    exit_code: int | None = None,
    log: str = "",
    sample: str = "",
    study: str = "",
    experiment_accession: str | None = None,
    run_accession: str | None = None,
) -> dict[str, Any]:
    """A result row for one helper-run upload: accessions parsed from the
    webin-cli log, overridden by any the helper reported itself."""
    accs = parse_accessions(log.splitlines()) if log else {}
    if experiment_accession:
        accs["experiment_accession"] = experiment_accession
    if run_accession:
        accs["run_accession"] = run_accession
    return {
        "name": name,
        "alias": alias,
        "sample": sample,
        "study": study,
        "exit_code": exit_code,
        "success": exit_code == 0,
        "skipped": False,
        **accs,
    }
