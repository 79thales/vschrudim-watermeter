"""Async client for the VS Chrudim ASP.NET WebForms customer portal.

The portal does not publish a supported machine API.  This client deliberately
replays only the WebForms forms and links actually supplied by the logged-in
portal; it does not invent undocumented endpoints or persist session cookies.
"""
from __future__ import annotations

import asyncio

from dataclasses import dataclass, replace
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
import math
import re
from collections.abc import Awaitable, Callable
from typing import Final, TypeVar
import unicodedata
from urllib.parse import urljoin

import aiohttp

from .calculation import latest_consumption
from .const import BASE_URL, PLACES_URL, READINGS_URL
from .models import (
    ConsumptionPlace,
    DownloadMetadata,
    MeterReading,
    WaterMeterData,
)

_DATE_FORMATS: Final = ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y")
_ResultT = TypeVar("_ResultT")
_REQUEST_TIMEOUT_SECONDS: Final = 45
_MAX_RESPONSE_BYTES: Final = 12 * 1024 * 1024
_RESPONSE_READ_CHUNK_BYTES: Final = 64 * 1024

class VsChrudimError(Exception):
    """Base portal error."""

class VsChrudimAuthError(VsChrudimError):
    """Credentials are invalid or the authenticated session expired."""

class VsChrudimProtocolError(VsChrudimError):
    """The portal markup changed or did not contain expected data."""

    def __init__(
        self,
        message: str,
        *,
        download_metadata: DownloadMetadata | None = None,
    ) -> None:
        super().__init__(message)
        self.download_metadata = download_metadata or DownloadMetadata()

class VsChrudimConnectionError(VsChrudimError):
    """The portal could not be reached."""


_MISSING_EXPORT_ERROR: Final = (
    "The portal exposed no recognizable CSV link or WebForms export control"
)


def is_empty_history_boundary_error(
    error: VsChrudimError,
    *,
    requested_to: date,
    earliest_reading: date | None,
) -> bool:
    """Identify the portal's empty-period response before available history.

    The measured-states page omits its export action when a filtered period has
    no readings. Only accept that response as the history boundary after a
    newer request has already established the earliest available reading.
    """
    return (
        earliest_reading is not None
        and requested_to < earliest_reading
        and str(error) == _MISSING_EXPORT_ERROR
    )

class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action = ""
        self.method = "GET"
        self.has_form = False
        self.inputs: dict[str, str] = {}
        # ASP.NET renders some read-only controls with a stable ``id`` but
        # without the expected ``name``. Keep these separate from submitted
        # successful controls: IDs are only used to verify the already
        # selected consumption-place context.
        self.context_values_by_id: dict[str, str] = {}
        self._context_elements: list[tuple[str, str, list[str]]] = []
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
            self.has_form = True
            self.action = values.get("action", "") or ""
            self.method = (values.get("method", "GET") or "GET").upper()
        elif tag == "input":
            name = values.get("name")
            value = values.get("value", "") or ""
            input_id = values.get("id")
            if input_id and input_id.casefold().endswith(("edcpid", "edcpevnum")):
                self.context_values_by_id[input_id] = value
            if name:
                input_type = (values.get("type", "text") or "text").lower()
                self.inputs[name] = value
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

        context_id = values.get("id", "") or ""
        if tag != "input" and context_id.casefold().endswith(
            ("edcpid", "edcpevnum")
        ):
            self._context_elements.append((tag, context_id, []))

    def handle_data(self, data: str) -> None:
        for _, _, text in self._context_elements:
            text.append(data)
        if self._href:
            self._text.append(data)
        if self._button_name:
            self._button_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._context_elements and tag == self._context_elements[-1][0]:
            _, context_id, text = self._context_elements.pop()
            self.context_values_by_id[context_id] = " ".join(text).strip()
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


def _control_values_ending(values: dict[str, str], suffix: str) -> tuple[str, ...]:
    """Return every control value whose stable name or ID suffix matches."""
    suffix = suffix.casefold()
    return tuple(
        value for name, value in values.items() if name.casefold().endswith(suffix)
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


def _portal_page_features(html: str) -> tuple[str, ...]:
    """Return a fixed, privacy-safe profile of recognized page structures.

    The profile deliberately stores neither raw HTML nor control names or
    values. It helps distinguish a provider markup change from a transport
    failure while remaining safe to include in downloaded diagnostics.
    """
    form = _parse_form(html)
    features: set[str] = set()
    if form.has_form:
        features.add("webforms_form")
    if re.search(r"<\s*table\b", html, re.I):
        features.add("html_table")
    for text, href in form.links:
        if _export_candidate_score(text + " " + href) < 45:
            continue
        if href.casefold().startswith("javascript:"):
            if _postback(href):
                features.add("csv_export_postback")
        else:
            features.add("csv_export_link")
    if any(
        _export_candidate_score(form.submit_descriptions.get(name, name)) >= 45
        for name in form.submit_names
    ):
        features.add("csv_export_submit")
    return tuple(sorted(features))


def _reading_quality_flags(readings: tuple[MeterReading, ...]) -> tuple[str, ...]:
    """Describe noteworthy readings without changing accepted portal data.

    A lower register value can be a legitimate meter replacement or a portal
    correction, so this deliberately reports rather than rejects it. The
    existing statistics writer remains the sole authority for baseline logic.
    """
    flags: set[str] = set()
    previous_timestamp: datetime | None = None
    previous_state: float | None = None
    for reading in readings:
        state = reading.meter_state_m3
        if not math.isfinite(state):
            flags.add("non_finite_meter_state")
        elif state < 0:
            flags.add("negative_meter_state")
        if previous_timestamp is not None and reading.timestamp <= previous_timestamp:
            flags.add("timestamps_not_strictly_increasing")
        if (
            previous_state is not None
            and math.isfinite(previous_state)
            and math.isfinite(state)
            and state < previous_state
        ):
            flags.add("meter_state_decreased")
        previous_timestamp = reading.timestamp
        previous_state = state
    return tuple(sorted(flags))

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


@dataclass(slots=True)
class _HtmlTableContext:
    """One table currently being parsed, including layout-nested tables."""

    rows: list[list[str]]
    row_depth: int = 0
    cell_depth: int = 0
    cells: list[str] | None = None
    cell_text: list[str] | None = None


class _ReadingsTableParser(HTMLParser):
    """Extract every table while preserving text inside wrapped table cells."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_stack: list[_HtmlTableContext] = []

    @property
    def _current(self) -> _HtmlTableContext | None:
        return self._table_stack[-1] if self._table_stack else None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table_stack.append(_HtmlTableContext(rows=[]))
            return
        context = self._current
        if context is None:
            return
        if tag == "tr":
            context.row_depth += 1
            if context.row_depth == 1:
                context.cells = []
            return
        if context.row_depth == 1 and tag in {"th", "td"}:
            context.cell_depth += 1
            if context.cell_depth == 1:
                context.cell_text = []
            return
        if context.cell_depth and tag in {"br", "div", "p", "li"}:
            assert context.cell_text is not None
            context.cell_text.append(" ")

    def handle_data(self, data: str) -> None:
        context = self._current
        if context and context.row_depth == 1 and context.cell_depth:
            assert context.cell_text is not None
            context.cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        context = self._current
        if context is None:
            return
        if tag in {"th", "td"} and context.row_depth == 1 and context.cell_depth:
            context.cell_depth -= 1
            if context.cell_depth == 0:
                assert context.cells is not None
                assert context.cell_text is not None
                context.cells.append(_clean_text("".join(context.cell_text)))
                context.cell_text = None
            return
        if tag == "tr" and context.row_depth:
            context.row_depth -= 1
            if context.row_depth == 0 and context.cells:
                context.rows.append(context.cells)
                context.cells = None
            return
        if tag in {"div", "p", "li"} and context.cell_depth:
            assert context.cell_text is not None
            context.cell_text.append(" ")
            return
        if tag == "table":
            finished = self._table_stack.pop()
            if finished.rows:
                self.tables.append(finished.rows)


def _parse_consumption_place_grid(
    html: str,
) -> _ConsumptionPlaceGridParser:
    parser = _ConsumptionPlaceGridParser()
    parser.feed(html)
    if not parser.found:
        raise VsChrudimProtocolError("Consumption-place grid was not found")
    return parser


def _matches_selected_consumption_place(
    html: str,
    place: ConsumptionPlace,
) -> bool:
    """Verify the consumption-place context stored by the portal session.

    The working WebDownloader accepts a missing place grid only when the
    rendered detail page exposes ``edCpId``/``edCpEvNum`` controls matching
    the requested place. The portal may expose those stable suffixes in an
    HTML ``name`` or an ``id`` (including rendered text); neither value is
    sent or persisted here. This prevents a remembered session context from
    silently returning readings for another customer place.
    """
    form = _parse_form(html)
    expected_evidence = re.sub(r"\D", "", place.evidence_number)
    expected_technical = re.sub(r"\D", "", place.technical_number)
    evidence_values = _control_values_ending(
        form.inputs, "edCpId"
    ) + _control_values_ending(form.context_values_by_id, "edCpId")
    technical_values = _control_values_ending(
        form.inputs, "edCpEvNum"
    ) + _control_values_ending(form.context_values_by_id, "edCpEvNum")
    return any(
        expected_evidence and re.sub(r"\D", "", value) == expected_evidence
        for value in evidence_values
    ) or any(
        expected_technical and re.sub(r"\D", "", value) == expected_technical
        for value in technical_values
    )

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
    # A date-shaped value and a number are not sufficient evidence: the page
    # contains layout and filter tables too.  Require a deterministic header
    # row with a time/date and a meter-state column, then use those exact
    # columns.  WebForms commonly wraps headings in spans and places the data
    # table inside another layout table, both of which _ReadingsTableParser
    # deliberately preserves.
    candidates: list[tuple[tuple[int, int, int, int], list[MeterReading]]] = []
    for table_index, table in enumerate(parser.tables):
        for header_index, header_row in enumerate(table[:5]):
            headers = [_csv_header_key(value) for value in header_row]
            date_index = next(
                (
                    index
                    for index, value in enumerate(headers)
                    if "cas" in value or "datum" in value
                ),
                None,
            )
            state_index = next(
                (
                    index
                    for index, value in enumerate(headers)
                    if "stav" in value or "odecet" in value
                ),
                None,
            )
            if date_index is None or state_index is None:
                continue
            meter_index = next(
                (index for index, value in enumerate(headers) if "meridlo" in value),
                None,
            )
            readings: dict[datetime, MeterReading] = {}
            for row in table[header_index + 1 :]:
                if max(date_index, state_index) >= len(row):
                    continue
                timestamp = _parse_table_datetime(row[date_index])
                state = _parse_table_number(row[state_index])
                if timestamp is None or state is None:
                    continue
                meter = (
                    row[meter_index]
                    if meter_index is not None and meter_index < len(row)
                    else ""
                )
                readings[timestamp] = MeterReading(timestamp, state, meter)
            if readings:
                candidates.append(
                    (
                        (
                            len(readings),
                            1 if meter_index is not None else 0,
                            -table_index,
                            -header_index,
                        ),
                        sorted(readings.values(), key=lambda item: item.timestamp),
                    )
                )
    return max(candidates, default=((0, 0, 0, 0), []), key=lambda item: item[0])[1]


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
        # Do not leave a stale success flag behind if a fresh login is rejected.
        self._logged_in = False
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
        return await self._async_retry_expired_session(self._async_get_places_once)

    async def _async_get_places_once(self) -> list[ConsumptionPlace]:
        html, _ = await self._request_text("GET", PLACES_URL)
        if self._looks_like_login(html):
            self._logged_in = False
            raise VsChrudimAuthError("Authenticated session expired")
        return parse_consumption_places(html)

    async def async_get_data(self, place: ConsumptionPlace) -> WaterMeterData:
        """Select a place then follow its actual 'Naměřené stavy' menu link/postback."""
        await self._ensure_login()
        return await self._async_retry_expired_session(
            lambda: self._async_get_data_once(place)
        )

    async def _async_get_data_once(self, place: ConsumptionPlace) -> WaterMeterData:
        html, url = await self._request_text("GET", PLACES_URL)
        selected_html, selected_url = await self._select_place(html, url, place)
        readings_html, readings_url = await self._open_measured_states(selected_html, selected_url)
        readings, metadata = await self._read_readings_with_metadata(
            readings_html, readings_url
        )
        return WaterMeterData(
            place,
            readings,
            latest_consumption(readings),
            download_metadata=metadata,
        )

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
        return await self._async_retry_expired_session(
            lambda: self._async_get_history_once(place, date_from, date_to)
        )

    async def _async_get_history_once(
        self,
        place: ConsumptionPlace,
        date_from: date,
        date_to: date,
    ) -> tuple[MeterReading, ...]:
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
        """Compatibility wrapper for callers that only need the readings."""
        readings, _ = await self._read_readings_with_metadata(html, url)
        return readings

    async def _read_readings_with_metadata(
        self, html: str, url: str
    ) -> tuple[tuple[MeterReading, ...], DownloadMetadata]:
        """Prefer the verified export and fall back to the rendered data table."""
        page_features = _portal_page_features(html)
        try:
            csv, metadata = await self._download_csv_with_metadata(html, url)
        except VsChrudimProtocolError as err:
            table_readings = tuple(parse_readings_html(html))
            failure_metadata = replace(
                err.download_metadata,
                portal_page_features=page_features,
                html_table_detected=bool(table_readings),
                reading_quality_flags=_reading_quality_flags(table_readings),
            )
            if table_readings:
                metadata = replace(
                    failure_metadata,
                    source="html_table",
                )
                return table_readings, metadata
            raise VsChrudimProtocolError(
                str(err), download_metadata=failure_metadata
            ) from err
        readings = tuple(parse_readings_csv(csv))
        if readings:
            return readings, replace(
                metadata,
                portal_page_features=page_features,
                reading_quality_flags=_reading_quality_flags(readings),
            )
        raise VsChrudimProtocolError(
            "The portal returned a CSV without valid water readings",
            download_metadata=replace(
                metadata,
                portal_page_features=page_features,
            ),
        )

    async def _ensure_login(self) -> None:
        if not self._logged_in:
            await self.async_login()

    async def _async_retry_expired_session(
        self,
        request: Callable[[], Awaitable[_ResultT]],
    ) -> _ResultT:
        """Refresh an expired portal session once and replay the full request.

        The initial login is deliberately outside this helper: an invalid
        username or password must reach Home Assistant as a normal reauth
        error, rather than be retried. A second expired-session response is
        likewise propagated so portal failures cannot loop indefinitely.
        """
        try:
            return await request()
        except VsChrudimAuthError:
            self._logged_in = False
            await self.async_login()
            return await request()

    async def _select_place(self, html: str, url: str, place: ConsumptionPlace) -> tuple[str, str]:
        if self._looks_like_login(html):
            self._logged_in = False
            raise VsChrudimAuthError("Authenticated session expired")
        try:
            grid = _parse_consumption_place_grid(html)
        except VsChrudimProtocolError:
            # The portal sometimes redirects ConsumptionPlaceList.aspx to the
            # already selected detail/reporting context. Reuse it only after
            # verifying both the portal control and the configured place.
            if _matches_selected_consumption_place(html, place):
                return html, url
            raise VsChrudimProtocolError(
                "The consumption-place list was unavailable and the portal "
                "did not confirm the configured place"
            ) from None
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

        # WebDownloader changes the select and then submits the actual browser
        # form. FormData includes every successful input, not only WebForms'
        # hidden state and select controls.
        period_payload = _complete_form_payload(form)
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
        # Clicking btnRenew in WebDownloader submits the complete form as well.
        # Retain unrelated portal inputs because server controls may depend on
        # them when producing the filtered result and its export action.
        range_payload = _complete_form_payload(form)
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
        """Compatibility wrapper returning only validated CSV content."""
        content, _ = await self._download_csv_with_metadata(html, url)
        return content

    async def _download_csv_with_metadata(
        self, html: str, url: str
    ) -> tuple[str, DownloadMetadata]:
        form = _parse_form(html)
        candidates: list[tuple[int, str, str, str]] = []
        for text, href in form.links:
            score = _export_candidate_score(text + " " + href)
            if score >= 45:
                postback = _postback(href)
                if href.casefold().startswith("javascript:"):
                    # A verified LinkButton is a supported WebForms action;
                    # arbitrary JavaScript is deliberately never evaluated.
                    if postback:
                        candidates.append((score, "postback", postback[0], postback[1]))
                else:
                    candidates.append((score, "link", href, ""))
        for name in form.submit_names:
            score = _export_candidate_score(form.submit_descriptions.get(name, name))
            if score >= 45:
                candidates.append((score, "submit", name, ""))

        attempted = 0
        found = len(candidates)
        for _, kind, value, argument in sorted(candidates, reverse=True):
            attempted += 1
            if kind == "link":
                content, _ = await self._request_text("GET", urljoin(url, value))
                source = "csv_link"
            elif kind == "postback":
                payload = _complete_form_payload(form)
                payload["__EVENTTARGET"] = value
                payload["__EVENTARGUMENT"] = argument
                content, _ = await self._request_text(
                    "POST",
                    urljoin(url, form.action or url),
                    data=payload,
                )
                source = "csv_postback"
            else:
                payload = _complete_form_payload(form)
                payload[value] = form.inputs.get(value, "")
                content, _ = await self._request_text(
                    form.method if form.method in {"GET", "POST"} else "POST",
                    urljoin(url, form.action or url),
                    data=payload,
                )
                source = "csv_submit"
            if self._looks_like_login(content):
                self._logged_in = False
                raise VsChrudimAuthError("Authenticated session expired")
            if not _has_readings_csv_header(content):
                continue
            return content, DownloadMetadata(
                source=source,
                export_candidates_found=found,
                export_candidates_attempted=attempted,
            )
        metadata = DownloadMetadata(
            export_candidates_found=found,
            export_candidates_attempted=attempted,
        )
        if attempted:
            raise VsChrudimProtocolError(
                f"The portal returned no valid water-reading CSV from {attempted} export candidate(s)",
                download_metadata=metadata,
            )
        raise VsChrudimProtocolError(
            _MISSING_EXPORT_ERROR,
            download_metadata=metadata,
        )

    @staticmethod
    def _looks_like_login(html: str) -> bool:
        value = html.casefold()
        return "password" in value and ("login" in value or "přihlás" in value)

    @staticmethod
    async def _read_bounded_response_text(response: aiohttp.ClientResponse) -> str:
        """Read a normal portal response without accepting unbounded content."""
        content_length = response.content_length
        if content_length is not None and content_length > _MAX_RESPONSE_BYTES:
            raise VsChrudimProtocolError("Portal response exceeded the safe size limit")
        # ``StreamReader.read(n)`` may return one currently available network
        # block rather than the complete response. Read until EOF explicitly;
        # otherwise a split WebForms page can lose its place grid or controls.
        body = bytearray()
        while True:
            chunk = await response.content.read(_RESPONSE_READ_CHUNK_BYTES)
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise VsChrudimProtocolError(
                    "Portal response exceeded the safe size limit"
                )
        try:
            encoding = response.charset or response.get_encoding()
        except (LookupError, RuntimeError):
            encoding = "utf-8"
        try:
            return body.decode(encoding or "utf-8", errors="replace")
        except LookupError:
            # A malformed charset declaration must not turn a valid portal
            # response into an unhandled decoder error.
            return body.decode("utf-8", errors="replace")

    async def _request_text(self, method: str, url: str, data: dict[str, str] | None = None) -> tuple[str, str]:
        try:
            timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
            async with self._session.request(
                method,
                url,
                data=data,
                allow_redirects=True,
                timeout=timeout,
            ) as response:
                if response.status >= 500:
                    raise VsChrudimConnectionError(f"Portal returned HTTP {response.status}")
                if response.status >= 400:
                    raise VsChrudimProtocolError(f"Portal returned HTTP {response.status}")
                text = await self._read_bounded_response_text(response)
                return text, str(response.url)
        except asyncio.TimeoutError as err:
            raise VsChrudimConnectionError(
                "VS Chrudim portal request timed out"
            ) from err
        except aiohttp.ClientError as err:
            raise VsChrudimConnectionError("Unable to connect to VS Chrudim portal") from err
