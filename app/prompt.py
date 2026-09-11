"""Sestaveni promptu pro davku a prevod odpovedi zpatky na segmenty.

Odstavce se modelu posilaji ocislovane znackou [[n]] na samostatnem radku.
Odkazy na poznamky pod carou se nahradi znackou {{n}}, kterou model jen
prepise, a po prekladu se vrati zpatky.
"""
import re

from bs4 import BeautifulSoup

from . import epubin

# Znacka smi mit na radku za sebou smeti: model obcas neco pripise a odstavec
# by se jinak ztratil. Text odstavce zacina az na dalsim radku.
MARK_RE = re.compile(r"^[ \t]*\[\[[ \t]*(\d+)[ \t]*\]\][^\n]*$", re.MULTILINE)
# Odkaz na poznamku se modelu posila bez obalu <sup>, at nema co zkomolit
SUP_NOTEREF_RE = re.compile(
    r'<sup>\s*<a class="noteref" data-note="([^"]*)">(.*?)</a>\s*</sup>')
NOTEREF_RE = re.compile(r'<a class="noteref" data-note="([^"]*)">(.*?)</a>')
TOKEN_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")

REGISTER = {
    "neutralni": "neutrální, spisovný",
    "hovorovy": "hovorový, blízký mluvené češtině",
    "archaizujici": "archaizující, s nádechem starší literatury",
}
NARRATOR = {
    "muz": "Vypravěč je muž, v minulém čase o něm piš v mužském rodě.",
    "zena": "Vypravěčka je žena, v minulém čase o ní piš v ženském rodě.",
    "neurceno": "",
}
ADDRESS = {
    "tykani": "Postavy si mezi sebou tykají.",
    "vykani": "Postavy si mezi sebou vykají.",
    "neurceno": "",
}

KIND_HINT = {
    "head": "nadpis kapitoly nebo oddílu",
    "quote": "citace nebo motto",
    "note": "poznámka pod čarou",
}


def strip_refs(html, refs):
    """Odkazy na poznamky nahradi znackou {{n}} a schova je do refs.

    Obal <sup> jde pryc spolu s odkazem. Model tak vidi jen {{n}} a nema
    prilezitost rozbit znacku, coz se pri zkousce stalo.
    """
    def sub(m, wrapped):
        refs.append((m.group(1), m.group(2), wrapped))
        return "{{" + str(len(refs)) + "}}"

    html = SUP_NOTEREF_RE.sub(lambda m: sub(m, True), html)
    return NOTEREF_RE.sub(lambda m: sub(m, False), html)


def restore_refs(text, refs):
    """Vrati znacky {{n}} zpatky na odkazy na poznamky i s puvodnim obalem."""
    def sub(m):
        i = int(m.group(1))
        if not 1 <= i <= len(refs):
            return ""
        note_id, mark, wrapped = refs[i - 1]
        anchor = '<a class="noteref" data-note="' + note_id + '">' + mark + "</a>"
        return "<sup>" + anchor + "</sup>" if wrapped else anchor
    return TOKEN_RE.sub(sub, text)


def sanitize(text):
    """Z odpovedi modelu nechá jen kurzívu a příbuzné značky, zbytek zahodí."""
    soup = BeautifulSoup(text, "lxml")
    node = soup.body or soup
    return epubin._inline_html(node, []).strip()


def plain(html):
    return epubin.WS.sub(" ", BeautifulSoup(html, "lxml").get_text()).strip()


def style_lines(book):
    """Stylova karta projektu jako radky do systemoveho promptu."""
    out = []
    reg = REGISTER.get(book["style_register"], REGISTER["neutralni"])
    out.append("Registr: " + reg + ".")
    nar = NARRATOR.get(book["style_narrator"], "")
    if nar:
        out.append(nar)
    adr = ADDRESS.get(book["style_address"], "")
    if adr:
        out.append(adr)
    if book["feminize_surnames"]:
        out.append("Ženská příjmení přechyluj podle české zvyklosti (Smith → Smithová).")
    else:
        out.append("Ženská příjmení nepřechyluj, nech je v původním tvaru.")
    note = (book["style_note"] or "").strip()
    if note:
        out.append("Poznámka od zadavatele: " + note)
    return out


def glossary_lines(entries):
    """Polozky slovnicku, ktere se v davce opravdu vyskytly."""
    if not entries:
        return []
    out = ["SLOVNÍČEK", "Tyhle výrazy překládej přesně takto. Uvedený český tvar je"
           " první pád, v textu ho skloňuj podle věty."]
    for e in entries:
        bits = [e["term_src"] + " → " + e["term_cs"]]
        if e["gender"]:
            bits.append({"m": "rod mužský", "f": "rod ženský",
                         "n": "rod střední"}.get(e["gender"], ""))
        if e["category"]:
            bits.append(e["category"])
        out.append("- " + ", ".join(b for b in bits if b))
    return out


def system_prompt(book, glossary=None, strict=False):
    src = "angličtiny" if book["source_lang"] == "en" else book["source_lang"]
    tgt = "češtiny" if book["target_lang"] == "cs" else book["target_lang"]
    parts = [
        "Jsi zkušený překladatel krásné literatury z " + src + " do " + tgt + ".",
        "Překládáš souvislou knihu, ne jednotlivé věty. Drž se autorova rytmu"
        " a obrazů, ale piš přirozenou češtinou, ne doslovným překladem.",
        "",
        "STYL",
    ]
    parts += style_lines(book)
    gl = glossary_lines(glossary)
    if gl:
        parts += [""] + gl
    parts += [
        "",
        "FORMÁT",
        "Vstup je rozdělený na očíslované odstavce. Každý začíná značkou [[n]]"
        " na samostatném řádku.",
        "Vrať překlad ve stejném pořadí a se stejnými značkami [[n]].",
        "Na řádku se značkou nesmí být nic jiného než ta značka."
        " Překlad odstavce začíná až na dalším řádku.",
        "Počet odstavců na výstupu se musí přesně rovnat počtu na vstupu."
        " Odstavce nespojuj ani nerozděluj.",
        "Nepiš žádný úvod, shrnutí ani vysvětlení, jen očíslované odstavce.",
        "Přelož přesně to, co na vstupu stojí, a nic navíc. Odstavec může být"
        " jen štítek, samotné číslo nebo věta utržená uprostřed; i tak ho"
        " přelož tak, jak je, a nedoplňuj chybějící část. Když není co"
        " překládat, opiš vstup beze změny. Nikdy si nedomýšlej vlastní text.",
        "Značky {{1}}, {{2}} jsou odkazy na poznámky pod čarou. Přepiš je beze"
        " změny na odpovídající místo v české větě.",
        "Značky <em> a <strong> smíš použít jen tam, kde stojí i ve zdrojovém"
        " odstavci, a to na odpovídajícím místě. Zvýraznění nikdy nepřidávej,"
        " ani u vlastních jmen. Když zdrojový odstavec žádnou značku nemá,"
        " nesmí ji mít ani překlad. Jiné značky nepoužívej.",
        "Vlastní jména osob skloňuj podle českého kontextu.",
    ]
    if strict:
        parts += [
            "",
            "DŮRAZ",
            "Předchozí pokus vrátil jiný počet odstavců, než kolik jich bylo na"
            " vstupu. Zkontroluj si výstup: každá značka [[n]] ze vstupu musí být"
            " i na výstupu, právě jednou, ve stejném pořadí.",
        ]
    return "\n".join(parts)


def user_message(batch, refs):
    """Davka segmentu jako ocislovany vstup pro model.

    Poznamka o druhu odstavce stoji pred blokem, ne na radku se znackou.
    Kdyz stala tam, model ji opisoval a znacka prestala byt rozpoznatelna.
    """
    notes = []
    for i, seg in enumerate(batch, 1):
        hint = KIND_HINT.get(seg["kind"])
        if hint:
            notes.append("Odstavec " + str(i) + " je " + hint + ".")
    lines = []
    if notes:
        lines.append(" ".join(notes))
        lines.append("")
    for i, seg in enumerate(batch, 1):
        lines.append("[[" + str(i) + "]]")
        lines.append(strip_refs(seg["src_html"], refs))
    return "\n".join(lines)


def split_marked(text):
    """Rozdeli odpoved modelu na {cislo: text} podle znacek [[n]]."""
    out = {}
    marks = list(MARK_RE.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip()
        if body:
            out[int(m.group(1))] = body
    return out


# ------------------------------------------------------ TranslateGemma

# TranslateGemma nema systemovou roli ani volny chat. Ucila se na jednom pevnem
# zadani, ktere se tu sklada rucne a posila pres /v1/completions. Zneni sedi
# znak po znaku s chatovou sablonou modelu. <bos> pridava server sam.
TG_HEAD = ("<start_of_turn>user\nYou are a professional English (en) to Czech (cs) "
           "translator. Your goal is to accurately convey the meaning and nuances of the "
           "original English text while adhering to Czech grammar, vocabulary, and "
           "cultural sensitivities.\n")
TG_TAIL = ("Produce only the Czech translation, without any additional explanations or "
           "commentary. Please translate the following English text into Czech:\n\n\n")
TG_STOP = ("<end_of_turn>",)
TG_HISTORY = 3

TG_REGISTER = {
    "neutralni": "neutral, standard literary Czech",
    "hovorovy": "colloquial Czech close to spoken language",
    "archaizujici": "slightly archaic Czech in the manner of older literature",
}
TG_NARRATOR = {
    "muz": "The narrator is a man; use masculine forms for him in the past tense.",
    "zena": "The narrator is a woman; use feminine forms for her in the past tense.",
}
TG_ADDRESS = {
    "tykani": "The characters address each other informally (in Czech they use tykání).",
    "vykani": "The characters address each other formally (in Czech they use vykání).",
}
TG_KIND = {
    "head": "The text is a chapter or section heading; do not add a full stop.",
    "quote": "The text is a quotation or an epigraph.",
    "note": "The text is a footnote.",
}
TG_GENDER = {"m": "a man", "f": "a woman"}

MD_STRONG_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
MD_EM_RE = re.compile(r"(?<![*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![*\w])")


def tg_turn(text, notes=""):
    return TG_HEAD + notes + TG_TAIL + text.strip() + "<end_of_turn>\n"


def tg_answer(text):
    return "<start_of_turn>model\n" + text.strip() + "<end_of_turn>\n"


def tg_context(html):
    """Predchozi odstavec do kontextu, bez odkazu na poznamky pod carou."""
    return NOTEREF_RE.sub("", SUP_NOTEREF_RE.sub("", html or "")).strip()


def tg_notes(book, glossary=None, kind=None, refs=False):
    """Stylova karta a postavy ze slovnicku jako jedna radka pokynu.

    Na ukazce romanu model pokyn v tomhle tvaru poslechl a do prekladu ho
    neopsal. Rod postav resi shodu, kterou model bez kontextu hada.
    """
    lines = ["Register: " + TG_REGISTER.get(book["style_register"],
                                            TG_REGISTER["neutralni"]) + "."]
    for mapping, key in ((TG_NARRATOR, "style_narrator"), (TG_ADDRESS, "style_address")):
        line = mapping.get(book[key])
        if line:
            lines.append(line)
    if book["feminize_surnames"]:
        lines.append("Feminize women's surnames the Czech way (Smith → Smithová).")
    else:
        lines.append("Do not feminize women's surnames; keep them as in the original.")
    for e in glossary or []:
        who = TG_GENDER.get(e.get("gender") or "")
        if e.get("category") == "osoba" and who:
            lines.append(e["term_cs"] + " is " + who + ".")
    lines += [
        "Do not put \"pan\" or \"paní\" before a name unless the original has"
        " Mr., Mrs., Ms. or Miss.",
        "Keep units as in the original (feet stay feet, miles stay miles);"
        " do not convert numbers.",
        "Keep the <em> and <strong> tags from the source on the corresponding words.",
    ]
    if refs:
        lines.append("Keep markers such as {{1}} unchanged at the corresponding place.")
    hint = TG_KIND.get(kind)
    if hint:
        lines.append(hint)
    note = (book["style_note"] or "").strip()
    if note:
        lines.append("Translator's note: " + note)
    return "Notes: " + " ".join(lines) + "\n"


def tg_prompt(book, seg, source, history=(), glossary=None):
    """Cely prompt pro jeden odstavec.

    Pojmy ze slovnicku a predchozi prelozene odstavce jdou jako drivejsi kola
    rozhovoru ve formatu, na kterem se model ucil. Nemusi je poslouchat, jen
    navazuje na to, co uz "sam" prelozil. Pokyn stoji jen v poslednim kole.

    source je zdroj odstavce s odkazy uz nahrazenymi znackou {{n}},
    history jsou dvojice (zdroj, preklad) od nejstarsiho.
    """
    parts = [tg_turn(e["term_src"]) + tg_answer(e["term_cs"]) for e in glossary or []]
    parts += [tg_turn(src) + tg_answer(tgt) for src, tgt in history]
    notes = tg_notes(book, glossary, seg["kind"], refs="{{" in source)
    return "".join(parts) + tg_turn(source, notes) + "<start_of_turn>model\n"


def markdown_emphasis(text):
    """Hvezdickove zvyrazneni z Markdownu zpatky na <strong> a <em>.

    TranslateGemma znacky ze zdroje nevraci jako HTML, ale jako *...*.
    Zvyrazneni, ktere zdroj nema, pak stejne odstrani strip_added_markup.
    """
    text = MD_STRONG_RE.sub(r"<strong>\1</strong>", text)
    return MD_EM_RE.sub(r"<em>\1</em>", text)
