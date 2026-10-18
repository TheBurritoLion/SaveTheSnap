"""Synthetic export regressions. These fixtures contain no personal history."""

import json
import unittest

from savethesnap.metadata import date_evidence, gps_evidence, parse_history


class HistoryTests(unittest.TestCase):
    def parse_json(self, rows):
        return parse_history(json.dumps({"Saved Media": rows}).encode(), "memories_history.json")

    def test_json_retains_evidence_but_not_download_url(self):
        secret_url = "https://example.invalid/download?token=SYNTHETIC_SECRET"
        rows = self.parse_json([{
            "Date": "2024-01-02 12:34 UTC", "Media Type": "Video",
            "Location": "Latitude, Longitude: 0, 0", "File Name": "media/clip.mp4",
            "Download Link": secret_url,
        }])
        self.assertEqual(rows, [{
            "reference": "media/clip.mp4", "date": "2024-01-02 12:34 UTC",
            "location": "Latitude, Longitude: 0, 0", "media_type": "Video",
            "url_present": True, "row_index": 0,
        }])
        self.assertNotIn(secret_url, repr(rows))
        self.assertNotIn("SYNTHETIC_SECRET", repr(rows))

    def test_json_does_not_invent_reference_from_order(self):
        rows = self.parse_json([{"Date": "2024-01-02", "Media Type": "Image"}])
        self.assertIsNone(rows[0]["reference"])
        self.assertFalse(rows[0]["url_present"])

    def test_json_remote_reference_is_discarded(self):
        rows = self.parse_json([{"File": "https://example.invalid/clip.mp4?token=SECRET"}])
        self.assertIsNone(rows[0]["reference"])
        self.assertNotIn("SECRET", repr(rows))

    def test_json_rejects_unsupported_schema_and_bad_rows(self):
        for document in ([], {"media": []}, {"Saved Media": {}}, {"Saved Media": [None]},
                         {"Saved Media": [{}]}, {"Saved Media": [{"Date": 42}]},
                         {"Saved Media": [{"Location": []}]}):
            with self.subTest(document=document), self.assertRaises(ValueError):
                parse_history(json.dumps(document).encode(), "history.json")

    def test_bad_row_fails_without_silently_returning_partial_count(self):
        with self.assertRaisesRegex(ValueError, "row 1"):
            self.parse_json([{"Date": "2024-01-02"}, {"unrecognized": "field"}])

    def test_errors_do_not_echo_download_urls(self):
        secret_url = "https://example.invalid/clip.mp4?token=SECRET"
        with self.assertRaises(ValueError) as error:
            self.parse_json([{"Download Link": [secret_url]}])
        self.assertNotIn("SECRET", str(error.exception))

    def test_html_nested_headers_and_download_callback_are_data_only(self):
        html = b'''<!doctype html><table><thead><tr>
          <th><b>Date</b></th><th>Media <span>Type</span></th><th>Location</th><th>Download Link</th>
        </tr></thead><tbody><tr>
          <td><span>2024-01-02 12:34 UTC</span></td><td>Video</td><td>Latitude, Longitude: 0, 0</td>
          <td><a href="#" onclick="downloadMemories('https://example.invalid/?token=SECRET'); throw new Error('MUST_NOT_RUN')">Download</a></td>
        </tr></tbody></table>'''
        rows = parse_history(html, "memories_history.html")
        self.assertEqual(rows[0]["date"], "2024-01-02 12:34 UTC")
        self.assertEqual(rows[0]["media_type"], "Video")
        self.assertIsNone(rows[0]["reference"])
        self.assertTrue(rows[0]["url_present"])
        self.assertNotIn("SECRET", repr(rows))
        self.assertNotIn("MUST_NOT_RUN", repr(rows))

    def test_html_local_media_href_and_file_column(self):
        html = b'''<table><tr><td>Date</td><td>Media Type</td><td>File</td></tr>
        <tr><td>2024-01-02</td><td>Image</td><td><a href="media/one.jpg">media/one.jpg</a></td></tr>
        </table>'''
        row = parse_history(html, "history.html")[0]
        self.assertEqual(row["reference"], "media/one.jpg")
        self.assertFalse(row["url_present"])

    def test_html_file_column_can_use_a_generic_link_label(self):
        html = b'''<table><tr><th>Date</th><th>Media Type</th><th>Filename</th></tr>
        <tr><td>2024-01-02</td><td>Image</td><td><a href="media/one.jpg">View image</a></td></tr></table>'''
        self.assertEqual(parse_history(html, "history.html")[0]["reference"], "media/one.jpg")

    def test_html_remote_href_never_becomes_a_reference(self):
        html = b'''<table><tr><th>Date</th><th>Media Type</th><th>Download</th></tr>
        <tr><td>2024-01-02</td><td>Video</td><td><a href="https://example.invalid/one.mp4?secret=TOKEN">Download</a></td></tr></table>'''
        row = parse_history(html, "history.html")[0]
        self.assertIsNone(row["reference"])
        self.assertTrue(row["url_present"])
        self.assertNotIn("TOKEN", repr(row))

    def test_html_download_label_or_empty_attributes_do_not_prove_a_url(self):
        html = b'''<table><tr><th>Date</th><th>Media Type</th><th>Download Link</th></tr>
        <tr><td>2024-01-02</td><td>Video</td><td><a href onclick>Download</a></td></tr></table>'''
        self.assertFalse(parse_history(html, "history.html")[0]["url_present"])

    def test_html_rejects_missing_columns_and_conflicting_local_links(self):
        for body in ("<td>2024-01-02</td>",
                     '<td>2024-01-02</td><td><a href="one.mp4">one</a><a href="two.mp4">two</a></td>'):
            html = f"<table><tr><th>Date</th><th>Media Type</th></tr><tr>{body}</tr></table>"
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse_history(html.encode(), "history.html")

    def test_html_ignores_non_history_tables_and_script_text(self):
        html = b'''<table><tr><td>Unrelated</td></tr></table>
        <table><tr><th>Date</th><th>Media Type</th></tr>
        <tr><td>2024-01-02<script>DO_NOT_INCLUDE</script></td><td>Image</td></tr></table>'''
        rows = parse_history(html, "history.html")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2024-01-02")

    def test_empty_saved_media_is_valid(self):
        self.assertEqual(self.parse_json([]), [])


class EmptyControlTests(unittest.TestCase):
    def test_empty_callbacks_and_inert_urls_are_not_download_urls(self):
        html = b'''<table><tr><th>Date</th><th>Media Type</th><th>Download Link</th></tr>
        <tr><td>2020-01-02</td><td>Image</td><td><a onclick="downloadMemories('', this, false)" href="javascript:void(0)">Download</a></td></tr></table>'''
        self.assertFalse(parse_history(html, 'memories_history.html')[0]['url_present'])


class DateTests(unittest.TestCase):
    def test_minute_precision_does_not_gain_seconds(self):
        self.assertEqual(date_evidence("2024-01-02 12:34 UTC"), {
            "status": "minute", "value": "2024-01-02T12:34+00:00",
        })
        self.assertEqual(date_evidence("2024-01-02T12:34-0700"), {
            "status": "minute", "value": "2024-01-02T12:34-07:00",
        })

    def test_seconds_and_fraction_are_preserved(self):
        self.assertEqual(date_evidence("2024-01-02T12:34:56.120Z"), {
            "status": "second", "value": "2024-01-02T12:34:56.120+00:00",
        })

    def test_timezone_is_never_assigned_from_the_machine(self):
        self.assertEqual(date_evidence("2024-01-02 12:34"), {
            "status": "timezone_missing", "value": "2024-01-02T12:34",
        })
        self.assertEqual(date_evidence("2024-01-02 12:34:56"), {
            "status": "timezone_missing", "value": "2024-01-02T12:34:56",
        })

    def test_unknown_and_calendar_day_stay_distinct(self):
        self.assertEqual(date_evidence(None), {"status": "unknown", "value": None})
        self.assertEqual(date_evidence(""), {"status": "unknown", "value": None})
        self.assertEqual(date_evidence("2024-02-29"), {"status": "date_only", "value": "2024-02-29"})

    def test_invalid_dates_are_not_normalized(self):
        for raw in ("2023-02-29", "2024-01-02T25:00Z", "2024-01-02T12:34+25:00",
                    "2024-01-02T12:34+03:60", "01/02/2024", 0):
            with self.subTest(raw=raw):
                self.assertEqual(date_evidence(raw), {"status": "invalid", "value": None})


class GpsTests(unittest.TestCase):
    def test_zero_coordinates_are_explicit_evidence(self):
        self.assertEqual(gps_evidence("Latitude, Longitude: 0, 0"), [0.0, 0.0])
        self.assertEqual(gps_evidence({"latitude": 0, "longitude": 0}), [0.0, 0.0])

    def test_nonzero_coordinate_formats(self):
        self.assertEqual(gps_evidence({"Latitude": "40.0", "Longitude": "-105.0"}), [40.0, -105.0])
        self.assertEqual(gps_evidence(" Latitude, Longitude: -90, 180 "), [-90.0, 180.0])

    def test_nan_infinity_ranges_missing_and_bools_are_not_coordinates(self):
        for raw in (None, "", "Latitude, Longitude: NaN, 0", "Latitude, Longitude: 0, Infinity",
                    {"latitude": float("nan"), "longitude": 0}, {"latitude": 91, "longitude": 0},
                    {"latitude": 0, "longitude": -181}, {"latitude": False, "longitude": 0},
                    {"latitude": 0}, [0, 0]):
            with self.subTest(raw=raw):
                self.assertIsNone(gps_evidence(raw))


if __name__ == "__main__":
    unittest.main()
