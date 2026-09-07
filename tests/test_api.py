"""Tests with anonymized, static portal export examples."""
import importlib.util
from datetime import date
from pathlib import Path
import sys
import types
import unittest

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
        with self.assertRaisesRegex(api.VsChrudimProtocolError, "verified CSV"):
            await client._download_csv(
                '<a href="/DocumentShow.aspx?id=example">Export</a>',
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
        self.assertEqual(calls[1][2]["ctl00$GraphFilter1$edDateFrom"], "02.01.2026")
        self.assertEqual(calls[1][2]["ctl00$GraphFilter1$hfDateTo"], "03.02.2026")
        self.assertEqual(calls[1][2]["ctl00$GraphFilter1$btnRenew"], "Update")
