"""Async client for the VS Chrudim ASP.NET WebForms customer portal.

The portal does not publish a supported machine API.  This client deliberately
replays only the WebForms forms and links actually supplied by the logged-in
portal; it does not invent undocumented endpoints or persist session cookies.
"""
from __future__ import annotations

from datetime import datetime
from html import unescape
from html.parser import HTMLParser
import re
from typing import Final
from urllib.parse import urljoin

import aiohttp

from .calculation import latest_consumption
from .const import BASE_URL, PLACES_URL
from .models import ConsumptionPlace, MeterReading, WaterMeterData

_DATE_FORMATS: Final = ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y")

class VsChrudimError(Exception):
    """Base portal error."""

class VsChrudimAuthError(VsChrudimError):
    """Credentials are invalid or the authenticated session expired."""

class VsChrudimProtocolError(VsChrudimError):
    """The portal markup changed or did not contain expected data."""

class VsChrudimConnectionError(VsChrudimError):
    """The portal could not be reached."""

class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action = ""
        self.method = "GET"
        self.inputs: dict[str, str] = {}
        self.submit_names: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self.action = values.get("action", "") or ""
            self.method = (values.get("method", "GET") or "GET").upper()
        elif tag == "input":
            name = values.get("name")
            if name:
                self.inputs[name] = values.get("value", "") or ""
                if (values.get("type", "") or "").lower() in {"submit", "image", "button"}:
                    self.submit_names.append(name)
        elif tag == "a":
            self._href = values.get("href", "") or ""
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((" ".join(self._text).strip(), self._href))
            self._href = ""
            self._text = []

def _parse_form(html: str) -> _FormParser:
    parser = _FormParser()
    parser.feed(html)
    return parser

def _normalized(value: str) -> str:
    return " ".join(unescape(value).replace("\xa0", " ").split()).casefold()

def _number(value: str) -> float | None:
    try:
        return float(value.strip().replace(" ", "").replace(",", "."))
    except ValueError:
        return None

def parse_readings_csv(content: str) -> list[MeterReading]:
    """Parse the documented ``MERIDLO;CAS;STAV`` CSV export defensively."""
    lines = [line.strip() for line in content.replace("\r", "").split("\n") if line.strip()]
    if not lines:
        return []
    headers = [_normalized(value.lstrip("\ufeff\"").rstrip("\"")) for value in lines[0].split(";")]
    try:
        date_index = next(i for i, value in enumerate(headers) if value in {"cas", "datum"})
        state_index = next(i for i, value in enumerate(headers) if "stav" in value)
    except StopIteration as err:
        raise VsChrudimProtocolError("CSV does not contain CAS/DATUM and STAV columns") from err
    meter_index = next((i for i, value in enumerate(headers) if "meridlo" in value), None)
    readings: dict[datetime, MeterReading] = {}
    for line in lines[1:]:
        cells = [cell.strip().strip('"') for cell in line.split(";")]
        if max(date_index, state_index) >= len(cells):
            continue
        timestamp = next((datetime.strptime(cells[date_index], fmt) for fmt in _DATE_FORMATS if _matches(cells[date_index], fmt)), None)
        state = _number(cells[state_index])
        if timestamp is None or state is None:
            continue
        meter = cells[meter_index] if meter_index is not None and meter_index < len(cells) else ""
        readings[timestamp] = MeterReading(timestamp, state, meter)
    return sorted(readings.values(), key=lambda item: item.timestamp)

def _matches(value: str, fmt: str) -> bool:
    try:
        datetime.strptime(value, fmt)
    except ValueError:
        return False
    return True

def parse_consumption_places(html: str) -> list[ConsumptionPlace]:
    """Extract the verified ConsumptionPlaceList WebForms grid."""
    table_match = re.search(r'<table[^>]+(?:id|name)=["\'][^"\']*gvConsumptionPlaces[^"\']*["\'][^>]*>(.*?)</table>', html, re.I | re.S)
    if not table_match:
        raise VsChrudimProtocolError("Consumption-place grid was not found")
    places: list[ConsumptionPlace] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table_match.group(1), re.I | re.S)[1:]:
        cells = [_normalized(re.sub(r"<[^>]+>", " ", cell)) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.I | re.S)]
        if len(cells) >= 4 and cells[0]:
            places.append(ConsumptionPlace(*((cells + [""] * 5)[:5])))
    return places

class VsChrudimClient:
    """Stateful, in-memory authenticated portal client."""
    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._logged_in = False

    async def async_login(self) -> None:
        html, url = await self._request_text("GET", BASE_URL)
        form = _parse_form(html)
        names = list(form.inputs)
        user_name = next((n for n in names if any(key in n.casefold() for key in ("user", "login", "jmeno"))), None)
        password_name = next((n for n in names if "pass" in n.casefold() or "heslo" in n.casefold()), None)
        if not user_name or not password_name:
            raise VsChrudimProtocolError("Login form fields were not found")
        payload = {name: value for name, value in form.inputs.items() if name.startswith("__")}
        payload.update({user_name: self._username, password_name: self._password})
        if form.submit_names:
            payload[form.submit_names[0]] = form.inputs.get(form.submit_names[0], "")
        response, _ = await self._request_text(form.method, urljoin(url, form.action or url), data=payload)
        if self._looks_like_login(response):
            raise VsChrudimAuthError("The portal rejected the supplied credentials")
        self._logged_in = True

    async def async_get_places(self) -> list[ConsumptionPlace]:
        await self._ensure_login()
        html, _ = await self._request_text("GET", PLACES_URL)
        if self._looks_like_login(html):
            self._logged_in = False
            raise VsChrudimAuthError("Authenticated session expired")
        return parse_consumption_places(html)

    async def async_get_data(self, place: ConsumptionPlace) -> WaterMeterData:
        """Select a place then follow its actual 'Naměřené stavy' menu link/postback."""
        await self._ensure_login()
        html, url = await self._request_text("GET", PLACES_URL)
        selected_html, selected_url = await self._select_place(html, url, place)
        readings_html, readings_url = await self._open_measured_states(selected_html, selected_url)
        csv = await self._download_csv(readings_html, readings_url)
        readings = tuple(parse_readings_csv(csv))
        return WaterMeterData(place, readings, latest_consumption(readings))

    async def _ensure_login(self) -> None:
        if not self._logged_in:
            await self.async_login()

    async def _select_place(self, html: str, url: str, place: ConsumptionPlace) -> tuple[str, str]:
        rows = re.findall(r"<tr[^>]*?(?:onclick=[\"']([^\"']+)[\"'])?[^>]*>(.*?)</tr>", html, re.I | re.S)
        for onclick, row in rows:
            text = _normalized(re.sub(r"<[^>]+>", " ", row))
            if place.evidence_number in text or (place.technical_number and place.technical_number in text):
                match = re.search(r"__doPostBack\('([^']+)'\s*,\s*'([^']+)'\)", onclick)
                if not match:
                    continue
                form = _parse_form(html)
                payload = {name: value for name, value in form.inputs.items() if name.startswith("__")}
                payload["__EVENTTARGET"], payload["__EVENTARGUMENT"] = match.groups()
                return await self._request_text("POST", urljoin(url, form.action or url), data=payload)
        raise VsChrudimProtocolError("Selected consumption place is absent from the portal grid")

    async def _open_measured_states(self, html: str, url: str) -> tuple[str, str]:
        form = _parse_form(html)
        for text, href in form.links:
            if _normalized(text) == "naměřené stavy" and not href.casefold().startswith("javascript:"):
                return await self._request_text("GET", urljoin(url, href))
        raise VsChrudimProtocolError("The portal did not expose a navigable 'Naměřené stavy' link")

    async def _download_csv(self, html: str, url: str) -> str:
        form = _parse_form(html)
        for text, href in form.links:
            if "csv" in _normalized(text + " " + href) and not href.casefold().startswith("javascript:"):
                content, _ = await self._request_text("GET", urljoin(url, href))
                if re.search(r"MERIDLO\s*;\s*CAS\s*;\s*STAV", content, re.I):
                    return content
        raise VsChrudimProtocolError("The portal did not expose a verified CSV export link")

    @staticmethod
    def _looks_like_login(html: str) -> bool:
        value = html.casefold()
        return "password" in value and ("login" in value or "přihlás" in value)

    async def _request_text(self, method: str, url: str, data: dict[str, str] | None = None) -> tuple[str, str]:
        try:
            async with self._session.request(method, url, data=data, allow_redirects=True) as response:
                text = await response.text()
                if response.status >= 500:
                    raise VsChrudimConnectionError(f"Portal returned HTTP {response.status}")
                if response.status >= 400:
                    raise VsChrudimProtocolError(f"Portal returned HTTP {response.status}")
                return text, str(response.url)
        except aiohttp.ClientError as err:
            raise VsChrudimConnectionError("Unable to connect to VS Chrudim portal") from err
