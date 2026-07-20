import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoomjoin import dispatch
from zoomjoin.dispatch import DispatchParseError, parse_dispatch, parse_when
from zoomjoin.store import Store

NOW = datetime(2026, 7, 8, 10, 0)


class TestParseDispatchFullMessage(unittest.TestCase):
    def test_full_message(self):
        text = 'join https://zoom.us/j/123?pwd=Ab9 at tomorrow 2pm group "Team Sync"'
        parsed = parse_dispatch(text, now=NOW)
        self.assertEqual(parsed.url, "https://zoom.us/j/123?pwd=Ab9")
        self.assertEqual(parsed.when, datetime(2026, 7, 9, 14, 0))
        self.assertEqual(parsed.group, "Team Sync")
        self.assertIsNone(parsed.duration_min)


class TestParseWhenForms(unittest.TestCase):
    def test_form1_iso_space(self):
        self.assertEqual(parse_when("2026-07-09 14:30", now=NOW), datetime(2026, 7, 9, 14, 30))

    def test_form1_iso_t_seconds(self):
        self.assertEqual(
            parse_when("2026-07-09T14:30:15", now=NOW), datetime(2026, 7, 9, 14, 30, 15)
        )

    def test_form1_iso_t(self):
        self.assertEqual(parse_when("2026-07-09T14:30", now=NOW), datetime(2026, 7, 9, 14, 30))

    def test_form2_date_12h_with_minutes(self):
        self.assertEqual(
            parse_when("2026-07-09 2:15pm", now=NOW), datetime(2026, 7, 9, 14, 15)
        )

    def test_form2_date_12h_hour_only(self):
        self.assertEqual(parse_when("2026-07-09 2pm", now=NOW), datetime(2026, 7, 9, 14, 0))

    def test_form3_today_24h(self):
        self.assertEqual(parse_when("today 23:00", now=NOW), datetime(2026, 7, 8, 23, 0))

    def test_form3_tomorrow_12h(self):
        self.assertEqual(parse_when("tomorrow 2pm", now=NOW), datetime(2026, 7, 9, 14, 0))

    def test_form3_tomorrow_12h_with_minutes(self):
        self.assertEqual(
            parse_when("tomorrow 9:15am", now=NOW), datetime(2026, 7, 9, 9, 15)
        )

    def test_form4_in_minutes(self):
        self.assertEqual(parse_when("in 30 minutes", now=NOW), datetime(2026, 7, 8, 10, 30))

    def test_form4_in_min_abbrev(self):
        self.assertEqual(parse_when("in 5 min", now=NOW), datetime(2026, 7, 8, 10, 5))

    def test_form4_in_hours(self):
        self.assertEqual(parse_when("in 2 hours", now=NOW), datetime(2026, 7, 8, 12, 0))

    def test_form4_in_hr_abbrev(self):
        self.assertEqual(parse_when("in 1 hr", now=NOW), datetime(2026, 7, 8, 11, 0))

    def test_form5_bare_future_today(self):
        # NOW is 10:00, 15:30 is still ahead today.
        self.assertEqual(parse_when("15:30", now=NOW), datetime(2026, 7, 8, 15, 30))

    def test_form5_bare_past_rolls_to_tomorrow(self):
        # 9:30 has already passed relative to NOW (10:00).
        self.assertEqual(parse_when("9:30", now=NOW), datetime(2026, 7, 9, 9, 30))

    def test_form5_bare_12h_am(self):
        self.assertEqual(parse_when("11am", now=NOW), datetime(2026, 7, 8, 11, 0))

    def test_form5_12am_boundary(self):
        # 12am -> 00:00, already past today at NOW=10:00 -> rolls to tomorrow.
        self.assertEqual(parse_when("12am", now=NOW), datetime(2026, 7, 9, 0, 0))

    def test_form5_12pm_boundary(self):
        self.assertEqual(parse_when("12pm", now=NOW), datetime(2026, 7, 8, 12, 0))

    def test_leading_at_stripped(self):
        self.assertEqual(parse_when("at tomorrow 2pm", now=NOW), datetime(2026, 7, 9, 14, 0))


class TestInvalidTimes(unittest.TestCase):
    def test_invalid_24h_hour(self):
        with self.assertRaises(DispatchParseError):
            parse_when("25:00", now=NOW)

    def test_invalid_12h_hour(self):
        with self.assertRaises(DispatchParseError):
            parse_when("13pm", now=NOW)

    def test_gibberish(self):
        with self.assertRaises(DispatchParseError):
            parse_when("banana", now=NOW)


class TestNoOrInvalidLink(unittest.TestCase):
    def test_no_link(self):
        with self.assertRaises(DispatchParseError):
            parse_dispatch("join at 2pm", now=NOW)

    def test_non_zoom_link(self):
        with self.assertRaises(DispatchParseError):
            parse_dispatch("join https://evil.com at 2pm", now=NOW)


class TestGroupExtraction(unittest.TestCase):
    def test_group_equals(self):
        parsed = parse_dispatch(
            "join https://zoom.us/j/123?pwd=abc at 2pm group=Standup", now=NOW
        )
        self.assertEqual(parsed.group, "Standup")

    def test_group_single_quoted_multi_word(self):
        parsed = parse_dispatch(
            "join https://zoom.us/j/123?pwd=abc at 2pm group 'A B'", now=NOW
        )
        self.assertEqual(parsed.group, "A B")

    def test_group_absent(self):
        parsed = parse_dispatch("join https://zoom.us/j/123?pwd=abc at 2pm", now=NOW)
        self.assertIsNone(parsed.group)


class TestDurationExtraction(unittest.TestCase):
    def test_for_min(self):
        parsed = parse_dispatch(
            "join https://zoom.us/j/123?pwd=abc at 2pm for 45 min", now=NOW
        )
        self.assertEqual(parsed.duration_min, 45)

    def test_duration_equals(self):
        parsed = parse_dispatch(
            "join https://zoom.us/j/123?pwd=abc at 2pm duration=60", now=NOW
        )
        self.assertEqual(parsed.duration_min, 60)

    def test_duration_absent(self):
        parsed = parse_dispatch("join https://zoom.us/j/123?pwd=abc at 2pm", now=NOW)
        self.assertIsNone(parsed.duration_min)

    def test_relative_time_not_mistaken_for_duration(self):
        parsed = parse_dispatch(
            "join https://zoom.us/j/123?pwd=abc in 30 minutes", now=NOW
        )
        self.assertIsNone(parsed.duration_min)
        self.assertEqual(parsed.when, datetime(2026, 7, 8, 10, 30))


class TestUrlNotMangled(unittest.TestCase):
    def test_mixed_case_pwd_and_tk_preserved(self):
        text = "join https://zoom.us/j/123?pwd=Ab9Xy&tk=ToK-Value_1 at 2pm"
        parsed = parse_dispatch(text, now=NOW)
        self.assertEqual(parsed.url, "https://zoom.us/j/123?pwd=Ab9Xy&tk=ToK-Value_1")

    def test_zoommtg_link_preserved(self):
        text = "join zoommtg://zoom.us/join?action=join&confno=123&pwd=AbC at 2pm"
        parsed = parse_dispatch(text, now=NOW)
        self.assertEqual(
            parsed.url, "zoommtg://zoom.us/join?action=join&confno=123&pwd=AbC"
        )


class TestCmdDispatchIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tmpdir.name) / "meetings.json"
        self.store = Store(self.store_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("zoomjoin.cli.scheduler.create_task")
    def test_good_message_schedules_and_returns_zero(self, mock_create_task):
        from zoomjoin.cli import cmd_dispatch
        import argparse

        args = argparse.Namespace(
            text='join https://zoom.us/j/123?pwd=Ab9 at 2099-01-01 09:00 group "Team Sync"'
        )
        rc = cmd_dispatch(args, self.store)
        self.assertEqual(rc, 0)
        mock_create_task.assert_called_once()
        records = self.store.list_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["url"], "https://zoom.us/j/123?pwd=Ab9")
        self.assertEqual(records[0]["group"], "Team Sync")

    @patch("zoomjoin.cli.scheduler.create_task")
    def test_unparseable_message_returns_two_and_writes_nothing(self, mock_create_task):
        from zoomjoin.cli import cmd_dispatch
        import argparse

        args = argparse.Namespace(text="join at 2pm")
        rc = cmd_dispatch(args, self.store)
        self.assertEqual(rc, 2)
        mock_create_task.assert_not_called()
        self.assertEqual(self.store.list_all(), [])


if __name__ == "__main__":
    unittest.main()
