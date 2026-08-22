"""Sber slovnicku. Beh nasucho pred prekladem.

Postup ma tri casti:
  1. po kapitolach se model zepta na vlastni jmena a autorskou terminologii,
     odpoved chce jako JSON
  2. vysledky se slozi dohromady, spocitaji se vyskyty a co je v knize min
     nez dvakrat, vypadne
  3. u zbytku model navrhne cesky tvar, rod a jednu vetu oduvodneni

Vysledek se ulozi s locked = 0. Zamek dava az uzivatel v panelu.
"""
import bisect
import json
import re
import time

from . import db, llm, projects, runner
from .config import CFG

CATEGORIES = {"osoba", "misto", "organizace", "pojem"}
GENDERS = {"m", "f", "n"}

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
OBJ_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def cfg():
    return CFG.get("glossary", {})


# ----------------------------------------------------------- cteni JSON

def extract_json_array(text):
    """Vytahne pole JSON z odpovedi modelu.

    Model casto pridava uvod nebo obalí odpoved znackami ```json.
    Kdyz se cele pole precist neda, zkusi se jednotlive objekty.
    """
    if not text:
        return []
    fenced = FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1)
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        except ValueError:
            pass
    out = []
    for m in OBJ_RE.finditer(text):
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


# ------------------------------------------------------------ pocitani

def scan_term(term, haystack):
    """Jeden pruchod textem. Vraci (pocet, skutecny tvar, pozice prvniho vyskytu)."""
    if not term:
        return 0, term, -1
    pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
    hits = list(re.finditer(pattern, haystack))
    if hits:
        return len(hits), term, hits[0].start()
    # model obcas zmeni velikost pismen; vezme se tvar, ktery je v textu
    hits = list(re.finditer(pattern, haystack, re.IGNORECASE))
    if hits:
        return len(hits), hits[0].group(0), hits[0].start()
    return 0, term, -1


def count_term(term, haystack):
    """Pocet vyskytu vyrazu jako celeho slova. Vraci (pocet, skutecny tvar)."""
    count, actual, _ = scan_term(term, haystack)
    return count, actual


MONTHS_DAYS = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
}
SENTENCE_END = set(".!?:;\"“”„'()[]—–\n")


def _mid_sentence(text, start):
    """Stoji vyraz uprostred vety, nebo na jejim zacatku?"""
    i = start - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    if i < 0:
        return False
    return text[i] not in SENTENCE_END


def name_evidence(term, haystack):
    """Doklad o tom, ze vyraz je vlastni jmeno.

    Vraci (velkym uprostred vety, malym pismenem). Vlastni jmeno se pise
    velkym i uprostred vety. Obecne podstatne jmeno, ktere se do slovnicku
    dostalo jen proto, ze stalo v nadpisu nebo na zacatku vety, se jinde
    v knize objevuje malym pismenem.
    """
    pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
    cap_mid = 0
    for m in re.finditer(pattern, haystack):
        if _mid_sentence(haystack, m.start()):
            cap_mid += 1
    lower_term = term.lower()
    lower_hits = 0
    if lower_term != term:
        lower_pattern = r"(?<!\w)" + re.escape(lower_term) + r"(?!\w)"
        lower_hits = len(re.findall(lower_pattern, haystack))
    return cap_mid, lower_hits


def junk_reason(term, haystack):
    """Proc polozka do slovnicku nepatri. None znamena, ze patri."""
    low = term.lower()
    if low in MONTHS_DAYS:
        return "měsíc nebo den v týdnu"
    if "'s " in low or "’s " in low or low.endswith(("'s", "’s")):
        return "přivlastňovací vazba, ne jméno"
    if not term[:1].isupper():
        return "výraz nezačíná velkým písmenem"
    cap_mid, lower_hits = name_evidence(term, haystack)
    if cap_mid == 0:
        return "v knize stojí velkým písmenem jen na začátku věty nebo v nadpisu"
    if lower_hits > cap_mid:
        return ("v knize je " + str(lower_hits) + "× malým písmenem proti " +
                str(cap_mid) + "× velkým uprostřed věty, jde o obecné slovo")
    return None


def chunk_chapter(segments, limit_tokens, chars_per_token):
    """Kapitolu rozdeli na kusy, ktere se vejdou do kontextu. Deli po odstavcich."""
    limit_chars = limit_tokens * chars_per_token
    chunks, cur, size = [], [], 0
    for seg in segments:
        text = seg["src_text"]
        if cur and size + len(text) > limit_chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(text)
        size += len(text)      # oddelovace se nepocitaji, limit je stejne odhad
    if cur:
        chunks.append(cur)
    return chunks


# ------------------------------------------------------------- prompty

EXTRACT_SYSTEM = """Jsi pomocník při přípravě knižního překladu. Z úryvku vypíšeš
vlastní jména a autorskou terminologii.

Vypisuj:
- jména osob, tedy křestní jména, příjmení i přezdívky
- zeměpisná jména, tedy města, země, řeky, stavby i smyšlená místa
- názvy organizací, tedy firmy, úřady, spolky, lodě, noviny
- výrazy, které autor používá jako vlastní terminologii a vracejí se v textu

Nevypisuj běžná podstatná jména, dny v týdnu, měsíce ani obecné pojmy.
Výraz opiš přesně tak, jak stojí v textu, včetně velkých písmen.

Odpověz výhradně polem JSON, bez úvodu a bez komentáře:
[{"vyraz": "...", "kategorie": "osoba"}]
Kategorie smí být jen osoba, misto, organizace nebo pojem.
Když v úryvku nic takového není, odpověz []."""


def czech_system(book):
    prechyl = ("Ženská příjmení přechyluj podle české zvyklosti, tedy Smith na Smithová."
               if book["feminize_surnames"] else
               "Ženská příjmení nepřechyluj, nech je v původním tvaru.")
    return """Jsi pomocník při přípravě knižního překladu z angličtiny do češtiny.
Ke každému výrazu navrhni český tvar v prvním pádě.

Pravidla:
- """ + prechyl + """
- U každé položky urči rod: m pro mužský, f pro ženský, n pro střední.
  Čeština ho potřebuje pro shodu v minulém čase.
- Zeměpisné jméno, které má vžitou českou podobu, převeď: London na Londýn,
  Vienna na Vídeň. Ostatní zeměpisná jména nech v původním tvaru.
- Jména osob se zpravidla nepřekládají, nech je tak, jak jsou.
- Ke každé položce napiš jednu větu, proč jsi zvolil právě tento tvar.

Odpověz výhradně polem JSON, bez úvodu a bez komentáře:
[{"vyraz": "...", "cesky": "...", "rod": "m", "kategorie": "osoba",
  "poznamka": "jedna věta"}]
Vrať právě tolik položek, kolik jich bylo na vstupu, a ve stejném pořadí."""


# --------------------------------------------------------------- beh

def start(slug):
    return runner.start(slug, "glossary", _worker)


def _worker(run):
    path = projects.project_dir(run.slug)
    con = db.connect(path / "project.db")
    t0 = time.time()
    try:
        runner.open_record(con, run)
        book = con.execute("SELECT * FROM book WHERE id = 1").fetchone()
        totals = projects.counts(con)
        run.state.update(total=totals["total"], done=totals["done"],
                         phase="sber", g_done=0, g_total=0, terms=0)

        segments = [dict(r) for r in con.execute(
            "SELECT ord, chapter, src_text FROM segment ORDER BY ord").fetchall()]
        if not segments:
            run.progress(message="Kniha nemá žádný text.")
            runner.close_record(con, run, "done", t0)
            return

        candidates = _phase_extract(run, segments)
        if run.should_stop():
            runner.close_record(con, run, "stopped", t0)
            return

        survivors = _phase_count(run, segments, candidates)
        if run.should_stop():
            runner.close_record(con, run, "stopped", t0)
            return

        written = _phase_czech(run, con, book, survivors)
        run.progress(phase="hotovo", terms=written,
                     message="slovníček má " +
                             runner.with_num(written, "položku", "položky",
                                             "položek"))
        run.segments_done = written
        status = "stopped" if run.should_stop() else "done"
        runner.close_record(con, run, status, t0)
    except llm.LLMError as exc:
        run.emit({"type": "error", "message": str(exc)})
        runner.close_record(con, run, "error", t0, str(exc))
    except Exception as exc:
        msg = type(exc).__name__ + ": " + str(exc)
        run.emit({"type": "error", "message": msg})
        runner.close_record(con, run, "error", t0, msg)
    finally:
        con.close()


def _phase_extract(run, segments):
    """Prvni cast: model vypise kandidaty po kapitolach."""
    per_token = float(CFG["batching"].get("chars_per_token", 4.0))
    limit = int(cfg().get("chunk_source_tokens", 2500))

    by_chapter = {}
    for seg in segments:
        by_chapter.setdefault(seg["chapter"], []).append(seg)

    chunks = []
    for chap in sorted(by_chapter):
        for piece in chunk_chapter(by_chapter[chap], limit, per_token):
            chunks.append((chap, piece))

    run.state["g_total"] = len(chunks)
    run.progress(phase="sber", g_done=0,
                 message="procházím knihu, " + str(len(chunks)) + " úseků")

    candidates = {}
    for i, (chap, piece) in enumerate(chunks, 1):
        if run.should_stop():
            break
        messages = [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": "\n\n".join(piece)},
        ]
        raw = collect_text(run, messages)
        if raw is None:
            break
        for item in extract_json_array(raw):
            term = str(item.get("vyraz") or "").strip()
            cat = str(item.get("kategorie") or "pojem").strip().lower()
            if not term or len(term) > 80:
                continue
            if cat not in CATEGORIES:
                cat = "pojem"
            key = term.lower()
            if key not in candidates:
                candidates[key] = {"term": term, "category": cat, "chapter": chap}
        run.progress(g_done=i, terms=len(candidates),
                     message="úsek " + str(i) + " / " + str(len(chunks)) +
                             ", kandidátů " + str(len(candidates)))
    return list(candidates.values())


def _phase_count(run, segments, candidates):
    """Druha cast: spocitat vyskyty a zahodit vse pod prahem."""
    minimum = int(cfg().get("min_occurrences", 2))
    run.progress(phase="pocitani",
                 message="počítám výskyty " +
                         runner.with_num(len(candidates), "kandidáta",
                                         "kandidátů", "kandidátů"))

    # text knihy najednou, k tomu mapa pozice na kapitolu
    parts, starts, chapters, pos = [], [], [], 0
    for seg in segments:
        starts.append(pos)
        chapters.append(seg["chapter"])
        parts.append(seg["src_text"])
        pos += len(seg["src_text"]) + 1
    haystack = "\n".join(parts)

    survivors = []
    junk = 0
    for cand in candidates:
        count, actual, first_pos = scan_term(cand["term"], haystack)
        if count < minimum:
            continue
        if junk_reason(actual, haystack):
            junk += 1                     # obecne slovo, mesic, nebo bez dokladu
            continue
        idx = max(0, bisect.bisect_right(starts, first_pos) - 1)
        survivors.append({"term": actual, "category": cand["category"],
                          "count": count, "first_chapter": chapters[idx]})
    run.state["junk"] = junk
    survivors.sort(key=lambda s: (-s["count"], s["term"].lower()))
    run.progress(terms=len(survivors),
                 message="prošlo " + str(len(survivors)) + " z " +
                         str(len(candidates)) + " kandidátů, " + str(junk) +
                         " vyřazeno jako obecná slova")
    return survivors


def clean_existing(slug):
    """Projde uz sestaveny slovnicek a vyhodi z nej obecna slova.

    Pouziva stejny test jako sber, jen se nemusi znovu ptat modelu. Polozky,
    ktere uzivatel sam potvrdil, se nechavaji byt.
    """
    con = projects.open_db(slug)
    if con is None:
        return None
    try:
        haystack = "\n".join(
            r["src_text"] for r in con.execute(
                "SELECT src_text FROM segment ORDER BY ord"))
        rows = con.execute(
            "SELECT id, term_src, term_cs, occurrences, locked FROM glossary"
        ).fetchall()
        removed = []
        for r in rows:
            if r["locked"]:
                continue
            reason = junk_reason(r["term_src"], haystack)
            if reason:
                removed.append({"term_src": r["term_src"], "term_cs": r["term_cs"],
                                "occurrences": r["occurrences"], "reason": reason})
                con.execute("DELETE FROM glossary WHERE id = ?", (r["id"],))
        if removed:
            con.commit()
        left = con.execute("SELECT COUNT(*) FROM glossary").fetchone()[0]
        return {"removed": removed, "removed_count": len(removed),
                "before": len(rows), "left": left}
    finally:
        con.close()


def _phase_czech(run, con, book, survivors):
    """Treti cast: cesky tvar, rod a oduvodneni. Zapisuje se prubezne."""
    if not survivors:
        return 0
    per_request = int(cfg().get("terms_per_request", 15))
    groups = [survivors[i:i + per_request]
              for i in range(0, len(survivors), per_request)]
    run.state["g_total"] = len(groups)
    run.progress(phase="tvary", g_done=0,
                 message="navrhuji české tvary, " + str(len(groups)) + " dávek")

    written = 0
    for gi, group in enumerate(groups, 1):
        if run.should_stop():
            break
        listing = "\n".join(
            str(i) + ". " + item["term"] + "  (" + item["category"] + ")"
            for i, item in enumerate(group, 1))
        messages = [
            {"role": "system", "content": czech_system(book)},
            {"role": "user", "content": listing},
        ]
        raw = collect_text(run, messages)
        if raw is None:
            break
        proposals = {}
        for item in extract_json_array(raw):
            key = str(item.get("vyraz") or "").strip().lower()
            if key:
                proposals[key] = item
        written += _store(con, run, group, proposals)
        run.progress(g_done=gi, terms=written,
                     message="dávka " + str(gi) + " / " + str(len(groups)) +
                             ", uloženo " + str(written))
    return written


def _store(con, run, group, proposals):
    """Zapis polozek. Uz zamcene polozky se nepretepou."""
    count = 0
    for item in group:
        got = proposals.get(item["term"].lower(), {})
        czech = str(got.get("cesky") or "").strip()
        gender = str(got.get("rod") or "").strip().lower()
        note = str(got.get("poznamka") or "").strip()
        category = str(got.get("kategorie") or item["category"]).strip().lower()
        if gender not in GENDERS:
            gender = ""
        if category not in CATEGORIES:
            category = item["category"]
        row = con.execute("SELECT id, locked FROM glossary WHERE term_src = ?",
                          (item["term"],)).fetchone()
        if row is not None and row["locked"]:
            continue                      # potvrzenou polozku model neprepisuje
        if row is None:
            con.execute(
                "INSERT INTO glossary (term_src, term_cs, category, gender, note,"
                " occurrences, first_chapter, locked, created_at)"
                " VALUES (?,?,?,?,?,?,?,0,?)",
                (item["term"], czech, category, gender, note, item["count"],
                 item["first_chapter"], runner.now()))
        else:
            con.execute(
                "UPDATE glossary SET term_cs=?, category=?, gender=?, note=?,"
                " occurrences=?, first_chapter=? WHERE id=?",
                (czech, category, gender, note, item["count"],
                 item["first_chapter"], row["id"]))
        count += 1
        run.emit({"type": "term", "term": {
            "term_src": item["term"], "term_cs": czech, "category": category,
            "gender": gender, "note": note, "occurrences": item["count"],
            "first_chapter": item["first_chapter"], "locked": 0}})
    con.commit()
    return count


# ------------------------------------------------- prace se slovnickem

EDITABLE = {"term_cs", "category", "gender", "note", "locked"}


def list_entries(slug):
    con = projects.open_db(slug)
    if con is None:
        return None
    try:
        rows = con.execute(
            "SELECT id, term_src, term_cs, category, gender, note, occurrences,"
            " first_chapter, locked FROM glossary"
            " ORDER BY locked, occurrences DESC, term_src COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def update_entry(slug, entry_id, fields):
    """Zmeni polozku. Vraci novy stav, nebo None kdyz polozka neexistuje."""
    sets, args = [], []
    for key, value in fields.items():
        if key not in EDITABLE:
            continue
        if key == "locked":
            value = 1 if value else 0
        elif key == "category":
            value = str(value).lower()
            if value not in CATEGORIES:
                continue
        elif key == "gender":
            value = str(value).lower()
            if value not in GENDERS and value != "":
                continue
        else:
            value = str(value).strip()
        sets.append(key + " = ?")
        args.append(value)
    con = projects.open_db(slug)
    if con is None:
        return None
    try:
        if sets:
            args.append(entry_id)
            con.execute("UPDATE glossary SET " + ", ".join(sets) + " WHERE id = ?", args)
            con.commit()
        row = con.execute(
            "SELECT id, term_src, term_cs, category, gender, note, occurrences,"
            " first_chapter, locked FROM glossary WHERE id = ?", (entry_id,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def delete_entry(slug, entry_id):
    con = projects.open_db(slug)
    if con is None:
        return False
    try:
        cur = con.execute("DELETE FROM glossary WHERE id = ?", (entry_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def lock_all(slug):
    """Potvrdi vse, co ma navrzeny cesky tvar."""
    con = projects.open_db(slug)
    if con is None:
        return 0
    try:
        cur = con.execute("UPDATE glossary SET locked = 1 WHERE locked = 0"
                          " AND term_cs != ''")
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def affected_segments(slug, entry_id):
    """Hotove segmenty, ve kterych zdrojovy vyraz stoji.

    Pouziva se, kdyz uzivatel zmeni potvrzenou polozku: ty segmenty uz jsou
    prelozene se starym tvarem.
    """
    con = projects.open_db(slug)
    if con is None:
        return None
    try:
        row = con.execute("SELECT term_src FROM glossary WHERE id = ?",
                          (entry_id,)).fetchone()
        if row is None:
            return None
        term = row["term_src"]
        hits = []
        for seg in con.execute(
                "SELECT id, ord, chapter, src_text FROM segment"
                " WHERE status = 'done' ORDER BY ord"):
            count, _, _ = scan_term(term, seg["src_text"])
            if count:
                hits.append({"id": seg["id"], "ord": seg["ord"],
                             "chapter": seg["chapter"]})
        return {"term_src": term, "segments": hits, "count": len(hits)}
    finally:
        con.close()


def mark_for_retranslation(slug, entry_id):
    """Dotcene segmenty vrati na pending, aby se prelozily s novym tvarem."""
    found = affected_segments(slug, entry_id)
    if found is None:
        return None
    ids = [s["id"] for s in found["segments"]]
    if not ids:
        return {"count": 0, "chapters": []}
    con = projects.open_db(slug)
    try:
        con.executemany("UPDATE segment SET status = 'pending' WHERE id = ?",
                        [(i,) for i in ids])
        con.commit()
    finally:
        con.close()
    chapters = sorted({s["chapter"] for s in found["segments"]})
    return {"count": len(ids), "chapters": chapters}


def collect_text(run, messages, track_speed=True):
    """Odpoved modelu jako jeden retezec. Vraci None, kdyz se ma zastavit.

    track_speed=False u kratkych pomocnych dotazu. U nich prevazi rezie nad
    generovanim, takze vychazi nizka rychlost, a kdyby prepsala udaj z davky,
    ukazovalo by okno rychlost pomocneho dotazu misto rychlosti prekladu.
    """
    chunks = []
    tokens = 0
    t0 = time.time()
    for kind, payload in llm.stream_chat(messages, run.should_stop):
        if kind == "delta":
            chunks.append(payload)
        elif kind == "usage":
            tokens = int(payload.get("completion_tokens") or 0)
    if run.should_stop():
        return None
    if not tokens:
        tokens = int(sum(len(c) for c in chunks) / 4)
    run.state["tokens_out"] += tokens
    if track_speed:
        run.state["tps"] = round(tokens / max(0.001, time.time() - t0), 1)
    return "".join(chunks)
