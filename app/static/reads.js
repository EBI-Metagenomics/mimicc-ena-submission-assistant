"use strict";

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------
// The local reads directory the helper scans/uploads from (on the user's machine).
function readsLocalDir() { return ($("readsLocalDir").value || "").trim(); }

// Two routes to ENA: the local helper app runs webin-cli for the user, or the
// user runs it themselves from a generated command. The mode only decides which
// controls exist (see the .helper-only/.manual-only rules in index.html) and
// what happens at the very end — pairing, experiment metadata, the manifests
// and the resume ledger are shared by both.
function readsMode() { return $("readsMode")?.value || "helper"; }

function applyReadsMode() {
  document.body.classList.toggle("reads-mode-manual", readsMode() === "manual");
}

function onReadsModeChange() {
  READS_MODE_CHOSEN = true;
  applyReadsMode();
  scheduleSave();
}

applyReadsMode();

let _dirPickerPath = "/";

async function browseDir() {
  if (!HELPER_OK && !(await detectHelper())) {
    banner("readsBanner", false, "The local upload helper isn't running — start it, then Browse will work."); return;
  }
  _dirPickerPath = readsLocalDir() || "/";
  $("dirPickerModal").classList.add("show");
  _browseNav(_dirPickerPath);
}

function closeDirPicker() { $("dirPickerModal").classList.remove("show"); }

function confirmDirPicker() {
  $("readsLocalDir").value = _dirPickerPath;
  scheduleSave();
  closeDirPicker();
}

async function _browseNav(path) {
  _dirPickerPath = path;
  // breadcrumb
  const parts = path.replace(/\/$/, "").split("/").filter(Boolean);
  const crumb = $("dirPickerBreadcrumb");
  crumb.innerHTML = "";
  const rootLink = document.createElement("a");
  rootLink.href = "#"; rootLink.textContent = "/";
  rootLink.onclick = (e) => { e.preventDefault(); _browseNav("/"); };
  crumb.appendChild(rootLink);
  let built = "";
  parts.forEach((p) => {
    built += "/" + p;
    const sep = document.createTextNode(" / ");
    const link = document.createElement("a");
    const capture = built;
    link.href = "#"; link.textContent = p;
    link.onclick = (e) => { e.preventDefault(); _browseNav(capture); };
    crumb.append(sep, link);
  });

  const list = $("dirPickerList");
  list.innerHTML = '<p class="muted" style="padding:8px">Loading…</p>';
  try {
    const r = await helperApi(`/api/browse?path=${encodeURIComponent(path)}`);
    list.innerHTML = "";
    if (!r.entries.length) {
      list.innerHTML = '<p class="muted" style="padding:8px">No subdirectories.</p>';
      return;
    }
    r.entries.forEach(({ name, path: epath }) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "session-row";
      btn.style.cssText = "width:100%;text-align:left;background:none;border:none;cursor:pointer;padding:6px 10px;font:inherit";
      btn.textContent = "📁 " + name;
      btn.onclick = () => _browseNav(epath);
      list.appendChild(btn);
    });
  } catch (e) { list.innerHTML = `<p class="muted" style="padding:8px">Error: ${e.message}</p>`; }
}

function blankRun(group) {
  return {
    NAME: group.group, files: group.files, paired: group.paired,
    FASTQ1: group.files_by_mate?.["1"] || "", FASTQ2: group.files_by_mate?.["2"] || "",
    FASTQ: group.paired ? "" : (group.files[0] || ""),
    SAMPLE: group.suggested_sample || "", STUDY: $("defaultStudy").value || "",
    confidence: group.confidence || "none", suggested_alias: group.suggested_alias || "",
  };
}

// ---------------------------------------------------------------------------
// Pairing table TSV export/import
// Columns: NAME, SAMPLE, STUDY, paired, FASTQ1, FASTQ2, FASTQ — a full
// round-trip of a pairing row (not just the assignment decision), so import
// works standalone without a prior Scan too.
//
// ONE ROW IS ONE RUN, and that is how the user says what a pair is, with no
// reference to filenames: FASTQ1+FASTQ2 on a single row is one paired run;
// two rows each carrying FASTQ, with the same SAMPLE, are two single-end runs
// of that sample. Sharing a sample is therefore not the same as being mates,
// which filename-based detection alone cannot express.
//
// An imported TSV is authoritative: it REPLACES the pairing table rather than
// patching the last scan, because a scan that mis-paired is exactly what the
// user is overriding, and leaving its leftover rows behind would submit them.
// ---------------------------------------------------------------------------
const PAIRING_TSV_COLS = ["NAME", "SAMPLE", "STUDY", "paired", "FASTQ1", "FASTQ2", "FASTQ"];

function downloadText(filename, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function exportPairingsTsv() {
  if (!RUN_ROWS.length) { banner("readsBanner", false, "Nothing to export — scan or pair some reads first."); return; }
  const lines = [PAIRING_TSV_COLS.join("\t")];
  RUN_ROWS.forEach((r) => {
    lines.push(PAIRING_TSV_COLS.map((c) => String(r[c] ?? "").replace(/\t|\n/g, " ")).join("\t"));
  });
  downloadText("read-sample-pairings.tsv", lines.join("\n") + "\n", "text/tab-separated-values");
  banner("readsBanner", true, `Exported ${RUN_ROWS.length} pairing(s).`);
}

function parsePairingsTsv(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim() !== "");
  if (!lines.length) return [];
  const header = lines[0].split("\t");
  return lines.slice(1).map((line) => {
    const cells = line.split("\t");
    const row = {};
    header.forEach((col, i) => { row[col] = cells[i] ?? ""; });
    return row;
  });
}

/** Turn one TSV row into a pairing row. The layout is read off the FILE
 *  columns, not the `paired` cell: FASTQ1+FASTQ2 means paired, FASTQ alone
 *  means single-end. Spreadsheets rewrite booleans (TRUE, 1, yes, a localised
 *  word) and a hand-written TSV may omit the column entirely, so the flag is
 *  only consulted to break a tie no file column settles. A row carrying no
 *  file columns at all is an assignment-only edit and keeps `previous`'s
 *  layout, instead of silently demoting a pair to single-end. */
function pairingRowFromTsv(row, previous) {
  const cell = (v) => String(v ?? "").trim();
  const f1 = cell(row.FASTQ1), f2 = cell(row.FASTQ2), f = cell(row.FASTQ);
  const flag = /^(true|t|yes|y|1|paired)$/i.test(cell(row.paired));
  const common = {
    NAME: cell(row.NAME), SAMPLE: cell(row.SAMPLE), STUDY: cell(row.STUDY),
    confidence: "manual", suggested_alias: "", reupload: !!previous?.reupload,
  };
  if (!f1 && !f2 && !f) {
    return {
      ...common, paired: !!previous?.paired,
      FASTQ1: previous?.FASTQ1 || "", FASTQ2: previous?.FASTQ2 || "", FASTQ: previous?.FASTQ || "",
      files: previous?.files || [],
    };
  }
  const paired = f1 && f2 ? true : f ? false : flag;
  return {
    ...common, paired, FASTQ1: f1, FASTQ2: f2, FASTQ: f,
    files: paired ? [f1, f2].filter(Boolean) : [f || f1].filter(Boolean),
  };
}

function importPairingsTsv() {
  const f = $("pairingsTsvFile").files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => {
    const imported = parsePairingsTsv(reader.result).filter((row) => String(row.NAME ?? "").trim());
    if (!imported.length) {
      banner("readsBanner", false, "No rows with a NAME in that TSV — nothing imported.");
      $("pairingsTsvFile").value = "";
      return;
    }
    const previous = new Map(RUN_ROWS.map((r) => [r.NAME, r]));
    const rows = imported.map((row) => pairingRowFromTsv(row, previous.get(String(row.NAME).trim())));
    const kept = rows.filter((r) => previous.has(r.NAME)).length;
    const dropped = RUN_ROWS.length - kept;
    RUN_ROWS = rows;
    renderRunTable();
    refreshAssignedCounts();
    syncPairingsToExperimentDh();
    const pairs = rows.filter((r) => r.paired).length;
    banner("readsBanner", true,
      `Imported ${rows.length} run(s) — ${pairs} paired, ${rows.length - pairs} single-end` +
      (dropped > 0 ? `; dropped ${dropped} row(s) the file doesn't list.` : "."));
    scheduleSave();
    $("pairingsTsvFile").value = "";
  };
  reader.readAsText(f);
}

function sampleAccession(sample) {
  return sample.accession || sample.secondary_accession || sample.external_accession || "";
}

function rowFileCount(row) {
  if (row.paired) {
    return [row.FASTQ1, row.FASTQ2].filter((v) => v && String(v).trim()).length || (row.files || []).length;
  }
  return (row.FASTQ && String(row.FASTQ).trim()) ? 1 : (row.files || []).length;
}

function sampleAssignmentCount(accession) {
  return RUN_ROWS
    .filter((row) => row.SAMPLE === accession)
    .reduce((total, row) => total + rowFileCount(row), 0);
}

// The per-sample file count is this app's arithmetic, not the grid's: it is
// derived from RUN_ROWS, so it has to follow every mutation of them.
// setCustomValues patches those cells in place — no re-sort, no lost selection.
function refreshAssignedCounts() {
  const map = {};
  READ_SAMPLES.forEach((sample) => {
    const accession = sampleAccession(sample);
    if (accession) map[accession] = sampleAssignmentCount(accession);
  });
  $("pairSamples")?.setCustomValues("reads_assigned", map);
}

async function loadReadSamples() {
  try {
    READ_SAMPLES = await enaPy("ena_service.list_records", {
      entity: "samples", search: $("pairSearch").value.trim(), linked_to: $("pairLinked").value.trim(),
    });
    if (!READ_SAMPLES.some((sample) => sampleAccession(sample) === SELECTED_SAMPLE)) {
      SELECTED_SAMPLE = "";
    }

    const grid = $("pairSamples");
    grid.applyConfig({
      entity: "samples",
      mode: "read",
      selectionMode: "single",
      customColumns: PAIR_CUSTOM_COLUMNS,
    });
    applySavedGridLayout("pairing", "samples");
    grid.setRows(READ_SAMPLES);
    if (SELECTED_SAMPLE) grid.setSelection([SELECTED_SAMPLE]);
    refreshAssignedCounts();
    banner("readsBanner", true, `Loaded ${READ_SAMPLES.length} sample(s).`);
    scheduleSave();
  } catch (e) { banner("readsBanner", false, e.message); }
}

/** Keep SELECTED_SAMPLE and the grid's own selection from drifting apart —
 *  everything outside the grid sets the selection through here. */
function setSelectedSample(accession) {
  SELECTED_SAMPLE = accession || "";
  const grid = $("pairSamples");
  if (!grid?.setSelection) return;
  if (SELECTED_SAMPLE) grid.setSelection([SELECTED_SAMPLE]);
  else grid.clearSelection();
}

// Declared up front, not just on load: refreshAssignedCounts() runs from
// renderRunTable() too, and setCustomValues refuses a column the grid has not
// been told about.
const PAIR_CUSTOM_COLUMNS = [
  { name: "reads_assigned", title: "Reads", type: "numeric", pinned: true, render: "badge" },
];
$("pairSamples").applyConfig({ customColumns: PAIR_CUSTOM_COLUMNS });

// ena-browser passes its configured height to Handsontable when it constructs
// the row viewport. CSS can stretch the custom element, but cannot change that
// already-created viewport, so keep the component configuration in step with
// the flex pane's measured height.
let pairSamplesViewportHeight = 0;
let pairSamplesResizeFrame = null;
function syncPairSamplesViewportHeight() {
  pairSamplesResizeFrame = null;
  const grid = $("pairSamples");
  if (!grid || grid.offsetParent === null) return;
  // ``height`` configures the Handsontable content area, while the custom
  // element also renders its toolbar above it. Reserve that toolbar height or
  // the final rows extend beneath the banner below this panel.
  const toolbarHeight = grid.querySelector(".ena-browser-toolbar")?.offsetHeight || 0;
  const height = Math.floor(grid.getBoundingClientRect().height - toolbarHeight);
  if (height < 80 || Math.abs(height - pairSamplesViewportHeight) < 2) return;
  pairSamplesViewportHeight = height;
  grid.applyConfig({ height });
}

const pairSamplesPane = document.querySelector(".assign-samples-pane");
if (pairSamplesPane && "ResizeObserver" in window) {
  new ResizeObserver(() => {
    if (pairSamplesResizeFrame !== null) cancelAnimationFrame(pairSamplesResizeFrame);
    pairSamplesResizeFrame = requestAnimationFrame(syncPairSamplesViewportHeight);
  }).observe(pairSamplesPane);
}
setTimeout(syncPairSamplesViewportHeight, 0);

// The split between the sample chooser and read assignments is intentionally
// local to the current layout: it is a working-space preference, not session
// data that needs to travel with a submission.
function setAssignSplit(leftWidth) {
  const layout = $("readsAssignGrid");
  const divider = $("readsAssignDivider");
  if (!layout || !divider) return;
  const available = layout.clientWidth - divider.offsetWidth;
  const minLeft = 280;
  const minRight = 320;
  const width = Math.round(Math.min(Math.max(leftWidth, minLeft), available - minRight));
  layout.style.setProperty("--assign-left-width", `${width}px`);
  divider.setAttribute("aria-valuenow", String(Math.round((width / layout.clientWidth) * 100)));
}

function installAssignDivider() {
  const layout = $("readsAssignGrid");
  const divider = $("readsAssignDivider");
  if (!layout || !divider) return;

  divider.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    divider.classList.add("is-dragging");
    divider.setPointerCapture(event.pointerId);
  });
  divider.addEventListener("pointermove", (event) => {
    if (!divider.hasPointerCapture(event.pointerId)) return;
    setAssignSplit(event.clientX - layout.getBoundingClientRect().left);
  });
  const stopDragging = (event) => {
    if (!divider.hasPointerCapture(event.pointerId)) return;
    divider.releasePointerCapture(event.pointerId);
    divider.classList.remove("is-dragging");
    redrawGrids(layout);
  };
  divider.addEventListener("pointerup", stopDragging);
  divider.addEventListener("pointercancel", stopDragging);
  divider.addEventListener("keydown", (event) => {
    const rect = layout.getBoundingClientRect();
    const current = parseFloat(getComputedStyle(layout).getPropertyValue("--assign-left-width")) || rect.width * 0.42;
    const step = event.shiftKey ? 48 : 16;
    if (event.key === "ArrowLeft") setAssignSplit(current - step);
    else if (event.key === "ArrowRight") setAssignSplit(current + step);
    else if (event.key === "Home") setAssignSplit(0);
    else if (event.key === "End") setAssignSplit(rect.width);
    else return;
    event.preventDefault();
    redrawGrids(layout);
  });
}

installAssignDivider();

// The only place a click sets SELECTED_SAMPLE. The pairing itself stays here:
// the next click on a run row writes it into that row (see renderRunTable).
$("pairSamples").addEventListener("ena-browser:selection-change", (e) => {
  SELECTED_SAMPLE = e.detail.lastKey || "";
  renderRunTable();   // re-applies the .assignable affordance
});

// ---------------------------------------------------------------------------
// Manual pairing
// Filename-based detection (read_assign.group_files) is a guess, so the run
// table is the authority: tick two single-end rows and they become one paired
// run, whatever the files are called. This is the only way to express "these
// two files are one run's mates" as distinct from "these two runs share a
// sample" — assigning the same SAMPLE to both rows leaves two single-end runs,
// which is what webin-cli then registers.
// ---------------------------------------------------------------------------

function selectedRunIndices() {
  return [...document.querySelectorAll("#runTable tbody input.pair-pick:checked")]
    .map((box) => Number(box.dataset.i))
    .sort((a, b) => a - b);
}

/** The pair's run name: what the two mates' names agree on, minus any trailing
 *  separator left by the mate token (``x_1_seq``/``x_2_seq`` -> ``x``). Falls
 *  back to the first row's name when they share no prefix. */
function commonRunName(a, b) {
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  return a.slice(0, i).replace(/[._\-\s]+$/, "") || a;
}

function readFileOf(row) {
  return row.FASTQ || row.FASTQ1 || (row.files || [])[0] || "";
}

function pairSelectedRuns() {
  const picked = selectedRunIndices();
  if (picked.length !== 2) {
    banner("readsBanner", false, "Tick exactly two rows — the two mates of one run — then press Pair.");
    return;
  }
  const [i, j] = picked;
  const [a, b] = [RUN_ROWS[i], RUN_ROWS[j]];
  if (a.paired || b.paired) {
    banner("readsBanner", false, "One of those rows is already a pair — Unpair it first.");
    return;
  }
  const [f1, f2] = [readFileOf(a), readFileOf(b)];
  if (!f1 || !f2) {
    banner("readsBanner", false, "Both rows need a read file before they can be paired.");
    return;
  }
  // Mates belong to one sample by definition; say so rather than silently
  // dropping one of two different assignments.
  const clash = a.SAMPLE && b.SAMPLE && a.SAMPLE !== b.SAMPLE;
  RUN_ROWS.splice(j, 1);
  RUN_ROWS[i] = {
    NAME: commonRunName(a.NAME || "", b.NAME || ""),
    paired: true, FASTQ1: f1, FASTQ2: f2, FASTQ: "", files: [f1, f2],
    SAMPLE: a.SAMPLE || b.SAMPLE || "", STUDY: a.STUDY || b.STUDY || "",
    confidence: "manual", suggested_alias: "",
    reupload: !!(a.reupload || b.reupload),
  };
  renderRunTable();
  refreshAssignedCounts();
  syncPairingsToExperimentDh();
  scheduleSave();
  banner("readsBanner", !clash,
    `Paired into "${RUN_ROWS[i].NAME}" (1: ${f1}, 2: ${f2}).` +
    (clash ? ` Those rows had different samples — kept ${RUN_ROWS[i].SAMPLE}, check it.` : ""));
}

function unpairSelectedRuns() {
  const picked = selectedRunIndices().filter((i) => RUN_ROWS[i].paired);
  if (!picked.length) {
    banner("readsBanner", false, "Tick one or more paired rows to split them back into single-end runs.");
    return;
  }
  // Right to left, so the earlier indices stay valid as rows are expanded.
  picked.reverse().forEach((i) => {
    const row = RUN_ROWS[i];
    const singles = [row.FASTQ1, row.FASTQ2].filter(Boolean).map((file, n) => ({
      ...row, NAME: `${row.NAME}_${n + 1}`, paired: false,
      FASTQ1: "", FASTQ2: "", FASTQ: file, files: [file], confidence: "manual",
    }));
    RUN_ROWS.splice(i, 1, ...singles);
  });
  renderRunTable();
  refreshAssignedCounts();
  syncPairingsToExperimentDh();
  scheduleSave();
  banner("readsBanner", true, `Unpaired ${picked.length} run(s).`);
}

/** Both scans end here: whoever found the read groups, they become run rows. */
function applyScannedGroups(groups, message) {
  RUN_ROWS = groups.map(blankRun);
  renderRunTable();
  banner("readsBanner", true, message);
  syncPairingsToExperimentDh();
  scheduleSave();
}

async function scanReads() {
  const dir = readsLocalDir();
  if (!dir) { banner("readsBanner", false, "Enter the absolute path to your local reads directory."); return; }
  if (!HELPER_OK && !(await detectHelper())) {
    banner("readsBanner", false, "The local upload helper isn't running — start it, then re-check."); return;
  }
  try {
    const r = await helperApi("/api/scan", { method: "POST", body: JSON.stringify({ host_dir: dir }) });
    applyScannedGroups(r.groups, `Found ${r.count} read group(s) in ${r.host_dir}.`);
  } catch (e) { banner("readsBanner", false, e.message); }
}

// Manual mode's scan. The directory picker hands us File objects, but only
// their NAMES are used — no file contents are opened and nothing is uploaded.
// The grouping itself is read_assign.group_files (via py()), the same pairing
// logic the helper's own scan applies, so the two modes can't drift apart.
// The browser is never told the picked folder's real path, which is why
// #readsLocalDir stays the user's own answer for webin-cli's -inputDir.
async function scanReadsFromPicker() {
  const input = $("readsDirInput");
  const files = [...(input?.files || [])];
  if (!files.length) return;
  try {
    const groups = await py("read_assign.group_files", { names: files.map((f) => f.name) });
    applyScannedGroups(groups, `Found ${groups.length} read group(s) among ${files.length} file(s).`);
  } catch (e) {
    banner("readsBanner", false, e.message);
  } finally {
    // Cleared so re-picking the same folder fires change again.
    if (input) input.value = "";
  }
}
async function suggestSamples() {
  if (!RUN_ROWS.length) { banner("readsBanner", false, "Scan first."); return; }
  try {
    const groups = RUN_ROWS.map((r) => ({ group: r.NAME, files: r.files, paired: r.paired, files_by_mate: { 1: r.FASTQ1, 2: r.FASTQ2 } }));
    const r = await enaPy("ena_service.suggest_samples", { groups });
    r.groups.forEach((g, i) => { if (g.suggested_sample) { RUN_ROWS[i].SAMPLE = g.suggested_sample; RUN_ROWS[i].confidence = g.confidence; } });
    READ_SAMPLES = r.samples;
    renderRunTable();
    refreshAssignedCounts();
    banner("readsBanner", true, `Auto-assigned ${r.groups.filter((g) => g.suggested_sample).length}/${r.groups.length} group(s).`);
    syncPairingsToExperimentDh();
    scheduleSave();
  } catch (e) { banner("readsBanner", false, e.message); }
}
function runStatusCell(name) {
  const led = READS_RUNS[name];
  if (!led) return { text: "—", title: "not yet submitted from this workspace" };
  const acc = led.run_accession || led.experiment_accession || "";
  if (led.status === "done") return { text: `✓ done ${acc}`.trim(), title: "submitted from this workspace" };
  if (led.status === "already_in_ena") return { text: `● in ENA ${acc}`.trim(), title: "already present in ENA — skipped on resume" };
  if (led.status === "failed") return { text: "✗ failed", title: "last submission failed" };
  return { text: led.status, title: led.status };
}

function renderRunTable() {
  const cols = ["NAME", "files", "SAMPLE", "STUDY"];
  const head = $("runTable").querySelector("thead");
  const body = $("runTable").querySelector("tbody");
  head.innerHTML = '<tr><th title="Tick two rows to pair them">pair</th><th></th>'
    + cols.map((c) => `<th>${c}</th>`).join("") + "<th>status</th><th>re-upload</th></tr>";
  body.innerHTML = "";
  RUN_ROWS.forEach((row, i) => {
    const tr = document.createElement("tr");
    tr.className = SELECTED_SAMPLE ? "assignable" : "";
    tr.onclick = (e) => {
      if (!SELECTED_SAMPLE || e.target.closest("input,button,select,textarea")) return;
      RUN_ROWS[i].SAMPLE = SELECTED_SAMPLE;
      RUN_ROWS[i].confidence = "manual";
      renderRunTable();
      refreshAssignedCounts();
      syncPairingsToExperimentDh();
      scheduleSave();
    };

    // Pair/unpair picker. Separate from the row click (which assigns the
    // selected sample) so the two never fight over one gesture.
    const pickTd = document.createElement("td");
    const pick = document.createElement("input");
    pick.type = "checkbox";
    pick.className = "pair-pick";
    pick.style.width = "auto";
    pick.dataset.i = String(i);
    pick.title = "Select for Pair / Unpair";
    pick.onclick = (e) => e.stopPropagation();
    pickTd.appendChild(pick);
    tr.appendChild(pickTd);

    const removeTd = document.createElement("td");
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "icon-btn danger";
    removeBtn.title = "Remove row";
    removeBtn.textContent = "×";
    removeBtn.onclick = (e) => {
      e.stopPropagation();
      RUN_ROWS.splice(i, 1);
      renderRunTable();
      refreshAssignedCounts();
      scheduleSave();
    };
    removeTd.appendChild(removeBtn);
    tr.appendChild(removeTd);

    cols.forEach((c) => {
      const td = document.createElement("td");
      if (c === "files") {
        td.className = "wrap";
        const listed = row.paired
          ? [`1: ${row.FASTQ1 || "—"}`, `2: ${row.FASTQ2 || "—"}`]
          : (row.FASTQ ? [row.FASTQ] : row.files);
        td.innerHTML = `<span class="tag">${row.paired ? "paired" : "single"}</span> `
          + listed.join("<br>")
          + (row.confidence === "high" ? ' <span class="tag high">auto</span>' : "");
      } else {
        const inp = document.createElement("input");
        inp.dataset.col = c;   // addressable by column, not by input position
        inp.value = row[c] || "";
        inp.oninput = (e) => {
          RUN_ROWS[i][c] = e.target.value;
          if (c === "SAMPLE" || c === "FASTQ" || c === "FASTQ1" || c === "FASTQ2") refreshAssignedCounts();
          if (c === "SAMPLE" || c === "NAME") syncPairingsToExperimentDh();
          scheduleSave();
        };
        td.appendChild(inp);
      }
      tr.appendChild(td);
    });

    // status (resume ledger)
    const statusTd = document.createElement("td");
    const st = runStatusCell(row.NAME);
    statusTd.textContent = st.text;
    statusTd.title = st.title;
    statusTd.className = "wrap";
    tr.appendChild(statusTd);

    // re-upload toggle
    const reTd = document.createElement("td");
    const reChk = document.createElement("input");
    reChk.type = "checkbox";
    reChk.style.width = "auto";
    reChk.checked = !!row.reupload;
    reChk.title = "Re-submit this run under a fresh alias even if it's already in ENA";
    reChk.onchange = (e) => { RUN_ROWS[i].reupload = e.target.checked; scheduleSave(); };
    reChk.onclick = (e) => e.stopPropagation();
    reTd.appendChild(reChk);
    tr.appendChild(reTd);

    body.appendChild(tr);
  });
  const has = RUN_ROWS.length > 0;
  ["readsSubmitBtn", "readsValidateBtn", "readsScriptBtn", "readsScriptValidateBtn",
   "pairSelectedBtn", "unpairSelectedBtn"]
    .forEach((id) => { if ($(id)) $(id).disabled = !has; });
  refreshAssignedCounts();
}
// Look up each pairing row's experiment metadata (by EXP_KEY_TITLE = NAME) in
// the experiment DataHarmonizer grid and merge the EXP_FIELD_TITLES-mapped
// columns into a run dict. Throws with a clear message if the experiment
// grid isn't ready or a row has no matching experiment entry, rather than
// silently submitting an incomplete manifest.
function mergeExperimentMetadata(runRows) {
  const dh = expDhApi();
  if (!dh) {
    throw new Error("Experiment metadata DataHarmonizer isn't ready yet — open the Samples-like panel above and try again.");
  }
  const exportJson = dh.getExportJson();
  const rows = Object.values(exportJson.Container || {})[0] || [];
  const byName = {};
  rows.forEach((row) => { if (row[EXP_KEY_TITLE]) byName[row[EXP_KEY_TITLE]] = row; });

  return runRows.map((r) => {
    const expRow = byName[r.NAME];
    if (!expRow) {
      throw new Error(`No experiment metadata row found for "${r.NAME}" — check the experiment DataHarmonizer panel above.`);
    }
    const o = { NAME: r.NAME, STUDY: r.STUDY, SAMPLE: r.SAMPLE, reupload: !!r.reupload };
    Object.entries(EXP_FIELD_TITLES).forEach(([field, title]) => {
      if (expRow[title]) o[field] = expRow[title];
    });
    if (r.paired) { o.FASTQ1 = r.FASTQ1; o.FASTQ2 = r.FASTQ2; } else { o.FASTQ = r.FASTQ; }
    return o;
  });
}

function appendReadsLog(text) {
  const log = $("readsLog");
  log.textContent += text + "\n";
  log.scrollTop = log.scrollHeight;
}

/** The submission prefix every run alias is built from (`<prefix>_<run>`).
 *  A stable prefix is what lets a re-run recognise runs already in ENA, so a
 *  blank one is filled in once and kept, rather than going one-off. */
function readsPrefix() {
  const el = $("readsPrefix");
  if (!el.value.trim()) {
    el.value = "sub-" + Date.now().toString(36);
    scheduleSave();
  }
  return el.value.trim();
}

// Reads upload is browser-bridged:
//   1. build a PLAN (which runs to upload vs. skip + manifest text; readsPlan),
//   2. for each upload, hand the manifest to the LOCAL HELPER which runs
//      webin-cli against the local files and streams the log,
//   3. turn each outcome into a result row for the resume ledger. Read files
//      never pass through this page.
async function submitReads(doSubmit) {
  $("readsLog").textContent = "";
  $("readsResults").innerHTML = "";
  let runs;
  try {
    runs = mergeExperimentMetadata(RUN_ROWS);
  } catch (e) { banner("submitReadsBanner", false, e.message); return; }

  if (!HELPER_OK && !(await detectHelper())) {
    banner("submitReadsBanner", false, "The local upload helper isn't running — start it, then re-check."); return;
  }
  const dir = readsLocalDir();
  if (!dir) { banner("submitReadsBanner", false, "Set your local reads directory in step 1."); return; }

  try {
    const { plan, warnings } = await readsPlan(runs);
    (warnings || []).forEach((w) => appendReadsLog("WARNING: " + w));

    const results = [];
    for (const entry of plan) {
      if (entry.action === "skip") {
        appendReadsLog(`=== ${entry.name} === SKIP (${entry.reason})`);
        results.push(entry);
        recordLedger(entry);
        continue;
      }
      appendReadsLog(`=== ${entry.name} === uploading via local helper…`);
      const result = await uploadOneViaHelper(entry, dir, doSubmit);
      results.push(result);
      recordLedger(result);
      renderRunTable();
    }

    const ok = results.every((r) => r.success !== false);
    const skipped = results.filter((r) => r.skipped).length;
    banner("submitReadsBanner", ok,
      ok ? `Done: ${results.length} run(s)${skipped ? `, ${skipped} skipped` : ""}${doSubmit ? "" : " (validate only)"}.`
         : "Some runs failed — see results.");
    renderTable("readsResults", results);
    await refreshReadsGrid(results);
    renderRunTable();
    saveWorkspaceNow();
  } catch (e) { banner("submitReadsBanner", false, e.message); }
}

/** The run accessions this workspace put in ENA: what the last batch returned,
 *  plus the resume ledger — a resumed batch skips runs it submitted earlier,
 *  and those belong in the confirmation too. */
function submittedRunAccessions(results = []) {
  const fromResults = results.map((r) => r.run_accession);
  const fromLedger = Object.values(READS_RUNS).map((r) => r.run_accession);
  return [...new Set([...fromResults, ...fromLedger])].filter(Boolean);
}

/** The runs as ENA now holds them — read-only, filtered to this workspace's. */
async function refreshReadsGrid(results = []) {
  const keep = submittedRunAccessions(results);
  const grid = $("readsGrid");
  $("readsGridEmpty").style.display = keep.length ? "none" : "block";
  grid.style.display = keep.length ? "block" : "none";
  if (!keep.length) { grid.setRows([]); return; }
  try {
    const rows = await enaPy("ena_service.list_records", { entity: "runs" });
    grid.applyConfig({ entity: "runs", mode: "read", selectionMode: "none", rowActions: [] });
    applySavedGridLayout("readsOut", "runs");
    grid.setRows(rows);
    grid.setFilters([{ column: "accession", operator: "in", values: keep }]);
  } catch (e) {
    banner("submitReadsBanner", false, e.message);
  }
}

/** Which runs to upload and which to skip (already done, or already in ENA
 *  under their stable alias), with each upload's manifest text. */
function readsPlan(runs) {
  return enaPy("ena_service.plan_reads", {
    runs, prefix: readsPrefix(), ledger: READS_RUNS, force_reupload: $("forceReupload").checked,
  });
}

// Run one upload on the local helper and turn the outcome into a result row.
function uploadOneViaHelper(entry, inputDir, doSubmit) {
  return new Promise(async (resolve) => {
    let job;
    try {
      job = await helperApi("/api/submit", { method: "POST", body: JSON.stringify({
        input_host_dir: inputDir, manifest_filename: entry.manifest_filename,
        manifest_text: entry.manifest_text, submit: doSubmit, test: TEST,
      }) });
    } catch (e) {
      appendReadsLog(`ERROR (${entry.name}): ${e.message}`);
      resolve({ name: entry.name, alias: entry.alias, sample: entry.sample, study: entry.study, success: false, exit_code: 1 });
      return;
    }
    const es = new EventSource(`${HELPER_BASE}/api/stream/${job.job_id}`);
    es.onmessage = async (ev) => {
      const m = JSON.parse(ev.data);
      if (m.line != null) appendReadsLog(m.line);
      if (m.done) {
        es.close();
        let result;
        try {
          result = await py("read_assign.upload_result", {
            name: entry.name, alias: entry.alias, stable_alias: entry.stable_alias,
            exit_code: m.exit_code, log: m.log || "", sample: entry.sample, study: entry.study,
            experiment_accession: m.experiment_accession, run_accession: m.run_accession,
          });
        } catch (e) {
          appendReadsLog(`ERROR recording result (${entry.name}): ${e.message}`);
          result = { name: entry.name, alias: entry.alias, sample: entry.sample, study: entry.study,
            success: m.exit_code === 0, exit_code: m.exit_code,
            experiment_accession: m.experiment_accession || "", run_accession: m.run_accession || "" };
        }
        resolve(result);
      }
    };
    es.onerror = () => {
      es.close();
      appendReadsLog(`ERROR (${entry.name}): lost connection to the local helper.`);
      resolve({ name: entry.name, alias: entry.alias, sample: entry.sample, study: entry.study, success: false, exit_code: 1 });
    };
  });
}

function recordLedger(r) {
  if (!r || !r.name) return;
  READS_RUNS[r.name] = {
    run_name: r.name,
    status: r.skipped ? (r.reason === "already_in_ena" ? "already_in_ena" : "done")
      : (r.success ? "done" : "failed"),
    experiment_accession: r.experiment_accession || "",
    run_accession: r.run_accession || "",
  };
}

// ---------------------------------------------------------------------------
// Manual mode: a webin-cli command instead of the helper
// ---------------------------------------------------------------------------
// Identical up to the plan — the same manifests either way.
// Here they become a shell script the user runs themselves, so this page never
// touches their read files and never needs a helper to be installed.

let READS_SCRIPT_TEXT = "";

/** Quote a value as one single-quoted POSIX shell word. Run aliases can carry
 *  free text from the experiment grid, so nothing reaches a command line raw. */
function _shellQuote(value) {
  return "'" + String(value ?? "").replace(/'/g, "'\\''") + "'";
}

/** A heredoc delimiter that cannot occur inside the manifests it must wrap. */
function _heredocDelimiter(texts) {
  let eof = "MANIFEST_EOF";
  while (texts.some((t) => t.includes(eof))) eof += "_X";
  return eof;
}

function buildReadsScript(entries, doSubmit) {
  const eof = _heredocDelimiter(entries.map((e) => e.manifest_text || ""));
  const lines = [
    "#!/usr/bin/env bash",
    "set -euo pipefail",
    "",
    `# Generated by the MIMICC ENA submission assistant for ${entries.length} run(s).`,
    `# Target: ENA ${TEST ? "TEST — nothing submitted here is permanent." : "PRODUCTION — submissions are permanent."}`,
    "",
    "# The folder holding your read files — check this is right:",
    `READS_DIR=${_shellQuote(readsLocalDir() || "/path/to/your/reads")}`,
    "# webin-cli: https://github.com/enasequence/webin-cli/releases",
    'WEBIN_CLI_JAR="${WEBIN_CLI_JAR:-webin-cli.jar}"',
    "",
    "# Credentials are NOT written into this script. Set them in your shell first:",
    "#   export WEBIN_USERNAME='Webin-XXXXX'",
    "#   read -rs WEBIN_PASSWORD && export WEBIN_PASSWORD",
    ': "${WEBIN_USERNAME:?set WEBIN_USERNAME first}" "${WEBIN_PASSWORD:?set WEBIN_PASSWORD first}"',
    "",
    'MANIFEST_DIR="$(mktemp -d)"',
    'echo "webin-cli reports will be left in $MANIFEST_DIR"',
  ];
  const oneLine = (v) => String(v ?? "").replace(/[\r\n]+/g, " ");
  entries.forEach((entry) => {
    lines.push(
      "",
      `# --- ${oneLine(entry.name)} -> ${oneLine(entry.alias)}`,
      `MANIFEST="$MANIFEST_DIR"/${_shellQuote(entry.manifest_filename)}`,
      `cat > "$MANIFEST" <<'${eof}'`,
      (entry.manifest_text || "").replace(/\n+$/, ""),
      eof,
      'java -jar "$WEBIN_CLI_JAR" -context=reads \\',
      '  -userName="$WEBIN_USERNAME" -password="$WEBIN_PASSWORD" \\',
      '  -manifest="$MANIFEST" \\',
      '  -inputDir="$READS_DIR" -outputDir="$MANIFEST_DIR" \\',
      `  ${doSubmit ? "-submit" : "-validate"}${TEST ? " -test" : ""}`,
    );
  });
  return lines.join("\n") + "\n";
}

/** Build the same plan submitReads() uses, then render it as a
 *  command instead of running it. Results come back on the NEXT generate: a run
 *  that reached ENA is recognised by its stable alias and returns as a skip,
 *  which is what feeds the ledger and the "In ENA" grid. Nothing to relay back,
 *  so there is no log to paste. */
async function generateReadsScript(doSubmit) {
  $("readsLog").textContent = "";
  $("readsResults").innerHTML = "";
  let runs;
  try {
    runs = mergeExperimentMetadata(RUN_ROWS);
  } catch (e) { banner("submitReadsBanner", false, e.message); return; }

  try {
    const { plan, warnings } = await readsPlan(runs);
    (warnings || []).forEach((w) => appendReadsLog("WARNING: " + w));

    const skipped = plan.filter((e) => e.action === "skip");
    skipped.forEach((entry) => {
      appendReadsLog(`=== ${entry.name} === SKIP (${entry.reason})`);
      recordLedger(entry);
    });
    const todo = plan.filter((e) => e.action === "submit");

    READS_SCRIPT_TEXT = todo.length ? buildReadsScript(todo, doSubmit) : "";
    $("readsScript").textContent = READS_SCRIPT_TEXT;
    $("readsScriptWrap").style.display = todo.length ? "block" : "none";

    banner("submitReadsBanner", true, todo.length
      ? `Command ready for ${todo.length} run(s)${skipped.length ? `, ${skipped.length} already done` : ""} — run it in a terminal.`
      : "Nothing left to run — every run is already in ENA.");
    if (skipped.length) renderTable("readsResults", skipped);
    await refreshReadsGrid(skipped);
    renderRunTable();
    saveWorkspaceNow();
  } catch (e) { banner("submitReadsBanner", false, e.message); }
}

function copyReadsScript() {
  if (!READS_SCRIPT_TEXT) return;
  navigator.clipboard.writeText(READS_SCRIPT_TEXT).then(
    () => banner("submitReadsBanner", true, "Copied — paste it into a terminal."),
    (e) => banner("submitReadsBanner", false, `Couldn't copy: ${e.message}`),
  );
}

function downloadReadsScript() {
  if (!READS_SCRIPT_TEXT) return;
  downloadText("submit-reads.sh", READS_SCRIPT_TEXT, "text/x-shellscript");
}
