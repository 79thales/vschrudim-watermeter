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
        self.input_types: dict[str, str] = {}
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
                input_type = (values.get("type", "text") or "text").lower()
                self.inputs[name] = values.get("value", "") or ""
                self.input_types[name] = input_type
                if input_type in {"submit", "image", "button"}:
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


def _login_field_names(form: _FormParser) -> tuple[str, str, str]:
    """Return the actual credential fields and login submit control."""
    user_name = next(
        (
            name
            for name, input_type in form.input_types.items()
            if input_type in {"text", "email"}
            and any(key in name.casefold() for key in ("email", "user", "login", "jmeno"))
        ),
        None,
    )
    password_name = next(
        (name for name, input_type in form.input_types.items() if input_type == "password"),
        None,
    )
    submit_name = next(
        (
            name
            for name in form.submit_names
            if form.input_types.get(name) == "submit"
            and "login" in name.casefold()
            and "logout" not in name.casefold()
        ),
        None,
    )
    if not user_name or not password_name or not submit_name:
        raise VsChrudimProtocolError("Login form fields or submit button were not found")
    return user_name, password_name, submit_name

def _normalized(value: str) -> str:
    return _clean_text(value).casefold()

def _clean_text(value: str) -> str:
    """Normalize portal whitespace without changing user-visible capitalization."""
    return " ".join(unescape(value).replace("\xa0", " ").split())

def _postback(value: str) -> tuple[str, str] | None:
    match = re.search(
        r"__doPostBack\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)",
        value,
        re.I,
    )
    return match.groups() if match else None

class _ConsumptionPlaceGridParser(HTMLParser):
    """Read the verified WebForms grid like the working WebDownloader DOM code."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found = False
        self.rows: list[tuple[list[str], tuple[str, str] | None]] = []
        self._table_depth = 0
        self._row_depth = 0
        self._cell_depth = 0
        self._cells: list[str] = []
        self._cell_text: list[str] = []
        self._cell_hidden = False
        self._row_postback: tuple[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.casefold(): value or "" for name, value in attrs}
        if tag == "table":
            identity = values.get("id", "") or values.get("name", "")
            if self._table_depth:
                self._table_depth += 1
            elif identity.casefold().endswith("gvconsumptionplaces"):
                self.found = True
                self._table_depth = 1
            return
        if not self._table_depth:
            return
        if tag == "tr":
            self._row_depth += 1
            if self._row_depth == 1:
                self._cells = []
                self._row_postback = _postback(values.get("onclick", ""))
            return
        if not self._row_depth:
            return
        if self._row_postback is None:
            self._row_postback = _postback(
                values.get("onclick", "") or values.get("href", "")
            )
        if tag == "td":
            self._cell_depth += 1
            if self._cell_depth == 1:
                classes = values.get("class", "").casefold().split()
                style = values.get("style", "").casefold().replace(" ", "")
                self._cell_hidden = (
                    "hidden" in classes
                    or "hidden" in values
                    or "display:none" in style
                )
                self._cell_text = []
            return
        if self._cell_depth and tag in {"br", "div", "li", "p"}:
            self._cell_text.append(" ")

    def handle_data(self, data: str) -> None:
        if self._table_depth and self._cell_depth and not self._cell_hidden:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._table_depth:
            return
        if tag == "td" and self._cell_depth:
            self._cell_depth -= 1
            if self._cell_depth == 0 and not self._cell_hidden:
                self._cells.append(_clean_text("".join(self._cell_text)))
            return
        if tag == "tr" and self._row_depth:
            self._row_depth -= 1
            if self._row_depth == 0 and self._cells:
                self.rows.append((self._cells, self._row_postback))
            return
        if tag == "table":
            self._table_depth -= 1

def _parse_consumption_place_grid(
    html: str,
) -> _ConsumptionPlaceGridParser:
    parser = _ConsumptionPlaceGridParser()
    parser.feed(html)
    if not parser.found:
        raise VsChrudimProtocolError("Consumption-place grid was not found")
    return parser

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
    grid = _parse_consumption_place_grid(html)
    places: list[ConsumptionPlace] = []
    seen: set[str] = set()
    for cells, _ in grid.rows:
        if len(cells) < 4 or not cells[0]:
            continue
        evidence_key = _normalized(cells[0])
        if evidence_key in seen:
            continue
        seen.add(evidence_key)
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
        user_name, password_name, submit_name = _login_field_names(form)
        payload = {name: value for name, value in form.inputs.items() if name.startswith("__")}
        payload.update({user_name: self._username, password_name: self._password})
        payload[submit_name] = form.inputs.get(submit_name, "")
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
        grid = _parse_consumption_place_grid(html)
        expected_evidence = re.sub(r"\D", "", place.evidence_number)
        expected_technical = re.sub(r"\D", "", place.technical_number)
        for row_index, (cells, postback) in enumerate(grid.rows):
            if len(cells) < 2:
                continue
            evidence = re.sub(r"\D", "", cells[0])
            technical = re.sub(r"\D", "", cells[1])
            if evidence == expected_evidence or (
                expected_technical and technical == expected_technical
            ):
                target, argument = postback or (
                    "ctl00$ctl00$ContentPlaceHolder1Common$ContentPlaceHolder1$gvConsumptionPlaces",
                    f"Show${row_index}",
                )
                form = _parse_form(html)
                payload = {name: value for name, value in form.inputs.items() if name.startswith("__")}
                payload["__EVENTTARGET"], payload["__EVENTARGUMENT"] = target, argument
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
