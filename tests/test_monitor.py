import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoomjoin.monitor import monitor


class _Clock:
    """Returns successive values from a fixed list on each call."""

    def __init__(self, values):
        self._values = list(values)
        self._i = 0

    def __call__(self):
        v = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return v


class _Sequence:
    """Returns successive values from a list, repeating the last forever."""

    def __init__(self, values):
        self._values = list(values)
        self._i = 0

    def __call__(self, *args, **kwargs):
        v = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return v


class TestMonitor(unittest.TestCase):
    def test_ended_after_threshold(self):
        # in_meeting: True, True, False (confirmed by second False)
        in_meeting = _Sequence([True, True, False, False])
        clock = _Clock([0, 700])  # start=0, gone-check=700 -> duration=700 >= 600
        cleanup_calls = []
        result = monitor(
            crash_threshold_s=600,
            in_meeting_fn=in_meeting,
            cleanup_fn=lambda: cleanup_calls.append(1),
            sleep_fn=lambda s: None,
            clock=clock,
        )
        self.assertEqual(result.outcome, "ended")
        self.assertEqual(result.duration_s, 700)
        self.assertEqual(cleanup_calls, [1])

    def test_crashed_early_before_threshold(self):
        in_meeting = _Sequence([False, False])
        clock = _Clock([0, 30])  # duration=30 < 600
        result = monitor(
            crash_threshold_s=600,
            in_meeting_fn=in_meeting,
            cleanup_fn=lambda: None,
            sleep_fn=lambda s: None,
            clock=clock,
        )
        self.assertEqual(result.outcome, "crashed_early")
        self.assertEqual(result.duration_s, 30)

    def test_debounce_transient_miss_does_not_end_meeting(self):
        # First poll: False (transient), debounce check: True (still there),
        # then resumes looping: True, then False, False (confirmed gone).
        in_meeting = _Sequence([False, True, True, False, False])
        clock = _Clock([0, 900])
        result = monitor(
            crash_threshold_s=600,
            in_meeting_fn=in_meeting,
            cleanup_fn=lambda: None,
            sleep_fn=lambda s: None,
            clock=clock,
        )
        self.assertEqual(result.outcome, "ended")
        # Confirm the debounce path was exercised (more than 2 in_meeting calls)
        self.assertGreaterEqual(in_meeting._i, 4)

    def test_cleanup_called_exactly_once_on_end(self):
        in_meeting = _Sequence([False, False])
        clock = _Clock([0, 700])
        calls = []
        monitor(
            crash_threshold_s=600,
            in_meeting_fn=in_meeting,
            cleanup_fn=lambda: calls.append(1),
            sleep_fn=lambda s: None,
            clock=clock,
        )
        self.assertEqual(len(calls), 1)

    def test_cleanup_exception_does_not_propagate(self):
        in_meeting = _Sequence([False, False])
        clock = _Clock([0, 700])

        def boom():
            raise RuntimeError("cleanup blew up")

        try:
            result = monitor(
                crash_threshold_s=600,
                in_meeting_fn=in_meeting,
                cleanup_fn=boom,
                sleep_fn=lambda s: None,
                clock=clock,
            )
        except RuntimeError:
            self.fail("cleanup_fn exception propagated out of monitor()")
        self.assertEqual(result.outcome, "ended")

    def test_sleep_fn_invoked_with_expected_intervals(self):
        in_meeting = _Sequence([False, False])
        clock = _Clock([0, 700])
        sleeps = []
        monitor(
            poll_interval_s=30,
            confirm_delay_s=5,
            crash_threshold_s=600,
            in_meeting_fn=in_meeting,
            cleanup_fn=lambda: None,
            sleep_fn=lambda s: sleeps.append(s),
            clock=clock,
        )
        self.assertIn(30, sleeps)
        self.assertIn(5, sleeps)


if __name__ == "__main__":
    unittest.main()
