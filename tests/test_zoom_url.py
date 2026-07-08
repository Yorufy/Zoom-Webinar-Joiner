import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoomjoin.zoom_url import parse, to_protocol_url


class TestParse(unittest.TestCase):
    def test_basic_meeting_link(self):
        link = parse("https://zoom.us/j/1234567890?pwd=abc123")
        self.assertEqual(link.confno, "1234567890")
        self.assertEqual(link.pwd, "abc123")
        self.assertIsNone(link.tk)

    def test_vanity_subdomain(self):
        link = parse("https://mycompany.zoom.us/j/1234567890?pwd=abc123")
        self.assertEqual(link.confno, "1234567890")
        self.assertEqual(link.pwd, "abc123")

    def test_webinar_link(self):
        link = parse("https://zoom.us/w/9876543210?pwd=xyz")
        self.assertEqual(link.confno, "9876543210")
        self.assertEqual(link.pwd, "xyz")

    def test_short_s_link(self):
        link = parse("https://zoom.us/s/1112223334?pwd=zzz")
        self.assertEqual(link.confno, "1112223334")
        self.assertEqual(link.pwd, "zzz")

    def test_registration_token(self):
        link = parse(
            "https://zoom.us/w/1234567890?pwd=abc&tk=xyz.token-value_123"
        )
        self.assertEqual(link.confno, "1234567890")
        self.assertEqual(link.pwd, "abc")
        self.assertEqual(link.tk, "xyz.token-value_123")

    def test_url_encoded_pwd(self):
        # "p@ss w/d!" URL-encoded
        link = parse("https://zoom.us/j/1234567890?pwd=p%40ss%20w%2Fd%21")
        self.assertEqual(link.confno, "1234567890")
        self.assertEqual(link.pwd, "p@ss w/d!")

    def test_no_password_link(self):
        link = parse("https://zoom.us/j/1234567890")
        self.assertEqual(link.confno, "1234567890")
        self.assertIsNone(link.pwd)
        self.assertIsNone(link.tk)

    def test_zoommtg_passthrough(self):
        raw = "zoommtg://zoom.us/join?action=join&confno=1234567890&pwd=abc&tk=tok"
        link = parse(raw)
        self.assertEqual(link.confno, "1234567890")
        self.assertEqual(link.pwd, "abc")
        self.assertEqual(link.tk, "tok")
        self.assertEqual(link.original_url, raw)

    def test_zoommtg_passthrough_minimal(self):
        raw = "zoommtg://zoom.us/join?action=join&confno=1234567890"
        link = parse(raw)
        self.assertEqual(link.confno, "1234567890")
        self.assertEqual(link.original_url, raw)

    def test_garbage_input_raises(self):
        with self.assertRaises(ValueError):
            parse("not a url at all")

    def test_garbage_non_zoom_host_raises(self):
        with self.assertRaises(ValueError):
            parse("https://example.com/j/1234567890?pwd=abc")

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            parse("")

    def test_none_raises(self):
        with self.assertRaises(ValueError):
            parse(None)  # type: ignore[arg-type]


class TestToProtocolUrl(unittest.TestCase):
    def test_builds_full_protocol_url(self):
        link = parse("https://zoom.us/j/1234567890?pwd=abc123&tk=tok1")
        proto = to_protocol_url(link)
        self.assertEqual(
            proto,
            "zoommtg://zoom.us/join?action=join&confno=1234567890&pwd=abc123&tk=tok1",
        )

    def test_builds_without_pwd_or_tk(self):
        link = parse("https://zoom.us/j/1234567890")
        proto = to_protocol_url(link)
        self.assertEqual(
            proto, "zoommtg://zoom.us/join?action=join&confno=1234567890"
        )

    def test_https_fallback_when_no_confno(self):
        # A zoom.us link with only a pwd/tk and no extractable numeric id
        # (e.g. a personal vanity URL) should yield None from
        # to_protocol_url so the caller falls back to opening the https
        # url directly.
        link = parse("https://zoom.us/my/somebody?pwd=abc")
        proto = to_protocol_url(link)
        self.assertIsNone(proto)


if __name__ == "__main__":
    unittest.main()
