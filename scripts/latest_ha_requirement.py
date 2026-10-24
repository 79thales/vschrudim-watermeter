"""Přesné závislosti CI: stabilní HA a pouze plugin určený pro stejný core."""

from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.request import urlopen


def latest_requirement(info: dict[str, object]) -> str:
    """Vrátit přesný pin stabilního HA; vadná metadata ani betu nepředat pipu."""
    version = info.get("version")
    if not isinstance(version, str) or re.fullmatch(r"\d{4}\.\d+\.\d+", version) is None:
        raise ValueError("PyPI did not return a stable Home Assistant release")
    return f"homeassistant=={version}"


def matching_plugin_requirement(ha_requirement: str, info: dict[str, object]) -> str:
    """Ověřit metadata pluginu proti již zvolené přesné verzi HA.

    Vstupem je pin core a metadata aktuálního pluginu z PyPI; výstupem jeho
    přesný pin pro pip. Uznáváme jedinou nepodmíněnou závislost == na stejném
    stabilním HA. Historický plugin bez pinu, rozsah, jiná verze nebo vadná
    metadata ukončí kontrolu chybou, nikoli návratem staršího HA či pluginu.
    Tím zabráníme backtrackingu pipu, který dříve vybral nekompatibilní 0.2.1.
    """
    if re.fullmatch(r"homeassistant==\d{4}\.\d+\.\d+", ha_requirement) is None:
        raise ValueError("Expected an exact stable Home Assistant requirement")
    version = info.get("version")
    if not isinstance(version, str) or re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise ValueError("PyPI did not return a stable test plugin release")
    dependencies = info.get("requires_dist")
    if not isinstance(dependencies, list) or not all(
        isinstance(dependency, str) for dependency in dependencies
    ):
        raise ValueError("PyPI did not return valid test plugin dependencies")

    # Ostatní závislosti pluginu posoudí pip. HA ověřujeme před instalací,
    # včetně staršího zápisu „homeassistant (==verze)“ v metadatech balíčku.
    ha_dependencies = [
        dependency
        for dependency in dependencies
        if re.match(r"homeassistant(?:\s|[<>=!~;(\[]|$)", dependency, re.IGNORECASE)
    ]
    ha_version = re.escape(ha_requirement.split("==", 1)[1])
    exact_pin = rf"homeassistant\s*(?:==\s*{ha_version}|\(\s*==\s*{ha_version}\s*\))"
    if len(ha_dependencies) != 1 or re.fullmatch(
        exact_pin, ha_dependencies[0], re.IGNORECASE
    ) is None:
        declared = ", ".join(ha_dependencies) or "no Home Assistant dependency"
        raise ValueError(
            f"Matching test plugin unavailable for {ha_requirement}: "
            f"latest pytest-homeassistant-custom-component=={version} declares "
            f"{declared}. Refusing an older or unpinned plugin. "
            "Full HA fixture tests remain unverified; rerun after a matching "
            "plugin is published."
        )
    return f"pytest-homeassistant-custom-component=={version}"


def _read_info(project: str) -> dict[str, object]:
    """Načíst veřejná metadata PyPI; síťový timeout je 20 sekund na požadavek."""
    with urlopen(f"https://pypi.org/pypi/{project}/json", timeout=20) as response:
        return json.load(response)["info"]


def main(argv: list[str] | None = None) -> int:
    """Vypsat jediný pin, nebo chybu na stderr a nenulový návratový kód.

    Bez argumentu zvolíme nejnovější stabilní HA. Druhá fáze dostane stejný
    pin přes --test-plugin-for, takže vydání nového HA mezi kroky nezmění
    testované prostředí. Při chybě nevznikne výstup pro instalaci; workflow
    musí respektovat nenulový návratový kód a zastavit před voláním pipu.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-plugin-for", metavar="homeassistant==VERSION")
    args = parser.parse_args(argv)
    try:
        if args.test_plugin_for is not None:
            requirement = matching_plugin_requirement(
                args.test_plugin_for,
                _read_info("pytest-homeassistant-custom-component"),
            )
        else:
            requirement = latest_requirement(_read_info("homeassistant"))
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"HA compatibility dependency check failed: {error}", file=sys.stderr)
        return 1
    print(requirement)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
