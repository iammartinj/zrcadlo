/* Zrcadlo - rozhrani. Vsechna data jdou z lokalniho serveru, nic zvenku. */
"use strict";

const $ = (id) => document.getElementById(id);
const TICKS = 48;

const state = {
  slug: null,
  book: null,
  chapter: 1,
  segments: [],
  status: null,
  running: false,
  runKind: null,
  stream: null,
  live: { tps: null, eta: null, note: "" },
  glossary: [],
};

const CATEGORIES = [["osoba", "osoba"], ["misto", "místo"],
                    ["organizace", "organizace"], ["pojem", "pojem"]];
const GENDERS = [["", "—"], ["m", "m"], ["f", "ž"], ["n", "s"]];

/* ---------------- pomocne ---------------- */

async function api(path, options) {
  const res = await fetch(path, options);
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const msg = (data && data.error) || (res.status + " " + res.statusText);
    throw new Error(msg);
  }
  return data;
}

function num(n) {
  return (n || 0).toLocaleString("cs-CZ");
}

/* Cestina sklonuje podle poctu: 1 polozka, 2 az 4 polozky, 5 a vic polozek. */
function plural(n, one, few, many) {
  const abs = Math.abs(n);
  if (abs === 1) return one;
  if (abs >= 2 && abs <= 4) return few;
  return many;
}

function withNum(n, one, few, many) {
  return num(n) + " " + plural(n, one, few, many);
}

function minutes(sec) {
  if (sec === null || sec === undefined) return "—";
  if (sec < 60) return "< 1 min";
  if (sec < 3600) return Math.round(sec / 60) + " min";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h + " h " + m + " min";
}

function notice(kind, text, hint) {
  const el = $("notice");
  if (!text) { el.className = "notice"; el.innerHTML = ""; return; }
  el.className = "notice on notice--" + kind;
  el.innerHTML = "";
  const b = document.createElement("b");
  b.textContent = text;
  el.appendChild(b);
  if (hint) {
    const h = document.createElement("span");
    h.className = "hint";
    h.textContent = hint;
    el.appendChild(h);
  }
}

/* ---------------- stav stroje ---------------- */

async function loadStatus() {
  let s;
  try {
    s = await api("/api/status");
  } catch (e) {
    notice("bad", "Server Zrcadla neodpovídá.", "Zkontroluj okno, ve kterém běží run.bat.");
    return;
  }
  state.status = s;
  fillModels(s);
  $("st-ctx").textContent = "kontext " + s.context;

  const lm = s.lm_studio || {};
  const dot = $("modeldot");
  dot.className = "dot" + (lm.ok ? "" : (lm.reason === "model" ? " dot--warn" : " dot--off"));
  if (!lm.ok && !state.running) {
    notice(lm.reason === "model" ? "warn" : "bad", lm.message, lm.hint);
  } else if (lm.ok && $("notice").classList.contains("notice--bad")) {
    notice(null);
  }

  const gpu = s.gpu;
  const line = $("st-gpu");
  if (gpu && gpu.total_mb) {
    line.hidden = false;
    line.textContent = "GPU " + num(gpu.total_mb) + " MB · využito " + num(gpu.used_mb) + " MB";
  } else {
    line.hidden = true;   // nvidia-smi nic neřekl, radek se vynecha
  }
  updateGo();
}

/* Vyber modelu z toho, co ma LM Studio nactene. Vkladaci modely se vynechavaji,
   ty prekladat neumeji. */
function fillModels(s) {
  const pick = $("modelpick");
  const lm = s.lm_studio || {};
  const list = (lm.models || []).filter((m) => !/embed/i.test(m));
  if (!list.length) {
    pick.innerHTML = "";
    const o = document.createElement("option");
    o.textContent = s.model;
    pick.appendChild(o);
    pick.disabled = true;
    pick.title = "Seznam modelů se načte, až poběží LM Studio.";
    return;
  }
  const known = [...pick.options].map((o) => o.value).join("|");
  if (known !== list.join("|")) {
    pick.innerHTML = "";
    list.forEach((m) => {
      const o = document.createElement("option");
      o.value = m;
      o.textContent = m;
      pick.appendChild(o);
    });
  }
  if (list.includes(s.model)) pick.value = s.model;
  pick.disabled = state.running;
  pick.title = state.running
    ? "Model se dá přepnout, až doběhne rozdělaná práce."
    : "Model pro překlad. Volba se uloží do config.json.";
}

async function chooseModel(name) {
  try {
    const res = await api("/api/model?model=" + encodeURIComponent(name),
                          { method: "POST" });
    notice("ok", "Model přepnut na " + res.model + ".",
      "Volba je uložená v config.json a platí pro další dávky.");
    await loadStatus();
  } catch (e) {
    notice("bad", "Model se nepodařilo přepnout.", e.message);
    await loadStatus();
  }
}

/* ---------------- projekty ---------------- */

async function loadProjects(preferred) {
  let list = [];
  try {
    list = (await api("/api/projects")).projects || [];
  } catch (e) { /* server uz hlasi chybu jinde */ }

  const box = $("projlist");
  box.innerHTML = "";
  list.forEach((p) => {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.slug = p.slug;
    b.setAttribute("aria-current", String(p.slug === state.slug));
    const n = document.createElement("span");
    n.className = "pl-name";
    n.textContent = p.title;
    const c = document.createElement("span");
    c.className = "pl-num";
    c.textContent = num(p.done) + "/" + num(p.total);
    b.append(n, c);
    b.addEventListener("click", () => openProject(p.slug));
    box.appendChild(b);
  });

  const pick = preferred || (list[0] && list[0].slug);
  if (pick && pick !== state.slug) await openProject(pick);
  else if (!pick) emptyState();
}

function emptyState() {
  $("src").innerHTML = "";
  $("tgt").innerHTML = "";
  const p = document.createElement("p");
  p.className = "empty";
  p.textContent = "Zatím žádná kniha. Přetáhni .epub do prostředního sloupce.";
  $("src").appendChild(p);
  buildRail(0, []);
  paint();
}

async function openProject(slug) {
  const book = await api("/api/projects/" + encodeURIComponent(slug));
  state.slug = slug;
  state.book = book;
  state.chapter = 1;

  $("fname").textContent = book.title;
  $("drop").classList.remove("file--empty");
  $("fsub").textContent = num(book.chapters.length) + " kapitol · " +
                          num(book.total) + " odstavců";
  $("lsrc").value = book.source_lang;
  $("ltgt").value = book.target_lang;
  $("srclang").textContent = "ORIGINÁL · " + book.source_lang.toUpperCase();
  $("tgtlang").textContent = "PŘEKLAD · " + book.target_lang.toUpperCase();
  resumeNotice(book);

  buildRail(book.total, book.chapters);
  markCurrent();
  updateExportButtons();
  $("histopen").disabled = false;
  await showChapter(1);
  await loadGlossary();
  paint();
  followRun();
}

function markCurrent() {
  [...$("projlist").children].forEach((b) => {
    b.setAttribute("aria-current", String(b.dataset.slug === state.slug));
  });
}

async function refreshBook() {
  if (!state.slug) return;
  try {
    const book = await api("/api/projects/" + encodeURIComponent(state.slug));
    state.book = book;
    syncGlossBlock();
    const row = $("projlist").querySelector('button[data-slug="' + state.slug + '"] .pl-num');
    if (row) row.textContent = num(book.done) + "/" + num(book.total);
  } catch (e) { /* nic */ }
}

/* ---------------- kapitoly ---------------- */

async function showChapter(n) {
  const book = state.book;
  if (!book || !book.chapters.length) return;
  state.chapter = Math.min(Math.max(1, n), book.chapters.length);
  const chap = book.chapters[state.chapter - 1];
  const data = await api("/api/projects/" + encodeURIComponent(state.slug) +
                         "/segments?chapter=" + state.chapter);
  state.segments = data.segments;
  renderChapter(chap, state.segments);
  updateGo();
}

function paraClass(seg) {
  const cls = [];
  if (seg.kind === "head") cls.push("head", "h" + Math.min(3, seg.level || 1));
  if (seg.kind === "quote") cls.push("quote");
  if (seg.kind === "note") cls.push("note");
  return cls.join(" ");
}

function renderChapter(chap, segs) {
  const src = $("src"), tgt = $("tgt");
  src.innerHTML = "";
  tgt.innerHTML = "";

  segs.forEach((seg) => {
    const a = document.createElement("p");
    a.className = paraClass(seg);
    a.dataset.ord = seg.ord;
    if (seg.kind === "note" && seg.note_txt) {
      const m = document.createElement("span");
      m.className = "note-mark";
      m.textContent = seg.note_txt;
      a.appendChild(m);
      const body = document.createElement("span");
      body.innerHTML = seg.src_html;
      a.appendChild(body);
    } else {
      a.innerHTML = seg.src_html;
    }
    src.appendChild(a);

    const b = document.createElement("p");
    b.className = paraClass(seg);
    b.dataset.ord = seg.ord;
    b.dataset.status = seg.status;
    if (seg.tgt_html || seg.tgt_text) {
      b.innerHTML = seg.tgt_html || seg.tgt_text;
      b.classList.add("on");
    }
    if (seg.status === "review" || seg.status === "failed") {
      b.title = seg.review_note || "Odstavec neprošel kontrolou.";
    } else if (seg.status === "skipped") {
      b.title = "Vyřazeno z knihy: " + (seg.review_note || "ručně");
    } else {
      b.title = "Klikni a můžeš překlad upravit.";
    }
    b.addEventListener("click", () => segmentAction(b, seg));
    tgt.appendChild(b);
  });

  src.scrollTop = 0;
  tgt.scrollTop = 0;

  const total = state.book.chapters.length;
  $("srcmeta").textContent = chap.title;
  $("srcfoot").textContent = "kapitola " + state.chapter + " / " + total;
  $("prev").disabled = state.chapter <= 1;
  $("next").disabled = state.chapter >= total;
  paintChapterFoot();
}

/* Ruční úprava odstavce. Otevře se kliknutím v pravém sloupci: text jde
   opravit, nechat přeložit znovu, nebo odstavec vyřadit z knihy. Nic se
   nemění bez toho, aby uživatel klikl. */
function segmentAction(paragraph, seg) {
  const existing = $("tgt").querySelector(".seg-action");
  const same = existing && existing.dataset.ord === String(seg.ord);
  if (existing) {
    existing.previousElementSibling?.classList.remove("editing");
    existing.remove();
  }
  if (same) return;

  const box = document.createElement("div");
  box.className = "seg-action";
  box.dataset.ord = seg.ord;
  paragraph.classList.add("editing");

  if (seg.review_note) {
    const why = document.createElement("span");
    why.className = "why";
    const label = document.createElement("b");
    label.textContent = seg.status === "failed" ? "Překlad se nepodařil. "
      : seg.status === "skipped" ? "Vyřazeno z knihy: " : "Neprošlo kontrolou: ";
    why.append(label, document.createTextNode(seg.review_note));
    box.appendChild(why);
  }

  const pole = document.createElement("textarea");
  pole.rows = Math.min(10, Math.max(2, Math.ceil((seg.tgt_text || "").length / 60)));
  pole.value = seg.tgt_html || seg.tgt_text || "";
  pole.spellcheck = true;
  box.appendChild(pole);

  const zavri = () => { paragraph.classList.remove("editing"); box.remove(); };

  const ulozit = tlacitko("Uložit", async () => {
    const seg2 = await patchSegment(seg, { tgt_html: pole.value });
    if (seg2) { zavri(); }
  });

  const znovu = tlacitko("Přeložit znovu", async () => {
    znovu.disabled = true;
    znovu.textContent = "překládám…";
    try {
      await api("/api/projects/" + encodeURIComponent(state.slug) +
                "/segments/" + seg.ord + "/retranslate", { method: "POST" });
    } catch (err) {
      notice("bad", "Odstavec se nepodařilo znovu přeložit.", err.message);
      zavri();
      return;
    }
    zavri();
    state.running = true;
    state.runKind = "translate";
    state.live = { tps: null, eta: null, note: "překládám odstavec znovu" };
    updateGo();
    paint();
    followRun();
  }, true);

  const vyradit = tlacitko(
    seg.status === "skipped" ? "Vrátit do knihy" : "Vyřadit z knihy",
    async () => {
      const novy = seg.status === "skipped"
        ? (seg.tgt_text ? "done" : "pending") : "skipped";
      const seg2 = await patchSegment(seg, { status: novy });
      if (seg2) zavri();
    }, true);

  const zrusit = tlacitko("Zavřít", zavri, true);

  box.append(ulozit, znovu, vyradit, zrusit);
  const napoveda = document.createElement("span");
  napoveda.className = "hint2";
  napoveda.textContent = "Kurzívu piš jako <em>text</em>, tučné jako <strong>text</strong>. "
    + "Vyřazený odstavec se nepřekládá ani nedostane do exportu, ale zůstane v databázi.";
  box.appendChild(napoveda);

  paragraph.after(box);
  pole.focus();
}

function tlacitko(popisek, akce, ghost) {
  const b = document.createElement("button");
  if (ghost) b.className = "ghost";
  b.textContent = popisek;
  b.addEventListener("click", (e) => { e.stopPropagation(); akce(); });
  return b;
}

async function patchSegment(seg, fields) {
  try {
    const res = await api("/api/projects/" + encodeURIComponent(state.slug) +
                          "/segments/" + seg.ord,
      { method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fields) });
    const novy = res.segment;
    const mistni = state.segments.find((x) => x.ord === seg.ord);
    if (mistni) Object.assign(mistni, novy);
    const p = $("tgt").querySelector('p[data-ord="' + seg.ord + '"]');
    if (p) {
      p.innerHTML = novy.tgt_html || novy.tgt_text || "";
      p.dataset.status = novy.status;
      p.classList.add("on");
    }
    await refreshBook();
    paintChapterFoot();
    paint();
    updateGo();
    return novy;
  } catch (e) {
    notice("bad", "Změnu se nepodařilo uložit.", e.message);
    return null;
  }
}

function paintChapterFoot() {
  const segs = state.segments;
  const done = segs.filter((s) => s.status === "done").length;
  const review = segs.filter((s) => s.status === "review").length;
  const failed = segs.filter((s) => s.status === "failed").length;
  const chap = state.book.chapters[state.chapter - 1];
  $("tgtmeta").textContent = (done || review) ? chap.title : "nepřeloženo";
  let foot = done + review + " / " + segs.length + " odstavců";
  if (review) foot += " · " + review + " ke kontrole";
  if (failed) foot += " · " + failed + " nepodařených";
  $("tgtfoot").textContent = foot;
}

/* Segmenty ve stavu review se samy znovu neprekladaji, ceka se na uzivatele.
   Do zbyvajici prace se proto nepocitaji. */
function chapterPending() {
  return state.segments.filter(
    (s) => s.status === "pending" || s.status === "failed").length;
}

/* ---------------- svazane scrollovani ---------------- */

let syncing = false;
function bindScroll(a, b) {
  a.addEventListener("scroll", () => {
    if (syncing) return;
    syncing = true;
    requestAnimationFrame(() => {
      const kids = a.children;
      let lead = null;
      for (const el of kids) {
        if (el.offsetTop + el.offsetHeight > a.scrollTop) { lead = el; break; }
      }
      if (lead) {
        const twin = b.querySelector('p[data-ord="' + lead.dataset.ord + '"]');
        if (twin) b.scrollTop = twin.offsetTop - (lead.offsetTop - a.scrollTop);
      }
      syncing = false;
    });
  }, { passive: true });
}

/* ---------------- ukazatel prubehu ---------------- */

let ticks = [];
function buildRail(total, chapters) {
  const rail = $("rail");
  rail.innerHTML = "";
  const starts = new Set();
  if (total > 0) {
    chapters.forEach((c) => {
      if (c.first_ord) starts.add(Math.min(TICKS - 1, Math.floor((c.first_ord - 1) / total * TICKS)));
    });
  }
  for (let i = 0; i < TICKS; i++) {
    const d = document.createElement("div");
    d.className = starts.has(i) ? "tick chap" : "tick";
    rail.appendChild(d);
  }
  ticks = [...rail.children];
}

function paint() {
  const book = state.book;
  // vyrazene odstavce se neprekladaji, do jmenovatele tedy nepatri
  const total = book ? (book.total || 0) - (book.skipped || 0) : 0;
  const done = book ? book.done : 0;
  const r = total ? done / total : 0;
  $("pct").textContent = Math.round(r * 100);
  $("cnt").textContent = total ? num(done) + " / " + num(total) : "—";
  $("tps").textContent = state.live.tps ? state.live.tps + " tok/s" : "—";
  $("eta").textContent = state.running ? minutes(state.live.eta) : "—";
  const cut = Math.floor(r * TICKS);
  ticks.forEach((t, i) => {
    t.classList.toggle("done", i < cut);
    t.classList.toggle("now", state.running && i === cut);
  });
  const vyrazeno = book && book.skipped
    ? " · " + num(book.skipped) + " vyřazeno" : "";
  $("cnt").textContent = total
    ? num(done) + " / " + num(total) + vyrazeno : "—";
  $("nowline").textContent = nowLine();
}

function nowLine() {
  const book = state.book;
  if (!book) return "připraveno";
  if (state.running) return state.live.note || "překládá se";
  const cil = (book.total || 0) - (book.skipped || 0);
  if (book.done === 0) return "kniha načtena, překlad nespuštěn";
  if (book.done >= cil) return "přeloženo celé";
  return "hotovo " + num(book.done) + " odstavců";
}

function updateGo() {
  const go = $("go");
  if (state.running && state.runKind === "glossary") {
    go.disabled = true;
    go.textContent = "SLOVNÍČEK SE SESTAVUJE";
    go.title = "Počkej, až průchod knihou doběhne, nebo ho zastav v panelu slovníčku.";
    return;
  }
  if (state.running) {
    go.textContent = "POZASTAVIT";
    go.disabled = false;
    go.title = "";
    return;
  }
  const lmOk = state.status && state.status.lm_studio && state.status.lm_studio.ok;
  const lmReason = state.status && state.status.lm_studio && state.status.lm_studio.reason;
  const blocked = !lmOk && (lmReason === "offline" || lmReason === "http");
  const pending = bookPending();
  go.disabled = !state.book || blocked || pending === 0;
  go.textContent = pending === 0 ? "KNIHA JE HOTOVÁ"
                 : (state.book && state.book.done ? "POKRAČOVAT" : "PŘELOŽIT KNIHU");
  go.title = blocked
    ? "Bez běžícího LM Studia překlad nespustím."
    : (pending === 0
        ? "Nezbývá nic k překladu. Odstavce ke kontrole se překládají po kliknutí."
        : "Přeloží celou knihu od místa, kde se skončilo. Zbývá " + num(pending) + ".");
}

/* Kolik odstavcu v knize jeste ceka. Stav review se nepocita, ten se
   neprekladá sam od sebe, ceka na kliknuti uzivatele. */
function bookPending() {
  const b = state.book;
  if (!b) return 0;
  return Math.max(0, (b.total || 0) - (b.done || 0) - (b.review || 0)
                     - (b.skipped || 0));
}

/* ---------------- preklad ---------------- */

async function startTranslate() {
  // tlacitko se prepne hned, at okno necuka; pri neuspechu se vrati zpet
  notice(null);
  state.running = true;
  state.runKind = "translate";
  state.live = { tps: null, eta: null, note: "navazuji spojení s modelem" };
  updateGo();
  paint();
  try {
    // bez parametru chapter: cela kniha od mista, kde skoncil
    await api("/api/projects/" + encodeURIComponent(state.slug) + "/translate",
              { method: "POST" });
  } catch (e) {
    state.running = false;
    state.live = { tps: null, eta: null, note: "" };
    updateGo();
    paint();
    notice("bad", "Překlad se nepodařilo spustit.", e.message);
    return;
  }
  followRun();
}

async function stopTranslate() {
  state.live.note = "zastavuji po doběhnutí dávky";
  paint();
  try {
    await api("/api/projects/" + encodeURIComponent(state.slug) + "/stop",
              { method: "POST" });
  } catch (e) { /* beh uz mohl skoncit sam */ }
}

function followRun() {
  if (!state.slug) return;
  if (state.stream) { state.stream.close(); state.stream = null; }
  const es = new EventSource("/api/projects/" + encodeURIComponent(state.slug) +
                             "/stream");
  state.stream = es;
  es.onmessage = (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch (err) { return; }
    handleEvent(ev);
  };
  es.onerror = () => {
    es.close();
    if (state.stream === es) state.stream = null;
    if (state.running) {
      state.running = false;
      updateGo();
      paint();
    }
  };
}

function putParagraph(ord, html, status) {
  const p = $("tgt").querySelector('p[data-ord="' + ord + '"]');
  if (!p) return;          // uzivatel se dival na jinou kapitolu
  p.innerHTML = html;
  if (status) p.dataset.status = status;
  p.classList.add("on");
}

function handleEvent(ev) {
  if (ev.kind) state.runKind = ev.kind;
  switch (ev.type) {
    case "idle":
      state.running = false;
      state.runKind = null;
      updateGo();
      glossUpdateButtons();
      break;

    case "start":
      state.running = ev.running !== false;
      if (state.runKind === "translate") {
        state.live.note = "dávka 1 / " + (ev.batches || "?");
      }
      updateGo();
      glossUpdateButtons();
      paint();
      break;

    case "term":
      glossAddTerm(ev.term);
      break;

    case "draft":
      putParagraph(ev.ord, ev.html, null);
      break;

    case "segment": {
      putParagraph(ev.ord, ev.html, ev.status);
      const seg = state.segments.find((s) => s.ord === ev.ord);
      if (seg) {
        seg.status = ev.status;
        seg.tgt_html = ev.html;
        seg.review_note = ev.review_note || null;
      }
      paintChapterFoot();
      break;
    }

    case "progress":
      state.running = ev.running !== false;
      if (state.book && typeof ev.done === "number") state.book.done = ev.done;
      state.live.tps = ev.tps;
      state.live.eta = ev.eta_s;
      state.live.note = ev.message || state.live.note;
      if (ev.kind === "glossary") glossProgress(ev);
      paint();
      updateGo();
      break;

    case "end":
      state.running = false;
      state.live = { tps: null, eta: null, note: "" };
      if (state.stream) { state.stream.close(); state.stream = null; }
      if (state.runKind === "glossary") {
        state.runKind = null;
        glossUpdateButtons();
        loadGlossary();
        refreshBook().then(paint);
        if (ev.status !== "stopped") {
          notice("ok", "Slovníček sestaven, " +
            withNum(ev.translated || 0, "položka", "položky", "položek") + ".",
            "Projdi je v panelu a co potvrdíš, dostane zámek. Zamčené položky model při překladu nepřepisuje.");
        }
        break;
      }
      state.runKind = null;
      refreshBook().then(() => showChapter(state.chapter)).then(paint);
      if (ev.status === "stopped") {
        notice("ok", "Překlad pozastaven.",
          "Hotové odstavce jsou uložené. Tlačítkem POKRAČOVAT naváže tam, kde skončil.");
      } else if (ev.failed) {
        notice("warn", "Kapitola hotová, ale " +
          withNum(ev.failed, "odstavec se přeložit nepodařil",
                  "odstavce se přeložit nepodařily",
                  "odstavců se přeložit nepodařilo") + ".",
          "Model je vrátil prázdné i po překladu po jednotlivých odstavcích. Klikni na ně v pravém sloupci.");
      } else if (ev.review) {
        notice("warn", "Kapitola hotová, " +
          withNum(ev.review, "odstavec neprošel kontrolou",
                  "odstavce neprošly kontrolou",
                  "odstavců neprošlo kontrolou") + ".",
          "Překlad zůstal, jak ho model vrátil, nic se nepřepsalo. V pravém sloupci mají svislou linku, po kliknutí uvidíš důvod.");
      } else {
        notice(null);
      }
      break;

    case "error":
      state.running = false;
      if (state.stream) { state.stream.close(); state.stream = null; }
      notice("bad", "Překlad se zastavil na chybě.", ev.message);
      refreshBook().then(() => showChapter(state.chapter)).then(paint);
      break;
  }
}

/* ---------------- slovnicek ---------------- */

function glossOpen(open) {
  $("gloss").classList.toggle("on", open);
  if (open) loadGlossary();
}

async function loadGlossary() {
  if (!state.slug) return;
  try {
    const data = await api("/api/projects/" + encodeURIComponent(state.slug) + "/glossary");
    state.glossary = data.entries || [];
  } catch (e) {
    state.glossary = [];
  }
  renderGlossary();
  glossUpdateButtons();
  syncGlossBlock();
}

function syncGlossBlock() {
  const total = state.glossary.length;
  const open = state.glossary.filter((e) => !e.locked).length;
  $("glosscount").textContent = num(total);
  $("glosssub").textContent = total
    ? withNum(open, "nepotvrzená", "nepotvrzené", "nepotvrzených")
    : "zatím nesestaven";
  $("glossopen").disabled = !state.slug;
  if (state.book) {
    state.book.glossary_total = total;
    state.book.glossary_unconfirmed = open;
  }
}

function selectCell(options, value, onChange) {
  const sel = document.createElement("select");
  options.forEach(([v, label]) => {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = label;
    sel.appendChild(o);
  });
  sel.value = value || "";
  sel.addEventListener("change", () => onChange(sel.value));
  return sel;
}

function renderGlossary() {
  const body = $("glossbody");
  body.innerHTML = "";
  if (!state.glossary.length) {
    const p = document.createElement("div");
    p.className = "gloss-empty";
    p.textContent = "Slovníček zatím není sestavený. Průchod projde knihu jednou " +
      "nasucho, vypíše vlastní jména a opakující se pojmy a navrhne české tvary. " +
      "Trvá to podle délky knihy a rychlosti modelu.";
    body.appendChild(p);
    return;
  }

  const table = document.createElement("table");
  table.className = "gloss-table";
  table.innerHTML = "<thead><tr>" +
    "<th>VÝRAZ</th><th>ČESKY</th><th>KAT.</th><th>ROD</th>" +
    "<th class='num'>×</th><th></th><th></th></tr></thead>";
  const tbody = document.createElement("tbody");

  state.glossary.forEach((entry) => {
    tbody.appendChild(glossRow(entry));
    if (entry.note) {
      const nr = document.createElement("tr");
      nr.className = "gr-note";
      const td = document.createElement("td");
      td.colSpan = 7;
      td.textContent = entry.note;
      nr.appendChild(td);
      tbody.appendChild(nr);
    }
  });
  table.appendChild(tbody);
  body.appendChild(table);
}

function glossRow(entry) {
  const tr = document.createElement("tr");
  tr.className = "gr" + (entry.locked ? " locked" : "");
  tr.dataset.id = entry.id;

  const src = document.createElement("td");
  src.className = "gr-src";
  src.textContent = entry.term_src;
  src.title = "první výskyt v kapitole " + (entry.first_chapter || "?");

  const cs = document.createElement("td");
  const csEdit = document.createElement("div");
  csEdit.className = "gr-cs";
  csEdit.contentEditable = "true";
  csEdit.spellcheck = false;
  csEdit.textContent = entry.term_cs || "";
  csEdit.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); csEdit.blur(); }
    if (e.key === "Escape") { csEdit.textContent = entry.term_cs || ""; csEdit.blur(); }
  });
  csEdit.addEventListener("blur", () => {
    const value = csEdit.textContent.trim();
    if (value === (entry.term_cs || "")) return;
    saveEntry(entry, { term_cs: value }, true);
  });
  cs.appendChild(csEdit);

  const cat = document.createElement("td");
  cat.appendChild(selectCell(CATEGORIES, entry.category,
    (v) => saveEntry(entry, { category: v }, false)));

  const gen = document.createElement("td");
  gen.appendChild(selectCell(GENDERS, entry.gender,
    (v) => saveEntry(entry, { gender: v }, false)));

  const cnt = document.createElement("td");
  cnt.className = "num gr-count";
  cnt.textContent = entry.occurrences;

  const lock = document.createElement("td");
  const lockBtn = document.createElement("button");
  lockBtn.className = "gr-lock" + (entry.locked ? " on" : "");
  lockBtn.textContent = entry.locked ? "▣" : "▢";
  lockBtn.title = entry.locked ? "potvrzeno, model to nesmí měnit" : "nepotvrzeno";
  lockBtn.addEventListener("click", () => saveEntry(entry, { locked: !entry.locked }, false));
  lock.appendChild(lockBtn);

  const del = document.createElement("td");
  const delBtn = document.createElement("button");
  delBtn.className = "gr-del";
  delBtn.textContent = "✕";
  delBtn.title = "vyřadit ze slovníčku";
  delBtn.addEventListener("click", () => deleteEntry(entry));
  del.appendChild(delBtn);

  tr.append(src, cs, cat, gen, cnt, lock, del);
  return tr;
}

async function saveEntry(entry, fields, mayAffectDone) {
  const wasLocked = entry.locked;
  try {
    const res = await api("/api/projects/" + encodeURIComponent(state.slug) +
                          "/glossary/" + entry.id,
      { method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fields) });
    Object.assign(entry, res.entry);
  } catch (e) {
    notice("bad", "Změnu se nepodařilo uložit.", e.message);
    return;
  }
  renderGlossary();
  syncGlossBlock();
  glossUpdateButtons();
  // zmena uz potvrzene polozky se muze tykat hotovych segmentu
  if (mayAffectDone && wasLocked) offerRetranslate(entry);
}

async function offerRetranslate(entry) {
  let found;
  try {
    found = await api("/api/projects/" + encodeURIComponent(state.slug) +
                      "/glossary/" + entry.id + "/affected");
  } catch (e) { return; }
  if (!found.count) return;

  const box = $("glossoffer");
  box.innerHTML = "";
  const text = document.createElement("div");
  text.textContent = "Výraz " + found.term_src + " stojí v " +
    withNum(found.count, "už přeloženém odstavci", "už přeložených odstavcích",
            "už přeložených odstavcích") + ". " +
    plural(found.count, "Nese starý tvar. Vrátit ho k překladu?",
           "Nesou starý tvar. Vrátit je k překladu?",
           "Nesou starý tvar. Vrátit je k překladu?");
  const row = document.createElement("div");
  row.className = "row";
  const yes = document.createElement("button");
  yes.className = "yes";
  yes.textContent = "Vrátit k překladu";
  yes.addEventListener("click", async () => {
    try {
      const res = await api("/api/projects/" + encodeURIComponent(state.slug) +
                            "/glossary/" + entry.id + "/retranslate", { method: "POST" });
      box.classList.remove("on");
      notice("ok", withNum(res.count, "odstavec vrácen", "odstavce vráceny",
                           "odstavců vráceno") + " k překladu.",
        "Dotčené kapitoly: " + res.chapters.join(", ") + ". Spusť překlad znovu.");
      await refreshBook();
      await showChapter(state.chapter);
      paint();
    } catch (e) {
      notice("bad", "Nepodařilo se je vrátit k překladu.", e.message);
    }
  });
  const no = document.createElement("button");
  no.textContent = "Nechat být";
  no.addEventListener("click", () => box.classList.remove("on"));
  row.append(yes, no);
  box.append(text, row);
  box.classList.add("on");
}

async function deleteEntry(entry) {
  try {
    await api("/api/projects/" + encodeURIComponent(state.slug) +
              "/glossary/" + entry.id, { method: "DELETE" });
  } catch (e) {
    notice("bad", "Položku se nepodařilo vyřadit.", e.message);
    return;
  }
  state.glossary = state.glossary.filter((e) => e.id !== entry.id);
  renderGlossary();
  syncGlossBlock();
  glossUpdateButtons();
}

function glossAddTerm(term) {
  if (!term) return;
  const stat = $("glossstat");
  const n = (state.glossary.length || 0) + 1;
  state.glossary.push(Object.assign({ id: -n }, term));
  stat.textContent = "sestavuje se, " +
    withNum(state.glossary.length, "položka", "položky", "položek");
}

function glossProgress(ev) {
  const stat = $("glossstat");
  const phase = { sber: "sběr", pocitani: "počítání výskytů",
                  tvary: "české tvary", hotovo: "hotovo" }[ev.phase] || "";
  const bits = [];
  if (phase) bits.push(phase);
  if (ev.message) bits.push(ev.message);
  stat.textContent = bits.join(" · ");
}

function glossUpdateButtons() {
  const building = state.running && state.runKind === "glossary";
  const build = $("glossbuild");
  build.textContent = building ? "ZASTAVIT" : (state.glossary.length
    ? "SESTAVIT ZNOVU" : "SESTAVIT SLOVNÍČEK");
  build.disabled = !state.slug || (state.running && !building);
  $("glosslockall").disabled = building || !state.glossary.some((e) => !e.locked && e.term_cs);
  if (!building && state.glossary.length) {
    const open = state.glossary.filter((e) => !e.locked).length;
    $("glossstat").textContent =
      withNum(state.glossary.length, "položka", "položky", "položek") + " · " +
      withNum(open, "nepotvrzená", "nepotvrzené", "nepotvrzených");
  } else if (!building && !state.glossary.length) {
    $("glossstat").textContent = "prázdný";
  }
}

async function buildGlossary() {
  if (state.running && state.runKind === "glossary") {
    await stopTranslate();
    return;
  }
  try {
    await api("/api/projects/" + encodeURIComponent(state.slug) + "/glossary/build",
              { method: "POST" });
  } catch (e) {
    notice("bad", "Sestavování slovníčku se nepodařilo spustit.", e.message);
    return;
  }
  notice(null);
  state.running = true;
  state.runKind = "glossary";
  state.glossary = [];
  renderGlossary();
  glossUpdateButtons();
  updateGo();
  followRun();
}

async function lockAllGlossary() {
  try {
    const res = await api("/api/projects/" + encodeURIComponent(state.slug) +
                          "/glossary/lock-all", { method: "POST" });
    notice("ok", "Potvrzeno " +
      withNum(res.locked, "položka", "položky", "položek") + ".",
      "Model je při překladu nesmí měnit.");
  } catch (e) {
    notice("bad", "Potvrzení se nepodařilo.", e.message);
    return;
  }
  loadGlossary();
}

/* ---------------- stylova karta ---------------- */

function openStyleCard() {
  if (!state.book) return;
  const s = state.book.style || {};
  $("f-register").value = s.register || "neutralni";
  $("f-narrator").value = s.narrator || "neurceno";
  $("f-address").value = s.address || "neurceno";
  $("f-note").value = s.note || "";
  $("f-feminize").checked = !!state.book.feminize_surnames;
  $("stylecard").showModal();
}

async function saveStyleCard() {
  const body = {
    style_register: $("f-register").value,
    style_narrator: $("f-narrator").value,
    style_address: $("f-address").value,
    style_note: $("f-note").value,
    feminize_surnames: $("f-feminize").checked,
  };
  try {
    const book = await api("/api/projects/" + encodeURIComponent(state.slug),
      { method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
    state.book = book;
    $("stylecard").close();
    notice("ok", "Stylová karta uložena.",
      "Půjde do promptu každé další dávky. Hotové odstavce se tím nemění.");
  } catch (e) {
    notice("bad", "Stylovou kartu se nepodařilo uložit.", e.message);
  }
}

/* ---------------- historie behu ---------------- */

const RUN_KINDS = { import: "načtení knihy", glossary: "slovníček",
                    translate: "překlad", export: "export" };
const RUN_STATUS = { done: "hotovo", stopped: "zastaveno", error: "chyba",
                     running: "běží", interrupted: "přerušeno" };

function whenText(iso) {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 16);
}

function duration(from, to) {
  if (!from || !to) return null;
  const s = Math.round((new Date(to) - new Date(from)) / 1000);
  if (!isFinite(s) || s < 0) return null;
  if (s < 60) return s + " s";
  if (s < 3600) return Math.round(s / 60) + " min";
  return Math.floor(s / 3600) + " h " + Math.round((s % 3600) / 60) + " min";
}

async function openHistory() {
  glossOpen(false);          // oba panely prekryvaji tentyz sloupec
  $("hist").classList.add("on");
  const body = $("histbody");
  body.innerHTML = "";
  let runs = [];
  try {
    runs = (await api("/api/projects/" + encodeURIComponent(state.slug) + "/runs")).runs || [];
  } catch (e) {
    notice("bad", "Historii se nepodařilo načíst.", e.message);
    return;
  }
  if (!runs.length) {
    const p = document.createElement("div");
    p.className = "gloss-empty";
    p.textContent = "Zatím žádné běhy.";
    body.appendChild(p);
    return;
  }
  runs.forEach((r) => {
    const row = document.createElement("div");
    row.className = "run-row";

    const kind = document.createElement("span");
    kind.className = "run-kind";
    kind.textContent = RUN_KINDS[r.kind] || r.kind;

    const when = document.createElement("span");
    when.className = "run-when";
    when.textContent = whenText(r.started_at);

    const stat = document.createElement("span");
    stat.className = "run-stat " + r.status;
    stat.textContent = RUN_STATUS[r.status] || r.status;

    row.append(kind, when, stat);

    const bits = [];
    if (r.segments_done) {
      bits.push("<b>" + num(r.segments_done) + "</b> " +
        plural(r.segments_done,
          r.kind === "glossary" ? "položka" : "odstavec",
          r.kind === "glossary" ? "položky" : "odstavce",
          r.kind === "glossary" ? "položek" : "odstavců"));
    }
    const dur = duration(r.started_at, r.finished_at);
    if (dur) bits.push("za " + dur);
    if (r.tokens_per_s) bits.push(r.tokens_per_s + " tok/s");
    if (r.tokens_out) bits.push(num(r.tokens_out) + " tokenů");
    if (bits.length) {
      const d = document.createElement("div");
      d.className = "run-detail";
      d.innerHTML = bits.join(" · ");
      row.appendChild(d);
    }
    if (r.error) {
      const e = document.createElement("div");
      e.className = "run-err";
      e.textContent = r.error;
      row.appendChild(e);
    }
    body.appendChild(row);
  });
}

/* ---------------- navazani po preruseni ---------------- */

function resumeNotice(book) {
  if (!book.source || book.source.exists === false) {
    notice("warn", "Zdrojový EPUB projektu chybí.",
      "Soubor " + book.source_file + " ve složce projektu není. Text v databázi " +
      "zůstal, ale znovu ho z originálu načíst nepůjde.");
    return;
  }
  if (book.source.changed) {
    notice("warn", "Zdrojový EPUB se od načtení změnil.",
      "Otisk souboru " + book.source_file + " nesouhlasí s tím, co je uložené " +
      "v projektu. Odstavce v databázi odpovídají původní verzi. Pro novou " +
      "verzi založ nový projekt, ať se hotová práce nepomíchá.");
    return;
  }
  const last = book.last_run;
  if (last && (last.status === "interrupted" || last.status === "stopped") &&
      book.resume) {
    const co = RUN_KINDS[last.kind] || last.kind;
    const jak = last.status === "interrupted"
      ? "skončil bez uzavření, nejspíš pádem nebo zavřením aplikace"
      : "byl zastaven";
    notice("ok", "Poslední " + co + " " + jak + ".",
      "Hotová práce je uložená. Navázat se dá v kapitole " + book.resume.chapter +
      ", kde čeká " + withNum(book.resume.left, "odstavec", "odstavce", "odstavců") +
      ".");
  }
}

/* ---------------- cisteni tiskoveho balastu ---------------- */

async function runCleanup() {
  const btn = $("clean");
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "hledám…";
  let found;
  try {
    found = await api("/api/projects/" + encodeURIComponent(state.slug) + "/cleanup");
  } catch (e) {
    notice("bad", "Hledání se nepodařilo.", e.message);
    btn.disabled = false; btn.textContent = label;
    return;
  }
  btn.disabled = false;
  btn.textContent = label;

  if (!found.found) {
    notice("ok", "Žádný tiskový balast jsem nenašel.",
      "Zdrojový EPUB vypadá čistě. Jednotlivé odstavce jde pořád vyřadit ručně kliknutím v pravém sloupci.");
    return;
  }

  const box = $("glossoffer");
  box.innerHTML = "";
  const text = document.createElement("div");
  const soucet = Object.entries(found.summary)
    .sort((a, b) => b[1] - a[1])
    .map(([d, n]) => n + "× " + d).join(", ");
  text.textContent = "Našel jsem " +
    withNum(found.found, "odstavec", "odstavce", "odstavců") +
    ", které vypadají jako tiskové příslušenství, ne jako text knihy: " +
    soucet + ". Vyřadit je z knihy?";
  const row = document.createElement("div");
  row.className = "row";
  const yes = document.createElement("button");
  yes.className = "yes";
  yes.textContent = "Vyřadit";
  yes.addEventListener("click", async () => {
    try {
      const res = await api("/api/projects/" + encodeURIComponent(state.slug) +
                            "/cleanup", { method: "POST" });
      box.classList.remove("on");
      notice("ok", "Vyřazeno " +
        withNum(res.skipped, "odstavec", "odstavce", "odstavců") + ".",
        "Nic se nesmazalo, jen se to nebude překládat ani exportovat. V pravém sloupci jsou přeškrtnuté a kliknutím je vrátíš.");
      await refreshBook();
      await showChapter(state.chapter);
      paint();
    } catch (e) {
      notice("bad", "Vyřazení se nepodařilo.", e.message);
    }
  });
  const no = document.createElement("button");
  no.textContent = "Nechat být";
  no.addEventListener("click", () => box.classList.remove("on"));
  row.append(yes, no);
  box.append(text, row);
  box.classList.add("on");
  glossOpen(true);
}

/* ---------------- export ---------------- */

const EXPORT_NAMES = {
  translation: "EPUB jen s překladem",
  mirror: "zrcadlový EPUB",
  markdown: "Markdown",
};

async function runExport(kind, button) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "ukládám…";
  notice(null);
  try {
    const info = await api("/api/projects/" + encodeURIComponent(state.slug) +
                           "/export?kind=" + kind, { method: "POST" });
    const kb = Math.max(1, Math.round(info.size / 1024));
    const hint = "Soubor " + info.name + " · " + num(kb) + " kB · ve složce " +
                 "projects/" + state.slug + "/export/";
    if (!info.counts_match) {
      notice("bad", "Export uložen, ale počet odstavců nesedí: " +
        num(info.paragraphs) + " místo " + num(info.source_paragraphs) + ".",
        hint + " Tohle je chyba, dej vědět.");
    } else if (info.missing) {
      notice("warn", EXPORT_NAMES[kind] + " uložen, " +
        withNum(info.missing, "odstavec ještě není přeložený",
                "odstavce ještě nejsou přeložené",
                "odstavců ještě není přeložených") + ".",
        hint + " Nepřeložené odstavce jsou v souboru v originále, aby jejich počet seděl.");
    } else {
      notice("ok", EXPORT_NAMES[kind] + " uložen, " +
        withNum(info.paragraphs, "odstavec", "odstavce", "odstavců") + " ve " +
        withNum(info.chapters, "kapitole", "kapitolách", "kapitolách") + ".", hint);
    }
  } catch (e) {
    notice("bad", "Export se nepodařil.", e.message);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

function updateExportButtons() {
  const has = !!state.book;
  $("exp").disabled = !has;
  $("clean").disabled = !has;
  if (!has) $("exports").hidden = true;
}

/* ---------------- nacteni knihy ---------------- */

async function uploadFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".epub")) {
    notice("bad", "Tohle není EPUB.", "Vyber soubor s příponou .epub.");
    return;
  }
  const drop = $("drop");
  drop.classList.add("busy");
  $("fname").textContent = file.name;
  $("fsub").textContent = "rozkládám na odstavce…";
  notice(null);
  try {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const res = await api("/api/projects", { method: "POST", body: fd });
    state.slug = null;
    await loadProjects(res.slug);
  } catch (e) {
    notice("bad", "Knihu se nepodařilo načíst.", e.message);
    $("fname").textContent = "přetáhni sem .epub";
    $("fsub").textContent = "nebo klikni a vyber soubor";
    $("drop").classList.add("file--empty");
  } finally {
    drop.classList.remove("busy");
  }
}

/* ---------------- start ---------------- */

function wire() {
  const drop = $("drop"), picker = $("picker");
  drop.addEventListener("click", () => picker.click());
  drop.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); picker.click(); }
  });
  picker.addEventListener("change", () => {
    uploadFile(picker.files[0]);
    picker.value = "";
  });
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drop"); }));
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drop"); }));
  drop.addEventListener("drop", (e) => {
    const f = e.dataTransfer && e.dataTransfer.files[0];
    uploadFile(f);
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());

  $("prev").addEventListener("click", () => showChapter(state.chapter - 1));
  $("next").addEventListener("click", () => showChapter(state.chapter + 1));
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
    if (e.key === "ArrowLeft" && !$("prev").disabled) showChapter(state.chapter - 1);
    if (e.key === "ArrowRight" && !$("next").disabled) showChapter(state.chapter + 1);
  });

  $("go").addEventListener("click", () => {
    if (state.running) stopTranslate();
    else startTranslate();
  });

  $("swap").addEventListener("click", () => {
    notice("warn", "Prohození jazyků zatím nic nemění.",
      "Směr překladu se bere z projektu, angličtina do češtiny.");
  });

  $("clean").addEventListener("click", runCleanup);

  $("exp").addEventListener("click", () => {
    const box = $("exports");
    box.hidden = !box.hidden;
  });
  [...$("exports").querySelectorAll("button")].forEach((b) =>
    b.addEventListener("click", () => runExport(b.dataset.kind, b)));

  $("glossopen").addEventListener("click", () => glossOpen(true));
  $("glossclose").addEventListener("click", () => glossOpen(false));
  $("glossbuild").addEventListener("click", buildGlossary);
  $("glosslockall").addEventListener("click", lockAllGlossary);
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if ($("gloss").classList.contains("on")) glossOpen(false);
    if ($("hist").classList.contains("on")) $("hist").classList.remove("on");
  });

  $("modelpick").addEventListener("change", (e) => chooseModel(e.target.value));

  $("settings").addEventListener("click", openStyleCard);
  $("settings").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openStyleCard(); }
  });
  $("stylesave").addEventListener("click", saveStyleCard);
  $("histopen").addEventListener("click", openHistory);
  $("histclose").addEventListener("click", () => $("hist").classList.remove("on"));

  bindScroll($("src"), $("tgt"));
  bindScroll($("tgt"), $("src"));
}

async function boot() {
  wire();
  await loadStatus();
  await loadProjects();
  setInterval(loadStatus, 15000);
}

boot();
