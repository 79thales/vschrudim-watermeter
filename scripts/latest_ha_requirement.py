"""Resolve an exact stable HA requirement; never silently install an older HA."""

from __future__ import annotations

import json
import re
from urllib.request import urlopen


def latest_requirement(info: dict[str, object]) -> str:
    """Reject prereleases and invalid metadata before passing anything to pip."""
    version = info.get("version")
    if not isinstance(version, str) or re.fullmatch(r"\d{4}\.\d+\.\d+", version) is None:
        raise ValueError("PyPI did not return a stable Home Assistant release")
    return f"homeassistant=={version}"


if __name__ == "__main__":
    with urlopen("https://pypi.org/pypi/homeassistant/json", timeout=20) as response:
        print(latest_requirement(json.load(response)["info"]))
