# Zrcadlo — zadání projektu

Desktopová aplikace pro překlad celých knih z angličtiny do češtiny lokálním jazykovým modelem. Nic neodchází na internet.

## Prostředí

- Windows, pracovní adresář `D:\AI\translator`
- Model: `C:\Users\User\.lmstudio\models\google\gemma-3-12b-it-qat\gemma-3-12b-it-q4_0.gguf`
- GPU: RTX 2080 Ti, 11 GB VRAM, PCIe 3.0. Model se vejde celý na kartu, kontext 8192.
- Předloha rozhraní: `ui-reference.html` v kořeni projektu. Vizuál, rozvržení i typografii z ní převezmi beze změny, doplňuješ jen chybějící funkční části.

## Stack

- Backend: Python 3.11, FastAPI, uvicorn. Spouštěč `run.bat` v kořeni.
- Frontend: statické HTML a vanilla JS podle předlohy. Žádný build krok, žádný framework.
- Databáze: SQLite přes `sqlite3` ze standardní knihovny.
- Inference: LM Studio v režimu serveru, endpoint kompatibilní s OpenAI na `http://localhost:1234/v1/chat/completions`. Adresu i jméno modelu drž v `config.json`, ne v kódu.
- Parsování knih: `ebooklib` + `beautifulsoup4`. Export: `ebooklib`.

Pokud LM Studio neběží, aplikace to při startu pozná a v rozhraní to napíše konkrétně, včetně toho, co má uživatel udělat. Nezkoušej model spouštět sám.

## Datový model

Jedna kniha = jeden projekt = jedna složka v `D:\AI\translator\projects\<slug>\` s `project.db` a kopií zdrojového EPUBu.

Tabulky:

- `book` — název, autor, cesta ke zdroji, jazyk zdroje, jazyk cíle, stylová karta, čas vytvoření
- `segment` — id, pořadí, kapitola, typ (nadpis, odstavec, citace, poznámka), zdrojový text, přeložený text, stav (`pending`, `done`, `failed`, `review`), hash zdroje, počet pokusů
- `glossary` — id, zdrojový výraz, český tvar v prvním pádě, kategorie (osoba, místo, organizace, pojem), rod, poznámka k překladu, počet výskytů, kapitola prvního výskytu, `locked` (uživatel potvrdil, model to nesmí měnit)
- `run` — historie běhů, časy, rychlost, chyby

Segmentem je vždy celý odstavec. Odstavec nikdy nedělíš.

## Průběh práce

### 1. Načtení knihy

EPUB se rozloží na kapitoly a odstavce. Zachovej pořadí, úroveň nadpisů a kurzívu. Poznámky pod čarou drž jako samostatné segmenty navázané na místo výskytu.

### 2. Sestavení slovníčku

Před vlastním překladem projdi knihu jednou nasucho a nasbírej vlastní jména a opakující se pojmy. Postup po kapitolách: modelu pošli text kapitoly a nech ho vypsat vlastní jména osob, míst a organizací plus výrazy, které vypadají jako autorská terminologie. Odpověď vyžaduj jako JSON.

Výsledky slož dohromady, spočítej výskyty a zahoď vše, co se v knize objeví méně než dvakrát. U zbytku nech model navrhnout český tvar. Tady zadej výslovně:

- ženská příjmení se přechylují jen tehdy, zvolí-li to uživatel v nastavení projektu (výchozí stav: nepřechylovat)
- u každého jména urči rod, protože čeština ho potřebuje pro shodu v minulém čase
- zeměpisná jména s vžitou českou podobou převeď (London → Londýn), ostatní nech v originále
- ke každé položce ulož jednu větu odůvodnění, ať uživatel při kontrole ví, proč to tak je

Slovníček se pak ukáže uživateli ke schválení. Co potvrdí, dostane `locked = 1`.

### 3. Překlad

Segmenty se skládají do dávek po zhruba 1 200 tokenech zdroje, hranicí dávky je vždy konec odstavce. Do systémového promptu každé dávky vlož:

- stylovou kartu projektu
- jen ty položky slovníčku, jejichž zdrojový výraz se v dané dávce skutečně vyskytuje
- instrukci, že jména ze slovníčku se skloňují podle českého kontextu, uvedený tvar je první pád

Parametry: `temperature` 0.3, `top_p` 0.9, `repeat_penalty` 1.05, pevný `seed`. Vše v `config.json`.

Po každé dávce zkontroluj:

- **Počet odstavců** ve vstupu a výstupu musí souhlasit. Když ne, opakuj dávku s důraznější instrukcí. Po druhém neúspěchu překládej odstavce jednotlivě.
- **Slovníček**: pokud se zdrojový výraz v dávce vyskytl, musí se v překladu objevit odpovídající kmen. Když ne, označ segment jako `review`.
- **Zbytky angličtiny**: dávka, kde přes polovinu slov nemá diakritiku ani české koncovky, je podezřelá. Označ jako `review`, nepřepisuj.

Nová vlastní jména, která se objeví až během překladu, doplň do slovníčku průběžně a použij je od dalšího výskytu dál.

### 4. Odolnost

Stav se ukládá po každé dávce. Když aplikace spadne nebo ji uživatel zavře, po dalším spuštění nabídne pokračování od posledního hotového segmentu. Změna zdrojového souboru se pozná podle hashe.

### 5. Export

- EPUB jen s překladem, se zachovanou strukturou kapitol
- zrcadlový EPUB, kde po každém odstavci originálu následuje odstavec překladu
- prostý Markdown

## Rozhraní

Vycházej z `ui-reference.html`. Doplň do prostředního sloupce čtvrtý blok pod volbu jazyka:

**SLOVNÍČEK** — počet položek, počet nepotvrzených, tlačítko pro otevření. Panel slovníčku ať překryje pravý sloupec: tabulka se zdrojovým výrazem, českým tvarem, kategorií, rodem, počtem výskytů a zámkem. Editace přímo v buňce. Změna potvrzené položky nabídne přepsání už hotových segmentů, které ji obsahují.

Dál:

- Svislý ukazatel průběhu napojený na skutečná data, tmavší dílky na hranicích kapitol.
- Levý a pravý sloupec scrollují svázaně, zarovnané po odstavcích.
- Segment ve stavu `review` má v pravém sloupci tenkou svislou linku u levého okraje a po kliknutí nabídne přeložit znovu.
- Ve stavovém řádku dole skutečné využití VRAM, když ho jde přečíst z `nvidia-smi`, jinak řádek vynech. Nevymýšlej čísla.
- Stylová karta projektu jako dialog: register (neutrální, hovorový, archaizující), rod vypravěče, tykání nebo vykání mezi postavami, poznámka volným textem. Tyhle údaje jdou do systémového promptu každé dávky.

## Milníky

1. Načtení EPUBu, rozklad na segmenty, uložení do SQLite, výpis do levého sloupce.
2. Spojení s LM Studiem, překlad jedné kapitoly, streamování průběhu do rozhraní.
3. Slovníček: sběr, schvalování, vkládání do promptu.
4. Kontroly kvality, stav `review`, opakování dávky.
5. Export do všech tří formátů.
6. Pokračování po pádu, historie běhů.

Po každém milníku se zastav a ukaž, co je hotové.

## Hranice

- Žádné volání ven z počítače. Ani telemetrie, ani kontrola aktualizací, ani fonty z CDN. Font JetBrains Mono stáhni jednou do `static/fonts/` a linkuj lokálně.
- Žádný cloudový překladač jako záloha.
- Do repozitáře nepatří obsah knih. `projects/` dej do `.gitignore`.
- Nepiš vlastní inferenční vrstvu, mluv s LM Studiem přes HTTP.

## Kontrola hotového

Na knize o zhruba tisíci odstavcích musí platit: počet odstavců v exportu sedí s originálem, jména postav jsou v celé knize stejná, přerušení a spuštění pokračuje bez ztráty práce a bez opakovaného překladu už hotových částí.
