# VSChrudim watermeter

Vlastní integrace pro Home Assistant, která načítá vzdálené odečty vodoměru ze zákaznického portálu VS Chrudim. Pro vybrané odběrné místo vytvoří zařízení se senzory kumulativního stavu vodoměru a poslední naměřené spotřeby.

[English documentation](README.md)

## Instalace

V HACS přidejte tento repozitář jako **Custom repository** typu **Integration**, integraci stáhněte a restartujte Home Assistant. Poté přejděte do **Nastavení → Zařízení a služby → Přidat integraci → VSChrudim watermeter**.

Při ruční instalaci zkopírujte složku `custom_components/vschrudim_watermeter` do `/config/custom_components/` a restartujte Home Assistant.

## Nastavení a aktualizace

Zadejte uživatelské jméno a heslo do portálu a vyberte odběrné místo. Výchozí interval aktualizace je jedna hodina, protože portál zaznamenává odečty po hodinách; lze ho změnit přes **Přenastavit** (minimálně 15 minut). Uložený uživatelský interval zůstává zachován i po aktualizaci integrace.

Portál poskytuje kumulativní stav vodoměru po hodinách. `Meter state` je živý
zobrazovací senzor v m³. Dokončené hodiny z portálu zapisuje jediný externí
zapisovač statistik integrace; živý senzor a import tak nevytvářejí dvě
konkurenční součtové statistiky Recorderu. `Latest consumption` je nezáporný
rozdíl mezi dvěma posledními stavy; nejedná se o okamžitý průtok.

Celkovou cenu vody a stočného nastavte přes **Přenastavit**. Výchozí hodnota `0` je záměrná, dokud nezadáte vlastní tarif. Integrace vytváří senzory `Water price` (`CZK/m³`) a `Total water cost` (`CZK`). Změna ceny spustí nový idempotentní průchod historií, aby se hodinové náklady přepočítaly podle aktuálního tarifu.

### Panel Energie a historické náklady

Pro zobrazení nákladů ze zpětně importované historie nastavte zdroj vody v
panelu Energie takto:

1. Otevřete **Nastavení → Nástěnky → Energie → Voda** a přidejte nebo upravte zdroj vody.
2. Jako **Spotřebu vody** zvolte `Water consumption` od **VSChrudim
   watermeter**. Jde o externí statistiku s ID
   `vschrudim_watermeter:<entry_id>_water_consumption`, nikoli `sensor.*`.
   Recorder vyžaduje malé znaky, proto se `<entry_id>` převede na malá písmena,
   pokud Home Assistant používá velké znaky ID konfigurační položky.
3. V části nákladů vyberte **Použít entitu sledující celkové náklady**.
4. Jako statistiku celkových nákladů zvolte `Water cost` od **VSChrudim
   watermeter**. Její ID je `vschrudim_watermeter:<entry_id>_water_cost`.

Tato volba je pro importovanou historii důležitá. Režimy Home Assistantu
**Použít fixní cenu** a **Použít entitu s aktuální cenou** vytvářejí pomocný
senzor, který začne na nule a počítá pouze budoucí živé změny. Zpětně
importované hodinové odečty neoceňují. Statistika `Water cost` naopak obsahuje
odpovídající hodinové statistiky nákladů, proto se zobrazí jak historická
spotřeba, tak její cena. Náklad za interval odpovídá změně stavu vodoměru
vynásobené cenou nastavenou přes **Přenastavit**.

`Water price` je senzor aktuální jednotkové ceny, nikoli kumulativní nákladová
statistika. `Total water cost` zůstává praktickým živým zobrazovacím senzorem,
ale není zdrojem statistik Energie. Pokud už panel používá starší zdroj
`sensor.*` (`Meter state` nebo `Total water cost`) či pevnou cenu, proveďte
nejdříve jednorázovou obnovu níže a teprve potom přepněte obě volby.

### Oprava starších statistik Energie

Verze 0.4.8 opravuje dřívější dvojí zápis statistik, který mohl zkreslit denní
a měsíční součty. Aktualizace sama žádná data nemaže. Po první úspěšné
aktualizaci nové verze spusťte v **Nástroje pro vývojáře → Akce** následující
službu s ID své konfigurační položky:

```yaml
action: vschrudim_watermeter.rebuild_energy_statistics
data:
  entry_id: "id-vasi-konfiguracni-polozky"
  confirm: true
```

Od verze 0.4.9 lze stejnou potvrzenou obnovu spustit také na stránce zařízení
vodoměru tlačítkem **Obnovit statistiky Energie** v části **Konfigurace**.
Tlačítko provede bezpečný předběžný download a volá obnovu s výslovným
potvrzením. Samostatné mazání statistik zůstává dostupné pouze jako služba.

Služba nejdříve stáhne a ověří celou dostupnou historii portálu. Teprve poté
vymaže staré statistiky `sensor.*` a integrační statistiky, chronologicky
naimportuje náhradu, ověří poslední monotónní součet a obnoví běžné zápisy.
Pokud stažení nebo ověření selže, stávající statistiky nesmaže. Živé stavy
senzorů ani jejich obyčejnou historii Recorderu nikdy nemaže.

`vschrudim_watermeter.clear_energy_statistics` vyžaduje stejné `entry_id` a
`confirm: true`, ale pouze smaže uvedené statistiky Energie a ponechá zápis
pozastavený. Použijte ji jen tehdy, když chcete řadu Energie záměrně vyprázdnit
před pozdější obnovou.

## Historická data

Po nastavení integrace automaticky prohledá až tři kalendářní roky zpět, včetně obou krajních dat, v 31denních blocích. Každý odečet zůstává samostatnou hodinovou statistikou; bloky pouze snižují počet požadavků na portál a data neagregují. Integrace používá stejná vlastní datumová pole ASP.NET WebForms jako WebDownloader, upřednostňuje ověřenou CSV odpověď a pokud portál nabídku exportu nezobrazí, načte vykreslenou tabulku naměřených stavů.

Každý neprázdný výsledek ověřuje podle překryvu s požadovaným obdobím, importuje pouze dokončené hodiny a po každém bloku uloží postup. Přerušené nebo chybné procházení pokračuje z uloženého bodu; požadavky portálu se řadí za běžné aktualizace a opakují se podle nastavení obnovy.

Do záznamu `.storage` integrace se ukládají pouze data postupu a počitadla. Odečty zákazníka se zapisují do Recorder statistik Home Assistantu a v záznamu postupu se neduplikují.

V části **Diagnostika** zařízení použijte tlačítko **Zkusit doplnit data**.
Vyvolá okamžitou aktualizaci portálu a potom obnoví uložené doplňování nebo
spustí nový idempotentní průchod dostupnou hodinovou historií. Nejde o tlačítko
**Obnovit statistiky Energie**: pokus nemaže statistiky ani běžnou historii
živých senzorů. `Data stažena do` vždy ukazuje nejnovější čas skutečně vrácený
portálem VS Chrudim; integrace nepředpokládá žádnou maximální přípustnou prodlevu.

Opakované odpovědi portálu se spojují podle časové značky; opravená hodnota
nahradí starší hodnotu stejného času. Zapisovače externích statistik navíc
vytvoří pro každou dokončenou hodinu jediný bod pod integračními ID statistik,
takže opakovaný pokus nevytvoří druhou statistickou řadu.

Diagnostické entity zobrazují:

- nejnovější časovou značku obsaženou v posledním úspěšném stažení (`Data available through`),
- čas a výsledek posledního pokusu o aktualizaci (`Last update attempt`),
- postup tříletého doplnění historie, počet importovaných hodin a poslední chybu (`History backfill status`).

Stažená diagnostika navíc obsahuje nejnovější-první průběžnou historii posledních
30 běžných pokusů o stažení. Každý záznam obsahuje pouze bezpečný čas, výsledek,
způsob získání, počet a očištěnou chybu. Historie přežije restart Home
Assistantu a neobsahuje přihlašovací údaje, cookies, HTML portálu, CSV obsah,
identifikátory odběrného místa ani URL požadavků.

## Upozornění na nedostupnost a chybějící odečty

Integrace používá trvalá oznámení Home Assistantu. Ve výchozím nastavení po třech po sobě jdoucích neúspěšných aktualizacích oznámí nedostupnost portálu, při dalších chybách stejné oznámení nahradí a po obnovení ho automaticky odstraní. Chyby přihlášení řeší standardním procesem opětovné autentizace.

Každé úspěšné stažení se spojí s odečty, které už byly během běhu integrace získány. Vnitřní hodinové mezery vyvolají až dva opakované požadavky s nastavitelnou prodlevou. Opravená hodnota portálu nahradí starší hodnotu se stejnou časovou značkou. Pokud mezery zůstanou, jedno trvalé oznámení uvede jejich počet a krátký přehled časů; po doplnění odečtů zmizí. Neexistující 02:00 při českém přechodu na letní čas se za chybějící odečet nepovažuje. Prahy, oznámení, počet pokusů i prodlevy lze změnit přes **Přenastavit**.

## Kompatibilita s portálem

VS Chrudim poskytuje autentizovaný web ASP.NET WebForms, nikoli zdokumentované
veřejné API. Klient následuje pole formulářů, odkazy v menu, WebForms postbacky
a odkazy pro CSV export nalezené v přihlášeném HTML. U naměřených stavů podporuje
ověřené přímé CSV odkazy, dokumentované WebForms ovladače odeslání, ověřené
exportní LinkButtony `__doPostBack` a deterministickou záložní tabulku s českými
záhlavími data a stavu vodoměru. Libovolný JavaScript nikdy nespouští. Pokud
očekávanou strukturu nenajde, bezpečně skončí s chybou; nevymýšlí REST endpointy
a nespouští WebDownloader.

Pokud portál přeskočí seznam odběrných míst a otevře rovnou dříve vybraný
reporting/detail, integrace přes něj pokračuje pouze tehdy, když portálové
identifikátory odpovídají nakonfigurovanému odběrnému místu. Chybějící
neověřená tabulka nikdy neopravňuje použít data jiného místa.

Pro zpětné načtení historie jsou potřeba aktuální prvky vlastního období portálu. Pokud je provozovatel změní nebo odstraní, běžné aktualizace zůstanou odděleny od chybného doplnění historie a diagnostický stav uvede chybu protokolu.

## Bezpečnost

Uživatelské jméno a heslo jsou uloženy v ConfigEntry Home Assistantu, a mohou proto být součástí šifrované zálohy Home Assistantu. Session cookies se neukládají; tokeny a přihlašovací údaje jsou z diagnostiky odstraněny a nikdy se nezapisují do logu.

Brand ikony jsou součástí `custom_components/vschrudim_watermeter/brand/`, což je aktuální umístění pro lokální značku Home Assistantu. Home Assistant je zobrazuje lokálně. Některé verze HACS mohou u vlastních repozitářů stále zobrazovat náhradní ikonu, protože jejich přehled stahování zatím nepoužívá lokální proxy pro značku Home Assistantu; soubory ikon jsou přesto součástí každé instalace i vydání.

## Prohlášení

Integrace je nezávislá a není oficiálním produktem ani podporovanou službou Vodárenské společnosti Chrudim. Používané webové rozhraní není veřejně garantováno a může se bez upozornění změnit.
