# MIMICC ENA Submission Assistant

A web app for submitting **studies**, **samples**, and **sequencing reads** to
the European Nucleotide Archive (ENA) for the
[MIMICC](../mimicc) project.

It is **a static site**: HTML, JavaScript and Python that runs in the browser
(Pyodide). There is no application server — any static host serves it, and the
browser talks to ENA directly (ENA's APIs are CORS-enabled). Your work (the
workspace, the schema library) stays in your browser; **reads upload goes
straight from your machine to ENA** — via a small local helper, or via a
`webin-cli` command you run yourself.

It ties together three existing tools:

| Concern | Reused from | How |
|---|---|---|
| Create/modify/list/delete **studies & samples** | [`ena-api-client`](../ena-api-client) + [`ena-submission-toolkit`](https://github.com/EBI-Metagenomics/ena-submission-toolkit) | `WebinClient` REST submission (from the browser's Python) + the `submit_study`/`submit_sample` batch builders |
| Enter **sample metadata** | [DataHarmonizer](../DataHarmonizer) | embedded spreadsheet UI (Samples tab) → export → filter/rename → submit |
| Submit **reads** | [`read-helper-app`](../read-helper-app), or nothing at all | **Helper mode:** a local **[read-helper-app](https://github.com/EBI-Metagenomics/read-helper-app)** Electron app runs Webin-CLI via Java on the user's machine; the browser bridges manifest → helper → result. **Manual mode:** no helper — the browser lists the reads folder itself and hands the user a `webin-cli` command to paste into a terminal |

New glue added here:

- **Read-to-sample pairing** — scan a reads folder, auto-suggest the sample for
  each FASTQ group by filename, export/import the pairing as TSV; experiment
  metadata (platform, instrument, library source/selection/strategy, …) is
  entered separately via its own embedded DataHarmonizer panel (see
  "Experiment metadata schema" below), kept in sync with the pairings; build
  webin-cli manifests, submit.
- **DH → submission pipeline** — filter a DataHarmonizer export to sample fields
  and rename columns to ENA field names (the `submit_mimicc_samples.sh` flow).
- **Account records browser** — list studies/samples/runs/experiments and run
  lifecycle actions (release/hold/suppress/cancel). The **All fields** toggle
  reads each record's full field set rather than the five columns the Webin
  Reports API returns: every checklist attribute as submitted (from the ENA
  Browser API — private records included, and it is the only source that works
  against the test environment) plus, on production, the ENA Portal's ~200
  indexed fields. It costs an extra request per 100 records, so it is off by
  default. Rendered by the reusable
  [`ena-browser`](https://github.com/EBI-Metagenomics/ena-browser) grid element,
  which also carries editing: in write mode a cell edit becomes an ENA **MODIFY**,
  gated behind a manifest preview of the exact XML — see "Record grids
  (ena-browser)" below.

Everything runs against ENA **test** by default; a header toggle switches to
**production** (with a confirm). Webin credentials are held in the browser tab
only (sessionStorage) and sent nowhere but ENA and your local read-helper-app.

## Architecture

```
Browser (static files from dist/: index.html, static/*.js, sw.js, config.json)
   │
   ├── py() ──► Web Worker: Pyodide + app.zip (server/*.py, pinned EBI packages)
   │              ena_service / read_assign / schema_service
   │              └── httpx over sync XHR ──► ENA Submit / Reports / Browser / Portal APIs (CORS, Basic auth)
   │
   ├── IndexedDB: the workspace (fields, DH grid data, reads ledger) + the schema library
   ├── Cache Storage + sw.js: each grid's compiled schema.json, served in place of the bundle default
   ├── <iframe> /dh/ ── the built DataHarmonizer bundle (fetches /templates/<folder>/schema.json)
   └── <iframe> dhtb sidecar (optional schema editor, cross-origin, postMessage)

Local read-helper-app (127.0.0.1:9100, https://github.com/EBI-Metagenomics/read-helper-app) ── webin-cli ──► ENA dropbox
   │  SSE log stream ─► Browser (read_assign.upload_result in Pyodide; browser updates the resume ledger)

   ── or, in MANUAL mode (no helper) ──
   Browser lists the reads folder itself (<input webkitdirectory>, names only) ─► read_assign.group_files (Pyodide)
   Browser renders the plan as a shell script ─► user pastes it into their terminal ── webin-cli ──► ENA dropbox
   Results come back on the next Generate: runs already in ENA are found by their stable alias.
```

- **Static site**: `scripts/build_dist.py` writes `dist/` — the page, the
  service worker, `config.json` (helper port, dhtb URL, and what the build found),
  `app.zip`, the schemas/XSDs the browser's Python reads, and the DataHarmonizer
  bundle when one is built. Serve it from a real origin (`file://` does not work:
  the service worker, the worker and the iframes need one). See
  `STATIC_BROWSER_PLAN.md` for how it got here.
- **Python in the browser**: every ENA call, study/sample Prepare and submit,
  the records browser, the reads plan and schema import/compile run in a Pyodide
  Web Worker (`server/static/py/`, `server/pyodide/`) over the unchanged
  `ena_service`/`read_assign`/`schema_service` modules. First use downloads
  Pyodide and its packages from jsDelivr/PyPI (~9 s cold, cached after); pages
  that never need Python never load it.
- **No server-side state**: the browser profile is the only copy of your
  workspace and schema library. **Download** in the header backs both up.
- **Reads**: the browser builds the webin-cli manifest and the upload *plan*
  (what to upload vs. skip, via the ledger + ENA Reports API), and the upload
  itself runs on the user's machine. The Reads tab offers two routes to run it:
  - **Local helper app** — the [read-helper-app](https://github.com/EBI-Metagenomics/read-helper-app)
    (built from a pinned tag, see "Pinned dependency versions" below) scans the
    folder and runs webin-cli, streaming its log back to the page.
  - **Manual** — no helper required. The browser lists the folder with a plain
    directory input (**file names only**; no contents read, nothing uploaded),
    `read_assign.group_files` pairs the mates, and the plan is rendered as a shell
    script the user pastes into their own terminal. Needs Java and
    [webin-cli](https://github.com/enasequence/webin-cli/releases) installed.
    Credentials are never written into the script — it reads
    `$WEBIN_USERNAME`/`$WEBIN_PASSWORD` from the shell. There is no log to paste
    back: re-running **Generate** finds anything already in ENA by its stable
    alias, which is what updates the ledger and the "In ENA" table.

## Install & run

Prerequisites: Docker Desktop. All sibling code (`DataHarmonizer`, `dh-builder`,
`ena-submission-toolkit`, `read-helper-app`, `linkml-lib`, `ena-api-client`,
`dataharmonizer-template-builder`) is pulled automatically at pinned versions
during `docker compose build` — no sibling checkouts to clone first. Node/Yarn
are **not** required on the host either — the Docker build compiles the
embedded DataHarmonizer bundle itself, in a dedicated build stage. The MIMICC
schemas + ENA XSDs (`schemas/`, `assets/ena_schema/`) are committed directly
in this repo — nothing to fetch for those either. See "Pinned dependency
versions" below for where the sibling-repo pins live.

### With Docker

```bash
cp .env.example .env   # optional — sensible defaults work out of the box
docker compose up -d --build
open http://localhost:9000
```

The image builds the DataHarmonizer bundle and the static site, then serves
`dist/` with nginx; `HELPER_PORT` and `DHTB_URL` are written into `config.json`
when the container starts. The `dhtb` schema-editor sidecar runs alongside it.
If port 9000 is already taken, set `MIMICC_PORT` in `.env`. Stop with
`docker compose down`.

### On any static host

```bash
uv sync
task build:dist -- --dh path/to/DataHarmonizer/web/dist   # omit --dh: the grids fall back to DH export upload
# upload dist/ — or try it locally:
task serve                                                 # http://127.0.0.1:9000
```

Set `HELPER_PORT`/`DHTB_URL` in the build's environment to change what
`config.json` says. The host should send `Cache-Control: no-cache` (or
otherwise not let old and new scripts mix) — `docker/nginx.conf` does. The
schema editor needs a running `dhtb` instance at `DHTB_URL`; without one the rest
of the app works, and schemas can still be imported from YAML/XML/XSD files.

Each user runs the [read-helper-app](https://github.com/EBI-Metagenomics/read-helper-app)
on their **own workstation** for helper-mode reads upload (or uses manual mode).
Point its `MIMICC_APP_ORIGIN` at the site's origin so the page may drive it.

### DataHarmonizer bundle build

The Samples tab embeds a built DataHarmonizer bundle (`server/static/dh/`) with
the MIMICC template, carrying the LinkML schema committed at
`schemas/mimicc_sample.yaml` (filtered from `mimicc_sample_experiment.yaml`
down to sample-scoped slots — see "Experiment metadata schema" below for the
sibling experiment template and the filter mechanism). `docker compose build`
produces this automatically via a `dh-builder` stage in the `Dockerfile` (Node +
Yarn + a pinned `DataHarmonizer` checkout, cloned at build time — see
`DATAHARMONIZER_REF` in the `Dockerfile`). If you need to build without it,
remove the `dh-builder` stage's `COPY --from=dh-builder` line in the final
image — the Samples tab still works via DH export upload either way.

For local non-Docker development, `scripts/build_dh_template.sh` does the same
build directly on the host (requires Node + Yarn there instead — see the
script's usage comment for the env vars it expects) against this repo's
committed `schemas/`. Both this script and the Dockerfile's `dh-builder` stage pull the
actual build steps (`dh_build_steps.sh`) from the standalone
[`dh-builder`](https://github.com/EBI-Metagenomics/dh-builder) repo — its
single canonical copy, not vendored here — pinned to a tag (`DH_BUILDER_REF` in
the `Dockerfile`), so they can't drift apart.

The bundle is baked into the image's `dist/` (at `/dh/`, with its templates
also at `/templates/`, where DataHarmonizer fetches them). Updating it (e.g.
after a DataHarmonizer version change) means rerunning `docker compose build`;
choosing a different schema for a grid does not — see "Schema library" below.

### Schema library (Schema tab)

The **Schema** tab lets you build, edit, save, and select LinkML schemas for the
sample/experiment grids, instead of being stuck with the two prebuilt MIMICC
templates:

- **Library** — kept in this browser (IndexedDB), seeded on first use from the
  bundled `schemas/*.yaml` (listed with their titles in `schemas/index.json`, so
  no Python is needed to show them). Each row can be edited, used for the sample
  or experiment grid, exported as a `.yaml` file, or deleted. You can also
  supply your own schema/checklist/XSD file from disk via the file picker.
  **Download** in the header carries the library and each grid's selection with
  the workspace; **Import…** restores both.
- **Build** — merges fields from bundled ENA sample checklists (`assets/
  ena_schema/*.xml` and `.../checklists/*.xml`, fetched with
  `scripts/fetch_ena_checklists.sh`, listed in `assets/ena_schema/index.json`),
  ENA/SRA XSDs (`assets/ena_schema/*.xsd`), and/or existing saved schemas
  (`schema_service.import_build` in the browser's Python, backed by
  `linkml_lib.pipeline.build` — the same XML/XSD→LinkML converters used
  elsewhere in this app). Earlier-selected sources win on conflicting fields.
- **Edit** — the merged/loaded schema opens in an embedded
  [`dataharmonizer-template-builder`](../dataharmonizer-template-builder)
  sidecar (the `dhtb` service in `docker-compose.yml`, built from a pinned
  git URL — see "Pinned dependency versions" below), via its `postMessage` bridge
  (`dhtb.loadYaml` / `dhtb.exportYaml` / `dhtb.ready` / `dhtb.exported`/
  `dhtb.error` — see its own `docs/integration-contract.md`). Saving validates
  the exported YAML in Python (`schema_service.describe_schema`) and stores it in
  the library.
- **Select** — choosing a schema for a grid compiles it in the browser's Python
  (`schema_service.compile_for_grid` → `linkml_lib.dataharmonizer_compile`, the
  same pure-Python compiler DH's own `script/linkml.py` performs) and puts the
  result in Cache Storage at `/templates/<folder>/schema.json` (`mimicc/`,
  `mimicc_experiment/` or `study/`). A service worker (`server/static/sw.js`,
  served at `/sw.js`) answers DataHarmonizer's fetch of that file from the
  cache and falls back to the bundle's built default, so this takes effect on
  the next iframe reload — **no DataHarmonizer bundle rebuild needed** — and
  survives a browser restart with no Python loaded. Each cached schema is
  tagged with the compiler version (`server/static/py/versions.js`); a stale
  one is recompiled from the library on load, and deleting a grid's schema
  reverts that grid to its default. Selections are per browser: nothing is
  written into the shared bundle any more.
- **Experiment schema caveat**: selecting an experiment schema that doesn't use
  the column-title contract below (`Experiment name` / `Sample alias`) breaks
  read-pairing sync — the Reads tab shows a non-blocking warning when this is
  detected.

#### Export integration (requires a patched DataHarmonizer fork)

The Samples tab's **Export to Prepare** button and its 30s autosave pull the current grid data
straight out of the embedded DataHarmonizer iframe (same-origin, via
`iframe.contentWindow.dataHarmonizer.getExportJson()`), persist it to the browser workspace
(IndexedDB) and populate the `#dhExport`
textarea that the **Prepare** step already reads — no manual File → Save As → upload round trip. On
reload the saved export is loaded **back into the grid** via
`iframe.contentWindow.dataHarmonizer.loadExportJson(...)`. The Reads tab's experiment-metadata panel
(below) uses the same mechanism under `kind=experiment`.

**This requires `window.dataHarmonizer` to exist in the DataHarmonizer bundle** — vanilla
DataHarmonizer doesn't expose it; it's a small patch applied directly to the `DataHarmonizer`
checkout pinned as the `dataharmonizer-src` build context:
- `lib/Toolbar.js`: `buildExportJson`/`getExportJson`/`loadExportJson` (full-grid export/import),
  plus a cell-level API (`getCellValue`, `setCellValue`, `findRowIndex`, `addRow`, `upsertRow`,
  `upsertRows`) used to sync individual columns without clobbering the rest of a row. `upsertRows`
  is the batched form (`[{key, values}, …]` against one key column): one key-column scan and a
  single `setDataAtCell` inside one `batchRender`, instead of a scan + render + validation pass per
  row — the Reads tab's pairing sync would otherwise crawl on a large scan.
- `web/index.js`: expose all of the above on `window.dataHarmonizer` once the grid loads
  (`{ready, getExportJson, loadExportJson, getCellValue, setCellValue, getRowCount, findRowIndex,
  addRow, upsertRow, upsertRows}`).

Without this patch, the export button shows "isn't ready yet" and the Samples tab falls back to the
manual upload/paste flow; the experiment-metadata panel (below) similarly can't sync or merge.

### Sample and experiment metadata schemas

Sample metadata and experiment metadata (platform, instrument, library source/selection/strategy,
…) are entered through **two separate** DataHarmonizer templates, both filtered out of the original
combined schema (`ena-submission-dataharmonizer/schemas/mimicc_sample_experiment.yaml`) via
the standalone `linkml-lib` package's `linkml_lib.transform.filter`:

- **`mimicc_sample.yaml`** (Samples tab) — every slot whose source metadata is one of
  `ERC000025`, `MIMICC.custom`, `ENA.sample`, `ENA.project` (44 slots). Generated with:
  ```python
  from linkml_lib import io, schema, transform
  from linkml_lib.dh_data import _select_slot_names

  s = io.load_yaml("schemas/mimicc_sample_experiment.yaml")
  rows = schema.slot_meta(s)
  names = _select_slot_names(rows, "source IN ('ERC000025', 'MIMICC.custom', 'ENA.sample', 'ENA.project')")
  ordered = [r["name"] for r in rows if r["name"] in names]
  io.write_yaml(transform.filter(s, include=ordered), "schemas/mimicc_sample.yaml")
  ```
  This reuses the same SQL-WHERE-on-slot-metadata mechanism `dh_data.filter_columns` already uses
  to filter exported *data* by source, applied here to the *schema*'s own slot list instead — and is
  exactly `ena_service.DEFAULT_SAMPLE_FILTER`, the WHERE the Prepare step already applies when
  going from a DataHarmonizer export to ENA submission fields, so the schema and that filter now
  describe the same set of fields by construction.
- **`mimicc_experiment.yaml`** (Reads tab, second panel) — the complementary 12
  `SRA.experiment`/`SRA.study`-scoped slots, plus two new slots (`PLATFORM`/`INSTRUMENT`, absent from
  the source schema — authored from scratch with standard ENA/SRA controlled-vocabulary enums) and
  two join-key slots (`experiment_name`/`sample_alias`) that don't exist in the sample/experiment
  source schema at all. `STUDY_REF`, `CENTER_NAME`, `LIBRARY_LAYOUT` and `TITLE` were dropped (the
  first three aren't needed by webin-cli or are redundant with the pairing table; `TITLE`'s original
  `ifabsent` formula referenced sample-only slots not present in this schema).

Both committed directly at `schemas/` in this repo (copied from
`ena-submission-dataharmonizer`'s `schemas/` directory, no per-file changes
needed there). The experiment template build step still tolerates
`mimicc_experiment.yaml` being absent (gracefully falling back to sample-template-only), even though
in practice both files now exist permanently.

- **Column-title contract** (experiment schema only — the sample schema needs no equivalent contract
  since the Samples tab just renders whatever the schema defines, with no app-side sync/merge logic
  reading specific column titles): the app syncs/merges by fixed, expected LinkML `title:` values
  (see `EXP_KEY_TITLE`/`EXP_SAMPLE_TITLE`/`EXP_FIELD_TITLES` near the top of the "Experiment metadata
  DataHarmonizer panel" section in `server/static/app.js`) — your schema's slots must use these exact
  titles:

  | Manifest field | Required `title:` |
  |---|---|
  | (row key, matches a pairing row's NAME) | `Experiment name` |
  | (matches a pairing row's SAMPLE) | `Sample alias` |
  | PLATFORM | `Platform` |
  | INSTRUMENT | `Instrument` |
  | LIBRARY_SOURCE | `Library source` |
  | LIBRARY_SELECTION | `Library selection` |
  | LIBRARY_STRATEGY | `Library strategy` |
  | INSERT_SIZE (optional) | `Insert size` |
  | LIBRARY_NAME (optional) | `Library name` |
  | DESCRIPTION (optional) | `Description` |

  Use your schema's own `ifabsent` defaults for PLATFORM/INSTRUMENT/etc. (replacing the removed
  hardcoded "library preset" dropdown) — new rows added by the sync below pick those up
  automatically (`addRows()`'s normal default-population behaviour).
- **How sync works**: whenever the Reads tab's pairing table changes (scan, auto-assign, manual
  edit, TSV import), each pairing row's NAME/SAMPLE is upserted into the experiment grid by `NAME`
  — only those two columns are touched, so anything already filled in (manually, or via a default)
  on that row is preserved. At submit time, each pairing row is merged with its matching experiment
  row (by NAME) to build the webin-cli manifest; a row with no experiment-grid match, or an
  experiment grid that isn't built/ready, blocks submission with a clear error rather than sending
  an incomplete manifest.
- There is no runtime rebuild path for either template — both require a full `docker compose build`
  to pick up schema changes (see "DataHarmonizer bundle build" above).

### Read-sample pairing TSV

The Reads tab's pairing table can be exported/imported as TSV (**Export pairings (TSV)** /
**Import pairings (TSV)** buttons), columns: `NAME, SAMPLE, STUDY, paired, FASTQ1, FASTQ2, FASTQ`.
This is a full round-trip of a pairing row (not just the sample assignment), so importing works
standalone without scanning first; importing onto an existing table merges by `NAME` (updates a
matching row, appends a new one otherwise).

### Record grids (ena-browser)

Record tables — anything showing rows that came from ENA's **Webin Reports API** —
are rendered by [`ena-browser`](https://github.com/EBI-Metagenomics/ena-browser),
a standalone, framework-free `<ena-browser>` custom element built on Handsontable.
It is vendored as a prebuilt bundle (`server/static/vendor/ena-browser/`) at a pinned
release tag and loaded with plain `<script>`/`<link>` tags — it introduces no npm
build step, exactly like the embedded DataHarmonizer bundle.

**Five grids:**

1. **Records tab** (`#recGrid`) — the main browser. Fetch criteria (search, "linked
   to accession", unlinked-only, status, all-fields) are criteria on the *request*,
   applied server-side by `ena-submission-toolkit`; per-column filtering, sorting and
   column pinning/reordering/hiding are the element's own and client-side. The "hide
   cancelled" / "hide suppressed" toggles are built into the element. With **write
   mode** ticked (`#recWrite`, off on every page load and never restored from a
   session) cells become editable and row actions appear.
2. **Studies tab** (`#studyGrid`) and **3. Samples tab** (`#sampleGrid`) —
   post-submission confirmation: a read-only grid of the account listing, filtered to
   the accessions this submission produced, shown *alongside* the receipt table
   (which is what the submission did, failures included, and not the same thing).
4. **Reads tab, pairing panel** (`#pairSamples`) — the *samples* side of read↔sample
   pairing, in `selection-mode="single"`. Selecting a sample fires
   `ena-browser:selection-change`; this app stores `detail.lastKey` in
   `SELECTED_SAMPLE` and the next click on a read-group row records the pairing. The
   per-sample file count is a **pinned custom column** (`reads_assigned`) pushed in
   with `setCustomValues("reads_assigned", {ERS…: 2})` after every change to the run
   rows — updating it does not re-sort the grid or lose the selection.
5. **Reads tab, confirmation** (`#readsGrid`) — the runs just submitted, unioned with
   the resume ledger so a resumed batch shows its earlier runs too. Run rows carry
   `process_status` / `process_date` / `process_error`: whether ENA has finished
   **archiving** the read files, which registering a run does not say. "Submitted" and
   "archived" are different things, and this column is where you see the difference.

**Editing is gated.** An ENA MODIFY *replaces* the whole record, so **Submit changes**
stays locked until *Generate manifests* has built and shown the exact XML for the
current edits (`ena_service.preview_modify_records`), and any further edit re-locks it.
The editable fields per entity come from `config.json` (`editable_columns`) — the
toolkit builds the XML, so it is the authority — plus this listing's checklist
attributes, which arrive as `attr:`-prefixed columns when **all fields** is ticked and
are addressed by tag in the MODIFY.

**The division of responsibility.** The element is a *view*: it renders, filters,
sorts, selects and tracks edits. It does not fetch, does not hold credentials, does
not know about test vs production, does not persist anything, and never submits.
This app keeps all of that — the `ena_service.list_records` fetches, the Webin credentials, the
debug log, the release/hold/suppress/cancel handlers (the element only *emits*
`ena-browser:row-action`), the IndexedDB session state that stores the grid layout and
filters, and the pairing logic that joins a selected sample to a read group.
Manifest/XML building for modifications stays in `ena-submission-toolkit`.

**Out of scope:** the Portal "all of ENA (read-only)" source, undo/redo, and a
change-history stack. They do not serve the submission workflow and can be added
independently later.

**Workspace state.** The workspace persists each grid's layout (column order, pins, hidden
columns, widths) and filters, and never its rows: a saved row shows the status it had
when it was saved, which after a release or a suppress is the wrong one. A reload
re-fetches instead, with the saved layout applied *before* the rows arrive —
a column the grid first meets in the data arrives hidden, and that sticks.

**Usage sketch:**

```js
const grid = document.getElementById("recGrid");
grid.applyConfig({ entity: "samples", mode: "edit", editableColumns: ["alias", "title"] });
grid.setRows(await enaPy("ena_service.list_records", { entity: "samples" }));

grid.addEventListener("ena-browser:change", () => refreshSubmitButton());
grid.addEventListener("ena-browser:row-action", (e) =>
  recAction(e.detail.action, e.detail.row?.accession || e.detail.key));
```

**Two page-level fixes the element needs** (both in `core.js`, both easy to
re-break):

- The body `keydown`/`keyup` swallower — there because each DataHarmonizer iframe
  attaches a Handsontable shortcut recorder to *this* page's `documentElement` — now
  lets events inside an `<ena-browser>` through, since the in-page grid's own recorder
  sits there too. Without it the grid takes no keystrokes at all.
- Handsontable focuses a hidden input on mousedown, and the browser scrolling that
  into view moved the grid ~140px between mousedown and mouseup, so a click on a
  row-action button never completed. The page scroll is pinned across that focus.

Theming needs no wiring: the element reads the same CSS custom properties this app
already defines (`--bg`, `--panel`, `--line`, `--fg`, `--muted`, `--accent`, …) and
honours `data-theme`, which is the app's single source of truth for light/dark.

`theme.js` owns it: `initTheme()` (called first in `boot.js`) stamps `<html
data-theme>` from `localStorage["mimicc-theme"]`, falling back to the OS
`prefers-color-scheme`, and the header's **Dark**/**Light** button
(`toggleTheme()`) rewrites it. The dark palette is the `:root[data-theme="dark"]`
block in `index.html`, plus overrides for the Visual Framework surfaces that
hard-code light colours (`.vf-card`, form controls, tabs, banners) — the hero's
green band is left alone in both themes. `<ena-browser>` follows the attribute
with no extra wiring; the schema editor sidecar is pushed the resolved theme by
`schema.js` `syncDhtbTheme()` (`dhtb.setTheme` on `dhtb.ready` and from a
`data-theme` MutationObserver), since dhtb otherwise follows the OS setting. The
**DataHarmonizer iframes stay light** — that bundle has no dark theme.

Refresh the vendored bundle with `task vendor:ena-browser` after bumping
`ENA_BROWSER_REF` in `Taskfile.yml`; the two downloaded files are committed (like
the DataHarmonizer bundle) so image builds and the Playwright suites need no
network fetch. `pre-commit` skips `server/static/vendor/` — reformatting a bundle
corrupts it.

The plan this was built from — including the collisions it had to work around —
is in [`ENA_BROWSER_PLAN.md`](ENA_BROWSER_PLAN.md).

## Workspace

There are no named sessions. The app keeps **one workspace** in this browser and restores it on
load, with no prompt:

- **What's persisted** — every text field, checkbox and selection, the three DataHarmonizer grids'
  data, all result tables, the Reads/Records logs, and the reads resume ledger. Saving is automatic
  (debounced as you type, plus immediately after submits); the header shows "saved …".
  **Credentials are never saved** — re-enter them after a restart.
- **Where** — IndexedDB in this browser profile, which is the **only copy**. A private window loses
  it on close; clearing site data deletes it. Use **Download** in the header to back it up or move
  it to another machine, **Import…** to restore one (an old `.session.json` imports too), and
  **Clear** to start over.
- **One at a time** — two submissions side by side in one browser profile are no longer possible.
  Download one before clearing, and import it again later.
- **Resumable reads** — each run is submitted under the alias `<submission prefix>_<run name>`.
  The prefix is a field on the Reads tab, filled in on first submit if blank. The upload **plan**
  skips runs already submitted from this workspace or already present in ENA under that alias
  (checked via the Reports API), so an interrupted batch resumes by just clicking **Submit** again.
  Tick a run's **Re-upload** box (or the global "force re-upload all" toggle) to submit it again
  under a fresh alias (ENA aliases are permanent, so a forced re-upload necessarily creates a new
  experiment/run). A browser that had sessions adopts the most recently used one on first load,
  with its name as the prefix.

## Using it

1. **Credentials** — enter your Webin username/password (memory only; also forwarded to the local
   read-helper-app when it's running, so it can upload).
2. **Studies** — create a study → note the `PRJEB…` accession.
3. **Samples** — enter metadata in DataHarmonizer, click **Export to Prepare** (autosaves every
   30s too — see "Export integration" above), **Prepare** (filter + rename), then **Submit** with
   checklist `ERC000025` → `ERS…`/`SAMEA…`.
4. **Reads** — pick how you want to run the upload at the top of the tab. With the
   **local helper app**: make sure the **read-helper-app** is running (the tab shows "helper:
   running"), enter the absolute path to your **local** reads directory and **Scan** (the helper
   lists read groups). In **manual** mode (the default when no helper is detected): click **Choose
   reads folder…** — only the file names are read, nothing is uploaded — and type the folder's
   absolute path into the box, which becomes webin-cli's `-inputDir`.
   Either way, continue with **Auto-assign samples** (or export/import the pairing as TSV) and fill
   in platform/instrument/library fields in the **experiment metadata** DataHarmonizer panel (synced
   from the pairings — see "Experiment metadata schema" above). Then **Submit reads to ENA** (helper
   mode: the helper runs webin-cli locally and streams its log back, and the experiment + run
   accessions are recorded) or **Generate submit command** (manual mode: copy the script into a
   terminal and run it yourself). Re-submit — or re-generate — to resume; runs already in ENA are
   skipped.
5. **Records** — browse account records and release/hold/suppress/cancel.

## Development

```bash
uv sync                                   # Python deps + dev tools (pytest, Playwright, ruff, mypy)
python -m playwright install chromium     # for the UI tests
task serve                                # build dist/ and serve it on http://127.0.0.1:9000
```

Re-run `task serve` (or `task build:dist`) after editing anything under
`server/` — the page is served from `dist/`, and `app.zip` bundles `server/*.py`.
For the DataHarmonizer grids locally, build a bundle with
`scripts/build_dh_template.sh`; it lands in `server/static/dh/`, which
`build_dist.py` picks up by default.

The schemas/XSDs (`schemas/`, `assets/ena_schema/`) are committed directly in
this repo; after adding one, run `task build:indexes` (a static host cannot list
directories, so the page reads `index.json` files).

### Tests

`pytest` runs the Python unit tests (the modules the browser runs, plus the
build scripts) and the Playwright UI suite, which builds `dist/` once and serves
it with `scripts/serve_dist.py`. No Docker needed: UI tests stub the page's
`py()` calls, and the few tests that run real Pyodide need network access to
jsDelivr and PyPI, and skip without it.

The test-only packages are the `dev` dependency group in `pyproject.toml`, so
`uv sync` installs them — and, just as importantly, does not prune them.

```bash
task test                                  # everything
task test:ui                               # Playwright only
```

### Docker Compose tests

`tests/test_compose_ui.py` runs Playwright against the real `docker compose`
stack instead of the in-process fixture above — the nginx-served image, the real
DataHarmonizer bundle, and the real `dhtb` sidecar container, reached over the
network instead of mocked. It's the one place
that exercises what `docker-compose.yml` actually assembles, at the cost of
an image build; it can't cover the ENA-data-dependent tests in
`tests/test_ui.py` (nothing to mock in a separate container), so it's a
narrower, slower complement to the suite above, not a replacement.

Opt-in (needs Docker, takes minutes for the image build) — skipped unless
`COMPOSE_TEST=1`:

```bash
COMPOSE_TEST=1 python -m pytest tests/test_compose_ui.py -q
```

## Layout

```
server/            the app's source (no server any more — the name is historical)
  static/            the page: index.html, *.js, sw.js, vendor/ena-browser/, py/ (worker.js, versions.js)
  pyodide/           browser-only Python: ena_bridge.py (httpx over sync XHR, py() entry point),
                     shims/pendulum.py
  ena_service.py     studies/samples/records/actions/reads plan — MIMICC glue only; every ENA request
                     is made by ena-submission-toolkit over ena-api-client, never here
  read_assign.py     read grouping / suggest / manifest text / upload plan
  schema_service.py  schema naming, ENA XML/XSD import/merge, grid compile
  _bootstrap.py      locates schemas/ and assets/ena_schema/ (under / in the browser)
schemas/           committed MIMICC LinkML schemas + index.json
assets/ena_schema/ committed ENA/SRA XSDs + checklist XMLs + index.json
scripts/
  build_dist.py            build dist/ (the static site)
  serve_dist.py            no-cache static server for local use and tests
  build_py_bundle.py       build static/py/app.zip (the Python the browser runs)
  build_static_indexes.py  write schemas/index.json + assets/ena_schema/index.json
  fetch_ena_checklists.sh  fetch the full set of public ENA sample-checklist XMLs
  build_dh_template.sh     build the embedded DataHarmonizer bundle (local dev)
docker/            nginx.conf + write-config.sh for the runtime image
tests/             pytest + Playwright
Dockerfile         DataHarmonizer bundle → site build → nginx
docker-compose.yml the app + the dhtb sidecar
```

The Dockerfile for the `dh-builder` image (shared with
[dataharmonizer-template-builder](https://github.com/EBI-Metagenomics/dataharmonizer-template-builder),
which runs the same image with a different `TEMPLATE`), and `dh_build_steps.sh`
(the shared DH build steps, pulled in by the Dockerfile's embedded
`dh-builder` stage and `scripts/build_dh_template.sh` above) live in the
standalone [`dh-builder`](https://github.com/EBI-Metagenomics/dh-builder) repo,
pulled at a pinned tag — used only at image-build time now (there's no
runtime/on-demand rebuild path), the same way [`read-helper-app`](https://github.com/EBI-Metagenomics/read-helper-app)
is pulled for reads upload.

### Pinned dependency versions

All sibling-repo code is pulled at a fixed git tag or full commit SHA, never a local checkout or
`main`/`master`. The pins live in two places:

- **`pyproject.toml`** — `linkml-lib` (v0.1.0) and `ena-submission-toolkit`
  (the URL-migration commit on top of v0.1.4, which adds `attr:` checklist columns
  and attribute editing) as
  `name @ git+https://github.com/EBI-Metagenomics/<repo>.git@<ref>` entries
  in `[project.dependencies]`.
- **`Taskfile.yml`** — `ENA_BROWSER_REF` (v0.1.2), the `ena-browser` release whose
  `ena-browser.iife.js` + `ena-browser.css` are vendored into
  `server/static/vendor/ena-browser/` and **committed**. Bump the ref, then
  `task vendor:ena-browser`, then commit the two files.
- **`Dockerfile`** — `DATAHARMONIZER_REF` / `DH_BUILDER_REF` build
  args, and **`docker-compose.yml`** — the `read-helper-app` and `dhtb` services'
  `build.context`/`additional_contexts` git URLs (`...git#<tag>`, or
  `...git#<tag>:<subdir>` for a subdirectory).

The MIMICC schemas + ENA XSDs (`schemas/`, `assets/ena_schema/`) aren't
pinned at all — they're committed directly in this repo, so they version
along with everything else.

To bump a pin: cut a new tag in the sibling repo, then update every reference
to that repo's tag across these two files (`grep -rn EBI-Metagenomics .` from
the repo root finds them all).

## Notes

- **Webin credentials** live in the browser tab (sessionStorage) and are never
  written to disk or logged. They go to ENA from the browser's Python and, in
  helper mode, to the local read-helper-app (in its memory only) so it can upload.
- **Reads** go through webin-cli on the **user's machine** — via the
  read-helper-app, or a command the user runs — **not** the JAR path in
  `submit_reads.py` (that module is intentionally not imported — avoids its
  mgnify-toolkit dependency). Nothing but that machine ever touches read files.
- **ENA's CORS** is its configuration, not a contract: the app breaks outright if
  it is withdrawn. Re-probe before a release — see `STATIC_BROWSER_PLAN.md` §0.1.
