"""argparse CLI: add / list / remove / run."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import dispatch, escalate, joiner, monitor, notify, scheduler, whatsapp, zoom_url
from .store import Store

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = REPO_ROOT / "logs"

AT_FORMAT = "%Y-%m-%d %H:%M"


def _parse_at(raw: str) -> datetime:
    try:
        return datetime.strptime(raw, AT_FORMAT)
    except ValueError as exc:
        raise ValueError(
            f"--at must look like 'YYYY-MM-DD HH:MM', got {raw!r}"
        ) from exc


def cmd_add(args: argparse.Namespace, store: Store) -> int:
    try:
        zoom_url.parse(args.url)
    except ValueError as exc:
        print(f"error: not a valid zoom url: {exc}", file=sys.stderr)
        return 1

    try:
        at_dt = _parse_at(args.at)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if at_dt <= datetime.now():
        print(f"error: --at ({args.at}) is in the past", file=sys.stderr)
        return 1

    record = {
        "url": args.url,
        "at": at_dt.isoformat(),
        "group": args.group,
        "duration_min": args.duration,
        "created": datetime.now().isoformat(),
        "status": "scheduled",
    }
    meeting_id = store.add(record)

    try:
        scheduler.create_task(meeting_id, at_dt)
    except Exception as exc:  # noqa: BLE001
        store.remove(meeting_id)
        print(f"error: failed to create scheduled task: {exc}", file=sys.stderr)
        return 1

    print(f"scheduled {meeting_id} at {at_dt.strftime(AT_FORMAT)}")
    return 0


def cmd_dispatch(args: argparse.Namespace, store: Store) -> int:
    try:
        parsed = dispatch.parse_dispatch(args.text, now=datetime.now())
    except dispatch.DispatchParseError as exc:
        print(f"error: could not parse dispatch message: {exc}", file=sys.stderr)
        print(dispatch.USAGE_HINT, file=sys.stderr)
        return 2
    # Reuse the exact add path so meetings.json + schtasks behavior is identical.
    add_args = argparse.Namespace(
        url=parsed.url,
        at=parsed.when.strftime(AT_FORMAT),
        group=parsed.group,
        duration=parsed.duration_min,
    )
    return cmd_add(add_args, store)


def cmd_list(args: argparse.Namespace, store: Store) -> int:
    records = sorted(store.list_all(), key=lambda r: r.get("at", ""))
    if not records:
        print("no meetings scheduled")
        return 0

    header = f"{'id':<20} {'at':<17} {'status':<12} {'group':<15} url"
    print(header)
    for r in records:
        url = r.get("url", "")
        url_trunc = url if len(url) <= 40 else url[:37] + "..."
        group = r.get("group") or ""
        print(
            f"{r.get('id', ''):<20} {r.get('at', ''):<17} "
            f"{r.get('status', ''):<12} {group:<15} {url_trunc}"
        )
    return 0


def cmd_remove(args: argparse.Namespace, store: Store) -> int:
    meeting_id = args.id
    try:
        scheduler.delete_task(meeting_id)
    except Exception as exc:  # noqa: BLE001
        print(f"warning: failed to delete scheduled task: {exc}", file=sys.stderr)

    removed = store.remove(meeting_id)
    if not removed:
        print(f"error: no such meeting id: {meeting_id}", file=sys.stderr)
        return 1

    print(f"removed {meeting_id}")
    return 0


def _setup_run_logging(meeting_id: str) -> Path:
    log_dir = LOGS_DIR / meeting_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(fmt)
    root.addHandler(stream_handler)

    return log_path


def _attend(store: Store, meeting_id: str, logger: logging.Logger) -> monitor.MonitorResult:
    """Run the post-join monitor + notifications.

    Returns the MonitorResult so the caller can re-enter the fallback chain on
    an early crash ("crashed_early") versus a clean host-ended webinar
    ("ended").
    """
    store.update(meeting_id, status="joined")
    notify.notify("Zoom webinar joined", f"{meeting_id}: attending")

    mon = monitor.monitor()
    if mon.outcome == "ended":
        store.update(meeting_id, status="ended")
        logger.info("meeting %s ended after %.1fs", meeting_id, mon.duration_s)
        notify.notify(
            "Zoom webinar ended",
            f"{meeting_id}: attended {int(mon.duration_s // 60)} min",
        )
    else:
        store.update(meeting_id, status="crashed")
        logger.warning(
            "Zoom dropped after %.0fs (< %ds) — re-entering fallback chain",
            mon.duration_s,
            monitor.CRASH_THRESHOLD_S,
        )
        notify.notify("Zoom dropped early", f"{meeting_id}: reconnect needed")
    return mon


def _fallback_chain(
    store: Store,
    meeting_id: str,
    record: dict,
    log_path: Path,
    logger: logging.Logger,
    tried: list[str],
    *,
    failed_url: str,
) -> int:
    """WhatsApp fallback then escalation, after a direct join failed or dropped.

    Bounded: WhatsApp is scanned at most once; a WhatsApp-recovered join is
    attended at most once. If that recovered join also drops early, we escalate
    rather than loop back into WhatsApp. Returns the process exit code.
    """
    group = record.get("group")
    if group:
        since = None
        try:
            since = datetime.fromisoformat(record["at"]) - timedelta(minutes=60)
        except (ValueError, KeyError):
            pass
        logger.info("attempting WhatsApp fallback in group %r", group)
        wa = whatsapp.find_replacement_link(group, failed_url=failed_url, since=since)
        logger.info("WhatsApp fallback: status=%s detail=%s", wa.status, wa.detail)
        if wa.status == "found" and wa.link:
            tried.append("whatsapp fallback recovered a replacement link; retried join")
            retry = joiner.join(wa.link)
            logger.info("retry JoinResult: joined=%s detail=%s", retry.joined, retry.detail)
            if retry.joined:
                store.update(meeting_id, recovered_url=wa.link)
                logger.info("meeting %s joined via WhatsApp-recovered link", meeting_id)
                mon = _attend(store, meeting_id, logger)
                if mon.outcome == "ended":
                    return 0
                tried.append("recovered-link join also dropped early (<10 min)")
            else:
                tried[-1] = "whatsapp fallback recovered a link but the retry join still failed"
        else:
            tried.append(f"whatsapp fallback: {wa.status} ({wa.detail})")
    else:
        tried.append("no whatsapp group configured for this meeting")

    # --- fallbacks exhausted: escalate --------------------------------------
    esc = escalate.escalate(meeting_id, record, log_path, tried=tried)
    store.update(meeting_id, status="escalated" if esc.attempted else "escalation_failed")
    return 0 if (esc.attempted and esc.agent_exit == 0) else 1


def cmd_run(args: argparse.Namespace, store: Store) -> int:
    meeting_id = args.id
    log_path = _setup_run_logging(meeting_id)
    logger = logging.getLogger(__name__)
    logger.info("run start for meeting %s, log=%s", meeting_id, log_path)

    record = store.get(meeting_id)
    if record is None:
        logger.error("no such meeting id: %s", meeting_id)
        return 1

    result = joiner.join(record["url"])
    logger.info(
        "JoinResult: joined=%s attempts=%s elapsed_s=%.1f detail=%s",
        result.joined,
        result.attempts,
        result.elapsed_s,
        result.detail,
    )

    if result.joined:
        logger.info("meeting %s joined successfully", meeting_id)
        mon = _attend(store, meeting_id, logger)
        if mon.outcome == "ended":
            return 0
        # Zoom dropped early: treat as a join failure, re-enter the fallback chain.
        tried = [f"direct join succeeded but Zoom dropped early after {mon.duration_s:.0f}s"]
        return _fallback_chain(
            store, meeting_id, record, log_path, logger, tried, failed_url=record["url"]
        )

    # --- direct join failed: fall through to WhatsApp fallback + escalation ---
    store.update(meeting_id, status="join_failed")
    logger.error("meeting %s failed to join: %s", meeting_id, result.detail)
    tried = ["direct zoom desktop join (2 attempts) failed"]
    return _fallback_chain(
        store, meeting_id, record, log_path, logger, tried, failed_url=record["url"]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zoomjoin")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="schedule a meeting to be joined")
    p_add.add_argument("--url", required=True, help="Zoom meeting/webinar link")
    p_add.add_argument("--at", required=True, help="'YYYY-MM-DD HH:MM' local time")
    p_add.add_argument("--group", default=None, help="WhatsApp group name")
    p_add.add_argument("--duration", type=int, default=None, dest="duration", help="duration hint in minutes")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="list scheduled meetings")
    p_list.set_defaults(func=cmd_list)

    p_remove = sub.add_parser("remove", help="remove a scheduled meeting")
    p_remove.add_argument("id")
    p_remove.set_defaults(func=cmd_remove)

    p_run = sub.add_parser("run", help="run the join for a meeting id (invoked by the scheduled task)")
    p_run.add_argument("id")
    p_run.set_defaults(func=cmd_run)

    p_disp = sub.add_parser(
        "dispatch",
        help="parse a free-text 'join <link> at <time>' message and schedule it",
    )
    p_disp.add_argument("text", help="the raw remote message, e.g. \"join <link> at tomorrow 2pm\"")
    p_disp.set_defaults(func=cmd_dispatch)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = Store()
    return args.func(args, store)
