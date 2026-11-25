"""Pure helpers that decide how the systray notification icon looks."""

ICONS_UNREAD: tuple[str, ...] = (
    "notifications-new-symbolic",
    "notification-new-symbolic",
    "preferences-system-notifications-symbolic",
)
ICONS_IDLE: tuple[str, ...] = (
    "notifications-symbolic",
    "notification-symbolic",
    "preferences-system-notifications-symbolic",
)
ICONS_DND: tuple[str, ...] = (
    "notifications-disabled-symbolic",
    "notification-disabled-symbolic",
    "notifications-symbolic",
)


def icon_candidates(unread: int, dnd: bool) -> tuple[str, ...]:
    """Return the icon names to try, best match first.

    Unread notifications win over Do Not Disturb so a stored message is never
    hidden behind the muted icon.

    Args:
        unread: Number of notifications not yet seen by the user.
        dnd: True when Do Not Disturb is active.

    Returns:
        Icon names ordered by preference, ending with a generic fallback.
    """
    if unread > 0:
        return ICONS_UNREAD
    if dnd:
        return ICONS_DND
    return ICONS_IDLE


def tooltip_text(unread: int, dnd: bool) -> str:
    """Build the tooltip of the systray notification icon.

    Args:
        unread: Number of notifications not yet seen by the user.
        dnd: True when Do Not Disturb is active.

    Returns:
        Human readable summary of the current notification state.
    """
    if unread > 0:
        noun = "notification" if unread == 1 else "notifications"
        text = f"{unread} new {noun}"
        return f"{text} (Do Not Disturb)" if dnd else text
    return "Do Not Disturb" if dnd else "No new notifications"


def resolve_seen(seen_id: int, latest_id: int) -> int:
    """Clamp the last seen notification id to the newest stored id.

    The id sequence restarts when the database is recreated, which would
    otherwise leave every new notification below the stored marker.

    Args:
        seen_id: Highest notification id the user has already seen.
        latest_id: Highest notification id currently in the database.

    Returns:
        The marker to use from now on.
    """
    return min(seen_id, latest_id)
