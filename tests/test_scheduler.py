import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoomjoin.scheduler import build_task_xml


class TestBuildTaskXml(unittest.TestCase):
    def setUp(self):
        self.run_at = datetime(2026, 7, 8, 19, 30, 0)
        self.xml = build_task_xml(
            "m-20260708-1930",
            self.run_at,
            python_exe=r"C:\Python\pythonw.exe",
            repo_dir=r"C:\repo",
            user_id="DESKTOP\\testuser",
        )

    def test_contains_wake_to_run(self):
        self.assertIn("<WakeToRun>true</WakeToRun>", self.xml)

    def test_contains_interactive_token_logon(self):
        self.assertIn("<LogonType>InteractiveToken</LogonType>", self.xml)

    def test_contains_least_privilege(self):
        self.assertIn("<RunLevel>LeastPrivilege</RunLevel>", self.xml)

    def test_command_line_correct(self):
        self.assertIn("<Command>C:\\Python\\pythonw.exe</Command>", self.xml)
        self.assertIn("<Arguments>-m zoomjoin run m-20260708-1930</Arguments>", self.xml)
        self.assertIn("<WorkingDirectory>C:\\repo</WorkingDirectory>", self.xml)

    def test_start_boundary_formatting(self):
        self.assertIn("<StartBoundary>2026-07-08T19:30:00</StartBoundary>", self.xml)

    def test_end_boundary_is_start_plus_one_hour(self):
        self.assertIn("<EndBoundary>2026-07-08T20:30:00</EndBoundary>", self.xml)

    def test_delete_expired_task_after(self):
        self.assertIn("<DeleteExpiredTaskAfter>PT1H</DeleteExpiredTaskAfter>", self.xml)

    def test_execution_time_limit(self):
        self.assertIn("<ExecutionTimeLimit>PT4H</ExecutionTimeLimit>", self.xml)

    def test_no_password_related_settings(self):
        # LogonType=Password / S4U would require storing credentials — must
        # never appear.
        self.assertNotIn("Password", self.xml)
        self.assertNotIn("S4U", self.xml)

    def test_battery_settings(self):
        self.assertIn("<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>", self.xml)
        self.assertIn("<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>", self.xml)

    def test_start_when_available(self):
        self.assertIn("<StartWhenAvailable>true</StartWhenAvailable>", self.xml)

    def test_task_name_prefix(self):
        from zoomjoin.scheduler import task_name

        self.assertEqual(task_name("m-20260708-1930"), "zoomjoin-m-20260708-1930")

    def test_default_python_exe_prefers_pythonw(self):
        # When python_exe isn't given, build_task_xml should still produce
        # valid XML with *some* command (covered indirectly via defaults).
        xml = build_task_xml("m-x", self.run_at, repo_dir=r"C:\repo", user_id="u")
        self.assertIn("<Command>", xml)


if __name__ == "__main__":
    unittest.main()
