# VSChrudim watermeter

Vlastní integrace pro Home Assistant, která načítá vzdálené odečty vodoměru ze zákaznického portálu VS Chrudim. Pro vybrané odběrné místo vytvoří zařízení se senzory kumulativního stavu vodoměru a poslední naměřené spotřeby.

[English documentation](README.md)

## Instalace

V HACS přidejte tento repozitář jako **Custom repository** typu **Integration**, integraci stáhněte a restartujte Home Assistant. Poté přejděte do **Nastavení → Zařízení a služby → Přidat integraci → VSChrudim watermeter**.

Při ruční instalaci zkopírujte složku `custom_components/vschrudim_watermeter` do `/config/custom_components/` a restartujte Home Assistant.

## Nastavení a aktualizace

Zadejte uživatelské jméno a heslo do portálu a vyberte odběrné místo. Výchozí interval aktualizace je jedna hodina, protože portál zaznamenává odečty po hodinách; lze ho změnit přes **Přenastavit** (minimálně 15 minut). Uložený uživatelský interval zůstává zachován i po aktualizaci integrace.

Portál poskytuje kumulativní stav vodoměru po hodinách. Senzor `Meter state` má třídu `total_increasing`, jednotku m³ a je určen pro výběr v **Nastavení → Nástěnky → Energie → Spotřeba vody**. Dokončené hodiny z portálu jsou importovány přímo pod tímto ID senzoru, takže panel Energie pracuje i se zpětně načtenou historií. `Latest consumption` je nezáporný rozdíl mezi dvěma posledními stavy; nejedná se o okamžitý průtok.

Celkovou cenu vody a stočného nastavte přes **Přenastavit**. Výchozí hodnota `0` je záměrná, dokud nezadáte vlastní tarif. Integrace vytváří senzory `Water price` (`CZK/m³`) a `Total water cost` (`CZK`). Změna ceny spustí nový idempotentní průchod historií, aby se hodinové náklady přepočítaly podle aktuálního tarifu.

### Panel Energie a historické náklady

Pro zobrazení nákladů ze zpětně importované historie nastavte zdroj vody v panelu Energie takto:

1. Otevřete **Nastavení → Nástěnky → Energie → Voda** a přidejte nebo upravte zdroj vody.
2. Jako **Spotřebu vody** zvolte `Meter state`.
3. V části nákladů vyberte **Použít entitu sledující celkové náklady**.
4. Jako entitu celkových nákladů zvolte `Total water cost` a nastavení uložte.

Tato volba je pro importovanou historii důležitá. Režimy Home Assistantu **Použít fixní cenu** a **Použít entitu s aktuální cenou** vytvářejí pomocný senzor, který začne na nule a počítá pouze budoucí živé změny. Zpětně importované hodinové odečty neoceňují. `Total water cost` naopak obsahuje odpovídající hodinové statistiky nákladů, proto se zobrazí jak historická spotřeba, tak její cena. Náklad za interval odpovídá změně stavu vodoměru vynásobené cenou nastavenou přes **Přenastavit**.

`Water price` je senzor aktuální jednotkové ceny, nikoli kumulativní nákladový senzor. Pokud už máte v panelu Energie uloženou pevnou cenu, upravte zdroj a přepněte ho na `Total water cost` podle postupu výše.

## Historická data

Po nastavení integrace automaticky prohledá až tři kalendářní roky zpět, včetně obou krajních dat, v 31denních blocích. Každý odečet zůstává samostatnou hodinovou statistikou; bloky pouze snižují počet požadavků na portál a data neagregují. Integrace používá stejná vlastní datumová pole ASP.NET WebForms jako WebDownloader, upřednostňuje ověřenou CSV odpověď a pokud portál nabídku exportu nezobrazí, načte vykreslenou tabulku naměřených stavů.

Každý neprázdný výsledek ověřuje podle překryvu s požadovaným obdobím, importuje pouze dokončené hodiny a po každém bloku uloží postup. Přerušené nebo chybné procházení pokračuje z uloženého bodu; požadavky portálu se řadí za běžné aktualizace a opakují se podle nastavení obnovy.

Do záznamu `.storage` integrace se ukládají pouze data postupu a počitadla. Odečty zákazníka se zapisují do Recorder statistik Home Assistantu a v záznamu postupu se neduplikují.

Diagnostické entity zobrazují:

- nejnovější časovou značku obsaženou v posledním úspěšném stažení (`Data available through`),
- čas a výsledek posledního pokusu o aktualizaci (`Last update attempt`),
- postup tříletého doplnění historie, počet importovaných hodin a poslední chybu (`History backfill status`).

## Upozornění na nedostupnost a chybějící odečty

Integrace používá trvalá oznámení Home Assistantu. Ve výchozím nastavení po třech po sobě jdoucích neúspěšných aktualizacích oznámí nedostupnost portálu, při dalších chybách stejné oznámení nahradí a po obnovení ho automaticky odstraní. Chyby přihlášení řeší standardním procesem opětovné autentizace.

Každé úspěšné stažení se spojí s odečty, které už byly během běhu integrace získány. Vnitřní hodinové mezery vyvolají až dva opakované požadavky s nastavitelnou prodlevou. Opravená hodnota portálu nahradí starší hodnotu se stejnou časovou značkou. Pokud mezery zůstanou, jedno trvalé oznámení uvede jejich počet a krátký přehled časů; po doplnění odečtů zmizí. Neexistující 02:00 při českém přechodu na letní čas se za chybějící odečet nepovažuje. Prahy, oznámení, počet pokusů i prodlevy lze změnit přes **Přenastavit**.

## Kompatibilita s portálem

VS Chrudim poskytuje autentizovaný web ASP.NET WebForms, nikoli zdokumentované veřejné API. Klient následuje pole formulářů, odkazy v menu, WebForms postbacky a odkazy pro CSV export nalezené v přihlášeném HTML. Pokud očekávanou strukturu nenajde, bezpečně skončí s chybou; nevymýšlí REST endpointy a nespouští WebDownloader.

Pro zpětné načtení historie jsou potřeba aktuální prvky vlastního období portálu. Pokud je provozovatel změní nebo odstraní, běžné aktualizace zůstanou odděleny od chybného doplnění historie a diagnostický stav uvede chybu protokolu.

## Bezpečnost

Uživatelské jméno a heslo jsou uloženy v ConfigEntry Home Assistantu, a mohou proto být součástí šifrované zálohy Home Assistantu. Session cookies se neukládají; tokeny a přihlašovací údaje jsou z diagnostiky odstraněny a nikdy se nezapisují do logu.

Brand ikony jsou součástí `custom_components/vschrudim_watermeter/brand/`, což je aktuální umístění pro lokální značku Home Assistantu. Home Assistant je zobrazuje lokálně. Některé verze HACS mohou u vlastních repozitářů stále zobrazovat náhradní ikonu, protože jejich přehled stahování zatím nepoužívá lokální proxy pro značku Home Assistantu; soubory ikon jsou přesto součástí každé instalace i vydání.

## Prohlášení

Integrace je nezávislá a není oficiálním produktem ani podporovanou službou Vodárenské společnosti Chrudim. Používané webové rozhraní není veřejně garantováno a může se bez upozornění změnit.
