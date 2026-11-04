# Přehled změn

## 0.4.28 – 2026-11-04

### Čeština

- Návody nyní doporučují instalaci vyhledáním integrace přímo v HACS; vlastní repozitář zůstává záložní možností.
- Dokumentace včetně přehledu změn je česky. Příprava vydání podporuje české poznámky a zachovává čtení starších dvojjazyčných poznámek.
- Ruční instalace a struktura instalačního ZIPu zůstávají zachovány. Funkční logika integrace se nemění.

## 0.4.27 – 2026-10-24

### Čeština

- Opravena volba testovacích závislostí: CI nejprve testuje přesně nejnovější stabilní Home Assistant a poté přijme pouze testovací plugin s přesnou závislostí na stejné verzi HA.
- Zabráněno tichému návratu pipu na historický plugin 0.2.1, který způsoboval chyby importu integrace. Při chybějícím odpovídajícím pluginu nyní validace i příprava vydání uvádějí konkrétní příčinu a zůstávají neúspěšné.
- Doplněny regresní testy výběru závislostí a dokumentace CI.
- Omezení ověření při vydání: nejnovější HA je 2026.10.3, ale dostupný plugin 0.13.373 vyžaduje HA 2026.10.2. Úplné testy s fixture na HA 2026.10.3 proto zůstávají neověřené; toto vydání nedeklaruje úspěšnou úplnou validaci na této verzi.
- Změna se týká CI a dokumentace; provozní logika integrace zůstává ve stavu verze 0.4.26.

## 0.4.26 – 2026-10-20

### Čeština

- Opravena reautentizace: nové platné heslo se uloží do původní konfigurace a Home Assistant integraci znovu načte místo chyby „již nastaveno“.
- Obnovení přihlášení ověřuje stejný účet a odběrné místo. Zachovává nastavení, další uložené položky, identitu konfigurace i stávající entity; ostatní odběrná místa nemění.
- Doplněny funkční testy instalace, chyb přihlášení a spojení, obnovy uložené konfigurace a zachování registrů při reloadu. CI je spouští na HA 2026.8.0, 2026.9.0, 2026.10.2 a nejnovější stabilní verzi.

## 0.4.25 – 2026-10-12

### Čeština

- Licence projektu změněna z MIT na GNU GPL v3.0 (GPL-3.0-only). Dříve vydané verze zůstávají dostupné pod licencí, pod kterou byly původně zveřejněny.
- Plný text licence a oznámení jsou také součástí instalačního ZIPu HACS.

## 0.4.24 – 2026-10-07

### Čeština

- Jednotné odznaky Home Assistant, HACS, Release, Downloads total, Downloads latest a Validation jsou nyní součástí vydaného tagu, nejen hlavní větve.
- Absolutní odkazy na loga a jednotná šířka 180 px pro správné zobrazení README v HACS.
- HACS zobrazuje dokumentaci nainstalované verze. Pro nový vzhled aktualizujte integraci na toto vydání; samotné obnovení cache staré README nezmění.
- Regresní kontrola úplné hlavičky README v release CI a kontrola kompatibility s přesně zjištěnou nejnovější stabilní verzí Home Assistantu.
- Bez změn funkční logiky, ID entit, nastavení nebo uložených dat. Staré tagy a ZIPy zůstávají zachované včetně počtů stažení.

## 0.4.23 – 2026-10-07

### Čeština

- Instalační příloha `vschrudim_watermeter.zip` pro HACS a odznaky celkových stažení i posledního vydání v anglickém a českém README. Počítání začíná touto verzí a zahrnuje stažení balíčku a aktualizace, nikoli unikátní uživatele.
- Horní počet v HACS patří vybranému vydání. Starší stažení bez přílohy nelze zpětně dopočítat; zdrojové archivy, instalace výchozí větve a exporty vodoměru se do odznaků nezapočítávají.
- Automatické balíčkování z přesného Git commitu nejdříve vytvoří koncept release s ověřeným ZIPem a českými i anglickými poznámkami. Zveřejněné přílohy nepřepisuje a nebalí místní přihlášení, odečty ani data Home Assistantu.
- Bez změn načítání portálu, živých odečtů, statistik, obnovy historie, entit nebo uložených dat; bez nové telemetrie. Starší vydání si zachovávají původní způsob instalace.

## 0.4.22 - 2026-09-11

- Obnovování dostupnosti tlačítek údržby po dokončení úlohy historie, aby `Retry history download` nezůstalo chybně neaktivní.
- Omezené čekání až 15,5 sekundy na zpřístupnění nově zařazených externích statistik Recorderem před označením ověření za čekající.
- Pozastavení neověřeného bloku historie bez posunu průchodu, oznámení chyby či změny živých odečtů; další úspěšná aktualizace bezpečně zopakuje stejný idempotentní blok.

## 0.4.21 - 2026-09-09

- Obnovení diagnostických entit i po úspěšném dotazu se stejným odečtem. `Last update attempt` ukazuje skutečný čas dotazu; živé hodnoty a statistiky Energie se nemění.

## 0.4.20 - 2026-09-08

- Stav statistik Energie zůstává viditelný v diagnostice a kontrole pouze pro čtení, bez trvalých oznámení o čekajícím, neúplném, chybném či obnoveném stavu. Aktualizace po dalším zveřejnění stavu odstraní oznámení z 0.4.19.

## 0.4.19 - 2026-09-08

- Trvalá kontrola dvou externích statistik Energie pouze pro čtení porovnává dokončené hodinové body s Recorderem a uvádí bezpečné údaje o pokrytí, počtech, mezerách, duplicitách a monotónnosti. Staré či nezměněné datum portálu nepovažuje za chybu.
- Diagnostika **Energy statistics status** a **Meter register status** a bezpečné tlačítko **Check Energy statistics**. Kontrola nevyvolává dotaz na portál, zápis statistik, průchod historií ani smazání či přestavbu.
- Omezený bezpečný kontrolní bod umožňuje po úspěšné aktualizaci idempotentně opakovat přerušený zápis; jeho selhání neposune doplňování historie ani nezneplatní živý odečet.
- Oznámení při změně stavu statistik Energie a informační diagnostika nižšího stavu registru nemění výchozí stav při výměně vodoměru ani výpočet spotřeby.

## 0.4.18 - 2026-09-08

- Přenosy portálu zůstávají sériové, expirovaná relace se jednou bezpečně obnoví a omezené exponenciální prodlevy se použijí jen po po sobě jdoucích chybách automatických aktualizací. Ruční test stažení zůstává ihned dostupný.
- Neočekávané chyby koordinátoru a zápisu Recorderu jsou oddělené, aby pomocná diagnostika či statistiky nezahodily ověřený živý odečet.
- Do diagnostiky a omezené historie pokusů přibyly bezpečné profily parseru a neblokující příznaky kvality odečtů. Používají pevný slovník bez HTML, údajů formulářů, URL, přihlašovacích údajů a identifikátorů zákazníka.

## 0.4.17 - 2026-09-08

- Oprava limitu velikosti odpovědi z 0.4.16, aby načetl všechny části HTTP odpovědi; jinak mohla dělená stránka ASP.NET přijít o seznam odběrných míst.
- Rozpoznání výběru odběrného místa podle stabilní přípony ASP.NET `id` včetně vykresleného textu i podle `name` formuláře, ve shodě s ověřeným klientem a bez oslabení ověřování místa.

## 0.4.16 - 2026-09-08

- Mezery v doplňované historii se nehlásí jako chybějící odečty v aktuálně stahovaném rozsahu portálu.
- Stav přechodů oznámení se zachová po restartu Home Assistantu; případné selhání uložení neblokuje běžnou aktualizaci.
- Omezení každé odpovědi na 12 MiB a požadavku na 45 sekund; před odebráním integrace se uloží průběh doplňování historie.
- Při platných odpovědích se nemění pravidelné dotazy, sladění historie, historie živých senzorů ani statistiky Energie.

## 0.4.15 - 2026-09-08

- Oprava regresního testu kompatibility Home Assistantu pro oddělenou kontrolu stažení bez vedlejších účinků.

## 0.4.14 - 2026-09-08

- Přidání stručného stavu zdroje, diagnostiky mezer a počítadel kvality včetně skutečné metody a času úspěšného stažení.
- Tlačítko **Test download** ověří přihlášení a aktuální stažení bez spouštění historie či změn statistik Energie.
- Oznámení zdroje a chybějících odečtů reagují na změny stavu; stáří dat je diagnostické, pokud uživatel výslovně nezapne práh zpoždění.

## 0.4.13 - 2026-09-08

- Přidání přesného místního času nejnovějšího odečtu portálu VS Chrudim vedle relativního zobrazení Home Assistantu.

## 0.4.12 - 2026-09-08

- Po expiraci relace automaticky vytvořit novou přihlášenou relaci a jednou zopakovat úplný požadavek.
- Při odmítnutí přihlašovacích údajů zachovat běžné obnovení přihlášení Home Assistantu; opakovanou expiraci oznámit bez nekonečných pokusů.

## 0.4.11 - 2026-09-08

- Samostatné tlačítko a akce okamžitě zopakují stažení a obnoví nebo znovu spustí idempotentní sladění historie.
- Opakování je oddělené od přestavby statistik Energie a nemaže statistiky ani historii živého senzoru.
- Před vytvořením jedné externí statistiky za hodinu se odečty sjednotí podle času.

## 0.4.10 - 2026-09-08

- Pokud dočasně chybí seznam odběrných míst, využít již zvolený kontext portálu pouze po ověření shody skrytých identifikátorů s nastaveným místem.
- Neověřený chybějící seznam klasifikovat samostatně místo zavádějící chyby parseru tabulky.
- Regresní testy ověřené záložní cesty a ochrany před použitím dat jiného místa.

## 0.4.9 - 2026-09-08

- Oprava chyby přestavby statistik Energie způsobené nepodporovaným argumentem ověření nákladů.
- Nižší stav registru se považuje za nový základ vodoměru, nikoli za hodinovou spotřebu rovnající se celému stavu náhradního přístroje.
- Mazání a přestavba se provádějí sériově, aby nemohly souběžně měnit Recorder.
- Konfigurační tlačítko spouští výslovnou přestavbu statistik Energie po úplném předběžném ověření zdroje.
- Regresní testy ověření přestavby, výměny vodoměru a tlačítka.

## 0.4.8 - 2026-09-07

- Podpora ověřených exportních odkazů ASP.NET WebForms `__doPostBack` při zachování všech úspěšných polí formuláře.
- Lepší záložní čtení vnořených a zabalených tabulek odečtů ASP.NET při nedostupném exportu.
- Platná odpověď se stejným nejnovějším odečtem je úspěšná; stáří dat zůstává diagnostickým údajem.
- Ukládání omezené a soukromí chránící historie pokusů o stažení včetně metody, doby a očištěných chyb.
- Spotřebu a náklady Energie zapisuje externí zapisovač integrace, čímž brání zkreslení denních a měsíčních součtů duplicitními `sensor.*` statistikami.
- Výslovně potvrzované služby pro vymazání nebo předběžně ověřenou obnovu pouze statistik Energie VSChrudim.

## 0.4.7 - 2026-09-07

- Filtrovaný rozsah bez exportu označit jako začátek dostupné historie pouze tehdy, pokud hranici již stanovily novější hodinové odečty.
- Na hranici dokončit obnovitelný průchod místo opakovaných chyb pro data před prvním dostupným odečtem.
- Při obnovení uloženého průchodu odstranit zastaralé oznámení o neúplné historii.

## 0.4.6 - 2026-09-07

- Při volbě vlastního období a použití jeho dat odesílat celý filtr měřených stavů podle ověřeného postupu WebDownloaderu.
- Zachovat všechna úspěšná vstupní pole potřebná pro vykreslení hodinových výsledků a exportu.
- Přidat regresní testy úplných formulářů WebForms pro vlastní období.

## 0.4.5 - 2026-09-07

- Oprava nastavení integrace importem konstant ceny používaných koordinátorem historických nákladů.
- Regresní kontrola závislostí nastavení ceny v Home Assistantu.

## 0.4.4 - 2026-09-07

- Při chybějícím odkazu CSV a exportu WebForms použít vykreslenou tabulku odečtů.
- Převzít výběr tabulky a parsování českých dat a stavu vodoměru z WebDownloaderu bez uvádění zákaznických hodnot v logu a diagnostice.
- Při přechodných chybách zachovat poslední úspěšné odečty a diagnostiku; místní cena zůstává nezávislá na portálu.
- Přidat kumulativní náklady a odpovídající hodinové statistiky, protože Home Assistant zpětně neoceňuje doplněné objemy.
- Regresně ověřit přímé čtení HTML tabulky a záložní postup API.

## 0.4.3 - 2026-09-07

- Ověřovat export stejnou normalizací sloupců CSV jako parser, včetně uvozovek, BOM a české diakritiky.
- Rozlišovat chybějící exportní prvek a neplatný obsah odpovědi bez zaznamenávání obsahu a zákaznických hodnot.
- Regresní testy variant hlavičky a obou kategorií chyb.

## 0.4.2 - 2026-09-07

- Podpora přímých exportních odkazů i tlačítek WebForms podle funkčního postupu WebDownloaderu.
- Při exportu tlačítkem odesílat úplný aktuální filtr včetně textových polí, vybraných hodnot a stavu ASP.NET.
- Zachovat ověření přihlášení a hlavičky CSV u každého kandidáta.
- Regresní test odpovědi exportního tlačítka WebForms.

## 0.4.1 - 2026-09-07

- Rozpoznat export historie `DocumentShow.aspx`, i když text odkazu ani URL neobsahují `CSV`.
- Přijímat export až po ověření hlavičky `MERIDLO;CAS;STAV`.
- Regresní testy platných i neplatných odpovědí `DocumentShow.aspx`.

## 0.4.0 - 2026-09-07

- Výchozí interval dotazů jedna hodina při zachování výslovně nastavených intervalů.
- Import dokončených kumulativních odečtů do statistiky entity `Meter state`, kterou lze vybrat jako spotřebu vody v Energii.
- Zachování podpory ceny z entity v `CZK/m³` v nastavení vody v Energii.
- Automatický obnovitelný průchod tříletou historií po ověřených 31denních HTTP intervalech se samostatnou hodinovou statistikou pro každý záznam portálu.
- Ukládání pouze postupu a počítadel; odečty zůstávají v Recorderu.
- Diagnostické senzory nejnovějších dat, posledního pokusu a postupu či chyb doplňování historie.
- Oznámení po vyčerpání pokusů o historii a jeho odstranění po úspěšném obnovení.
- Odstranění identifikátorů odběrného místa a odečtů ze sdílené diagnostiky.
- Regresní testy vlastního období WebForms a interních statistik.
- Zdokumentování tehdejšího omezení ikon vlastních repozitářů HACS při zachování obou dodávaných rozlišení ikon.

## 0.3.4 - 2026-09-07

- Po výběru odběrného místa otevřít měřené stavy ověřenou cestou `/Userdata/ProfileData.aspx`.
- Před stažením ověřit přítomnost filtru období odečtů.
- Jako zálohu podporovat skutečný postback WebForms `MainMenu1$btnProfileData` včetně prázdného argumentu události.
- Regresní testy přímé navigace i navigace postbackem.

## 0.3.3 - 2026-09-06

- Každému ověření konfiguračního toku a účtu přiřadit vlastní úložiště cookies.
- Zabránit přenosu přihlášené relace z konfiguračního toku do nového provozního klienta, kde chybějící přihlašovací formulář vedl k chybné protokolové chybě.
- Oddělit cookies účtů VS Chrudim a nechat Home Assistant automaticky uklidit HTTP relaci konfigurační položky.

## 0.3.2 - 2026-09-06

- Oprava vyhledávání odběrných míst vynecháním skrytých buněk tabulky podle ověřené implementace WebDownloaderu.
- Zachování původních velkých a malých písmen v údajích místa zobrazených zákazníkovi.
- Pokud řádek nemá vlastní obsluhu postbacku, použít ověřenou záložní cestu WebForms `Show$N`.
- Regresní test skutečné struktury tabulky se skrytou úvodní buňkou.

## 0.3.1 - 2026-09-06

- Oprava odeslání přihlášení ASP.NET, které vybíralo obrázek jazyka místo skutečného tlačítka `btnLogin`.
- Regresní test podle tehdejší veřejné struktury přihlašovacího formuláře.

## 0.3.0 - 2026-09-06

- Nastavitelná trvalá oznámení po opakovaných chybách zdroje.
- Omezené opakování stahování pro doplnění jednotlivých chybějících hodinových odečtů.
- Sloučení opravených hodnot portálu podle času a diagnostika mezer a opakování.
- CI kompatibility s Home Assistantem 2026.8 a 2026.9.

## 0.2.0 - 2026-09-06

- Senzor `Water price` v CZK/m³ pro náklady vody v Energii Home Assistantu.
- Zdokumentování a vymezení plánu doplnění tříleté hodinové historie.

## 0.1.0 - 2026-09-06

- První integrace VSChrudim watermeter připravená pro HACS.
- Konfigurační tok, obnovení přihlášení, koordinátor a senzory vodoměru.
- Odolný klient ASP.NET WebForms/CSV, diagnostika a testy parseru a výpočtů.
