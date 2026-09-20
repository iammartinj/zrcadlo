# Zrcadlo

Desktopová aplikace pro překlad celých knih z angličtiny do češtiny **lokálním
jazykovým modelem**. Nic neodchází z počítače — žádný cloudový překladač, žádná
telemetrie, žádné fonty z CDN.

Načte EPUB, rozloží ho na odstavce, projde knihu nasucho a sestaví slovníček
vlastních jmen, pak překládá a průběžně kontroluje, co model vrátil. Výsledek
uloží jako českou knihu ve formátu EPUB.

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
podle jména v požadavku a při potřebě jiného modelu ho sám vymění.

**CORS nech vypnuté.** Zrcadlo se ptá z Pythonu, ne ze stránky v prohlížeči,
takže ho nepotřebuje.

Jestli server neběží nebo běží na jiné adrese, Zrcadlo to pozná při startu
a napíše konkrétně co s tím. Nehádej, přečti si to hlášení.

---

## 2. Instalace a spuštění

```
git clone https://github.com/iammartinj/zrcadlo.git
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
počítej s hodinou a půl. Slovníček sestavuje vždy *pomocný model* (viz
[Nastavení](#nastavení)), ne ten, kterým se překládá.

Slovníček se pak vkládá do promptu, ale jen ty položky, jejichž výraz se
v překládaném textu opravdu vyskytuje. Díky tomu se jména drží stejná napříč
celou knihou. V panelu jde každou položku opravit, přepnout jí kategorii nebo
rod, vyřadit ji, nebo ji zamknout. Klik na anglický výraz (nebo na číslo výskytů
na konci řádku) skočí na první výskyt v knize a podbarví samo slovo v obou
sloupcích, další klik posune na další. V překladu se hledá i ve skloňovaných
tvarech; když tam slovo není — protože ho model přeložil jinak, než říká
slovníček — podbarví se celý odstavec, ať je vidět, kam jsme skočili. Bere
i odstavce, které ještě nejsou přeložené, v levém sloupci je originál vidět
tak jako tak. Zamčenou položku model při novém sestavování
nepřepíše. U osob stojí za to vyplnit rod: TranslateGemma podle něj drží shodu
v minulém čase.

**Nastav stylovou kartu.** Vpravo nahoře *Nastavení*: registr, rod vypravěče,
tykání nebo vykání, přechylování ženských příjmení a poznámka volným textem.
Všechno jde do pokynu pro model.

**Přelož.** Tlačítko PŘELOŽIT KNIHU projede celou knihu od místa, kde se
skončilo. Zastavit se dá kdykoli — hotové odstavce zůstanou uložené a příště
naváže tam, kde přestal. Průběh vidíš v obou sloupcích a na svislém ukazateli.

**Pohybuj se po knize.** Šipky v patičce levého sloupce listují po kapitolách,
klávesy ← → dělají totéž. Na delší skok je vedle nich seznam všech kapitol
i s názvy. Klik na svislý ukazatel vlevo skočí na kapitolu, která na tom místě
knihy běží.

**Zkontroluj podezřelé odstavce.** Odstavec, který neprošel kontrolou, má
v pravém sloupci tenkou svislou linku. Po kliknutí uvidíš důvod a můžeš ho nechat
přeložit znovu. Co všechno se kontroluje, je [níže](#co-aplikace-hlídá-sama).

**Vyčisti tiskový balast.** Knihy převedené z PDF si často nesou živá záhlaví,
čísla stránek, tisková razítka a názvy souborů ze sazby jako běžné odstavce.
Tlačítko *Vyčistit tiskový balast* je najde a nabídne k vyřazení. Nic se nemaže
— odstavce zůstanou v databázi, jen se nebudou překládat ani exportovat, a jde
je kdykoli vrátit.

Rozlišuje se podle sousedství, ne podle četnosti: živé záhlaví stojí vedle čísla
stránky, kdežto označení mluvčího v dialogu nebo připsání citátu se opakuje
taky, ale uprostřed textu. Na jedné skutečné knize to našlo 969 odstavců z 3766
a nespletlo se ani u jednoho.

**Nebo začni znovu.** Tlačítko *Nový překlad* zahodí hotový překlad celé knihy
a vrátí ji na začátek — hodí se, když přepneš model. Slovníček ani stylová karta
se nemění, na sestavení slovníčku je vlastní tlačítko, a vyřazené odstavce
zůstanou vyřazené. Poslední stav zůstane v `project.db.zaloha` ve složce
projektu, další zahození ho přepíše.

**Uprav, co ti vadí.** Klikni na kterýkoli odstavec v pravém sloupci a piš
rovnou do něj. Kurzíva je Ctrl+I, tučné Ctrl+B, Enter uloží a Esc zruší.
Tlačítka pod odstavcem umí ještě nechat ho přeložit znovu nebo ho vyřadit
z knihy.

**Ulož překlad.** Tlačítko *Uložit překlad (EPUB)* vyrobí českou knihu se
zachovanou strukturou kapitol, kurzívou i poznámkami pod čarou. Názvy kapitol
v obsahu jsou přeložené. Soubory se ukládají do `projects/<název>/export/`
s časovým razítkem, takže starší verze nepřepisují.

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

Pořiďte si zvyk, který se vyplácí: přeložte s každým kandidátem **jednu
kapitolu** a porovnejte ji v okně, kde stojí originál a překlad vedle sebe.
Přepnutí modelu je otázka jednoho kliknutí, takže je to levnější než hádat.

Pozor ještě na jednu věc: **zavři před překladem programy, které berou paměť
grafické karty.**

### Gemma 3 — výchozí a pomocný model

`gemma-3-12b-it-qat` je trénovaný tak, aby snesl čtyřbitovou kvantizaci, takže
u něj Q4 neztrácí tolik jako jinde. Umí plnit úkoly, proto **sestavuje
slovníček** i tehdy, když překládáš jiným modelem. Nech ho staženého.

### TranslateGemma — model jen na překlad

`translategemma-12b-it` od Googlu je Gemma 3 doučená výhradně na překlad. Na
ukázce románu psala čistší češtinu než Gemma 3, s kontextem správně držela rod
postav a vykání a nedělala nesmyslná slova. Cenou je sklon zplošťovat obrazná
místa a vysvětlovat eliptické věty.

Nemá chat ani systémové pokyny, a proto s ní Zrcadlo mluví jinak: překládá **po
jednom odstavci**. K odstavci přidá tři předchozí přeložené odstavce z téže
kapitoly, pojmy ze slovníčku a stylovou kartu jako krátký pokyn. Jednotky nechává
jako v originále (stopy zůstanou stopami) a zvýraznění ze zdroje zachovává.

**1. Stáhni model.** V Discover vyhledej `mradermacher/translategemma-12b-it-GGUF`
a v rozbalovacím seznamu velikostí vyber **Q4_K_M (6,8 GB)**. Předvybraná položka
nemusí být ta správná.

**2. Přepiš mu šablonu, jinak se nenačte.** Jeho vlastní šablona odmítne běžnou
zprávu a server při startu spadne s hláškou *exited before becoming healthy*.
V LM Studiu otevři **My Models**, u `translategemma-12b-it` klikni na ozubené
kolo, sjeď do sekce **Advanced** a otevři **Chat Template**. Obsah pole celý
nahraď obsahem souboru
[`docs/translategemma-chat-template.jinja`](docs/translategemma-chat-template.jinja)
a ulož. Zrcadlo šablonu nepoužívá, posílá hotový text — jde jen o to, aby server
naběhl.

**3. Vyber ji v aplikaci.** Gemma 3 musí zůstat stažená kvůli slovníčku.
Na obě se nevejdou 11 GB najednou, ale LM Studio si je vymění samo, výměna trvá
kolem deseti sekund.

Nová jména, která se objeví až během překladu, TranslateGemma do slovníčku
nedoplňuje — neumí takový úkol splnit a přepínat model po každém odstavci by
bylo pomalejší než překlad. Když se v knize objeví nové postavy, sestav
slovníček znovu.

---

## Co aplikace hlídá sama

U Gemmy 3 musí sedět počet odstavců v každé dávce. Když ne, dávka se zopakuje
s důraznější instrukcí, a po druhém neúspěchu se odstavce překládají po jednom.

Když odpověď narazí na limit `max_tokens`, aplikace to řekne. U TranslateGemmy
dostane odstavec stav k revizi s poznámkou, že může být useknutý.

Zvýraznění, které si model přidal a zdroj ho nemá, se odstraní. Maže se jen
značka, text zůstává. TranslateGemma vrací zvýraznění jako `*hvězdičky*`, ty se
převedou zpátky na kurzívu a tučné písmo.

K revizi jde odstavec, ve kterém chybí výraz ze slovníčku, zůstala angličtina,
model si domyslel text (překlad je nejméně třikrát delší než zdroj), nebo přidal
„pan“ či „paní“ před jméno osoby ze slovníčku, ačkoli originál žádný titul nemá.
Stejně dopadne odstavec, který se modelu vůbec nechtělo překládat a vrátil ho
beze změny — typicky titulek verzálkami nebo krátká replika. Oddělovače,
čísla a samotná jména se tím nezdržují.

Uzavírací značku s escapovaným lomítkem (`<em>slovo<\/em>`, jak ji model zná
z JSONu) aplikace narovná, aby kurzíva nepřetekla přes zbytek odstavce.

U Gemmy 3 se nová vlastní jména, která se objeví až během překladu, průběžně
doplňují do slovníčku a od dalšího výskytu se používají.

Stav se ukládá po každé dávce nebo odstavci. Když aplikace spadne nebo ji
zavřeš, po dalším spuštění nabídne pokračování. Změna zdrojového EPUBu se pozná
podle otisku.

---

## Známá omezení

Tabulka se v exportu rozpadne na samostatné odstavce. Počet sedí, mřížka je pryč.

Záměrně prázdný odstavec (`<p>&nbsp;</p>` jako oddělovač scény) se vynechá,
protože nenese text k překladu.

Kontrola slovníčku porovnává kmeny, takže u výrazů, kde se v češtině mění
samohláska i souhlásky naráz, může označit k revizi i správný překlad.

Kontrola zbylé angličtiny se u krátkých odstavců, kde převažují vlastní jména,
může ozvat i u správného překladu.

Kvalita překladu je kvalita modelu. Aplikace hlídá strukturu, konzistenci jmen
a zjevné vady, ale jestli se text dobře čte, posoudí jen člověk.

---

## Nastavení

Všechno podstatné je v `config.json`:

```jsonc
{
  "lm_studio": {
    "base_url": "http://127.0.0.1:1234/v1",  // adresa serveru LM Studia
    "model": "gemma-3-12b-it-qat",           // model pro překlad, mění se i v aplikaci
    "helper_model": "gemma-3-12b-it-qat",    // model pro slovníček; prázdný = stejný jako model
    "context": 8192,
    "timeout_s": 900
  },
  "models": {
    // nastavení podle modelu: jak se s ním mluví a čím přepisuje "inference"
    "translategemma-12b-it": {
      "mode": "translategemma",
      "temperature": 0.1     // méně rozptylu mezi běhy, na ukázce bez ztráty kvality
    }
  },
  "inference": {
    "temperature": 0.3,      // nižší = věrnější, vyšší = volnější
    "top_p": 0.9,
    "repeat_penalty": 1.05,
    "seed": 606169,          // pevné, aby šly běhy porovnávat
    "max_tokens": 4096
  },
  "batching": {
    "target_source_tokens": 1200,  // jen Gemma 3; kratší dávka = méně chyb, ale pomalejší
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
  translate.py  překladový běh, dávky pro Gemmu 3, odstavce pro TranslateGemmu
  prompt.py     pokyny a formát pro oba druhy modelů
  checks.py     kontroly kvality
  export.py     výstup do EPUBu a Markdownu
  llm.py        rozhraní k LM Studiu
static/       rozhraní, vanilla JS bez build kroku
docs/         šablona pro TranslateGemmu do LM Studia
tests/        testy
projects/     projekty knih (do repozitáře nepatří)
```

Testy se spouštějí z kořene repozitáře:

```
.venv\Scripts\python.exe -m unittest discover -s tests
```

Obsah knih do repozitáře nepatří — složka `projects/` je v `.gitignore`.

---

## Licence

Font JetBrains Mono ve `static/fonts/` je pod licencí SIL Open Font License 1.1,
její znění je ve stejné složce.
