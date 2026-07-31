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
  historyById: new Map(),
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
  state.historyById = new Map(history.map((h) => [h.id, h]));

  $("chapters").innerHTML = chapters.map((c) => `
    <button class="row" data-chapter="${c.chapter}">
      <span class="num">${c.chapter}</span>
      <span class="label">${escapeHtml(c.title)}</span>
      <span class="count">${c.verse_count}</span>
    </button>`).join("");

  // data-history-id, not data-question: clicking a past question restores its
  // saved answer instantly from what's already in `history` (free), rather
  // than only refilling the box and requiring a fresh, paid /ask call for an
  // answer that was already generated once.
  $("history").innerHTML = history.length
    ? history.map((h) => `
        <button class="row" data-history-id="${h.id}" title="${
          h.answer ? "Click to view this answer again — free, no new request"
                    : "Click to ask this question again"}">
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
  hideHistoryBanner();
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
  // --i drives the staggered rise-in in CSS, so paragraphs land in sequence
  // rather than all at once.
  const html = escapeHtml(text)
    .split(/\n{2,}/)
    .map((p, i) => `<p style="--i:${i}">${p.replace(/\[?\bBG\.?\s*(\d{1,2})[.:](\d{1,3})\b\]?/g,
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
  const t = e.target.closest("[data-verse],[data-chapter],[data-history-id],[data-save],[data-unpin],[data-nav],[data-index],[data-newq]");
  if (!t) return;

  if (t.dataset.newq !== undefined) return startNewQuestion();
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
  if (t.dataset.historyId !== undefined) {
    const entry = state.historyById.get(Number(t.dataset.historyId));
    if (!entry) return;
    renderHistoryEntry(entry);
    return $("q").focus();
  }
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
  // Plain Enter submits, like a normal chat input, as long as the question
  // box is focused (elsewhere on the page Enter shouldn't hijack anything).
  // Shift+Enter keeps its own meaning -- a free preview of just the
  // retrieved verses, no answer generated -- rather than doubling as
  // "insert a newline", since asking short personal questions rarely needs
  // manual line breaks and losing the free preview shortcut would remove
  // the only no-cost way to sanity-check retrieval before spending anything.
  if (e.key === "Enter" && !e.shiftKey && document.activeElement === $("q")) {
    e.preventDefault(); return ask({ retrieveOnly: false });
  }
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

// ---------------------------------------------------------------- export

function citedList() {
  return state.citations.map((c) => c.replace("BG.", "BG ")).join(", ");
}

// PDF export is the browser's own print-to-PDF, not a hand-rolled writer --
// it already handles pagination and font rendering correctly for every
// answer language this app produces (English, Hindi, Gujarati), which
// nothing built here would match without embedding real font files.
function exportPdf() {
  if (!state.answerText) return;
  const q = $("q").value.trim();
  $("printQuestion").textContent = q;
  $("printCited").textContent = state.citations.length ? `Cited: ${citedList()}` : "";
  const prevTitle = document.title;
  document.title = (q || "Madhav answer").slice(0, 80);
  window.print();
  document.title = prevTitle;
}

// PNG export is a plain <canvas> renderer, not a DOM-rasterisation library:
// the foreignObject-based SVG trick that would let real HTML/CSS be
// rasterised is unreliable across browsers for this (tainted canvas,
// inconsistent font handling), and this answer format -- paragraphs plus
// inline citation pills -- is simple enough to lay out by hand instead of
// pulling in an html2canvas-sized dependency for it.
const CITATION_RE = /\[?\bBG\.?\s*(\d{1,2})[.:](\d{1,3})\b\]?/g;

function wrapTokens(text) {
  const tokens = [];
  let last = 0, m;
  CITATION_RE.lastIndex = 0;
  while ((m = CITATION_RE.exec(text))) {
    if (m.index > last) {
      tokens.push(...text.slice(last, m.index).split(/\s+/).filter(Boolean)
        .map((w) => ({ type: "word", text: w })));
    }
    tokens.push({ type: "pill", text: `BG ${m[1]}.${m[2]}` });
    last = CITATION_RE.lastIndex;
  }
  if (last < text.length) {
    tokens.push(...text.slice(last).split(/\s+/).filter(Boolean)
      .map((w) => ({ type: "word", text: w })));
  }
  return tokens;
}

function layoutParagraph(ctx, text, maxWidth, spaceWidth, pillPadX) {
  const lines = [];
  let current = [];
  let width = 0;
  for (const tok of wrapTokens(text)) {
    const tokWidth = tok.type === "pill"
      ? ctx.measureText(tok.text).width + pillPadX * 2
      : ctx.measureText(tok.text).width;
    const addWidth = current.length ? spaceWidth + tokWidth : tokWidth;
    if (current.length && width + addWidth > maxWidth) {
      lines.push(current);
      current = [tok];
      width = tokWidth;
    } else {
      current.push(tok);
      width += addWidth;
    }
  }
  if (current.length) lines.push(current);
  return lines;
}

function wrapPlainText(ctx, text, maxWidth) {
  const words = text.split(/\s+/).filter(Boolean);
  const lines = [];
  let current = "";
  for (const w of words) {
    const candidate = current ? `${current} ${w}` : w;
    if (current && ctx.measureText(candidate).width > maxWidth) {
      lines.push(current);
      current = w;
    } else {
      current = candidate;
    }
  }
  if (current) lines.push(current);
  return lines.length ? lines : [""];
}

function exportPng() {
  if (!state.answerText) return;
  const btn = $("btnExportPng");
  const prevLabel = btn.textContent;
  btn.textContent = "Rendering…";

  const W = 960, PAD_X = 64, PAD_TOP = 56, PAD_BOTTOM = 48;
  const contentWidth = W - PAD_X * 2;
  const LINE_H = 30, TITLE_LINE_H = 30, PARA_GAP = 14, PILL_PAD_X = 8;
  const BODY_FONT = "17px -apple-system, 'SF Pro Text', 'Segoe UI', 'Helvetica Neue', sans-serif";
  const TITLE_FONT = "600 22px -apple-system, 'SF Pro Text', 'Segoe UI', 'Helvetica Neue', sans-serif";
  const FOOT_FONT = "12px -apple-system, 'SF Pro Text', 'Segoe UI', 'Helvetica Neue', sans-serif";

  const measure = document.createElement("canvas").getContext("2d");
  measure.font = BODY_FONT;
  const spaceWidth = measure.measureText(" ").width;

  const question = $("q").value.trim();
  const paragraphs = state.answerText.split(/\n{2,}/).filter(Boolean);
  const paraLines = paragraphs.map((p) =>
    layoutParagraph(measure, p, contentWidth, spaceWidth, PILL_PAD_X));

  measure.font = TITLE_FONT;
  const titleLines = wrapPlainText(measure, question, contentWidth);
  const citedText = state.citations.length ? `Cited: ${citedList()}` : "";

  let totalHeight = PAD_TOP + titleLines.length * TITLE_LINE_H + 20;
  for (const lines of paraLines) totalHeight += lines.length * LINE_H + PARA_GAP;
  if (citedText) totalHeight += 30;
  totalHeight += 64 + PAD_BOTTOM; // divider + footer

  const scale = 2; // crisp on retina displays
  const canvas = document.createElement("canvas");
  canvas.width = W * scale;
  canvas.height = totalHeight * scale;
  const ctx = canvas.getContext("2d");
  ctx.scale(scale, scale);

  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, W, totalHeight);

  let y = PAD_TOP;
  ctx.fillStyle = "#111";
  ctx.font = TITLE_FONT;
  for (const line of titleLines) { ctx.fillText(line, PAD_X, y + 18); y += TITLE_LINE_H; }
  y += 20;

  ctx.font = BODY_FONT;
  for (const lines of paraLines) {
    for (const line of lines) {
      let x = PAD_X;
      for (const tok of line) {
        if (tok.type === "word") {
          ctx.fillStyle = "#222";
          ctx.fillText(tok.text, x, y);
          x += ctx.measureText(tok.text).width + spaceWidth;
        } else {
          const w = ctx.measureText(tok.text).width + PILL_PAD_X * 2;
          ctx.fillStyle = "#f2ece0";
          ctx.beginPath();
          ctx.roundRect(x, y - 15, w, 22, 5);
          ctx.fill();
          ctx.strokeStyle = "#c9a35a";
          ctx.lineWidth = 1;
          ctx.stroke();
          ctx.fillStyle = "#8a6320";
          ctx.fillText(tok.text, x + PILL_PAD_X, y - 1);
          x += w + spaceWidth;
        }
      }
      y += LINE_H;
    }
    y += PARA_GAP;
  }

  if (citedText) {
    ctx.font = FOOT_FONT;
    ctx.fillStyle = "#666";
    ctx.fillText(citedText, PAD_X, y + 4);
    y += 30;
  }

  y += 20;
  ctx.strokeStyle = "#ddd";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(PAD_X, y);
  ctx.lineTo(W - PAD_X, y);
  ctx.stroke();
  y += 24;
  ctx.font = FOOT_FONT;
  ctx.fillStyle = "#888";
  ctx.fillText("Madhav — the Gita, answered and cited", PAD_X, y);

  const a = document.createElement("a");
  a.href = canvas.toDataURL("image/png");
  a.download = `madhav-${(question || "answer").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "answer"}.png`;
  a.click();

  btn.textContent = prevLabel;
}

$("btnPalette").addEventListener("click", openPalette);
$("btnCopy").addEventListener("click", copyMarkdown);
$("btnExportPng").addEventListener("click", exportPng);
$("btnExportPdf").addEventListener("click", exportPdf);
$("btnInspector").addEventListener("click", toggleInspector);
function startNewQuestion() {
  $("q").value = ""; $("answer").innerHTML = ""; $("provenance").innerHTML = "";
  state.retrieved = []; state.citations = []; state.answerText = "";
  hideHistoryBanner();
  $("q").focus();
}

$("btnNew").addEventListener("click", startNewQuestion);

// ---------------------------------------------------------------- utils

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// ---------------------------------------------------------------- boot

// Renders a saved answer from clicking a history row -- deliberately NOT
// called on page load. Restoring the last conversation automatically on
// every refresh made a one-off past answer look like it was still "live",
// as if the page had kept a conversation going rather than just remembering
// what was last asked. A refresh now always starts clean, matching "New
// question"; a past answer only appears when explicitly asked for, and the
// banner below marks it clearly as a saved answer, not a continuing chat.
function renderHistoryEntry(entry) {
  // The question box stays empty, not pre-filled with the old question --
  // dropping it into the live, editable input made a past answer look like
  // it could be continued or edited, the same "ongoing chat" feeling the
  // banner alone didn't fully fix. The historical question is shown as
  // read-only text instead, inside the banner itself.
  $("q").value = "";
  state.retrieved = [];
  state.citations = entry.citations || [];
  state.answerText = entry.answer || "";
  $("provenance").innerHTML = "";
  showHistoryBanner(entry.question);
  if (entry.answer) {
    renderAnswer(entry.answer);
  } else {
    $("answer").innerHTML = "";
  }
}

function showHistoryBanner(question) {
  $("historyBanner").innerHTML = `
    <div class="hb-meta"><span class="hdot"></span> Viewing a saved answer from history
      <button type="button" data-newq>Ask something new</button></div>
    <div class="hb-question">${escapeHtml(question)}</div>`;
  $("historyBanner").classList.add("show");
}

function hideHistoryBanner() {
  $("historyBanner").classList.remove("show");
  $("historyBanner").innerHTML = "";
}

// ------------------------------------------------------------- starfield

// Canvas 2D rather than WebGL/Three.js: a few hundred drifting points is
// nowhere near needing a GPU pipeline, and this project is deliberately
// zero-build and zero-dependency -- pulling in a 600KB 3D library for a
// background texture would trade the whole architecture for an effect that
// plain canvas draws just as well.
function startCosmos() {
  const cv = $("cosmos");
  if (!cv || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const ctx = cv.getContext("2d");
  const warm = getComputedStyle(document.documentElement).getPropertyValue("--gw-star").trim();
  const cool = getComputedStyle(document.documentElement).getPropertyValue("--gw-star-2").trim();

  let stars = [], w = 0, h = 0;
  const DPR = Math.min(window.devicePixelRatio || 1, 2);

  function seed() {
    w = cv.clientWidth; h = cv.clientHeight;
    cv.width = w * DPR; cv.height = h * DPR;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    // Density scaled to area so a large display isn't sparse and a small
    // one isn't a snowstorm.
    const n = Math.round((w * h) / 9000);
    stars = Array.from({ length: n }, () => ({
      x: Math.random() * w,
      y: Math.random() * h,
      r: Math.random() * 1.25 + 0.25,
      vy: Math.random() * 0.05 + 0.012,
      vx: (Math.random() - 0.5) * 0.02,
      a: Math.random() * 0.6 + 0.15,
      tw: Math.random() * 0.014 + 0.003,   // twinkle rate
      up: Math.random() > 0.5,
      cool: Math.random() > 0.72,
    }));
  }

  function frame() {
    ctx.clearRect(0, 0, w, h);
    for (const s of stars) {
      s.a += s.up ? s.tw : -s.tw;
      if (s.a >= 0.8) s.up = false;
      if (s.a <= 0.12) s.up = true;
      s.y -= s.vy; s.x += s.vx;
      if (s.y < -2) { s.y = h + 2; s.x = Math.random() * w; }
      if (s.x < -2) s.x = w + 2;
      if (s.x > w + 2) s.x = -2;

      ctx.globalAlpha = s.a;
      ctx.fillStyle = s.cool ? cool : warm;
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    requestAnimationFrame(frame);
  }

  seed();
  addEventListener("resize", seed);
  requestAnimationFrame(frame);
}

// ---------------------------------------------------------------- boot

// The count tracks real progress -- it starts when boot() starts and only
// completes once the corpus and sidebar have actually loaded, rather than
// running a fixed timer that pretends to be loading something.
function preloader() {
  const el = $("preloader"), pct = $("prePct"), arc = $("preArc");
  if (!el) return { done: () => {} };
  const CIRC = 339.29;
  let shown = 0, target = 8, settled = false;

  const tick = setInterval(() => {
    // Ease toward the target so the number moves continuously instead of
    // jumping between the few real milestones.
    shown += Math.max(0.6, (target - shown) * 0.14);
    if (shown > target) shown = target;
    const v = Math.min(100, Math.round(shown));
    pct.textContent = v + "%";
    arc.style.strokeDashoffset = String(CIRC - (CIRC * v) / 100);
    if (settled && v >= 100) {
      clearInterval(tick);
      el.classList.add("done");
      setTimeout(() => el.remove(), 800);
    }
  }, 40);

  return {
    step: (t) => { target = Math.max(target, t); },
    done: () => { target = 100; settled = true; },
  };
}

(async function boot() {
  const pre = preloader();
  startCosmos();
  pre.step(35);
  await Promise.all([loadHealth(), loadSidebar()]);
  pre.step(92);
  pre.done();
  $("q").focus();
})();
