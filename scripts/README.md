# Release installer

`build_release.py` builds `dist/vschrudim_watermeter.zip` with integration files directly at
the archive root, as required by HACS `zip_release`. It uses `git archive` on the
selected commit's `custom_components/vschrudim_watermeter` tree, never the working directory.
Uncommitted edits, local credentials, exports, caches and repository metadata are
not included. Keep private data out of Git as well; the repository privacy tests
remain part of validation.

Local verification, without uploading or publishing:

```shell
python -m unittest discover -s tests -v
python scripts/build_release.py --ref HEAD
```

## Preparing a new release

HACS reads documentation from the installed release tag. README changes that
must appear in HACS therefore need a new patch release, even if no functional
code changed. Commit the README before creating the new tag; do not move older
tags or overwrite their assets. Release CI checks the shared README header in
the tagged checkout.

1. Update `manifest.json` and add a matching version section to `CHANGELOG.md`,
   preserving both `### Čeština` and `### English`. Commit the changes and pass
   the **Validate** workflow, including HACS and Hassfest.
2. Push the corresponding `vX.Y.Z` tag. **Prepare release installer** checks out
   that exact tag, runs tests, verifies the manifest version and builds the ZIP.
3. The workflow attaches the ZIP to a **draft** release and copies the matching
   bilingual changelog section into its notes. It does not publish the release.
4. Review the draft's notes and ensure `vschrudim_watermeter.zip` is attached and the
   workflow succeeded. Only then publish the draft. Do not publish an empty
   release first: HACS expects the configured ZIP to be available immediately.

The workflow can also be run manually with an existing tag that contains these
packaging scripts. It will reuse an identical asset on a draft but will not
overwrite a different asset or modify a published release. Removing/replacing a
GitHub release asset resets its download count; keep published assets intact.

Existing tags are not rewritten. Older releases retain their original `hacs.json`
and source-archive installation path. Default-branch installations are still
supported but do not count as installer downloads.

The README badges filter by the exact asset filename, `vschrudim_watermeter.zip`, so future
dashboard samples or other attachments do not inflate the installer count. The
latest-release badge and HACS's download indicator cover one release; the total
badge combines installer downloads across releases. Neither metric identifies
unique users or active installations, and no integration telemetry is involved.

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
