"""Pure text helpers for the clipboard dialog: sizes, ages and row titles."""

import time
from datetime import datetime


def format_size(size: int) -> str:
    """Human readable byte size: ``19 B``, ``5.2 KB``, ``1.4 MB``."""
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024.0
        if value < 1024.0 or unit == "GB":
            return f"{value:.1f} {unit}"
    return f"{size} B"


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


def first_line(text: str, limit: int) -> str:
    """The first non-empty line of ``text``, cut to ``limit`` characters."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[: max(1, limit)]
    return ""
