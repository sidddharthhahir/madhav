// Madhav — zero-build frontend. Served same-origin by FastAPI, so fetch paths
// are relative and there is no CORS preflight on the normal path.

const $ = (id) => document.getElementById(id);

// The answer is written by JS after load, so without an explicit live region
// a screen reader is silent through the entire stream and then silent again
// when it completes. "polite" rather than "assertive" so it waits for a pause
// instead of interrupting on every token.
addEventListener("DOMContentLoaded", () => {
  const a = $("answer");
  a.setAttribute("aria-live", "polite");
  a.setAttribute("aria-atomic", "false");
  a.setAttribute("role", "region");
  a.setAttribute("aria-label", "Answer");
});

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
  lastQuestion: "",
  historyById: new Map(),
  chapterCounts: {},   // chapter -> verse_count, from /chapters
  dilemmaMode: false,
  vishvarupaShown: false,
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

  chapters.forEach((c) => { state.chapterCounts[c.chapter] = c.verse_count; });
  renderChapters(chapters);

  // data-history-id, not data-question: clicking a past question restores its
  // saved answer instantly from what's already in `history` (free), rather
  // than only refilling the box and requiring a fresh, paid /ask call for an
  // answer that was already generated once.
  $("history").innerHTML = history.length
    ? history.map((h) => `
        <div class="histrow">
          <button class="row" data-history-id="${h.id}" title="${
            h.answer ? "Click to view this answer again — free, no new request"
                      : "Click to ask this question again"}">
            <span class="dot" style="background:${
              h.status === "ok" ? "var(--gw-accent)" : "var(--gw-muted)"}"></span>
            <span class="label">${escapeHtml(h.question)}</span>
          </button>
          <button class="delbtn" data-del-history="${h.id}"
                  aria-label="Delete this question" title="Delete">×</button>
        </div>`).join("") +
      `<button class="clearall" data-clear-history>Clear all history</button>`
    : `<div style="padding:4px 8px;font-size:12px;color:var(--gw-muted)">Nothing yet.</div>`;

  $("saved").innerHTML = saved.length
    ? saved.map((s) => `
        <button class="row" data-verse="${s.verse_id}">
          <span class="num">${s.chapter}.${s.verse}</span>
          <span class="label">saved</span>
        </button>`).join("")
    : `<div style="padding:4px 8px;font-size:12px;color:var(--gw-muted)">No saved verses.</div>`;
}

// ------------------------------------------------------------------ dhvaja
//
// Every warrior at Kurukshetra fought under a standard -- Hanuman on Arjuna's
// chariot, a serpent for Duryodhana, a palm for Bhishma -- and the chapters
// had nothing but a number.
//
// These are NOT warrior banners: mapping eighteen chapters onto eighteen
// warriors would be invention, and most of those warriors have no attested
// device anyway. Each mark is taken from its own chapter's subject, so it
// says something true about what is inside:
//
//   1  the dropped bow -- Arjuna lays down Gandiva (1.47)
//   2  self within body -- the discrimination Sankhya draws
//   3  the wheel -- action, and the wheel set turning (3.16)
//   4  fire -- "the fire of knowledge burns action to ash" (4.37)
//   5  lotus above water -- untouched as a lotus leaf by water (5.10)
//   6  the steady lamp -- "a lamp in a windless place" (6.19)
//   7  beads on a thread -- "strung on me as pearls on a string" (7.7)
//   8  the syllable -- om, the one imperishable (8.13)
//   9  the crown -- raja-vidya, the sovereign knowledge
//  10  the sun -- "of lights I am the sun" (10.21)
//  11  the burst -- the cosmic form, a thousand suns at once (11.12)
//  12  the leaf -- what devotion offers (9.26), and bhakti's own chapter
//  13  field and knower -- the ksetra, and the one who knows it
//  14  three bands -- the three gunas
//  15  the inverted tree -- the ashvattha, roots above (15.1)
//  16  two opposed -- the divine and the demonic natures
//  17  three flames -- the three kinds of faith
//  18  the parted ring -- moksha, the fetter opened
//
// 24x24, stroke-only, currentColor, so they take the row's own colour and
// stay legible at 18px in the dropdown.
const DHVAJA = [
  // 1 dropped bow: limb bowed, string gone slack
  '<path d="M8 4.5A9 9 0 0 1 8 19.5"/><path d="M8 4.5Q11.5 12 8 19.5"/>',
  // 2 the self within the body
  '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
  // 3 the wheel of action
  '<circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="12" r="1.6"/>'
  + '<path d="M12 4.5v15M4.5 12h15M6.7 6.7l10.6 10.6M17.3 6.7L6.7 17.3"/>',
  // 4 the fire of knowledge
  '<path d="M12 3.5c3.6 4.7 5.2 6.9 5.2 9.6a5.2 5.2 0 0 1-10.4 0c0-2.7 1.6-4.9 5.2-9.6z"/>',
  // 5 the lotus, unwetted
  '<path d="M12 4.5c3.6 3.2 3.6 8.2 0 11-3.6-2.8-3.6-7.8 0-11z"/><path d="M3.5 18.5h17"/>',
  // 6 the lamp in a windless place
  '<path d="M12 4.5c2.4 3.2 3.4 4.7 3.4 6.5a3.4 3.4 0 0 1-6.8 0c0-1.8 1-3.3 3.4-6.5z"/>'
  + '<path d="M5.5 16.5q6.5 4.5 13 0"/><path d="M12 16.5v-2"/>',
  // 7 beads strung on a thread
  '<path d="M2.5 12h19"/><circle cx="7" cy="12" r="2.3"/><circle cx="12" cy="12" r="2.3"/>'
  + '<circle cx="17" cy="12" r="2.3"/>',
  // 8 the one imperishable syllable
  '<circle cx="12" cy="14.5" r="6"/><path d="M7.5 6.2a5.4 5.4 0 0 1 9 0"/>'
  + '<circle cx="12" cy="3.2" r="1.1"/>',
  // 9 the sovereign knowledge
  '<path d="M4 18h16"/><path d="M4 18L5.6 8l3.6 4L12 5.5l2.8 6.5 3.6-4L20 18"/>',
  // 10 of lights, the sun
  '<circle cx="12" cy="12" r="4"/>'
  + '<path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2.1 2.1M16.9 16.9L19 19M19 5l-2.1 2.1M7.1 16.9L5 19"/>',
  // 11 a thousand suns at once
  '<circle cx="12" cy="12" r="2.6"/>'
  + '<path d="M12 1.5v5M12 17.5v5M1.5 12h5M17.5 12h5'
  + 'M4.4 4.4l3.5 3.5M16.1 16.1l3.5 3.5M19.6 4.4l-3.5 3.5M7.9 16.1l-3.5 3.5'
  + 'M7 2.3l1.7 4.1M15.3 17.6l1.7 4.1M2.3 17l4.1-1.7M17.6 8.7l4.1-1.7'
  + 'M2.3 7l4.1 1.7M17.6 15.3l4.1 1.7M7 21.7l1.7-4.1M15.3 6.4l1.7-4.1"/>',
  // 12 what devotion offers
  '<path d="M12 21c-6-3.6-6-11.4 0-18 6 6.6 6 14.4 0 18z"/><path d="M12 3v18"/>',
  // 13 the field, and the one who knows it
  '<path d="M4.5 11h15v8.5h-15z"/><path d="M4.5 15h15"/><circle cx="12" cy="6" r="2"/>',
  // 14 the three gunas
  '<path d="M3.5 7.5h17" stroke-width="3"/><path d="M3.5 12h17" stroke-width="1.9"/>'
  + '<path d="M3.5 16.5h17" stroke-width="1"/>',
  // 15 the ashvattha, roots above
  '<path d="M12 21V8"/>'
  + '<path d="M12 8C9.4 6 7.6 4.6 6 3M12 8c2.6-2 4.4-3.4 6-5M12 8V2.6"/>'
  + '<path d="M12 13.5c-2.4 1.4-3.6 3-4.6 5M12 13.5c2.4 1.4 3.6 3 4.6 5"/>',
  // 16 the two natures, facing
  '<path d="M9.5 4.5L3 12l6.5 7.5z"/><path d="M14.5 4.5L21 12l-6.5 7.5z"/>',
  // 17 the three kinds of faith
  '<path d="M6 19c-1.7-1.6-2-2.7-2-3.8 0-1.3.9-2.4 2-4 1.1 1.6 2 2.7 2 4 0 1.1-.3 2.2-2 3.8z"/>'
  + '<path d="M12 19c-2.2-2.1-2.6-3.5-2.6-5 0-1.7 1.2-3.2 2.6-5.3 1.4 2.1 2.6 3.6 2.6 5.3 0 1.5-.4 2.9-2.6 5z"/>'
  + '<path d="M18 19c-1.7-1.6-2-2.7-2-3.8 0-1.3.9-2.4 2-4 1.1 1.6 2 2.7 2 4 0 1.1-.3 2.2-2 3.8z"/>',
  // 18 the fetter opened
  '<path d="M13.6 4.6a8 8 0 0 1 0 14.8"/><path d="M10.4 4.6a8 8 0 0 0 0 14.8"/>',
];

function dhvaja(chapter) {
  return '<svg class="dhvaja" viewBox="0 0 24 24" aria-hidden="true">'
    + (DHVAJA[chapter - 1] || "") + "</svg>";
}

// Custom listbox rather than <select>. A native select's popup is drawn by the
// OS and cannot be themed, so it arrived as a grey system menu in the middle
// of a parchment sidebar. Owning the panel means owning the keyboard
// behaviour a select gave for free, so all of it is here: arrows, Home/End,
// Enter/Space, Escape, and type-ahead.
function renderChapters(chapters) {
  const wrap = $("chapters");
  wrap.innerHTML = `
    <div class="chapwrap" id="chapWrap">
      <button class="chaptrigger" id="chapTrigger" aria-haspopup="listbox"
              aria-expanded="false" aria-controls="chapList">
        <span id="chapLabel">Choose a chapter…</span><span class="caret">▼</span>
      </button>
      <div class="chaplist" id="chapList" role="listbox" tabindex="-1"
           aria-label="Chapters">
        ${chapters.map((c, i) => `
          <button class="chapopt" role="option" aria-selected="false"
                  data-chapter="${c.chapter}" data-idx="${i}">
            ${dhvaja(c.chapter)}
            <span class="num">${c.chapter}</span>
            <span class="label">${escapeHtml(c.title)}</span>
            <span class="cnt">${c.verse_count}</span>
          </button>`).join("")}
      </div>
    </div>`;

  const wrapEl = $("chapWrap"), trigger = $("chapTrigger"), list = $("chapList");
  const opts = [...list.querySelectorAll(".chapopt")];
  let active = -1, typed = "", typedAt = 0;

  const setActive = (i) => {
    if (active >= 0) opts[active].setAttribute("aria-selected", "false");
    active = Math.max(0, Math.min(opts.length - 1, i));
    opts[active].setAttribute("aria-selected", "true");
    opts[active].scrollIntoView({ block: "nearest" });
  };
  const open = () => {
    wrapEl.classList.add("open");
    trigger.setAttribute("aria-expanded", "true");
    setActive(active < 0 ? 0 : active);
  };
  const close = () => {
    wrapEl.classList.remove("open");
    trigger.setAttribute("aria-expanded", "false");
  };
  const choose = async (i) => {
    const ch = opts[i].dataset.chapter;
    // The chosen chapter keeps flying its standard in the closed trigger.
    $("chapLabel").innerHTML = dhvaja(+ch)
      + `<span>${escapeHtml(opts[i].querySelector(".label").textContent)}</span>`;
    close();
    trigger.focus();
    const verses = await api(`/chapters/${ch}`);
    if (verses.length) showVerse(verses[0].verse_id);
    closeDrawers();
  };

  trigger.addEventListener("click", () =>
    wrapEl.classList.contains("open") ? close() : open());
  opts.forEach((o, i) => {
    o.addEventListener("click", () => choose(i));
    o.addEventListener("mouseenter", () => setActive(i));
  });

  wrapEl.addEventListener("keydown", (e) => {
    const isOpen = wrapEl.classList.contains("open");
    if (!isOpen && (e.key === "Enter" || e.key === " " || e.key === "ArrowDown")) {
      e.preventDefault(); return open();
    }
    if (!isOpen) return;
    if (e.key === "Escape") { e.preventDefault(); close(); return trigger.focus(); }
    if (e.key === "ArrowDown") { e.preventDefault(); return setActive(active + 1); }
    if (e.key === "ArrowUp") { e.preventDefault(); return setActive(active - 1); }
    if (e.key === "Home") { e.preventDefault(); return setActive(0); }
    if (e.key === "End") { e.preventDefault(); return setActive(opts.length - 1); }
    if (e.key === "Enter") { e.preventDefault(); return choose(active); }
    // Type-ahead: letters within a second of each other build one prefix, so
    // "dh" reaches Dhyana rather than jumping to every d- then every h-.
    if (e.key.length === 1 && /\S/.test(e.key)) {
      const now = Date.now();
      typed = (now - typedAt < 1000 ? typed : "") + e.key.toLowerCase();
      typedAt = now;
      const hit = opts.findIndex((o) =>
        o.querySelector(".label").textContent.toLowerCase().startsWith(typed));
      if (hit >= 0) setActive(hit);
    }
  });

  // Any click outside closes it, the way a real menu behaves. Registered once
  // for the lifetime of the page, not per render: renderChapters() runs on
  // every loadSidebar(), so attaching here would add a listener after every
  // question, each one holding a reference to a by-then-detached wrapper.
  if (!renderChapters._outsideBound) {
    document.addEventListener("click", (e) => {
      const w = $("chapWrap");
      if (w && !w.contains(e.target)) {
        w.classList.remove("open");
        const tr = $("chapTrigger");
        if (tr) tr.setAttribute("aria-expanded", "false");
      }
    });
    renderChapters._outsideBound = true;
  }
}

// ---------------------------------------------------------------- health

async function loadHealth() {
  const h = await api("/health");
  state.health = h;

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
  $("answer").classList.remove("settled");
  $("provenance").innerHTML = "";
  $("counterpoint").innerHTML = "";
  hideHistoryBanner();
  setInspectorStages(retrieveOnly);

  try {
    if (retrieveOnly) {
      const out = await api("/preview", {
        method: "POST",
        body: JSON.stringify({ question }),
      });
      state.retrieved = out.retrieved;
      state.answerText = "";
      $("answer").innerHTML =
        `<p style="color:var(--gw-muted)">Retrieval only — no model call was made.
         These are the verses an answer would have been grounded in, and the only
         references it would have been permitted to cite.</p>`;
      renderProvenance(out.citable);
      renderCounterpointButton();
      if (out.retrieved.length) await showVerse(out.retrieved[0].verse_id);
    } else {
      await askStreaming(question);
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

// Streams /ask/stream and renders as it arrives.
//
// Deltas are PROVISIONAL. Citations can only be checked once an answer is
// finished, so streamed text has not been verified yet -- it is shown in a
// visibly unverified state, with a standing "checking citations" note, and
// citation markers are left as plain text rather than becoming pills. Only
// when `done` arrives is the answer re-rendered as the checked article. If a
// draft is rejected, `reset` clears it completely before the retry starts, so
// a rejected draft never survives on screen. `failed` withholds the text
// exactly as the non-streaming path does.
async function askStreaming(question) {
  const res = await fetch("/ask/stream", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question }),
  });

  if (res.status === 429) {
    const body = await res.json().catch(() => ({}));
    $("answer").innerHTML =
      `<p style="color:var(--gw-text);font-size:16px;margin-bottom:8px">Hourly limit reached.</p>
       <p style="color:var(--gw-muted);font-size:14px">${escapeHtml(body.detail || "")}</p>`;
    return;
  }
  if (!res.ok || !res.body) throw new Error(`POST /ask/stream → ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "", draft = "";

  const paintDraft = () => {
    $("answer").innerHTML =
      `<div class="draft">${escapeHtml(draft).replace(/\n{2,}/g, "</p><p>")
        .replace(/^/, "<p>").replace(/$/, "</p>")}</div>
       <div class="draftnote"><span class="spin">checking citations…</span></div>`;
    $("answer").scrollIntoView({ block: "nearest" });
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line; a chunk can split one.
    let sep;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const ev = /^event: (.+)$/m.exec(frame);
      const dt = /^data: (.+)$/m.exec(frame);
      if (!ev || !dt) continue;
      const payload = JSON.parse(dt[1]);

      switch (ev[1]) {
        case "stage":
          if (!draft) setInspectorStages(false, payload.name);
          break;
        case "retrieved":
          // Already verified, so it can be shown immediately -- this is the
          // part that makes the wait feel like progress rather than a hang.
          state.retrieved = payload.verses || [];
          renderProvenance(null, { ok: false });
          break;
        case "delta":
          draft += payload.text;
          paintDraft();
          break;
        case "reset":
          draft = "";
          $("answer").innerHTML =
            `<p style="color:var(--gw-muted);font-size:13px">A draft cited a verse it
             wasn't given, so it was discarded. Rewriting…</p>`;
          break;
        case "done":
          state.retrieved = payload.retrieved || [];
          state.citations = payload.citations || [];
          state.answerText = payload.answer;
          state.lastQuestion = question;
          renderAnswer(payload.answer);
          renderProvenance(null, payload);
          renderCounterpointButton();
          break;
        case "failed":
          state.retrieved = payload.retrieved || [];
          state.answerText = "";
          renderFailure(payload);
          // Offered even on a withheld answer: the retrieval succeeded, so
          // the counterweight is just as available and just as free.
          if (state.retrieved.length) {
            renderProvenance(null, payload);
            renderCounterpointButton();
          }
          break;
      }
    }
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
  $("answer").classList.remove("settled");
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
  // Sanjaya's dashed witness rule resolves to solid gold: this text has
  // passed the citation check. Removed first so re-rendering the same answer
  // (restoring from history) replays it rather than silently doing nothing.
  $("answer").classList.remove("settled");
  void $("answer").offsetWidth;
  $("answer").classList.add("settled");
}

// ----------------------------------------------------------- dharma-sankata
//
// Two options in, verses for each side, and the verses that hold whichever
// you choose. Free -- /dilemma makes no model call.
//
// The middle column is the point. Krishna never tells Arjuna which way to go;
// he changes what the choice means and then says "do as you will" (18.63). A
// split screen alone would just be two searches next to each other.

function setMode(dilemmaOn) {
  state.dilemmaMode = dilemmaOn;
  $("q").hidden = dilemmaOn;
  $("dilemmaInput").hidden = !dilemmaOn;
  $("btnMode").setAttribute("aria-pressed", String(dilemmaOn));
  $("btnMode").textContent = dilemmaOn ? "Ask one question instead"
                                       : "Weighing two options?";
  $("askLabelText").textContent = dilemmaOn ? "The choice" : "Question";
  // Leaving one mode clears the other's output, so a stale answer never sits
  // under a question that is no longer on screen.
  $("dilemmaResult").innerHTML = "";
  if (dilemmaOn) {
    $("answer").innerHTML = "";
    $("answer").classList.remove("settled");
    $("provenance").innerHTML = "";
    $("counterpoint").innerHTML = "";
    $("optA").focus();
  } else {
    $("q").focus();
  }
}

async function runDilemma() {
  const a = $("optA").value.trim(), b = $("optB").value.trim();
  const el = $("dilemmaResult");
  if (!a || !b) {
    el.innerHTML = `<div class="cpwait">Fill in both sides — the point is the tension between them.</div>`;
    return;
  }
  if (state.busy) return;
  state.busy = true;
  el.innerHTML = `<div class="cpwait">holding both…</div>`;

  let res;
  try {
    res = await api("/dilemma", {
      method: "POST",
      body: JSON.stringify({ option_a: a, option_b: b, k: 5 }),
    });
  } catch (err) {
    el.innerHTML = `<div class="cpwait">Request failed: ${escapeHtml(err.message)}</div>`;
    state.busy = false;
    return;
  }
  state.busy = false;

  if (!res.ok) {
    const why = res.reason === "options_identical"
      ? "Both sides say the same thing — there is no dilemma to hold."
      : "Nothing retrieved for either side. Try describing each option as a situation.";
    el.innerHTML = `<div class="cpwait">${escapeHtml(why)}</div>`;
    return;
  }

  const col = (side, tag) => `
    <div class="dl-col">
      <div class="dl-colhead"><span class="dl-tag">${tag}</span>
        <span class="dl-opt">${escapeHtml(side.text)}</span></div>
      ${side.verses.map(verseCard).join("") ||
        `<div class="cpwait">nothing distinct to this side</div>`}
    </div>`;

  el.innerHTML = `
    <div class="dl-head">
      <span class="t">Dharma-sankata</span>
      <span class="n">the Gita does not choose for you</span>
    </div>
    <div class="dl-cols">
      ${col(res.a, "One way")}
      ${col(res.b, "The other")}
    </div>
    ${res.shared.length ? `
      <div class="dl-shared">
        <div class="dl-sharedhead">
          <span class="t">Whichever you choose</span>
          <span class="n">retrieved for both sides</span>
        </div>
        ${res.shared.map(verseCard).join("")}
      </div>` : ""}
    <p class="dl-note">${
      res.overlap >= 0.4
        ? `These two options retrieve <strong>${Math.round(res.overlap * 100)}%</strong>
           the same verses — they may be closer to one option than two.`
        : `The two sides share ${Math.round(res.overlap * 100)}% of their verses,
           so this is genuinely different counsel on each side.`}</p>`;
}

// Who is speaking. Krishna is 82% of the text, so marking HIM everywhere would
// be noise on almost every row -- the tag is only drawn for the other three,
// where it carries the information that actually changes how a verse reads.
const SPEAKER_NOTE = {
  Arjuna: "the question, not the answer",
  Sanjaya: "narration, not instruction",
  Dhritarashtra: "the opening question, not instruction",
};

function speakerTag(speaker, compact) {
  if (!speaker || speaker === "Krishna") return "";
  const note = SPEAKER_NOTE[speaker] || "";
  return `<span class="speaker${compact ? " compact" : ""}" title="${escapeHtml(note)}">`
    + `${escapeHtml(speaker)}</span>`;
}

function verseCard(v) {
  return `
    <button class="dl-verse" data-verse="${v.verse_id}">
      <span class="ref">BG ${v.verse_id.split(".").slice(1).join(".")}${
        v.speaker && v.speaker !== "Krishna" ? " · " + escapeHtml(v.speaker) : ""}</span>
      <span class="body">
        <span class="sum">${escapeHtml(v.summary)}</span>
        ${v.stance && v.stance.length
          ? `<span class="stance">${escapeHtml(v.stance[0])}</span>` : ""}
      </span>
    </button>`;
}

// ------------------------------------------------------------- counterpoint
//
// The verses that face the other way. An answer is grounded in verses that
// matched the question as it was asked, which is what makes it useful and
// also what makes it one-sided -- ask something shaped like self-justification
// and retrieval will hand back the verses that agree with you.
//
// This costs nothing: /counterpoint makes no model call, so it is exempt from
// the spend guard and safe to offer as a plain button. It is still opt-in
// rather than automatic, because the counterweight is worth seeking out
// deliberately and worth nothing if it just appears under every answer.

function renderCounterpointButton() {
  const el = $("counterpoint");
  if (!el) return;
  if (!state.retrieved.length) { el.innerHTML = ""; return; }
  el.innerHTML = `
    <button class="cpbtn" id="btnCounterpoint">
      <span class="cpglyph" aria-hidden="true">⟳</span>
      Show me the opposite
      <span class="cpfree">free · no model call</span>
    </button>`;
}

async function loadCounterpoint() {
  const el = $("counterpoint");
  if (!el || !state.retrieved.length) return;
  el.innerHTML = `<div class="cpwait">finding the other side…</div>`;

  let res;
  try {
    res = await api("/counterpoint", {
      method: "POST",
      body: JSON.stringify({
        verse_ids: state.retrieved.map((r) => r.verse_id), k: 5,
      }),
    });
  } catch (err) {
    el.innerHTML = `<div class="cpwait">Could not load the other side.</div>`;
    return;
  }

  if (!res.ok || !res.verses.length) {
    // Say which of the two reasons it was. "Nothing found" with no
    // explanation is the kind of dead end that reads as a bug.
    const why = res.reason === "no_contrastive_stance"
      ? "These verses don’t carry a stated counter-position, so there is nothing to invert."
      : "Every opposing verse was already in the set above.";
    el.innerHTML = `<div class="cpwait">${escapeHtml(why)}</div>`;
    return;
  }

  el.innerHTML = `
    <div class="cphead">
      <span class="t">The other side</span>
      <span class="n">verses whose stance points away from this question</span>
    </div>
    <p class="cpquery">Searched for: <em>${escapeHtml(
      res.clauses.slice(0, 4).join(" · "))}</em>${
      res.clauses.length > 4 ? ` <span>+${res.clauses.length - 4} more</span>` : ""}</p>
    ${res.verses.map((v) => `
      <button class="cprow" data-verse="${v.verse_id}">
        <span class="ref">BG ${v.verse_id.split(".").slice(1).join(".")}${
          v.speaker && v.speaker !== "Krishna" ? " · " + escapeHtml(v.speaker) : ""}</span>
        <span class="body">
          <span class="sum">${escapeHtml(v.summary)}</span>
          ${v.stance && v.stance.length
            ? `<span class="stance">${escapeHtml(v.stance[0])}</span>` : ""}
        </span>
      </button>`).join("")}`;
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
        title="Ranked ${r.rank} of ${state.retrieved.length} for this question">
        <span class="rank">${r.rank}</span>
        <span class="dot" style="background:${
          cited.has(r.verse_id) ? "var(--gw-accent)" : "var(--gw-rule)"}"></span>
        <span class="ref">BG ${r.verse_id.split(".").slice(1).join(".")}</span>
        ${speakerTag(r.speaker, true)}
        <span style="font-size:12px;color:var(--gw-muted)">${
          cited.has(r.verse_id) ? "quoted in the answer" : ""}</span>
        <span class="score" aria-hidden="true">${
          "\u2588".repeat(Math.max(1, 5 - Math.floor((r.rank - 1) /
            Math.max(1, state.retrieved.length / 5))))}</span>
      </button>`).join("")}`;
}

// ---------------------------------------------------------------- inspector

// `active` comes from the stream's stage events, so the list tracks what is
// actually happening. It used to hard-highlight the first row for the whole
// request, which read as "understanding your question" for fifteen seconds
// no matter what the pipeline was really doing.
const STAGE_LABELS = [
  ["understanding", "understanding your question"],
  ["retrieving", "retrieving verses"],
  ["writing", "writing"],
  ["rewriting", "rewriting after a rejected citation"],
];

function setInspectorStages(retrieveOnly, active) {
  const stages = retrieveOnly
    ? [["retrieving", "retrieving verses"]]
    : STAGE_LABELS;
  const at = Math.max(0, stages.findIndex(([k]) => k === active));
  $("inspscroll").innerHTML = `<div class="empty">
    ${stages.map(([, label], i) => `<span class="${i === at ? "spin" : ""}"
      style="color:${i === at ? "var(--gw-accent)"
                   : i < at ? "var(--gw-text-3)" : "var(--gw-muted)"}"
      >${i < at ? "✓ " : ""}${label}${i === at ? "…" : ""}</span>`).join("")}
  </div>`;
}

async function showVerse(verseId) {
  try {
    const v = await api(`/verse/${verseId}`);
    // Opening a verse has to reveal the panel it opens into. Without this,
    // clicking a citation pill or picking a chapter while the panel is closed
    // does nothing a user can see -- the verse loads into a hidden element and
    // the click reads as broken.
    $("app").classList.remove("hide-inspector");
    state.pinned = [v, ...state.pinned.filter((p) => p.verse_id !== verseId)].slice(0, 6);
    renderInspector();
    if (v.chapter === 11) vishvarupa();
  } catch { /* 404 on an unknown reference is not worth interrupting for */ }
}

// Chapter 11 is the one place the Gita stops explaining and shows. Marked, but
// only once a session: an effect that fires on every chapter-11 verse stops
// being the cosmic form and becomes a tic.
function vishvarupa() {
  if (state.vishvarupaShown) return;
  state.vishvarupaShown = true;
  const el = document.querySelector(".chakra-wrap");
  if (!el) return;
  el.classList.add("vishvarupa");
  // Cleared after the animation so the class cannot linger and pin the
  // watermark at the brightened opacity for the rest of the session.
  setTimeout(() => el.classList.remove("vishvarupa"), 3200);
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
        ${speakerTag(v.speaker)}
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
      ${(v.other_langs && (v.other_langs.hi || v.other_langs.gu)) ? `
      <div class="langbox">
        ${v.other_langs.hi ? `<div class="langrow">
          <span class="tlabel">हिन्दी</span>
          <span class="langbody deva">${escapeHtml(v.other_langs.hi)}</span>
        </div>` : ""}
        ${v.other_langs.gu ? `<div class="langrow">
          <span class="tlabel">ગુજરાતી</span>
          <span class="langbody guj">${escapeHtml(v.other_langs.gu)}</span>
        </div>` : ""}
      </div>` : ""}
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

// ---------------------------------------------------------------- reader
//
// A full-screen takeover for reading the Gita straight through, rather than
// arriving at verses sideways through a question. The app dissolves; what is
// left is the text.
//
// Chapters load one at a time and stay loaded. The whole corpus is only a few
// hundred KB, but fetching all eighteen up front would stall the open, and the
// reader has to feel like a book falling open, not like a page load.
//
// Phase 1 is deliberately image-free. Every verse gets an art slot that is
// currently empty; the procedural and curated visuals drop into it without
// touching any of this.

const READ_KEY = "madhav-reading";
const SOURCE_KEY = "madhav-translator";

const reader = {
  open: false,
  chapters: new Map(),      // chapter -> payload
  order: [],                // flat [{chapter, verse, verse_id}] in reading order
  index: 0,                 // position in `order`
  source: "purohit",
  loading: false,
};

function readerSources(v) {
  return Object.keys(v.translations || {}).sort();
}

// chapter (string) -> ordered list of /static/art files, e.g. {"1": [...]}.
// Built offline by scripts/build_reader_art.py from verified public-domain
// scans; see NOTICE.md for what each depicts and its licence. Most chapters
// have no entry yet -- .rd-art is a real empty slot, not a placeholder, and
// procedural art fills it later without this code changing.
let artMap = null;

async function loadArtMap() {
  if (artMap) return artMap;
  try {
    artMap = await fetch("/static/art/map.json").then((r) => r.json());
  } catch (e) {
    artMap = {};   // the reader works with no art at all; this must not block it
  }
  return artMap;
}

// A chapter's plates are spread evenly across its verses rather than repeated
// or randomised, so a chapter with two plates shows the first for roughly its
// first half and the second for its second half -- a chapter-length fade
// from one image to the next instead of a jump cut on every verse.
function artFor(chapter, verseIndex, verseCount) {
  const list = artMap && artMap[String(chapter)];
  if (!list || !list.length) return null;
  const slot = Math.min(list.length - 1,
    Math.floor((verseIndex / Math.max(1, verseCount)) * list.length));
  return list[slot];
}

// A chapter this size has far more verses than curated plates -- chapter 1
// spreads 2 images over 47 verses, so each one sits, pixel-identical, behind
// roughly 23 consecutive screens. That reads as "the same picture the whole
// time" even though the split is working exactly as designed; scrolling
// through that many screens with no visible change is indistinguishable from
// nothing happening.
//
// Rather than fabricate more art, each verse gets its own deterministic crop
// of whichever plate it was assigned: a small, repeatable hash of the verse
// id drives background-position, so adjacent verses under the same painting
// are still visibly different frames of it. Deterministic on verse_id (not
// random) so a verse looks the same on every visit, not different each time
// the page is reloaded.
// FNV-1a plus a Murmur-style finalizer, not a plain polynomial hash. Tried
// the plain version first (h = h*31 + charCode) and it clustered badly:
// verse ids sharing a chapter share almost their entire string ("BG.2."),
// so a hash that accumulates left-to-right with a small multiplier makes
// h(BG.2.11) and h(BG.2.30) numerically close, and taking %61 kept them
// close too -- adjacent verses landed on nearly the same crop, which is
// exactly the sameness this function exists to break. The finalizer forces
// avalanche: a one-character input difference should flip roughly half the
// output bits, which plain polynomial accumulation does not do on its own.
function versePosition(verseId) {
  let h = 0x811c9dc5;             // FNV offset basis
  for (let i = 0; i < verseId.length; i++) {
    h ^= verseId.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;   // FNV prime
  }
  h ^= h >>> 16; h = Math.imul(h, 0x85ebca6b) >>> 0;
  h ^= h >>> 13; h = Math.imul(h, 0xc2b2ae35) >>> 0;
  // `>>> 0` on this last line is not decoration: `^` in JS returns a SIGNED
  // int32, so without it h can come out negative here even though every
  // prior step was forced unsigned -- and `h % 61` on a negative h returns a
  // negative remainder in JS (unlike Python), producing a CSS percentage
  // like "-8%". Caught by generating positions for 180 verse ids and finding
  // negative values in the output, not by inspection.
  h = (h ^ (h >>> 16)) >>> 0;
  // Kept away from the 0-100 extremes: a crop centred at the very edge of a
  // painting tends to land on empty border or margin rather than the subject.
  const x = 20 + (h % 61);              // 20-80
  const y = 20 + ((h >>> 8) % 61);      // 20-80
  return `${x}% ${y}%`;
}

// Verse scene. The Devanagari leads because it is the thing itself; the
// transliteration under it is what lets someone sound it out without reading
// the script, and it has existed in the corpus all along with nowhere to go.
function renderScene(v, chapter, verseIndex, verseCount) {
  const src = v.translations[reader.source] ? reader.source
            : readerSources(v)[0];
  const body = (v.translations[src] || "").replace(/^\d+\.\d+\s*/, "");
  const art = artFor(chapter, verseIndex, verseCount);
  const artStyle = art
    ? ` style="background-image:url('/static/art/${art}');background-position:${versePosition(v.verse_id)}"`
    : "";
  return `
    <section class="rd-scene" data-verse="${v.verse_id}"
             data-chapter="${chapter}" data-verse-n="${v.verse}">
      <div class="rd-art${art ? "" : " empty"}" aria-hidden="true"${artStyle}></div>
      <div class="rd-body">
        <div class="rd-ref">
          <span class="n">${chapter}.${v.verse}</span>
          ${v.speaker && v.speaker !== "Krishna"
            ? `<span class="speaker">${escapeHtml(v.speaker)}</span>` : ""}
        </div>
        <p class="rd-deva">${escapeHtml(stripEnd(v.sanskrit))}</p>
        <p class="rd-iast">${escapeHtml(stripEnd(v.transliteration))}</p>
        <p class="rd-en">${escapeHtml(body)}</p>
        <button class="rd-more" data-verse="${v.verse_id}">
          commentary &amp; translations →
        </button>
      </div>
    </section>`;
}

// The corpus stores the verse-number marker inside both the Sanskrit and the
// transliteration. It belongs in the reference line, not in the middle of the
// poem.
//
// The digits differ between the two: the transliteration writes ||2-47|| in
// ASCII, the Devanagari writes ||२-४७|| in Devanagari numerals (U+0966-096F).
// A \d-only pattern therefore cleans the transliteration, silently leaves the
// marker sitting in the Sanskrit, and looks like it works.
function stripEnd(s) {
  return String(s || "")
    .replace(/\|\|[\d\u0966-\u096F\-]+\|\|/g, "")
    .replace(/\s+$/, "");
}

function renderChapterCard(payload) {
  return `
    <section class="rd-card" data-chapter="${payload.chapter}">
      <div class="rd-cardmark">${dhvaja(payload.chapter)}</div>
      <div class="rd-cardnum">Chapter ${payload.chapter}</div>
      <h2 class="rd-cardtitle">${escapeHtml(payload.title)}</h2>
      <div class="rd-cardcount">${payload.verse_count} verses</div>
    </section>`;
}

async function loadReaderChapter(n) {
  if (reader.chapters.has(n)) return reader.chapters.get(n);
  const payload = await api(`/read/${n}`);
  reader.chapters.set(n, payload);
  return payload;
}

// Appends a chapter to the scroller. Chapters are appended in order and never
// removed, so `order` stays a straight index into what is on screen.
async function appendChapter(n) {
  await loadArtMap();
  const payload = await loadReaderChapter(n);
  const html = renderChapterCard(payload)
    + payload.verses.map((v, i) =>
        renderScene(v, n, i, payload.verses.length)).join("");
  $("rdScroll").insertAdjacentHTML("beforeend", html);
  payload.verses.forEach((v) =>
    reader.order.push({ chapter: n, verse: v.verse, verse_id: v.verse_id }));
  return payload;
}

async function openReader(startChapter) {
  if (reader.open) return;
  reader.open = true;
  try { reader.source = localStorage.getItem(SOURCE_KEY) || "purohit"; } catch (e) { /**/ }

  const el = $("reader");
  el.hidden = false;
  el.removeAttribute("aria-hidden");
  document.body.classList.add("reading");

  const resume = startChapter ? { chapter: startChapter, verse: 1 } : readingMark();
  await enterReaderAt(resume);
}

// The actual content load, split out from openReader() so a failed attempt
// can be retried without re-running the open guard above. `reader.open` means
// "the shell is on screen" -- it stays true across a failure so the close
// button keeps working, which means openReader() itself can't be called
// again to retry (its guard would just return). Retry calls this directly.
async function enterReaderAt(resume) {
  $("rdScroll").innerHTML = "";
  reader.chapters.clear();
  reader.order = [];
  reader.lastResume = resume;

  try {
    await appendChapter(resume.chapter);
  } catch (err) {
    // The shell is already visible at this point (so the close button works
    // and the app underneath stays hidden), but nothing had filled it yet.
    // This used to be an unhandled promise rejection: the reader was left
    // open, empty, with no error and no way to tell what happened short of
    // opening devtools -- indistinguishable from the page being broken.
    console.error("reader: failed to load chapter %d", resume.chapter, err);
    $("rdScroll").innerHTML = `
      <section class="rd-scene rd-error">
        <div class="rd-body">
          <p class="rd-en">Could not load this chapter.</p>
          <p class="rd-iast" style="font-style:normal">${escapeHtml(err.message || String(err))}</p>
          <button class="rd-more rd-retry" id="rdRetry">try again →</button>
        </div>
      </section>`;
    return;
  }
  paintSource();
  // Scroll to the resumed verse before the reader is interactive, so it does
  // not visibly jump from the chapter card down to where you left off.
  const target = $("rdScroll").querySelector(
    `[data-verse="BG.${resume.chapter}.${resume.verse}"]`);
  // "instant", not the stylesheet's smooth. Two reasons: resuming should put
  // you where you were, not scroll you there past everything in between; and
  // scroll-snap-stop:always makes a smooth scroll halt at the very next snap
  // point, so a multi-verse jump silently does not arrive.
  if (target) target.scrollIntoView({ block: "start", behavior: "instant" });
  reader.index = Math.max(0, readerScenes().indexOf(target));
  $("rdScroll").focus({ preventScroll: true });
  updateReaderPosition();
}

function closeReader() {
  if (!reader.open) return;
  reader.open = false;
  const el = $("reader");
  el.hidden = true;
  el.setAttribute("aria-hidden", "true");
  document.body.classList.remove("reading");
  $("btnRead").focus();
}

// Where the reader left off, so the book reopens where it closed.
function readingMark() {
  try {
    const raw = localStorage.getItem(READ_KEY);
    if (raw) {
      const m = JSON.parse(raw);
      if (m && m.chapter >= 1 && m.chapter <= 18) return m;
    }
  } catch (e) { /* private mode */ }
  return { chapter: 1, verse: 1 };
}

function saveReadingMark(chapter, verse) {
  try {
    localStorage.setItem(READ_KEY, JSON.stringify({ chapter, verse }));
  } catch (e) { /* private mode */ }
}

// Position in all 701 rather than within the chapter: the reader treats the
// Gita as one continuous text, and the chapters are stations along it.
//
// Derived from /chapters rather than written out. This corpus is the Gita
// Press recension, where chapter 13 has 35 verses and not the 34 most editions
// print -- a hard-coded table would be wrong for exactly one chapter and would
// stay wrong quietly.
function chapterOffset(chapter) {
  let n = 0;
  for (let i = 1; i < chapter; i++) n += state.chapterCounts[i] || 0;
  return n;
}
function corpusSize() {
  return Object.values(state.chapterCounts).reduce((a, b) => a + b, 0) || 701;
}

function updateReaderPosition() {
  const scenes = readerScenes();
  if (!scenes.length) return;
  const mid = $("rdScroll").scrollTop + $("rdScroll").clientHeight / 2;
  let current = scenes[0];
  for (const s of scenes) {
    if (s.offsetTop <= mid) current = s; else break;
  }
  reader.index = scenes.indexOf(current);
  // Chapter cards carry no verse number. Report the verse that follows.
  if (!current.dataset.verseN) {
    current = scenes[scenes.indexOf(current) + 1] || current;
  }
  if (!current.dataset.verseN) return;
  const ch = +current.dataset.chapter, vn = +current.dataset.verseN;
  $("rdWhere").textContent = `${ch}.${vn}`;
  saveReadingMark(ch, vn);
  const absolute = chapterOffset(ch) + vn;
  $("rdRail").style.height = `${(absolute / corpusSize()) * 100}%`;

  // Pull in the next chapter before the reader reaches the end of this one.
  const last = scenes[scenes.length - 1];
  if (current === last || scenes.indexOf(current) > scenes.length - 4) {
    const next = +last.dataset.chapter + 1;
    if (next <= 18 && !reader.chapters.has(next) && !reader.loading) {
      reader.loading = true;
      appendChapter(next).finally(() => { reader.loading = false; });
    }
  }
}

function paintSource() {
  const any = reader.chapters.values().next().value;
  if (!any) return;
  const names = readerSources(any.verses[0]);
  const label = reader.source in any.verses[0].translations ? reader.source : names[0];
  $("rdSource").textContent = label;
  $("rdSource").title = `Translation: ${label} — click for ${
    names[(names.indexOf(label) + 1) % names.length]}`;
}

function cycleSource() {
  const any = reader.chapters.values().next().value;
  if (!any) return;
  const names = readerSources(any.verses[0]);
  const i = names.indexOf(reader.source);
  reader.source = names[(i + 1) % names.length];
  try { localStorage.setItem(SOURCE_KEY, reader.source); } catch (e) { /**/ }
  // Repaint the visible translations in place rather than re-rendering the
  // scroller, which would lose the scroll position mid-read.
  for (const [ch, payload] of reader.chapters) {
    for (const v of payload.verses) {
      const node = $("rdScroll").querySelector(
        `[data-verse="${v.verse_id}"] .rd-en`);
      if (node) {
        const src = v.translations[reader.source] ? reader.source
                  : readerSources(v)[0];
        node.textContent = (v.translations[src] || "").replace(/^\d+\.\d+\s*/, "");
      }
    }
  }
  paintSource();
}

// Step one verse.
//
// Driven by an explicit cursor rather than by re-reading scrollTop each press.
// Deriving the position per keystroke looked correct and was not: a held
// arrow key issues presses faster than the scroll lands, so every press
// recomputed "where am I" from a position that had not arrived yet and
// re-targeted almost the same verse. Twenty-nine presses moved half a screen.
//
// `behavior: instant` for the same reason smooth is wrong on resume: with
// scroll-snap-stop:always an animated scroll halts at the next snap point,
// and successive animations interrupt each other. Snap already makes stepping
// feel discrete; animating it adds nothing and breaks holding the key.
function readerScenes() {
  return [...$("rdScroll").querySelectorAll(".rd-scene, .rd-card")];
}

function readerStep(delta) {
  const scenes = readerScenes();
  if (!scenes.length) return;
  reader.index = Math.max(0, Math.min(scenes.length - 1, reader.index + delta));
  scenes[reader.index].scrollIntoView({ behavior: "instant", block: "start" });
}

// ---------------------------------------------------------------- events

document.addEventListener("click", async (e) => {
  const t = e.target.closest("#btnCounterpoint,#btnMode,#btnDilemma,.rd-more,[data-verse],[data-history-id],[data-save],[data-unpin],[data-nav],[data-index],[data-newq],[data-del-history],[data-clear-history]");
  if (!t) return;

  if (t.id === "btnCounterpoint") return loadCounterpoint();
  // Checked before the generic .rd-more handling below, which this button
  // also carries (for the same look) but must not fall into: it has no
  // data-verse, and closeReader() + showVerse(undefined) would silently
  // dismiss the reader instead of retrying it. Calls enterReaderAt()
  // directly, not openReader() -- reader.open is still true from the failed
  // attempt (that is what keeps the close button working), so openReader()'s
  // own re-entry guard would just return without doing anything.
  if (t.id === "rdRetry") return enterReaderAt(reader.lastResume);
  // From inside the reader, "commentary & translations" hands off to the
  // ordinary verse panel -- the reader is for reading, the panel is for study.
  if (t.classList.contains("rd-more")) {
    closeReader();
    return showVerse(t.dataset.verse);
  }
  if (t.id === "btnMode") return setMode(!state.dilemmaMode);
  if (t.id === "btnDilemma") return runDilemma();
  if (t.dataset.newq !== undefined) return startNewQuestion();
  if (t.dataset.delHistory) {
    await api(`/history/${t.dataset.delHistory}`, { method: "DELETE" });
    return loadSidebar();
  }
  if (t.dataset.clearHistory !== undefined) {
    // Destructive and not undoable, so it asks first.
    if (!confirm("Delete all saved questions and answers? This cannot be undone.")) return;
    await api("/history", { method: "DELETE" });
    startNewQuestion();
    return loadSidebar();
  }
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
  if (t.dataset.verse) return showVerse(t.dataset.verse);
});

document.addEventListener("keydown", (e) => {
  const meta = e.metaKey || e.ctrlKey;

  // The reader takes over the whole window, so it takes over the keyboard
  // too -- and returns first, before any of the app's own shortcuts can fire
  // underneath a view that is covering them.
  if (reader.open) {
    if (e.key === "Escape") { e.preventDefault(); return closeReader(); }
    if (e.key === "ArrowDown" || e.key === "ArrowRight" || e.key === "PageDown"
        || (e.key === " " && !e.shiftKey)) {
      e.preventDefault(); return readerStep(1);
    }
    if (e.key === "ArrowUp" || e.key === "ArrowLeft" || e.key === "PageUp"
        || (e.key === " " && e.shiftKey)) {
      e.preventDefault(); return readerStep(-1);
    }
    return;
  }

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

// Class-based rather than inline style.display: an inline style wins over
// every media query, so the old version pinned the panel visible on a phone
// where the layout needs it off-canvas.
function toggleInspector() {
  $("app").classList.toggle("hide-inspector");
  $("app").classList.remove("show-sidebar");
}

function toggleSidebar() {
  $("app").classList.toggle("show-sidebar");
  $("app").classList.add("hide-inspector");
}

function closeDrawers() {
  $("app").classList.remove("show-sidebar");
}

// The copy and print buttons are gone from the toolbar; both still work from
// the keyboard, so this reports success somewhere other than a button label.
function flashStatus(msg) {
  const el = $("notice");
  el.innerHTML = `<div class="notice">${escapeHtml(msg)}</div>`;
  setTimeout(() => { el.innerHTML = ""; }, 1600);
}

async function copyMarkdown() {
  const q = $("q").value.trim();
  const body = state.answerText || "(no answer generated)";
  const refs = state.citations.map((c) => `- ${c.replace("BG.", "Bhagavad Gītā ")}`).join("\n");
  const md = `## ${q}\n\n${body}\n\n### Cited\n${refs || "- none"}\n`;
  try {
    await navigator.clipboard.writeText(md);
    flashStatus("Answer copied");
  } catch {
    flashStatus("Copy blocked by the browser");
  }
}

// ---------------------------------------------------------------- export

function citedList() {
  return state.citations.map((c) => c.replace("BG.", "BG ")).join(", ");
}

// Fill the print-only blocks whenever a print is about to start, however it
// was triggered. They used to be populated by an Export PDF button; that
// button is gone, so without this a native Cmd+P produced the answer with an
// empty question heading and no citation list. beforeprint covers Cmd+P, the
// File menu and print preview alike.
addEventListener("beforeprint", () => {
  $("printQuestion").textContent = $("q").value.trim() || state.lastQuestion || "";
  $("printCited").textContent =
    state.citations.length ? `Cited: ${citedList()}` : "";
});

$("btnRead").addEventListener("click", () => openReader());
$("rdClose").addEventListener("click", closeReader);
$("rdSource").addEventListener("click", cycleSource);
// Passive: this only reads layout and writes a rail height, so it must never
// be able to block the scroll it is measuring.
$("rdScroll").addEventListener("scroll", () => {
  if (reader.raf) return;
  reader.raf = requestAnimationFrame(() => {
    reader.raf = 0;
    updateReaderPosition();
  });
}, { passive: true });

$("btnPalette").addEventListener("click", openPalette);
$("btnInspector").addEventListener("click", toggleInspector);
function startNewQuestion() {
  $("q").value = ""; $("answer").innerHTML = ""; $("provenance").innerHTML = "";
  $("answer").classList.remove("settled");
  $("counterpoint").innerHTML = "";
  $("dilemmaResult").innerHTML = "";
  $("optA").value = ""; $("optB").value = "";
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
  // History rows store citations but not the full retrieval set, so there is
  // nothing to invert -- the button stays hidden rather than appearing and
  // then failing.
  $("counterpoint").innerHTML = "";
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

// ----------------------------------------------------------------- theme

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
  // Sampled once. There is only one palette now, so there is nothing that
  // could later change these.
  const cs = getComputedStyle(document.documentElement);
  const warm = cs.getPropertyValue("--gw-star").trim();
  const cool = cs.getPropertyValue("--gw-star-2").trim();

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

$("btnMenu").addEventListener("click", toggleSidebar);
$("btnCloseSidebar").addEventListener("click", closeDrawers);
$("btnCloseInspector").addEventListener("click",
  () => $("app").classList.add("hide-inspector"));
$("scrim").addEventListener("click", closeDrawers);

// Below the drawer breakpoint the verse panel covers the answer, so it starts
// closed. Without this the first mobile load opens straight into the panel
// with the scrim over everything, since "no class" means "open" on desktop.
function applyInitialLayout() {
  if (innerWidth <= 1000) $("app").classList.add("hide-inspector");
}

(async function boot() {
  const pre = preloader();
  startCosmos();
  applyInitialLayout();
  pre.step(35);
  await Promise.all([loadHealth(), loadSidebar()]);
  pre.step(92);
  pre.done();
  $("q").focus();
})();
