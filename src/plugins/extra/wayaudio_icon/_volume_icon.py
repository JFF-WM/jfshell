"""Pure helpers that map the output volume to the systray audio icon."""

LOW_BELOW = 20
HIGH_FROM = 80

ICONS_MUTED: tuple[str, ...] = (
    "audio-volume-muted-symbolic",
    "audio-volume-low-symbolic",
)
ICONS_LOW: tuple[str, ...] = (
    "audio-volume-low-symbolic",
    "audio-volume-medium-symbolic",
    "audio-volume-high-symbolic",
)
ICONS_MEDIUM: tuple[str, ...] = (
    "audio-volume-medium-symbolic",
    "audio-volume-high-symbolic",
)
ICONS_HIGH: tuple[str, ...] = ("audio-volume-high-symbolic",)


def volume_percent(value_flat: float) -> int:
    """Convert a PulseAudio flat volume (1.0 is 100%) to a whole percent.

    Args:
        value_flat: Average volume over all channels, as ``value_flat``.

    Returns:
        The volume in percent, never negative.
    """
    return max(0, round(value_flat * 100))


def icon_candidates(percent: int | None, muted: bool) -> tuple[str, ...]:
    """Return the icon names to try for a volume state, best match first.

    Args:
        percent: Volume of the default output in percent, or None when there
            is no output device.
        muted: True when the output is muted.

    Returns:
        Icon names ordered by preference, ending with one every theme has.
    """
    if percent is None or muted or percent <= 0:
        return ICONS_MUTED
    if percent < LOW_BELOW:
        return ICONS_LOW
    if percent < HIGH_FROM:
        return ICONS_MEDIUM
    return ICONS_HIGH


def tooltip_text(percent: int | None, muted: bool) -> str:
    """Build the tooltip of the systray audio icon.

    Args:
        percent: Volume of the default output in percent, or None when there
            is no output device.
        muted: True when the output is muted.

    Returns:
        Human readable summary of the output state.
    """
    if percent is None:
        return "No audio output"
    if muted:
        return f"Muted ({percent}%)"
    return f"Volume {percent}%"
