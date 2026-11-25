from src.plugins.experimental.dbus.notification._icon_state import (
    icon_candidates,
    resolve_seen,
    tooltip_text,
)


def test_unread_uses_new_icon_first():
    assert icon_candidates(3, False)[0] == "notifications-new-symbolic"


def test_idle_uses_plain_icon_first():
    assert icon_candidates(0, False)[0] == "notifications-symbolic"


def test_dnd_without_unread_uses_disabled_icon():
    assert icon_candidates(0, True)[0] == "notifications-disabled-symbolic"


def test_unread_wins_over_dnd():
    assert icon_candidates(1, True)[0] == "notifications-new-symbolic"


def test_tooltip_plural_and_singular():
    assert tooltip_text(1, False) == "1 new notification"
    assert tooltip_text(2, False) == "2 new notifications"


def test_tooltip_states():
    assert tooltip_text(0, False) == "No new notifications"
    assert tooltip_text(0, True) == "Do Not Disturb"
    assert tooltip_text(2, True) == "2 new notifications (Do Not Disturb)"


def test_resolve_seen_keeps_marker_when_db_grows():
    assert resolve_seen(5, 9) == 5


def test_resolve_seen_clamps_after_database_reset():
    assert resolve_seen(40, 2) == 2
    assert resolve_seen(40, 0) == 0
