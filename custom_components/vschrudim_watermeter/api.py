"""Async client for the VS Chrudim ASP.NET WebForms customer portal.

The portal does not publish a supported machine API.  This client deliberately
replays only the WebForms forms and links actually supplied by the logged-in
portal; it does not invent undocumented endpoints or persist session cookies.
"""
from __future__ import annotations

from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
import re
from typing import Final
import unicodedata
from urllib.parse import urljoin

import aiohttp

from .calculation import latest_consumption
from .const import BASE_URL, PLACES_URL, READINGS_URL
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
        self.submit_descriptions: dict[str, str] = {}
        self.links: list[tuple[str, str]] = []
        self.selects: dict[str, str] = {}
        self._select_name = ""
        self._selected_option = ""
        self._first_option = ""
        self._href = ""
        self._text: list[str] = []
        self._button_name = ""
        self._button_value = ""
        self._button_text: list[str] = []

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
                    self.submit_descriptions[name] = " ".join(
                        filter(
                            None,
                            (
                                name,
                                values.get("value"),
                                values.get("title"),
                                values.get("aria-label"),
                                values.get("alt"),
                                values.get("onclick"),
                            ),
                        )
                    )
        elif tag == "a":
            self._href = values.get("href", "") or ""
            self._text = []
        elif tag == "button":
            self._button_name = values.get("name", "") or ""
            self._button_value = values.get("value", "") or ""
            self._button_text = [
                value
                for value in (
                    self._button_name,
                    self._button_value,
                    values.get("title"),
                    values.get("aria-label"),
                    values.get("onclick"),
                )
                if value
            ]
        elif tag == "select":
            self._select_name = values.get("name", "") or ""
            self._selected_option = ""
            self._first_option = ""
        elif tag == "option" and self._select_name:
            option_value = values.get("value", "") or ""
            if not self._first_option:
                self._first_option = option_value
            if "selected" in values:
                self._selected_option = option_value

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)
        if self._button_name:
            self._button_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((" ".join(self._text).strip(), self._href))
            self._href = ""
            self._text = []
        elif tag == "button" and self._button_name:
            self.inputs[self._button_name] = self._button_value
            self.input_types[self._button_name] = "button"
            self.submit_names.append(self._button_name)
            self.submit_descriptions[self._button_name] = " ".join(
                self._button_text
            )
            self._button_name = ""
            self._button_value = ""
            self._button_text = []
        elif tag == "select" and self._select_name:
            self.selects[self._select_name] = (
                self._selected_option or self._first_option
            )
            self._select_name = ""
            self._selected_option = ""
            self._first_option = ""

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
        r"__doPostBack\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*\)",
        value,
        re.I,
    )
    return match.groups() if match else None

def _looks_like_readings_page(html: str) -> bool:
    """Return whether the verified readings filter is present."""
    return bool(
        re.search(
            r"(?:id|name)=[\"'][^\"']*(?:edGraphLength|GraphFilter1_(?:btnRenew|edDateFrom|hfDateFrom))[^\"']*[\"']",
            html,
            re.I,
        )
    )


def _control_ending(values: dict[str, str], suffix: str) -> str:
    """Find a WebForms control by its stable name suffix."""
    suffix = suffix.casefold()
    return next(
        (name for name in values if name.casefold().endswith(suffix)),
        "",
    )


def _webforms_payload(form: _FormParser) -> dict[str, str]:
    """Build a postback payload from hidden fields and selected lists."""
    payload = {
        name: value
        for name, value in form.inputs.items()
        if form.input_types.get(name) == "hidden"
    }
    payload.update(form.selects)
    return payload


def _complete_form_payload(form: _FormParser) -> dict[str, str]:
    """Build the successful-control payload used by browser FormData."""
    payload = {
        name: value
        for name, value in form.inputs.items()
        if form.input_types.get(name) not in {"submit", "image", "button"}
    }
    payload.update(form.selects)
    return payload


def _export_candidate_score(value: str) -> int:
    """Score export controls using the same signals as WebDownloader."""
    normalized = _normalized(value)
    score = 0
    if re.search(r"\.csv(?:$|[?&#])", normalized):
        score += 100
    if "documentshow.aspx" in normalized:
        score += 100
    if "csv" in normalized:
        score += 80
    if "stáhn" in normalized or "stahn" in normalized:
        score += 50
    if "export" in normalized or "download" in normalized:
        score += 45
    if any(word in normalized for word in ("stav", "data", "soubor")):
        score += 20
    return score

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


class _ReadingsTableParser(HTMLParser):
    """Extract HTML tables using the same fallback principle as WebDownloader."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._rows: list[list[str]] = []
        self._row_depth = 0
        self._cells: list[str] = []
        self._cell_depth = 0
        self._cell_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            if self._table_depth == 0:
                self._rows = []
            self._table_depth += 1
            return
        if self._table_depth != 1:
            return
        if tag == "tr":
            self._row_depth += 1
            if self._row_depth == 1:
                self._cells = []
            return
        if self._row_depth == 1 and tag in {"th", "td"}:
            self._cell_depth += 1
            if self._cell_depth == 1:
                self._cell_text = []
        elif self._cell_depth and tag == "br":
            self._cell_text.append(" ")

    def handle_data(self, data: str) -> None:
        if self._table_depth == 1 and self._row_depth == 1 and self._cell_depth:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._table_depth:
            return
        if self._table_depth == 1 and tag in {"th", "td"} and self._cell_depth:
            self._cell_depth -= 1
            if self._cell_depth == 0:
                self._cells.append(_clean_text("".join(self._cell_text)))
            return
        if self._table_depth == 1 and tag == "tr" and self._row_depth:
            self._row_depth -= 1
            if self._row_depth == 0 and self._cells:
                self._rows.append(self._cells)
            return
        if tag == "table":
            self._table_depth -= 1
            if self._table_depth == 0 and self._rows:
                self.tables.append(self._rows)


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


def _csv_header_key(value: str) -> str:
    """Normalize quoted Czech CSV headers for structural validation."""
    value = value.lstrip("\ufeff").strip().strip('"')
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value).casefold()
        if not unicodedata.combining(character)
    )


def _has_readings_csv_header(content: str) -> bool:
    """Return whether content has the columns required by the parser."""
    first_line = next(
        (
            line.strip()
            for line in content.replace("\r", "").split("\n")
            if line.strip()
        ),
        "",
    )
    headers = [_csv_header_key(value) for value in first_line.split(";")]
    return any(value in {"cas", "datum"} for value in headers) and any(
        "stav" in value for value in headers
    )

def parse_readings_csv(content: str) -> list[MeterReading]:
    """Parse the documented ``MERIDLO;CAS;STAV`` CSV export defensively."""
    lines = [line.strip() for line in content.replace("\r", "").split("\n") if line.strip()]
    if not lines:
        return []
    headers = [_csv_header_key(value) for value in lines[0].split(";")]
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


def _parse_table_datetime(value: str) -> datetime | None:
    cleaned = _clean_text(value)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def _parse_table_number(value: str) -> float | None:
    cleaned = _clean_text(value).replace(" ", "")
    match = re.search(r"-?\d+(?:[.,]\d+)?", cleaned)
    return _number(match.group(0)) if match else None


def parse_readings_html(content: str) -> list[MeterReading]:
    """Parse the measured-state HTML table when no export control is rendered."""
    parser = _ReadingsTableParser()
    parser.feed(content)
    best_headers: list[str] = []
    best_rows: list[list[str]] = []
    for table in parser.tables:
        if not table:
            continue
        dated_rows = [row for row in table[1:] if any(_parse_table_datetime(cell) for cell in row)]
        if len(dated_rows) > len(best_rows):
            best_headers = table[0]
            best_rows = dated_rows
    if not best_rows:
        return []

    headers = [_csv_header_key(value) for value in best_headers]
    date_index = next(
        (index for index, value in enumerate(headers) if "cas" in value or "datum" in value),
        None,
    )
    state_index = next(
        (index for index, value in enumerate(headers) if "stav" in value or "odecet" in value),
        None,
    )
    meter_index = next(
        (index for index, value in enumerate(headers) if "meridlo" in value),
        None,
    )
    readings: dict[datetime, MeterReading] = {}
    for row in best_rows:
        if date_index is not None and date_index < len(row):
            timestamp = _parse_table_datetime(row[date_index])
        else:
            timestamp = next(
                (
                    parsed
                    for cell in row
                    if (parsed := _parse_table_datetime(cell)) is not None
                ),
                None,
            )
        if timestamp is None:
            continue
        state = (
            _parse_table_number(row[state_index])
            if state_index is not None and state_index < len(row)
            else next(
                (
                    value
                    for cell in row
                    if _parse_table_datetime(cell) is None
                    and (value := _parse_table_number(cell)) is not None
                ),
                None,
            )
        )
        if state is None:
            continue
        meter = row[meter_index] if meter_index is not None and meter_index < len(row) else ""
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
        readings = await self._read_readings(readings_html, readings_url)
        return WaterMeterData(place, readings, latest_consumption(readings))

    async def async_get_history(
        self,
        place: ConsumptionPlace,
        date_from: date,
        date_to: date,
    ) -> tuple[MeterReading, ...]:
        """Download a verified custom date range from the measured-states page."""
        if date_to < date_from:
            raise ValueError("date_to must not precede date_from")
        await self._ensure_login()
        html, url = await self._request_text("GET", PLACES_URL)
        selected_html, selected_url = await self._select_place(html, url, place)
        readings_html, readings_url = await self._open_measured_states(
            selected_html, selected_url
        )
        filtered_html, filtered_url = await self._set_custom_range(
            readings_html,
            readings_url,
            date_from,
            date_to,
        )
        readings = await self._read_readings(filtered_html, filtered_url)
        inside = tuple(
            reading
            for reading in readings
            if date_from <= reading.timestamp.date() <= date_to
        )
        if readings and not inside:
            raise VsChrudimProtocolError(
                "The portal CSV did not overlap the requested custom date range"
            )
        return inside

    async def _read_readings(self, html: str, url: str) -> tuple[MeterReading, ...]:
        """Prefer the verified export and fall back to the rendered data table."""
        try:
            csv = await self._download_csv(html, url)
        except VsChrudimProtocolError:
            table_readings = tuple(parse_readings_html(html))
            if table_readings:
                return table_readings
            raise
        return tuple(parse_readings_csv(csv))

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
        if _looks_like_readings_page(html):
            return html, url

        # WebDownloader uses this address after the selected place has been
        # stored in the authenticated portal session. Never accept the page
        # unless its verified readings filter is actually present.
        direct_html, direct_url = await self._request_text("GET", READINGS_URL)
        if self._looks_like_login(direct_html):
            self._logged_in = False
            raise VsChrudimAuthError("Authenticated session expired")
        if _looks_like_readings_page(direct_html):
            return direct_html, direct_url

        form = _parse_form(html)
        for text, href in form.links:
            label = _normalized(text)
            if label != "naměřené stavy" and "profiledata" not in href.casefold():
                continue
            if postback := _postback(href):
                payload = {
                    name: value
                    for name, value in form.inputs.items()
                    if name.startswith("__")
                }
                payload["__EVENTTARGET"], payload["__EVENTARGUMENT"] = postback
                candidate = await self._request_text(
                    "POST",
                    urljoin(url, form.action or url),
                    data=payload,
                )
            elif not href.casefold().startswith("javascript:"):
                candidate = await self._request_text("GET", urljoin(url, href))
            else:
                continue
            if _looks_like_readings_page(candidate[0]):
                return candidate
        raise VsChrudimProtocolError(
            "The portal did not expose the verified readings filter"
        )

    async def _set_custom_range(
        self,
        html: str,
        url: str,
        date_from: date,
        date_to: date,
    ) -> tuple[str, str]:
        """Replay the WebDownloader custom-range WebForms sequence."""
        form = _parse_form(html)
        period_name = _control_ending(form.selects, "$edGraphLength")
        if not period_name:
            raise VsChrudimProtocolError(
                "The portal did not expose the measured-state period selector"
            )

        period_payload = _webforms_payload(form)
        period_payload[period_name] = "U"
        period_payload["__EVENTTARGET"] = period_name
        period_payload["__EVENTARGUMENT"] = ""
        html, url = await self._request_text(
            "POST",
            urljoin(url, form.action or url),
            data=period_payload,
        )

        form = _parse_form(html)
        period_name = _control_ending(form.selects, "$edGraphLength")
        date_from_name = _control_ending(form.inputs, "$edDateFrom")
        date_to_name = _control_ending(form.inputs, "$edDateTo")
        hidden_from_name = _control_ending(form.inputs, "$hfDateFrom")
        hidden_to_name = _control_ending(form.inputs, "$hfDateTo")
        renew_name = _control_ending(form.inputs, "$btnRenew")
        if not all(
            (
                period_name,
                date_from_name,
                date_to_name,
                hidden_from_name,
                hidden_to_name,
                renew_name,
            )
        ):
            raise VsChrudimProtocolError(
                "The portal did not expose all custom date-range controls"
            )

        formatted_from = date_from.strftime("%d.%m.%Y")
        formatted_to = date_to.strftime("%d.%m.%Y")
        range_payload = _webforms_payload(form)
        range_payload.update(
            {
                period_name: "U",
                date_from_name: formatted_from,
                date_to_name: formatted_to,
                hidden_from_name: formatted_from,
                hidden_to_name: formatted_to,
                renew_name: form.inputs.get(renew_name, ""),
            }
        )
        range_payload["__EVENTTARGET"] = ""
        range_payload["__EVENTARGUMENT"] = ""
        response = await self._request_text(
            "POST",
            urljoin(url, form.action or url),
            data=range_payload,
        )
        if self._looks_like_login(response[0]):
            self._logged_in = False
            raise VsChrudimAuthError("Authenticated session expired")
        if not _looks_like_readings_page(response[0]):
            raise VsChrudimProtocolError(
                "The portal did not return the measured-state page for the custom range"
            )
        return response

    async def _download_csv(self, html: str, url: str) -> str:
        form = _parse_form(html)
        candidates: list[tuple[int, str, str]] = []
        for text, href in form.links:
            if href.casefold().startswith("javascript:"):
                continue
            score = _export_candidate_score(text + " " + href)
            if score >= 45:
                candidates.append((score, "link", href))
        for name in form.submit_names:
            score = _export_candidate_score(form.submit_descriptions.get(name, name))
            if score >= 45:
                candidates.append((score, "submit", name))

        attempted = 0
        for _, kind, value in sorted(candidates, reverse=True):
            attempted += 1
            if kind == "link":
                content, _ = await self._request_text("GET", urljoin(url, value))
            else:
                payload = _complete_form_payload(form)
                payload[value] = form.inputs.get(value, "")
                content, _ = await self._request_text(
                    form.method if form.method in {"GET", "POST"} else "POST",
                    urljoin(url, form.action or url),
                    data=payload,
                )
            if self._looks_like_login(content):
                self._logged_in = False
                raise VsChrudimAuthError("Authenticated session expired")
            if not _has_readings_csv_header(content):
                continue
            return content
        if attempted:
            raise VsChrudimProtocolError(
                f"The portal returned no valid water-reading CSV from {attempted} export candidate(s)"
            )
        raise VsChrudimProtocolError(
            "The portal exposed no recognizable CSV link or WebForms export control"
        )

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
