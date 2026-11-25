"""Pure helpers of the media page: time text, progress and marquee motion."""

import re


def format_time(microseconds: int | None) -> str:
    """Format a media time as ``m:ss`` or ``h:mm:ss``.

    Args:
        microseconds: Time in microseconds, as MPRIS reports it.

    Returns:
        The formatted time, ``0:00`` for missing or negative values.
    """
    seconds = max(0, int(microseconds or 0)) // 1_000_000
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def progress_fraction(position: int | None, length: int | None) -> float:
    """Playback progress between 0.0 and 1.0.

    Args:
        position: Current position in microseconds.
        length: Track length in microseconds.

    Returns:
        The fraction played, 0.0 when the length is unknown.
    """
    if not length or length <= 0:
        return 0.0
    return min(1.0, max(0.0, (position or 0) / length))


def estimate_position(
    position: int, elapsed_seconds: float, playing: bool, length: int | None
) -> int:
    """Advance the last reported position while the track is playing.

    Args:
        position: Position in microseconds at the last report.
        elapsed_seconds: Seconds since that report.
        playing: True when the player is playing.
        length: Track length in microseconds, if known.

    Returns:
        The estimated position, capped at the track length.
    """
    if not playing:
        return position
    estimate = position + int(max(0.0, elapsed_seconds) * 1_000_000)
    if length and length > 0:
        return min(estimate, length)
    return estimate


def marquee_offset(
    elapsed_us: int, span: float, speed: float, pause_us: int
) -> float:
    """Horizontal scroll offset of a ping-pong marquee.

    The text rests at the start, scrolls to the end, rests again and scrolls
    back, so the label never changes size while it moves.

    Args:
        elapsed_us: Microseconds since the animation started.
        span: Distance in pixels between the start and the end position.
        speed: Scroll speed in pixels per second.
        pause_us: Microseconds to rest at each end.

    Returns:
        The offset in pixels between 0 and ``span``.
    """
    if span <= 0 or speed <= 0:
        return 0.0
    travel_us = int(span / speed * 1_000_000)
    cycle = 2 * (pause_us + travel_us)
    t = elapsed_us % cycle
    if t < pause_us:
        return 0.0
    if t < pause_us + travel_us:
        return span * (t - pause_us) / travel_us
    if t < 2 * pause_us + travel_us:
        return span
    return span * (1 - (t - 2 * pause_us - travel_us) / travel_us)


def player_name(service: str) -> str:
    """Readable application name from an MPRIS bus name.

    Args:
        service: Bus name such as ``org.mpris.MediaPlayer2.firefox.instance_1_2``.

    Returns:
        The name, for example ``Firefox``; empty when nothing usable is left.
    """
    rest = service.removeprefix("org.mpris.MediaPlayer2.")
    parts = [p for p in rest.split(".") if p and not p.startswith("instance")]
    if not parts:
        return ""
    name = re.sub(r"[-_]+", " ", parts[-1]).strip()
    return name[:1].upper() + name[1:] if name.islower() else name
