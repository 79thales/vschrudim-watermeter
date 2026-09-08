"""Tests with anonymized, static portal export examples."""
import importlib.util
from datetime import date
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

# Parser tests do not make HTTP calls; provide the minimal runtime module when
# executing outside Home Assistant's dependency environment.
aiohttp = types.ModuleType("aiohttp")
aiohttp.ClientSession = object
aiohttp.ClientError = OSError
sys.modules.setdefault("aiohttp", aiohttp)

PACKAGE = "vschrudim_watermeter"
root = Path(__file__).parents[1] / "custom_components" / PACKAGE
package = types.ModuleType(PACKAGE); package.__path__ = [str(root)]; sys.modules[PACKAGE] = package
for name in ("const", "models", "calculation", "api"):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", root / f"{name}.py")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
api = sys.modules[f"{PACKAGE}.api"]

class ApiParserTests(unittest.TestCase):
    def test_parse_csv_and_deduplicates_timestamp(self):
        readings = api.parse_readings_csv("MERIDLO;CAS;STAV\nA;01.01.2026 00:00;10,000\nA;01.01.2026 01:00;10,125\nA;01.01.2026 01:00;10,130\n")
        self.assertEqual([(item.timestamp.hour, item.meter_state_m3) for item in readings], [(0, 10.0), (1, 10.13)])

    def test_parse_csv_rejects_changed_header(self):
        with self.assertRaises(api.VsChrudimProtocolError):
            api.parse_readings_csv("foo;bar\n1;2")

    def test_parse_csv_accepts_quoted_accented_header_and_bom(self):
        readings = api.parse_readings_csv(
            '\ufeff"MĚŘIDLO";"ČAS";"STAV"\nA;01.01.2026 00:00;10,000\n'
        )

        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0].meter_state_m3, 10.0)

    def test_parse_readings_html_table(self):
        readings = api.parse_readings_html(
            """
            <table><tr><th>Unrelated</th></tr><tr><td>Text</td></tr></table>
            <table id="measurements">
              <tr><th>Měřidlo</th><th>Čas</th><th>Stav vodoměru</th><th>Spotřeba</th></tr>
              <tr><td>A</td><td>01.01.2026 00:00</td><td>10,000 m³</td><td>0,000 m³</td></tr>
              <tr><td>A</td><td>01.01.2026 01:00</td><td>10,125 m³</td><td>0,125 m³</td></tr>
            </table>
            """
        )

        self.assertEqual(
            [(item.timestamp.hour, item.meter_state_m3) for item in readings],
            [(0, 10.0), (1, 10.125)],
        )

    def test_parse_readings_html_nested_webforms_table(self):
        readings = api.parse_readings_html(
            """
            <table class="layout"><tr><td>
              <div><table id="states"><tbody>
                <tr><th><span>Měřidlo</span></th><th><span>Datum a čas</span></th>
                    <th><span>Stav vodoměru</span></th><th>Další údaj</th></tr>
                <tr><td><span>A</span></td><td><span>01.01.2026&nbsp;00:00</span></td>
                    <td><strong>10,250 m³</strong></td><td>ignored</td></tr>
              </tbody></table></div>
            </td></tr></table>
            """
        )

        self.assertEqual([(item.timestamp.hour, item.meter_state_m3) for item in readings], [(0, 10.25)])

    def test_parse_consumption_places(self):
        html = '<table id="x_gvConsumptionPlaces"><tr><th>x</th></tr><tr><td>123</td><td>456</td><td>Example 1</td><td>C-1</td><td>v1</td></tr></table>'
        place = api.parse_consumption_places(html)[0]
        self.assertEqual(place.evidence_number, "123")
        self.assertEqual(place.address, "Example 1")

    def test_parse_consumption_places_ignores_hidden_grid_cells(self):
        html = """
        <table id="ctl00_ctl00_ContentPlaceHolder1Common_ContentPlaceHolder1_gvConsumptionPlaces">
          <tr><th>Internal</th><th>Evidence</th><th>Technical</th><th>Address</th><th>Contract</th></tr>
          <tr onclick="__doPostBack('grid','Show$0')">
            <td class="hidden">internal row key</td>
            <td>123</td><td>456</td><td>Example Address 1</td><td>C-1</td><td>v1</td>
          </tr>
        </table>
        """
        place = api.parse_consumption_places(html)[0]
        self.assertEqual(place.evidence_number, "123")
        self.assertEqual(place.technical_number, "456")
        self.assertEqual(place.address, "Example Address 1")
        self.assertEqual(place.contract, "C-1")
        grid = api._parse_consumption_place_grid(html)
        self.assertEqual(grid.rows[0][1], ("grid", "Show$0"))

    def test_login_uses_btnlogin_not_preceding_language_image(self):
        html = """
        <form method="post" action="./">
          <input type="hidden" name="__VIEWSTATE" value="state">
          <input type="image" name="ctl00$ctl00$imgLangCS">
          <input type="text" name="ctl00$ctl00$lvLoginForm$LoginDialog1$edEmail">
          <input type="password" name="ctl00$ctl00$lvLoginForm$LoginDialog1$edPassword">
          <input type="submit" name="ctl00$ctl00$lvLoginForm$LoginDialog1$btnLogin" value="Vstoupit">
        </form>
        """
        form = api._parse_form(html)
        self.assertEqual(
            api._login_field_names(form),
            (
                "ctl00$ctl00$lvLoginForm$LoginDialog1$edEmail",
                "ctl00$ctl00$lvLoginForm$LoginDialog1$edPassword",
                "ctl00$ctl00$lvLoginForm$LoginDialog1$btnLogin",
            ),
        )

class MeasuredStatesNavigationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _place():
        return api.ConsumptionPlace("123", "456", "Example Address", "C-1", "v1")

    def test_recognizes_verified_selected_place_context(self):
        html = """
        <form>
          <input type="hidden" name="ctl00$Detail$edCpId" value="123">
          <input type="hidden" name="ctl00$Detail$edCpEvNum" value="456">
        </form>
        """

        self.assertTrue(api._matches_selected_consumption_place(html, self._place()))

    def test_recognizes_verified_selected_place_context_by_input_id(self):
        html = """
        <form>
          <input id="ctl00_Detail_edCpId" type="hidden" value="123">
          <input id="ctl00_Detail_edCpEvNum" type="hidden" value="456">
        </form>
        """

        self.assertTrue(api._matches_selected_consumption_place(html, self._place()))

    def test_recognizes_verified_selected_place_context_by_rendered_id(self):
        html = """
        <section id="ctl00_Detail_edCpId"><span>123</span></section>
        <span id="ctl00_Detail_edCpEvNum">456</span>
        """

        self.assertTrue(api._matches_selected_consumption_place(html, self._place()))

    def test_rejects_selected_context_for_another_place(self):
        html = """
        <form>
          <input type="hidden" name="ctl00$Detail$edCpId" value="999">
          <input type="hidden" name="ctl00$Detail$edCpEvNum" value="888">
        </form>
        """

        self.assertFalse(api._matches_selected_consumption_place(html, self._place()))

    async def test_bounded_response_reader_preserves_normal_portal_text(self):
        class Content:
            def __init__(self):
                self.chunks = ["Měřidlo;Čas;Stav".encode(), b""]

            async def read(self, maximum):
                self.maximum = maximum
                return self.chunks.pop(0)

        class Response:
            content_length = len("Měřidlo;Čas;Stav".encode())
            charset = "utf-8"
            content = Content()

            @staticmethod
            def get_encoding():
                return "utf-8"

        text = await api.VsChrudimClient._read_bounded_response_text(Response())

        self.assertEqual(text, "Měřidlo;Čas;Stav")
        self.assertEqual(Response.content.maximum, api._RESPONSE_READ_CHUNK_BYTES)

    async def test_bounded_response_reader_reads_all_network_chunks(self):
        class Content:
            def __init__(self):
                self.chunks = [b"<table id=\"grid\">", b"complete page</table>", b""]
                self.calls = 0

            async def read(self, maximum):
                self.calls += 1
                self.maximum = maximum
                return self.chunks.pop(0)

        class Response:
            content_length = None
            charset = "utf-8"
            content = Content()

            @staticmethod
            def get_encoding():
                return "utf-8"

        text = await api.VsChrudimClient._read_bounded_response_text(Response())

        self.assertEqual(text, '<table id="grid">complete page</table>')
        self.assertEqual(Response.content.calls, 3)
        self.assertEqual(Response.content.maximum, api._RESPONSE_READ_CHUNK_BYTES)

    async def test_bounded_response_reader_rejects_oversized_content(self):
        class Content:
            async def read(self, maximum):
                self.fail("Oversized response must be rejected before reading")

            @staticmethod
            def fail(message):
                raise AssertionError(message)

        class Response:
            content_length = api._MAX_RESPONSE_BYTES + 1
            content = Content()

        with self.assertRaisesRegex(api.VsChrudimProtocolError, "size limit"):
            await api.VsChrudimClient._read_bounded_response_text(Response())

    async def test_bounded_response_reader_rejects_oversized_stream(self):
        class Content:
            def __init__(self):
                self.chunks = [b"ab", b"c"]

            async def read(self, maximum):
                return self.chunks.pop(0)

        class Response:
            content_length = None
            content = Content()

        with patch.object(api, "_MAX_RESPONSE_BYTES", 2):
            with self.assertRaisesRegex(api.VsChrudimProtocolError, "size limit"):
                await api.VsChrudimClient._read_bounded_response_text(Response())

    async def test_bounded_response_reader_falls_back_from_invalid_charset(self):
        class Content:
            def __init__(self):
                self.chunks = ["Měřidlo;Čas;Stav".encode(), b""]

            async def read(self, maximum):
                return self.chunks.pop(0)

        class Response:
            content_length = len("Měřidlo;Čas;Stav".encode())
            charset = "not-a-real-charset"
            content = Content()

        text = await api.VsChrudimClient._read_bounded_response_text(Response())

        self.assertEqual(text, "Měřidlo;Čas;Stav")

    async def test_request_timeout_is_a_connection_error(self):
        class Request:
            async def __aenter__(self):
                raise TimeoutError

            async def __aexit__(self, *args):
                return False

        class Session:
            @staticmethod
            def request(*args, **kwargs):
                return Request()

        client = api.VsChrudimClient(Session(), "user", "password")
        with patch.object(api.aiohttp, "ClientTimeout", create=True):
            with self.assertRaisesRegex(api.VsChrudimConnectionError, "timed out"):
                await client._request_text("GET", "https://example.invalid/")

    async def test_uses_verified_reporting_context_when_place_grid_is_absent(self):
        client = api.VsChrudimClient(object(), "user", "password")
        client._logged_in = True
        calls = []
        detail_html = """
        <form>
          <input type="hidden" name="ctl00$Detail$edCpId" value="123">
          <input type="hidden" name="ctl00$Detail$edCpEvNum" value="456">
        </form>
        """
        readings_html = """
        <input id="ctl00_GraphFilter1_btnRenew" value="Aktualizovat">
        <table>
          <tr><th>Měřidlo</th><th>Čas</th><th>Stav</th></tr>
          <tr><td>A</td><td>01.01.2026 00:00</td><td>10,250 m³</td></tr>
        </table>
        """

        async def request(method, url, data=None):
            calls.append((method, url, data))
            if url == api.PLACES_URL:
                return detail_html, "https://zakaznik.vschrudim.cz/detail"
            if url == api.READINGS_URL:
                return readings_html, url
            self.fail(f"Unexpected request: {method} {url}")

        client._request_text = request
        result = await client.async_get_data(self._place())

        self.assertEqual(len(result.readings), 1)
        self.assertEqual(result.readings[0].meter_state_m3, 10.25)
        self.assertEqual(result.download_metadata.source, "html_table")
        self.assertEqual(
            [(method, url) for method, url, _ in calls],
            [("GET", api.PLACES_URL), ("GET", api.READINGS_URL)],
        )

    async def test_reauthenticates_once_and_replays_full_download_after_expiry(self):
        client = api.VsChrudimClient(object(), "user", "password")
        client._logged_in = True
        download_attempts = 0
        fresh_logins = 0

        async def fresh_login():
            nonlocal fresh_logins
            fresh_logins += 1
            client._logged_in = True

        async def download_once(_place):
            nonlocal download_attempts
            download_attempts += 1
            if download_attempts == 1:
                client._logged_in = False
                raise api.VsChrudimAuthError("Authenticated session expired")
            return "downloaded"

        client.async_login = fresh_login
        client._async_get_data_once = download_once

        self.assertEqual(await client.async_get_data(self._place()), "downloaded")
        self.assertEqual(download_attempts, 2)
        self.assertEqual(fresh_logins, 1)

    async def test_does_not_loop_when_session_expires_again_after_fresh_login(self):
        client = api.VsChrudimClient(object(), "user", "password")
        client._logged_in = True
        download_attempts = 0
        fresh_logins = 0

        async def fresh_login():
            nonlocal fresh_logins
            fresh_logins += 1
            client._logged_in = True

        async def download_once(_place):
            nonlocal download_attempts
            download_attempts += 1
            raise api.VsChrudimAuthError("Authenticated session expired")

        client.async_login = fresh_login
        client._async_get_data_once = download_once

        with self.assertRaisesRegex(api.VsChrudimAuthError, "session expired"):
            await client.async_get_data(self._place())

        self.assertEqual(download_attempts, 2)
        self.assertEqual(fresh_logins, 1)

    async def test_rejected_fresh_login_is_not_retried_as_a_session_expiry(self):
        client = api.VsChrudimClient(object(), "user", "password")
        client._logged_in = True
        fresh_logins = 0

        async def rejected_login():
            nonlocal fresh_logins
            fresh_logins += 1
            raise api.VsChrudimAuthError("The portal rejected the supplied credentials")

        async def expired_download(_place):
            raise api.VsChrudimAuthError("Authenticated session expired")

        client.async_login = rejected_login
        client._async_get_data_once = expired_download

        with self.assertRaisesRegex(api.VsChrudimAuthError, "rejected"):
            await client.async_get_data(self._place())

        self.assertEqual(fresh_logins, 1)

    async def test_missing_grid_does_not_reuse_unverified_place_context(self):
        client = api.VsChrudimClient(object(), "user", "password")

        with self.assertRaisesRegex(
            api.VsChrudimProtocolError,
            "did not confirm the configured place",
        ):
            await client._select_place(
                "<html>portal detail without identity controls</html>",
                "https://zakaznik.vschrudim.cz/detail",
                self._place(),
            )

    def test_empty_range_before_earliest_reading_finishes_history_scan(self):
        error = api.VsChrudimProtocolError(
            "The portal exposed no recognizable CSV link or WebForms export control"
        )

        self.assertTrue(
            api.is_empty_history_boundary_error(
                error,
                requested_to=date(2025, 10, 1),
                earliest_reading=date(2025, 10, 11),
            )
        )
        self.assertFalse(
            api.is_empty_history_boundary_error(
                error,
                requested_to=date(2025, 11, 1),
                earliest_reading=date(2025, 10, 11),
            )
        )
        self.assertFalse(
            api.is_empty_history_boundary_error(
                error,
                requested_to=date(2025, 10, 1),
                earliest_reading=None,
            )
        )

    async def test_falls_back_to_rendered_readings_table(self):
        client = api.VsChrudimClient(object(), "user", "password")
        html = """
        <table>
          <tr><th>Čas</th><th>Stav</th></tr>
          <tr><td>01.01.2026 00:00</td><td>10,250 m³</td></tr>
        </table>
        """

        readings = await client._read_readings(
            html,
            "https://zakaznik.vschrudim.cz/Userdata/ProfileData.aspx",
        )

        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0].meter_state_m3, 10.25)
    async def test_downloads_document_show_export_without_csv_label(self):
        client = api.VsChrudimClient(object(), "user", "password")
        calls = []

        async def request(method, url, data=None):
            calls.append((method, url, data))
            return (
                "MERIDLO;CAS;STAV\nA;01.01.2026 00:00;10,000\n",
                url,
            )

        client._request_text = request
        content = await client._download_csv(
            '<a href="/DocumentShow.aspx?id=example"><img alt="Export"></a>',
            "https://zakaznik.vschrudim.cz/Userdata/ProfileData.aspx",
        )

        self.assertIn("MERIDLO;CAS;STAV", content)
        self.assertEqual(
            calls,
            [
                (
                    "GET",
                    "https://zakaznik.vschrudim.cz/DocumentShow.aspx?id=example",
                    None,
                )
            ],
        )

    async def test_rejects_document_show_response_without_verified_header(self):
        client = api.VsChrudimClient(object(), "user", "password")

        async def request(method, url, data=None):
            return ("<html>Unexpected response</html>", url)

        client._request_text = request
        with self.assertRaisesRegex(api.VsChrudimProtocolError, "no valid"):
            await client._download_csv(
                '<a href="/DocumentShow.aspx?id=example">Export</a>',
                "https://zakaznik.vschrudim.cz/Userdata/ProfileData.aspx",
            )

    async def test_accepts_quoted_accented_export_header(self):
        client = api.VsChrudimClient(object(), "user", "password")

        async def request(method, url, data=None):
            return ('\ufeff"MĚŘIDLO";"ČAS";"STAV"\n', url)

        client._request_text = request
        content = await client._download_csv(
            '<a href="/DocumentShow.aspx?id=example">Export</a>',
            "https://zakaznik.vschrudim.cz/Userdata/ProfileData.aspx",
        )

        self.assertIn("MĚŘIDLO", content)

    async def test_distinguishes_missing_export_control(self):
        client = api.VsChrudimClient(object(), "user", "password")

        with self.assertRaisesRegex(api.VsChrudimProtocolError, "no recognizable"):
            await client._download_csv(
                "<form><input name=\"ordinary\" value=\"nothing\"></form>",
                "https://zakaznik.vschrudim.cz/Userdata/ProfileData.aspx",
            )

    async def test_submits_export_control_with_complete_webforms_payload(self):
        client = api.VsChrudimClient(object(), "user", "password")
        calls = []

        async def request(method, url, data=None):
            calls.append((method, url, data))
            return (
                "MERIDLO;CAS;STAV\nA;01.01.2026 00:00;10,000\n",
                url,
            )

        client._request_text = request
        content = await client._download_csv(
            """
            <form method="post" action="./ProfileData.aspx">
              <input type="hidden" name="__VIEWSTATE" value="state">
              <input type="text" name="filterFrom" value="01.01.2026">
              <select name="period"><option value="U" selected>Custom</option></select>
              <input type="submit" name="ctl00$btnExport" value="Export data">
            </form>
            """,
            "https://zakaznik.vschrudim.cz/Userdata/ProfileData.aspx",
        )

        self.assertIn("MERIDLO;CAS;STAV", content)
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(
            calls[0][1],
            "https://zakaznik.vschrudim.cz/Userdata/ProfileData.aspx",
        )
        self.assertEqual(
            calls[0][2],
            {
                "__VIEWSTATE": "state",
                "filterFrom": "01.01.2026",
                "period": "U",
                "ctl00$btnExport": "Export data",
            },
        )

    async def test_submits_named_html_export_button(self):
        client = api.VsChrudimClient(object(), "user", "password")
        calls = []

        async def request(method, url, data=None):
            calls.append((method, url, data))
            return ("MERIDLO;CAS;STAV\n", url)

        client._request_text = request
        await client._download_csv(
            """
            <form method="post" action="./ProfileData.aspx">
              <input type="hidden" name="__VIEWSTATE" value="state">
              <button name="downloadData" value="export">Stáhnout soubor</button>
            </form>
            """,
            "https://zakaznik.vschrudim.cz/Userdata/ProfileData.aspx",
        )

        self.assertEqual(calls[0][2]["downloadData"], "export")

    async def test_replays_verified_webforms_export_postback(self):
        client = api.VsChrudimClient(object(), "user", "password")
        calls = []

        async def request(method, url, data=None):
            calls.append((method, url, data))
            return ("MERIDLO;CAS;STAV\nA;01.01.2026 00:00;10,000\n", url)

        client._request_text = request
        content, metadata = await client._download_csv_with_metadata(
            """
            <form method="post" action="./ProfileData.aspx">
              <input type="hidden" name="__VIEWSTATE" value="state">
              <input type="text" name="filterFrom" value="01.09.2026">
              <select name="period"><option value="U" selected>Custom</option></select>
              <a href="javascript:__doPostBack('ctl00$GraphFilter1$btnExport','')">Export CSV</a>
            </form>
            """,
            "https://zakaznik.vschrudim.cz/Userdata/ProfileData.aspx",
        )

        self.assertIn("MERIDLO;CAS;STAV", content)
        self.assertEqual(metadata.source, "csv_postback")
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], "https://zakaznik.vschrudim.cz/Userdata/ProfileData.aspx")
        self.assertEqual(
            calls[0][2],
            {
                "__VIEWSTATE": "state",
                "filterFrom": "01.09.2026",
                "period": "U",
                "__EVENTTARGET": "ctl00$GraphFilter1$btnExport",
                "__EVENTARGUMENT": "",
            },
        )

    async def test_does_not_execute_unrecognized_javascript_export_link(self):
        client = api.VsChrudimClient(object(), "user", "password")

        async def request(*args, **kwargs):
            self.fail("Unrecognized JavaScript must not be requested")

        client._request_text = request
        with self.assertRaisesRegex(api.VsChrudimProtocolError, "no recognizable"):
            await client._download_csv(
                '<a href="javascript:window.evil()">Export CSV</a>',
                "https://zakaznik.vschrudim.cz/Userdata/ProfileData.aspx",
            )

    async def test_uses_verified_readings_url_and_requires_filter(self):
        client = api.VsChrudimClient(object(), "user", "password")
        calls = []

        async def request(method, url, data=None):
            calls.append((method, url, data))
            return (
                '<select name="ctl00$GraphFilter1$edGraphLength">'
                '<option value="W">Week</option></select>',
                url,
            )

        client._request_text = request
        html, url = await client._open_measured_states("<html></html>", "https://example.invalid/detail")
        self.assertTrue(api._looks_like_readings_page(html))
        self.assertEqual(url, api.READINGS_URL)
        self.assertEqual(calls, [("GET", api.READINGS_URL, None)])

    async def test_replays_actual_measured_states_postback_as_fallback(self):
        client = api.VsChrudimClient(object(), "user", "password")
        calls = []
        selected_html = """
        <form method="post" action="./detail">
          <input type="hidden" name="__VIEWSTATE" value="state">
          <a href="javascript:__doPostBack('ctl00$ctl00$MainMenu1$btnProfileData','')">Naměřené stavy</a>
        </form>
        """

        async def request(method, url, data=None):
            calls.append((method, url, data))
            if method == "GET":
                return "<html>not the readings page</html>", url
            return '<input id="x_GraphFilter1_btnRenew" value="Aktualizovat">', url

        client._request_text = request
        html, _ = await client._open_measured_states(
            selected_html,
            "https://zakaznik.vschrudim.cz/detail",
        )
        self.assertTrue(api._looks_like_readings_page(html))
        self.assertEqual(calls[-1][0], "POST")
        self.assertEqual(
            calls[-1][2]["__EVENTTARGET"],
            "ctl00$ctl00$MainMenu1$btnProfileData",
        )
        self.assertEqual(calls[-1][2]["__EVENTARGUMENT"], "")

    async def test_custom_history_replays_period_then_date_range(self):
        client = api.VsChrudimClient(object(), "user", "password")
        calls = []
        form_html = """
        <form method="post" action="./ProfileData.aspx">
          <input type="hidden" name="__VIEWSTATE" value="state">
          <input type="text" name="ctl00$GraphFilter1$edDateFrom" value="">
          <input type="text" name="ctl00$GraphFilter1$edDateTo" value="">
          <input type="hidden" name="ctl00$GraphFilter1$hfDateFrom" value="">
          <input type="hidden" name="ctl00$GraphFilter1$hfDateTo" value="">
          <input type="text" name="ctl00$ProfileData$context" value="keep-me">
          <input type="submit" name="ctl00$GraphFilter1$btnRenew" value="Update">
          <select name="ctl00$GraphFilter1$edGraphLength">
            <option value="W">Week</option><option value="U">Custom</option>
          </select>
        </form>
        """
        custom_html = form_html.replace(
            '<option value="U">', '<option value="U" selected>'
        )

        async def request(method, url, data=None):
            calls.append((method, url, data))
            return (custom_html, url)

        client._request_text = request
        await client._set_custom_range(
            form_html,
            "https://zakaznik.vschrudim.cz/Userdata/ProfileData.aspx",
            date(2026, 1, 2),
            date(2026, 2, 3),
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][2]["__EVENTTARGET"], "ctl00$GraphFilter1$edGraphLength")
        self.assertEqual(calls[0][2]["ctl00$GraphFilter1$edGraphLength"], "U")
        self.assertEqual(calls[0][2]["ctl00$ProfileData$context"], "keep-me")
        self.assertEqual(calls[1][2]["ctl00$GraphFilter1$edDateFrom"], "02.01.2026")
        self.assertEqual(calls[1][2]["ctl00$GraphFilter1$hfDateTo"], "03.02.2026")
        self.assertEqual(calls[1][2]["ctl00$GraphFilter1$btnRenew"], "Update")
        self.assertEqual(calls[1][2]["ctl00$ProfileData$context"], "keep-me")
