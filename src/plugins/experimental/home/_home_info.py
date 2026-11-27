"""Pure helpers for the Home page of the clock popover.

Nothing here touches GTK, D-Bus or subprocesses: the plugin runs the commands
and hands their output to these functions, which keeps them testable.
"""

import re
from typing import Optional

PROFILE_ORDER = ("power-saver", "balanced", "performance")
PROFILE_LABELS = {
    "power-saver": "Power Saver",
    "balanced": "Balanced",
    "performance": "Performance",
}
PROFILE_ICONS = {
    "power-saver": "power-profile-power-saver-symbolic",
    "balanced": "power-profile-balanced-symbolic",
    "performance": "power-profile-performance-symbolic",
}

_PROFILE_LINE = re.compile(r"^(?:\*|\s)\s*([a-z][a-z-]*):$")
_NMCLI_ESCAPE = re.compile(r"\\(.)")


def parse_uptime(text: str) -> Optional[float]:
    """Seconds of uptime from the content of /proc/uptime, None if unreadable."""
    try:
        return float(text.split()[0])
    except (IndexError, ValueError):
        return None


def _plural(value: int, unit: str) -> str:
    return f"{value} {unit}" if value == 1 else f"{value} {unit}s"


def format_uptime(seconds: float) -> str:
    """Uptime as at most two units, for example "9 hours 59 minutes"."""
    total_minutes = max(0, int(seconds // 60))
    days, rest = divmod(total_minutes, 1440)
    hours, minutes = divmod(rest, 60)
    parts = []
    if days:
        parts.append(_plural(days, "day"))
    if hours:
        parts.append(_plural(hours, "hour"))
    if minutes or not parts:
        parts.append(_plural(minutes, "minute"))
    return " ".join(parts[:2])


def uptime_line(seconds: Optional[float]) -> str:
    """The uptime row of the user card."""
    if seconds is None:
        return "Uptime unknown"
    return f"Uptime – {format_uptime(seconds)}"


def parse_radio_enabled(text: str) -> bool:
    """True when `nmcli radio wifi` prints "enabled"."""
    return text.strip().lower() == "enabled"


def parse_active_ssid(text: str) -> str:
    """SSID of the connected network in `nmcli -t -f ACTIVE,SSID dev wifi`."""
    for line in text.splitlines():
        active, separator, ssid = line.partition(":")
        if separator and active == "yes":
            return _NMCLI_ESCAPE.sub(r"\1", ssid).strip()
    return ""


def parse_bluetooth_powered(text: str) -> bool:
    """True when the `Powered` line of `bluetoothctl show` says yes."""
    for line in text.splitlines():
        key, _, value = line.strip().partition(":")
        if key == "Powered":
            return value.strip().lower() == "yes"
    return False


def parse_profile_list(text: str) -> list[str]:
    """Known profile names printed by `powerprofilesctl list`, in cycle order."""
    found = set()
    for line in text.splitlines():
        match = _PROFILE_LINE.match(line.rstrip())
        if match:
            found.add(match.group(1))
    return [name for name in PROFILE_ORDER if name in found]


def next_profile(current: str, available: list[str]) -> Optional[str]:
    """The profile after `current`, wrapping around; None without profiles."""
    if not available:
        return None
    if current not in available:
        return available[0]
    return available[(available.index(current) + 1) % len(available)]


def profile_label(name: str) -> str:
    """Tile label of a power profile."""
    if not name:
        return "Power Profile"
    return PROFILE_LABELS.get(name, name.replace("-", " ").title())


def pick_player(players: list[dict]) -> Optional[dict]:
    """The player to show: one that is playing, else the first paused one.

    Players without a real title or in any other state (stopped) are skipped.
    """
    paused = None
    for player in players:
        title = (player.get("title") or "").strip()
        state = (player.get("status") or "").strip().lower()
        if not title or title.lower() == "unknown":
            continue
        if state == "playing":
            return player
        if state == "paused" and paused is None:
            paused = player
    return paused


def media_texts(player: Optional[dict]) -> tuple[str, str]:
    """Title and subtitle of the media card."""
    if not player:
        return "Nothing playing", "Idle"
    title = (player.get("title") or "").strip()
    artist = (player.get("artist") or "").strip()
    if artist.lower() == "unknown":
        artist = ""
    if (player.get("status") or "").strip().lower() == "paused":
        return title, f"Paused · {artist}" if artist else "Paused"
    return title, artist or "Playing"


def location_text(name: str) -> str:
    """Location row of the clock card."""
    return (name or "").strip() or "Location not set"
