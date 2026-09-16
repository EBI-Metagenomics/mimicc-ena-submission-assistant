"use strict";

// ---------------------------------------------------------------------------
// Schema library: select a schema for the sample/experiment/study grids, build
// new schemas by importing ENA XML/XSD/YAML sources, and edit/preview schemas
// via the embedded dataharmonizer-template-builder (dhtb) sidecar. All of it in
// the browser: the library in IndexedDB, LinkML work in Python (py()).
// ---------------------------------------------------------------------------
let SCHEMA_LIST = [];

// ---------------------------------------------------------------------------
// The library: IndexedDB (SCHEMA_STORE, workspace.js), one record per schema —
// { id, name, title, description, yaml }. Seeded from the bundled schemas on
// first use; schemas/index.json carries their metadata, so listing them never
// needs Python.
// ---------------------------------------------------------------------------
let _seeding = null;

/** One seeding per page load, shared by every caller: a second caller must not
 *  see a half-seeded library as "already seeded". */
function seedSchemaLibrary() {
  _seeding ||= (async () => {
    if ((await idbAll(SCHEMA_STORE)).length) return;
    const index = await (await fetch("/schemas/index.json", { cache: "no-store" })).json();
    for (const { file, ...meta } of index) {
      const yaml = await (await fetch(`/schemas/${file}`, { cache: "no-store" })).text();
      await idbPut({ ...meta, yaml }, SCHEMA_STORE);
    }
  })().catch((e) => { _seeding = null; throw e; });
  return _seeding;
}

async function listLibrarySchemas() {
  await seedSchemaLibrary();
  return (await idbAll(SCHEMA_STORE)).sort((a, b) => a.id.localeCompare(b.id));
}

async function readLibrarySchema(schemaId) {
  await seedSchemaLibrary();
  const schema = await idbGet(schemaId, SCHEMA_STORE);
  if (!schema) throw new Error(`Schema not found: ${schemaId}`);
  return schema;
}

async function refreshSchemaList() {
  try {
    SCHEMA_LIST = await listLibrarySchemas();
  } catch (e) {
    console.error("Could not load the schema library", e);
    SCHEMA_LIST = [];
  }
  renderSchemaLibrary();
  populateSchemaSelect("sampleSchemaSelect");
  populateSchemaSelect("expSchemaSelect");
  populateSchemaSelect("studySchemaSelect");
  populateSchemaMultiSelect("schemaImportExisting");
}

function schemaOptionLabel(s) {
  return s.title && s.title !== s.id ? `${s.title} (${s.id})` : s.id;
}

function populateSchemaSelect(selectId) {
  const el = $(selectId);
  if (!el) return;
  const prev = el.value;
  el.innerHTML = SCHEMA_LIST.map((s) => `<option value="${esc(s.id)}">${esc(schemaOptionLabel(s))}</option>`).join("");
  if (SCHEMA_LIST.some((s) => s.id === prev)) el.value = prev;
}

function populateSchemaMultiSelect(selectId) {
  const el = $(selectId);
  if (!el) return;
  el.innerHTML = SCHEMA_LIST.map((s) => `<option value="${esc(s.id)}">${esc(schemaOptionLabel(s))}</option>`).join("");
}

function renderSchemaLibrary() {
  const el = $("schemaLibraryList");
  if (!SCHEMA_LIST.length) { el.innerHTML = '<p class="muted" style="padding:10px">No schemas saved yet.</p>'; return; }
  let h = "<table><thead><tr><th>Schema</th><th>Description</th><th>Actions</th></tr></thead><tbody>";
  SCHEMA_LIST.forEach((s) => {
    const id = esc(s.id);
    h += `<tr>
      <td>${esc(schemaOptionLabel(s))}</td>
      <td class="wrap">${esc(s.description || "")}</td>
      <td>
        <button class="btn secondary" style="padding:3px 8px" onclick="editSchemaInLibrary('${id}')">Edit</button>
        <button class="btn secondary" style="padding:3px 8px" onclick="selectSchemaById('sample','${id}')">Use for sample</button>
        <button class="btn secondary" style="padding:3px 8px" onclick="selectSchemaById('experiment','${id}')">Use for experiment</button>
        <button class="btn secondary" style="padding:3px 8px" onclick="exportSchemaFromLibrary('${id}')">Export</button>
        <button class="btn danger" style="padding:3px 8px" onclick="deleteSchemaFromLibrary('${id}')">Delete</button>
      </td>
    </tr>`;
  });
  el.innerHTML = h + "</tbody></table>";
}

// ---------------------------------------------------------------------------
// Grid schemas. Selecting a schema compiles it in Python and puts the result
// in Cache Storage at the path DataHarmonizer fetches (/templates/<folder>/
// schema.json); the service worker (sw.js) serves it from there, falling back
// to the bundle's built default. Each entry is tagged with the compiler that
// made it and the library schema it came from; the workspace records which
// schema each grid uses (grid_schemas), so a returning page needs no Python.
// ---------------------------------------------------------------------------
const TEMPLATE_CACHE = "dh-templates";
let _templateWorker = null;

/** Register sw.js; resolves true once it is active, false where the browser
 *  has no service workers (or the page is not a secure context). */
function templateWorkerReady() {
  if (_templateWorker) return _templateWorker;
  if (!("serviceWorker" in navigator)) return (_templateWorker = Promise.resolve(false));
  _templateWorker = navigator.serviceWorker.register("/sw.js")
    .then(() => Promise.race([
      navigator.serviceWorker.ready.then(() => true),
      new Promise((resolve) => setTimeout(() => resolve(false), 10000)),
    ]))
    .catch((e) => { console.error("Service worker registration failed", e); return false; });
  return _templateWorker;
}

async function compilerVersion() {
  return (await import("/static/py/versions.js")).COMPILER_VERSION;
}

/** Compile a library schema for a grid and cache it where the service worker
 *  serves it. Returns compile_for_grid's result (template, diagnostics). */
async function installGridSchema(role, schema) {
  if (!(await templateWorkerReady())) {
    throw new Error("This browser can't serve a custom grid schema (no service worker — the page needs https or localhost).");
  }
  const registry = await fetchDhRegistry();
  const result = await py("schema_service.compile_for_grid", { role, yaml_text: schema.yaml });
  if (!registry[result.folder]) {
    throw new Error(`DataHarmonizer template folder "${result.folder}" is missing. Rebuild the bundle so the fixed grid templates are registered.`);
  }
  const tags = { "x-role": role, "x-schema-id": schema.id, "x-compiler-version": await compilerVersion() };
  const cache = await caches.open(TEMPLATE_CACHE);
  await cache.put(`/templates/${result.folder}/schema.json`,
    new Response(JSON.stringify(result.schema_json), { headers: { ...tags, "content-type": "application/json" } }));
  await cache.put(`/templates/${result.folder}/schema.yaml`,
    new Response(schema.yaml, { headers: { ...tags, "content-type": "application/yaml" } }));
  return result;
}

/** Back to the bundle's built default for a grid. */
async function dropGridSchema(role) {
  const cache = await caches.open(TEMPLATE_CACHE);
  for (const request of await cache.keys()) {
    if ((await cache.match(request))?.headers.get("x-role") === role) await cache.delete(request);
  }
  await dbSetGridSchema(role, null);
}

/** Reload a grid's iframe on the same template — it re-fetches schema.json —
 *  and put the workspace's saved rows back into it. */
async function reloadDhGrid(role) {
  const frame = $(dhRoleConfig(role).frameId);
  const template = frame?.src && new URL(frame.src).searchParams.get("template");
  if (!template) return;
  pointDhFrameAtTemplate(role, template);
  const ws = (await idbGet(WORKSPACE_ID)) || {};
  if (role === "sample") loadDhGridWhenReady(ws.dh_export_sample);
  else if (role === "experiment") loadExpDhGridWhenReady(ws.dh_export_experiment);
  else loadStudyDhGridWhenReady(ws.dh_export_study);
}

/** On load: keep each grid's cached schema only if the workspace still selects
 *  it and today's compiler made it. Otherwise recompile it from the library,
 *  or — its schema deleted, or the workspace cleared — drop it so the grid
 *  falls back to the built default. Only this rare path starts Python.
 *  Resolves to the roles it changed. */
async function restoreGridSchemas() {
  const touched = [];
  try {
    if (!(await templateWorkerReady())) return touched;
    const version = await compilerVersion();
    const selected = await dbGetGridSchemas();
    const cached = {};
    const cache = await caches.open(TEMPLATE_CACHE);
    for (const request of await cache.keys()) {
      const response = await cache.match(request);
      if (request.url.endsWith("/schema.json")) cached[response.headers.get("x-role")] = response.headers;
    }
    for (const role of new Set([...Object.keys(cached), ...Object.keys(selected)])) {
      const schemaId = selected[role];
      const tags = cached[role];
      if (tags && schemaId && tags.get("x-schema-id") === schemaId && tags.get("x-compiler-version") === version) continue;
      const schema = schemaId && (await idbGet(schemaId, SCHEMA_STORE));
      try {
        if (schema) await installGridSchema(role, schema);
        else await dropGridSchema(role);
      } catch (e) {
        console.error(`Could not restore the ${role} grid's schema "${schemaId}"`, e);
        await dropGridSchema(role);
      }
      await reloadDhGrid(role);
      touched.push(role);
    }
  } catch (e) { console.error("Could not restore grid schemas", e); }
  return touched;
}

/** Ask the browser not to evict the library and cached grid schemas under
 *  storage pressure; say so in the header when it won't promise that. */
async function requestPersistentStorage() {
  try {
    if (!navigator.storage?.persist || (await navigator.storage.persist())) return;
  } catch { /* treated as refused */ }
  $("storageWarning").hidden = false;
}

function _roleMeta(role) {
  if (role === "sample") return { selectId: "sampleSchemaSelect", bannerId: "prepBanner" };
  if (role === "study")  return { selectId: "studySchemaSelect",  bannerId: "studyPrepBanner" };
  return { selectId: "expSchemaSelect", bannerId: "readsBanner" };
}

async function applySchemaSelection(role) {
  const { selectId, bannerId } = _roleMeta(role);
  const schemaId = $(selectId).value;
  if (!schemaId) { banner(bannerId, false, "No schema selected."); return; }
  await selectSchemaById(role, schemaId, bannerId);
}

async function selectSchemaById(role, schemaId, bannerId) {
  const fallbackBanner = bannerId || _roleMeta(role).bannerId;
  try {
    const result = await installGridSchema(role, await readLibrarySchema(schemaId));
    await dbSetGridSchema(role, schemaId);
    console.info("DataHarmonizer schema selection", result.template, result.diagnostics);
    const loadToken = pointDhFrameAtTemplate(role, result.template);
    if (role === "experiment") {
      EXP_TEMPLATE_PATH = result.template;
      checkExpSchemaColumns();
    }
    await waitForDhFrameReady(role, result.template, loadToken);
    const diagnostics = Array.isArray(result.diagnostics) && result.diagnostics.length
      ? ` ${result.diagnostics.map((d) => d.message || String(d)).join(" ")}`
      : "";
    banner(fallbackBanner, true, `Switched the ${role} grid to "${schemaId}".${diagnostics}`);
  } catch (e) {
    console.error(`Failed to switch ${role} DataHarmonizer schema`, e);
    banner(fallbackBanner, false, e.message);
  }
}

async function deleteSchemaFromLibrary(schemaId) {
  if (!confirm(`Delete schema "${schemaId}" from the library?`)) return;
  try {
    const selected = await dbGetGridSchemas();
    await idbDelete(schemaId, SCHEMA_STORE);
    // A grid showing it goes back to its built default.
    for (const [role, id] of Object.entries(selected)) {
      if (id !== schemaId) continue;
      await dropGridSchema(role);
      await reloadDhGrid(role);
    }
    banner("schemaLibraryBanner", true, `Deleted "${schemaId}".`);
    refreshSchemaList();
  } catch (e) { banner("schemaLibraryBanner", false, e.message); }
}

async function exportSchemaFromLibrary(schemaId) {
  try {
    downloadText(`${schemaId}.yaml`, (await readLibrarySchema(schemaId)).yaml, "application/yaml");
  } catch (e) { banner("schemaLibraryBanner", false, e.message); }
}

async function editSchemaInLibrary(schemaId) {
  try {
    const schema = await readLibrarySchema(schemaId);
    $("schemaSaveName").value = schemaId;
    loadSchemaIntoEditor(schema.yaml, schemaId);
    banner("schemaLibraryBanner", true, `Loaded "${schemaId}" into the editor below.`);
  } catch (e) { banner("schemaLibraryBanner", false, e.message); }
}

/** Save YAML to the library under a name (Python validates it and names it). */
async function saveLibrarySchema(name, yamlText) {
  const meta = await py("schema_service.describe_schema", { yaml_text: yamlText, name });
  await idbPut({ ...meta, yaml: yamlText }, SCHEMA_STORE);
  return meta.id;
}

async function refreshEnaSources() {
  try {
    const res = await fetch("/assets/ena_schema/index.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`ENA sources unavailable (HTTP ${res.status}).`);
    const sources = await res.json();
    $("schemaImportChecklists").innerHTML = sources.checklists
      .map((s) => `<option value="${esc(s.id)}">${esc(s.filename)}</option>`).join("");
    $("schemaImportXsd").innerHTML = sources.xsd
      .map((s) => `<option value="${esc(s.id)}">${esc(s.filename)}</option>`).join("");
  } catch (e) { banner("schemaImportBanner", false, e.message); }
}

function selectedOptions(selectId) {
  return Array.from($(selectId).selectedOptions).map((o) => o.value);
}

function clearSchemaMultiSelect(selectId) {
  const el = $(selectId);
  if (!el) return;
  el.selectedIndex = -1;
  Array.from(el.options).forEach((o) => { o.selected = false; });
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

/** py() files for bundled ENA sources — plus SRA.common.xsd, which every XSD imports. */
function enaSourceFiles(sourceIds) {
  return servedFiles(...sourceIds.map((id) => `/assets/ena_schema/${id}`), "/assets/ena_schema/SRA.common.xsd");
}

async function buildImportedSchema() {
  const sourceIds = selectedOptions("schemaImportChecklists").concat(selectedOptions("schemaImportXsd"));
  const schemaIds = selectedOptions("schemaImportExisting");
  if (!sourceIds.length && !schemaIds.length) {
    banner("schemaImportBanner", false, "Select at least one checklist, XSD, or existing schema to import.");
    return;
  }
  const name = $("schemaImportName").value.trim();
  try {
    // Library schemas go in as files, after the ENA sources (earlier inputs win).
    const files = enaSourceFiles(sourceIds);
    const uploadPaths = [];
    for (const id of schemaIds) {
      const path = `/tmp/library/${id}.yaml`;
      files[path] = { data: (await readLibrarySchema(id)).yaml };
      uploadPaths.push(path);
    }
    const yamlText = await py("schema_service.import_build",
      { source_ids: sourceIds, upload_paths: uploadPaths, name: name || null }, files);
    $("schemaSaveName").value = name || "";
    loadSchemaIntoEditor(yamlText, name);
    banner("schemaImportBanner", true, "Built merged schema — loaded into the editor below.");
  } catch (e) { banner("schemaImportBanner", false, e.message); }
}

async function importSchemaFile() {
  const f = $("schemaFilePicker").files[0];
  if (!f) return;
  try {
    const path = `/tmp/upload/${f.name}`;
    const yamlText = await py("schema_service.import_build", { upload_paths: [path] },
      { [path]: { data: new Uint8Array(await f.arrayBuffer()) } });
    $("schemaSaveName").value = f.name.replace(/\.(ya?ml|xml|xsd)$/i, "");
    loadSchemaIntoEditor(yamlText, $("schemaSaveName").value);
    banner("schemaLibraryBanner", true, `Loaded "${f.name}" into the editor below.`);
  } catch (e) {
    banner("schemaLibraryBanner", false, e.message);
  } finally {
    $("schemaFilePicker").value = "";
  }
}

// ---------------------------------------------------------------------------
// dataharmonizer-template-builder (dhtb) embed: iframe + postMessage bridge.
// See ../dataharmonizer-template-builder/docs/integration-contract.md.
// ---------------------------------------------------------------------------
let DHTB_READY = false;
let DHTB_PENDING_YAML = null; // {yaml, name} queued until dhtb.ready fires
let DHTB_EXPORT_INTENT = null; // "save" | "export" — which action requested the pending dhtb.exportYaml

function initSchemaEditorFrame() {
  // dhtb follows the OS colour scheme unless the host tells it otherwise, so
  // keep it pinned to this page's data-theme (and to any later change).
  new MutationObserver(syncDhtbTheme).observe(document.documentElement,
    { attributes: true, attributeFilter: ["data-theme"] });
  const url = HEALTH.dhtb_url;
  if (!url) { $("schemaEditorMissing").style.display = "block"; return; }
  $("schemaEditorFrame").src = url;
}

function syncDhtbTheme() {
  if (!DHTB_READY) return; // resent from the dhtb.ready handler
  postToDhtb("dhtb.setTheme", { theme: document.documentElement.dataset.theme === "dark" ? "dark" : "light" });
}

function loadSchemaIntoEditor(yamlText, name) {
  if (DHTB_READY) {
    postToDhtb("dhtb.loadYaml", { yaml: yamlText, name: name || "" });
  } else {
    DHTB_PENDING_YAML = { yaml: yamlText, name: name || "" };
  }
}

function postToDhtb(type, payload) {
  const frame = $("schemaEditorFrame");
  if (!frame || !frame.contentWindow) return;
  frame.contentWindow.postMessage({ type, ...payload }, "*");
}

window.addEventListener("message", (ev) => {
  if (HEALTH.dhtb_url && ev.origin !== new URL(HEALTH.dhtb_url).origin) return;
  const msg = ev.data;
  if (!msg || typeof msg !== "object") return;
  if (msg.type === "dhtb.ready") {
    DHTB_READY = true;
    $("schemaEditorMissing").style.display = "none";
    syncDhtbTheme();
    if (DHTB_PENDING_YAML) { postToDhtb("dhtb.loadYaml", DHTB_PENDING_YAML); DHTB_PENDING_YAML = null; }
  } else if (msg.type === "dhtb.exported") {
    window.__dhtbExportedYaml = msg.yaml;
    const intent = DHTB_EXPORT_INTENT;
    DHTB_EXPORT_INTENT = null;
    if (intent === "export") downloadYamlFile(msg.yaml);
    else saveExportedSchema(msg.yaml);
  } else if (msg.type === "dhtb.error") {
    banner("schemaEditorBanner", false, msg.message || "dataharmonizer-template-builder reported an error.");
  }
});

async function saveExportedSchema(yamlText) {
  const name = $("schemaSaveName").value.trim();
  if (!name) { banner("schemaEditorBanner", false, "Enter a name to save as."); return; }
  try {
    const id = await saveLibrarySchema(name, yamlText);
    banner("schemaEditorBanner", true, `Saved as "${id}".`);
    refreshSchemaList();
  } catch (e) { banner("schemaEditorBanner", false, e.message); }
}

function downloadYamlFile(yamlText) {
  const name = $("schemaSaveName").value.trim() || "schema";
  const blob = new Blob([yamlText], { type: "application/x-yaml" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${name}.yaml`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  banner("schemaEditorBanner", true, `Exported "${name}.yaml".`);
}

function saveEditorSchema() {
  if (!DHTB_READY) { banner("schemaEditorBanner", false, "Editor isn't ready yet."); return; }
  DHTB_EXPORT_INTENT = "save";
  postToDhtb("dhtb.exportYaml", {});
}

function exportEditorSchema() {
  if (!DHTB_READY) { banner("schemaEditorBanner", false, "Editor isn't ready yet."); return; }
  DHTB_EXPORT_INTENT = "export";
  postToDhtb("dhtb.exportYaml", {});
}
