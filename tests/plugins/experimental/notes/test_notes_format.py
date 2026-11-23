from datetime import datetime

from src.plugins.experimental.notes._notes_format import (
    first_line,
    format_age,
    format_countdown,
    format_created,
    format_interval,
    join_content,
    matches_query,
    new_stamp,
    reminder_summary,
    split_content,
)


def test_split_content_with_stamp():
    assert split_content("2026-11-23 14:02:09 Buy milk") == (
        "2026-11-23 14:02:09",
        "Buy milk",
    )


def test_split_content_without_stamp():
    assert split_content("  just a title ") == (None, "just a title")
    assert split_content("") == (None, "")


def test_split_content_keeps_inner_spacing():
    _, title = split_content("2026-11-23 14:02:09 a  b")
    assert title == "a  b"


def test_join_content_round_trip():
    stamp, title = split_content("2026-11-23 14:02:09 Buy milk")
    assert join_content(stamp, title) == "2026-11-23 14:02:09 Buy milk"
    assert join_content(None, "x") == "x"


def test_new_stamp_is_splittable():
    now = datetime(2026, 11, 23, 8, 5, 3).timestamp()
    assert new_stamp(now) == "2026-11-23 08:05:03"
    assert split_content(f"{new_stamp(now)} t") == ("2026-11-23 08:05:03", "t")


def test_format_created():
    assert format_created("2026-11-23 14:02:09") == "Nov 23, 14:02"
    assert format_created("garbage") == "garbage"
    assert format_created(None) == ""


def test_format_age():
    now = 1_000_000
    assert format_age(None, now) == ""
    assert format_age(now - 5, now) == "Just now"
    assert format_age(now - 180, now) == "3 min ago"
    assert format_age(now - 7200, now) == "2 h ago"
    assert format_age(now - 86400, now) == "1 day ago"
    assert format_age(now - 3 * 86400, now) == "3 days ago"


def test_format_interval():
    assert format_interval(0) == "1 min"
    assert format_interval(300) == "5 min"
    assert format_interval(3600) == "1 h"
    assert format_interval(5400) == "1 h 30 min"
    assert format_interval(2 * 86400) == "2 days"


def test_format_countdown():
    now = 1_000_000
    assert format_countdown(now - 1, now) == "now"
    assert format_countdown(now + 30, now) == "in less than a minute"
    assert format_countdown(now + 240, now) == "in 4 min"
    assert format_countdown(now + 7300, now) == "in 2 h"
    assert format_countdown(now + 3 * 86400, now) == "in 3 days"


def test_reminder_summary():
    now = 1_000_000
    assert reminder_summary(0, 0, 0, now) == ""
    assert reminder_summary(now + 60, 300, 0, now) == ""
    assert reminder_summary(now + 240, 300, 3, now) == (
        "Next in 4 min \u2022 every 5 min \u2022 3 times left"
    )
    assert reminder_summary(now + 240, 300, 1, now).endswith("1 time left")


def test_first_line():
    assert first_line("\n  \nhello\nworld", 50) == "hello"
    assert first_line("abcdef", 3) == "abc"
    assert first_line("", 5) == ""


def test_matches_query():
    assert matches_query("", "t", "d")
    assert matches_query("MILK", "Buy milk", "")
    assert matches_query("buy shop", "Buy milk", "at the shop")
    assert not matches_query("buy bread", "Buy milk", "at the shop")
