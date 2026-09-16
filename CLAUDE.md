# CLAUDE.md

Guidance for AI agents working in this repo. Read this before changing UI code.

**There is no application server.** The app is a static site (`scripts/build_dist.py`
→ `dist/`) whose Python runs in the browser (Pyodide). Do not add HTTP endpoints:
new behaviour is a Python function called through `py()`/`enaPy()`, or plain JS. The
`server/` directory name is historical. After editing `server/`, rebuild (`task serve`).

## Testing — this project HAS a Playwright + docker-compose test suite

Do not re-invent it. It lives in `tests/` and runs via `task` (see `Taskfile.yml`):

- `task test` — full pytest suite (Python unit tests + the UI suite, no Docker).
  Run this for any Python or build change.
- `task test:ui` — fast Playwright UI suite (`tests/test_ui.py`): `dist/` built
  once per session and served in a thread (`tests/conftest.py` `live_server_url`),
  with `py()` stubbed. Use for anything that can be exercised without the real
  DataHarmonizer bundle or the `dhtb` sidecar.
- `task test:compose` — Playwright against the **real** `docker compose` stack
  (`tests/test_compose_ui.py`, gated on `COMPOSE_TEST=1`). Builds the image and
  starts the containers, so it's slow (minutes) but it's the only test that
  exercises the built DH bundle, the fixed template folders, and the real
  cross-origin `dhtb` iframe. See README "Docker Compose tests".

**Python in the browser.** Calls the page makes through `py()` (a Pyodide
worker, `server/static/py/worker.js`) are stubbed in `test_ui.py` with
`_stub_py` and asserted with `_py_calls` — never mock them at the HTTP layer.
The logic behind them is tested as plain Python (`test_read_assign.py`,
`test_ena_bridge.py`); the few `*_in_a_browser_worker` tests run real Pyodide.

**Rule:** any change under `server/static/*` (tabs, DataHarmonizer panels, JS)
or to the `Dockerfile` dh-builder stage MUST add or extend a Playwright test —
`test_ui.py` for mockable behaviour, `test_compose_ui.py` for anything needing
the real bundle / sidecar. The three submission grids (Studies, Samples, Reads)
are parallel: a test for one usually has an analog for the others.

**Record grids are `ena-browser`'s, not ours.** Filtering, sorting, pinning,
selection and cell-edit mechanics have their own Playwright suite in that repo —
do not re-test them here. Tests in this repo assert the *wiring*, through the
element's public API: `getRows`, `getVisibleRows`, `getSelection`,
`getChangeSet`, `getLayout`, `getFilters`. Never assert on Handsontable
internals or `.ht*` classes. Two unavoidable exceptions, both because the pinned
column's clickable copy is the `.ht_clone_inline_start` overlay rather than the
master table: clicking a row-action button, and reading a custom column's badge
(`reads_assigned` has no public getter). Nothing else may reach for them.

## DataHarmonizer template folders

Each submission role is bound to a **fixed** DH template folder
(`server/schema_service.py` `ROLE_FOLDERS`): sample→`mimicc`,
experiment→`mimicc_experiment`, study→`study`. Selecting a schema from a tab's
dropdown compiles it in the browser's Python and caches it at that folder's
`/templates/<folder>/schema.json`, where the service worker (`static/sw.js`)
serves it over the bundle's default, then reloads the grid — the folder must
already exist in the built bundle. Anything that changes compiled output means
bumping `COMPILER_VERSION` in `static/py/versions.js`. New role or new fixed
folder ⇒ add a build step to the `Dockerfile` dh-builder stage AND a startup
branch in `server/static/dataharmonizer.js` `initDhFrames()`.
