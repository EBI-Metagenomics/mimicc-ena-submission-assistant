# Plan: make the assistant a static, browser-side app

> **Status: all six phases built.** Each phase has a "Built" note recording what
> changed from the sketch. Paths below predate the final rename of `server/` to
> `app/`.

Goal: remove the application server. Keep the Python. Remove session
management. What remains should be a directory of files any static host can
serve.

The headline finding is that this is **feasible**, for one reason that was not
obvious before measuring it:

## 0. The two facts this whole plan rests on

### 0.1 ENA's submission APIs are CORS-enabled

Probed 2026-09-16 against both environments:

| Endpoint | Preflight | `access-control-allow-origin` | Notes |
|---|---|---|---|
| `wwwdev.ebi.ac.uk/ena/submit/webin-v2/submit` | 200 | echoes `Origin` | `allow-methods: POST`, `allow-headers: authorization, content-type`, `allow-credentials: true` |
| `www|wwwdev.ebi.ac.uk/ena/submit/report/*` | 200 | echoes `Origin` | `allow-methods: GET`, `allow-headers: authorization` |
| `www.ebi.ac.uk/ena/browser/api/xml/*` | 200 | `*` | `max-age: 1800` |
| `www.ebi.ac.uk/ena/portal/api/search` | 200 (GET: `authorization`; POST: `content-type`) | `*` | **HEAD returns 403 with no CORS headers** — don't probe it with `curl -I` (an earlier draft of this plan did, and wrongly concluded the Portal had no CORS) |

So the browser can submit XML, list records, run lifecycle actions, read a
record's current XML and query the Portal **directly**, with Basic auth, no
proxy. Credentials stop
passing through an app server entirely, which is a security improvement over
today's `X-Webin-*` headers.

Reproduce with:

```bash
curl -s -o /dev/null -D - -X OPTIONS -H 'Origin: https://example.org' -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: authorization,content-type' https://wwwdev.ebi.ac.uk/ena/submit/webin-v2/submit | grep -i access-control
```

### 0.2 `WebinClient` already accepts a transport override

`ena_api/client.py`:

```python
def __init__(self, config=None, *, timeout=120.0, transport: httpx.BaseTransport | None = None)
```

`httpx` is pure Python and runs under Pyodide; only its socket-based transport
does not. A ~40-line `httpx.BaseTransport` subclass backed by the browser's
`fetch` makes **the entire existing Python submission stack work unmodified** —
`records.py` (41 KB), `submit_sample.py` (17 KB), `submit_study.py` (13 KB),
`common.py` (22 KB) — no port, no re-test, no new bugs.

That is the minimal-diff path, and it is what this plan takes.

---

## 1. Target shape

```
dist/                        ← any static host; no application server
  index.html                 ← unchanged shell
  *.js                       ← app scripts, minus the /api/* clients
  py/                        ← Pyodide runtime + wheels (cached after first load)
  ena_bridge.py              ← NEW: fetch-backed httpx transport + the 12 call sites
  sw.js                      ← NEW: service worker, serves /templates/*/schema.json
  dh/                        ← built DataHarmonizer bundle (build-time, unchanged)
  vendor/ena-browser/        ← unchanged
  schemas/, assets/ena_schema/ ← fetched lazily into the Pyodide FS
```

`server/` is deleted: `views_core.py`, `views_records.py`, `views_schemas.py`,
`webin_creds.py`, `config/`, `manage.py`, Django, gunicorn. `ena_service.py`,
`schema_service.py`, `read_assign.py` and `_bootstrap.py` **survive as Python
modules loaded into Pyodide** — they are already framework-free.

Serving is still an *origin* (`python -m http.server`, nginx, GitHub Pages,
S3). `file://` does not work: the DataHarmonizer iframe, the service worker and
Pyodide's fetches all need a real origin.

---

## 2. Phases

### Phase 1 — remove session management (independent of everything else; do first)

Sessions are already browser-only (`sessions.js`, IndexedDB). What goes is the
*concept*: the modal, the `body.no-session` gate, the create/open/delete list,
and the snapshot/restore of every field, log and result table.

Replace with **one implicit workspace**: a single IndexedDB record, written by
the same debounced `scheduleSave()`, restored on load with no prompt.

- Delete: `openSessionModal`, `loadSessionList`, `createSession`, `openSession`,
  `dbCreateSession`, `dbListSessions`, `sessionMeta`, `resetToBlank`,
  `applyState`, `captureInitialDefaults`, `INITIAL_DEFAULTS`, the session chip,
  and the `#sessionModal` markup. ~250 of `sessions.js`'s 441 lines.
- Keep: `dbSaveState`/`dbSaveDhExport` against a fixed id (`"workspace"`), the
  grid-layout persistence (`collectGrids`/`applySavedGridLayout`), and
  download/import-as-JSON — that stays the only durability story. Phase 5
  extends the export with the schema library.
- **The one thing sessions genuinely bought:** the reads resume ledger keys
  runs by `session_run_alias(session_name, run_name)`
  (`views_records.py:61`), and that stable alias is what lets a resume detect
  "already in ENA". Replace the session name with a plain
  **"submission prefix"** text field on the Reads tab, persisted in the
  workspace record. Same alias, same resumability, no session object.
- `SESSION &&` guards in `dataharmonizer.js` (6 sites) become unconditional.

**Cost:** no more side-by-side named submissions in one browser profile.
Anyone who needs that uses the existing export/import JSON. Say so in the README.

### Phase 2 — the Python bridge

> **Built (2026-09-16).** Measured against the real stack; three corrections to
> the design below:
>
> - **Sync XHR in a module Web Worker, not `fetch`.** `WebinClient` and
>   `ENAClient` are *synchronous* `httpx.Client`s, so an async `fetch`/`pyfetch`
>   cannot sit under them. `FetchTransport` (`server/pyodide/ena_bridge.py`) uses
>   synchronous `XMLHttpRequest`, which workers allow with binary bodies and
>   without blocking the page. Pyodide 314 refuses classic workers
>   (`importScripts` reports it as a network error), so it is `type: "module"`.
> - **No per-endpoint wrappers.** `ena_bridge.call("module.function", kwargs)`
>   calls the existing `ena_service` / `read_assign` / `schema_service` functions
>   directly (module allowlist, `creds` dict → `Credentials`). The page calls
>   `py(target, kwargs)` (`core.js`).
> - **`linkml` needs four mock packages.** `watchdog`, `antlr4-python3-runtime`,
>   `pytest-logging` and `cfgraph` have no pure-Python wheel; they serve ShEx and
>   file watching, which the app never imports. `micropip.add_mock_package`
>   gets past them. Everything the app uses imports: `linkml.validator`, all of
>   `linkml_lib`, every toolkit submit module, `records`, `portal`, lxml
>   `XMLSchema`.
>
> Cold load (Pyodide + packages + PyPI wheels) is **~9 s**, not 10–30 s. The
> pinned EBI git packages and the server modules ship as `server/static/py/app.zip`
> (`task build:py`, gitignored). The `pendulum` shim is
> `server/pyodide/shims/pendulum.py`, checked against real `pendulum` in
> `tests/test_ena_bridge.py`.

New `static/py/ena_bridge.py`, loaded into Pyodide:

1. `class FetchTransport(httpx.BaseTransport)` — `handle_request` →
   `pyodide.http.pyfetch` (or `js.fetch` via `to_js`), returning
   `httpx.Response`. Basic auth is applied by `httpx.Client(auth=...)` above
   the transport, so it needs nothing special.
2. Install it globally, before any client is built:
   `httpx.HTTPTransport = httpx._client.HTTPTransport = FetchTransport`
   (the constructor must accept and ignore `retries=`). Two HTTP clients sit in
   the stack and neither needs editing: `WebinClient` (`ena_api`) builds its
   default transport through `httpx._client`, and `ENAClient`
   (`ena_api_handler`, used by `ena_submission_toolkit/portal.py`) hard-codes
   `httpx.HTTPTransport(retries=...)` with no transport parameter at all. One
   patch covers both; no per-client monkeypatching.
3. Thin `async` JS-facing wrappers for exactly the twelve operations the UI
   calls today (§4 table). They take/return plain dicts, so `core.js`'s `api()`
   is replaced by a `py()` that calls into Pyodide instead of `fetch`.

Deps: Pyodide ships `lxml`, `pydantic`/`pydantic-core`, `PyYAML`; `httpx`,
`typer`, `linkml`, `linkml-runtime` and friends install from PyPI wheels via
`micropip` (all pure Python).

**`pendulum` is the one blocker:** 3.2.0 ships a Rust extension
(`_pendulum.cpython-312-*.so`) with no Pyodide wheel. It has 7 call sites in
the toolkit, all date formatting/parsing (`pendulum.now().format(...)`,
hold-until validation). Ship a ~25-line stdlib `pendulum` shim on the Pyodide
path before `micropip` resolves it. (Upstream fix: make the toolkit use
`datetime`; not required here.)

### Phase 3 — move the pure endpoints

> **Built (2026-09-16).** `reads/group`, `reads/plan`, `reads/result`,
> `sample/prepare` and `study/prepare` are gone from Django; the page calls
> `read_assign.group_files`, `ena_service.plan_reads`,
> `read_assign.upload_result`, `ena_service.prepare_sample_records` and
> `ena_service.prepare_study_records` through `py()`. The view glue (resume
> planning, result shaping) moved into those modules. Files a call reads are
> passed as `py(target, kwargs, { fsPath: url })` and fetched into Pyodide's
> filesystem per call; `/schemas/*` is now served for that. The Docker image
> builds `app.zip`.
>
> **Not done: `/api/health` → `config.json`.** Half its payload is deploy
> configuration from the environment (`HELPER_PORT`, `DHTB_URL`) and the rest is
> derived at request time, so a static file needs a build/deploy step to write
> it — that belongs with the `dist/` build in Phase 6, not here.

These touch no network and no filesystem. Under Pyodide they move for free:

| Endpoint | Python behind it | Notes |
|---|---|---|
| `/api/reads/group` | `read_assign.group_files` | pure; 40 lines |
| `/api/reads/result` | `read_assign.parse_accessions` | two regexes |
| `/api/reads/plan` | `build_manifest_text` + one ENA run lookup | the lookup goes via Phase 2 |
| `/api/sample/prepare` | `dh_data.filter_columns` + `prepare_dh_output.prepare_data` | needs `linkml` (validator) — see §5.1 |
| `/api/study/prepare` | `prepare_dh_output.prepare_data` | dict key renaming only |
| `/api/health` | — | becomes a static `config.json` (`dhtb_url`, `helper_port`) |

### Phase 4 — move the ENA-talking endpoints

> **Built (2026-09-16).** `views_records.py` and `webin_creds.py` are deleted;
> no ENA request and no Webin credential reaches the Django server any more.
> The page calls `ena_service.{submit_studies, submit_samples, list_records,
> read_editable_fields, preview_modify_records, modify_records, run_action,
> suggest_samples}` through `enaPy()` (`core.js`), which adds `creds` + `test`
> and fails fast without credentials. `reads/suggest` was the only view with
> glue (now `ena_service.suggest_samples`); `MODIFY_ALIAS` moved into
> `ena_service` as the default. Submits fetch their XSDs (`/assets/ena_schema/`,
> now served) and `mimicc_sample.yaml` into the worker per call. A real-Pyodide
> test prepares, XSD-validates and POSTs a sample; the only ENA URL it touches
> is `webin-v2/submit`.

`study/submit`, `sample/submit`, `records/<entity>`, `records/<entity>/fields`,
`records/modify{,/preview}`, `records/action`, `reads/suggest`. Each is already
a two-line view over an `ena_service` function; with Phase 2 in place the JS
calls the same function directly. `webin_creds.py` disappears — credentials are
handed to `WebinClient` in-process.

### Phase 5 — the schema library and the DataHarmonizer bundle

> **Built (2026-09-16).** `views_schemas.py` and every `/api/schemas*` route are
> gone. As planned: the library is an IndexedDB store (`schemas`, DB version 2);
> select compiles in Python (`schema_service.compile_for_grid`) and the page
> writes Cache Storage `dh-templates`; `static/sw.js` (served at `/sw.js`, so its
> scope covers `/templates/` and `/dh/`) serves it cache-first; the workspace
> records `grid_schemas`; load checks each entry's `x-compiler-version` /
> `x-schema-id` tags and recompiles or drops; delete reverts the grid; workspace
> download/import carries `schemas` + `grid_schemas`; `navigator.storage.persist()`
> with a header warning when refused. Differences from the sketch:
>
> - **No `_bootstrap.schemas_dir()` shim.** `schema_service` became pure
>   (`describe_schema`, `import_build`, `compile_for_grid`); JavaScript owns the
>   library and hands Python the YAML it needs as `py()` file *data*
>   (`{ fsPath: { data } }`), which also covers file uploads.
> - **Directory listings became committed indexes.** `schemas/index.json` (with
>   each schema's name/title/description, so seeding and the dropdowns need no
>   Python) and `assets/ena_schema/index.json`, written by
>   `scripts/build_static_indexes.py` and checked for drift by a test.
> - **The compiler version is `static/py/versions.js`**, an ES module shared by the
>   worker and the page, alongside the Pyodide/package pins.
> - The `mimicc-schemas` Docker volume and `SCHEMAS_CONTAINER_DIR` are removed;
>   selections no longer touch the shared DH bundle, so they are per browser.

This is the only genuinely server-shaped part, because
`schema_service.select_for_grid_result` **writes `schema.json` into a folder
DataHarmonizer then fetches over HTTP** (`/templates/<folder>/schema.json`).

Approach: a **service worker** owning `/templates/*` and `/dh-template-registry.json`.

1. Schema library (`list/read/save/delete/import`) moves to IndexedDB, keyed by
   the same slug `schema_service._slugify` produces. `_bootstrap.schemas_dir()`
   is replaced by an IndexedDB-backed shim; the seeding logic
   (`_ensure_seeded`) fetches the six committed `schemas/*.yaml` on first run.
2. "Select schema for grid" runs `dataharmonizer_compile.compile_schema_json`
   in Pyodide (unchanged), then the page writes the result straight into Cache
   Storage: `caches.open("dh-templates")` →
   `put("/templates/<folder>/schema.json", new Response(json))`, plus the
   `schema.yaml` alongside it. The service worker serves `/templates/*`
   cache-first, falling back to the network (the bundle's built defaults). Not
   `postMessage` to the SW: a service worker is killed when idle and loses
   anything held in memory, so the compiled schema would vanish after a browser
   restart. Cache Storage survives it, and the SW reads it without Pyodide — a
   returning user's grids load with no 10–30 s Python start. The iframe reload
   that already follows picks it up, as today.
   Tag each cached entry with the `linkml-runtime` version that compiled it;
   on mismatch, recompile from the stored YAML. Call
   `navigator.storage.persist()` once, so the browser does not evict the
   library under storage pressure.
3. `assets/ena_schema/` (5.1 MB) is fetched lazily into the Pyodide FS — only
   the one XSD a validation needs, and only the checklist XMLs an import names.
   Do not preload it.
4. The DH bundle itself stays a **build-time** Node/Yarn artefact
   (`Dockerfile` dh-builder stage). That container becomes build-only: its
   output is committed or published as a release asset, the way
   `vendor/ena-browser` already is. No runtime server.

#### Schema persistence across browser sessions

Custom schemas must survive a browser restart with no user action. Three
stores, each written at the moment its data changes:

| What | Where | Written when | Replaces |
|---|---|---|---|
| Schema library (LinkML YAML) | IndexedDB `schemas` store, keyed by `_slugify(name)` | save, import, delete | `/schemas` Docker volume |
| Compiled grid schema | Cache Storage `dh-templates`, at `/templates/<folder>/schema.json` (+ `schema.yaml`) | select-for-grid | `schema.json` written into the bundle folder |
| Role → schema id | the Phase 1 workspace record, `grid_schemas: {sample, experiment, study}` | select-for-grid | nothing (implicit in the bundle folder today) |

Restore needs no code path of its own: the service worker serves
`/templates/*` cache-first, so the iframe's ordinary fetch returns the user's
schema, and the network fallback returns the bundle's built default for a grid
never customised. The registry needs no storage — the class names are fixed
(`ROLE_TEMPLATE_CLASSES`). Returning users do not start Pyodide just to see
their grids; it loads only on import or select.

On load:

1. `navigator.storage.persist()` once (idempotent), so the browser does not
   evict IndexedDB/Cache Storage under storage pressure. Show a one-line warning
   if it is refused.
2. For each cached compiled schema, compare its stored `linkml-runtime`
   version (an `x-compiler-version` header on the cached `Response`) with the
   deployed one. On mismatch, recompile from the IndexedDB YAML named in
   `grid_schemas` and overwrite the cache entry. If that YAML is gone, delete the
   cache entry so the grid falls back to the built default rather than serving
   stale output.
3. Deleting a schema that a grid currently uses also drops that grid's cache
   entry and `grid_schemas` key, reverting it to the default.

Portability: the workspace export/import JSON (Phase 1) gains a `schemas` array
(id + YAML) and `grid_schemas`. Import writes the library, then recompiles each
selected schema into the cache — so moving machines, or recovering from cleared
site data, is one file.

Tests (`test_ui.py`): select a custom schema, reload the page (new browser
context sharing storage state), assert the grid renders the custom column set
without the select step being repeated; and assert that deleting the selected
schema reverts the grid to the built default. Analogous test for each of the
three grids.

### Phase 6 — tests and docs

> **Built (2026-09-16).** Django is gone: `server/config/`, `views_core.py`,
> `manage.py`, `scripts/server_entrypoint.sh`, Django/gunicorn from `pyproject.toml`.
>
> - **The static build is `scripts/build_dist.py` → `dist/`**, keeping the URL layout
>   the page already used (`/static/`, `/sw.js`, `/dh/`, `/templates/`, `/schemas/`,
>   `/assets/ena_schema/`), so no JavaScript paths changed. `config.json` replaces
>   `/api/health` (the deferred Phase 3 item): `helper_port`/`dhtb_url` from the
>   build's environment, the rest computed at build (`editable_columns` from the
>   toolkit, bundle and ena-browser availability).
> - **Docker**: a `site-builder` stage runs `build_dist.py` with `${HELPER_PORT}` /
>   `${DHTB_URL}` placeholders; the runtime is `nginx:1.29-alpine`, where
>   `docker/write-config.sh` `envsubst`s them at start. The DH bundle is baked in;
>   the `mimicc-dh-bundle` volume and its seeding entrypoint are gone.
> - **Tests**: `conftest.py` builds `dist/` once and serves it with
>   `scripts/serve_dist.py` (a no-cache `http.server`, also `task serve`). UI tests
>   were already mocking at the `py()` boundary (Phases 3–5), not at `fetch`.
>   `test_server.py` became `test_dist.py` (layout, `config.json`, no-cache, no API).
> - README's accounts/Postgres/`DEPLOYMENT_MODE` sections are deleted, not ported;
>   ECOSYSTEM §8 is two columns.
> - **Not done: renaming `server/`.** It holds the app's source (page + Python) and
>   no server; renaming touches every path in the repo, so it is left for its own
>   change.

- `tests/test_ui.py` (Playwright, in-process WSGI + mocked `ena_service`) needs
  a new fixture: serve `dist/` with `http.server` and mock at the **fetch**
  layer (`page.route("**/ena/submit/**")`) instead of mocking a Python module.
  The assertions about UI wiring survive; the transport under them changes.
- `tests/test_server.py` and `tests/test_schema_api.py` test Django views that
  will not exist. The Python they cover still exists — retarget them at
  `ena_service` / `schema_service` directly with a stub `httpx` transport
  (which is what `WebinClient(transport=...)` was built for).
- `test_prepare.py`, `test_read_assign.py`, `test_records_enrichment.py`,
  `test_schema_service.py` are already module-level — unchanged.
- `README.md` still documents a `DEPLOYMENT_MODE` local/hosted split with
  Postgres accounts that `server/config/settings.py` says does not exist. That
  section is already stale; delete it rather than port it.
- `ECOSYSTEM.md` §8 "Where each part runs" collapses to two columns.

---

## 3. Recommended order

Phase 1 ships alone and is worth doing regardless. Phase 2 is the gate for 3–4.
Phase 5 is separable and can lag — until it lands, keep a build-time script
that compiles the default schemas into the bundle and accept that schema
*selection* is temporarily a rebuild.

---

## 4. Endpoint disposition

| Today | After | Mechanism |
|---|---|---|
| `GET /api/health` | static `config.json` | build-time |
| `POST /api/study/prepare` | browser | Pyodide, pure |
| `POST /api/study/submit` | browser → ENA | Pyodide + fetch transport |
| `POST /api/sample/prepare` | browser | Pyodide, pure (+`linkml`) |
| `POST /api/sample/submit` | browser → ENA | Pyodide + fetch transport |
| `GET /api/{study,sample}/list` | browser → ENA | Reports API, CORS OK |
| `GET /api/records/<entity>` | browser → ENA | Reports API (+ Browser API and, on production, Portal API for `full_fields`) |
| `POST /api/records/<entity>/fields` | browser → ENA | Browser API, CORS `*` |
| `POST /api/records/modify{,/preview}` | browser → ENA | Browser API read + Submit POST |
| `POST /api/records/action` | browser → ENA | Submit POST |
| `POST /api/reads/group` | browser | pure |
| `POST /api/reads/suggest` | browser → ENA | Reports API + pure matching |
| `POST /api/reads/plan` | browser → ENA | pure + one Reports lookup |
| `POST /api/reads/result` | browser | pure |
| `GET/POST /api/schemas*` | browser | IndexedDB + Pyodide compile |
| `POST /api/schemas/select` | browser | Pyodide compile → service worker |
| `GET /static,/dh,/templates` | static host (+ SW for `/templates`) | — |

Reads upload is unaffected: it already goes browser → read-helper-app → ENA, or
browser → generated `webin-cli` script → ENA. The server never touched read
files.

---

## 5. What does not move, and what it costs

### 5.1 Cost: first-load weight and time
Pyodide core is ~10–15 MB; `lxml` + `pydantic` + `linkml`/`linkml-runtime` and
their pure-Python dependency tree (`rdflib`, `SPARQLWrapper`, `antlr4`,
`curies`, `prefixmaps`, …) realistically push the first load to **~40–60 MB and
10–30 s**, cached thereafter. `linkml` proper is pulled in by exactly one
module — `linkml_lib/dh_data.py`, for the sample-filter validator — and
`linkml_runtime` by `dataharmonizer_compile`. If the load time proves
unacceptable, loading those two lazily (only when the user prepares samples or
selects a schema) keeps the common path light.

### 5.2 Cannot move: the DataHarmonizer bundle build
Node 20 / Yarn against the DataHarmonizer fork. Stays a build-time Docker
stage; its output is static. Not a runtime server, but it is still a build
machine someone has to run.

### 5.3 Moves, with caveats: ENA Portal enrichment on production
`ena_submission_toolkit/portal.py` backs the production half of the **All
fields** toggle (~200 indexed fields). The Portal API is CORS-enabled for GET
and POST (§0.1), so it moves browser-side with the rest, via the global
transport patch (Phase 2). Caveats:

- **`ACAO: *`, not an echoed origin.** Fine for an explicit `Authorization`
  header — the preflight lists `authorization` by name — but the
  `FetchTransport` must call `fetch` with the default `credentials: "same-origin"`,
  never `"include"`; a wildcard origin rejects credentialed-mode requests.
- **Private records need that header.** Portal returns private records only to
  Webin Basic auth; `portal.py` already passes the credentials, so this is
  unchanged — just verify it against a private production record in the
  compose/Playwright pass.
- **Query length.** `fields_for_accessions` chunks accessions into OR queries
  (50 per request). If a GET URL ever exceeds limits, the Portal also accepts
  form-encoded POST (preflight allows `content-type`).
- **Never HEAD.** HEAD is the one method answered with 403 and no CORS headers.

Re-probe all four APIs (§0.1 commands, using GET/OPTIONS) in CI or before
each release: CORS is ENA's configuration, not a contract, and this app would
break outright if it were withdrawn.

### 5.4 Cannot move (without a rewrite): the dhtb schema editor
`dataharmonizer-template-builder` is a separate Django + React + Vite app with
its own runtime bundle-preview step. It is already loaded cross-origin from
`DHTB_URL`, so it is not *this* app's server — but "no servers at all" means
dropping the Schema tab's editor iframe, or pointing it at a hosted dhtb
instance. Recommend: keep it optional, driven by `config.json`; the app works
without it (schemas can be imported from YAML/XML/XSD without the editor).

### 5.5 Cost: `pendulum`'s Rust extension
See Phase 2. ~25 lines of shim, or an upstream `datetime` change.

### 5.6 Cost: no server-side record of anything
No logs, no audit trail, no shared state between machines. Today's server is
already stateless, so this changes less than it sounds — but the browser
profile becomes the single copy of the workspace **and of the schema library**
(today that lives in a Docker volume, which outlives a browser). Private windows
lose everything on close; clearing site data deletes the library. The export/import JSON is the
whole backup story, and Phase 1 makes it more prominent, not less.

### 5.7 Cost: tests
`test_server.py` + `test_schema_api.py` (~25 KB) lose their subject and must be
retargeted (§ Phase 6). This is the largest single chunk of throwaway work in
the plan.

---

## 6. The alternative that was rejected

Rewriting the Python in JS — `submit_sample.py`, `submit_study.py`,
`records.py`, `common.py` are ~95 KB of checklist rules, unit normalisation,
XSD validation and MODIFY-patching logic. `group_files`, `parse_accessions` and
`prepare_data` are genuinely trivial in JS (~80 lines total), but the
submission core is not, and it is the part where a subtle bug submits wrong
data to a public archive. Pyodide buys reuse of code that is already tested,
at the price of one transport shim and a slow first load. Take that trade.

If the first-load cost turns out to be unacceptable in practice, the fallback
is a **hybrid**: JS for the four trivial endpoints and all the plain REST
calls, Pyodide loaded on demand only for sample/study XML build + validate and
schema compilation. That is strictly more code, so do not start there.
