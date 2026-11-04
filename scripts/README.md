# Instalační balíček vydání

`build_release.py` sestaví `dist/vschrudim_watermeter.zip` se soubory integrace přímo v kořeni archivu, jak vyžaduje `zip_release` v HACS. Používá `git archive` pro strom `custom_components/vschrudim_watermeter` vybraného commitu, nikoli pracovní adresář. Nezahrne necommitované změny, místní přihlašovací údaje, exporty, cache ani metadata repozitáře. Soukromé údaje neukládejte ani do Gitu; součástí validace zůstávají testy soukromí.

Místní ověření bez nahrání nebo zveřejnění:

```shell
python -m unittest discover -s tests -v
python scripts/build_release.py --ref HEAD
```

## Příprava nového vydání

HACS načítá dokumentaci z tagu nainstalovaného vydání. Aby se změny README zobrazily v HACS, potřebují nové opravné vydání i bez změn funkčního kódu. README commitněte před vytvořením tagu; starší tagy nepřesouvejte ani nepřepisujte jejich přílohy. CI vydání kontroluje společnou hlavičku README v příslušném tagu.

1. Aktualizujte `manifest.json` a přidejte odpovídající oddíl verze do `CHANGELOG.md` se sekcí `### Čeština`. Změny commitněte a ověřte úspěšný běh **Validate**, včetně HACS a Hassfest.
2. Odešlete odpovídající tag `vX.Y.Z`. **Prepare release installer** načte přesně tento tag, spustí testy, ověří verzi manifestu a sestaví ZIP.
3. Workflow připojí ZIP ke konceptu vydání a zkopíruje do poznámek příslušný oddíl changelogu. Vydání nezveřejní.
4. Zkontrolujte poznámky konceptu, přílohu `vschrudim_watermeter.zip` a úspěšné dokončení workflow. Teprve potom koncept zveřejněte. Nezveřejňujte nejprve prázdné vydání: HACS očekává ZIP ihned.

Workflow lze také spustit ručně nad existujícím tagem obsahujícím tyto balicí skripty. Shodnou přílohu konceptu znovu použije, odlišnou však nepřepíše a zveřejněné vydání nezmění. Odebrání nebo nahrazení přílohy na GitHubu vynuluje její počítadlo stažení; zveřejněné přílohy zachovejte.

Existující tagy se nepřepisují. Starší vydání si zachovávají původní `hacs.json` a způsob instalace ze zdrojového archivu. Instalace z výchozí větve jsou nadále podporované, ale nezapočítávají se jako stažení instalátoru.

Odznaky README filtrují přesný název přílohy `vschrudim_watermeter.zip`, takže případné ukázky panelů či jiné přílohy nezvyšují počet instalátorů. Odznak posledního vydání a ukazatel HACS sledují jedno vydání; celkový odznak sčítá stažení instalátoru napříč vydáními. Žádná z metrik neurčuje počet jedinečných uživatelů ani aktivních instalací a integrace neposílá telemetrii.

## Ověření aktuální stabilní verze Home Assistantu

Validace i příprava vydání nejprve pomocí latest_ha_requirement.py určí
přesný pin aktuálního stabilního HA z PyPI, nainstalují pouze tento core a
spustí unittest. Stejný pin uloží do HA_REQUIREMENT pro následující kroky;
nové vydání HA během běhu tedy nezmění testovanou verzi.

Potom příkaz python scripts/latest_ha_requirement.py --test-plugin-for "$HA_REQUIREMENT"
ověří, že aktuální stabilní pytest-homeassistant-custom-component z PyPI
deklaruje přesnou, nepodmíněnou závislost na testovaném HA. Teprve pak
workflow instaluje oba přesné piny, provede pip check a spustí skutečné
testy konfigurace a zachování registrů pomocí pytest.

Pokud odpovídající plugin ještě nebyl vydán, kontrola skončí jasnou chybou
a úplná kompatibilita zůstává neověřená. CI nesmí přejít na starší HA, betu
ani historický plugin bez přesné závislosti. Stejná chyba blokuje vytvoření
draftu vydání; po zveřejnění odpovídajícího pluginu lze běh zopakovat.
Síťová chyba či vadná metadata mají rovněž nenulový návratový kód.
Žádný krok se nepřipojuje k provoznímu Home Assistantu.

Regresní testy resolveru běží bez sítě jako součást unittest discover.
Pokrývají zejména situaci z 24. 10. 2026: HA 2026.10.3, aktuální plugin
0.13.373 pro HA 2026.10.2 a nepřípustný fallback pipu na plugin 0.2.1
s neomezenou závislostí homeassistant.
