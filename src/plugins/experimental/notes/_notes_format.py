"""Pure text helpers for the notes dialog: stored content, ages and reminders."""

import re
import time
from datetime import datetime

STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.*)$", re.DOTALL)


def new_stamp(now=None) -> str:
    """Local ``YYYY-MM-DD HH:MM:SS`` stamp stored in front of a note title."""
    moment = datetime.now() if now is None else datetime.fromtimestamp(now)
    return moment.strftime(STAMP_FORMAT)


def split_content(content: str):
    """Split stored content into ``(created_stamp, title)``.

    Notes are stored as ``"<stamp> <title>"``. Content without a leading stamp
    gives ``(None, content)``.
    """
    match = _STAMP_RE.match(content or "")
    if match:
        return match.group(1), match.group(2).strip()
    return None, (content or "").strip()


def join_content(stamp, title: str) -> str:
    """Inverse of :func:`split_content`."""
    return f"{stamp} {title}" if stamp else title


def format_created(stamp) -> str:
    """``Nov 23, 14:02`` for a stored stamp, or the raw stamp if unparsable."""
    if not stamp:
        return ""
    try:
        return datetime.strptime(stamp, STAMP_FORMAT).strftime("%b %d, %H:%M")
    except ValueError:
        return stamp


def format_age(timestamp, now=None) -> str:
    """How long ago ``timestamp`` (unix seconds) was: ``Just now``, ``3 min ago``,
    ``2 h ago``, ``4 days ago``, then a date such as ``Nov 14``."""
    if timestamp is None:
        return ""
    now = time.time() if now is None else now
    delta = max(0, int(now - timestamp))
    if delta < 60:
        return "Just now"
    if delta < 3600:
        return f"{delta // 60} min ago"
    if delta < 86400:
        return f"{delta // 3600} h ago"
    if delta < 7 * 86400:
        days = delta // 86400
        return f"{days} day ago" if days == 1 else f"{days} days ago"
    then = datetime.fromtimestamp(timestamp)
    if then.year != datetime.fromtimestamp(now).year:
        return then.strftime("%b %d, %Y")
    return then.strftime("%b %d")


def format_interval(seconds: int) -> str:
    """``5 min``, ``1 h``, ``1 h 30 min`` or ``2 days`` for a repeat interval."""
    minutes = max(1, int(seconds) // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {rest} min" if rest else f"{hours} h"
    days = hours // 24
    return f"{days} day" if days == 1 else f"{days} days"


def format_countdown(remind_at: int, now=None) -> str:
    """``now``, ``in 4 min``, ``in 2 h`` or ``in 3 days`` until ``remind_at``."""
    now = time.time() if now is None else now
    delta = int(remind_at - now)
    if delta <= 0:
        return "now"
    if delta < 60:
        return "in less than a minute"
    if delta < 3600:
        return f"in {delta // 60} min"
    if delta < 86400:
        return f"in {delta // 3600} h"
    return f"in {delta // 86400} days"


def reminder_summary(remind_at: int, interval: int, remaining: int, now=None) -> str:
    """One line describing an active reminder, or ``""`` when none is set."""
    if remaining <= 0 or remind_at <= 0:
        return ""
    times = "1 time left" if remaining == 1 else f"{remaining} times left"
    return (
        f"Next {format_countdown(remind_at, now)} \u2022 "
        f"every {format_interval(interval)} \u2022 {times}"
    )


def first_line(text: str, limit: int) -> str:
    """The first non-empty line of ``text``, cut to ``limit`` characters."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[: max(1, limit)]
    return ""


def matches_query(query: str, title: str, description: str) -> bool:
    """True when every word of ``query`` occurs in the title or description."""
    terms = (query or "").lower().split()
    if not terms:
        return True
    haystack = f"{title}\n{description}".lower()
    return all(term in haystack for term in terms)
