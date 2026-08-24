"""Vyrazeni tiskoveho baLastu ze spatne prevedenych knih.

EPUBy prevedene z PDF si casto nesou zivou zahlavi, cisla stranek, tiskova
razitka a nazvy souboru ze sazby jako bezne odstavce. Do prekladu nepatri
a v exportu jen prekazi.

Nic se nemaze. Segment dostane stav 'skipped', takze se neprekilada,
nekontroluje a nedostane se do exportu, ale zustava v databazi a da se
kdykoli vratit.
"""
import re
from collections import Counter

from . import projects

# nazev souboru ze sazby: b03 cash ch 3.pmd, kapitola.indd
SAZBA_RE = re.compile(r"^[\w \-]{0,40}\.(pmd|indd|qxd|qxp|doc|docx)\s*$", re.I)
# tiskove razitko: 10/8/2008, 4:10 PM
RAZITKO_RE = re.compile(
    r"^\d{1,2}[/.]\d{1,2}[/.]\d{2,4},?\s*\d{1,2}[:.]\d{2}(\s*[AP]M)?\s*$", re.I)
# odstavec slozeny jen z cislic a interpunkce
CISLO_RE = re.compile(r"^[\d\s.,:;\-–—()\[\]]+$")

OPAKOVANI = 5       # od kolika vyskytu je kratky odstavec zivym zahlavim
ZAHLAVI_DELKA = 60  # zive zahlavi byva kratke


def duvod(text, cetnost):
    """Proc odstavec neni text knihy. None znamena, ze je.

    Delka sama o sobe nerozhoduje. Kratke odstavce byvaji bunky tabulek nebo
    znacky seznamu (Per, Age, 65+, A, B, C) a ty do knihy patri. Vyrazuje se
    jen to, co nese jasnou stopu tisku: cislo stranky, razitko, nazev souboru
    ze sazby nebo text, ktery se opakuje na kazde strane.
    """
    t = (text or "").strip()
    if not t:
        return "prázdný"
    if RAZITKO_RE.match(t):
        return "tiskové razítko"
    if SAZBA_RE.match(t):
        return "název souboru ze sazby"
    if CISLO_RE.match(t):
        return "číslo stránky"
    return None


VEDLE_PODIL = 0.5   # tolik vyskytu musi stat vedle tiskoveho apparatu


def _ziva_zahlavi(segs, cetnost):
    """Ktere opakovane texty jsou zive zahlavi, a ktere patri do knihy.

    Rozhoduje sousedstvi, ne cetnost. Zive zahlavi stoji vedle cisla stranky,
    razitka nebo nazvu souboru ze sazby, protoze je to tataz sada z paty
    stranky. Oznaceni mluvciho v dialogu (Drew:) nebo pripsani citatu
    (—David Ogilvy) se opakuji taky, ale stoji uprostred textu.
    """
    jiste = {i for i, s in enumerate(segs) if duvod(s["src_text"], cetnost)}
    if not jiste:
        return set()

    kandidati = {t for t, n in cetnost.items()
                 if n >= OPAKOVANI and len((t or "").strip()) < ZAHLAVI_DELKA}
    pozice = {}
    for i, s in enumerate(segs):
        if s["src_text"] in kandidati:
            pozice.setdefault(s["src_text"], []).append(i)

    out = set()
    for text, misto in pozice.items():
        vedle = sum(1 for i in misto
                    if (i - 1) in jiste or (i + 1) in jiste)
        if vedle / len(misto) >= VEDLE_PODIL:
            out.add(text)
    return out


def _skupina(reason):
    """Duvody se v souhrnu slucuji, at nevznikne kategorie na kazdy pocet."""
    if reason.startswith("opakuje se"):
        return "opakované záhlaví nebo pata"
    return reason


def scan(slug):
    """Najde balast, ale nic nezmeni. Vraci prehled ke schvaleni."""
    con = projects.open_db(slug)
    if con is None:
        return None
    try:
        segs = [dict(r) for r in con.execute(
            "SELECT ord, kind, status, src_text FROM segment ORDER BY ord")]
    finally:
        con.close()

    cetnost = Counter(s["src_text"] for s in segs)
    zahlavi = _ziva_zahlavi(segs, cetnost)

    nalezy = []
    for s in segs:
        if s["status"] == "skipped":
            continue
        d = duvod(s["src_text"], cetnost)
        if not d and s["src_text"] in zahlavi:
            d = ("opakuje se " + str(cetnost[s["src_text"]]) +
                 "× vedle čísel stránek, živé záhlaví nebo pata")
        if d:
            nalezy.append({"ord": s["ord"], "reason": d,
                           "text": s["src_text"][:70]})
    souhrn = Counter(_skupina(n["reason"]) for n in nalezy)
    return {"total": len(segs), "found": len(nalezy),
            "summary": dict(souhrn), "items": nalezy}


def apply(slug):
    """Oznaci nalezeny balast jako vyrazeny. Vraci, kolik jich bylo."""
    found = scan(slug)
    if found is None:
        return None
    if not found["items"]:
        return {"skipped": 0, "summary": {}}
    con = projects.open_db(slug)
    if con is None:
        return None
    try:
        con.executemany(
            "UPDATE segment SET status = 'skipped', review_note = ?"
            " WHERE ord = ?",
            [(n["reason"], n["ord"]) for n in found["items"]])
        con.commit()
    finally:
        con.close()
    return {"skipped": len(found["items"]), "summary": found["summary"]}


def restore(slug):
    """Vrati vsechny vyrazene odstavce zpatky do hry."""
    con = projects.open_db(slug)
    if con is None:
        return None
    try:
        cur = con.execute(
            "UPDATE segment SET status = CASE WHEN tgt_text IS NOT NULL"
            " AND tgt_text != '' THEN 'done' ELSE 'pending' END,"
            " review_note = NULL WHERE status = 'skipped'")
        con.commit()
        return {"restored": cur.rowcount}
    finally:
        con.close()
