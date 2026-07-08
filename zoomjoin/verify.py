"""In-meeting detection for the Zoom desktop client.

Hard constraint: this module must work on a **locked** workstation, so it
uses ONLY process enumeration and Win32 window enumeration via ctypes
(EnumWindows / GetClassName / GetWindowTextW). No input injection
(SendInput/mouse/keyboard) and no screenshots — both are unavailable or
unreliable on a locked session and are intentionally not used here.
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Zoom signature constants — MUST be re-validated whenever Zoom updates.
# Validated 2026-07-08 against a live join (Zoom Workplace client) via
# tools/discover_zoom_windows.py; trace in phase1testoutput.md, notes in
# .frugal-fable/phase-1/report.md.
# --------------------------------------------------------------------------

# Executable names (case-insensitive) belonging to the Zoom desktop client.
# "Zoom.exe" hosts both the launcher and per-meeting renderer processes;
# "CptHost.exe" is the screen-share host (confirmed in the live trace).
ZOOM_PROCESS_NAMES = {
    "zoom.exe",
    "cpthost.exe",
}

# Legacy in-meeting window class (pre-Workplace clients). Class presence
# alone is sufficient for these.
IN_MEETING_WINDOW_CLASSES = {
    "ZPContentViewWndClass",
}

# Zoom Workplace clients use ConfMultiTabContentWndClass for the meeting
# window — but the class ALONE is a false positive: it exists with title
# "Zoom Workplace" while still connecting, and Zoom pre-spawns an idle
# renderer process carrying it after a meeting ends. The title flips to
# "Zoom Meeting" (or "Zoom Webinar") only once actually joined.
CONF_WINDOW_CLASS = "ConfMultiTabContentWndClass"
CONF_IN_MEETING_TITLES = {
    "Zoom Meeting",
    "Zoom Webinar",
}

# Modal dialog shown when a join attempt fails outright.
JOIN_FAILED_DIALOG_CLASSES = {
    "zJoinMeetingFailedDlgClass",
}

# "Connecting…" splash shown between launch and joined.
CONNECTING_WINDOW_CLASSES = {
    "zWaitingMeetingIDWndClass",
}

# --------------------------------------------------------------------------
# Win32 plumbing (ctypes only — no pywin32).
# --------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

WM_CLOSE = 0x0010


def _get_window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _get_window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _get_process_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _get_process_exe_name(pid: int) -> str | None:
    if pid == 0:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf_len = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(buf_len.value)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(buf_len))
        if not ok:
            return None
        return os.path.basename(buf.value)
    finally:
        kernel32.CloseHandle(handle)


def snapshot() -> list[dict]:
    """Return all top-level windows belonging to Zoom processes.

    Each entry: {"pid": int, "exe": str, "class": str, "title": str,
    "visible": bool}. Uses only window/process enumeration — safe on a
    locked workstation.
    """
    results: list[dict] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        pid = _get_process_pid(hwnd)
        exe = _get_process_exe_name(pid)
        if not exe or exe.lower() not in ZOOM_PROCESS_NAMES:
            return True  # continue enumeration

        results.append(
            {
                "pid": pid,
                "exe": exe,
                "hwnd": hwnd,
                "class": _get_window_class(hwnd),
                "title": _get_window_title(hwnd),
                "visible": bool(user32.IsWindowVisible(hwnd)),
            }
        )
        return True

    proc = EnumWindowsProc(callback)
    user32.EnumWindows(proc, 0)
    return results


def is_in_meeting() -> bool:
    """Return True if a Zoom in-meeting window is currently present.

    Two signals count: a legacy IN_MEETING_WINDOW_CLASSES window (class
    alone suffices), or a CONF_WINDOW_CLASS window whose title is one of
    CONF_IN_MEETING_TITLES (class alone is a false positive — see the
    constants block). Visibility is not required: the meeting window can be
    hidden/minimized (or the session locked) while a meeting is active.
    """
    for win in snapshot():
        if win["class"] in IN_MEETING_WINDOW_CLASSES:
            logger.debug("in-meeting window found: %r", win)
            return True
        if win["class"] == CONF_WINDOW_CLASS and win["title"] in CONF_IN_MEETING_TITLES:
            logger.debug("in-meeting conf window found: %r", win)
            return True
    return False


def has_join_failed_dialog() -> bool:
    """Return True if Zoom's join-failed modal dialog is present."""
    return any(win["class"] in JOIN_FAILED_DIALOG_CLASSES for win in snapshot())


def is_connecting() -> bool:
    """Return True if Zoom's 'Connecting…' splash window is present."""
    return any(win["class"] in CONNECTING_WINDOW_CLASSES for win in snapshot())


# Classes closed by close_meeting_windows(): the in-meeting window, the
# Workplace conf window (also covers the "meeting ended" dialog, which
# reuses CONF_WINDOW_CLASS with a different title), and the join-failed
# dialog. The always-running Zoom main/launcher window uses none of these
# classes and is therefore never targeted.
MEETING_CLOSE_CLASSES = IN_MEETING_WINDOW_CLASSES | {CONF_WINDOW_CLASS} | JOIN_FAILED_DIALOG_CLASSES


def close_meeting_windows() -> int:
    """Best-effort: post WM_CLOSE to every meeting-scoped/dialog window.

    Never targets the Zoom main/launcher window. Never terminates a
    process. Never raises — any failure is logged and skipped.
    Returns the number of windows a close was posted to.
    """
    closed = 0
    try:
        wins = snapshot()
    except Exception:  # noqa: BLE001
        logger.info("close_meeting_windows: snapshot() failed", exc_info=True)
        return 0

    for win in wins:
        if win.get("class") not in MEETING_CLOSE_CLASSES:
            continue
        hwnd = win.get("hwnd")
        if not hwnd:
            continue
        try:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            closed += 1
            logger.debug("posted WM_CLOSE to hwnd=%s class=%r title=%r", hwnd, win.get("class"), win.get("title"))
        except Exception:  # noqa: BLE001
            logger.debug("PostMessageW failed for hwnd=%s", hwnd, exc_info=True)

    logger.info("close_meeting_windows: posted WM_CLOSE to %d window(s)", closed)
    return closed
