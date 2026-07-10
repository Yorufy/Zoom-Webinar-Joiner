"""End-to-end integration tests for cmd_run's orchestration (Phase 7).

Every other module is unit-tested in isolation; this file exercises the wiring
in ``zoomjoin.cli.cmd_run`` / ``_fallback_chain`` / ``_attend`` that ties join ->
attend -> WhatsApp fallback -> escalation together. The external effects
(launching Zoom, polling windows, driving Playwright, spawning ``claude -p``,
Windows toasts) are all stubbed at the ``zoomjoin.cli`` boundary so the test is
deterministic, offline, and touches no real scheduler/network.

Branch matrix covered:
  A  direct join -> ended                                  -> 0, status "ended"
  B  direct join -> crashed_early -> WA link -> ended      -> 0, recovered_url set
  C  direct join -> crashed_early -> WA link -> crash again -> escalate
  D  join fails  -> no group                               -> escalate
  E  join fails  -> WA found -> retry joins -> ended        -> 0, recovered_url set
  F  join fails  -> WA found -> retry fails                 -> escalate
  G  join fails  -> WA no_link                              -> escalate
  H  unknown meeting id                                    -> 1
Plus: escalation exit code maps to process exit (0 vs 1).
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoomjoin import cli
from zoomjoin.escalate import EscalateResult
from zoomjoin.joiner import JoinResult
from zoomjoin.monitor import MonitorResult
from zoomjoin.store import Store
from zoomjoin.whatsapp import WhatsAppResult


def _join(joined: bool) -> JoinResult:
    return JoinResult(joined=joined, attempts=1 if joined else 2, elapsed_s=1.0,
                      detail="ok" if joined else "no in-meeting window")


def _mon(outcome: str) -> MonitorResult:
    dur = 3600.0 if outcome == "ended" else 30.0
    return MonitorResult(outcome=outcome, duration_s=dur, detail=outcome)


def _wa(status: str, link: str | None = None) -> WhatsAppResult:
    return WhatsAppResult(status=status, link=link, detail=status)


def _esc(attempted: bool = True, agent_exit: int | None = 0) -> EscalateResult:
    return EscalateResult(attempted=attempted, agent_exit=agent_exit, detail="drill")


class IntegrationRunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        # cmd_run attaches file+stream handlers to the root logger; snapshot and
        # restore so they don't accumulate across tests and (on Windows) so the
        # open log file doesn't block temp-dir cleanup.
        self.addCleanup(self._restore_root_logger)
        root = logging.getLogger()
        self._saved_handlers = list(root.handlers)
        self._saved_level = root.level
        self.store = Store(tmp / "meetings.json")
        # Keep run logging entirely inside the temp dir (real handler wiring).
        self._logs_patch = mock.patch.object(cli, "LOGS_DIR", tmp / "logs")
        self._logs_patch.start()
        self.addCleanup(self._logs_patch.stop)
        # Silence real toasts/pushes.
        self._notify_patch = mock.patch.object(cli.notify, "notify")
        self._notify_patch.start()
        self.addCleanup(self._notify_patch.stop)

        self.meeting_id = self.store.add({
            "url": "https://zoom.us/j/123456789?pwd=SECRET",
            "at": "2026-07-08T09:00:00",
            "group": "team-standup",
            "duration_min": 30,
            "status": "scheduled",
        })

    def _restore_root_logger(self):
        root = logging.getLogger()
        for h in list(root.handlers):
            if h not in self._saved_handlers:
                root.removeHandler(h)
                if isinstance(h, logging.FileHandler):
                    h.close()
        root.setLevel(self._saved_level)

    def _run(self, *, join, monitor=None, wa=None, esc=None, group="team-standup"):
        """Drive cmd_run with the external boundary stubbed. Returns (exit, record).

        ``join`` may be a single JoinResult (used for every joiner.join call) or a
        list consumed in order (direct join, then the WhatsApp-retry join).
        ``monitor`` likewise: single MonitorResult or an ordered list.
        """
        if group != "team-standup":
            self.store.update(self.meeting_id, group=group)

        join_seq = list(join) if isinstance(join, list) else None
        mon_seq = list(monitor) if isinstance(monitor, list) else None

        def join_side(_url, *a, **k):
            return join_seq.pop(0) if join_seq is not None else join

        def mon_side(*a, **k):
            if mon_seq is not None:
                return mon_seq.pop(0)
            return monitor

        args = argparse.Namespace(id=self.meeting_id)
        with mock.patch.object(cli.joiner, "join", side_effect=join_side), \
             mock.patch.object(cli.monitor, "monitor", side_effect=mon_side), \
             mock.patch.object(cli.whatsapp, "find_replacement_link",
                               return_value=wa or _wa("no_link")) as wa_mock, \
             mock.patch.object(cli.escalate, "escalate",
                               return_value=esc or _esc()) as esc_mock:
            code = cli.cmd_run(args, self.store)
        self.wa_mock = wa_mock
        self.esc_mock = esc_mock
        return code, self.store.get(self.meeting_id)

    # -- A: happy path -------------------------------------------------------
    def test_direct_join_then_ended(self):
        code, rec = self._run(join=_join(True), monitor=_mon("ended"))
        self.assertEqual(code, 0)
        self.assertEqual(rec["status"], "ended")
        self.esc_mock.assert_not_called()
        self.wa_mock.assert_not_called()

    # -- B: direct join, early crash, WhatsApp recovers, then ends -----------
    def test_direct_join_crash_then_whatsapp_recovers(self):
        code, rec = self._run(
            join=[_join(True), _join(True)],
            monitor=[_mon("crashed_early"), _mon("ended")],
            wa=_wa("found", "https://zoom.us/j/999?pwd=NEW"),
        )
        self.assertEqual(code, 0)
        self.assertEqual(rec["status"], "ended")
        self.assertEqual(rec["recovered_url"], "https://zoom.us/j/999?pwd=NEW")
        # WhatsApp is scanned excluding the original failed link.
        _, kwargs = self.wa_mock.call_args
        self.assertEqual(kwargs["failed_url"], rec["url"])
        self.esc_mock.assert_not_called()

    # -- C: recovered link also crashes early -> escalate (no WA re-loop) ----
    def test_recovered_link_crash_escalates(self):
        code, rec = self._run(
            join=[_join(True), _join(True)],
            monitor=[_mon("crashed_early"), _mon("crashed_early")],
            wa=_wa("found", "https://zoom.us/j/999?pwd=NEW"),
        )
        self.assertEqual(code, 0)  # escalation attempted, agent_exit 0
        self.assertEqual(rec["status"], "escalated")
        self.assertEqual(self.wa_mock.call_count, 1)  # scanned exactly once
        self.esc_mock.assert_called_once()

    # -- D: join fails, no group -> straight to escalation -------------------
    def test_join_fail_no_group_escalates(self):
        code, rec = self._run(join=_join(False), group=None)
        self.assertEqual(code, 0)
        self.assertEqual(rec["status"], "escalated")
        self.wa_mock.assert_not_called()
        self.esc_mock.assert_called_once()

    # -- E: join fails, WhatsApp recovers, retry joins, ends -----------------
    def test_join_fail_whatsapp_recovers(self):
        code, rec = self._run(
            join=[_join(False), _join(True)],
            monitor=_mon("ended"),
            wa=_wa("found", "https://zoom.us/j/777?pwd=ALT"),
        )
        self.assertEqual(code, 0)
        self.assertEqual(rec["status"], "ended")
        self.assertEqual(rec["recovered_url"], "https://zoom.us/j/777?pwd=ALT")
        self.esc_mock.assert_not_called()

    # -- F: join fails, WhatsApp recovers, retry also fails -> escalate ------
    def test_join_fail_whatsapp_link_but_retry_fails(self):
        code, rec = self._run(
            join=[_join(False), _join(False)],
            wa=_wa("found", "https://zoom.us/j/777?pwd=ALT"),
        )
        self.assertEqual(code, 0)
        self.assertEqual(rec["status"], "escalated")
        self.esc_mock.assert_called_once()

    # -- G: join fails, WhatsApp finds nothing -> escalate -------------------
    def test_join_fail_whatsapp_no_link_escalates(self):
        code, rec = self._run(join=_join(False), wa=_wa("no_link"))
        self.assertEqual(code, 0)
        self.assertEqual(rec["status"], "escalated")
        self.esc_mock.assert_called_once()

    # -- H: unknown meeting id ----------------------------------------------
    def test_unknown_meeting_id(self):
        args = argparse.Namespace(id="does-not-exist")
        with mock.patch.object(cli.joiner, "join") as jm:
            code = cli.cmd_run(args, self.store)
        self.assertEqual(code, 1)
        jm.assert_not_called()

    # -- escalation failure maps to non-zero exit ---------------------------
    def test_escalation_failure_exit_code(self):
        code, rec = self._run(
            join=_join(False), wa=_wa("no_link"),
            esc=_esc(attempted=True, agent_exit=3),
        )
        self.assertEqual(code, 1)
        self.assertEqual(rec["status"], "escalated")

    def test_escalation_not_attempted_exit_code(self):
        code, rec = self._run(
            join=_join(False), wa=_wa("no_link"),
            esc=_esc(attempted=False, agent_exit=None),
        )
        self.assertEqual(code, 1)
        self.assertEqual(rec["status"], "escalation_failed")


if __name__ == "__main__":
    unittest.main()
