/* AI Data Control Plane — Console app.
   Drives the interactive demo: sidebar navigation, animated SVG pipeline
   stepper, gate scorecards, semantic search, rollback, a version-detail drawer,
   and the live audit tables. Everything is backed by the /demo/* endpoints,
   which run the REAL control-plane logic in memory. */

"use strict";

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function api(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(body.detail || `HTTP ${r.status}`), { body });
  return body;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove("show"), 2800);
}

/* ---------------- inline Lucide-style icons (no external JS) ---------------- */
const ICONS = {
  "git-branch": '<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
  "layout-dashboard": '<rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/>',
  "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  "git-commit": '<circle cx="12" cy="12" r="4"/><line x1="1.05" y1="12" x2="8" y2="12"/><line x1="16" y1="12" x2="22.95" y2="12"/>',
  "git-merge": '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M6 21V9a9 9 0 0 0 9 9"/>',
  "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  "rotate-ccw": '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
  "activity": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
  "book-open": '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
  "check-circle": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
  "alert-triangle": '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  "file-text": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
  "play": '<polygon points="5 3 19 12 5 21 5 3"/>',
  "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  "check": '<polyline points="20 6 9 17 4 12"/>',
  "rocket": '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
  "shield-x": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><line x1="9.5" y1="9" x2="14.5" y2="14"/><line x1="14.5" y1="9" x2="9.5" y2="14"/>',
};
function icon(name) {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;
}
function paintIcons(root = document) {
  $$("[data-icon]", root).forEach(el => {
    if (el.dataset.painted) return;
    el.innerHTML = icon(el.dataset.icon);
    el.dataset.painted = "1";
  });
}

/* ---------------- relative timestamps ---------------- */
function relTime(iso) {
  if (!iso) return "–";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "–";
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

const STAGES = ["ingest", "validate", "enrich", "embed", "gate", "promote"];
const GATE_LABELS = {
  min_records: "Minimum records", completeness: "Field completeness",
  uniqueness: "Key uniqueness", validation_pass_rate: "Validation pass-rate",
  embedding_coverage: "Embedding coverage", schema_drift: "Schema drift",
};

let running = false;

/* ================================ NAV ================================ */
const VIEW_META = {
  overview:   { title: "Overview", sub: "Live in-memory demo — real pipeline logic, no external infrastructure." },
  ingest:     { title: "Ingest & run", sub: "Trigger the pipeline and watch every stage execute." },
  versions:   { title: "Versions", sub: "Every dataset version the control plane has seen." },
  promotions: { title: "Promotions", sub: "Append-only audit ledger of promote / reject / rollback." },
  search:     { title: "Search", sub: "Query the promoted collection through the blue/green alias." },
};
function switchView(name) {
  $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach(v => v.classList.toggle("active", v.id === `view-${name}`));
  const meta = VIEW_META[name] || VIEW_META.overview;
  $("#view-title").textContent = meta.title;
  $("#view-sub").textContent = meta.sub;
  if (name === "versions") loadVersionsFull();
  if (name === "promotions") loadPromotionsFull();
}

/* ============================== HEALTH ============================== */
async function loadHealth() {
  try {
    const m = await api("/mode");
    const map = {
      full:     { dot: "ok",   label: "full stack online" },
      degraded: { dot: "warn", label: "degraded — some deps down" },
      demo:     { dot: "ok",   label: "demo engine (in-memory)" },
    };
    const s = map[m.mode] || map.demo;
    $("#hdot").className = "health-dot " + s.dot;
    $("#hstatus").textContent = s.label;
    $("#health").title = Object.entries(m.checks || {}).map(([k, v]) => `${k}: ${v}`).join("\n") || "in-memory demo engine";
  } catch {
    $("#hdot").className = "health-dot bad";
    $("#hstatus").textContent = "gateway offline";
  }
}

/* =============================== STATS =============================== */
async function loadStats() {
  try {
    const s = await api("/demo/stats");
    $("#s-versions").textContent    = s.versions;
    $("#s-promoted").textContent    = s.promoted;
    $("#s-rejected").textContent    = s.rejected;
    $("#s-rolled").textContent      = s.rolled_back;
    $("#s-datasets").textContent    = s.datasets;
    $("#s-collections").textContent = s.collections;

    const serving = s.serving || {};
    const keys = Object.keys(serving);
    $("#serving").innerHTML = keys.length
      ? keys.map(ds => `<div class="serving-row"><span class="ds">${esc(ds)}</span>
          <span class="mono muted">${serving[ds] ? esc(serving[ds]) : "nothing promoted"}</span></div>`).join("")
      : '<div class="empty">No datasets yet — run a scenario.</div>';
  } catch { /* backend momentarily unavailable */ }
}

/* ============================== TABLES ============================== */
function statusChip(s) { return `<span class="chip ${esc(s)}">${esc(s)}</span>`; }

async function loadTables() {
  try {
    const [v, p] = await Promise.all([api("/demo/versions?limit=8"), api("/demo/promotions?limit=8")]);
    $("#t-versions").innerHTML = v.versions.length ? v.versions.map(r => `
      <tr class="clickable" data-vid="${esc(r.version_id)}">
        <td class="mono">${esc(shortId(r.version_id))}</td>
        <td>${statusChip(r.status)}</td>
        <td>${r.record_count ?? "–"}</td>
        <td class="muted">${esc(relTime(r.created_at))}</td></tr>`).join("")
      : '<tr><td colspan="4" class="empty">No versions yet.</td></tr>';

    $("#t-promotions").innerHTML = p.promotions.length ? p.promotions.map(r => `
      <tr><td>${statusChip(r.decision)}</td>
      <td class="mono">${esc(shortId(r.version_id))}</td>
      <td class="muted">${esc(r.reason || "")}</td></tr>`).join("")
      : '<tr><td colspan="3" class="empty">No promotions yet.</td></tr>';

    wireVersionRows("#t-versions");
  } catch { /* ignore transient */ }
}

function shortId(id) {
  if (!id) return "–";
  const s = String(id);
  return s.length > 22 ? s.slice(0, 12) + "…" + s.slice(-6) : s;
}

async function loadVersionsFull() {
  const tb = $("#t-versions-full");
  tb.innerHTML = skeletonRows(6, 7);
  try {
    const v = await api("/demo/versions?limit=200");
    $("#ver-count").textContent = v.versions.length ? `${v.versions.length} total` : "";
    tb.innerHTML = v.versions.length ? v.versions.map(r => `
      <tr class="clickable" data-vid="${esc(r.version_id)}">
        <td class="mono">${esc(shortId(r.version_id))}</td>
        <td>${esc(r.dataset)}</td>
        <td>${statusChip(r.status)}</td>
        <td>${r.record_count ?? "–"}</td>
        <td class="muted">${esc(r.trigger_type || "manual")}</td>
        <td class="muted" title="${esc(r.created_at || "")}">${esc(relTime(r.created_at))}</td>
        <td class="muted">details ›</td></tr>`).join("")
      : '<tr><td colspan="7" class="empty">No versions yet.</td></tr>';
    wireVersionRows("#t-versions-full");
  } catch (e) {
    tb.innerHTML = `<tr><td colspan="7" class="empty">Failed to load: ${esc(e.message)}</td></tr>`;
  }
}

async function loadPromotionsFull() {
  const tb = $("#t-promotions-full");
  tb.innerHTML = skeletonRows(6, 5);
  try {
    const p = await api("/demo/promotions?limit=200");
    tb.innerHTML = p.promotions.length ? p.promotions.map(r => `
      <tr><td>${statusChip(r.decision)}</td>
      <td class="mono">${esc(shortId(r.version_id))}</td>
      <td class="mono muted">${esc(r.from_target || "–")}</td>
      <td class="mono muted">${esc(r.to_target || "–")}</td>
      <td class="muted">${esc(r.reason || "")}</td></tr>`).join("")
      : '<tr><td colspan="5" class="empty">No promotions yet.</td></tr>';
  } catch (e) {
    tb.innerHTML = `<tr><td colspan="5" class="empty">Failed to load: ${esc(e.message)}</td></tr>`;
  }
}

function skeletonRows(rows, cols) {
  const cell = '<td><span class="skeleton">placeholder</span></td>';
  return Array.from({ length: rows }, () => `<tr>${cell.repeat(cols)}</tr>`).join("");
}

function wireVersionRows(sel) {
  $$(`${sel} tr.clickable`).forEach(tr => {
    tr.onclick = () => openDrawer(tr.dataset.vid);
  });
}

function refresh() { loadStats(); loadTables(); loadHealth(); }

/* ============================ DRAWER ============================ */
async function openDrawer(versionId) {
  const drawer = $("#drawer"), scrim = $("#drawer-scrim"), body = $("#drawer-body");
  $("#drawer-title").textContent = "Version detail";
  body.innerHTML = '<div class="empty">Loading…</div>';
  drawer.hidden = false; scrim.hidden = false;

  try {
    const d = await api(`/demo/versions/${encodeURIComponent(versionId)}`);
    const v = d.version || {};
    $("#drawer-title").textContent = shortId(versionId);

    const checks = d.quality_checks || [];
    const quarantine = d.quarantine || [];

    body.innerHTML = `
      <div class="detail-block">
        <h3>Metadata</h3>
        <dl class="kv-grid">
          <dt>Version</dt><dd>${esc(v.version_id)}</dd>
          <dt>Dataset</dt><dd>${esc(v.dataset)}</dd>
          <dt>Status</dt><dd>${statusChip(v.status)}</dd>
          <dt>Records</dt><dd>${v.record_count ?? "–"}</dd>
          <dt>Trigger</dt><dd>${esc(v.trigger_type || "manual")}</dd>
          <dt>Created</dt><dd>${esc(v.created_at || "–")}</dd>
        </dl>
      </div>
      <div class="detail-block">
        <h3>Quality checks</h3>
        ${checks.length ? checks.map(c => `
          <div class="gate ${c.passed ? "pass" : "fail"}">
            <div class="gate-check">${icon(c.passed ? "check" : "x")}</div>
            <div><div class="gate-name">${esc(GATE_LABELS[c.check] || c.check)}</div>
              <div class="gate-bar"><i style="width:${gateBarPct(c)}%"></i></div></div>
            <div class="gate-score"><b>${gateScoreText(c)}</b><small>${gateThreshText(c)}</small></div>
          </div>`).join("") : '<div class="empty">No quality checks recorded.</div>'}
      </div>
      <div class="detail-block">
        <h3>Quarantine ${quarantine.length ? `(${quarantine.length})` : ""}</h3>
        ${quarantine.length ? quarantine.slice(0, 25).map(q => `
          <div class="row-item"><div class="mono">${esc(q.id || "—")}</div>
          <div class="meta" style="text-align:right;max-width:65%">${esc(q.reason || "")}</div></div>`).join("")
          : '<div class="empty">No records quarantined.</div>'}
      </div>`;
    paintIcons(body);
  } catch (e) {
    body.innerHTML = `<div class="empty">Failed to load: ${esc(e.message)}</div>`;
  }
}
function closeDrawer() { $("#drawer").hidden = true; $("#drawer-scrim").hidden = true; }

/* ============================ MODAL CONFIRM ============================ */
function confirmModal(message, okLabel = "Confirm") {
  return new Promise(resolve => {
    const scrim = $("#modal-scrim");
    $("#modal-msg").textContent = message;
    $("#modal-ok").textContent = okLabel;
    scrim.hidden = false;
    const cleanup = (val) => {
      scrim.hidden = true;
      $("#modal-ok").onclick = null; $("#modal-cancel").onclick = null;
      resolve(val);
    };
    $("#modal-ok").onclick = () => cleanup(true);
    $("#modal-cancel").onclick = () => cleanup(false);
    scrim.onclick = (e) => { if (e.target === scrim) cleanup(false); };
    $("#modal-ok").focus();
  });
}

/* ============================ PIPELINE ============================ */
function buildStepper() {
  $("#pipe").innerHTML = STAGES.map(stage => `
    <div class="step" data-stage="${stage}" data-state="pending">
      <div class="step-rail"><div class="step-dot">${icon("git-commit")}</div><div class="step-line"></div></div>
      <div class="step-body"><div class="step-name">${stage}</div><div class="step-detail"></div></div>
    </div>`).join("");
  paintIcons($("#pipe"));
}
function resetPipe() {
  $$("#pipe .step").forEach(s => { s.dataset.state = "pending"; $(".step-detail", s).textContent = ""; });
  const v = $("#verdict"); v.hidden = true; v.className = "verdict";
  $("#gates").innerHTML = '<div class="empty">Run a scenario to see gate scorecards.</div>';
}
function setStep(stage, state, text) {
  const s = $(`#pipe .step[data-stage="${stage}"]`);
  if (!s) return;
  s.dataset.state = state;
  const dot = $(".step-dot", s);
  if (state === "done") dot.innerHTML = icon("check");
  else if (state === "failed") dot.innerHTML = icon("x");
  else dot.innerHTML = icon("git-commit");
  if (text != null) $(".step-detail", s).textContent = text;
}

function gateBarPct(c) { return c.check === "min_records" ? (c.passed ? 100 : 30) : Math.min(100, c.score * 100); }
function gateScoreText(c) { return c.check === "min_records" ? c.score : (c.score * 100).toFixed(1) + "%"; }
function gateThreshText(c) { return c.check === "min_records" ? "min " + c.threshold : "≥ " + (c.threshold * 100).toFixed(0) + "%"; }

function renderGates(checks) {
  $("#gates").innerHTML = checks.map(c => `
    <div class="gate ${c.passed ? "pass" : "fail"}">
      <div class="gate-check">${icon(c.passed ? "check" : "x")}</div>
      <div><div class="gate-name">${esc(GATE_LABELS[c.check] || c.check)}</div>
        <div class="gate-bar"><i style="width:${gateBarPct(c)}%"></i></div></div>
      <div class="gate-score"><b>${gateScoreText(c)}</b><small>${gateThreshText(c)}</small></div>
    </div>`).join("");
  paintIcons($("#gates"));
}

function showVerdict(trace) {
  const v = $("#verdict");
  const promoted = trace.decision === "promoted";
  v.hidden = false;
  v.className = `verdict ${promoted ? "promoted" : "rejected"}`;
  const gate = trace.events.find(e => e.stage === "gate");
  const failed = gate?.metrics?.checks?.filter(c => !c.passed).map(c => GATE_LABELS[c.check] || c.check) || [];
  v.innerHTML = `
    <span class="verdict-icon">${icon(promoted ? "rocket" : "shield-x")}</span>
    <div>
      <div class="verdict-title">${promoted ? "Promoted to production" : "Rejected — production untouched"}</div>
      <div class="verdict-detail">${promoted
        ? `Blue/green alias now serves <span class="mono">${esc(trace.serving)}</span>.`
        : `Failed gate(s): <b>${esc(failed.join(", "))}</b>. The previous good version keeps serving.`}</div>
    </div>`;
  paintIcons(v);
}

function renderQuarantine(trace) {
  const val = trace.events.find(e => e.stage === "validate");
  const samples = val?.metrics?.quarantine_samples || [];
  const total = val?.metrics?.quarantined || 0;
  if (!total) { $("#quarantine").innerHTML = '<div class="empty">No quarantined records — data was clean.</div>'; return; }
  $("#quarantine").innerHTML =
    `<div class="serving-note">${total} record(s) quarantined:</div>` +
    samples.map(s => `<div class="row-item"><div class="mono">${esc(s.id || "—")}</div>
      <div class="meta" style="text-align:right;max-width:65%">${esc(s.reason)}</div></div>`).join("") +
    (total > samples.length ? `<div class="serving-note">…and ${total - samples.length} more.</div>` : "");
}

async function runTrace(trace) {
  resetPipe();
  const byStage = Object.fromEntries(trace.events.map(e => [e.stage, e]));
  for (const stage of STAGES) {
    const ev = byStage[stage];
    setStep(stage, "running", "…");
    await sleep(420);
    if (!ev) { setStep(stage, "skipped", "skipped"); continue; }
    const state = ev.status === "failed" ? "failed" : ev.status === "skipped" ? "skipped" : "done";
    setStep(stage, state, ev.detail);
    if (stage === "gate" && ev.metrics?.checks) renderGates(ev.metrics.checks);
    await sleep(150);
  }
  showVerdict(trace);
  renderQuarantine(trace);
  refresh();
}

/* ============================ ACTIONS ============================ */
async function runScenario(id) {
  if (running) return; running = true;
  switchView("ingest");
  try {
    toast(`Running "${id}" pipeline…`);
    const trace = await api("/demo/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario: id }),
    });
    await runTrace(trace);
  } catch (e) { toast("Run failed: " + (e.message || e)); }
  finally { running = false; }
}

async function runCustom() {
  if (running) return;
  let records;
  try {
    const txt = $("#c-json").value.trim();
    records = JSON.parse(txt);
    if (!Array.isArray(records)) records = [records];
  } catch { toast("Invalid JSON — check your syntax."); return; }
  running = true;
  try {
    const trace = await api("/demo/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset: $("#c-dataset").value, records, trigger_type: "manual" }),
    });
    await runTrace(trace);
  } catch (e) { toast("Run failed: " + (e.message || e)); }
  finally { running = false; }
}

async function search() {
  const ds = $("#q-dataset").value, q = $("#q-text").value.trim();
  if (!q) { toast("Type a query first."); return; }
  $("#q-results").innerHTML = '<div class="empty">Searching…</div>';
  try {
    const r = await api(`/demo/search/${encodeURIComponent(ds)}?q=${encodeURIComponent(q)}&limit=5`);
    if (!r.serving_collection) {
      $("#q-results").innerHTML = `<div class="empty">Nothing promoted for "${esc(ds)}" yet — run a scenario first.</div>`;
      return;
    }
    $("#q-results").innerHTML = (r.results.length ? r.results.map(h => {
      const p = h.payload;
      const title = p.title || p.name || Object.values(p)[0];
      const sub = p.category ? `${p.category} · $${p.price ?? "—"}` : (p.author || "");
      return `<div class="row-item">
        <div><div class="ttl">${esc(title)}</div><div class="meta">${esc(sub)}</div></div>
        <div style="text-align:right"><div class="score">${h.score}</div>
        <div class="meta mono">${esc(shortId(h.version_id))}</div></div></div>`;
    }).join("") : '<div class="empty">No results.</div>')
      + `<div class="serving-note">serving: <span class="mono">${esc(r.serving_collection)}</span></div>`;
  } catch (e) {
    $("#q-results").innerHTML = `<div class="empty">Search error: ${esc(e.message || e)}</div>`;
  }
}

async function rollback() {
  const ds = $("#q-dataset").value;
  const ok = await confirmModal(`Roll production for "${ds}" back to the previous promoted version?`, "Roll back");
  if (!ok) return;
  try {
    const r = await api(`/demo/rollback/${encodeURIComponent(ds)}?reason=console+rollback`, { method: "POST" });
    toast(`Rolled back → ${r.now_serving}`);
    refresh(); search();
  } catch (e) { toast("Rollback failed: " + (e.body?.detail || e.message)); }
}

async function reset() {
  const ok = await confirmModal("Reset the demo? This clears all versions, promotions and collections.", "Reset");
  if (!ok) return;
  try {
    await api("/demo/reset", { method: "POST" });
    resetPipe();
    $("#q-results").innerHTML = '<div class="empty">Run a scenario, then search the promoted data.</div>';
    $("#quarantine").innerHTML = '<div class="empty">No quarantined records yet — run the corrupted scenario.</div>';
    toast("Demo reset.");
    refresh();
  } catch (e) { toast("Reset failed: " + (e.body?.detail || e.message)); }
}

/* ============================ SCHEMA HINTS ============================ */
async function loadSchemaHint() {
  try {
    const s = await api("/demo/scenarios");
    const upd = () => {
      const sc = s.schemas[$("#c-dataset").value];
      $("#schema-hint").textContent = sc ? `required: ${sc.required.join(", ")}` : "";
    };
    $("#c-dataset").addEventListener("change", upd); upd();
  } catch { /* schemas unavailable */ }
}

/* ============================ WIRE UP ============================ */
function init() {
  paintIcons();
  buildStepper();
  resetPipe();

  $$(".nav-item").forEach(b => b.onclick = () => switchView(b.dataset.view));
  $$("[data-scenario]").forEach(b => b.onclick = () => runScenario(b.dataset.scenario));
  $("#btn-run-custom").onclick = runCustom;
  $("#q-go").onclick = search;
  $("#q-text").addEventListener("keydown", e => { if (e.key === "Enter") search(); });
  $("#q-rollback").onclick = rollback;
  $("#btn-reset").onclick = reset;
  $("#drawer-close").onclick = closeDrawer;
  $("#drawer-scrim").onclick = closeDrawer;
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") { closeDrawer(); if (!$("#modal-scrim").hidden) $("#modal-cancel").click(); }
  });

  loadSchemaHint();
  refresh();
  setInterval(() => { if (!running) refresh(); }, 5000);
}

document.addEventListener("DOMContentLoaded", init);
