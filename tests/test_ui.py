"""Playwright UI tests.

Driven against a real WSGI server (``live_server_url`` fixture) with the
webin-cli runner patched, so no Docker is required. Skipped automatically if
Playwright (and its browsers) are not installed.
"""

from __future__ import annotations

import json
import pathlib

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402


def _wait_for_workspace(pg):
    """The app restores its one workspace on load, with no prompt."""
    pg.wait_for_function("() => window.WORKSPACE_READY === true")


# What the account "holds", per entity — the default answer to list_records.
_RECORDS = {
    "studies": [
        {"alias": "studyA", "accession": "ERP111", "title": "Study A", "status": "PRIVATE"},
        {"alias": "studyB", "accession": "ERP222", "title": "Study B", "status": "PRIVATE"},
    ],
    "samples": [
        {"alias": "MIMICC_A_1", "accession": "ERS111", "title": "Sample A1", "status": "PRIVATE"},
        {"alias": "MIMICC_B_2", "accession": "ERS222", "title": "Sample B2", "status": "PRIVATE"},
    ],
    "runs": [
        {
            "alias": "runA",
            "accession": "ERR111",
            "experiment_accession": "ERX111",
            "study_accession": "ERP111",
            "sample_accession": "ERS111",
            "status": "PRIVATE",
            # ENA's run-processing report: whether the read files are
            # archived, which registering a run does not say.
            "process_status": "COMPLETED",
            "process_date": "2026-01-02",
        },
        {
            "alias": "runB",
            "accession": "ERR222",
            "experiment_accession": "ERX222",
            "study_accession": "ERP111",
            "sample_accession": "ERS222",
            "status": "PRIVATE",
            "process_status": "IN_QUEUE",
            "process_date": "2026-01-02",
        },
    ],
    "experiments": [
        {
            "alias": "expA",
            "accession": "ERX111",
            "title": "Experiment A",
            "study_accession": "ERP111",
            "sample_accession": "ERS111",
            "status": "PRIVATE",
        },
    ],
}

_DEFAULT_PY = {
    "ena_service.list_records": {"__by_entity": _RECORDS},
    "ena_service.read_editable_fields": {},
}


def _stub_py(page, responses):
    """Replace py() — the page's call into Python — with canned answers keyed by
    target (on top of ``_DEFAULT_PY``), recording each call in
    ``window.__pyCalls``. ``{"__error": msg}`` rejects; ``{"__by_entity": {...}}``
    answers by the call's ``entity``. The real worker is covered by the Pyodide
    tests at the end of this file."""
    page.evaluate(
        """(responses) => {
            window.__pyCalls = [];
            py = async (target, kwargs = {}, files = {}) => {
                window.__pyCalls.push({ target, kwargs, files });
                if (!(target in responses)) throw new Error('unstubbed py(): ' + target);
                const answer = responses[target];
                if (answer && answer.__error) throw new Error(answer.__error);
                if (answer && answer.__by_entity) return answer.__by_entity[kwargs.entity] || [];
                return answer;
            };
        }""",
        {**_DEFAULT_PY, **responses},
    )


def _use_real_python(page):
    page.evaluate("() => { py = window.__realPy; }")


def _py_calls(page, target):
    return page.evaluate("(target) => window.__pyCalls.filter((c) => c.target === target)", target)


@pytest.fixture
def page(live_server_url):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browsers not installed
            pytest.skip(f"Chromium not available: {exc}")
            return
        pg = browser.new_page()
        pg.goto(live_server_url)
        _wait_for_workspace(pg)
        pg.evaluate("() => { window.__realPy = py; }")
        _stub_py(pg, {})
        yield pg
        browser.close()


def test_page_loads_with_tabs(page):
    assert "MIMICC ENA Submission Assistant" in page.title()
    for tab in ("Credentials", "Studies", "Samples", "Reads", "Records"):
        assert page.query_selector(f"a.vf-tabs__link:has-text('{tab}')")


def test_env_pill_default_test(page):
    assert page.inner_text("#envPill").strip() == "TEST"


def test_tab_switching(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    # VF JS assigns the section ID to the tab anchor too; target content sections by class position
    sections = page.locator(".vf-tabs-content .vf-tabs__section")
    reads_idx = 3  # 0=creds, 1=studies, 2=samples, 3=reads
    assert sections.nth(reads_idx).is_visible()
    assert not sections.nth(0).is_visible()  # creds section should be hidden


def test_credentials_indicator(page):
    # Credentials are entered in the browser (held for this tab only); saving
    # them flips the indicator to "set".
    page.fill("#username", "Webin-test")
    page.fill("#password", "secret")
    page.click("#vf-tabs__section--creds button:has-text('Save')")
    page.wait_for_timeout(100)
    assert "set" in page.inner_text("#credStatus")


def test_library_preset_ui_removed(page):
    # Experiment metadata now comes from its own DataHarmonizer panel, not a
    # hardcoded preset dropdown.
    page.click("a.vf-tabs__link:has-text('Reads')")
    assert page.query_selector("#presetSelect") is None
    assert page.query_selector("#expDhPanel") is not None


def test_study_submit_displays_submission_logs(page):
    page.click("a.vf-tabs__link:has-text('Studies')")
    assert page.locator("#studyLog.log").count() == 1
    assert page.inner_text("#studyLog").strip() == "No study submission run yet."
    assert page.locator("#vf-tabs__section--studies h3:has-text('Submission log')").count() == 1
    page.evaluate(
        """() => {
            CREDS = { username: 'Webin-test', password: 'secret' };
            window.__preparedStudies = [{ alias: "study-a", TITLE: "Study A" }];
        }"""
    )
    _stub_py(
        page,
        {
            "ena_service.submit_studies": {
                "success": False,
                "accessions": [],
                "error": "receipt rejected",
                "logs": ["INFO: XSD validation passed", "ERROR: Receipt: invalid study"],
            }
        },
    )

    page.click("button:has-text('Submit prepared studies')")
    page.wait_for_function("() => document.querySelector('#studyLog')?.innerText.includes('XSD validation passed')")
    assert page.inner_text("#studyBanner").strip() == "invalid study"
    assert "INFO: XSD validation passed" in page.inner_text("#studyLog")
    assert "ERROR: Receipt: invalid study" in page.inner_text("#studyLog")
    assert "No records." in page.inner_text("#studyOut")
    assert page.evaluate("() => window.__lastStudySubmitResponse?.error") == "receipt rejected"
    (call,) = _py_calls(page, "ena_service.submit_studies")
    assert call["kwargs"]["creds"] == {"username": "Webin-test", "password": "secret"}
    assert call["kwargs"]["test"] is True
    assert set(call["files"]) == {"/assets/ena_schema/ENA.project.xsd", "/assets/ena_schema/SRA.common.xsd"}


def test_study_submit_without_prepared_records_logs_error(page):
    page.click("a.vf-tabs__link:has-text('Studies')")
    page.click("button:has-text('Submit prepared studies')")
    page.wait_for_function("() => document.querySelector('#studyBanner')?.innerText.includes('No prepared studies')")
    assert "No prepared studies" in page.inner_text("#studyBanner")
    assert page.inner_text("#studyLog").strip() == "ERROR: No prepared studies. Click Prepare first."


def test_study_submit_displays_log_area_when_response_has_no_logs(page):
    page.click("a.vf-tabs__link:has-text('Studies')")
    page.evaluate(
        """() => {
            CREDS = { username: 'Webin-test', password: 'secret' };
            window.__preparedStudies = [{ alias: "study-a", TITLE: "Study A" }];
        }"""
    )
    _stub_py(page, {"ena_service.submit_studies": {"success": False, "accessions": [], "error": "receipt rejected"}})

    page.click("button:has-text('Submit prepared studies')")
    page.wait_for_function("() => document.querySelector('#studyLog')?.innerText.includes('receipt rejected')")
    assert page.inner_text("#studyLog").strip() == "ERROR: receipt rejected"


def test_study_prepare_displays_table_and_banner_in_prepare_panel(page):
    page.click("a.vf-tabs__link:has-text('Studies')")
    _stub_py(page, {"ena_service.prepare_study_records": {"records": [{"alias": "study-a", "TITLE": "Study A"}]}})
    # studyDhApi() is normally backed by the DH iframe; stub it directly so
    # Prepare can run without a real DataHarmonizer bundle in this test.
    page.evaluate("() => { studyDhApi = () => ({ getExportJson: () => ({}) }); }")
    page.click("#vf-tabs__section--studies button:has-text('Prepare')")
    page.wait_for_selector("#studyPrepOut table")
    assert "study-a" in page.inner_text("#studyPrepOut")
    assert "Prepared 1 study record(s)" in page.inner_text("#studyPrepBanner")
    # The old shared banner must NOT pick up prepare feedback anymore.
    assert page.inner_text("#studyBanner").strip() == ""
    # Python reads the selected study schema from where the grid loads it.
    (call,) = _py_calls(page, "ena_service.prepare_study_records")
    assert call["kwargs"]["dh_dir"] == "/dh"
    assert call["files"] == {"/dh/templates/study/schema.yaml": "/templates/study/schema.yaml"}


def test_sample_prepare_displays_table_like_study(page):
    page.click("a.vf-tabs__link:has-text('Samples')")
    _stub_py(
        page,
        {"ena_service.prepare_sample_records": {"records": [{"alias": "sample-a", "TITLE": "Sample A"}], "count": 1}},
    )
    # No DH iframe is loaded in this test environment, so dhApi() returns
    # null and Prepare falls back to the textarea, matching the documented
    # "DataHarmonizer isn't available" path.
    page.fill("#dhExport", '{"Container": {}}')
    page.click("#vf-tabs__section--samples button:has-text('Prepare')")
    page.wait_for_selector("#prepOut table")
    assert "sample-a" in page.inner_text("#prepOut")
    assert "Prepared 1 sample record(s)" in page.inner_text("#prepBanner")
    (call,) = _py_calls(page, "ena_service.prepare_sample_records")
    assert call["kwargs"]["dh_export"] == {"Container": {}}
    assert call["files"] == {"/schemas/mimicc_sample.yaml": "/schemas/mimicc_sample.yaml"}


def test_sample_prepare_shows_python_errors(page):
    page.click("a.vf-tabs__link:has-text('Samples')")
    _stub_py(page, {"ena_service.prepare_sample_records": {"__error": "Unknown filter slot: nope"}})
    page.fill("#dhExport", '{"Container": {}}')
    page.click("#vf-tabs__section--samples button:has-text('Prepare')")
    page.wait_for_function("() => document.getElementById('prepBanner').innerText.includes('Unknown filter slot')")
    assert page.is_disabled("#sampleSubmitBtn")


def test_iframe_loses_focus_on_outside_click(page):
    page.click("a.vf-tabs__link:has-text('Samples')")
    # No DataHarmonizer bundle is built here, so config.json hides the grid;
    # focus handling needs only the (empty) iframe on screen.
    page.evaluate("() => { $('dhWrap').style.display = ''; $('dhMissing').style.display = 'none'; }")
    page.click("#dhFrame")
    assert page.evaluate("() => document.activeElement.id") == "dhFrame"
    page.click("#sampleFilter")
    assert page.evaluate("() => document.activeElement.id") == "sampleFilter"
    page.fill("#sampleFilter", "hello")
    assert page.evaluate("() => $('sampleFilter').value") == "hello"


def test_maximize_controls_for_reads_and_dataharmonizer(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.click("#readsAssignPanel button[aria-label='Maximize panel']")
    assert "maximized" in page.get_attribute("#readsAssignPanel", "class")
    # The fixed panel must sit above Visual Framework's hero and tab layers;
    # otherwise those elements are visible through the maximized pairing view.
    page.evaluate("window.scrollTo(0, 0)")
    stacking_check = page.evaluate(
        """() => {
            const panel = document.querySelector('#readsAssignPanel');
            return ['.vf-hero__heading', '.vf-tabs__link'].map((selector) => {
                const rect = document.querySelector(selector).getBoundingClientRect();
                const topElement = document.elementFromPoint(
                    rect.left + rect.width / 2, rect.top + rect.height / 2,
                );
                return { selector, covered: panel.contains(topElement) };
            });
        }"""
    )
    assert all(result["covered"] for result in stacking_check), stacking_check
    page.click("#readsAssignPanel button[aria-label='Minimize panel']")
    assert "maximized" not in page.get_attribute("#readsAssignPanel", "class")

    page.click("a.vf-tabs__link:has-text('Studies')")
    page.click("#studyDhPanel button[aria-label='Maximize panel']")
    assert "maximized" in page.get_attribute("#studyDhPanel", "class")
    page.click("#studyDhPanel button[aria-label='Minimize panel']")
    assert "maximized" not in page.get_attribute("#studyDhPanel", "class")

    page.click("a.vf-tabs__link:has-text('Samples')")
    page.click("#dhPanel button[aria-label='Maximize panel']")
    assert "maximized" in page.get_attribute("#dhPanel", "class")
    page.click("#dhPanel button[aria-label='Minimize panel']")
    assert "maximized" not in page.get_attribute("#dhPanel", "class")

    page.click("a.vf-tabs__link:has-text('Reads')")
    page.click("#expDhPanel button[aria-label='Maximize panel']")
    assert "maximized" in page.get_attribute("#expDhPanel", "class")
    page.click("#expDhPanel button[aria-label='Minimize panel']")
    assert "maximized" not in page.get_attribute("#expDhPanel", "class")


def test_reads_pairing_splitter_resizes_full_height_grid(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.click("#readsAssignPanel button[aria-label='Maximize panel']")
    page.wait_for_timeout(100)

    grid = page.locator("#pairSamples")
    divider = page.locator("#readsAssignDivider")
    grid_box = grid.bounding_box()
    workspace_box = page.locator("#readsAssignGrid").bounding_box()
    # The sample browser consumes the left pane's available height rather than
    # keeping its old fixed height when the panel is maximized.
    assert grid_box["height"] > 250
    assert workspace_box["y"] + workspace_box["height"] - (grid_box["y"] + grid_box["height"]) < 2
    row_viewport = page.locator("#pairSamples .ht_master .wtHolder").first.bounding_box()
    assert row_viewport["height"] > grid_box["height"] - 120
    assert row_viewport["y"] + row_viewport["height"] <= grid_box["y"] + grid_box["height"] + 2

    page.evaluate("() => banner('readsBanner', true, 'Samples loaded.')")
    page.wait_for_timeout(100)
    assert grid.bounding_box()["y"] + grid.bounding_box()["height"] <= page.locator("#readsBanner").bounding_box()["y"]
    row_viewport = page.locator("#pairSamples .ht_master .wtHolder").first.bounding_box()
    grid_box = grid.bounding_box()
    assert row_viewport["y"] + row_viewport["height"] <= grid_box["y"] + grid_box["height"] + 2

    before = page.locator(".assign-samples-pane").bounding_box()["width"]
    divider_box = divider.bounding_box()
    page.mouse.move(divider_box["x"] + 7, divider_box["y"] + 30)
    page.mouse.down()
    page.mouse.move(divider_box["x"] + 120, divider_box["y"] + 30)
    page.mouse.up()

    assert page.locator(".assign-samples-pane").bounding_box()["width"] > before + 80
    assert int(divider.get_attribute("aria-valuenow")) > 42


def _load_pairing_samples(page):
    page.click("button:has-text('Load samples')")
    page.wait_for_function("() => document.getElementById('pairSamples').getRows().length > 0")


def _assigned_count(page, accession):
    """The reads_assigned badge, read through the element's rendered cell."""
    return page.evaluate(
        """(acc) => {
            const grid = document.getElementById('pairSamples');
            const index = grid.getRows().findIndex((row) => row.accession === acc);
            const cell = document.querySelectorAll(
                '#pairSamples .ht_clone_inline_start td.ena-browser-badge')[index];
            return cell ? cell.innerText.trim() : null;
        }""",
        accession,
    )


def test_reads_sample_assignment_and_row_delete(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    _load_pairing_samples(page)
    assert page.evaluate("() => document.getElementById('pairSamples').getRows().length") == 2

    page.evaluate(
        """() => {
            RUN_ROWS = [
                {
                    NAME: "runA", files: ["runA_R1.fastq.gz", "runA_R2.fastq.gz"], paired: true,
                    FASTQ1: "runA_R1.fastq.gz", FASTQ2: "runA_R2.fastq.gz", FASTQ: "",
                    SAMPLE: "", STUDY: "", confidence: "none"
                },
                {
                    NAME: "runB", files: ["runB.fastq.gz"], paired: false,
                    FASTQ1: "", FASTQ2: "", FASTQ: "runB.fastq.gz",
                    SAMPLE: "", STUDY: "", confidence: "none"
                }
            ];
            renderRunTable();
        }"""
    )

    # Selecting through the element's API and by a real click must both reach
    # SELECTED_SAMPLE — it is the only thing the run-row click reads.
    page.evaluate("() => document.getElementById('pairSamples').setSelection(['ERS222'])")
    assert page.evaluate("() => SELECTED_SAMPLE") == "ERS222"
    page.locator("#pairSamples .ht_master td", has_text="ERS111").first.click()
    page.wait_for_function("() => SELECTED_SAMPLE === 'ERS111'")

    page.click("#runTable tbody tr:first-child td.wrap")
    first_sample = page.locator("#runTable tbody tr").nth(0).locator("input").nth(1)
    assert first_sample.input_value() == "ERS111"
    assert _assigned_count(page, "ERS111") == "2"

    page.click("#runTable tbody tr:first-child .icon-btn")
    assert page.locator("#runTable tbody tr").count() == 1
    assert _assigned_count(page, "ERS111") == "0"


def test_reads_pairing_selection_survives_a_filter(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    _load_pairing_samples(page)

    page.evaluate("() => document.getElementById('pairSamples').setSelection(['ERS111'])")
    page.evaluate(
        """() => document.getElementById('pairSamples')
            .setFilters([{ column: 'accession', operator: 'eq', value: 'ERS111' }])"""
    )
    assert page.evaluate("() => document.getElementById('pairSamples').getVisibleRows().length") == 1
    assert page.evaluate("() => document.getElementById('pairSamples').getSelection()") == ["ERS111"]
    assert page.evaluate("() => SELECTED_SAMPLE") == "ERS111"


def _fetch_records(page, entity):
    """Fetch one entity into the grid and wait for the rows to land."""
    page.select_option("#recEntity", entity)
    page.click("button:has-text('Fetch')")
    page.wait_for_function(
        "() => document.getElementById('recGrid').getRows().length > 0",
    )
    return page.evaluate("() => document.getElementById('recGrid').getRows()")


def test_records_runs_and_experiments_views(page):
    """The grid is fed the rows the API returned, linking accessions included.

    Asserted through the element's public API — the grid's own rendering,
    filtering and sorting are ena-browser's Playwright suite, not this one's.
    """
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.click("a.vf-tabs__link:has-text('Records')")

    rows = _fetch_records(page, "runs")
    assert rows[0]["experiment_accession"] == "ERX111"
    assert rows[0]["study_accession"] == "ERP111"
    assert rows[0]["sample_accession"] == "ERS111"

    rows = _fetch_records(page, "experiments")
    assert rows[0]["accession"] == "ERX111"
    assert rows[0]["sample_accession"] == "ERS111"


def test_records_criteria_reach_the_request(page):
    """The fetch criteria are request criteria — they go to list_records."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.click("a.vf-tabs__link:has-text('Records')")

    page.fill("#recSearch", "MIMICC")
    page.fill("#recLinked", "PRJEB1234")
    page.check("#recUnlinked")
    page.check("#recFullFields")
    _fetch_records(page, "samples")

    kwargs = _py_calls(page, "ena_service.list_records")[-1]["kwargs"]
    assert (kwargs["entity"], kwargs["search"], kwargs["linked_to"]) == ("samples", "MIMICC", "PRJEB1234")
    assert kwargs["unlinked"] is True and kwargs["full_fields"] is True


def test_records_fetch_needs_credentials(page):
    page.evaluate("() => { CREDS = { username: '', password: '' }; }")
    page.click("a.vf-tabs__link:has-text('Records')")
    page.click("button:has-text('Fetch')")
    page.wait_for_function("() => document.getElementById('recLog').textContent.includes('Credentials not set')")
    assert _py_calls(page, "ena_service.list_records") == []


def _enable_write(page):
    """Tick write mode. It confirms first (edits go to ENA), so accept that."""
    page.on("dialog", lambda dialog: dialog.accept())
    page.check("#recWrite")


def test_records_row_action_posts_accession(page):
    """A row-action button the element renders reaches ena_service.run_action."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    _stub_py(page, {"ena_service.run_action": {"success": True, "messages": "released"}})
    page.click("a.vf-tabs__link:has-text('Records')")
    _enable_write(page)  # row actions only exist in write mode
    _fetch_records(page, "samples")

    # The frozen-column clone is the copy on top; the master one under it is
    # covered by design. Clicking it is also the regression test for the page
    # scroll pinning in core.js — without it the grid slides out from under the
    # cursor between mousedown and mouseup and the click never lands.
    page.locator("ena-browser#recGrid .ht_clone_inline_start button:has-text('Release')").first.click()
    page.wait_for_timeout(300)

    posted = _py_calls(page, "ena_service.run_action")
    assert posted, "no lifecycle action was run"
    assert posted[0]["kwargs"]["action"] == "release"
    assert posted[0]["kwargs"]["accession"] == "ERS111"


def _edit_title(page, current, text):
    """Type into a grid cell — also the regression test for the narrowed
    keyboard swallower (core.js): without it Handsontable gets no keys at all."""
    page.locator("ena-browser#recGrid td", has_text=current).first.dblclick()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type(text)
    page.keyboard.press("Enter")
    page.wait_for_function(
        "() => document.getElementById('recGrid').getChangeSet().rows.length > 0",
    )


def test_records_edit_lands_in_the_change_set(page):
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.click("a.vf-tabs__link:has-text('Records')")
    _enable_write(page)
    _fetch_records(page, "samples")

    _edit_title(page, "Sample A1", "Edited A1")
    changes = page.evaluate("() => pendingChanges()")
    assert changes[0]["accession"] == "ERS111"
    assert changes[0]["changes"]["title"] == "Edited A1"


def test_records_manifest_gate(page):
    """Submit stays locked until the manifests for the current edits have been
    built, and re-locks as soon as anything is edited again."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    _stub_py(
        page,
        {
            "ena_service.preview_modify_records": {
                "success": True,
                "results": [
                    {
                        "accession": "ERS111",
                        "success": True,
                        "xml": "<SAMPLE_SET/>",
                        "changes": {"title": "Edited A1"},
                        "messages": [],
                    }
                ],
            }
        },
    )
    page.click("a.vf-tabs__link:has-text('Records')")
    _enable_write(page)
    _fetch_records(page, "samples")

    _edit_title(page, "Sample A1", "Edited A1")
    assert page.is_disabled("#recSubmit"), "staged edits alone must not unlock submit"
    assert not page.is_disabled("#recGenerate")

    page.click("#recGenerate")
    page.wait_for_function("() => !document.getElementById('recSubmit').disabled")
    assert "manifest(s) built" in page.inner_text("#recManifestState")

    _edit_title(page, "Edited A1", "Edited again")
    assert page.is_disabled("#recSubmit"), "a further edit must re-lock submit"


def test_records_grid_layout_survives_a_reload(page):
    """The workspace stores the grid's arrangement, never its rows: a reload
    returns the layout and filters, and re-fetches the records."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.click("a.vf-tabs__link:has-text('Records')")
    _fetch_records(page, "samples")

    page.evaluate(
        """() => {
            const grid = document.getElementById('recGrid');
            grid.setLayout({ ...grid.getLayout(), hidden: ['alias'], pinned: ['title'] });
            grid.setFilters([{ column: 'status', operator: 'eq', value: 'PRIVATE' }]);
        }"""
    )
    page.evaluate("() => saveWorkspaceNow()")

    page.reload()
    _wait_for_workspace(page)
    _stub_py(page, {})
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; loadRecords(); }")
    page.wait_for_function("() => document.getElementById('recGrid').getRows().length > 0")
    layout = page.evaluate("() => document.getElementById('recGrid').getLayout()")
    assert layout["hidden"] == ["alias"]
    assert layout["pinned"] == ["title"]
    assert page.evaluate("() => document.getElementById('recGrid').getFilters()")[0]["column"] == "status"


def test_clear_workspace_starts_blank(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.fill("#readsPrefix", "batch-1")
    page.uncheck("#expDhAutoSync")
    page.evaluate("() => saveWorkspaceNow()")

    page.on("dialog", lambda d: d.accept())
    with page.expect_navigation():
        page.click("#workspaceChip button:has-text('Clear')")
    _wait_for_workspace(page)
    assert page.input_value("#readsPrefix") == ""
    assert page.is_checked("#expDhAutoSync")


def test_old_session_is_adopted_with_its_name_as_the_reads_prefix(page):
    """A session saved before sessions were removed becomes the workspace, and
    its name keeps the run aliases it submitted under — so resume still works."""
    page.evaluate(
        """async () => {
            const db = await idbOpen();
            const tx = db.transaction(DB_STORE, 'readwrite');
            tx.objectStore(DB_STORE).delete('workspace');
            tx.objectStore(DB_STORE).put({ id: 'old1', name: 'older', updated_at: '2026-01-01', state: { test: true, fields: {} } });
            tx.objectStore(DB_STORE).put({ id: 'old2', name: 'run A', updated_at: '2026-06-01',
                                           state: { test: true, fields: {} }, reads_runs: { r1: { status: 'done' } } });
            await new Promise((r) => { tx.oncomplete = r; });
        }"""
    )
    page.reload()
    _wait_for_workspace(page)
    assert page.input_value("#readsPrefix") == "run A"
    assert page.evaluate("() => READS_RUNS.r1.status") == "done"


def test_session_state_holds_no_grid_rows(page):
    """Row data is never persisted — a saved status is a stale status."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.click("a.vf-tabs__link:has-text('Records')")
    _fetch_records(page, "samples")

    state = page.evaluate("() => collectState()")
    assert state["v"] == 2
    assert "recOut" not in state["resultsHtml"]
    assert set(state["grids"]["records"]) == {"layout", "filters", "entity"}
    # The debug log deliberately keeps the first raw row; nothing else may.
    assert "ERS111" not in json.dumps({k: v for k, v in state.items() if k != "logs"})


def test_studies_grid_confirms_only_this_submission(page):
    """After a submit, the grid shows what ENA holds — filtered to the
    accessions this submission produced, and read-only."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    _stub_py(
        page,
        {
            "ena_service.submit_studies": {
                "success": True,
                "logs": ["INFO: done"],
                "accessions": [{"alias": "studyA", "accession": "ERP111"}],
            }
        },
    )
    page.click("a.vf-tabs__link:has-text('Studies')")
    page.evaluate("() => { window.__preparedStudies = [{ alias: 'studyA' }]; }")
    page.click("button:has-text('Submit prepared studies')")

    page.wait_for_function("() => document.getElementById('studyGrid').getRows().length > 0")
    assert page.evaluate("() => document.getElementById('studyGrid').getRows().length") == 2
    visible = page.evaluate("() => document.getElementById('studyGrid').getVisibleRows()")
    assert [row["accession"] for row in visible] == ["ERP111"]
    assert page.get_attribute("#studyGrid", "mode") == "read"
    assert page.evaluate("() => document.getElementById('studyGridEmpty').style.display") == "none"


def test_samples_grid_confirms_only_this_submission(page):
    """The Phase-5 study check, for samples — the three grids are parallel."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    _stub_py(
        page,
        {
            "ena_service.submit_samples": {
                "success": True,
                "logs": ["INFO: done"],
                "accessions": [{"alias": "MIMICC_A_1", "accession": "ERS111"}],
            }
        },
    )
    page.click("a.vf-tabs__link:has-text('Samples')")
    page.evaluate(
        """() => {
            window.__prepared = [{ alias: 'MIMICC_A_1' }];
            document.getElementById('sampleSubmitBtn').disabled = false;
        }"""
    )
    page.click("#sampleSubmitBtn")

    page.wait_for_function("() => document.getElementById('sampleGrid').getRows().length > 0")
    assert page.evaluate("() => document.getElementById('sampleGrid').getRows().length") == 2
    visible = page.evaluate("() => document.getElementById('sampleGrid').getVisibleRows()")
    assert [row["accession"] for row in visible] == ["ERS111"]
    assert page.get_attribute("#sampleGrid", "mode") == "read"
    (call,) = _py_calls(page, "ena_service.submit_samples")
    assert set(call["files"]) == {
        "/assets/ena_schema/SRA.sample.xsd",
        "/assets/ena_schema/SRA.common.xsd",
        "/schemas/mimicc_sample.yaml",
    }


def test_reads_grid_confirms_submitted_runs(page):
    """The reads confirmation grid is limited to this workspace's runs and shows
    ENA's archiving state, which "submitted" does not imply."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.evaluate(
        """() => { READS_RUNS = { runA: { run_name: 'runA', status: 'done',
                                          run_accession: 'ERR111',
                                          experiment_accession: 'ERX111' } }; }"""
    )
    page.click("#vf-tabs__section--reads button:has-text('Refresh from ENA')")

    page.wait_for_function("() => document.getElementById('readsGrid').getRows().length > 0")
    assert page.evaluate("() => document.getElementById('readsGrid').getRows().length") == 2
    visible = page.evaluate("() => document.getElementById('readsGrid').getVisibleRows()")
    assert [row["accession"] for row in visible] == ["ERR111"]
    assert visible[0]["process_status"] == "COMPLETED"
    headers = page.eval_on_selector_all("#readsGrid th", "els => els.map((e) => e.innerText)")
    assert any("rocess status" in h for h in headers)


def test_confirmation_grids_explain_when_nothing_was_submitted(page):
    """Refreshing before a submission must not leave a header-only grid."""
    for tab, grid, empty in (
        ("Studies", "studyGrid", "studyGridEmpty"),
        ("Samples", "sampleGrid", "sampleGridEmpty"),
        ("Reads", "readsGrid", "readsGridEmpty"),
    ):
        page.click(f"a.vf-tabs__link:has-text('{tab}')")
        page.locator(f"#vf-tabs__section--{tab.lower()} button:has-text('Refresh from ENA')").click()
        assert page.evaluate(f"() => document.getElementById('{grid}').style.display") == "none"
        assert "submitted from this workspace" in page.locator(f"#{empty}").inner_text()


def _inject_fake_experiment_dh(page, rows):
    """Stand in for a loaded experiment DataHarmonizer grid: the real second
    template isn't built in this (non-Docker) test environment, but the merge
    logic only ever talks to window.dataHarmonizer.getExportJson(), so a
    minimal fake covering that one call is enough to test it."""
    page.evaluate(
        """(rows) => {
            const frame = document.getElementById('expDhFrame');
            frame.contentWindow.dataHarmonizer = {
                ready: true,
                getExportJson: () => ({ Container: { MIMICC_Experiment: rows } }),
            };
        }""",
        rows,
    )


def test_reads_submit_merges_experiment_metadata(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.evaluate(
        """() => {
            RUN_ROWS = [
                {
                    NAME: "runA", files: ["runA_R1.fastq.gz", "runA_R2.fastq.gz"], paired: true,
                    FASTQ1: "runA_R1.fastq.gz", FASTQ2: "runA_R2.fastq.gz", FASTQ: "",
                    SAMPLE: "ERS111", STUDY: "ERP111", confidence: "manual"
                }
            ];
            renderRunTable();
        }"""
    )
    _inject_fake_experiment_dh(
        page,
        [
            {
                "Experiment name": "runA",
                "Sample alias": "ERS111",
                "Platform": "ILLUMINA",
                "Instrument": "Illumina MiSeq",
                "Library source": "METAGENOMIC",
                "Library selection": "PCR",
                "Library strategy": "AMPLICON",
            }
        ],
    )

    # An empty plan, so submitReads() finishes without needing the local helper
    # (which isn't running in this test).
    _stub_py(page, {"ena_service.plan_reads": {"plan": [], "warnings": []}})
    # Pretend the local upload helper is running + a reads dir is set, so the
    # flow proceeds to build the plan.
    page.evaluate(
        """() => { HELPER_OK = true; CREDS = { username: 'Webin-test', password: 'secret' };
                   document.getElementById('readsLocalDir').value = '/tmp/reads'; }"""
    )
    page.evaluate("() => submitReads(true)")
    page.wait_for_timeout(500)

    calls = _py_calls(page, "ena_service.plan_reads")
    assert calls, "submitReads() never built a plan"
    body = calls[0]["kwargs"]
    assert body["creds"] == {"username": "Webin-test", "password": "secret"}
    run = body["runs"][0]
    assert run["NAME"] == "runA"
    assert run["SAMPLE"] == "ERS111"
    assert run["STUDY"] == "ERP111"
    assert run["PLATFORM"] == "ILLUMINA"
    assert run["INSTRUMENT"] == "Illumina MiSeq"
    assert run["LIBRARY_SOURCE"] == "METAGENOMIC"
    assert run["LIBRARY_SELECTION"] == "PCR"
    assert run["LIBRARY_STRATEGY"] == "AMPLICON"
    assert run["FASTQ1"] == "runA_R1.fastq.gz" and run["FASTQ2"] == "runA_R2.fastq.gz"
    # A blank submission prefix is filled in once and kept, so a re-run resumes.
    prefix = page.input_value("#readsPrefix")
    assert prefix.startswith("sub-")
    assert body["prefix"] == prefix


def test_reads_plan_uses_the_submission_prefix(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.fill("#readsPrefix", "batch-7")
    _generate_manual_script(page, [_MANUAL_SUBMIT_ENTRY])
    assert _py_calls(page, "ena_service.plan_reads")[0]["kwargs"]["prefix"] == "batch-7"


def test_reads_plan_needs_credentials(page):
    page.evaluate("() => { CREDS = { username: '', password: '' }; }")
    _stub_py(page, {"ena_service.plan_reads": {"plan": [], "warnings": []}})
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.select_option("#readsMode", "manual")
    page.evaluate(
        """() => { RUN_ROWS = [{ NAME: 'runA', SAMPLE: 'ERS1', STUDY: 'ERP1', paired: false, FASTQ: 'a.fq.gz' }]; }"""
    )
    _inject_fake_experiment_dh(page, [{"Experiment name": "runA", "Sample alias": "ERS1"}])
    page.evaluate("() => generateReadsScript(true)")
    page.wait_for_function(
        "() => document.getElementById('submitReadsBanner').innerText.includes('Credentials not set')"
    )
    assert _py_calls(page, "ena_service.plan_reads") == []


# ---------------------------------------------------------------------------
# Manual (no-helper) reads mode
# ---------------------------------------------------------------------------


def test_reads_mode_toggle_swaps_helper_and_manual_controls(page):
    page.click("a.vf-tabs__link:has-text('Reads')")

    page.select_option("#readsMode", "helper")
    assert page.is_visible("#scanReadsBtn")
    assert page.is_visible("#readsSubmitBtn")
    assert not page.is_visible("#readsDirPickLabel")
    assert not page.is_visible("#readsScriptBtn")

    page.select_option("#readsMode", "manual")
    assert not page.is_visible("#scanReadsBtn")
    assert not page.is_visible("#readsSubmitBtn")
    assert page.is_visible("#readsDirPickLabel")
    assert page.is_visible("#readsScriptBtn")
    # The helper's "not running" warning must not nag in a mode that never uses it.
    assert not page.is_visible("#helperMissing")


def test_reads_manual_directory_picker_lists_runs(page, tmp_path):
    """The picker hands Python file names only; the pairing is
    read_assign.group_files, so it matches what the helper's own scan produces."""
    for name in ("runA_R1.fastq.gz", "runA_R2.fastq.gz", "README.md"):
        (tmp_path / name).write_text("x")

    page.click("a.vf-tabs__link:has-text('Reads')")
    page.select_option("#readsMode", "manual")
    _stub_py(
        page,
        {
            "read_assign.group_files": [
                {
                    "group": "runA",
                    "files": ["runA_R1.fastq.gz", "runA_R2.fastq.gz"],
                    "paired": True,
                    "files_by_mate": {"1": "runA_R1.fastq.gz", "2": "runA_R2.fastq.gz"},
                }
            ]
        },
    )
    # A webkitdirectory input takes a directory path, not file payloads.
    page.set_input_files("#readsDirInput", str(tmp_path))
    page.wait_for_function("() => RUN_ROWS.length > 0")
    (call,) = _py_calls(page, "read_assign.group_files")
    assert sorted(call["kwargs"]["names"]) == ["README.md", "runA_R1.fastq.gz", "runA_R2.fastq.gz"]

    assert page.evaluate("() => RUN_ROWS.map(r => r.NAME)") == ["runA"]
    assert page.evaluate("() => RUN_ROWS[0].paired") is True
    assert "README" not in page.inner_text("#runTable")


_MANUAL_MANIFEST = "STUDY\tERP1\nNAME\tsess_runA\nDESCRIPTION\t$(whoami) 'quoted'\n"
_MANUAL_SUBMIT_ENTRY = {
    "name": "runA",
    "action": "submit",
    "alias": "sess_runA",
    "stable_alias": "sess_runA",
    "manifest_filename": "sess_runA.manifest",
    "manifest_text": _MANUAL_MANIFEST,
    "sample": "ERS111",
    "study": "ERP1",
}


def _generate_manual_script(page, plan, do_submit=True, test_env=True):
    """Drive manual mode to a rendered command, with the plan stubbed."""
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.select_option("#readsMode", "manual")
    page.evaluate(
        """() => {
            RUN_ROWS = [
                { NAME: "runA", files: ["runA_R1.fastq.gz", "runA_R2.fastq.gz"], paired: true,
                  FASTQ1: "runA_R1.fastq.gz", FASTQ2: "runA_R2.fastq.gz", FASTQ: "",
                  SAMPLE: "ERS111", STUDY: "ERP1", confidence: "manual" }
            ];
            renderRunTable();
            document.getElementById("readsLocalDir").value = "/Users/me/my reads";
        }"""
    )
    _inject_fake_experiment_dh(
        page,
        [
            {
                "Experiment name": "runA",
                "Sample alias": "ERS111",
                "Platform": "ILLUMINA",
                "Instrument": "Illumina MiSeq",
                "Library source": "METAGENOMIC",
                "Library selection": "PCR",
                "Library strategy": "AMPLICON",
            }
        ],
    )
    _stub_py(page, {"ena_service.plan_reads": {"plan": plan, "warnings": []}})
    page.evaluate("() => { if (!CREDS.username) CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.evaluate(f"() => {{ TEST = {json.dumps(test_env)}; }}")
    page.evaluate(f"() => generateReadsScript({json.dumps(do_submit)})")
    page.wait_for_timeout(500)
    return page.text_content("#readsScript")


def test_reads_manual_script_carries_manifest_and_flags(page):
    page.evaluate("() => { CREDS = { username: 'Webin-secret', password: 'hunter2' }; }")
    script = _generate_manual_script(page, [_MANUAL_SUBMIT_ENTRY], do_submit=True, test_env=True)

    assert page.is_visible("#readsScriptWrap")
    # The manifest survives the heredoc verbatim — tabs, $(...) and quotes alike.
    assert _MANUAL_MANIFEST.strip() in script
    assert "<<'MANIFEST_EOF'" in script
    # The reads dir is quoted, not interpolated bare (it has a space in it).
    assert "READS_DIR='/Users/me/my reads'" in script
    assert "-submit" in script and "-validate" not in script
    assert "-test" in script
    # Credentials are referenced, never written in.
    assert "Webin-secret" not in script and "hunter2" not in script
    assert '-userName="$WEBIN_USERNAME"' in script


def test_reads_manual_script_validate_only_and_production(page):
    script = _generate_manual_script(page, [_MANUAL_SUBMIT_ENTRY], do_submit=False, test_env=False)
    assert "-validate" in script and "-submit" not in script
    # -test against production would silently submit to the wrong service.
    assert "-test" not in script


def test_reads_manual_mode_never_calls_the_helper(page):
    helper_calls = []
    page.evaluate("() => { HELPER_OK = false; HELPER_BASE = 'http://127.0.0.1:9/helper'; }")
    page.route(
        "http://127.0.0.1:9/**",
        lambda route: (helper_calls.append(route.request.url), route.abort())[-1],
    )
    script = _generate_manual_script(page, [_MANUAL_SUBMIT_ENTRY])

    assert script, "manual mode produced no command"
    assert helper_calls == []


def test_reads_manual_skips_feed_the_ledger(page):
    """Manual mode's results come back through the plan, not a relayed log: a run
    already in ENA returns as a skip carrying its accessions."""
    skip = {
        "name": "runA",
        "action": "skip",
        "reason": "already_in_ena",
        "skipped": True,
        "success": True,
        "alias": "sess_runA",
        "sample": "ERS111",
        "study": "ERP1",
        "exit_code": 0,
        "experiment_accession": "ERX999",
        "run_accession": "ERR999",
    }
    script = _generate_manual_script(page, [skip])

    assert script == ""  # nothing left to run
    assert not page.is_visible("#readsScriptWrap")
    assert page.evaluate("() => READS_RUNS['runA'].status") == "already_in_ena"
    assert page.evaluate("() => READS_RUNS['runA'].run_accession") == "ERR999"
    assert "in ENA" in page.inner_text("#runTable")


def test_reads_submit_blocks_without_matching_experiment_row(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.evaluate(
        """() => {
            RUN_ROWS = [
                { NAME: "runB", files: ["runB.fastq.gz"], paired: false,
                  FASTQ1: "", FASTQ2: "", FASTQ: "runB.fastq.gz",
                  SAMPLE: "ERS222", STUDY: "ERP111", confidence: "manual" }
            ];
            renderRunTable();
        }"""
    )
    # Experiment grid is "loaded" but has no row for runB.
    _inject_fake_experiment_dh(page, [])

    _stub_py(page, {})
    page.evaluate("() => submitReads(true)")
    page.wait_for_timeout(300)

    assert _py_calls(page, "ena_service.plan_reads") == []
    assert "No experiment metadata row found" in page.inner_text("#submitReadsBanner")


def _inject_recording_experiment_dh(page, batched=True):
    """A fake experiment grid that records the upsert calls the sync makes, so
    it can be asserted on those rather than on grid contents. ``batched=False``
    stands in for a DataHarmonizer bundle predating upsertRows."""
    page.evaluate(
        """(batched) => {
            const frame = document.getElementById('expDhFrame');
            window.__upserts = [];
            window.__batches = [];
            const dh = {
                ready: true,
                getExportJson: () => ({ Container: { MIMICC_Experiment: [] } }),
                upsertRow: (keyCol, key, patch) => window.__upserts.push([key, patch]),
            };
            if (batched) {
                dh.upsertRows = (keyCol, entries) => {
                    window.__batches.push(entries);
                    entries.forEach((e) => window.__upserts.push([e.key, e.values]));
                };
            }
            frame.contentWindow.dataHarmonizer = dh;
            EXP_SYNCED.clear();
        }""",
        batched,
    )


def test_experiment_sync_sends_one_batched_upsert(page):
    """Every changed row goes over in a single upsertRows call — the batched
    form does one render/validation pass instead of one per row."""
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.evaluate(
        """() => {
            RUN_ROWS = [
                { NAME: "r1", files: [], paired: false, SAMPLE: "ERS1", STUDY: "" },
                { NAME: "r2", files: [], paired: false, SAMPLE: "ERS2", STUDY: "" },
                { NAME: "r3", files: [], paired: false, SAMPLE: "ERS3", STUDY: "" },
            ];
        }"""
    )
    _inject_recording_experiment_dh(page)
    page.evaluate("() => syncPairingsToExperimentDhNow()")

    batches = page.evaluate("() => window.__batches")
    assert len(batches) == 1
    assert [e["key"] for e in batches[0]] == ["r1", "r2", "r3"]
    assert batches[0][0]["values"] == {"Sample alias": "ERS1"}


def test_experiment_sync_falls_back_to_per_row_upsert(page):
    """An older bundle without upsertRows must still sync, one row at a time."""
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.evaluate(
        """() => {
            RUN_ROWS = [
                { NAME: "r1", files: [], paired: false, SAMPLE: "ERS1", STUDY: "" },
                { NAME: "r2", files: [], paired: false, SAMPLE: "ERS2", STUDY: "" },
            ];
        }"""
    )
    _inject_recording_experiment_dh(page, batched=False)
    page.evaluate("() => syncPairingsToExperimentDhNow()")

    assert page.evaluate("() => window.__batches") == []
    assert page.evaluate("() => window.__upserts.map((u) => u[0])") == ["r1", "r2"]


def test_experiment_sync_only_pushes_changed_pairings(page):
    """Re-pushing every row on every edit is what made the grid crawl."""
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.evaluate(
        """() => {
            RUN_ROWS = [
                { NAME: "r1", files: [], paired: false, SAMPLE: "ERS1", STUDY: "ERP1" },
                { NAME: "r2", files: [], paired: false, SAMPLE: "ERS2", STUDY: "ERP1" },
            ];
        }"""
    )
    _inject_recording_experiment_dh(page)

    page.evaluate("() => syncPairingsToExperimentDhNow()")
    assert page.evaluate("() => window.__upserts.map((u) => u[0])") == ["r1", "r2"]

    # Nothing changed — no work at all.
    page.evaluate("() => syncPairingsToExperimentDhNow()")
    assert page.evaluate("() => window.__upserts.length") == 2

    # One pairing changed — exactly one push.
    page.evaluate("() => { RUN_ROWS[1].SAMPLE = 'ERS9'; syncPairingsToExperimentDhNow(); }")
    assert page.evaluate("() => window.__upserts.slice(2)") == [["r2", {"Sample alias": "ERS9"}]]


def test_experiment_sync_toggle_and_update_button(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.evaluate("""() => { RUN_ROWS = [{ NAME: "r1", files: [], paired: false, SAMPLE: "ERS1", STUDY: "" }]; }""")
    _inject_recording_experiment_dh(page)

    page.uncheck("#expDhAutoSync")
    page.evaluate("() => syncPairingsToExperimentDh()")
    page.wait_for_timeout(300)
    assert page.evaluate("() => window.__upserts.length") == 0

    page.click("#expDhUpdateBtn")
    assert page.evaluate("() => window.__upserts") == [["r1", {"Sample alias": "ERS1"}]]

    page.check("#expDhAutoSync")
    page.evaluate("() => { RUN_ROWS[0].SAMPLE = 'ERS2'; syncPairingsToExperimentDh(); }")
    page.wait_for_function("() => window.__upserts.length === 2")


def test_experiment_auto_sync_toggle_persists_in_the_workspace(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.uncheck("#expDhAutoSync")
    page.evaluate("() => saveWorkspaceNow()")

    page.reload()
    _wait_for_workspace(page)
    assert not page.is_checked("#expDhAutoSync")


def test_ena_browser_element_registered(page, live_server_url):
    """The vendored bundle is served and defines the custom element."""
    resp = page.request.get(f"{live_server_url}/static/vendor/ena-browser/ena-browser.iife.js")
    assert resp.status == 200
    assert page.evaluate("() => !!window.customElements.get('ena-browser')")


# ---------------------------------------------------------------------------
# Schema library + grid schemas (browser-side) — STATIC_BROWSER_PLAN.md Phase 5
# ---------------------------------------------------------------------------

_REGISTRY = {"mimicc": "MIMICC_Sample", "mimicc_experiment": "MIMICC_Experiment", "study": "SRA_study"}
# (role, dropdown, folder) — the three grids are parallel.
_GRIDS = [
    ("sample", "sampleSchemaSelect", "mimicc"),
    ("experiment", "expSchemaSelect", "mimicc_experiment"),
    ("study", "studySchemaSelect", "study"),
]


def _compiled(role, folder, marker):
    return {
        "role": role,
        "folder": folder,
        "template_name": _REGISTRY[folder],
        "template": f"{folder}/{_REGISTRY[folder]}",
        "schema_json": {"name": marker, "classes": {_REGISTRY[folder]: {"name": _REGISTRY[folder]}}},
        "diagnostics": [],
    }


def _prepare_grid_selection(page, role, folder, marker="custom"):
    """No DataHarmonizer bundle here: serve its registry, skip waiting for the
    grid to render, and stub the compile."""
    page.route(
        "**/dh/dh-template-registry.json*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(_REGISTRY)),
    )
    page.evaluate("() => { waitForDhFrameReady = async () => {}; }")
    _stub_py(page, {"schema_service.compile_for_grid": _compiled(role, folder, marker)})


def _served_schema(page, folder):
    """What DataHarmonizer would get for the grid — through the service worker."""
    return page.evaluate(
        """async (folder) => {
            await templateWorkerReady();
            const res = await fetch(`/templates/${folder}/schema.json`);
            return { status: res.status, schemaId: res.headers.get('x-schema-id'),
                     body: res.ok ? await res.text() : null };
        }""",
        folder,
    )


def test_schema_library_seeds_from_the_bundled_index_without_python(page):
    page.click("a.vf-tabs__link:has-text('Schema')")
    page.wait_for_selector("#sampleSchemaSelect option[value='mimicc_sample']", state="attached")
    ids = page.evaluate("async () => (await listLibrarySchemas()).map((s) => s.id)")
    assert {"mimicc_sample", "mimicc_experiment", "sra_study"} <= set(ids)
    assert page.evaluate("async () => (await readLibrarySchema('sra_study')).yaml").startswith(("id:", "name:", "#"))
    assert page.evaluate("() => window.__pyCalls.length") == 0


@pytest.mark.parametrize(("role", "select_id", "folder"), _GRIDS)
def test_selected_grid_schema_survives_a_reload_without_recompiling(page, role, select_id, folder):
    _prepare_grid_selection(page, role, folder)
    page.evaluate("([role]) => selectSchemaById(role, 'mimicc_sample')", [role])

    (call,) = _py_calls(page, "schema_service.compile_for_grid")
    assert call["kwargs"]["role"] == role
    assert "MIMICC" in call["kwargs"]["yaml_text"] or "mimicc" in call["kwargs"]["yaml_text"]
    served = _served_schema(page, folder)
    assert served["schemaId"] == "mimicc_sample" and '"custom"' in served["body"]
    assert page.evaluate("async () => await dbGetGridSchemas()") == {role: "mimicc_sample"}

    page.reload()
    _wait_for_workspace(page)
    # Nothing was stale, so nothing was recompiled — no Python on a returning load.
    assert page.evaluate("async () => await window.GRID_SCHEMAS_RESTORED") == []
    assert '"custom"' in _served_schema(page, folder)["body"]


@pytest.mark.parametrize(("role", "select_id", "folder"), _GRIDS)
def test_deleting_the_selected_schema_reverts_the_grid_to_its_default(page, role, select_id, folder):
    _prepare_grid_selection(page, role, folder)
    page.evaluate("([role]) => selectSchemaById(role, 'erc000025')", [role])
    assert _served_schema(page, folder)["schemaId"] == "erc000025"

    page.on("dialog", lambda dialog: dialog.accept())
    page.evaluate("() => deleteSchemaFromLibrary('erc000025')")

    # Falls through to the bundle, which this environment does not have.
    assert _served_schema(page, folder)["schemaId"] is None
    assert page.evaluate("async () => await dbGetGridSchemas()") == {}
    assert "erc000025" not in page.evaluate("async () => (await listLibrarySchemas()).map((s) => s.id)")


def test_stale_grid_schema_is_recompiled_on_restore(page):
    _prepare_grid_selection(page, "sample", "mimicc", marker="old compiler")
    page.evaluate("() => selectSchemaById('sample', 'mimicc_sample')")
    # Re-tag the cached entry as made by another compiler version.
    page.evaluate(
        """async () => {
            const cache = await caches.open('dh-templates');
            const old = await cache.match('/templates/mimicc/schema.json');
            const headers = new Headers(old.headers);
            headers.set('x-compiler-version', 'linkml-runtime==0.0.1');
            await cache.put('/templates/mimicc/schema.json', new Response(await old.text(), { headers }));
        }"""
    )
    _stub_py(page, {"schema_service.compile_for_grid": _compiled("sample", "mimicc", "new compiler")})

    assert page.evaluate("async () => await restoreGridSchemas()") == ["sample"]
    assert '"new compiler"' in _served_schema(page, "mimicc")["body"]
    # Current again: a second restore leaves it alone.
    assert page.evaluate("async () => await restoreGridSchemas()") == []


def test_restore_drops_a_cached_schema_the_workspace_no_longer_selects(page):
    _prepare_grid_selection(page, "study", "study")
    page.evaluate("() => selectSchemaById('study', 'sra_study')")
    page.evaluate("async () => { await dbSetGridSchema('study', null); }")  # as Clear leaves it

    assert page.evaluate("async () => await restoreGridSchemas()") == ["study"]
    assert _served_schema(page, "study")["schemaId"] is None


def test_saving_a_schema_names_it_in_python_and_stores_it(page):
    yaml_text = "name: my_schema\nid: https://example.org/my_schema\nclasses: {}\n"
    _stub_py(
        page,
        {
            "schema_service.describe_schema": {
                "id": "my-schema",
                "name": "my_schema",
                "title": "my_schema",
                "description": None,
            }
        },
    )
    schema_id = page.evaluate("([name, text]) => saveLibrarySchema(name, text)", ["My Schema", yaml_text])
    assert schema_id == "my-schema"
    assert page.evaluate("async () => (await readLibrarySchema('my-schema')).yaml") == yaml_text
    (call,) = _py_calls(page, "schema_service.describe_schema")
    assert call["kwargs"] == {"yaml_text": yaml_text, "name": "My Schema"}


def test_saving_a_schema_makes_it_available_in_every_schema_picker(page):
    yaml_text = "name: immediate_schema\nid: https://example.org/immediate_schema\nclasses: {}\n"
    _stub_py(
        page,
        {
            "schema_service.describe_schema": {
                "id": "immediate-schema",
                "name": "immediate_schema",
                "title": "Immediate schema",
                "description": None,
            }
        },
    )
    page.evaluate("async () => { await refreshSchemaList(); $('schemaSaveName').value = 'Immediate schema'; }")

    # saveExportedSchema resolves only after the library has been republished
    # to the selectors that live in the Samples, Reads, Studies, and Schema tabs.
    page.evaluate("async (yaml) => await saveExportedSchema(yaml)", yaml_text)

    for select_id in ("sampleSchemaSelect", "expSchemaSelect", "studySchemaSelect", "schemaImportExisting"):
        assert page.locator(f"#{select_id} option[value='immediate-schema']").count() == 1
    assert 'Saved as "immediate-schema".' in page.inner_text("#schemaEditorBanner")


def test_building_a_schema_sends_sources_and_library_schemas_as_files(page):
    _stub_py(page, {"schema_service.import_build": "name: merged\n"})
    page.evaluate("() => { loadSchemaIntoEditor = (yaml) => { window.__editorYaml = yaml; }; }")
    page.click("a.vf-tabs__link:has-text('Schema')")
    page.wait_for_selector("#schemaImportChecklists option[value='ERC000025.xml']", state="attached")
    page.wait_for_selector("#schemaImportExisting option[value='sra_study']", state="attached")
    page.select_option("#schemaImportChecklists", "ERC000025.xml")
    page.select_option("#schemaImportExisting", "sra_study")
    page.evaluate("() => buildImportedSchema()")

    (call,) = _py_calls(page, "schema_service.import_build")
    assert call["kwargs"]["source_ids"] == ["ERC000025.xml"]
    assert call["kwargs"]["upload_paths"] == ["/tmp/library/sra_study.yaml"]
    assert call["files"]["/assets/ena_schema/ERC000025.xml"] == "/assets/ena_schema/ERC000025.xml"
    assert "/assets/ena_schema/SRA.common.xsd" in call["files"]
    assert call["files"]["/tmp/library/sra_study.yaml"]["data"].startswith(("id:", "name:", "#"))
    assert page.evaluate("() => window.__editorYaml") == "name: merged\n"


def test_workspace_download_carries_the_schema_library_and_grid_schemas(page):
    _prepare_grid_selection(page, "sample", "mimicc")
    page.evaluate("() => selectSchemaById('sample', 'mimicc_sample')")
    with page.expect_download() as download:
        page.click("#workspaceChip button:has-text('Download')")
    saved = json.loads(pathlib.Path(download.value.path()).read_text())
    assert saved["grid_schemas"] == {"sample": "mimicc_sample"}
    assert "mimicc_sample" in {schema["id"] for schema in saved["schemas"]}


def test_schema_editor_follows_the_app_theme(page):
    # dhtb follows the OS colour scheme on its own; the app pins it to its own
    # data-theme on ready and on every later change. The sidecar isn't running
    # here, so stand a same-origin about:blank frame in for it and drive the
    # bridge with a faked dhtb.ready.
    page.click("a.vf-tabs__link:has-text('Schema')")
    page.evaluate("""async () => {
      CONFIG.dhtb_url = '';  // accept the faked ready from this same-origin stub
      const f = document.getElementById('schemaEditorFrame');
      f.src = 'about:blank';
      await new Promise((r) => { f.onload = r; });
      window.__dhtbMsgs = [];
      f.contentWindow.addEventListener('message', (e) => window.__dhtbMsgs.push(e.data));
      window.postMessage({ type: 'dhtb.ready' }, '*');
    }""")
    page.wait_for_function("() => (window.__dhtbMsgs || []).some((m) => m.type === 'dhtb.setTheme')")
    assert page.evaluate("window.__dhtbMsgs.at(-1).theme") == "light"

    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    page.wait_for_function("() => window.__dhtbMsgs.at(-1).theme === 'dark'")


def test_theme_toggle_switches_and_persists(page):
    # The header button rewrites <html data-theme> — everything that themes
    # itself (this page's variables, <ena-browser>, the dhtb sidecar) follows
    # that one attribute — and the choice is remembered for the next load.
    assert page.get_attribute("html", "data-theme") == "light"
    page.click("#themeToggle")
    assert page.get_attribute("html", "data-theme") == "dark"
    assert page.inner_text("#themeToggle").strip() == "Light"
    assert page.evaluate("localStorage.getItem('mimicc-theme')") == "dark"

    page.reload()
    _wait_for_workspace(page)
    assert page.get_attribute("html", "data-theme") == "dark"
    page.click("#themeToggle")
    assert page.get_attribute("html", "data-theme") == "light"


# ---------------------------------------------------------------------------
# Python in the browser (Pyodide worker) — STATIC_BROWSER_PLAN.md Phase 2
# ---------------------------------------------------------------------------

_PYODIDE_MJS = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs"


@pytest.fixture(scope="session")
def pyodide_reachable():
    """Skip when Pyodide's CDN (or PyPI) is out of reach. app.zip is already in
    the built site (dist_dir)."""
    import httpx

    try:
        httpx.get(_PYODIDE_MJS, timeout=10).raise_for_status()
        httpx.get("https://pypi.org/simple/linkml/", timeout=10).raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"Pyodide CDN / PyPI unreachable: {exc}")


def test_python_stack_runs_in_a_browser_worker(page, pyodide_reachable):
    """The real submission stack loads in Pyodide and reaches ENA through the
    sync-XHR httpx transport, with Basic auth, from the worker."""
    _use_real_python(page)
    seen = {}

    def reports(route):
        seen["authorization"] = route.request.headers.get("authorization")
        route.fulfill(
            status=200, content_type="application/json", headers={"access-control-allow-origin": "*"}, body="[]"
        )

    page.route("**/ena/submit/report/**", reports)
    results = page.evaluate(
        """async () => [
            await py('read_assign.group_files', { names: ['a_R1.fastq.gz', 'a_R2.fastq.gz'] }),
            await py('ena_service.validate_credentials', { creds: { username: 'Webin-1', password: 'pw' }, test: true }),
            await py('os.system', { command: 'true' }).catch((e) => 'refused: ' + e.message),
        ]"""
    )

    grouped, validated, refused = results
    assert grouped[0]["group"] == "a" and grouped[0]["paired"]
    assert validated is None
    assert seen["authorization"].startswith("Basic ")
    assert refused.startswith("refused: Not callable")


def test_python_prepares_and_plans_in_a_browser_worker(page, pyodide_reachable):
    """Phase 3's calls against the real worker: the schema a call needs is
    fetched into Python's filesystem, linkml filters and renames in the
    browser, and a reads plan is built after an ENA lookup."""
    _use_real_python(page)
    page.route(
        "**/ena/submit/report/**",
        lambda route: route.fulfill(
            status=200, content_type="application/json", headers={"access-control-allow-origin": "*"}, body="[]"
        ),
    )
    export = {
        "Container": {
            "MIMICC_SampleExperiments": [
                {
                    "Sample alias (ENA sample alias)": "MIMICC_A_1",
                    "Sample title": "MIMICC bioreactor A t1",
                    "LIBRARY_STRATEGY": "AMPLICON",
                }
            ]
        }
    }
    run = {
        "NAME": "runA", "STUDY": "ERP1", "SAMPLE": "ERS1", "PLATFORM": "ILLUMINA", "INSTRUMENT": "Illumina MiSeq",
        "LIBRARY_SOURCE": "METAGENOMIC", "LIBRARY_SELECTION": "PCR", "LIBRARY_STRATEGY": "AMPLICON",
        "FASTQ": "runA.fastq.gz",
    }  # fmt: skip
    samples, study_error, plan = page.evaluate(
        """async ({ exportJson, run }) => [
            await py('ena_service.prepare_sample_records',
                     { dh_export: exportJson, where: CONFIG.default_sample_filter },
                     { '/schemas/mimicc_sample.yaml': '/schemas/mimicc_sample.yaml' }),
            await py('ena_service.prepare_study_records', { dh_export: {}, dh_dir: '/dh' },
                     { '/dh/templates/study/schema.yaml': '/templates/study/schema.yaml' })
                .then(() => 'resolved', (e) => e.message),
            await py('ena_service.plan_reads',
                     { creds: { username: 'Webin-1', password: 'pw' }, runs: [run], prefix: 'batch-7' }),
        ]""",
        {"exportJson": export, "run": run},
    )

    assert samples["count"] == 1
    assert samples["records"][0]["SAMPLE_TITLE"] == "MIMICC bioreactor A t1"
    assert "LIBRARY_STRATEGY" not in samples["records"][0]
    # No DataHarmonizer bundle in this environment, so no study schema is served.
    assert "No study schema selected" in study_error
    assert plan["warnings"] == []
    assert plan["plan"][0]["alias"] == "batch-7_runA"


def test_py_rejects_when_the_worker_cannot_start(page):
    """A worker that dies on load never answers; py() must fail, not hang."""
    _use_real_python(page)
    page.route(
        "**/static/py/worker.js",
        lambda route: route.fulfill(status=200, content_type="text/javascript", body="throw new Error('boom');"),
    )
    message = page.evaluate(
        "() => py('read_assign.group_files', { names: [] }).then(() => 'resolved', (e) => e.message)"
    )
    assert message.startswith("Python runtime failed to start")


def test_python_submits_samples_from_a_browser_worker(page, pyodide_reachable):
    """Phase 4 against the real worker: prepare, XSD-validate in the browser with
    the served XSDs, and POST the sample XML to Webin with Basic auth."""
    _use_real_python(page)
    hits = []

    def ena(route):
        request = route.request
        hits.append((request.method, request.url.split("?")[0]))
        cors = {"access-control-allow-origin": "*"}
        if "/webin-v2/submit" in request.url:
            assert request.headers["authorization"].startswith("Basic ")
            assert b"<SAMPLE_SET" in (request.post_data_buffer or b"")
            receipt = (
                '<?xml version="1.0"?><RECEIPT success="true">'
                '<SAMPLE alias="MIMICC_A_2" accession="ERS999" status="PRIVATE"/>'
                '<SUBMISSION alias="s" accession="ERA1"/></RECEIPT>'
            )
            route.fulfill(status=200, content_type="application/xml", headers=cors, body=receipt)
        else:
            route.fulfill(status=200, content_type="application/json", headers=cors, body="[]")

    page.route("**://*.ebi.ac.uk/**", ena)
    export = {
        "Container": {
            "MIMICC_SampleExperiments": [
                {
                    "Sample alias (ENA sample alias)": "MIMICC_A_2",
                    "Sample title": "MIMICC_A_2",
                    "Sample storage temperature": "-80",
                    "Collection date": "2026-05-10",
                    "Taxon ID": "1235509",
                    "Scientific name": "synthetic metagenome",
                }
            ]
        }
    }
    page.evaluate("() => { CREDS = { username: 'Webin-1', password: 'pw' }; }")
    result = page.evaluate(
        """async (exportJson) => {
            const prepared = await py('ena_service.prepare_sample_records',
                { dh_export: exportJson, where: CONFIG.default_sample_filter }, servedFiles('/schemas/mimicc_sample.yaml'));
            return enaPy('ena_service.submit_samples', { records: prepared.records, checklist: 'ERC000025' },
                servedFiles('/assets/ena_schema/SRA.sample.xsd', '/assets/ena_schema/SRA.common.xsd',
                            '/schemas/mimicc_sample.yaml'));
        }""",
        export,
    )

    assert result["success"], result.get("logs")
    assert [a["accession"] for a in result["accessions"]] == ["ERS999"]
    assert any("webin-v2/submit" in url for _, url in hits), hits


def test_python_builds_and_compiles_schemas_in_a_browser_worker(page, pyodide_reachable):
    """Phase 5 against the real worker: name a schema, build one from an ENA
    checklist plus an uploaded file handed over as data, and compile it for a
    grid's fixed class."""
    _use_real_python(page)
    described, built, compiled = page.evaluate(
        """async () => {
            const upload = new TextEncoder().encode('name: seed\\nid: https://example.org/seed\\nclasses: {}\\nslots: {}\\n');
            const built = await py('schema_service.import_build',
                { source_ids: ['ERC000025.xml'], upload_paths: ['/tmp/upload/seed.yaml'], name: 'merged' },
                { ...enaSourceFiles(['ERC000025.xml']), '/tmp/upload/seed.yaml': { data: upload } });
            return [
                await py('schema_service.describe_schema', { yaml_text: built, name: 'My Merged' }),
                built,
                await py('schema_service.compile_for_grid',
                         { role: 'sample', yaml_text: (await readLibrarySchema('mimicc_sample')).yaml }),
            ];
        }"""
    )
    assert described["id"] == "my-merged"
    assert "slots:" in built
    assert compiled["template"] == "mimicc/MIMICC_Sample"
    assert "MIMICC_Sample" in compiled["schema_json"]["classes"]
