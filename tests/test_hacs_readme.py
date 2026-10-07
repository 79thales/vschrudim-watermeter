"""Protect the shared HACS README header in the version checked out by CI."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
BADGES = (
    "Home Assistant", "HACS Integration", "Latest release",
    "Installer downloads, all releases", "Installer downloads, latest release",
    "Validation",
)


class HacsReadmeTest(unittest.TestCase):
    """Release workflows run this against the tagged checkout, not main."""

    def test_header_has_the_shared_badges_and_absolute_logo(self) -> None:
        hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
        self.assertTrue(hacs["render_readme"])
        documents = ("README.md", "README.cs.md") if (ROOT / "README.cs.md").is_file() else ("README.md",)
        for document in documents:
            with self.subTest(document=document):
                header = (ROOT / document).read_text(encoding="utf-8").split("##", 1)[0]
                badges = re.findall(r"\[!\[([^\]]+)\]\(([^)]+)\)\]\(([^)]+)\)", header)
                self.assertEqual(tuple(badge[0] for badge in badges), BADGES)
                for label, image, link in badges:
                    url = urlparse(image)
                    query = parse_qs(url.query)
                    self.assertEqual(url.netloc, "img.shields.io")
                    self.assertEqual(url.scheme, "https")
                    self.assertEqual(query["style"], ["flat"])
                    self.assertIn("logo", query)
                    self.assertTrue(link.startswith("https://"))
                    if label == "Home Assistant":
                        self.assertIn(hacs["homeassistant"] + "%2B", image)
                    if label.startswith("Installer downloads"):
                        self.assertIn(hacs["filename"], url.path)
                    if label == "Validation":
                        self.assertTrue(url.path.startswith("/github/check-suites/"))
                logo = re.search(r'<img src="([^"]+)"[^>]*width="180"', header)
                self.assertIsNotNone(logo)
                url = urlparse(logo.group(1))
                self.assertEqual(url.scheme, "https")
                self.assertEqual(url.netloc, "raw.githubusercontent.com")
                local = ROOT / "/".join(url.path.strip("/").split("/")[3:])
                self.assertTrue(local.is_file())
                self.assertTrue(local.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
