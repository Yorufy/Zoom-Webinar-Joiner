import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoomjoin import escalate


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_record():
    return {
        "id": "m-20260708-0900",
        "url": "https://zoom.us/j/123456789?pwd=SECRETPWD",
        "at": "2026-07-08T09:00:00",
        "group": "team-standup",
        "duration_min": 30,
        "status": "join_failed",
    }


class TestEscalate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        log_dir = Path(self._tmp.name)
        self.log_path = log_dir / "20260708-090000.log"
        self.log_path.write_text("log contents", encoding="utf-8")
        self.record = _make_record()
        self.meeting_id = "m-20260708-0900"
        self.tried = ["direct zoom desktop join (2 attempts) failed"]

    def _patch_which(self, found=True):
        return mock.patch(
            "zoomjoin.escalate.shutil.which",
            return_value=(r"C:\fake\claude.exe" if found else None),
        )

    def test_happy_path(self):
        runner = mock.Mock(return_value=FakeCompletedProcess(returncode=0, stdout="JOINED"))
        notify_fn = mock.Mock()

        with self._patch_which(found=True):
            result = escalate.escalate(
                self.meeting_id, self.record, self.log_path, self.tried,
                runner=runner, notify_fn=notify_fn,
            )

        self.assertTrue(result.attempted)
        self.assertEqual(result.agent_exit, 0)

        context_path = self.log_path.parent / f"{escalate.CONTEXT_FILENAME_PREFIX}{self.log_path.stem}.json"
        self.assertTrue(context_path.exists())
        context = json.loads(context_path.read_text(encoding="utf-8"))
        self.assertEqual(context["meeting_id"], self.meeting_id)
        self.assertEqual(context["tried"], self.tried)
        self.assertEqual(context["record"], self.record)

        self.assertTrue(runner.called)
        argv, kwargs = runner.call_args
        argv = argv[0]
        self.assertIn("-p", argv)
        self.assertIn("--allowedTools", argv)
        self.assertIn("--model", argv)
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertEqual(kwargs["cwd"], str(escalate.REPO_ROOT))

        titles = [c.args[0] for c in notify_fn.call_args_list]
        self.assertIn("Zoom join escalating", titles)
        self.assertIn("Zoom escalation done", titles)

    def test_agent_fails(self):
        runner = mock.Mock(return_value=FakeCompletedProcess(returncode=1, stdout="NOT JOINED"))
        notify_fn = mock.Mock()

        with self._patch_which(found=True):
            result = escalate.escalate(
                self.meeting_id, self.record, self.log_path, self.tried,
                runner=runner, notify_fn=notify_fn,
            )

        self.assertTrue(result.attempted)
        self.assertEqual(result.agent_exit, 1)
        titles = [c.args[0] for c in notify_fn.call_args_list]
        self.assertIn("Zoom escalation ended", titles)

    def test_claude_not_found(self):
        runner = mock.Mock()
        notify_fn = mock.Mock()

        with self._patch_which(found=False):
            result = escalate.escalate(
                self.meeting_id, self.record, self.log_path, self.tried,
                runner=runner, notify_fn=notify_fn,
            )

        self.assertFalse(result.attempted)
        self.assertIsNone(result.agent_exit)
        titles = [c.args[0] for c in notify_fn.call_args_list]
        self.assertIn("Zoom escalation failed", titles)
        runner.assert_not_called()

    def test_timeout(self):
        runner = mock.Mock(side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=escalate.AGENT_TIMEOUT_S))
        notify_fn = mock.Mock()

        with self._patch_which(found=True):
            result = escalate.escalate(
                self.meeting_id, self.record, self.log_path, self.tried,
                runner=runner, notify_fn=notify_fn,
            )

        self.assertTrue(result.attempted)
        self.assertIsNone(result.agent_exit)
        self.assertIn("timed out", result.detail)
        titles = [c.args[0] for c in notify_fn.call_args_list]
        self.assertIn("Zoom escalation timed out", titles)

    def test_runner_raises_generic_exception(self):
        runner = mock.Mock(side_effect=RuntimeError("boom"))
        notify_fn = mock.Mock()

        with self._patch_which(found=True):
            try:
                result = escalate.escalate(
                    self.meeting_id, self.record, self.log_path, self.tried,
                    runner=runner, notify_fn=notify_fn,
                )
            except Exception:  # noqa: BLE001
                self.fail("escalate() raised")

        self.assertTrue(result.attempted)
        self.assertIsNone(result.agent_exit)

    def test_prompt_hygiene_no_url_leak(self):
        runner = mock.Mock(return_value=FakeCompletedProcess(returncode=0, stdout="JOINED"))
        notify_fn = mock.Mock()

        with self._patch_which(found=True):
            escalate.escalate(
                self.meeting_id, self.record, self.log_path, self.tried,
                runner=runner, notify_fn=notify_fn,
            )

        argv = runner.call_args[0][0]
        prompt = argv[2]  # [claude_bin, "-p", prompt, ...]
        self.assertNotIn(self.record["url"], prompt)

    def test_resolve_claude_bin_precedence(self):
        with mock.patch.dict(os.environ, {"ZOOMJOIN_CLAUDE_BIN": "env-claude"}, clear=False):
            with mock.patch.object(escalate, "CONFIG_PATH", Path("does-not-exist.json")):
                self.assertEqual(escalate.resolve_claude_bin(), "env-claude")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZOOMJOIN_CLAUDE_BIN", None)
            tmp_dir = Path(self._tmp.name)
            cfg = tmp_dir / "config.json"
            cfg.write_text(json.dumps({"claude_bin": "cfg-claude"}), encoding="utf-8")
            with mock.patch.object(escalate, "CONFIG_PATH", cfg):
                self.assertEqual(escalate.resolve_claude_bin(), "cfg-claude")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZOOMJOIN_CLAUDE_BIN", None)
            with mock.patch.object(escalate, "CONFIG_PATH", Path("does-not-exist.json")):
                self.assertEqual(escalate.resolve_claude_bin(), "claude")

    def test_resolve_model_precedence(self):
        with mock.patch.dict(os.environ, {"ZOOMJOIN_ESCALATE_MODEL": "env-model"}, clear=False):
            with mock.patch.object(escalate, "CONFIG_PATH", Path("does-not-exist.json")):
                self.assertEqual(escalate.resolve_model(), "env-model")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZOOMJOIN_ESCALATE_MODEL", None)
            tmp_dir = Path(self._tmp.name)
            cfg = tmp_dir / "config2.json"
            cfg.write_text(json.dumps({"escalate_model": "cfg-model"}), encoding="utf-8")
            with mock.patch.object(escalate, "CONFIG_PATH", cfg):
                self.assertEqual(escalate.resolve_model(), "cfg-model")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZOOMJOIN_ESCALATE_MODEL", None)
            with mock.patch.object(escalate, "CONFIG_PATH", Path("does-not-exist.json")):
                self.assertEqual(escalate.resolve_model(), escalate.DEFAULT_MODEL)


if __name__ == "__main__":
    unittest.main()
