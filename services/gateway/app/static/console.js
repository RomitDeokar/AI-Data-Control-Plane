/* AI Data Control Plane — Console app
   Drives the interactive demo: animated pipeline, gate scorecards, search,
   rollback, live audit tables. All backed by /demo/* endpoints that run the
   REAL control-plane logic in memory. */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c]));
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function api(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(body.detail || r.status), { body });
  return body;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("show"), 2600);
}

const STAGES = ["ingest", "validate", "enrich", "embed", "gate", "promote"];
const GATE_LABELS = {
  min_records: "Minimum records", completeness: "Field completeness",
  uniqueness: "Key uniqueness", validation_pass_rate: "Validation pass-rate",
  embedding_coverage: "Embedding coverage", schema_drift: "Schema drift",
};

let running = false;

/* ---------------- health + stats ---------------- */
async function loadHealth() {
  try {
    const h = await api("/healthz");
    // In the live demo the backing infra is intentionally absent; the demo
    // engine is always healthy, so show "demo mode" rather than "degraded".
    $("#hdot").className = "dot ok";
    $("#hstatus").textContent = "demo engine online";
  } catch {
    $("#hdot").className = "dot bad"; $("#hstatus").textContent = "gateway offline";
  }
}

async function loadStats() {
  try {
    const s = await api("/demo/stats");
    $("#s-versions").textContent    = s.versions;
    $("#s-promoted").textContent    = s.promoted;
    $("#s-rejected").textContent    = s.rejected;
    $("#s-rolled").textContent      = s.rolled_back;
    $("#s-datasets").textContent    = s.datasets;
    $("#s-collections").textContent = s.collections;
  } catch {}
}

async function loadTables() {
  try {
    const [v, p] = await Promise.all([api("/demo/versions?limit=100"), api("/demo/promotions?limit=100")]);
    $("#ver-count").textContent = v.versions.length ? `${v.versions.length} total` : "";
    $("#t-versions").innerHTML = v.versions.length ? v.versions.map(r => `
      <tr><td class="mono">${esc(r.version_id)}</td>
      <td><span class="chip ${esc(r.status)}">${esc(r.status)}</span></td>
      <td>${r.record_count ?? "–"}</td>
      <td class="muted">${esc(r.trigger_type || "manual")}</td></tr>`).join("")
      : '<tr><td colspan="4" class="empty">No versions yet.</td></tr>';

    $("#t-promotions").innerHTML = p.promotions.length ? p.promotions.map(r => `
      <tr><td><span class="chip ${esc(r.decision)}">${esc(r.decision)}</span></td>
      <td class="mono">${esc(r.version_id)}</td>
      <td class="muted">${esc(r.reason || "")}</td></tr>`).join("")
      : '<tr><td colspan="3" class="empty">No promotions yet.</td></tr>';
  } catch {}
}

function refresh() { loadStats(); loadTables(); loadHealth(); }

/* ---------------- pipeline animation ---------------- */
function resetPipe() {
  $$("#pipe .node").forEach(n => {
    n.className = "node pending";
    $(".st", n).textContent = "";
  });
  $("#verdict").className = "verdict";
  $("#gates").innerHTML = "";
}

function setNode(stage, state, text) {
  const n = $(`#pipe .node[data-stage="${stage}"]`);
  if (!n) return;
  n.className = `node ${state}`;
  if (text != null) $(".st", n).textContent = text;
}

function renderGates(checks) {
  $("#gates").innerHTML = checks.map(c => {
    const pct = Math.max(0, Math.min(100, (c.threshold ? (c.score / (c.threshold || 1)) : c.score) * 100));
    const scorePct = c.check === "min_records"
      ? `${c.score} / ${c.threshold}`
      : `${(c.score * 100).toFixed(1)}% <span class="muted">≥ ${(c.threshold * 100).toFixed(0)}%</span>`;
    const barPct = c.check === "min_records" ? (c.passed ? 100 : 30) : Math.min(100, c.score * 100);
    return `<div class="gate ${c.passed ? "pass" : "fail"}">
      <div class="ck">${c.passed ? "✅" : "❌"}</div>
      <div><div class="nm">${esc(GATE_LABELS[c.check] || c.check)}</div>
        <div class="bar"><i style="width:${barPct}%"></i></div></div>
      <div class="sc"><b>${c.check === "min_records" ? c.score : (c.score * 100).toFixed(1) + "%"}</b><br>
        <span class="muted">${c.check === "min_records" ? "min " + c.threshold : "≥ " + (c.threshold * 100).toFixed(0) + "%"}</span></div>
    </div>`;
  }).join("");
}

function showVerdict(trace) {
  const v = $("#verdict");
  const promoted = trace.decision === "promoted";
  v.className = `verdict show ${promoted ? "promoted" : "rejected"}`;
  $("#v-em").textContent = promoted ? "🚀" : "🛑";
  $("#v-title").textContent = promoted ? "PROMOTED to production" : "REJECTED — production untouched";
  const gate = trace.events.find(e => e.stage === "gate");
  const failed = gate?.metrics?.checks?.filter(c => !c.passed).map(c => GATE_LABELS[c.check] || c.check) || [];
  $("#v-detail").innerHTML = promoted
    ? `Blue/green alias now serves <span class="mono">${esc(trace.serving)}</span>. Try a search →`
    : `Failed gate(s): <b>${esc(failed.join(", "))}</b>. The previous good version keeps serving.`;
}

function renderQuarantine(trace) {
  const val = trace.events.find(e => e.stage === "validate");
  const samples = val?.metrics?.quarantine_samples || [];
  const total = val?.metrics?.quarantined || 0;
  if (!total) {
    $("#quarantine").innerHTML = '<div class="empty">No quarantined records — data was clean. ✨</div>';
    return;
  }
  $("#quarantine").innerHTML = `
    <div class="kv" style="margin-bottom:8px">${total} record(s) quarantined with reasons:</div>` +
    samples.map(s => `<div class="hit"><div><span class="mono">${esc(s.id || "—")}</span></div>
      <div class="meta" style="text-align:right;max-width:70%">${esc(s.reason)}</div></div>`).join("") +
    (total > samples.length ? `<div class="kv" style="margin-top:8px">…and ${total - samples.length} more.</div>` : "");
}

async function runTrace(trace) {
  resetPipe();
  const byStage = Object.fromEntries(trace.events.map(e => [e.stage, e]));
  for (const stage of STAGES) {
    const ev = byStage[stage];
    setNode(stage, "running", "…");
    await sleep(430);
    if (!ev) { setNode(stage, "skipped", "skipped"); continue; }
    const state = ev.status === "failed" ? "failed" : ev.status === "skipped" ? "skipped" : "done";
    setNode(stage, state, ev.detail);
    if (stage === "gate" && ev.metrics?.checks) renderGates(ev.metrics.checks);
    await sleep(160);
  }
  showVerdict(trace);
  renderQuarantine(trace);
  refresh();
}

/* ---------------- actions ---------------- */
async function runScenario(id) {
  if (running) return; running = true;
  try {
    toast(`Running “${id}” pipeline…`);
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
  $("#q-results").innerHTML = '<div class="empty">searching…</div>';
  try {
    const r = await api(`/demo/search/${encodeURIComponent(ds)}?q=${encodeURIComponent(q)}&limit=5`);
    if (!r.serving_collection) {
      $("#q-results").innerHTML = `<div class="empty">Nothing promoted for “${esc(ds)}” yet — run a scenario first.</div>`;
      return;
    }
    $("#q-results").innerHTML = (r.results.length ? r.results.map(h => {
      const p = h.payload;
      const title = p.title || p.name || Object.values(p)[0];
      const sub = p.category ? `${p.category} · $${p.price ?? "—"}` : (p.author || "");
      return `<div class="hit">
        <div><div class="ttl">${esc(title)}</div><div class="meta">${esc(sub)}</div></div>
        <div style="text-align:right"><div class="score">${h.score}</div>
        <div class="meta mono">${esc((h.version_id || "").slice(0, 24))}</div></div></div>`;
    }).join("") : '<div class="empty">No results.</div>')
      + `<div class="kv" style="margin-top:8px">serving: <span class="mono">${esc(r.serving_collection)}</span></div>`;
  } catch (e) {
    $("#q-results").innerHTML = `<div class="empty">Search error: ${esc(e.message || e)}</div>`;
  }
}

async function rollback() {
  const ds = $("#q-dataset").value;
  if (!confirm(`Roll production for “${ds}” back to the previous promoted version?`)) return;
  try {
    const r = await api(`/demo/rollback/${encodeURIComponent(ds)}?reason=console+rollback`, { method: "POST" });
    toast(`↩ Rolled back → ${r.now_serving}`);
    refresh(); search();
  } catch (e) { toast("Rollback failed: " + (e.body?.detail || e.message)); }
}

async function reset() {
  if (!confirm("Reset the demo? Clears all versions, promotions and collections.")) return;
  await api("/demo/reset", { method: "POST" });
  resetPipe();
  $("#q-results").innerHTML = '<div class="empty">Run a scenario, then search the promoted data.</div>';
  $("#quarantine").innerHTML = '<div class="empty">No quarantined records yet — run the corrupted scenario.</div>';
  toast("Demo reset.");
  refresh();
}

/* ---------------- schema hints ---------------- */
async function loadSchemaHint() {
  try {
    const s = await api("/demo/scenarios");
    const upd = () => {
      const sc = s.schemas[$("#c-dataset").value];
      $("#schema-hint").textContent = sc ? `required: ${sc.required.join(", ")}` : "";
    };
    $("#c-dataset").addEventListener("change", upd); upd();
  } catch {}
}

/* ---------------- wire up ---------------- */
$$("[data-scenario]").forEach(b => b.onclick = () => runScenario(b.dataset.scenario));
$("#btn-run-custom").onclick = runCustom;
$("#q-go").onclick = search;
$("#q-text").addEventListener("keydown", e => { if (e.key === "Enter") search(); });
$("#q-rollback").onclick = rollback;
$("#btn-reset").onclick = reset;
$$(".tab").forEach(t => t.onclick = () => {
  $$(".tab").forEach(x => x.classList.remove("active")); t.classList.add("active");
  $("#custom-panel").style.display = t.dataset.mode === "custom" ? "block" : "none";
});

loadSchemaHint();
refresh();
setInterval(refresh, 4000);
