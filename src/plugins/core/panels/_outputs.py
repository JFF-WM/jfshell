"""Pure helpers about which monitor gets which copy of a panel.

Kept free of GTK so they can be tested without a display.
"""

from typing import Iterable, Optional


def plan_mirrors(
    monitors: Iterable[str], primary: Optional[str], existing: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Decide which per-monitor panel copies to create and which to drop.

    The bottom panel window already lives on ``primary``; every other monitor
    gets a copy.

    Args:
        monitors: Connector names of every monitor that is connected now.
        primary: Connector of the monitor the main panel window is on, or None
            when it is not known (then every monitor gets a copy).
        existing: Outputs that already have a copy.

    Returns:
        ``(to_add, to_remove)``, both in a stable order.
    """
    wanted = [name for name in dict.fromkeys(monitors) if name and name != primary]
    have = list(dict.fromkeys(existing))
    to_add = [name for name in wanted if name not in have]
    to_remove = [name for name in have if name not in wanted]
    return to_add, to_remove


def in_overview(output: Optional[str], overview_output: Optional[str]) -> bool:
    """Whether the panel window on ``output`` belongs to the open overview.

    The overview is opened from one monitor (the focused one). Only that
    monitor's panel is shown for it. When either side is unknown there is
    nothing to compare, so the panel is shown rather than left unreachable.

    Args:
        output: Connector of the monitor the panel window is on.
        overview_output: Connector of the monitor the overview was opened from.
    """
    if not output or not overview_output:
        return True
    return output == overview_output
