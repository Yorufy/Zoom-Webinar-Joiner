import builtins
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoomjoin import whatsapp
from zoomjoin.whatsapp import Message


class TestExtractZoomLinks(unittest.TestCase):
    def test_extracts_valid_link_from_mixed_text(self):
        text = "hey the link moved, join here: https://acme.zoom.us/j/123?pwd=ab thanks!"
        links = whatsapp.extract_zoom_links(text)
        self.assertEqual(links, ["https://acme.zoom.us/j/123?pwd=ab"])

    def test_strips_trailing_punctuation(self):
        text = "see (https://acme.zoom.us/j/123)."
        links = whatsapp.extract_zoom_links(text)
        self.assertEqual(links, ["https://acme.zoom.us/j/123"])

    def test_non_zoom_url_returns_empty(self):
        text = "check this out: https://example.com/whatever"
        self.assertEqual(whatsapp.extract_zoom_links(text), [])


class TestParsePrePlainText(unittest.TestCase):
    def test_parses_valid_timestamp(self):
        ts = whatsapp.parse_pre_plain_text("[10:30 AM, 7/8/2026] Alice: ")
        self.assertEqual(ts, datetime(2026, 7, 8, 10, 30))

    def test_garbage_returns_none(self):
        self.assertIsNone(whatsapp.parse_pre_plain_text("not a timestamp at all"))

    def test_empty_returns_none(self):
        self.assertIsNone(whatsapp.parse_pre_plain_text(""))


class TestSelectReplacementLink(unittest.TestCase):
    def test_picks_newest_link(self):
        messages = [
            Message(text="https://acme.zoom.us/j/111", ts=datetime(2026, 7, 8, 9, 0)),
            Message(text="https://acme.zoom.us/j/222", ts=datetime(2026, 7, 8, 10, 0)),
        ]
        link = whatsapp.select_replacement_link(messages, failed_url=None, since=None)
        self.assertEqual(link, "https://acme.zoom.us/j/222")

    def test_excludes_failed_url_case_and_slash_insensitive(self):
        messages = [
            Message(text="https://acme.zoom.us/j/111", ts=datetime(2026, 7, 8, 9, 0)),
            Message(text="https://ACME.zoom.us/j/222/", ts=datetime(2026, 7, 8, 10, 0)),
        ]
        link = whatsapp.select_replacement_link(
            messages, failed_url="https://acme.zoom.us/j/222", since=None
        )
        self.assertEqual(link, "https://acme.zoom.us/j/111")

    def test_since_excludes_older_but_keeps_ts_none(self):
        messages = [
            Message(text="https://acme.zoom.us/j/111", ts=datetime(2026, 7, 8, 8, 0)),  # too old
            Message(text="https://acme.zoom.us/j/222", ts=None),  # unknown recency, kept
        ]
        link = whatsapp.select_replacement_link(
            messages, failed_url=None, since=datetime(2026, 7, 8, 9, 0)
        )
        self.assertEqual(link, "https://acme.zoom.us/j/222")

    def test_only_link_equals_failed_url_returns_none(self):
        messages = [
            Message(text="https://acme.zoom.us/j/111", ts=datetime(2026, 7, 8, 9, 0)),
        ]
        link = whatsapp.select_replacement_link(
            messages, failed_url="https://acme.zoom.us/j/111", since=None
        )
        self.assertIsNone(link)

    def test_no_links_returns_none(self):
        messages = [
            Message(text="just chatting, no links here", ts=datetime(2026, 7, 8, 9, 0)),
        ]
        link = whatsapp.select_replacement_link(messages, failed_url=None, since=None)
        self.assertIsNone(link)


class TestFindReplacementLinkUnavailable(unittest.TestCase):
    def test_returns_unavailable_when_playwright_missing(self):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "playwright.sync_api" or name.startswith("playwright"):
                raise ImportError(f"No module named {name!r}")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            result = whatsapp.find_replacement_link("Some Group")
        finally:
            builtins.__import__ = real_import

        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.link)


if __name__ == "__main__":
    unittest.main()
