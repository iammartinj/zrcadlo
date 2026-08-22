"""Export hotoveho prekladu.

Tri podoby:
  translation - EPUB jen s prekladem, se zachovanou strukturou kapitol
  mirror      - zrcadlovy EPUB, po kazdem odstavci originalu nasleduje preklad
  markdown    - prosty Markdown

Poradi odstavcu i uroven nadpisu se drzi. Neprelozene odstavce se vypustit
nesmi, jinak by pocet odstavcu v exportu nesedel s originalem; misto prekladu
se u nich vypise zdrojovy text.
"""
import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import epub

from . import db, projects

NOTEREF_RE = re.compile(r'<a class="noteref" data-note="([^"]*)">(.*?)</a>')
# Escapuje se jen to, co Markdown uprostred radku opravdu cte jako znacku.
# Tecky, pomlcky a zavorky se v beletrii vyskytuji porad a escapovat je
# znamena zaplevelit text zpetnymi lomitky.
MD_ESCAPE = re.compile(r"([\\`*_\[\]])")
# na zacatku radku ma vyznam jeste toto
MD_LINE_START = re.compile(r"^([#>+]|\d+\.|-\s)")

KINDS = ("translation", "mirror", "markdown")


# ---------------------------------------------------------------- pomocne

def _clean_id(raw, fallback):
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(raw or "")).strip("-")
    return slug or fallback


def _epub_noterefs(html):
    """Odkaz na poznamku prevede do podoby, ktere rozumi ctecky."""
    def sub(m):
        target = _clean_id(m.group(1), "note")
        return ('<sup><a epub:type="noteref" href="#' + target + '">' +
                m.group(2) + "</a></sup>")
    return NOTEREF_RE.sub(sub, html)


def _strip_noterefs(html):
    return NOTEREF_RE.sub("", html)


def _text_for(seg, side):
    """Text jednoho segmentu. Kdyz preklad chybi, vraci se zdroj."""
    if side == "src":
        return seg["src_html"], False
    html = (seg["tgt_html"] or "").strip()
    if html:
        return html, False
    return seg["src_html"], True          # neprelozeno, jde ven original


# ------------------------------------------------------------------ EPUB

def _chapter_html(segments, mirror):
    """Jedna kapitola jako XHTML. Poznamky jdou na konec kapitoly."""
    body = []
    notes = []
    quote_open = False
    missing = 0
    paragraphs = 0

    def close_quote():
        nonlocal quote_open
        if quote_open:
            body.append("</blockquote>")
            quote_open = False

    for seg in segments:
        if seg["kind"] == "note":
            html, gap = _text_for(seg, "tgt")
            missing += 1 if gap else 0
            nid = _clean_id(seg["note_id"], "note-" + str(seg["ord"]))
            mark = seg["note_txt"] or str(seg["ord"])
            note = ('<aside epub:type="footnote" id="' + nid + '">'
                    '<p><sup>' + mark + "</sup> " + _strip_noterefs(html) +
                    "</p></aside>")
            if mirror:
                src_html, _ = _text_for(seg, "src")
                note = ('<aside epub:type="footnote" id="' + nid + '">'
                        '<p class="src"><sup>' + mark + "</sup> " +
                        _strip_noterefs(src_html) + "</p>"
                        '<p class="tgt"><sup>' + mark + "</sup> " +
                        _strip_noterefs(html) + "</p></aside>")
            notes.append(note)
            paragraphs += 1
            continue

        tgt_html, gap = _text_for(seg, "tgt")
        missing += 1 if gap else 0
        src_html, _ = _text_for(seg, "src")
        paragraphs += 1

        if seg["kind"] == "head":
            close_quote()
            level = min(6, max(1, seg["level"] or 1))
            tag = "h" + str(level)
            if mirror:
                body.append("<" + tag + ' class="src">' +
                            _epub_noterefs(src_html) + "</" + tag + ">")
                body.append("<" + tag + ' class="tgt">' +
                            _epub_noterefs(tgt_html) + "</" + tag + ">")
            else:
                body.append("<" + tag + ">" + _epub_noterefs(tgt_html) +
                            "</" + tag + ">")
            continue

        if seg["kind"] == "quote":
            if not quote_open:
                body.append("<blockquote>")
                quote_open = True
        else:
            close_quote()

        if mirror:
            body.append('<p class="src">' + _epub_noterefs(src_html) + "</p>")
            body.append('<p class="tgt">' + _epub_noterefs(tgt_html) + "</p>")
        else:
            body.append("<p>" + _epub_noterefs(tgt_html) + "</p>")

    close_quote()
    if notes:
        body.append('<hr class="notes"/>')
        body.extend(notes)
    return "\n".join(body), paragraphs, missing


MIRROR_CSS = """
body { line-height: 1.6; }
p.src, h1.src, h2.src, h3.src, h4.src, h5.src, h6.src {
  color: #666; font-style: normal; margin-bottom: 0.2em;
}
p.tgt, h1.tgt, h2.tgt, h3.tgt, h4.tgt, h5.tgt, h6.tgt {
  color: #111; margin-top: 0.2em; margin-bottom: 1.4em;
}
blockquote { margin-left: 1.5em; color: #444; }
aside { font-size: 0.9em; color: #444; }
"""

PLAIN_CSS = """
body { line-height: 1.6; }
blockquote { margin-left: 1.5em; color: #444; }
aside { font-size: 0.9em; color: #444; }
"""


def build_epub(slug, mirror, out_path):
    con = projects.open_db(slug)
    if con is None:
        return None
    try:
        book_row = con.execute("SELECT * FROM book WHERE id = 1").fetchone()
        chapters = con.execute("SELECT ord, title FROM chapter ORDER BY ord").fetchall()
        out = epub.EpubBook()
        out.set_identifier("zrcadlo-" + slug + ("-mirror" if mirror else ""))
        title = book_row["title"]
        out.set_title(title + (" (zrcadlově)" if mirror else "") )
        out.set_language(book_row["target_lang"] or "cs")
        if book_row["author"]:
            out.add_author(book_row["author"])

        style = epub.EpubItem(uid="style", file_name="style/main.css",
                              media_type="text/css",
                              content=(MIRROR_CSS if mirror else PLAIN_CSS))
        out.add_item(style)

        items = []
        total_paragraphs = 0
        total_missing = 0
        for chap in chapters:
            segs = [dict(r) for r in con.execute(
                "SELECT ord, kind, level, src_html, tgt_html, note_id, note_txt"
                " FROM segment WHERE chapter = ? ORDER BY ord", (chap["ord"],))]
            html, count, missing = _chapter_html(segs, mirror)
            total_paragraphs += count
            total_missing += missing
            name = "ch%03d.xhtml" % chap["ord"]
            item = epub.EpubHtml(title=chap["title"], file_name=name,
                                 lang=book_row["target_lang"] or "cs")
            # bez deklarace <?xml?>: ebooklib si obsah sam parsuje a na retezci
            # s deklaraci kodovani spadne pri sestavovani navigace
            item.content = (
                '<html xmlns="http://www.w3.org/1999/xhtml"'
                ' xmlns:epub="http://www.idpf.org/2007/ops">'
                "<head><title>" + _escape(chap["title"]) + "</title>"
                '<link rel="stylesheet" href="style/main.css" type="text/css"/>'
                "</head><body>" + html + "</body></html>")
            item.add_item(style)
            out.add_item(item)
            items.append(item)

        out.toc = tuple(epub.Link(it.file_name, it.title, "ch%d" % (i + 1))
                        for i, it in enumerate(items))
        out.add_item(epub.EpubNcx())
        out.add_item(epub.EpubNav())
        out.spine = ["nav"] + items
        epub.write_epub(str(out_path), out)
        return {"paragraphs": total_paragraphs, "missing": total_missing,
                "chapters": len(chapters)}
    finally:
        con.close()


def _escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# -------------------------------------------------------------- Markdown

def html_to_markdown(html):
    """Prevod naseho omezeneho HTML na Markdown. Kurziva a tucne se drzi."""
    soup = BeautifulSoup(html, "lxml")
    node = soup.body or soup

    def walk(el):
        out = []
        for child in el.children:
            name = getattr(child, "name", None)
            if name is None:
                out.append(MD_ESCAPE.sub(r"\\\1", str(child)))
            elif name == "em":
                out.append("*" + walk(child).strip() + "*")
            elif name == "strong":
                out.append("**" + walk(child).strip() + "**")
            elif name == "br":
                out.append("  \n")
            elif name == "a" and "noteref" in (child.get("class") or []):
                out.append("[^" + _clean_id(child.get("data-note"), "note") + "]")
            else:
                out.append(walk(child))
        return "".join(out)

    return re.sub(r"[ \t]+", " ", walk(node)).strip()


def md_paragraph(text):
    """Odstavec, ktery nezacne omylem vypadat jako nadpis nebo odrazka."""
    return MD_LINE_START.sub(r"\\\1", text)


def build_markdown(slug, out_path):
    con = projects.open_db(slug)
    if con is None:
        return None
    try:
        book_row = con.execute("SELECT * FROM book WHERE id = 1").fetchone()
        chapters = con.execute("SELECT ord, title FROM chapter ORDER BY ord").fetchall()
        lines = ["# " + book_row["title"]]
        if book_row["author"]:
            lines.append("")
            lines.append("*" + book_row["author"] + "*")
        total = missing = 0

        for chap in chapters:
            segs = [dict(r) for r in con.execute(
                "SELECT ord, kind, level, src_html, tgt_html, note_id, note_txt"
                " FROM segment WHERE chapter = ? ORDER BY ord", (chap["ord"],))]
            notes = []
            lines.append("")
            for seg in segs:
                html, gap = _text_for(seg, "tgt")
                missing += 1 if gap else 0
                total += 1
                text = html_to_markdown(html)
                if seg["kind"] == "note":
                    nid = _clean_id(seg["note_id"], "note-" + str(seg["ord"]))
                    notes.append("[^" + nid + "]: " + text)
                    continue
                lines.append("")
                if seg["kind"] == "head":
                    level = min(6, max(1, seg["level"] or 1))
                    lines.append("#" * level + " " + text)
                elif seg["kind"] == "quote":
                    lines.append("> " + md_paragraph(text))
                else:
                    lines.append(md_paragraph(text))
            if notes:
                lines.append("")
                lines.extend(notes)

        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"paragraphs": total, "missing": missing,
                "chapters": len(chapters)}
    finally:
        con.close()


# ------------------------------------------------------------------ beh

def run(slug, kind):
    """Vyrobi soubor a vrati, co se do nej dostalo."""
    if kind not in KINDS:
        raise ValueError("Neznámý formát exportu: " + str(kind))
    pdir = projects.project_dir(slug)
    if pdir is None:
        return None
    outdir = pdir / "export"
    outdir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")

    if kind == "markdown":
        path = outdir / (slug + "-" + stamp + ".md")
        info = build_markdown(slug, path)
    else:
        suffix = "-zrcadlo" if kind == "mirror" else "-preklad"
        path = outdir / (slug + suffix + "-" + stamp + ".epub")
        info = build_epub(slug, kind == "mirror", path)

    if info is None:
        return None

    con = projects.open_db(slug)
    try:
        source_total = con.execute("SELECT COUNT(*) FROM segment").fetchone()[0]
        started = datetime.now().isoformat(timespec="seconds")
        con.execute(
            "INSERT INTO run (kind, started_at, finished_at, segments_done, status)"
            " VALUES ('export',?,?,?,'done')", (started, started, info["paragraphs"]))
        con.commit()
    finally:
        con.close()

    info.update({
        "kind": kind,
        "path": str(path),
        "name": path.name,
        "size": path.stat().st_size,
        "source_paragraphs": source_total,
        "counts_match": source_total == info["paragraphs"],
    })
    return info
