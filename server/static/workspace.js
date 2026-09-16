"use strict";

// ---------------------------------------------------------------------------
// Workspace store — IndexedDB (single-user, local-only; no backend). One
// implicit record holds the full UI snapshot, the three DataHarmonizer
// exports and the reads resume ledger. It is restored on load with no prompt.
// ---------------------------------------------------------------------------
const DB_NAME = "mimicc";
const DB_STORE = "sessions";   // store name kept so existing browser data stays readable
const WORKSPACE_ID = "workspace";
let _dbPromise = null;

function idbOpen() {
  if (_dbPromise) return _dbPromise;
  _dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(DB_STORE)) db.createObjectStore(DB_STORE, { keyPath: "id" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return _dbPromise;
}
function idbReq(request) {
  return new Promise((res, rej) => { request.onsuccess = () => res(request.result); request.onerror = () => rej(request.error); });
}
async function idbGet(id) {
  const db = await idbOpen();
  return idbReq(db.transaction(DB_STORE, "readonly").objectStore(DB_STORE).get(id));
}
async function idbAll() {
  const db = await idbOpen();
  return idbReq(db.transaction(DB_STORE, "readonly").objectStore(DB_STORE).getAll());
}
async function idbPut(record) {
  const db = await idbOpen();
  const tx = db.transaction(DB_STORE, "readwrite");
  tx.objectStore(DB_STORE).put(record);
  return new Promise((res, rej) => { tx.oncomplete = () => res(record); tx.onerror = () => rej(tx.error); });
}

/** A record from before sessions were removed (or a downloaded
 *  `.session.json`) as the workspace. Its session name becomes the reads
 *  submission prefix, so resumed runs keep the aliases they were submitted
 *  under. */
function asWorkspace(rec) {
  const ws = { ...rec, id: WORKSPACE_ID, reads_runs: rec.reads_runs || {} };
  if (rec.name && rec.state) {
    ws.state = { ...rec.state, fields: { readsPrefix: rec.name, ...(rec.state.fields || {}) } };
  }
  delete ws.name;
  return ws;
}

async function dbLoadWorkspace() {
  const ws = await idbGet(WORKSPACE_ID);
  if (ws) return ws;
  // First load since sessions were removed: adopt the most recently used one.
  // ponytail: older sessions stay in IndexedDB unreachable; download them from an older build if needed.
  const [latest] = (await idbAll()).sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  return latest ? idbPut(asWorkspace(latest)) : null;
}
async function dbSaveState(state, readsRuns) {
  const r = (await idbGet(WORKSPACE_ID)) || { id: WORKSPACE_ID };
  const now = new Date().toISOString();
  Object.assign(r, { state, state_saved_at: now, updated_at: now, reads_runs: readsRuns || {} });
  await idbPut(r);
  return now;
}
async function dbSaveDhExport(kind, exportJson) {
  const r = (await idbGet(WORKSPACE_ID)) || { id: WORKSPACE_ID };
  const now = new Date().toISOString();
  const field = kind === "experiment" ? "dh_export_experiment" : kind === "study" ? "dh_export_study" : "dh_export_sample";
  r[field] = exportJson; r[field + "_saved_at"] = now; r.updated_at = now;
  await idbPut(r);
  return now;
}

// ---------------------------------------------------------------------------
// Snapshot / restore
// ---------------------------------------------------------------------------
let saveTimer = null;
let suppressSave = false;  // true while applying restored state (don't echo back)

function setWorkspaceSaved(isoTs) {
  $("workspaceSaved").textContent = isoTs ? "saved " + new Date(isoTs).toLocaleTimeString() : "not saved yet";
}

// Snapshot all user-entered + result/log state (never credentials). Result
// tables and logs are captured as rendered HTML/text so they restore exactly;
// interactive state (run rows, samples, prepared records) is captured as data.
const _FIELD_IDS = [
  "studyHold", "sampleFilter", "sampleChecklist", "sampleHold",
  "defaultStudy", "recEntity", "recStatus", "recSearch", "recLinked", "dhExport", "readsLocalDir",
  "readsMode", "readsPrefix",
];
// recWrite is deliberately absent: write mode is never restored.
const _CHECK_IDS = ["studyModify", "studyPublic", "sampleModify", "samplePublic", "forceReupload",
                    "recFullFields", "recUnlinked", "expDhAutoSync"];
// Receipt tables only. A record grid (#recGrid) is an <ena-browser>, whose
// serialized innerHTML restores as dead DOM — its layout is persisted instead.
const _RESULT_IDS = ["studyPrepOut", "studyOut", "prepOut", "sampleOut", "readsResults"];
const _LOG_IDS = ["studyLog", "readsLog", "recLog"];

// --- Record grids -----------------------------------------------------------
// Only what the user arranged — column order, pins, hidden columns, widths and
// filters. Never the rows: a restored row shows the status it had when it was
// saved, which after a release or a suppress is the wrong one. Rows are
// re-fetched instead.
const _GRID_IDS = {
  records: "recGrid", studyOut: "studyGrid", sampleOut: "sampleGrid", pairing: "pairSamples",
  readsOut: "readsGrid",
};

let SAVED_GRIDS = {};

function collectGrids() {
  const out = {};
  for (const [key, id] of Object.entries(_GRID_IDS)) {
    const grid = $(id);
    if (!grid?.getLayout) continue;
    out[key] = { layout: grid.getLayout(), filters: grid.getFilters() };
  }
  if (out.records) out.records.entity = $("recEntity").value;
  if (out.studyOut) out.studyOut.entity = "studies";
  if (out.sampleOut) out.sampleOut.entity = "samples";
  if (out.pairing) out.pairing.entity = "samples";
  if (out.readsOut) out.readsOut.entity = "runs";
  return out;
}

/** Apply the saved arrangement to a grid, before its rows arrive: a column
 *  the grid first meets in the data arrives hidden, and that sticks. A layout
 *  saved for one entity says nothing about another's columns, so it is only
 *  applied to the entity it came from. */
function applySavedGridLayout(key, entity) {
  const saved = SAVED_GRIDS[key];
  const grid = $(_GRID_IDS[key]);
  if (!saved || !grid?.setLayout) return;
  if (saved.entity && entity && saved.entity !== entity) return;
  if (saved.layout) grid.setLayout(saved.layout);
  if (saved.filters?.length) grid.setFilters(saved.filters);
}

function collectState() {
  const fields = {};
  _FIELD_IDS.forEach((id) => { fields[id] = $(id).value; });
  const checks = {};
  _CHECK_IDS.forEach((id) => { checks[id] = $(id).checked; });
  const resultsHtml = {};
  _RESULT_IDS.forEach((id) => { resultsHtml[id] = $(id).innerHTML; });
  const logs = {};
  _LOG_IDS.forEach((id) => { logs[id] = $(id).textContent; });
  return {
    v: 2, test: TEST, fields, checks, resultsHtml, logs, grids: collectGrids(),
    runRows: RUN_ROWS, readSamples: READ_SAMPLES, selectedSample: SELECTED_SAMPLE,
    prepared: window.__prepared || null,
    preparedStudies: window.__preparedStudies || null,
  };
}

// Applied once, onto a freshly loaded page — so there is nothing to reset first.
function applyState(data) {
  suppressSave = true;
  try {
    const st = data.state || {};
    // Env
    TEST = st.test !== undefined ? st.test : data.test_env !== undefined ? !!data.test_env : true;
    $("prodToggle").checked = !TEST;
    $("envPill").textContent = TEST ? "TEST" : "PRODUCTION";
    $("envPill").className = "vf-badge " + (TEST ? "vf-badge--primary" : "vf-badge--secondary");
    // Fields + checkboxes
    Object.entries(st.fields || {}).forEach(([id, v]) => { if ($(id) != null) $(id).value = v; });
    Object.entries(st.checks || {}).forEach(([id, v]) => { if ($(id) != null) $(id).checked = v; });
    // A workspace that recorded a reads mode is the user's own choice — honour
    // it and stop helper detection from overriding it. Workspaces saved before
    // the manual mode existed carry none, and keep whatever detection decided.
    if (st.fields && st.fields.readsMode) READS_MODE_CHOSEN = true;
    applyReadsMode();
    // Result tables + logs (rendered HTML / text)
    Object.entries(st.resultsHtml || {}).forEach(([id, html]) => { if ($(id) != null) $(id).innerHTML = html; });
    Object.entries(st.logs || {}).forEach(([id, txt]) => { if ($(id) != null) $(id).textContent = txt; });
    // Interactive state
    SAVED_GRIDS = st.grids || {};
    RUN_ROWS = st.runRows || [];
    READ_SAMPLES = st.readSamples || [];
    setSelectedSample(st.selectedSample || "");
    window.__prepared = st.prepared || undefined;
    window.__preparedStudies = st.preparedStudies || undefined;
    READS_RUNS = data.reads_runs || {};
    renderRunTable();
    refreshAssignedCounts();
    $("sampleSubmitBtn").disabled = !(window.__prepared && window.__prepared.length);
    // DataHarmonizer grids: repopulated once each iframe is ready.
    if (data.dh_export_sample) {
      $("dhExport").value = JSON.stringify(data.dh_export_sample);
      setDhSavedIndicator(data.dh_export_sample_saved_at);
      loadDhGridWhenReady(data.dh_export_sample);
    }
    if (data.dh_export_experiment) {
      setExpDhSavedIndicator(data.dh_export_experiment_saved_at);
      loadExpDhGridWhenReady(data.dh_export_experiment);
    }
    if (data.dh_export_study) {
      setStudyDhSavedIndicator(data.dh_export_study_saved_at);
      loadStudyDhGridWhenReady(data.dh_export_study);
    }
  } finally {
    suppressSave = false;
  }
}

async function restoreWorkspace() {
  try {
    const ws = await dbLoadWorkspace();
    if (ws) {
      applyState(ws);
      // Grid rows are never saved, so they are re-fetched — with the saved
      // arrangement already applied (applySavedGridLayout).
      if (SAVED_GRIDS.records && CREDS.username) loadRecords();
    }
    setWorkspaceSaved(ws && ws.updated_at);
  } catch (e) { console.error("Could not restore the workspace", e); }
  window.WORKSPACE_READY = true;
}

function scheduleSave() {
  if (suppressSave) return;
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(saveWorkspaceNow, 1200);
}

async function saveWorkspaceNow() {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
  try {
    setWorkspaceSaved(await dbSaveState(collectState(), READS_RUNS));
  } catch { /* transient; next change retries */ }
}

// ---------------------------------------------------------------------------
// Backup: download / import the workspace as JSON. The browser profile is the
// only copy, so this is the whole durability story — and how work moves
// between machines.
// ---------------------------------------------------------------------------
async function downloadWorkspace() {
  await saveWorkspaceNow();
  const rec = await idbGet(WORKSPACE_ID);
  const blob = new Blob([JSON.stringify(rec, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `mimicc-workspace-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Replace the workspace with a downloaded file (a workspace, or an old
 *  `.session.json`), then reload so it is applied onto a clean page. */
function importWorkspace() {
  const f = $("workspaceImportFile").files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const rec = JSON.parse(reader.result);
      if (!rec || typeof rec !== "object" || !("state" in rec)) throw new Error("Not a valid workspace file.");
      if (!confirm("Replace the current workspace with this file? Anything not downloaded is lost.")) return;
      if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
      await idbPut(asWorkspace(rec));
      location.reload();
    } catch (e) { alert(e.message); }
    finally { $("workspaceImportFile").value = ""; }
  };
  reader.readAsText(f);
}

async function clearWorkspace() {
  if (!confirm("Clear the workspace? All entered metadata, logs and the reads resume ledger are removed from this browser.")) return;
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
  suppressSave = true;  // a pending autosave must not write it back before the reload
  // An empty record, not a delete: a missing one would re-adopt an old session.
  await idbPut({ id: WORKSPACE_ID });
  location.reload();
}

// Persist field edits (text inputs, selects, checkboxes) as the user types.
// Credentials inputs are excluded — they are never part of workspace state.
document.querySelector("main").addEventListener("input", (e) => {
  if (["username", "password", "readsLocalDir", "newUserName", "newUserPassword"].includes(e.target.id)) return;
  scheduleSave();
});
document.querySelector("main").addEventListener("change", (e) => {
  if (["username", "password", "newUserName", "newUserPassword"].includes(e.target.id)) return;
  scheduleSave();
});
