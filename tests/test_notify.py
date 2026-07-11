import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoomjoin import notify


class TestResolveNtfyTopic(unittest.TestCase):
    def test_env_var_wins(self):
        with mock.patch.dict(os.environ, {"ZOOMJOIN_NTFY_TOPIC": "env-topic"}, clear=False):
            with mock.patch.object(notify, "CONFIG_PATH", Path("does-not-exist.json")):
                self.assertEqual(notify.resolve_ntfy_topic(), "env-topic")

    def test_config_json_used_when_no_env(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZOOMJOIN_NTFY_TOPIC", None)
            tmp_dir = Path(os.environ.get("TEMP", ".")) / "zj_test_notify"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            cfg = tmp_dir / "config.json"
            cfg.write_text(json.dumps({"ntfy_topic": "cfg-topic"}), encoding="utf-8")
            try:
                with mock.patch.object(notify, "CONFIG_PATH", cfg):
                    self.assertEqual(notify.resolve_ntfy_topic(), "cfg-topic")
            finally:
                cfg.unlink()

    def test_none_when_neither_present(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZOOMJOIN_NTFY_TOPIC", None)
            with mock.patch.object(notify, "CONFIG_PATH", Path("does-not-exist.json")):
                self.assertIsNone(notify.resolve_ntfy_topic())

    def test_malformed_config_json_tolerated(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZOOMJOIN_NTFY_TOPIC", None)
            tmp_dir = Path(os.environ.get("TEMP", ".")) / "zj_test_notify"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            cfg = tmp_dir / "config_bad.json"
            cfg.write_text("{not valid json", encoding="utf-8")
            try:
                with mock.patch.object(notify, "CONFIG_PATH", cfg):
                    self.assertIsNone(notify.resolve_ntfy_topic())
            finally:
                cfg.unlink()

    def test_empty_env_var_falls_through(self):
        with mock.patch.dict(os.environ, {"ZOOMJOIN_NTFY_TOPIC": ""}, clear=False):
            with mock.patch.object(notify, "CONFIG_PATH", Path("does-not-exist.json")):
                self.assertIsNone(notify.resolve_ntfy_topic())


class TestNotify(unittest.TestCase):
    def test_notify_never_raises_when_subprocess_raises(self):
        with mock.patch("zoomjoin.notify.subprocess.run", side_effect=RuntimeError("boom")):
            with mock.patch.object(notify, "resolve_ntfy_topic", return_value=None):
                try:
                    notify.notify("title", "message")
                except Exception:  # noqa: BLE001
                    self.fail("notify() raised")

    def test_notify_no_topic_skips_ntfy(self):
        with mock.patch("zoomjoin.notify.subprocess.run") as run_mock:
            with mock.patch.object(notify, "resolve_ntfy_topic", return_value=None):
                with mock.patch("zoomjoin.notify.urllib.request.urlopen") as urlopen_mock:
                    notify.notify("title", "message")
                    run_mock.assert_called_once()
                    urlopen_mock.assert_not_called()

    def test_notify_posts_to_ntfy_when_topic_set(self):
        with mock.patch("zoomjoin.notify.subprocess.run"):
            with mock.patch.object(notify, "resolve_ntfy_topic", return_value="my-topic"):
                with mock.patch("zoomjoin.notify.urllib.request.urlopen") as urlopen_mock:
                    notify.notify("hello", "world")
                    urlopen_mock.assert_called_once()
                    req = urlopen_mock.call_args[0][0]
                    self.assertEqual(req.full_url, "https://ntfy.sh/my-topic")
                    self.assertEqual(req.get_header("Title"), "hello")

    def test_ntfy_failure_swallowed(self):
        with mock.patch("zoomjoin.notify.subprocess.run"):
            with mock.patch.object(notify, "resolve_ntfy_topic", return_value="my-topic"):
                with mock.patch(
                    "zoomjoin.notify.urllib.request.urlopen",
                    side_effect=OSError("network down"),
                ):
                    try:
                        notify.notify("hello", "world")
                    except Exception:  # noqa: BLE001
                        self.fail("notify() raised on ntfy failure")


if __name__ == "__main__":
    unittest.main()
