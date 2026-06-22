"use strict";

// --------------------------------------------------------------------------- //
// State
// --------------------------------------------------------------------------- //
const state = {
  content: null,          // /api/content payload
  scores: null,           // { bullets: {id: {...}}, items: {id: {...}} }
  packed: null,           // /api/pack payload
  pins: new Set(),        // bullet ids + project ids forced in / open
  excludes: new Set(),    // bullet ids + project ids vetoed
  selectedBullets: new Set(),
  selectedProjects: new Set(),
  query: "",
};

const $ = (sel) => document.querySelector(sel);

// --------------------------------------------------------------------------- //
// API
// --------------------------------------------------------------------------- //
async function api(path, body) {
  const opts = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

function setStatus(msg, cls = "") {
  const el = $("#status");
  el.textContent = msg;
  el.className = "status" + (cls ? " " + cls : "");
}

function packRequest(extra = {}) {
  return {
    jd_text: $("#jd").value,
    pins: [...state.pins],
    excludes: [...state.excludes],
    ...extra,
  };
}

// --------------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------------- //
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;  // textContent: bullet text is raw LaTeX, never HTML
  return n;
}

function itemLabel(it) {
  return it.kind === "job"
    ? { title: it.company || it.title || it.id, sub: [it.title, it.dates].filter(Boolean).join(" · ") }
    : { title: it.name || it.id, sub: [it.tech, it.dates].filter(Boolean).join(" · ") };
}

function bulletScore(id) {
  return state.scores && state.scores.bullets ? state.scores.bullets[id] : null;
}

function matchesQuery(it) {
  const q = state.query;
  if (!q) return { item: true, bullets: null };
  const hay = (s) => (s || "").toLowerCase().includes(q);
  const lbl = itemLabel(it);
  const itemHit = hay(lbl.title) || hay(lbl.sub) || hay(it.fixed_bullet) || (it.tags || []).some(hay);
  const bulletHits = new Set(
    it.bullets.filter((b) => hay(b.text) || (b.tags || []).some(hay)).map((b) => b.id)
  );
  return { item: itemHit || bulletHits.size > 0, bullets: itemHit ? null : bulletHits };
}

// --------------------------------------------------------------------------- //
// Library rendering
// --------------------------------------------------------------------------- //
function renderLibrary() {
  const body = $("#library-body");
  body.innerHTML = "";
  const c = state.content;
  if (!c) return;

  body.appendChild(renderLockedSummary(c));

  appendGroup(body, "Experience (locked — secondary bullets score-governed)", c.experience);
  appendGroup(body, "Projects (inclusion + bullets score-governed)", c.projects);
}

function renderLockedSummary(c) {
  const box = el("div", "locked");
  box.appendChild(el("div", "row")).innerHTML = "<strong>Always shown:</strong> header, education, awards, skills, all jobs + fixed bullets.";
  const contacts = (c.contacts || []).map((x) => x.text).join(" · ");
  if (c.name) box.appendChild(el("div", "row")).append(`${c.name}${contacts ? " — " + contacts : ""}`);
  (c.education || []).forEach((e) =>
    box.appendChild(el("div", "row")).append(`${e.school}${e.degree ? " — " + e.degree : ""}`)
  );
  const skills = (c.skills || []).map((s) => s.category).join(", ");
  if (skills) box.appendChild(el("div", "row")).append("Skills: " + skills);
  return box;
}

function appendGroup(parent, heading, items) {
  if (!items || !items.length) return;
  const h = el("h2", "group-head", heading);
  h.style.margin = "12px 0 8px";
  parent.appendChild(h);
  items.forEach((it) => {
    const card = renderItem(it);
    if (card) parent.appendChild(card);
  });
}

function renderItem(it) {
  const m = matchesQuery(it);
  if (!m.item) return null;

  const card = el("div", "item");
  if (it.kind === "project" && state.selectedProjects.has(it.id)) card.style.borderColor = "var(--accent)";

  const head = el("div", "item-head");
  const lbl = itemLabel(it);
  head.appendChild(el("span", "item-title", lbl.title));
  if (lbl.sub) head.appendChild(el("span", "item-sub", lbl.sub));
  head.appendChild(el("span", "item-kind", it.kind));
  if (it.kind === "project") head.appendChild(projectToggles(it));
  card.appendChild(head);

  // Fixed bullet — always shown when the item appears; never score-governed.
  const fixed = el("div", "fixed-bullet");
  fixed.appendChild(el("span", "lock", "▦ fixed"));
  fixed.appendChild(el("span", "bullet-text", it.fixed_bullet));
  card.appendChild(fixed);

  const visible = m.bullets;  // null => show all
  it.bullets.forEach((b) => {
    if (visible && !visible.has(b.id)) return;
    card.appendChild(renderBullet(it, b));
  });
  return card;
}

function renderBullet(it, b) {
  const row = el("div", "bullet");
  if (state.selectedBullets.has(b.id)) row.classList.add("selected");
  if (state.excludes.has(b.id)) row.classList.add("excluded");

  const main = el("div", "bullet-text");
  main.appendChild(el("span", null, b.text));
  const meta = el("div", "bullet-meta");
  meta.appendChild(el("span", "tier " + b.tier, b.tier));
  (b.tags || []).forEach((t) => meta.appendChild(el("span", "tag", t)));

  const sc = bulletScore(b.id);
  if (sc) {
    const pct = Math.round((sc.pack_score || 0) * 100);
    const scoreEl = el("span", "score" + (pct >= 50 ? " hot" : ""), pct + "%");
    meta.appendChild(scoreEl);
    if (sc.matched && sc.matched.length) {
      meta.appendChild(el("span", "matched", "↳ " + sc.matched.slice(0, 4).join(", ")));
    }
  }
  main.appendChild(meta);
  row.appendChild(main);

  row.appendChild(bulletToggles(b.id));
  return row;
}

function bulletToggles(id) {
  const wrap = el("div", "toggles");
  wrap.appendChild(toggleBtn("pin", "📌", state.pins.has(id), () => flip(state.pins, state.excludes, id)));
  wrap.appendChild(toggleBtn("exclude", "✕", state.excludes.has(id), () => flip(state.excludes, state.pins, id)));
  return wrap;
}

function projectToggles(it) {
  const wrap = el("div", "toggles");
  wrap.appendChild(toggleBtn("pin", "📌 open", state.pins.has(it.id), () => flip(state.pins, state.excludes, it.id)));
  wrap.appendChild(toggleBtn("exclude", "✕ drop", state.excludes.has(it.id), () => flip(state.excludes, state.pins, it.id)));
  return wrap;
}

function toggleBtn(kind, label, active, onClick) {
  const b = el("button", "toggle " + kind + (active ? " active" : ""), label);
  b.type = "button";
  b.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
  return b;
}

// flip `id` in setA; mutually exclusive with setB.
function flip(setA, setB, id) {
  if (setA.has(id)) setA.delete(id);
  else { setA.add(id); setB.delete(id); }
  renderLibrary();
  if (state.packed) repack();  // live re-pack once a proposal exists
}

// --------------------------------------------------------------------------- //
// Proposal rendering
// --------------------------------------------------------------------------- //
function renderProposal() {
  const body = $("#proposal-body");
  body.innerHTML = "";
  const p = state.packed;
  if (!p) { body.innerHTML = '<p class="empty">Auto-pack to build a proposal.</p>'; return; }

  const c = state.content;
  const byId = {};
  [...c.experience, ...c.projects].forEach((it) => (byId[it.id] = it));

  // Experience: always present; show selected secondary bullets.
  c.experience.forEach((job) => {
    const picks = (p.selection.experience || {})[job.id] || [];
    body.appendChild(proposalItem(job, picks));
  });

  // Projects: only those the packer opened, in open order.
  (p.selection.open_projects || []).forEach((pid) => {
    const proj = byId[pid];
    if (proj) body.appendChild(proposalItem(proj, (p.selection.project_bullets || {})[pid] || []));
  });

  $("#proposal-meta").textContent =
    `score ${p.total_score} · ${p.fits ? "one page" : "overflow"}`;
}

function proposalItem(it, pickIds) {
  const card = el("div", "item");
  const head = el("div", "item-head");
  const lbl = itemLabel(it);
  head.appendChild(el("span", "item-title", lbl.title));
  if (lbl.sub) head.appendChild(el("span", "item-sub", lbl.sub));
  head.appendChild(el("span", "item-kind", it.kind));
  card.appendChild(head);

  const fixed = el("div", "fixed-bullet");
  fixed.appendChild(el("span", "lock", "▦"));
  fixed.appendChild(el("span", "bullet-text", it.fixed_bullet));
  card.appendChild(fixed);

  const byId = {};
  it.bullets.forEach((b) => (byId[b.id] = b));
  pickIds.forEach((bid) => {
    const b = byId[bid];
    if (!b) return;
    const row = el("div", "bullet selected");
    row.appendChild(el("span", "bullet-text", b.text));
    card.appendChild(row);
  });
  return card;
}

// --------------------------------------------------------------------------- //
// Fit gauge + LLM badge
// --------------------------------------------------------------------------- //
function renderGauge(p) {
  const gauge = $("#fit-gauge");
  gauge.hidden = false;
  const fill = $("#fit-fill");
  const text = $("#fit-text");
  if (!p.fits) {
    fill.style.width = "100%";
    fill.className = "fit-fill bad";
    text.textContent = p.fit.pages > 1 ? `Overflow (${p.fit.pages} pages)` : "Does not fit";
    return;
  }
  const rem = p.fit.remaining_cm;
  const fullness = rem == null ? 90 : Math.min(100, Math.max(6, 100 - rem * 4));
  fill.style.width = fullness + "%";
  fill.className = "fit-fill" + (rem != null && rem < 1 ? " warn" : "");
  text.textContent = rem != null ? `Fits — ${rem} cm to spare` : "Fits";
}

function renderLlmBadge(used) {
  const b = $("#llm-badge");
  b.textContent = "LLM: " + (used ? "on" : "baseline");
  b.className = "badge " + (used ? "on" : "off");
}

// --------------------------------------------------------------------------- //
// Actions
// --------------------------------------------------------------------------- //
function syncSelectionSets() {
  state.selectedBullets = new Set();
  state.selectedProjects = new Set();
  const p = state.packed;
  if (!p) return;
  Object.values(p.selection.experience || {}).forEach((ids) => ids.forEach((i) => state.selectedBullets.add(i)));
  (p.selection.open_projects || []).forEach((pid) => state.selectedProjects.add(pid));
  Object.values(p.selection.project_bullets || {}).forEach((ids) => ids.forEach((i) => state.selectedBullets.add(i)));
}

async function doScore() {
  try {
    setStatus("scoring…", "busy");
    state.scores = await api("/api/score", { jd_text: $("#jd").value });
    renderLlmBadge(state.scores.llm_used);
    renderLibrary();
    setStatus("scored " + Object.keys(state.scores.bullets).length + " bullets");
  } catch (e) { setStatus("score failed: " + e.message, "error"); }
}

async function doPack() {
  try {
    setStatus("packing + compiling…", "busy");
    const p = await api("/api/pack", packRequest());
    state.packed = p;
    renderLlmBadge(p.llm_used);
    syncSelectionSets();
    renderProposal();
    renderLibrary();
    renderGauge(p);
    reloadPdf();
    $("#export-btn").disabled = false;
    setStatus(p.fits ? "packed — one page" : "packed — overflow, adjust pins/excludes", p.fits ? "" : "error");
  } catch (e) { setStatus("pack failed: " + e.message, "error"); }
}

// re-pack triggered by a pin/exclude toggle (no manual button press)
async function repack() {
  await doPack();
}

async function doExport() {
  try {
    setStatus("exporting…", "busy");
    const r = await api("/api/export", packRequest({ company: $("#company").value || null }));
    state.packed = r;
    renderGauge(r);
    setStatus("archived → " + r.archive);
  } catch (e) { setStatus("export failed: " + e.message, "error"); }
}

function reloadPdf() {
  const iframe = $("#pdf");
  iframe.hidden = false;
  $("#preview-empty").style.display = "none";
  iframe.src = "/api/pdf?t=" + Date.now();  // cache-bust each compile
}

// --------------------------------------------------------------------------- //
// Init
// --------------------------------------------------------------------------- //
async function init() {
  $("#score-btn").addEventListener("click", doScore);
  $("#pack-btn").addEventListener("click", doPack);
  $("#export-btn").addEventListener("click", doExport);
  $("#search").addEventListener("input", (e) => {
    state.query = e.target.value.trim().toLowerCase();
    renderLibrary();
  });

  try {
    state.content = await api("/api/content");
    renderLibrary();
    setStatus("library loaded — paste a JD and Score or Auto-pack");
  } catch (e) {
    setStatus("could not load content: " + e.message, "error");
  }
}

document.addEventListener("DOMContentLoaded", init);
