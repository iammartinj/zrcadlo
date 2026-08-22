"""Prekladovy beh. Davky, streamovani a zapis stavu po kazde davce.

Beh drzi vlastni vlakno, spolecnou obsluhu ma v modulu runner. Rozhrani ho
posloucha pres frontu udalosti, takze prekresleni okna beh nepreusi.
"""
import re
import time

from . import checks, db, llm, projects, prompt, runner
from . import glossary as glossary_mod
from .config import CFG

# ---------------------------------------------------------------- davkovani

def make_batches(segments, cfg):
    """Segmenty do davek po zhruba target_source_tokens. Odstavec se nikdy nedeli."""
    target = float(cfg["target_source_tokens"])
    per_token = float(cfg.get("chars_per_token", 4.0))
    max_segs = int(cfg.get("max_segments", 25))
    batches, cur, cur_tokens = [], [], 0.0
    for seg in segments:
        est = max(1.0, len(seg["src_html"]) / per_token)
        if cur and (cur_tokens + est > target or len(cur) >= max_segs):
            batches.append(cur)
            cur, cur_tokens = [], 0.0
        cur.append(seg)
        cur_tokens += est
    if cur:
        batches.append(cur)
    return batches


def _batches_by_chapter(segments, cfg):
    """Davky, ktere nikdy nepresahnou z jedne kapitoly do druhe.

    Model dostava souvisly kus textu; michat konec jedne kapitoly se zacatkem
    dalsi mu jen mate kontext.
    """
    out = []
    current_chapter = None
    group = []
    for seg in segments:
        if seg["chapter"] != current_chapter:
            if group:
                out.extend(make_batches(group, cfg))
            current_chapter = seg["chapter"]
            group = []
        group.append(seg)
    if group:
        out.extend(make_batches(group, cfg))
    return out


def est_tokens(text, cfg):
    return max(1.0, len(text) / float(cfg.get("chars_per_token", 4.0)))


# ------------------------------------------------- postupne cteni odpovedi

class MarkStream:
    """Sklada odpoved modelu a hlasi odstavce, jakmile jsou cele."""

    def __init__(self):
        self.buf = ""
        self.done = {}

    def feed(self, text):
        self.buf += text
        out = []
        marks = list(prompt.MARK_RE.finditer(self.buf))
        for i in range(len(marks) - 1):
            n = int(marks[i].group(1))
            if n in self.done:
                continue
            body = self.buf[marks[i].end():marks[i + 1].start()].strip()
            self.done[n] = body
            out.append((n, body))
        return out

    def finish(self):
        marks = list(prompt.MARK_RE.finditer(self.buf))
        out = []
        if marks:
            last = marks[-1]
            n = int(last.group(1))
            if n not in self.done:
                body = self.buf[last.end():].strip()
                if body:
                    self.done[n] = body
                    out.append((n, body))
        return out


# ------------------------------------------------------------------- beh

def active(slug):
    return runner.active(slug)


def start(slug, chapter=None):
    """Spusti preklad. chapter=None znamena celou knihu od mista, kde skoncil."""
    return runner.start(slug, "translate", _worker, {"chapter": chapter})


# --------------------------------------------------------------- slovnicek

_TERM_RE = {}


def term_pattern(term):
    """Vyraz jako cele slovo, ne jako kus jineho slova.

    Bez teto hranice sedi Eve uvnitr never nebo believe a Cor uvnitr record.
    Do promptu by se pak dostavaly polozky, ktere v davce vubec nestoji,
    a kontrola by hlasila jejich chybejici preklad.
    """
    pattern = _TERM_RE.get(term)
    if pattern is None:
        pattern = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)",
                             re.IGNORECASE)
        _TERM_RE[term] = pattern
    return pattern


def glossary_for(con, batch):
    """Jen ty polozky, jejichz zdrojovy vyraz se v davce opravdu vyskytuje."""
    rows = con.execute(
        "SELECT term_src, term_cs, category, gender FROM glossary"
        " WHERE term_cs != '' ORDER BY LENGTH(term_src) DESC").fetchall()
    if not rows:
        return []
    haystack = " ".join(seg["src_text"] for seg in batch)
    out = []
    for r in rows:
        if term_pattern(r["term_src"]).search(haystack):
            out.append(dict(r))
    return out


# ------------------------------------------------------------------ worker

def _worker(run):
    path = projects.project_dir(run.slug)
    con = db.connect(path / "project.db")
    cfg_batch = CFG["batching"]
    chapter = run.params["chapter"]
    t0 = time.time()
    try:
        runner.open_record(con, run)

        book = con.execute("SELECT * FROM book WHERE id = 1").fetchone()
        totals = projects.counts(con)
        if chapter is None:
            chapter_total = totals["total"]
            pending = [dict(r) for r in con.execute(
                "SELECT id, ord, chapter, kind, src_text, src_html, attempts"
                " FROM segment WHERE status IN ('pending','failed')"
                " ORDER BY ord").fetchall()]
        else:
            chapter_total = con.execute(
                "SELECT COUNT(*) FROM segment WHERE chapter = ?",
                (chapter,)).fetchone()[0]
            pending = [dict(r) for r in con.execute(
                "SELECT id, ord, chapter, kind, src_text, src_html, attempts"
                " FROM segment WHERE chapter = ? AND status IN ('pending','failed')"
                " ORDER BY ord", (chapter,)).fetchall()]

        run.state.update(total=totals["total"], done=totals["done"],
                         chapter_total=chapter_total,
                         chapter_done=chapter_total - len(pending))
        run.emit(dict(run.state, type="start", batches=0))

        if not pending:
            run.progress(message="Není co překládat, všechno je hotové.")
            runner.close_record(con, run, "done", t0)
            return

        batches = _batches_by_chapter(pending, cfg_batch)
        run.emit(dict(run.state, type="start", batches=len(batches)))

        segs_done = 0
        for bi, batch in enumerate(batches, 1):
            if run.should_stop():
                break
            kde = ("kapitola " + str(batch[0]["chapter"]) + ", dávka "
                   if chapter is None else "dávka ")
            run.progress(message=kde + str(bi) + " / " + str(len(batches)))
            entries = glossary_for(con, batch)
            results, stopped = _translate_batch(run, con, book, batch, cfg_batch,
                                                entries)
            # pri zastaveni se ulozi odstavce, ktere uz dobehly, zbytek zustane pending
            _commit(con, run, batch, results, entries, partial=stopped)
            if stopped:
                break
            _learn_new_names(run, con, book, batch)
            segs_done += len(batch)
            elapsed = max(0.001, time.time() - t0)
            per_seg = elapsed / max(1, segs_done)
            remaining = sum(len(b) for b in batches[bi:])
            run.progress(eta_s=int(per_seg * remaining),
                         message=kde + str(bi) + " / " + str(len(batches)) +
                                 " hotová")

        status = "stopped" if run.should_stop() else "done"
        runner.close_record(con, run, status, t0)
    except llm.LLMError as exc:
        run.state["message"] = str(exc)
        run.emit({"type": "error", "message": str(exc)})
        runner.close_record(con, run, "error", t0, str(exc))
    except Exception as exc:
        msg = type(exc).__name__ + ": " + str(exc)
        run.emit({"type": "error", "message": msg})
        runner.close_record(con, run, "error", t0, msg)
    finally:
        con.close()


def recheck(slug, chapter=None):
    """Prozene uz prelozene odstavce kontrolami znovu, bez volani modelu.

    Hodi se, kdyz se zmeni slovnicek nebo se opravi samotna kontrola: preklad
    zustane, jak je, prepocita se jen stav a duvod.
    """
    path = projects.project_dir(slug)
    if path is None:
        return None
    con = db.connect(path / "project.db")
    try:
        sql = ("SELECT id, ord, chapter, kind, src_text, src_html, tgt_html,"
               " tgt_text, status FROM segment"
               " WHERE status IN ('done','review') AND tgt_text IS NOT NULL")
        args = []
        if chapter is not None:
            sql += " AND chapter = ?"
            args.append(chapter)
        segs = [dict(r) for r in con.execute(sql + " ORDER BY ord", args)]

        changed = []
        stripped = 0
        for seg in segs:
            html = checks.strip_added_markup(seg["src_html"], seg["tgt_html"])
            if html != seg["tgt_html"]:
                stripped += 1
                seg["tgt_html"] = html
                seg["tgt_text"] = prompt.plain(html)
                con.execute("UPDATE segment SET tgt_html = ?, tgt_text = ?"
                            " WHERE id = ?", (html, seg["tgt_text"], seg["id"]))
            entries = glossary_for(con, [seg])
            problems = checks.inspect(seg, seg["tgt_html"], seg["tgt_text"], entries)
            status = "review" if problems else "done"
            note = "; ".join(p["detail"] for p in problems) if problems else None
            if status != seg["status"]:
                changed.append({"ord": seg["ord"], "from": seg["status"],
                                "to": status, "note": note})
            con.execute("UPDATE segment SET status = ?, review_note = ?"
                        " WHERE id = ?", (status, note, seg["id"]))
        con.commit()
        counts = con.execute(
            "SELECT status, COUNT(*) c FROM segment"
            + (" WHERE chapter = ?" if chapter is not None else "")
            + " GROUP BY status", args).fetchall()
        return {"checked": len(segs), "changed": changed, "stripped": stripped,
                "counts": {r["status"]: r["c"] for r in counts}}
    finally:
        con.close()


# --------------------------------------------- nova jmena behem prekladu

NAME_RE = re.compile(r"[^\W\d_][\w'’-]*", re.UNICODE)
NAME_STOP = {"The", "This", "That", "There", "Then", "They", "These", "Those",
             "But", "And", "For", "Not", "Her", "His", "She", "Its",
             "Chapter", "Book", "Part", "One", "Two", "Three", "Four", "Five",
             "Mr", "Mrs", "Ms", "Dr", "Sir", "God", "Lord", "Monday", "Tuesday",
             "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
             "January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"}
MAX_NEW_NAMES = 8


# Vse od apostrofu dal pryc. Resi privlastnovaci Robert's i staženiny I'd,
# I'll a I've: anglicke "I" se pise velkym vzdy, takze uprostred vety vypada
# jako vlastni jmeno. Po orezu zbyde "I", ktere propadne na delce.
APOSTROPHE_RE = re.compile(r"['’].*$")


def _phrases(text):
    """Souvisle useky slov s velkym pismenem uprostred vety.

    San Francisco je jedno jmeno, ne San a Francisco. Kdyz se jmena berou po
    slovech, do slovnicku napadaji ulomky, ktere samy o sobe nic neznamenaji.
    """
    out = []
    run_words = []
    run_start = None
    prev_end = None
    for m in NAME_RE.finditer(text):
        word = m.group(0)
        capital = word[:1].isupper() and checks.is_proper_name(text, m)
        spojite = prev_end is not None and text[prev_end:m.start()] in (" ", " ")
        if capital and run_words and spojite:
            run_words.append(word)
        elif capital:
            if run_words:
                out.append((" ".join(run_words), run_start))
            run_words = [word]
            run_start = m.start()
        else:
            if run_words:
                out.append((" ".join(run_words), run_start))
            run_words = []
        prev_end = m.end()
    if run_words:
        out.append((" ".join(run_words), run_start))
    return out


def _new_name_candidates(con, batch, haystack):
    """Vlastni jmena z davky, ktera slovnicek jeste nezna.

    Za vlastni jmeno se bere souvisly usek slov s velkym pismenem uprostred
    vety. Na kazdeho kandidata se pak pusti stejny filtr obecnych slov jako
    pri sberu slovnicku, aby se do nej nedostalo War nebo Lake.
    """
    known = {r["term_src"].lower()
             for r in con.execute("SELECT term_src FROM glossary")}
    found = {}
    for seg in batch:
        if seg["kind"] == "head":     # v nadpisech se velka pismena nepocitaji
            continue
        for phrase, _pos in _phrases(seg["src_text"]):
            phrase = APOSTROPHE_RE.sub("", phrase).strip()
            if len(phrase) < 3 or phrase.isupper():
                continue
            if phrase in NAME_STOP or phrase.lower() in known:
                continue
            found[phrase] = found.get(phrase, 0) + 1

    out = []
    for phrase in sorted(found, key=lambda w: -found[w]):
        if glossary_mod.junk_reason(phrase, haystack):
            continue
        out.append(phrase)
        if len(out) >= MAX_NEW_NAMES:
            break
    return out


def _learn_new_names(run, con, book, batch):
    """Jmena, ktera se objevila az pri prekladu, doplni do slovnicku.

    Od dalsiho vyskytu se pak posilaji modelu jako zbytek slovnicku.
    """
    # text knihy se pro filtr obecnych slov spocita jednou za beh
    if getattr(run, "book_text", None) is None:
        run.book_text = "\n".join(
            r["src_text"] for r in con.execute(
                "SELECT src_text FROM segment ORDER BY ord"))
    names = _new_name_candidates(con, batch, run.book_text)
    if not names:
        return
    listing = "\n".join(str(i) + ". " + n + "  (osoba)"
                        for i, n in enumerate(names, 1))
    messages = [
        {"role": "system", "content": glossary_mod.czech_system(book)},
        {"role": "user", "content": listing},
    ]
    # track_speed=False: kratky pomocny dotaz nema prepisovat rychlost prekladu
    raw = glossary_mod.collect_text(run, messages, track_speed=False)
    if raw is None:                       # beh se zastavuje
        return
    proposals = {}
    for item in glossary_mod.extract_json_array(raw):
        key = str(item.get("vyraz") or "").strip().lower()
        if key:
            proposals[key] = item
    added = 0
    for name in names:
        got = proposals.get(name.lower())
        if not got:
            continue
        czech = str(got.get("cesky") or "").strip()
        if not czech:
            continue
        gender = str(got.get("rod") or "").strip().lower()
        category = str(got.get("kategorie") or "osoba").strip().lower()
        if gender not in glossary_mod.GENDERS:
            gender = ""
        if category not in glossary_mod.CATEGORIES:
            category = "osoba"
        note = str(got.get("poznamka") or "").strip()
        try:
            con.execute(
                "INSERT INTO glossary (term_src, term_cs, category, gender, note,"
                " occurrences, first_chapter, locked, created_at)"
                " VALUES (?,?,?,?,?,?,?,0,?)",
                (name, czech, category, gender,
                 (note + " Doplněno během překladu.").strip(),
                 1, batch[0]["chapter"], runner.now()))
        except Exception:
            continue                      # jmeno uz mezitim nekdo vlozil
        added += 1
        run.emit({"type": "term", "term": {
            "term_src": name, "term_cs": czech, "category": category,
            "gender": gender, "note": note, "occurrences": 1,
            "first_chapter": batch[0]["chapter"], "locked": 0}})
    if added:
        con.commit()
        run.progress(message="slovníček doplněn o " +
                             runner.with_num(added, "jméno", "jména", "jmen"))


def _translate_batch(run, con, book, batch, cfg_batch, glossary):
    """Jedna davka. Pri nesedicim poctu odstavcu opakuje, pak jde po odstavcich.

    Vraci dvojici (vysledky, zastaveno). Pri zastaveni jsou ve vysledcich jen
    odstavce, ktere stihly dobehnout.
    """
    for attempt in (0, 1):
        refs = []
        messages = [
            {"role": "system",
             "content": prompt.system_prompt(book, glossary, strict=attempt > 0)},
            {"role": "user", "content": prompt.user_message(batch, refs)},
        ]
        parts, stopped = _run_stream(run, messages, batch, refs)
        if stopped:
            return parts, True
        if all(i in parts for i in range(1, len(batch) + 1)):
            return {i: parts[i] for i in range(1, len(batch) + 1)}, False
        run.progress(message="dávka vrátila " + str(len(parts)) + " z " +
                             str(len(batch)) + " odstavců, opakuji")
    return _translate_one_by_one(run, book, batch, glossary)


def _translate_one_by_one(run, book, batch, glossary):
    """Zaloha po druhem neuspechu: kazdy odstavec zvlast."""
    run.progress(message="překládám odstavce jednotlivě")
    out = {}
    for i, seg in enumerate(batch, 1):
        if run.should_stop():
            return out, True
        refs = []
        messages = [
            {"role": "system", "content": prompt.system_prompt(book, glossary)},
            {"role": "user", "content": prompt.user_message([seg], refs)},
        ]
        parts, stopped = _run_stream(run, messages, [seg], refs)
        if stopped:
            return out, True
        out[i] = parts.get(1, "")
    return out, False


def _run_stream(run, messages, batch, refs):
    """Posle davku, streamuje odpoved a hlasi hotove odstavce rovnou do okna."""
    ms = MarkStream()
    chars = 0
    batch_tokens = 0
    t_batch = time.time()
    try:
        for kind, payload in llm.stream_chat(messages, run.should_stop):
            if kind == "delta":
                chars += len(payload)
                for n, body in ms.feed(payload):
                    _emit_draft(run, batch, refs, n, body)
                if chars % 400 < len(payload):
                    # behem streamu je k dispozici jen odhad podle znaku
                    _tick(run, chars / 4.0, t_batch)
            elif kind == "usage":
                batch_tokens = int(payload.get("completion_tokens") or 0)
    except llm.LLMError:
        raise
    stopped = run.should_stop()
    if not stopped:
        for n, body in ms.finish():
            _emit_draft(run, batch, refs, n, body)
    if not batch_tokens:
        batch_tokens = int(chars / 4)      # server pocet tokenu neposlal
    run.state["tokens_out"] += batch_tokens
    # na konci davky uz je znamy skutecny pocet tokenu, ten je presnejsi
    _tick(run, batch_tokens, t_batch)
    parts = {n: prompt.restore_refs(prompt.sanitize(b), refs)
             for n, b in ms.done.items()}
    return parts, stopped


def _tick(run, tokens, t_batch):
    dt = max(0.001, time.time() - t_batch)
    run.progress(tps=round(tokens / dt, 1))


def _emit_draft(run, batch, refs, n, body):
    """Hotovy odstavec putuje do okna hned, jeste pred zapisem davky."""
    idx = n - 1
    if not 0 <= idx < len(batch):
        return
    html = prompt.restore_refs(prompt.sanitize(body), refs)
    run.emit({"type": "draft", "ord": batch[idx]["ord"], "html": html})


def _commit(con, run, batch, results, entries, partial=False):
    """Zapis po davce. Kdyz se aplikace zavre, hotove segmenty zustanou.

    Pri partial=True se zapisou jen odstavce, ktere dobehly. Zbytek zustane
    pending, aby se pri navazani prelozil znovu a nic se neztratilo.

    Kazdy odstavec projde kontrolami. Kdyz nesedi, dostane stav 'review'
    a duvod. Prekladat se nepreklada znovu a nic se neprepisuje.
    """
    written = []      # (segment, html, text, status, poznamka)
    for i, seg in enumerate(batch, 1):
        html = (results.get(i) or "").strip()
        # zvyrazneni, ktere zdroj nema, se rovnou odstrani; text zustava
        cistc = checks.strip_added_markup(seg["src_html"], html)
        if cistc != html:
            run.segments_stripped += 1   # at je videt, jestli si model znacky vymysli
        html = cistc
        text = prompt.plain(html)
        if partial and not text:
            continue
        if not text:
            written.append((seg, html, text, "failed", None))
            continue
        problems = checks.inspect(seg, html, text, entries)
        if problems:
            note = "; ".join(p["detail"] for p in problems)
            written.append((seg, html, text, "review", note))
        else:
            written.append((seg, html, text, "done", None))
    if not written:
        return
    con.executemany(
        "UPDATE segment SET tgt_html=?, tgt_text=?, status=?, review_note=?,"
        " attempts = attempts + 1 WHERE id=?",
        [(html, text, status, note, seg["id"])
         for seg, html, text, status, note in written])
    con.commit()
    done_delta = sum(1 for w in written if w[3] == "done")
    review_delta = sum(1 for w in written if w[3] == "review")
    run.state["done"] += done_delta
    run.state["chapter_done"] += done_delta
    run.segments_done += done_delta
    run.segments_failed += len(written) - done_delta - review_delta
    run.segments_review = getattr(run, "segments_review", 0) + review_delta
    for seg, html, text, status, note in written:
        run.emit({"type": "segment", "ord": seg["ord"], "status": status,
                  "html": html, "review_note": note})
