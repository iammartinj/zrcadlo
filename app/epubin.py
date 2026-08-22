"""Rozklad EPUBu na kapitoly a segmenty.

Segmentem je vzdy cely odstavec, nikdy se nedeli. Zachovava se poradi,
uroven nadpisu a kurziva. Poznamky pod carou jdou zvlast a nesou odkaz
na segment, kde se na ne odkazuje.
"""
import hashlib
import re
import unicodedata
import warnings
from dataclasses import dataclass, field

import ebooklib
from bs4 import BeautifulSoup, NavigableString, Tag
from ebooklib import epub

# bloky, ktere samy o sobe tvori segment
LEAF_BLOCKS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dd", "dt",
               "figcaption", "pre", "caption"}
# bloky, do kterych se zanoruje
CONTAINERS = {"body", "div", "section", "article", "blockquote", "ol", "ul",
              "dl", "table", "thead", "tbody", "tr", "td", "th", "figure",
              "header", "footer", "main", "aside", "details", "hgroup"}
# inline znacky, ktere se v textu zachovavaji
KEEP_INLINE = {"em", "strong", "sup", "sub", "br"}
NORMALIZE = {"i": "em", "b": "strong", "cite": "em", "u": "em"}

NOTE_TYPES = {"footnote", "endnote", "rearnote", "note", "footnotes", "endnotes"}
NOTE_ROLES = {"doc-footnote", "doc-endnote"}
NOTEREF_TYPES = {"noteref", "footnoteref", "endnoteref"}
NOTEREF_ROLES = {"doc-noteref"}

WS = re.compile(r"\s+")

# XHTML se schvalne cte HTML parserem, upozorneni na to je jen sum
warnings.filterwarnings("ignore", category=UserWarning, module="ebooklib")
warnings.filterwarnings("ignore", category=FutureWarning, module="ebooklib")
try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def file_hash(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Segment:
    ord: int = 0
    chapter: int = 0
    kind: str = "para"
    level: int = 0
    src_text: str = ""
    src_html: str = ""
    note_id: str = ""
    note_ord: int = 0
    note_txt: str = ""


@dataclass
class Chapter:
    ord: int
    title: str
    href: str = ""


@dataclass
class Parsed:
    title: str = ""
    author: str = ""
    language: str = "en"
    chapters: list = field(default_factory=list)
    segments: list = field(default_factory=list)


def _epub_type(tag):
    raw = tag.get("epub:type") or tag.get("{http://www.idpf.org/2007/ops}type") or ""
    return set(str(raw).lower().split())


def _roles(tag):
    return set(str(tag.get("role") or "").lower().split())


def _is_note_body(tag):
    if _epub_type(tag) & NOTE_TYPES or _roles(tag) & NOTE_ROLES:
        return True
    cls = " ".join(tag.get("class") or []).lower()
    if tag.name == "aside" and ("note" in cls or "footnote" in cls):
        return True
    return False


def _is_noteref(tag):
    if tag.name != "a":
        return False
    if _epub_type(tag) & NOTEREF_TYPES or _roles(tag) & NOTEREF_ROLES:
        return True
    cls = " ".join(tag.get("class") or []).lower()
    if "noteref" in cls or "footnote" in cls:
        return True
    # zapis <sup><a href="#fn1">1</a></sup> se pouziva bez epub:type
    href = tag.get("href") or ""
    if href.startswith("#") and tag.parent is not None and tag.parent.name in ("sup", "sub"):
        return True
    return False


def _inline_html(node, notes_seen):
    """Prevede obsah bloku na ulozitelne HTML. Kurzivu drzi, zbytek zahazuje."""
    out = []
    for child in node.children:
        if isinstance(child, NavigableString):
            out.append(str(child).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            continue
        if not isinstance(child, Tag):
            continue
        name = NORMALIZE.get(child.name, child.name)
        if _is_noteref(child):
            target = (child.get("href") or "").lstrip("#").split("/")[-1]
            mark = WS.sub(" ", child.get_text()).strip() or str(len(notes_seen) + 1)
            if target:
                notes_seen.append((target, mark))
                out.append('<a class="noteref" data-note="' + target + '">' + mark + '</a>')
            continue
        if name == "br":
            out.append("<br>")
            continue
        inner = _inline_html(child, notes_seen)
        if name in KEEP_INLINE and inner.strip():
            out.append("<" + name + ">" + inner + "</" + name + ">")
        else:
            out.append(inner)
    return "".join(out)


def _plain(html):
    return WS.sub(" ", BeautifulSoup(html, "lxml").get_text()).strip()


def _toc_titles(book):
    """Mapa href souboru -> nadpis z obsahu knihy."""
    titles = {}

    def walk(items):
        for it in items:
            if isinstance(it, (list, tuple)):
                walk(list(it))
            elif isinstance(it, epub.Link):
                key = it.href.split("#")[0]
                if key and it.title:
                    titles.setdefault(key, it.title.strip())
            elif isinstance(it, epub.Section):
                if it.href and it.title:
                    titles.setdefault(it.href.split("#")[0], it.title.strip())

    try:
        walk(list(book.toc))
    except Exception:
        pass
    return titles


def _collect_blocks(node, quote_depth, out, note_bodies):
    """Projde strom a vraci ploche bloky v poradi, v jakem stoji v textu."""
    for child in node.children:
        if not isinstance(child, Tag):
            continue
        name = child.name
        if name in ("script", "style", "nav", "svg", "img", "hr", "head"):
            continue
        if _is_note_body(child):
            nid = child.get("id") or ("__anon" + str(len(note_bodies)))
            note_bodies[nid] = child
            continue
        if name in LEAF_BLOCKS:
            out.append((child, quote_depth))
            continue
        if name in CONTAINERS:
            depth = quote_depth + 1 if name == "blockquote" else quote_depth
            before = len(out)
            _collect_blocks(child, depth, out, note_bodies)
            if len(out) == before and child.get_text(strip=True):
                out.append((child, quote_depth))
            continue
        if child.get_text(strip=True):
            out.append((child, quote_depth))


def _same_title(a, b):
    """Porovna nazvy bez ohledu na velikost pismen, diakritiku a mezery."""
    def norm(t):
        t = unicodedata.normalize("NFKD", (t or "").strip().lower())
        t = "".join(c for c in t if not unicodedata.combining(c))
        return WS.sub(" ", t)
    return bool(norm(a)) and norm(a) == norm(b)


def chapter_title(toc_title, heads, book_title, chap_ord):
    """Nazev kapitoly z obsahu knihy, nebo z nadpisu v textu.

    Titulni list byva slepeny s prvni kapitolou do jednoho souboru a obsah
    knihy pak pro nej uvadi nazev knihy. V takovem pripade se vezme nadpis,
    ktery za nim nasleduje, jinak by se cela kapitola tvarila jako titulni list.
    """
    title = toc_title or (heads[0] if heads else "")
    if _same_title(title, book_title):
        for i, h in enumerate(heads):
            if _same_title(h, book_title) and i + 1 < len(heads):
                title = heads[i + 1]
                break
    return title or ("Kapitola " + str(chap_ord))


def _meta(book, name):
    try:
        vals = book.get_metadata("DC", name)
        if vals:
            return str(vals[0][0]).strip()
    except Exception:
        pass
    return ""


def parse(path):
    book = epub.read_epub(path, options={"ignore_ncx": False})
    res = Parsed()
    res.title = _meta(book, "title") or "Bez nazvu"
    res.author = _meta(book, "creator") or ""
    res.language = (_meta(book, "language") or "en").split("-")[0].lower()

    toc = _toc_titles(book)
    ordinal = 0
    chap_ord = 0

    for idref, _linear in book.spine:
        item = book.get_item_with_id(idref)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if "nav" in (item.properties or []):
            continue
        soup = BeautifulSoup(item.get_content(), "lxml")
        body = soup.body or soup
        if body is None:
            continue

        note_bodies = {}
        blocks = []
        _collect_blocks(body, 0, blocks, note_bodies)

        chap_segments = []
        note_anchor = {}
        pending_title = ""

        for tag, quote_depth in blocks:
            seen = []
            html = _inline_html(tag, seen).strip()
            text = _plain(html)
            if not text:
                continue
            if tag.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                kind, level = "head", int(tag.name[1])
            elif quote_depth > 0:
                kind, level = "quote", 0
            else:
                kind, level = "para", 0
            ordinal += 1
            seg = Segment(ord=ordinal, kind=kind, level=level,
                          src_text=text, src_html=html)
            chap_segments.append(seg)
            for nid, mark in seen:
                note_anchor[nid] = (ordinal, mark)
            if kind == "head" and not pending_title:
                pending_title = text

        if not chap_segments and not note_bodies:
            continue

        chap_ord += 1
        href = item.get_name()
        heads = [seg.src_text for seg in chap_segments if seg.kind == "head"]
        title = chapter_title(toc.get(href), heads, res.title, chap_ord)
        res.chapters.append(Chapter(ord=chap_ord, title=title, href=href))
        for seg in chap_segments:
            seg.chapter = chap_ord
        res.segments.extend(chap_segments)

        # poznamky pod carou jako samostatne segmenty navazane na misto vyskytu
        for nid, tag in note_bodies.items():
            html = _inline_html(tag, []).strip()
            text = _plain(html)
            if not text:
                continue
            anchor_ord, mark = note_anchor.get(nid, (0, ""))
            ordinal += 1
            res.segments.append(Segment(
                ord=ordinal, chapter=chap_ord, kind="note", level=0,
                src_text=text, src_html=html,
                note_id=nid, note_ord=anchor_ord, note_txt=mark))

    return res


def segment_hash(seg):
    return _hash(seg.src_html)
