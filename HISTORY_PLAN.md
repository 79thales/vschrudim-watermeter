# Doplňování hodinové historie

Stav: implementováno ve verzi 0.4.0. Tento historický plán popisuje tehdejší řešení; aktuální stav uvádí [README](README.md).

## Cíl

Importovat nejvýše tři kalendářní roky potvrzených hodinových odečtů pro každé nastavené odběrné místo bez přetížení portálu VS Chrudim nebo poškození statistik Home Assistantu.

## Průběh

Úloha se spustí po prvním úspěšném nastavení integrace a po restartu naváže na uložený kontrolní bod.

1. Procházet období zpětně od dneška do data před třemi kalendářními roky.
2. Žádat nejvýše 31 dnů včetně krajních dat pomocí ověřeného filtru portálu `U` (vlastní období), viditelných i skrytých dat `GraphFilter1` a akce `btnRenew`.
3. Vyžadovat CSV s hlavičkou `MERIDLO;CAS;STAV` a ověřit překryv každé neprázdné odpovědi s požadovaným obdobím. Odmítnout odpověď, která místo toho pouze vrací nedávná data.
4. Dokončené hodiny idempotentně importovat pod ID statistiky skutečného senzoru vodoměru. Absolutní stav registru použít pro stav i součet statistiky, aby nezávisle obnovené intervaly zůstaly konzistentní.
5. Ukládat pouze necitlivý kontrolní bod: konec dalšího intervalu, začátek průchodu, počítadla, časové údaje a obecnou chybu. Neukládat odečty, cookies relace ani přihlašovací údaje.
6. Historii a běžné dotazy řadit za sebe pomocí jediného zámku API, mezi bloky krátce čekat a chybné bloky opakovat podle nastaveného počtu pokusů a prodlevy. Další úspěšná aktualizace koordinátoru obnoví neúspěšný průchod.

## Tehdejší vazba na panel Energie

`sensor.<place>_meter_state` zůstává zdrojovým senzorem s `device_class: water`, `state_class: total_increasing` a jednotkou `m³`. Historie se importuje jako interní statistika Recorderu se stejným ID entity. `sensor.<place>_total_water_cost` odpovídá kumulativnímu stavu vynásobenému cenou v `CZK/m³`; odpovídající hodinové nákladové statistiky se importují pod jeho ID. Generovaný nákladový senzor Home Assistantu pro pevnou či aktuální cenu reaguje pouze na budoucí změny stavu a zpětně importovanou historii neoceňuje. Historické změny tarifu nejsou zahrnuty, dokud není k dispozici datovaný zdroj tarifů.

## Akceptační kontroly

- Zkušební průchod za měsíc vrátí hodinové hodnoty pro požadovaný měsíc.
- Opakovaný průchod nevytvoří duplicitní statistiky.
- Po restartu průchod pokračuje z uloženého kontrolního bodu.
- Reset vodoměru nikdy nevytvoří zápornou spotřebu.
- Přechody letního času zachovají všechny časové údaje portálu bez vymyšlené chybějící hodiny.
