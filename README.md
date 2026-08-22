# Zrcadlo

Desktopová aplikace pro překlad celých knih z angličtiny do češtiny **lokálním
jazykovým modelem**. Nic neodchází z počítače — žádný cloudový překladač, žádná
telemetrie, žádné fonty z CDN.

Načte EPUB, rozloží ho na odstavce, projde knihu nasucho a sestaví slovníček
vlastních jmen, pak překládá po dávkách a průběžně kontroluje, co model vrátil.
Výsledek exportuje do tří podob: česká kniha, zrcadlová kniha s originálem
i překladem pod sebou, a prostý Markdown.

---

## Co potřebuješ

| | |
|---|---|
| Systém | Windows 10 nebo 11 |
| Python | 3.11 nebo novější (3.12 ověřeno) |
| GPU | NVIDIA s alespoň 8 GB, ideálně 12 GB a víc |
| Software | [LM Studio](https://lmstudio.ai) s načteným modelem |

Bez grafické karty to poběží taky, ale na procesoru bude překlad knihy trvat
dny místo hodin.

---

## 1. Nastavení LM Studia

Zrcadlo si samo model nespouští, mluví s LM Studiem po HTTP. Musíš mu proto
otevřít dveře.

**Stáhni model.** V LM Studiu záložka **Discover**, najdi model a stáhni.
Doporučení podle velikosti karty je [níže](#jaký-model-zvolit).

**Zapni server.** Tohle je krok, na kterém to nejčastěji vázne. V LM Studiu jdi
do nastavení, sekce **Local Models → Local Model API**, a přepni **Local API
server** ze stavu *Stopped* do zapnutého. V políčku **Base URL** se pak objeví
adresa, obvykle `http://127.0.0.1:1234/v1`. Přes **Edit port** se dá port změnit.

> Ve starších verzích LM Studia (řada 0.3) je totéž pod záložkou **Developer**,
> tlačítko **Start Server**.

**Nech zapnuté Just-in-time model loading.** Server si pak model natáhne sám
podle jména v požadavku.

**CORS nech vypnuté.** Zrcadlo se ptá z Pythonu, ne ze stránky v prohlížeči,
takže ho nepotřebuje.

Jestli server neběží nebo běží na jiné adrese, Zrcadlo to pozná při startu
a napíše konkrétně co s tím. Nehádej, přečti si to hlášení.

---

## 2. Instalace a spuštění

```
git clone https://github.com/<uživatel>/zrcadlo.git
cd zrcadlo
run.bat
```

`run.bat` si při prvním spuštění sám založí virtuální prostředí, doinstaluje
balíky a otevře okno aplikace. Podruhé už jen spustí.

Adresu serveru a port aplikace najdeš v `config.json`, měnit je nemusíš.

---

## 3. Jak se to používá

**Načti knihu.** Přetáhni `.epub` do prostředního sloupce, nebo klikni a vyber.
Kniha se rozloží na kapitoly a odstavce a založí se projekt ve složce
`projects/<název>/`. Původní soubor zůstane nedotčený, Zrcadlo si dělá kopii.

**Sestav slovníček.** V bloku SLOVNÍČEK klikni na *Otevřít* a pak *Sestavit
slovníček*. Aplikace projde knihu nasucho, nasbírá vlastní jména a opakující se
pojmy, zahodí všechno s méně než dvěma výskyty a u zbytku nechá model navrhnout
český tvar, rod a odůvodnění. **Tohle trvá dlouho** — na knize o 1400 odstavcích
počítej s hodinou a půl.

Slovníček se pak vkládá do promptu každé dávky, ale jen ty položky, jejichž
výraz se v dané dávce opravdu vyskytuje. Díky tomu se jména drží stejná napříč
celou knihou. V panelu jde každou položku opravit, přepnout jí kategorii nebo
rod, vyřadit ji, nebo ji zamknout. Zamčenou položku model při novém sestavování
nepřepíše.

**Nastav stylovou kartu.** Vpravo nahoře *Nastavení*: registr, rod vypravěče,
tykání nebo vykání, přechylování ženských příjmení a poznámka volným textem.
Všechno jde do systémového promptu každé dávky.

**Přelož.** Tlačítko PŘELOŽIT KNIHU projede celou knihu od místa, kde se
skončilo. Zastavit se dá kdykoli — hotové odstavce zůstanou uložené a příště
naváže tam, kde přestal. Průběh vidíš v obou sloupcích a na svislém ukazateli.

**Zkontroluj podezřelé odstavce.** Odstavec, který neprošel kontrolou, má
v pravém sloupci tenkou svislou linku. Po kliknutí uvidíš důvod a můžeš ho nechat
přeložit znovu. Kontroluje se, jestli sedí počet odstavců, jestli se v překladu
objevily výrazy ze slovníčku, jestli nezůstala angličtina a jestli si model
nepřidal zvýraznění, které originál nemá.

**Exportuj.** Tlačítko *Uložit překlad* nabídne tři formáty. Soubory se ukládají
do `projects/<název>/export/` s časovým razítkem, takže starší verze nepřepisují.

---

## Jaký model zvolit

Model se vybírá **přímo v aplikaci** — rozbalovací seznam vlevo nahoře nabízí
to, co má LM Studio k dispozici. Vkládací modely se vynechávají. Volba se uloží
do `config.json`, restart není potřeba.

Rozhoduje velikost paměti na kartě. Model se musí vejít celý, jinak část vrstev
počítá procesor a rychlost spadne na polovinu i míň.

| VRAM | Co se vejde | Poznámka |
|---|---|---|
| 8 GB | modely kolem 7–8 miliard parametrů v Q4 | 14B se nevejde ani v Q4 |
| 11–12 GB | 12B až 14B v Q4, kontext 8192 | těsné, hlídej volnou paměť |
| 16 GB a víc | 14B v Q5 nebo Q6 | pohodlné |

**RTX 3060 existuje ve verzi 8 GB i 12 GB.** Zjisti si kterou máš, rozhoduje to.
V LM Studiu se velikost karty ukazuje při načítání modelu.

Ke konkrétním modelům: `gemma-3-12b-it-qat` je trénovaný tak, aby snesl
čtyřbitovou kvantizaci, takže u něj Q4 neztrácí tolik jako jinde. Modely řady
Qwen2.5 kolem 14B jsou další rozumný kandidát. **Který z nich je lepší na
českou beletrii, se od stolu říct nedá** — záleží na knize i na tom, co od
překladu čekáš.

Pořiďte si proto zvyk, který se vyplácí: přeložte s každým kandidátem **jednu
kapitolu**, otevřete zrcadlový export a porovnejte. Přepnutí modelu je otázka
jednoho kliknutí, takže je to levnější než hádat.

Pozor ještě na jednu věc: **zavři před překladem programy, které berou paměť
grafické karty.** Při vývoji se ukázalo, že běžící 3ds Max srazil rychlost
z 16 na 7 tokenů za sekundu.

---

## Co aplikace hlídá sama

Počet odstavců v každé dávce musí sedět. Když ne, dávka se zopakuje s důraznější
instrukcí, a po druhém neúspěchu se odstavce překládají po jednom.

Zvýraznění, které si model přidal a zdroj ho nemá, se odstraní. Maže se jen
značka, text zůstává.

Nová vlastní jména, která se objeví až během překladu, se průběžně doplňují do
slovníčku a od dalšího výskytu se používají.

Stav se ukládá po každé dávce. Když aplikace spadne nebo ji zavřeš, po dalším
spuštění nabídne pokračování. Změna zdrojového EPUBu se pozná podle otisku.

---

## Známá omezení

Tabulka se v exportu rozpadne na samostatné odstavce. Počet sedí, mřížka je pryč.

Záměrně prázdný odstavec (`<p>&nbsp;</p>` jako oddělovač scény) se vynechá,
protože nenese text k překladu.

Kontrola slovníčku porovnává kmeny, takže u výrazů, kde se v češtině mění
samohláska i souhlásky naráz, může označit k revizi i správný překlad.

Kvalita překladu je kvalita modelu. Aplikace hlídá strukturu, konzistenci jmen
a zjevné vady, ale jestli se text dobře čte, posoudí jen člověk.

---

## Nastavení

Všechno podstatné je v `config.json`:

```jsonc
{
  "lm_studio": {
    "base_url": "http://127.0.0.1:1234/v1",  // adresa serveru LM Studia
    "model": "gemma-3-12b-it-qat",           // mění se i v aplikaci
    "context": 8192,
    "timeout_s": 900
  },
  "inference": {
    "temperature": 0.3,      // nižší = věrnější, vyšší = volnější
    "top_p": 0.9,
    "repeat_penalty": 1.05,
    "seed": 606169,          // pevné, aby šly běhy porovnávat
    "max_tokens": 4096
  },
  "batching": {
    "target_source_tokens": 1200,  // kratší dávka = méně chyb, ale pomalejší
    "chars_per_token": 4.0,
    "max_segments": 25
  },
  "glossary": {
    "chunk_source_tokens": 2500,
    "terms_per_request": 15,
    "min_occurrences": 2     // co je v knize míň než dvakrát, vypadne
  }
}
```

---

## Struktura

```
app/          backend v Pythonu
  epubin.py     rozklad EPUBu na kapitoly a odstavce
  glossary.py   sběr slovníčku
  translate.py  překladový běh, dávkování
  checks.py     kontroly kvality
  export.py     výstup do EPUBu a Markdownu
  llm.py        rozhraní k LM Studiu
static/       rozhraní, vanilla JS bez build kroku
projects/     projekty knih (do repozitáře nepatří)
```

Obsah knih do repozitáře nepatří — složka `projects/` je v `.gitignore`.

---

## Licence

Font JetBrains Mono ve `static/fonts/` je pod licencí SIL Open Font License 1.1,
její znění je ve stejné složce.
