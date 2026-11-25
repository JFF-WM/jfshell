from datetime import date, datetime, timedelta, timezone

from src.plugins.experimental.calendar._calendar_info import (
    MAX_WORLD_CLOCKS,
    clean_zone_list,
    day_facts,
    format_day_summary,
    moon_phase,
    sun_times,
    world_clocks,
    zone_label,
)

UTC = timezone.utc
BRT = timezone(timedelta(hours=-3))
SAO_PAULO = (-23.5505, -46.6333)


# --- day facts ---------------------------------------------------------------


def test_day_facts_for_a_regular_day():
    facts = day_facts(date(2026, 11, 24))
    assert facts.week == 48
    assert facts.day_of_year == 328
    assert facts.days_left == 37


def test_day_facts_at_the_edges_of_the_year():
    assert day_facts(date(2026, 1, 1)).day_of_year == 1
    assert day_facts(date(2026, 12, 31)).days_left == 0
    assert day_facts(date(2024, 12, 31)).day_of_year == 366


def test_summary_uses_singular_for_one_day():
    assert (
        format_day_summary(day_facts(date(2026, 11, 24)))
        == "Week 48 · Day 328 · 37 days left"
    )
    assert format_day_summary(day_facts(date(2026, 12, 30))).endswith("1 day left")


# --- sun ---------------------------------------------------------------------


def test_sun_times_in_sao_paulo_in_late_november():
    sun = sun_times(date(2026, 11, 24), *SAO_PAULO)
    assert sun.state == "normal"
    rise = sun.sunrise.astimezone(BRT)
    sset = sun.sunset.astimezone(BRT)
    assert 5 <= rise.hour + rise.minute / 60 <= 5.7
    assert 18.2 <= sset.hour + sset.minute / 60 <= 18.9
    length = (sun.sunset - sun.sunrise).total_seconds() / 3600
    assert 12.9 <= length <= 13.5


def test_sun_times_day_is_longer_in_summer_than_winter_in_the_south():
    summer = sun_times(date(2026, 12, 21), *SAO_PAULO)
    winter = sun_times(date(2026, 6, 21), *SAO_PAULO)
    assert (summer.sunset - summer.sunrise) > (winter.sunset - winter.sunrise)


def test_sun_times_on_the_equator_at_the_equinox_is_about_twelve_hours():
    sun = sun_times(date(2026, 3, 20), 0.0, 0.0)
    length = (sun.sunset - sun.sunrise).total_seconds() / 3600
    assert 12.0 <= length <= 12.3


def test_sun_times_polar_day_and_night():
    day = sun_times(date(2026, 6, 21), 80.0, 0.0)
    night = sun_times(date(2026, 12, 21), 80.0, 0.0)
    assert (day.state, day.sunrise, day.sunset) == ("polar_day", None, None)
    assert (night.state, night.sunrise, night.sunset) == ("polar_night", None, None)


# --- moon --------------------------------------------------------------------


def test_moon_is_new_at_the_reference_moment():
    phase = moon_phase(datetime(2000, 1, 6, 18, 14, tzinfo=UTC))
    assert phase.name == "New moon"
    assert phase.illumination < 0.01


def test_moon_is_full_on_a_known_full_moon():
    phase = moon_phase(datetime(2000, 1, 21, 4, 40, tzinfo=UTC))
    assert phase.name == "Full moon"
    assert phase.illumination > 0.95


def test_moon_waxes_then_wanes():
    start = datetime(2000, 1, 6, 18, 14, tzinfo=UTC)
    names = [moon_phase(start + timedelta(days=d)).name for d in (3.7, 7.4, 11, 18, 22, 26)]
    assert names == [
        "Waxing crescent",
        "First quarter",
        "Waxing gibbous",
        "Waning gibbous",
        "Last quarter",
        "Waning crescent",
    ]


def test_moon_age_stays_inside_one_month():
    for days in range(0, 4000, 97):
        phase = moon_phase(datetime(2000, 1, 1, tzinfo=UTC) + timedelta(days=days))
        assert 0 <= phase.age_days < 29.6
        assert 0 <= phase.illumination <= 1


# --- world clocks ------------------------------------------------------------


def test_world_clocks_show_each_zone_time():
    now = datetime(2026, 11, 24, 12, 0, tzinfo=BRT)
    clocks = world_clocks(["America/New_York", "Asia/Tokyo"], now)
    assert [(c.label, c.time) for c in clocks] == [
        ("New York", "10:00"),
        ("Tokyo", "00:00"),
    ]
    assert clocks[0].note == ""
    assert clocks[1].note == "tomorrow"


def test_world_clocks_note_yesterday():
    now = datetime(2026, 11, 24, 0, 30, tzinfo=BRT)
    clocks = world_clocks(["America/New_York"], now)
    assert clocks[0].note == "yesterday"


def test_world_clocks_skip_unknown_zones():
    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    clocks = world_clocks(["Not/AZone", "/etc/passwd", "UTC"], now)
    assert [c.label for c in clocks] == ["UTC"]


def test_clean_zone_list_accepts_lists_and_single_names():
    assert clean_zone_list(["UTC", " Asia/Tokyo ", 5, "", None]) == ["UTC", "Asia/Tokyo"]
    assert clean_zone_list("UTC") == ["UTC"]
    assert clean_zone_list(None) == []
    assert clean_zone_list({"a": 1}) == []


def test_clean_zone_list_is_capped():
    assert len(clean_zone_list([f"Etc/GMT+{i}" for i in range(1, 10)])) == MAX_WORLD_CLOCKS


def test_zone_label_uses_the_last_part():
    assert zone_label("America/Sao_Paulo") == "Sao Paulo"
    assert zone_label("UTC") == "UTC"
