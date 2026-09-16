// ---------------------------------------------------------------------------
// Python worker: Pyodide + the app's submission stack, off the main thread.
// Messages in:  { id, target: "module.function", kwargs, files }
// Messages out: { id, result } | { id, error }
// A worker (not the page) because the httpx transport is synchronous XHR —
// see server/pyodide/ena_bridge.py. A *module* worker, because Pyodide 314
// refuses to start in a classic one.
// ---------------------------------------------------------------------------

import { MOCKED_PACKAGES, PYODIDE_URL, PYPI_REQUIREMENTS } from "./versions.js";

async function boot() {
  // Imported here, not at the top: a module worker with top-level await could
  // miss messages posted before its onmessage handler exists.
  const { loadPyodide } = await import(PYODIDE_URL + "pyodide.mjs");
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

/** Put the files a call reads into Pyodide's filesystem first: { fsPath: url }
 *  fetches (every time — a selected schema changes under the same URL), and
 *  { fsPath: { data } } writes text or bytes the page already holds. A URL the
 *  server doesn't have removes the file, so Python raises its own "not found". */
async function provide(pyodide, files) {
  for (const [path, source] of Object.entries(files || {})) {
    const dir = path.slice(0, path.lastIndexOf("/")) || "/";
    if (typeof source !== "string") {
      pyodide.FS.mkdirTree(dir);
      pyodide.FS.writeFile(path, source.data);
      continue;
    }
    const res = await fetch(source, { cache: "no-store" });
    if (res.ok) {
      pyodide.FS.mkdirTree(dir);
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
