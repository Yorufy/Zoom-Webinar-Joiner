"""Escalation stage: bundle context and hand off a failed join to a headless
Claude Code agent (`claude -p`) that troubleshoots per `runbooks/escalation.md`,
then notifies the user of the outcome. Best-effort throughout — escalate()
must never raise, it is the last line of defense.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import notify

logger = logging.getLogger(__name__)

REPO_ROOT = notify.REPO_ROOT
CONFIG_PATH = notify.CONFIG_PATH

CONTEXT_FILENAME_PREFIX = "escalation-context-"
AGENT_TIMEOUT_S = 16 * 60  # hard subprocess cap; runbook's soft cap is 15 min
DEFAULT_MODEL = "sonnet"  # cost control; overridable (see resolve_* below)
ALLOWED_TOOLS = ["Read", "Glob", "Grep", "Bash(python:*)", "Bash(python.exe:*)"]


@dataclass
class EscalateResult:
    attempted: bool  # did we actually launch the agent?
    agent_exit: int | None  # claude -p exit code, or None if not launched / timed out
    detail: str


def resolve_claude_bin() -> str:
    """Resolve the `claude` binary name/path.

    Precedence: ZOOMJOIN_CLAUDE_BIN env var (if set and non-empty), else
    "claude_bin" in config.json at the repo root, else "claude". A missing
    or malformed config.json is tolerated.
    """
    env_bin = os.environ.get("ZOOMJOIN_CLAUDE_BIN")
    if env_bin:
        return env_bin

    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        claude_bin = data.get("claude_bin")
        if claude_bin:
            return claude_bin
    except Exception:  # noqa: BLE001
        logger.debug("resolve_claude_bin: no usable config.json at %s", CONFIG_PATH, exc_info=True)

    return "claude"


def resolve_model() -> str:
    """Resolve the model to run the escalation agent with.

    Precedence: ZOOMJOIN_ESCALATE_MODEL env var (if set and non-empty), else
    "escalate_model" in config.json at the repo root, else DEFAULT_MODEL. A
    missing or malformed config.json is tolerated.
    """
    env_model = os.environ.get("ZOOMJOIN_ESCALATE_MODEL")
    if env_model:
        return env_model

    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        model = data.get("escalate_model")
        if model:
            return model
    except Exception:  # noqa: BLE001
        logger.debug("resolve_model: no usable config.json at %s", CONFIG_PATH, exc_info=True)

    return DEFAULT_MODEL


def escalate(
    meeting_id: str,
    record: dict,
    log_path: Path,
    tried: list[str],
    *,
    runner=subprocess.run,  # injected for tests
    notify_fn=notify.notify,  # injected for tests
) -> EscalateResult:
    """Hand off a failed join to a headless Claude Code agent.

    Best-effort at every step; never raises.
    """
    notify_fn("Zoom join escalating", f"{meeting_id}: auto-join failed, agent troubleshooting")

    context_path = log_path.parent / f"{CONTEXT_FILENAME_PREFIX}{log_path.stem}.json"
    context = {
        "meeting_id": meeting_id,
        "record": record,
        "tried": tried,
        "log_dir": str(log_path.parent),
        "log_path": str(log_path),
    }
    try:
        with open(context_path, "w", encoding="utf-8") as f:
            json.dump(context, f, indent=2)
    except Exception:  # noqa: BLE001
        logger.warning("failed to write escalation context bundle to %s", context_path, exc_info=True)

    claude_bin = shutil.which(resolve_claude_bin())
    if not claude_bin:
        logger.error("claude CLI not found; cannot escalate for meeting %s", meeting_id)
        notify_fn("Zoom escalation failed", f"{meeting_id}: claude CLI not found — cannot auto-troubleshoot")
        return EscalateResult(attempted=False, agent_exit=None, detail="claude CLI not found")

    runbook_path = REPO_ROOT / "runbooks" / "escalation.md"
    try:
        runbook = runbook_path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.error("escalation runbook missing at %s", runbook_path, exc_info=True)
        notify_fn("Zoom escalation failed", f"{meeting_id}: escalation runbook missing")
        return EscalateResult(attempted=False, agent_exit=None, detail="runbook missing")

    prompt = runbook + f"\n\nCONTEXT FILE: {context_path}\n"

    argv = [
        claude_bin,
        "-p",
        prompt,
        "--allowedTools",
        *ALLOWED_TOOLS,
        "--model",
        resolve_model(),
    ]

    try:
        completed = runner(
            argv,
            cwd=str(REPO_ROOT),
            timeout=AGENT_TIMEOUT_S,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ},
        )
    except subprocess.TimeoutExpired:
        logger.warning("escalation agent timed out for meeting %s", meeting_id, exc_info=True)
        notify_fn("Zoom escalation timed out", f"{meeting_id}: agent hit 16 min cap")
        return EscalateResult(attempted=True, agent_exit=None, detail="timed out")
    except Exception as exc:  # noqa: BLE001
        logger.error("escalation agent invocation failed for meeting %s", meeting_id, exc_info=True)
        notify_fn("Zoom escalation failed", f"{meeting_id}: agent invocation error — see logs")
        return EscalateResult(attempted=True, agent_exit=None, detail=str(exc))

    transcript_path = log_path.parent / f"escalation-agent-{log_path.stem}.log"
    try:
        transcript = completed.stdout or ""
        if getattr(completed, "stderr", None):
            transcript += "\n--- stderr ---\n" + completed.stderr
        transcript_path.write_text(transcript, encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("failed to persist escalation agent transcript to %s", transcript_path, exc_info=True)

    if completed.returncode == 0:
        notify_fn("Zoom escalation done", f"{meeting_id}: agent finished — check Zoom")
    else:
        notify_fn("Zoom escalation ended", f"{meeting_id}: agent could not join — see logs")

    return EscalateResult(
        attempted=True,
        agent_exit=completed.returncode,
        detail=f"agent exited {completed.returncode}",
    )
