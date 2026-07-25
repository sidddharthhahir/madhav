// Madhav — zero-build frontend. Served same-origin by FastAPI, so fetch paths
// are relative and there is no CORS preflight on the normal path.

const $ = (id) => document.getElementById(id);

const state = {
  pinned: [],          // verse records, newest first
  retrieved: [],
  answerText: "",
  citations: [],
  health: null,
  busy: false,
  paletteOpen: false,
  paletteRows: [],
  paletteIndex: 0,
  savedIds: new Set(),
};

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...opts,
  });
  if (!res.ok && res.status !== 422) {
    throw new Error(`${opts?.method || "GET"} ${path} → ${res.status}`);
  }
  return res.json();
}

// ---------------------------------------------------------------- sidebar

async function loadSidebar() {
  const [chapters, history, saved] = await Promise.all([
    api("/chapters"), api("/history?limit=12"), api("/saved"),
  ]);

  state.savedIds = new Set(saved.map((s) => s.verse_id));

  $("chapters").innerHTML = chapters.map((c) => `
    <button class="row" data-chapter="${c.chapter}">
      <span class="num">${c.chapter}</span>
      <span class="label">${escapeHtml(c.title)}</span>
      <span class="count">${c.verse_count}</span>
    </button>`).join("");

  $("history").innerHTML = history.length
    ? history.map((h) => `
        <button class="row" data-question="${escapeAttr(h.question)}">
          <span class="dot" style="background:${
            h.status === "ok" ? "var(--gw-accent)" : "var(--gw-muted)"}"></span>
          <span class="label">${escapeHtml(h.question)}</span>
        </button>`).join("")
    : `<div style="padding:4px 8px;font-size:12px;color:var(--gw-muted)">Nothing yet.</div>`;

  $("saved").innerHTML = saved.length
    ? saved.map((s) => `
        <button class="row" data-verse="${s.verse_id}">
          <span class="num">${s.chapter}.${s.verse}</span>
          <span class="label">saved</span>
        </button>`).join("")
    : `<div style="padding:4px 8px;font-size:12px;color:var(--gw-muted)">No saved verses.</div>`;
}

// ---------------------------------------------------------------- health

async function loadHealth() {
  const h = await api("/health");
  state.health = h;
  $("tagline").textContent = "the Gita, answered and cited";

  // Search runs on the plain-meaning notes, not the verse text. If they don't
  // exist the results are noticeably worse, so say that in words a reader
  // understands rather than showing an empty panel that looks broken.
  if (h.enriched === 0) {
    $("notice").innerHTML = `<div class="notice">
      <strong style="color:var(--gw-text-2)">Search is running in a limited mode.</strong>
      Each verse still needs a short plain-English note describing the life
      situations it speaks to — that's what search actually looks through. Those
      notes haven't been written yet, so results will be rough for questions about
      feelings, and the “In plain words” section below each verse will be empty.
      Everything else works normally.</div>`;
  }
}

// ---------------------------------------------------------------- asking

async function ask({ retrieveOnly }) {
  const question = $("q").value.trim();
  if (!question || state.busy) return;

  state.busy = true;
  $("answer").innerHTML = "";
  $("provenance").innerHTML = "";
  setInspectorStages(retrieveOnly);

  try {
    if (retrieveOnly) {
      const out = await api("/preview", {
        method: "POST",
        body: JSON.stringify({ question, k: 8 }),
      });
      state.retrieved = out.retrieved;
      state.answerText = "";
      $("answer").innerHTML =
        `<p style="color:var(--gw-muted)">Retrieval only — no model call was made.
         These are the verses an answer would have been grounded in, and the only
         references it would have been permitted to cite.</p>`;
      renderProvenance(out.citable);
      if (out.retrieved.length) await showVerse(out.retrieved[0].verse_id);
    } else {
      const out = await api("/ask", {
        method: "POST",
        body: JSON.stringify({ question, k: 8 }),
      });
      state.retrieved = out.retrieved || [];
      state.citations = out.citations || [];

      if (!out.ok) {
        renderFailure(out);
      } else {
        state.answerText = out.answer;
        renderAnswer(out.answer);
      }
      renderProvenance(null, out);
      await loadSidebar();
    }
  } catch (err) {
    $("answer").innerHTML =
      `<p style="color:var(--gw-accent)">Request failed: ${escapeHtml(err.message)}</p>`;
  } finally {
    state.busy = false;
    if (!state.pinned.length) renderInspector();
  }
}

const FAILURE_COPY = {
  no_credentials: [
    "No API key configured.",
    "Retrieval works without one — press ⇧↩ to see the verses this question " +
    "would have been grounded in. Set ANTHROPIC_API_KEY to generate answers.",
  ],
  off_topic: [
    "The Gita doesn’t speak to this one.",
    "Try a question about your own situation — what you’re feeling, deciding, " +
    "or struggling with.",
  ],
  no_verses: [
    "Nothing retrieved for this question.",
    "Try rephrasing it in terms of the feeling or the situation.",
  ],
  citation_validation_failed: [
    "Answer withheld — a citation could not be verified.",
    "The model produced text that cited a verse it wasn’t given. Rather than " +
    "serve an answer whose sources don’t check out, the pipeline discarded it. " +
    "This is the guarantee working, not a crash.",
  ],
  refused: ["The request was declined.", "Try rephrasing."],
  empty_question: ["No question provided.", ""],
};

function renderFailure(out) {
  const [head, body] = FAILURE_COPY[out.status] || [out.status, out.detail || ""];
  $("answer").innerHTML = `
    <p style="color:var(--gw-text);font-size:16px;margin-bottom:8px">${escapeHtml(head)}</p>
    <p style="color:var(--gw-muted);font-size:14px">${escapeHtml(body)}</p>
    ${out.detail ? `<p class="kbd" style="margin-top:14px">${escapeHtml(out.detail)}</p>` : ""}`;
}

// Turn [BG 3.37] into a clickable pill. This is the signature interaction:
// the citation is the product.
function renderAnswer(text) {
  const html = escapeHtml(text)
    .split(/\n{2,}/)
    .map((p) => `<p>${p.replace(/\[?\bBG\.?\s*(\d{1,2})[.:](\d{1,3})\b\]?/g,
      (m, c, v) => `<span class="pill" data-verse="BG.${c}.${v}"
                          role="button" tabindex="0">BG ${c}.${v}</span>`)}</p>`)
    .join("");
  $("answer").innerHTML = html;
}

function renderProvenance(citable, out) {
  if (!state.retrieved.length) return;
  const cited = new Set(state.citations);
  $("provenance").innerHTML = `
    <div class="provhead">
      <span class="t">Verses this answer looked at</span>
      <span class="n">${
        out && out.ok
          ? `${cited.size} of ${state.retrieved.length} used · all references checked`
          : `${state.retrieved.length} found, best match first`}</span>
    </div>
    ${state.retrieved.map((r) => `
      <button class="provrow" data-verse="${r.verse_id}"
        title="Match strength ${r.score.toFixed(2)} — higher is a closer match">
        <span class="rank">${r.rank}</span>
        <span class="dot" style="background:${
          cited.has(r.verse_id) ? "var(--gw-accent)" : "var(--gw-rule)"}"></span>
        <span class="ref">BG ${r.verse_id.split(".").slice(1).join(".")}</span>
        <span style="font-size:12px;color:var(--gw-muted)">${
          cited.has(r.verse_id) ? "quoted in the answer" : ""}</span>
        <span class="score">${r.score.toFixed(1)}</span>
      </button>`).join("")}`;
}

// ---------------------------------------------------------------- inspector

function setInspectorStages(retrieveOnly) {
  const stages = retrieveOnly
    ? ["retrieving verses"]
    : ["understanding your question", "retrieving verses", "writing", "verifying citations"];
  $("inspscroll").innerHTML = `<div class="empty">
    ${stages.map((s, i) => `<span class="${i === 0 ? "spin" : ""}"
      style="color:${i === 0 ? "var(--gw-accent)" : "var(--gw-muted)"}">${s}…</span>`).join("")}
  </div>`;
}

async function showVerse(verseId) {
  try {
    const v = await api(`/verse/${verseId}`);
    state.pinned = [v, ...state.pinned.filter((p) => p.verse_id !== verseId)].slice(0, 6);
    renderInspector();
  } catch { /* 404 on an unknown reference is not worth interrupting for */ }
}

function renderInspector() {
  $("pinCount").textContent = state.pinned.length
    ? `${state.pinned.length} open` : "";
  if (!state.pinned.length) {
    $("inspscroll").innerHTML = `<div class="empty">
      <span style="color:var(--gw-text-3);font-size:13px">No verse open yet.</span>
      <span>Ask a question, or click a chapter on the left. You can also press
            ⌘K and type a verse number like 2.47.</span></div>`;
    return;
  }

  $("inspscroll").innerHTML = state.pinned.map((v) => {
    const ref = `${v.chapter}.${v.verse}`;
    const isSaved = state.savedIds.has(v.verse_id);
    const purohit = v.translations.purohit || "—";
    const sivananda = v.translations.siva || "—";
    const plain = v.enrichment && v.enrichment.summary;
    return `
    <section class="vcard">
      <div class="vtop">
        <span class="vref">Chapter ${v.chapter}, verse ${v.verse}</span>
        <span style="flex:1"></span>
        <button class="iconbtn" data-nav="prev" data-verse="${v.verse_id}" title="Previous verse">←</button>
        <button class="iconbtn" data-nav="next" data-verse="${v.verse_id}" title="Next verse">→</button>
        <button class="iconbtn" data-save="${v.verse_id}"
          title="${isSaved ? "Remove from saved" : "Save this verse"}">${isSaved ? "★" : "☆"}</button>
        <button class="iconbtn" data-unpin="${v.verse_id}" title="Close this verse">×</button>
      </div>
      <div class="sanskrit">${escapeHtml(v.sanskrit || "")}</div>
      <div class="tgrid">
        <div class="tcol">
          <span class="tlabel">Purohit Swami</span>
          <span class="tbody">${escapeHtml(purohit)}</span>
        </div>
        <div class="sep"></div>
        <div class="tcol">
          <span class="tlabel">Sivananda</span>
          <span class="tbody">${escapeHtml(sivananda)}</span>
        </div>
      </div>
      <div class="plainbox">
        <span class="tlabel">In plain words</span>
        <span class="plaintext">${
          plain ? escapeHtml(plain)
                : `<span style="color:var(--gw-muted)">Not written yet. This is where a
                   short everyday explanation of the verse will go.</span>`}</span>
      </div>
    </section>`;
  }).join("");
}

// ---------------------------------------------------------------- palette

const VERSE_RE = /^(\d{1,2})\s*[.:]\s*(\d{1,3})$/;

function openPalette() {
  state.paletteOpen = true;
  state.paletteIndex = 0;
  const el = document.createElement("div");
  el.className = "overlay";
  el.id = "overlay";
  el.innerHTML = `
    <div class="palette" role="dialog" aria-modal="true" aria-label="Command palette">
      <input id="pq" placeholder="Verse reference, keyword, or command"
             aria-label="Verse reference, keyword, or command"
             aria-controls="plist" aria-expanded="true" autocomplete="off">
      <ul id="plist" role="listbox" aria-label="Results"></ul>
      <div class="pfoot"><span>↑↓ navigate</span><span>↩ open</span><span>esc close</span></div>
    </div>`;
  document.body.appendChild(el);
  $("pq").focus();
  updatePalette("");

  $("pq").addEventListener("input", (e) => updatePalette(e.target.value));
  el.addEventListener("click", (e) => { if (e.target === el) closePalette(); });
}

function closePalette() {
  state.paletteOpen = false;
  $("overlay")?.remove();
}

async function updatePalette(query) {
  const rows = [];
  const m = query.trim().match(VERSE_RE);
  if (m) {
    rows.push({ ref: `BG ${m[1]}.${m[2]}`, label: "Open this verse",
                hint: "↩", verse: `BG.${m[1]}.${m[2]}` });
  } else if (query.trim().length > 2) {
    try {
      const out = await api(`/search?q=${encodeURIComponent(query)}&k=8`);
      for (const h of out.hits) {
        rows.push({
          ref: `BG ${h.verse_id.split(".").slice(1).join(".")}`,
          label: h.terms.join(", ") || "match",
          hint: h.score.toFixed(2), verse: h.verse_id,
        });
      }
    } catch { /* ignore transient search errors while typing */ }
  } else {
    rows.push({ ref: "⌘↩", label: "Ask the question in the editor", hint: "" });
    rows.push({ ref: "⇧↩", label: "Retrieve only — free, no model call", hint: "" });
  }
  state.paletteRows = rows;
  if (state.paletteIndex >= rows.length) state.paletteIndex = 0;
  renderPaletteRows();
}

function renderPaletteRows() {
  const list = $("plist");
  if (!list) return;
  list.innerHTML = state.paletteRows.map((r, i) => `
    <li role="option" id="popt-${i}" aria-selected="${i === state.paletteIndex}"
        data-index="${i}">
      <span class="lref">${escapeHtml(r.ref)}</span>
      <span>${escapeHtml(r.label)}</span>
      <span class="lhint">${escapeHtml(r.hint || "")}</span>
    </li>`).join("");
  $("pq")?.setAttribute("aria-activedescendant", `popt-${state.paletteIndex}`);
}

function runPaletteRow(row) {
  closePalette();
  if (row?.verse) showVerse(row.verse);
}

// ---------------------------------------------------------------- events

document.addEventListener("click", async (e) => {
  const t = e.target.closest("[data-verse],[data-chapter],[data-question],[data-save],[data-unpin],[data-nav],[data-index]");
  if (!t) return;

  if (t.dataset.index !== undefined) return runPaletteRow(state.paletteRows[+t.dataset.index]);
  if (t.dataset.unpin) {
    state.pinned = state.pinned.filter((p) => p.verse_id !== t.dataset.unpin);
    return renderInspector();
  }
  if (t.dataset.save) {
    const id = t.dataset.save;
    if (state.savedIds.has(id)) {
      await api(`/saved/${id}`, { method: "DELETE" });
      state.savedIds.delete(id);
    } else {
      await api("/saved", { method: "POST", body: JSON.stringify({ verse_id: id }) });
      state.savedIds.add(id);
    }
    renderInspector();
    return loadSidebar();
  }
  if (t.dataset.nav) {
    const [, c, v] = t.dataset.verse.split(".").map(Number.parseFloat);
    const next = t.dataset.nav === "next" ? v + 1 : v - 1;
    if (next >= 1) showVerse(`BG.${c}.${next}`);
    return;
  }
  if (t.dataset.question) { $("q").value = t.dataset.question; return $("q").focus(); }
  if (t.dataset.chapter) {
    const verses = await api(`/chapters/${t.dataset.chapter}`);
    if (verses.length) showVerse(verses[0].verse_id);
    return;
  }
  if (t.dataset.verse) return showVerse(t.dataset.verse);
});

document.addEventListener("keydown", (e) => {
  const meta = e.metaKey || e.ctrlKey;

  if (state.paletteOpen) {
    if (e.key === "Escape") { e.preventDefault(); return closePalette(); }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const n = state.paletteRows.length || 1;
      state.paletteIndex = (state.paletteIndex + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
      return renderPaletteRows();
    }
    if (e.key === "Enter") {
      e.preventDefault();
      return runPaletteRow(state.paletteRows[state.paletteIndex]);
    }
    return;
  }

  if (meta && e.key.toLowerCase() === "k") { e.preventDefault(); return openPalette(); }
  if (meta && e.key === "Enter") { e.preventDefault(); return ask({ retrieveOnly: false }); }
  if (e.shiftKey && e.key === "Enter" && document.activeElement === $("q")) {
    e.preventDefault(); return ask({ retrieveOnly: true });
  }
  if (meta && e.key === "\\") { e.preventDefault(); return toggleInspector(); }
  if (meta && e.shiftKey && e.key.toLowerCase() === "c") {
    e.preventDefault(); return copyMarkdown();
  }
});

function toggleInspector() {
  const el = $("inspector");
  el.style.display = el.style.display === "none" ? "flex" : "none";
}

async function copyMarkdown() {
  const q = $("q").value.trim();
  const body = state.answerText || "(no answer generated)";
  const refs = state.citations.map((c) => `- ${c.replace("BG.", "Bhagavad Gītā ")}`).join("\n");
  const md = `## ${q}\n\n${body}\n\n### Cited\n${refs || "- none"}\n`;
  try {
    await navigator.clipboard.writeText(md);
    $("btnCopy").textContent = "Copied";
    setTimeout(() => ($("btnCopy").textContent = "Copy as Markdown"), 1400);
  } catch {
    $("btnCopy").textContent = "Copy blocked";
    setTimeout(() => ($("btnCopy").textContent = "Copy as Markdown"), 1400);
  }
}

$("btnPalette").addEventListener("click", openPalette);
$("btnCopy").addEventListener("click", copyMarkdown);
$("btnInspector").addEventListener("click", toggleInspector);
$("btnNew").addEventListener("click", () => {
  $("q").value = ""; $("answer").innerHTML = ""; $("provenance").innerHTML = "";
  state.retrieved = []; state.citations = []; state.answerText = "";
  $("q").focus();
});

// ---------------------------------------------------------------- utils

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeAttr(s) { return escapeHtml(s).replace(/\n/g, " "); }

// ---------------------------------------------------------------- boot

(async function boot() {
  await Promise.all([loadHealth(), loadSidebar()]);
  $("q").focus();
})();
