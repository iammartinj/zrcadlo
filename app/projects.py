"""Sprava projektu. Jedna kniha = jedna slozka v projects/<slug>/."""
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from . import db, epubin
from .config import PROJECTS_DIR

SLUG_BAD = re.compile(r"[^a-z0-9]+")


def slugify(text, fallback="kniha"):
    norm = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(c for c in norm if not unicodedata.combining(c))
    slug = SLUG_BAD.sub("-", ascii_text.lower()).strip("-")
    slug = slug[:60].strip("-")
    return slug or fallback


def _unique_dir(slug):
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    candidate = PROJECTS_DIR / slug
    n = 2
    while candidate.exists():
        candidate = PROJECTS_DIR / (slug + "-" + str(n))
        n += 1
    return candidate


def project_dir(slug):
    path = PROJECTS_DIR / slug
    if path.parent.resolve() != PROJECTS_DIR.resolve() or not path.is_dir():
        return None
    return path


def open_db(slug):
    path = project_dir(slug)
    if path is None:
        return None
    dbf = path / "project.db"
    if not dbf.exists():
        return None
    return db.connect(dbf)


def now():
    return datetime.now().isoformat(timespec="seconds")


def import_epub(upload_path, original_name, source_lang="en", target_lang="cs"):
    """Rozlozi EPUB, zalozi slozku projektu a ulozi vse do project.db."""
    parsed = epubin.parse(str(upload_path))
    if not parsed.segments:
        raise ValueError("V EPUBu se nenašel žádný text k překladu.")

    slug = slugify(parsed.title or Path(original_name).stem)
    pdir = _unique_dir(slug)
    pdir.mkdir(parents=True)

    epub_name = slugify(Path(original_name).stem, "zdroj") + ".epub"
    source_copy = pdir / epub_name
    shutil.copyfile(upload_path, source_copy)
    src_hash = epubin.file_hash(source_copy)

    con = db.init(pdir / "project.db")
    started = now()
    try:
        con.execute(
            "INSERT INTO book (id, title, author, source_path, source_hash,"
            " source_lang, target_lang, created_at) VALUES (1,?,?,?,?,?,?,?)",
            (parsed.title, parsed.author, str(source_copy), src_hash,
             parsed.language or source_lang, target_lang, started))
        con.executemany(
            "INSERT INTO chapter (ord, title, href) VALUES (?,?,?)",
            [(c.ord, c.title, c.href) for c in parsed.chapters])
        con.executemany(
            "INSERT INTO segment (ord, chapter, kind, level, src_text, src_html,"
            " status, src_hash, note_id, note_ord, note_txt)"
            " VALUES (?,?,?,?,?,?,'pending',?,?,?,?)",
            [(s.ord, s.chapter, s.kind, s.level, s.src_text, s.src_html,
              epubin.segment_hash(s), s.note_id or None,
              s.note_ord or None, s.note_txt or None) for s in parsed.segments])
        con.execute(
            "INSERT INTO run (kind, started_at, finished_at, segments_done, status)"
            " VALUES ('import',?,?,?,'done')",
            (started, now(), len(parsed.segments)))
        con.commit()
    finally:
        con.close()

    return pdir.name


def counts(con):
    row = con.execute(
        "SELECT COUNT(*) AS total,"
        " SUM(status='done') AS done,"
        " SUM(status='review') AS review,"
        " SUM(status='failed') AS failed"
        " FROM segment").fetchone()
    gl = con.execute(
        "SELECT COUNT(*) AS total, SUM(locked=0) AS unlocked FROM glossary"
    ).fetchone()
    return {
        "total": row["total"] or 0,
        "done": row["done"] or 0,
        "review": row["review"] or 0,
        "failed": row["failed"] or 0,
        "glossary_total": gl["total"] or 0,
        "glossary_unconfirmed": gl["unlocked"] or 0,
    }


STYLE_FIELDS = {
    "style_register": {"neutralni", "hovorovy", "archaizujici"},
    "style_narrator": {"neurceno", "muz", "zena"},
    "style_address": {"neurceno", "tykani", "vykani"},
}


def mark_interrupted_runs(con):
    """Behy, ktere zustaly viset po padu, uz nebezi. Vraci jejich pocet."""
    cur = con.execute(
        "UPDATE run SET status = 'interrupted',"
        " error = COALESCE(error, 'běh skončil bez uzavření, nejspíš pádem"
        " nebo zavřením aplikace')"
        " WHERE status = 'running'")
    if cur.rowcount:
        con.commit()
    return cur.rowcount


def source_path(slug, book_row):
    """Kde zdrojovy EPUB opravdu lezi.

    Ulozena cesta je absolutni z doby importu. Kopie souboru je ale vzdy uvnitr
    slozky projektu, takze se hleda nejdriv tam: projekt pak jde presunout
    i prejmenovat, aniz by se zdroj ztratil.
    """
    stored = Path(book_row["source_path"])
    pdir = project_dir(slug)
    if pdir is not None:
        inside = pdir / stored.name
        if inside.exists():
            return inside
    return stored


def source_state(slug, book_row):
    """Porovna ulozeny otisk zdrojoveho EPUBu s tim, co lezi na disku."""
    path = source_path(slug, book_row)
    if not path.exists():
        return {"exists": False, "changed": False}
    try:
        current = epubin.file_hash(path)
    except OSError:
        return {"exists": True, "changed": False}
    return {"exists": True, "changed": current != book_row["source_hash"]}


def update_book(slug, fields):
    """Ulozi stylovou kartu projektu. Vraci novy stav knihy."""
    sets, args = [], []
    for key, allowed in STYLE_FIELDS.items():
        if key in fields:
            value = str(fields[key]).strip().lower()
            if value in allowed:
                sets.append(key + " = ?")
                args.append(value)
    if "style_note" in fields:
        sets.append("style_note = ?")
        args.append(str(fields["style_note"]).strip()[:2000])
    if "feminize_surnames" in fields:
        sets.append("feminize_surnames = ?")
        args.append(1 if fields["feminize_surnames"] else 0)
    if not sets:
        return book_info(slug)
    con = open_db(slug)
    if con is None:
        return None
    try:
        args.append(1)
        con.execute("UPDATE book SET " + ", ".join(sets) + " WHERE id = ?", args)
        con.commit()
    finally:
        con.close()
    return book_info(slug)


def runs(slug, limit=50):
    """Historie behu projektu, od nejnovejsiho."""
    con = open_db(slug)
    if con is None:
        return None
    try:
        mark_interrupted_runs(con)
        rows = con.execute(
            "SELECT id, kind, started_at, finished_at, segments_done, tokens_out,"
            " tokens_per_s, status, error FROM run ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def book_info(slug, check_source=False):
    con = open_db(slug)
    if con is None:
        return None
    try:
        book = con.execute("SELECT * FROM book WHERE id = 1").fetchone()
        if book is None:
            return None
        chapters = con.execute(
            "SELECT c.ord, c.title, c.title AS name,"
            " (SELECT COUNT(*) FROM segment s WHERE s.chapter = c.ord) AS segments,"
            " (SELECT MIN(s.ord) FROM segment s WHERE s.chapter = c.ord) AS first_ord,"
            " (SELECT SUM(s.status='done') FROM segment s WHERE s.chapter = c.ord) AS done"
            " FROM chapter c ORDER BY c.ord").fetchall()
        src = source_path(slug, book)
        info = {
            "slug": slug,
            "title": book["title"],
            "author": book["author"] or "",
            "source_file": src.name,
            "source_exists": src.exists(),
            "source_lang": book["source_lang"],
            "target_lang": book["target_lang"],
            "created_at": book["created_at"],
            "feminize_surnames": bool(book["feminize_surnames"]),
            "style": {
                "register": book["style_register"],
                "narrator": book["style_narrator"],
                "address": book["style_address"],
                "note": book["style_note"],
            },
            "chapters": [dict(c) for c in chapters],
        }
        info.update(counts(con))

        # kde se da navazat: prvni kapitola, ve ktere jeste neco ceka
        resume = con.execute(
            "SELECT chapter, COUNT(*) AS left_ FROM segment"
            " WHERE status IN ('pending','failed')"
            " GROUP BY chapter ORDER BY chapter LIMIT 1").fetchone()
        info["resume"] = ({"chapter": resume["chapter"], "left": resume["left_"]}
                          if resume else None)

        mark_interrupted_runs(con)
        last = con.execute(
            "SELECT kind, started_at, finished_at, segments_done, status"
            " FROM run WHERE kind IN ('translate','glossary')"
            " ORDER BY id DESC LIMIT 1").fetchone()
        info["last_run"] = dict(last) if last else None

        if check_source:
            info["source"] = source_state(slug, book)
        return info
    finally:
        con.close()


def list_projects():
    if not PROJECTS_DIR.exists():
        return []
    out = []
    for path in sorted(PROJECTS_DIR.iterdir()):
        if not (path / "project.db").exists():
            continue
        info = book_info(path.name)
        if info:
            out.append({k: info[k] for k in
                        ("slug", "title", "author", "total", "done", "created_at")})
    out.sort(key=lambda p: p["created_at"], reverse=True)
    return out


def relabel_chapters(slug):
    """Prepocita nazvy kapitol z nadpisu, ktere uz jsou v databazi.

    Pouziva se u projektu nactenych driv, nez se opravilo pojmenovavani.
    Preklad se nedotkne, meni se jen nazev kapitoly.
    """
    con = open_db(slug)
    if con is None:
        return None
    try:
        book_title = con.execute("SELECT title FROM book WHERE id = 1").fetchone()[0]
        zmeny = []
        for chap in con.execute("SELECT ord, title FROM chapter ORDER BY ord").fetchall():
            heads = [r["src_text"] for r in con.execute(
                "SELECT src_text FROM segment WHERE chapter = ? AND kind = 'head'"
                " ORDER BY ord", (chap["ord"],))]
            novy = epubin.chapter_title(chap["title"], heads, book_title, chap["ord"])
            if novy != chap["title"]:
                zmeny.append({"chapter": chap["ord"], "from": chap["title"],
                              "to": novy})
                con.execute("UPDATE chapter SET title = ? WHERE ord = ?",
                            (novy, chap["ord"]))
        if zmeny:
            con.commit()
        return zmeny
    finally:
        con.close()


def reset_segment(slug, ord_):
    """Vrati jeden segment k prekladu. Vraci kapitolu, nebo None."""
    con = open_db(slug)
    if con is None:
        return None
    try:
        row = con.execute("SELECT id, chapter FROM segment WHERE ord = ?",
                          (ord_,)).fetchone()
        if row is None:
            return None
        con.execute("UPDATE segment SET status = 'pending', review_note = NULL"
                    " WHERE id = ?", (row["id"],))
        con.commit()
        return row["chapter"]
    finally:
        con.close()


def segments(slug, chapter=None, offset=0, limit=0):
    con = open_db(slug)
    if con is None:
        return None
    try:
        sql = ("SELECT id, ord, chapter, kind, level, src_text, src_html,"
               " tgt_text, tgt_html, status, attempts, note_id, note_ord,"
               " note_txt, review_note FROM segment")
        args = []
        if chapter is not None:
            sql += " WHERE chapter = ?"
            args.append(chapter)
        sql += " ORDER BY ord"
        if limit:
            sql += " LIMIT ? OFFSET ?"
            args += [limit, offset]
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()
