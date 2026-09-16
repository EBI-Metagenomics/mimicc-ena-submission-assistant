// ---------------------------------------------------------------------------
// Python worker: Pyodide + the app's submission stack, off the main thread.
// Messages in:  { id, target: "module.function", kwargs, files }
// Messages out: { id, result } | { id, error }
// A worker (not the page) because the httpx transport is synchronous XHR —
// see server/pyodide/ena_bridge.py. A *module* worker, because Pyodide 314
// refuses to start in a classic one.
// ---------------------------------------------------------------------------

import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

// ponytail: Pyodide from the CDN, pinned; self-host under /static/py/ if offline use matters.
const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";

// linkml declares these but the app never imports them (ShEx parsing, file
// watching, a test plugin), and none has a pure-Python wheel. Registering them
// as already installed lets micropip resolve the rest.
const MOCKED_PACKAGES = [["watchdog", "6.0.0"], ["antlr4-python3-runtime", "4.9.3"], ["pytest-logging", "2015.11.4"], ["cfgraph", "0.2.1"]];
// Versions match uv.lock, so the browser runs what the Python tests ran.
// Pyodide supplies lxml, pydantic, PyYAML, httpx and jsonschema itself.
const PYPI_REQUIREMENTS = ["linkml==1.11.1", "linkml-runtime==1.11.1", "typer==0.26.8", "pydantic-settings==2.14.2"];

async function boot() {
  const pyodide = await loadPyodide({ indexURL: PYODIDE_URL });
  await pyodide.loadPackage(["micropip", "httpx", "lxml", "pydantic", "pyyaml"]);
  const bundle = await fetch(new URL("app.zip", self.location.href));
  if (!bundle.ok) throw new Error(`app.zip: HTTP ${bundle.status} — run scripts/build_py_bundle.py`);
  pyodide.unpackArchive(await bundle.arrayBuffer(), "zip", { extractDir: "/app" });
  await pyodide.runPythonAsync(`
import sys
sys.path.insert(0, "/app")
import micropip
for name, version in ${JSON.stringify(MOCKED_PACKAGES)}:
    micropip.add_mock_package(name, version)
await micropip.install(${JSON.stringify(PYPI_REQUIREMENTS)})
import ena_bridge
ena_bridge.install()
`);
  return { pyodide, call: pyodide.pyimport("ena_bridge").call };
}

const ready = boot();

/** Put the files a call reads into Pyodide's filesystem first ({ fsPath: url }).
 *  Fetched every time — a selected schema changes under the same URL. A file
 *  the server doesn't have is removed, so Python raises its own "not found". */
async function provide(pyodide, files) {
  for (const [path, url] of Object.entries(files || {})) {
    const res = await fetch(url, { cache: "no-store" });
    if (res.ok) {
      pyodide.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")) || "/");
      pyodide.FS.writeFile(path, new Uint8Array(await res.arrayBuffer()));
    } else {
      try { pyodide.FS.unlink(path); } catch { /* was never there */ }
    }
  }
}

self.onmessage = async ({ data: { id, target, kwargs, files } }) => {
  let bridge;
  try {
    bridge = await ready;
  } catch (e) {
    self.postMessage({ id, error: `Python runtime failed to load: ${e.message || e}` });
    return;
  }
  try {
    await provide(bridge.pyodide, files);
    const out = JSON.parse(bridge.call(target, JSON.stringify(kwargs || {})));
    self.postMessage(out.error !== undefined ? { id, error: out.error } : { id, result: out.result });
  } catch (e) {
    self.postMessage({ id, error: e.message || String(e) });
  }
};
