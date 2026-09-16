"use strict";

// ---------------------------------------------------------------------------
// Boot: runs last, after every other script has defined its globals. Holds
// the only top-level statements that immediately call across files — the
// theme bootstrap (reaches postToDhtb) and init() (reaches nearly every
// section).
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
async function init() {
  await startApp();
}

async function startApp() {
  initTheme();               // stamp <html data-theme> before anything paints
  restoreCreds();            // pull Webin creds saved for this browser tab (if any)
  await refreshHealth();
  templateWorkerReady();     // register sw.js early: it serves the grids' selected schemas
  requestPersistentStorage(); // keep the library + cached grid schemas from eviction
  initDhFrames();            // point both DH iframes at explicit ?template= paths
  refreshSchemaList();       // schema library + the Samples/Reads grid selectors
  refreshEnaSources();       // bundled ENA checklist/XSD options for "Build a new schema"
  initSchemaEditorFrame();   // point the Schema tab's editor iframe at the dhtb sidecar
  await restoreWorkspace();  // the one implicit workspace, no prompt
  window.GRID_SCHEMAS_RESTORED = restoreGridSchemas(); // recompile/drop cached grid schemas only if stale
}
init();
