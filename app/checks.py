"""Kontroly kvality prelozeneho segmentu.

Nic se nepřepisuje. Kdyz kontrola nesedi, segment dostane stav 'review'
a uzivatel se na nej podiva sam.
"""
import re
import unicodedata

from bs4 import BeautifulSoup

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
EMPHASIS = ("em", "strong")

CZ_DIACRITICS = set("áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ")

# nejcastejsi anglicka slova; kdyz jich je v ceskem prekladu hodne,
# nejspis kus textu zustal nepreloženy
EN_COMMON = {
    "the", "and", "of", "to", "in", "that", "was", "with", "for", "he", "she",
    "it", "his", "her", "had", "have", "has", "been", "are", "is", "not", "but",
    "they", "from", "this", "which", "you", "all", "were", "when", "there",
    "can", "as", "at", "by", "on", "or", "an", "would", "their", "what", "so",
    "out", "if", "about", "who", "get", "him", "them", "then", "now", "its",
    "did", "no", "my", "we", "your", "one", "into", "than", "could", "will",
    "said", "over", "only", "very", "some", "just", "like", "these", "how",
    "back", "down", "after", "before", "where", "while", "any", "our", "more",
    "other", "such", "made", "make", "come", "came", "went", "know", "knew",
    "time", "man", "men", "day", "way", "old", "new", "long", "little",
}

# Ceska neohebna slova: predlozky, spojky, zajmena, castice. Uzavrena trida,
# da se vyjmenovat. Obecna podstatna jmena sem nepatri, na ta by byl potreba
# slovnik a seznam by se ladil na konkretni vety.
CZ_COMMON = {
    "do", "se", "si", "na", "ve", "ze", "ke", "po", "za", "od", "pro", "bez",
    "nad", "pod", "mezi", "kolem", "vedle", "podle", "proti", "pres", "krome",
    "je", "jsou", "jsem", "jste", "byl", "byla", "bylo", "byli", "byly",
    "ale", "nebo", "nez", "aby", "jako", "kdyz", "protoze", "aniz",
    "jak", "kde", "kdy", "kdo", "tak", "tam", "tady", "ted", "hned", "jeste",
    "ten", "ta", "ty", "toho", "tomu", "tim", "tech", "temi",
    "ona", "oni", "ono", "jeho", "jej", "jich", "jim", "nim", "nej",
    "mu", "ji", "mi", "ti", "vy", "nam", "vam", "nas", "vas",
    "ne", "ano", "nic", "vse", "jen", "pak", "uz", "sem",
}

# koncovky, na ktere se v cestine mluvnicke tvary lamou
CZ_ENDINGS = (
    "ost", "ové", "ová", "ých", "ími", "ách", "ého", "ému", "ovi", "ovat",
    "ají", "ují", "ete", "eme", "íte", "íme", "ala", "alo", "ali", "aly",
    "ila", "ilo", "ili", "ěla", "ělo", "ěli", "nou", "ním", "cím", "kem",
    "tem", "sti", "ci", "ce", "ky", "ka", "ku", "ám", "ách", "em", "ém",
    "ou", "mi", "ch", "ní", "ný", "ná", "né", "cí", "la", "lo", "li", "ly",
    "al", "il", "el", "ěl", "ám", "áš", "ím", "íš", "je", "je",
)


def strip_diacritics(text):
    norm = unicodedata.normalize("NFKD", text)
    return "".join(c for c in norm if not unicodedata.combining(c))


# ------------------------------------------------------ zbytky anglictiny

SENTENCE_END = set(".!?:;\"“”„'()[]—–")


def is_proper_name(text, match):
    """Slovo s velkym pismenem uprostred vety. Vlastni jmeno nenese doklad
    o jazyku: Sable Point nevypada cesky, protoze cesky vypadat nema."""
    word = match.group(0)
    if not word[:1].isupper():
        return False
    i = match.start() - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    if i < 0:
        return False                      # zacatek textu, muze byt bezne slovo
    return text[i] not in SENTENCE_END


def czech_ratio(text):
    """Podil slov, ktera vypadaji cesky. Vraci (podil, pocet hodnocenych slov).

    Vlastni jmena se do pomeru nepocitaji vubec.
    """
    czech = total = 0
    for match in WORD_RE.finditer(text):
        word = match.group(0)
        if len(word) < 2:
            continue
        if is_proper_name(text, match):
            continue
        total += 1
        low = word.lower()
        if any(ch in CZ_DIACRITICS for ch in word):
            czech += 1
        elif low in EN_COMMON:
            pass                          # slovo v obou seznamech bereme jako anglicke
        elif low in CZ_COMMON or low.endswith(CZ_ENDINGS):
            czech += 1
    if not total:
        return 1.0, 0
    return czech / total, total


def english_residue(text, threshold=0.5, min_words=6):
    """Vic nez polovina slov bez diakritiky i bez ceskych koncovek je podezrela.

    U kratkych useku se nekontroluje, tam by nahoda rozhodovala vic nez text.
    """
    ratio, count = czech_ratio(text)
    if count < min_words:
        return False, ratio
    return ratio < threshold, ratio


# ------------------------------------------------------------- slovnicek

VOWELS = set("aeiouy")


def stem(word):
    """Hrube zkraceni na kmen, aby sedely i sklonovane tvary.

    Koncove samohlasky jdou pryc: sklonovani je vetsinou meni. U kratkych
    jmen na tom zalezi nejvic, protoze z Eva by jinak zbylo cele slovo
    a tvary Evy, Eve nebo Evu by neprosly.
    """
    base = strip_diacritics(word).lower()
    bez_koncovky = base.rstrip("aeiouy")
    if len(bez_koncovky) >= 2:
        base = bez_koncovky
    if len(base) <= 3:
        return base
    return base[:max(3, len(base) - 2)]


def consonants(word):
    """Souhlaskova kostra slova.

    Cestina meni pri sklonovani samohlasku v kmeni: bůh a Bohu, sůl a soli,
    dům a domu. Porovnani kmene po pismenech tady selze, souhlasky ale
    zustavaji stejne, takze slouzi jako zaloha.
    """
    base = strip_diacritics(word).lower()
    return "".join(c for c in base if c not in VOWELS)


def _stem_hit(word, tgt_words):
    """Stoji v prekladu slovo, ktere zacina kmenem?

    Kmen se hleda na zacatku slova, ne kdekoli uvnitr. Kmen z Eva je "ev"
    a uvnitr slova nevím by prosel, ackoli se jmenem nema nic spolecneho.
    """
    base = stem(word)
    if not base:
        return False
    return any(strip_diacritics(w).lower().startswith(base) for w in tgt_words)


def _skeleton_hit(word, tgt_words):
    """Stoji v prekladu slovo se stejnou souhlaskovou kostrou?"""
    skeleton = consonants(word)
    if len(skeleton) < 2:
        return False
    return any(consonants(w).startswith(skeleton) for w in tgt_words)


def glossary_misses(src_text, tgt_text, entries):
    """Vyrazy, ktere ve zdroji stoji, ale v prekladu jim nic neodpovida."""
    if not entries or not tgt_text:
        return []
    tgt_flat = strip_diacritics(tgt_text).lower()
    tgt_words = WORD_RE.findall(tgt_text)
    misses = []
    for entry in entries:
        term_src = (entry.get("term_src") or "").strip()
        term_cs = (entry.get("term_cs") or "").strip()
        if not term_src or not term_cs:
            continue
        # cele slovo, ne kus jineho: Eve nesmi sedet uvnitr never nebo believe
        if not re.search(r"(?<!\w)" + re.escape(term_src) + r"(?!\w)",
                         src_text, re.IGNORECASE):
            continue
        # projde, kdyz v prekladu stoji kmen ceskeho tvaru nebo puvodni vyraz
        parts = [p for p in WORD_RE.findall(term_cs) if p]
        hit = all(_stem_hit(p, tgt_words) for p in parts) if parts else False
        if not hit:
            hit = any(strip_diacritics(w).lower() == strip_diacritics(term_src).lower()
                      for w in tgt_words)
        if not hit and parts:
            # zaloha na sklonovani, ktere meni samohlasku v kmeni
            hit = all(_skeleton_hit(p, tgt_words) for p in parts)
        if not hit:
            misses.append({"term_src": term_src, "term_cs": term_cs})
    return misses


# --------------------------------------------------------- znacky navic

def tag_counts(html):
    if not html:
        return {}
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for name in EMPHASIS:
        found = len(soup.find_all(name))
        if found:
            out[name] = found
    return out


def strip_added_markup(src_html, tgt_html):
    """Odstrani z prekladu zvyrazneni, ktere zdroj vubec nema.

    Kdyz ve zdrojovem odstavci nestoji jediny <strong>, nema ho mit ani
    preklad. Maze se pouze znacka, text uvnitr zustava beze zmeny.
    """
    if not tgt_html:
        return tgt_html
    src = tag_counts(src_html)
    tgt = tag_counts(tgt_html)
    nadbytecne = [n for n in EMPHASIS if tgt.get(n) and not src.get(n)]
    if not nadbytecne:
        return tgt_html
    soup = BeautifulSoup(tgt_html, "lxml")
    for name in nadbytecne:
        for tag in soup.find_all(name):
            tag.unwrap()
    node = soup.body or soup
    return node.decode_contents().strip()


def added_markup(src_html, tgt_html):
    """Zvyrazneni, ktere prekladu pribylo, ackoli ve zdroji nebylo.

    Model si obcas vymysli <strong> kolem slova, ktere sam pridal. Takovy
    segment stoji za kontrolu.
    """
    src = tag_counts(src_html)
    tgt = tag_counts(tgt_html)
    added = {}
    for name, count in tgt.items():
        extra = count - src.get(name, 0)
        if extra > 0:
            added[name] = extra
    return added


# ------------------------------------------------------------ dohromady

def inspect(segment, tgt_html, tgt_text, entries):
    """Vsechny kontrolky nad jednim segmentem. Vraci seznam vyhrad."""
    problems = []

    misses = glossary_misses(segment["src_text"], tgt_text, entries)
    if misses:
        seznam = ", ".join(m["term_src"] + " → " + m["term_cs"] for m in misses)
        problems.append({"kind": "glossary", "detail": seznam})

    suspicious, ratio = english_residue(tgt_text)
    if suspicious:
        problems.append({"kind": "english",
                         "detail": "česky vypadá jen " + str(round(ratio * 100)) +
                                   " % slov"})

    added = added_markup(segment["src_html"], tgt_html)
    if added:
        seznam = ", ".join("<" + k + "> " + str(v) + "×" for k, v in added.items())
        problems.append({"kind": "markup", "detail": "navíc " + seznam})

    return problems
