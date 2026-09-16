# The MIMICC ENA Submission Ecosystem

This document describes the group of related `EBI-Metagenomics` projects that
together let the MIMICC project submit studies, samples and sequencing reads to the
**European Nucleotide Archive (ENA)**. It explains what each project is for, where
each kind of functionality belongs, which language/tool implements what (and why),
where each piece runs, and the role of each external dependency.

---

## 1. Overview

End to end, the ecosystem does this:

1. **Create an ENA study** for the project.
2. **Enter sample and experiment metadata** in an embedded **DataHarmonizer**
   spreadsheet whose columns are driven by a **LinkML** schema.
3. **Prepare and submit samples** — metadata is filtered, renamed to ENA field
   names, turned into SRA XML, validated against ENA's XSDs, and POSTed to the
   Webin Submission API.
4. **Scan local sequencing reads**, group paired-end mates, and assign each read
   group to a sample/experiment.
5. **Submit reads** via ENA's `webin-cli`, run on the *user's own machine* so the
   large data files never leave it except to ENA.

The pieces are layered: a **static site** — a vanilla-JS UI plus Python that runs
in the browser (Pyodide) — which orchestrates a set of **Python libraries** (ENA
transport, LinkML utilities, submission builders) and calls ENA directly. Two companions sit beside it: a
**schema editor** (dhtb) run as a Docker Compose service, and an *optional* **native
desktop reads uploader** (read-helper-app) that the browser drives directly —
optional because the Reads tab can instead hand the user a `webin-cli` command to
run themselves. A **bundle builder** (dh-builder) runs at image build time.

```
                ┌───────────────────────────────────────────────┐
                │  mimicc-ena-submission-assistant (static site)│
                │  vanilla-JS SPA  +  embedded DataHarmonizer   │
                └───────┬────────────────┬───────────────┬──────┘
                        │ imports        │ postMessage   │ HTTP (from browser)
        ┌───────────────┴───┐    ┌───────┴─────────┐  ┌──┴──────────────┐
        │  Python libraries │    │ dataharmonizer- │  │ read-helper-app │
        │  • ena-api-client │    │ template-builder│  │ (Electron,      │
        │  • linkml-lib     │    │ (schema editor) │  │  webin-cli via  │
        │  • ena-submission-│    └───────┬─────────┘  │  Java on user PC│
        │    toolkit        │            │ Docker     └──────┬──────────┘
        └─────────┬─────────┘      ┌─────┴──────┐            │
                  │                │ dh-builder │            │
                  │                │ (DH bundle │            │
                  │                │  builder)  │            │
                  │                └─────┬──────┘            │
                  │                      │ builds            │
                  │                ┌─────┴───────────┐       │
                  │                │ DataHarmonizer  │       │
                  │                │ fork (UI engine)│       │
                  │                └─────────────────┘       │
                  ▼                                          ▼
            ┌───────────────────────────────────────────────────┐
            │         ENA — Webin Submission & Reports APIs     │
            └───────────────────────────────────────────────────┘
```

---

## 2. Summary table

| Project | Type | Language(s) | Purpose | Key dependencies | Consumed by |
|---|---|---|---|---|---|
| **mimicc-ena-submission-assistant** | Web app (static) | vanilla JS + Python in the browser (Pyodide) | The product: end-to-end UI for studies, samples, reads submission to ENA | ena-api-client, ena-submission-toolkit, linkml-lib, DataHarmonizer, dh-builder, ena-browser, read-helper-app, dhtb | — (top of stack) |
| **dataharmonizer-template-builder** (dhtb) | Web app / embeddable component | Python/Django + React/TypeScript/Vite | Interactive editor for LinkML DataHarmonizer schemas (YAML ↔ tables ↔ schema.json) | linkml-lib, DataHarmonizer, dh-builder, Handsontable | mimicc-assistant (iframe + postMessage) |
| **ena-api-client** | Library | Python | Typed client for ENA Webin Submission (XML) and Reports (JSON) APIs | httpx, pydantic | toolkit, assistant |
| **linkml-lib** | Library | Python | LinkML utilities: schema I/O, editable-table conversion, XML/XSD↔LinkML, DataHarmonizer compilation, diagnostics | linkml, linkml-runtime, PyYAML | toolkit, assistant, dhtb |
| **ena-submission-toolkit** | Library + CLI | Python | Schema-driven study/sample XML builders, unit normalisation, XSD validation, batch submit, record listing/MODIFY/lifecycle | ena-api-client, linkml-lib, lxml, typer | assistant |
| **read-helper-app** | Electron desktop app | TypeScript/Node | Scans local reads and runs Webin-CLI uploads on the user's machine via Java | electron, node, Java | assistant's browser UI (HTTP on 127.0.0.1:9100) |
| **dh-builder** | Docker build steps + Python executor | Shell + Python + Node | Builds a DataHarmonizer web bundle from a LinkML schema | DataHarmonizer source, Node/Yarn | assistant (image build), dhtb (runtime previews) |
| **DataHarmonizer** (fork) | UI engine (vendored) | JavaScript + Handsontable | The spreadsheet editor/validator embedded for metadata entry | handsontable | assistant, dhtb, dh-builder |
| **ena-browser** | UI element (vendored) | TypeScript + Handsontable | Reusable `<ena-browser>` custom element for viewing/filtering/editing ENA Webin **report** records | handsontable | assistant |

---

## 3. Architectural layers

**Foundation libraries.** `ena-api-client` is the only thing that talks HTTP to ENA's
Webin APIs; `linkml-lib` is the only thing that understands LinkML schemas and
converts between LinkML, editable tables, XSD/XML and DataHarmonizer's
`schema.json`. Everything else builds on these two.

**Orchestration library + CLI.** `ena-submission-toolkit` sits on top of the two
foundation libraries and turns structured (or DataHarmonizer-exported) data into
validated SRA XML and submits it in batches. It is both an importable library and a
`ena-submission-toolkit` CLI.

**Applications.** `mimicc-ena-submission-assistant` is the user-facing product.
`dataharmonizer-template-builder` (dhtb) is a focused companion app for *editing the
schema* that drives the grids.

**Companions.** `read-helper-app` is a native desktop app that runs Webin-CLI on the
user's machine; `dh-builder` builds the DataHarmonizer bundle. Both are decoupled
over HTTP/JSON or Docker, not Python imports.

**Vendored UI engines.** The `DataHarmonizer` fork (pinned at `v2.1.1-mimicc`) is the
Handsontable-based metadata spreadsheet both apps embed; `ena-browser` is the
Handsontable-based record grid the assistant embeds.

---

## 4. Per-project documentation

### 4.1 mimicc-ena-submission-assistant

The product. A **static site** — no application server — serving a
**single-page vanilla-JavaScript** UI with **no Node/npm build step**, whose domain
logic is the Python submission stack running **in the browser** (Pyodide). It is
**single-user**: all persistent state lives in the browser, and the only server is
whatever serves the files (nginx in the Docker image, `scripts/serve_dist.py` locally).

- **Python in the browser** (`app/pyodide/`, `app/static/py/`): every ENA
  call — study/sample prepare + submit, record listing, MODIFY, lifecycle actions,
  reads suggest/plan/result — runs in a Pyodide Web Worker. The page calls
  `py("module.function", kwargs)`; `ena_bridge.py` routes `httpx` over
  synchronous XHR, so requests go from the browser straight to ENA (CORS-enabled)
  with the Webin credentials and never touch this app's server. `ena_service.py`
  is MIMICC glue over `ena-submission-toolkit` (its `records.py` owns listing,
  MODIFY and lifecycle actions) — no ENA request is made directly in this repo;
  `read_assign.py` groups reads and builds webin-cli manifests and the upload plan.
- **Build** (`scripts/build_dist.py`): writes `dist/` — the page, `sw.js`,
  `config.json` (helper port, dhtb URL, bundle availability, editable columns),
  `app.zip`, schemas/XSDs and the DataHarmonizer bundle. `schema_service.py` wraps
  `linkml-lib`: it names, imports and compiles schemas for the fixed DH template
  folders, in the browser; a service worker serves each grid's compiled schema.
- **Frontend** (`app/static/`): `index.html` shell plus per-concern scripts
  (`core.js` API clients, `credentials.js`, `workspace.js`, `records.js`,
  `samples.js`, `reads.js`, `schema.js`, `dataharmonizer.js`, `theme.js`,
  `boot.js`). The workspace (all entered state) and the reads resume ledger are kept in
  **IndexedDB**; Webin credentials in **sessionStorage** for the tab only. The
  DataHarmonizer bundle is built in Docker and copied into the site at `/dh/`; `ena-browser` is vendored under `app/static/vendor/`.
- **Schema artifacts** committed in the repo: `schemas/*.yaml` (LinkML) and
  `assets/ena_schema/` (ENA/SRA XSDs and checklist XMLs). User-saved schemas live in
  the browser (IndexedDB); the grids' compiled schemas in Cache Storage, served
  by a service worker.
- **Pinned sibling libraries** (in `pyproject.toml`, no submodules):
  `ena-api-client @ ...@v0.1.0`, `linkml-lib @ ...@v0.1.0`,
  `ena-submission-toolkit @ ...@v0.1.0`.

### 4.2 dataharmonizer-template-builder (dhtb)

A browser-based editor for LinkML DataHarmonizer schemas. It loads a schema (YAML),
converts it to editable tables (Schemasheets-style: classes, slots, enums,
annotations), lets the user edit them in a Handsontable grid, and converts back to
LinkML YAML — plus produces a DataHarmonizer preview. Runs standalone or embedded.

- **Type:** full-stack web app — **Django** backend + **React 18 / TypeScript /
  Vite 6** frontend. Not published to npm/PyPI; deployed as a single Docker container
  on port **8765**.
- **Backend** (`src/dataharmonizer_template_builder/`): `conversion.py` and
  `dh_compile.py` call `linkml-lib` (`edit_tables`, `io`, `dataharmonizer_compile`)
  directly — no wrapper layer; `table_sync.py` holds the table-merge/diff logic;
  `dh_builder_runner.py` runs `dh-builder-lib` for preview rebuilds.
  Schema-handling code (including annotation handling) lives in `linkml-lib` itself,
  not re-implemented here. In-memory per-session store.
- **Frontend** (`frontend/src/`): `App.tsx` (the editor), `DataGrid.tsx`
  (Handsontable wrapper), `api.ts` (HTTP client), `tableSync.ts` (client-side mirror
  of the backend sync logic).
- **Dependencies:** `linkml-lib @ ...@v0.1.0`, the `DataHarmonizer` fork
  (`file:../DataHarmonizer` for dev, `v2.1.1-mimicc` in Docker), `dh-builder-lib` for
  on-demand bundle rebuilds, `handsontable` / `@handsontable/react-wrapper` 17.1.0.
- **Integration:** embedded by the assistant as a cross-origin iframe loaded by the
  browser straight from `DHTB_URL` (not proxied through the assistant); they exchange
  schema YAML over `postMessage` (`dhtb.loadYaml` → `dhtb.exported`).

### 4.3 ena-api-client

Typed **Python** client for ENA's two Webin HTTP APIs: the v2 Submission API (XML
submission, lifecycle actions release/hold/suppress/cancel) and the Reports API
(querying studies/samples/runs). Built on **httpx** (transport) and **pydantic** /
**pydantic-settings** (typed models + env config). It is the single point of HTTP
contact with ENA's Webin APIs (read files go via `webin-cli` instead). Distributed
via pip; consumed by `ena-submission-toolkit` and the assistant. Module layout:
`ena_api/` (client, config, models, submit, reports).

### 4.4 linkml-lib

Reusable **Python** utilities for LinkML schemas used with DataHarmonizer and ENA
tooling. Built on **linkml** / **linkml-runtime** / **PyYAML**. Key modules
(`src/linkml_lib/`): `io.py` (YAML I/O), `edit_tables.py` (schema ↔ editable tables),
`convert_xml.py` / `convert_xsd.py` (ENA XML/XSD ↔ LinkML), `dataharmonizer_compile.py`
(LinkML → DataHarmonizer `schema.json`), `schema.py` (introspection, `UnitRule`),
`pipeline.py` / `transform.py` / `dh_data.py` (build/merge schemas, filter exports),
`diagnostics.py`. It is the shared schema brain consumed by the toolkit, the
assistant *and* dhtb — dhtb calls these modules directly rather than through its own
wrapper layer, and schema logic (e.g. annotation handling) is pushed down here rather
than living in dhtb.

### 4.5 ena-submission-toolkit

Schema-driven **Python** library + **Typer** CLI (`ena-submission-toolkit`) for
building and submitting ENA records. It orchestrates XML manifest building, unit
normalisation, duplicate detection, **lxml** XSD validation (ENA/SRA XSDs bundled in
`assets/ena_schema/`), and submission via the Webin API. Depends on `ena-api-client`
(transport) and `linkml-lib` (schema utilities/unit rules), both pinned at `v0.1.0`.
Key modules (`src/ena_submission_toolkit/`): `submit_sample.py`, `submit_study.py`,
`prepare_dh_output.py`, `records.py`, `common.py`, `cli.py`. The assistant imports
these directly.

### 4.6 read-helper-app

A native **Electron** desktop app (**TypeScript/Node**) that runs `webin-cli` read
uploads from the user's machine — large read files never reach the assistant server.
It is one of two routes: the assistant's Reads tab also has a **manual mode** that
needs no helper at all, listing the reads folder with a plain browser directory
input and rendering the same plan as a `webin-cli` script the user runs themselves.
The main process starts a small Node HTTP server bound to **127.0.0.1:9100**; the
assistant's *browser* UI detects it on localhost and drives it with cross-origin
requests (the assistant server never talks to it).

- **API** (`src/server.ts`): `GET /api/health`, `POST`/`DELETE /api/credentials`
  (Webin credentials held in memory only), `GET /api/browse` (local directory
  picker), `POST /api/scan` (find and group reads, `src/readScanner.ts`),
  `POST /api/submit` (start an upload job), `GET /api/status/<job_id>` (polling) and
  `GET /api/stream/<job_id>` (log stream, used by the assistant via `EventSource`),
  `POST /api/shutdown`.
- **webin-cli** (`src/webinCli.ts`): downloads and caches the official
  `webin-cli.jar` release from GitHub and spawns it with the local **Java** runtime
  (`JAVA_BIN` or `java` on `PATH`). No Docker is involved.
- **Other files:** `src/main.ts` (Electron entry), `src/renderer/index.html` (status
  window), `src/accessions.ts`; packaged per OS with **electron-builder**.

### 4.7 dh-builder

A standalone set of **shell build steps** plus a minimal **Python executor** that
builds a DataHarmonizer web bundle from a LinkML schema. Build steps run over
**Node 20 / Yarn** against the DataHarmonizer fork; the Python side
(`dh_builder_lib`) exposes `iter_dh_builder_logs()` / `run_dh_builder()`. The
assistant uses it **only at image build time** — the `Dockerfile` `dh-builder` stage
runs `dh_build_steps.sh` for the `mimicc`, `mimicc_experiment` and `study` template
folders; at runtime the assistant just recompiles `schema.json` into those folders
with `linkml-lib`. dhtb uses it at runtime for previews
(`TEMPLATE=template_builder_preview`).

### 4.8 DataHarmonizer (fork)

The external **JavaScript** spreadsheet editor/validator from CIDGOH, forked and
pinned at **`v2.1.1-mimicc`**. It provides the **Handsontable**-based grid UI that
both apps embed for metadata entry. It is not edited as part of normal work; it is
built into a bundle by `dh-builder` and consumed as a static asset (assistant) or via
`@handsontable/react-wrapper` (dhtb).

### 4.9 ena-browser

A standalone, framework-free **custom element** (`<ena-browser>`) that renders ENA
**Webin Reports** records — studies, samples, runs, experiments, analyses, files — in
a **Handsontable** grid with per-column filtering and sorting, column pinning/
reordering/hiding, row selection, host-driven dynamic columns, and an optional edit
mode that emits a change set. Written in **TypeScript**, built with **Vite** in
library mode into an ESM bundle (Handsontable as a peer dependency) and a
self-contained **IIFE** bundle the assistant vendors under `app/static/vendor/`
the same way it vendors the DataHarmonizer bundle — no npm build step is introduced.

It is deliberately a *view*: it never talks to ENA's submission API, never builds a
manifest, never stores credentials and never persists anything. It takes rows (or an
optional Reports-API data source, used by its standalone demo app) and emits events —
`selection-change` (the read↔sample pairing hook), `change` (the edit change set the
host feeds to `ena-submission-toolkit`'s MODIFY path), `row-action` (release/hold/
suppress/cancel, executed by the host), `filter-change` and `layout-change`. Status
of "cancelled"/"suppressed" include/exclude toggles are built in, because every
consumer wants them. In the assistant it backs the Studies, Samples, Reads,
sample-pairing and Records grids, with rows supplied by
`ena_service.list_records` running in the browser.

Repo: `EBI-Metagenomics/ena-browser`. The assistant's adoption plan lives in this
repo's `ENA_BROWSER_PLAN.md`.

---

## 5. Language & tool choices in the two large apps — and why

Both large apps run their domain logic in **Python** for the same reason: *all* of
it — the ENA HTTP client, the LinkML utilities, the submission builders — is Python.
dhtb calls it from a Django backend; the assistant runs it in the browser. The other
difference is the **frontend**.

### mimicc-ena-submission-assistant — Python *in the browser*, *vanilla JS* frontend

- **Python, run in the browser** (Pyodide, in a Web Worker), for all domain work:
  orchestrating `ena-api-client`, `ena-submission-toolkit` and `linkml-lib`. Chosen
  because the submission stack is already Python and tested; `httpx` runs over
  synchronous XHR, and ENA's APIs are CORS-enabled, so no server sits in between.
  It started as a Django server; the server was removed once every endpoint could
  run in the page (`STATIC_BROWSER_PLAN.md`).
- **Vanilla JavaScript with no build step.** The app does not ship React or a
  bundler. The heavy interactive UI — the metadata spreadsheet and record grids — is
  the embedded **DataHarmonizer** bundle and the vendored **ena-browser** element, so
  the app shell only needs to manage tabs, `py()` calls, browser-side storage
  and the DataHarmonizer/read-helper-app lifecycle. Hand-written JS keeps the app
  build-free and dependency-light: there is no `package.json` to maintain.
- **DataHarmonizer embedded as an iframe** with a patched `window.dataHarmonizer`
  bridge (`getExportJson()` / `loadExportJson()`) so grid exports can be saved in
  the browser-held workspace and handed to Python for prepare/submit.
- **Schema editing delegated to the dhtb sidecar** over `postMessage`, rather than
  reimplementing a schema editor in the assistant.
- **Reads upload kept on the user's machine** so large files go straight from
  there to ENA — a deliberate data-path boundary. The local
  read-helper-app automates it; manual mode keeps the same boundary with no extra
  software, by generating the `webin-cli` command instead of running it.

### dataharmonizer-template-builder — Python backend, *React/TypeScript/Vite* frontend

- **Python/Django** backend, calling `linkml-lib` directly for the
  YAML ↔ editable-tables ↔ `schema.json` conversion and validation, with no
  intermediate wrapper modules of its own.
- **React + TypeScript + Vite** frontend — unlike the assistant — because this app is
  a genuinely interactive *editor*: it maintains real client-side state (tables,
  cross-references between classes/slots/enums, edit history, diagnostics, preview)
  and reuses DataHarmonizer's **Handsontable** grid through
  `@handsontable/react-wrapper`. That state-heavy, component-driven UI is exactly
  what React is good at, so the build toolchain earns its keep.

**The contrast in one line:** the assistant *hosts* finished DataHarmonizer grids
(no React needed — vanilla JS glue is enough), whereas the template builder *builds
and edits* schemas interactively (React + TS pays for itself).

---

## 6. External dependencies & their roles

| Dependency | Where | Role |
|---|---|---|
| **DataHarmonizer** (fork `v2.1.1-mimicc`) | assistant, dhtb, dh-builder | Browser spreadsheet editor/validator for metadata entry; the UI engine both apps embed |
| **Handsontable** 17.1.0 | inside DataHarmonizer and ena-browser; dhtb directly | The spreadsheet grid widget |
| **LinkML / linkml-runtime** (≥1.7 / ≥1.8) | via linkml-lib | Schema metamodel, validation and runtime used for all schema work |
| **Django** 5.x | dhtb | Backend framework: HTTP views, routing, static serving |
| **Pyodide** 314 (CDN) | assistant | CPython in the browser: runs the submission stack in a Web Worker |
| **nginx** | assistant | Static file server in the container |
| **React 18 / TypeScript / Vite 6** | dhtb frontend | Component UI, typing and build for the interactive schema editor |
| **Electron / Node** | read-helper-app | Native desktop app exposing Webin-CLI via loopback HTTP |
| **Java** | read-helper-app (user's machine) | Runtime for `webin-cli.jar` |
| **httpx** (≥0.27) | ena-api-client, toolkit | HTTP transport to ENA |
| **pydantic / pydantic-settings** (≥2) | ena-api-client, dhtb, assistant | Typed request/response models and env-based config |
| **lxml** (≥5) | toolkit, linkml-lib | SRA XML building and XSD validation |
| **Typer** | toolkit | CLI framework for `ena-submission-toolkit` |
| **PyYAML** (≥6) | linkml-lib | LinkML YAML parsing/dumping |
| **Browser storage** (IndexedDB, Cache Storage, sessionStorage) | assistant frontend | Workspace, reads resume ledger and schema library (IndexedDB); compiled grid schemas (Cache Storage, via a service worker); Webin credentials for the tab only (sessionStorage) |
| **Docker / docker-compose** | assistant, dhtb, dh-builder | Packaging and running the assistant and dhtb containers; building the DH bundle |
| **webin-cli** (`webin-cli.jar` GitHub release) | read-helper-app | ENA's official read-upload tool, run with local Java on the user's machine |
| **Node 20 / Yarn** | dh-builder | Build toolchain for the DataHarmonizer bundle |

---

## 7. Cross-project wiring

- **Sibling Python libraries are pinned git dependencies**, not submodules or
  vendored copies. The assistant's `pyproject.toml` pins
  `ena-api-client @ git+...@v0.1.0`, `linkml-lib @ ...@v0.1.0` and
  `ena-submission-toolkit @ ...@v0.1.0`; dhtb pins `linkml-lib` and `dh-builder-lib`
  the same way. Upgrades happen by bumping a tag.
- **ena-browser is vendored as a built artefact.** The assistant commits
  `dist/ena-browser.iife.js` + `.css` from a pinned release tag into
  `app/static/vendor/ena-browser/` and loads them with plain `<script>`/`<link>`
  tags. It depends on nothing else in the ecosystem — the only shared vocabulary is
  the Reports API field names (mirrored from `ena-api-client`'s models) and the ENA
  status values.
- **DataHarmonizer is built, not imported.** A Docker build stage clones the fork
  (`...DataHarmonizer.git#v2.1.1-mimicc`) and runs `dh-builder`'s build steps
  (Node/Yarn) to produce a bundle, which the assistant's image build copies into
  its static site at `/dh/`.
- **dhtb is a Docker Compose service; read-helper-app is not.** `docker-compose.yml`
  runs `mimicc-server` (:9000) and `dhtb` (:8765), both bound to loopback.
  read-helper-app is installed and started separately by the user as a desktop app
  (:9100). The browser reaches read-helper-app over cross-origin HTTP and dhtb over an
  iframe `postMessage` bridge; `config.json` tells the page where to find them
  (`helper_port`, `dhtb_url`).
- **Data flows over three channels:** Python imports (the assistant's in-browser
  Python and dhtb ↔ libraries), HTTP (browser ↔ static files, browser ↔
  read-helper-app, browser and read-helper-app ↔ ENA), and `postMessage` (assistant
  page ↔ dhtb iframe).

```
ena-api-client ──┐
                 ├─► ena-submission-toolkit ──► mimicc-ena-submission-assistant
linkml-lib ──────┤                                    │  │    │
   │             └────────────────────────────────────┘  │    │
   └──► dataharmonizer-template-builder ◄─ postMessage ──┘    │
                 │                                            │
            dh-builder ──builds──► DataHarmonizer (fork)      │
                                                              │
                              read-helper-app ◄──HTTP (browser)─┘
```

---

## 8. Where each part runs

Two execution locations for the assistant: **the browser** and, for reads upload,
**read-helper-app** on the user's machine. The container (or any static host) only
serves files. dhtb and dh-builder still run as containers.

| Project | Browser | read-helper-app (native, user's machine) |
|---|---|---|
| **mimicc-ena-submission-assistant** | SPA; workspace and schema library in IndexedDB, grid schemas in Cache Storage (service worker); credentials in sessionStorage; all ENA calls, prepare/submit, records, reads plan and schema import/compile in a Pyodide worker; orchestrates reads upload with the helper, or renders it as a `webin-cli` script | — |
| **ena-api-client** | All Webin Submission/Reports API calls (in the Pyodide worker) | — |
| **ena-submission-toolkit** | XML builders, XSD validation, records, DH-export prep (in the Pyodide worker) | — |
| **linkml-lib** | Schema compile/IO (in the Pyodide worker; also in dhtb's container) | — |
| **dhtb** | React editor in a cross-origin iframe (Django backend in its own container) | — |
| **dh-builder** | — (image build of the assistant; preview rebuilds inside dhtb) | — |
| **DataHarmonizer** | Metadata grids (a static bundle) | — |
| **ena-browser** | Record grids (a static file) | — |
| **read-helper-app** | Status page; driven by the SPA via `fetch`/`EventSource` | Read scanning, in-memory credentials, `webin-cli.jar` via Java, upload of read files to ENA |

**Data paths:** Webin credentials are held in the browser tab and go to ENA from
the page's Python and to read-helper-app (in memory); in manual mode they reach
webin-cli only through the user's own shell environment, and are never written into
the generated script. Metadata/XML goes browser → ENA. Read files go read-helper-app
→ ENA (or user's terminal → ENA); the page only sees read *file names* and upload
outcomes.

---

## Historical lineage

`ena-submission-dataharmonizer` (the original monolithic toolkit/predecessor) and
`ena-dh-scripts` (an intermediate extraction, now legacy) were both superseded by
`ena-submission-toolkit`; they are kept only for history and are not part of the
active ecosystem documented above.
