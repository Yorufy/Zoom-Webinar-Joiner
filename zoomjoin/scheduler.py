"""Windows Task Scheduler integration with wake-from-sleep support.

`schtasks /Create` command-line flags cannot set WakeToRun, so we generate a
full Task Scheduler XML document (UTF-16, as schtasks requires) and register
it with `schtasks /Create /TN <name> /XML <file> /F`.

XML generation lives in the pure `build_task_xml()` function so tests can
assert on its contents without touching the real scheduler.
"""

from __future__ import annotations

import getpass
import logging
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

TASK_NAME_PREFIX = "zoomjoin-"

# How long past the fire time the task stays registered before it deletes
# itself (self-cleanup), and the ExecutionTimeLimit for the join attempt.
END_BOUNDARY_DELTA = timedelta(hours=1)
DELETE_EXPIRED_TASK_AFTER = "PT1H"
EXECUTION_TIME_LIMIT = "PT4H"


def task_name(meeting_id: str) -> str:
    return f"{TASK_NAME_PREFIX}{meeting_id}"


def _python_executable() -> str:
    """Prefer pythonw.exe next to sys.executable (no console window)."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        return str(pythonw)
    return sys.executable


def _iso_no_micro(dt: datetime) -> str:
    """Task Scheduler wants local time without a UTC offset or microseconds."""
    return dt.replace(microsecond=0).isoformat()


def build_task_xml(
    meeting_id: str,
    run_at: datetime,
    *,
    python_exe: str | None = None,
    repo_dir: str | Path | None = None,
    user_id: str | None = None,
) -> str:
    """Build the Task Scheduler XML for a one-shot zoomjoin run task.

    Pure function: no filesystem/registry access, safe to unit test.
    """
    python_exe = python_exe or _python_executable()
    repo_dir = str(Path(repo_dir) if repo_dir is not None else Path(__file__).resolve().parent.parent)
    user_id = user_id or getpass.getuser()

    start_boundary = _iso_no_micro(run_at)
    end_boundary = _iso_no_micro(run_at + END_BOUNDARY_DELTA)

    command = escape(python_exe)
    arguments = escape(f"-m zoomjoin run {meeting_id}")
    working_dir = escape(repo_dir)
    author = escape(user_id)

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>{author}</Author>
    <Description>zoomjoin: join meeting {escape(meeting_id)} at {escape(start_boundary)}</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{start_boundary}</StartBoundary>
      <EndBoundary>{end_boundary}</EndBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{author}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>{EXECUTION_TIME_LIMIT}</ExecutionTimeLimit>
    <Priority>7</Priority>
    <DeleteExpiredTaskAfter>{DELETE_EXPIRED_TASK_AFTER}</DeleteExpiredTaskAfter>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{working_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def create_task(meeting_id: str, run_at: datetime) -> None:
    """Register a one-shot scheduled task that runs `zoomjoin run <id>`."""
    xml = build_task_xml(meeting_id, run_at)

    # schtasks /Create /XML requires a UTF-16 encoded file.
    fd_path = None
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-16", suffix=".xml", delete=False
    ) as f:
        f.write(xml)
        fd_path = f.name

    try:
        result = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                task_name(meeting_id),
                "/XML",
                fd_path,
                "/F",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"schtasks /Create failed (code {result.returncode}): "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
        logger.info("created scheduled task %s for %s", task_name(meeting_id), run_at)
    finally:
        try:
            Path(fd_path).unlink(missing_ok=True)
        except OSError:
            pass


def delete_task(meeting_id: str) -> None:
    """Delete the scheduled task, tolerating it already being gone."""
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name(meeting_id), "/F"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        combined = (result.stdout or "") + (result.stderr or "")
        if "cannot find" in combined.lower() or "does not exist" in combined.lower():
            logger.info("task %s already gone", task_name(meeting_id))
            return
        raise RuntimeError(
            f"schtasks /Delete failed (code {result.returncode}): "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    logger.info("deleted scheduled task %s", task_name(meeting_id))


def task_exists(meeting_id: str) -> bool:
    """Return True if the scheduled task is currently registered."""
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name(meeting_id)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
