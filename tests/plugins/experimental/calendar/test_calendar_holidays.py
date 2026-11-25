from datetime import date

from src.plugins.experimental.calendar._calendar_holidays import (
    ZoneSpot,
    clean_country,
    format_days_until,
    holiday_on,
    nearest_country,
    next_holiday,
    parse_iso6709,
    parse_zone_tab,
)

ZONE_TAB = "\n".join(
    [
        "# tz database zone.tab",
        "#country-code\tcoordinates\tTZ\tcomments",
        "US\t+404251-0740023\tAmerica/New_York\tEastern (most areas)",
        "BR\t-2332-04637\tAmerica/Sao_Paulo\tBrazil (SE)",
        "BR\t-0343-03830\tAmerica/Fortaleza\tBrazil (NE)",
        "PT\t+3843-00908\tEurope/Lisbon\t",
        "DE\t+5230+01322\tEurope/Berlin\tmost of Germany",
        "JP\t+353916+1394441\tAsia/Tokyo\t",
        "AR\t-3436-05827\tAmerica/Argentina/Buenos_Aires\tBuenos Aires",
        "",
        "broken line",
        "XX\tnot-a-point\tNowhere/Land",
        "\tdigits",
    ]
)


# --- zone.tab ----------------------------------------------------------------


def test_parse_iso6709_with_and_without_seconds():
    lat, lon = parse_iso6709("-2332-04637")
    assert round(lat, 3) == -23.533 and round(lon, 3) == -46.617
    lat, lon = parse_iso6709("+404251-0740023")
    assert round(lat, 3) == 40.714 and round(lon, 3) == -74.006


def test_parse_iso6709_rejects_garbage():
    assert parse_iso6709("") is None
    assert parse_iso6709("23.5,-46.6") is None
    assert parse_iso6709("+23-046") is None


def test_parse_zone_tab_skips_comments_and_bad_rows():
    spots = parse_zone_tab(ZONE_TAB)
    assert [s.country for s in spots] == ["US", "BR", "BR", "PT", "DE", "JP", "AR"]


def test_nearest_country_for_the_weather_default_and_other_places():
    spots = parse_zone_tab(ZONE_TAB)
    assert nearest_country(spots, -23.5505, -46.6333) == "BR"
    assert nearest_country(spots, -3.7, -38.5) == "BR"
    assert nearest_country(spots, 38.7, -9.1) == "PT"
    assert nearest_country(spots, 35.7, 139.7) == "JP"
    assert nearest_country(spots, 40.7, -74.0) == "US"


def test_nearest_country_handles_the_date_line():
    spots = [ZoneSpot("NZ", -36.9, 174.8), ZoneSpot("US", 21.3, -157.9)]
    assert nearest_country(spots, -17.0, -179.0) == "NZ"


def test_nearest_country_without_zones_is_none():
    assert nearest_country([], 0.0, 0.0) is None


def test_clean_country():
    assert clean_country(" br ") == "BR"
    assert clean_country("BRA") == ""
    assert clean_country("1a") == ""
    assert clean_country(None) == ""
    assert clean_country(55) == ""


# --- holidays ----------------------------------------------------------------

HOLIDAYS = {
    date(2026, 11, 15): "Proclamação da República",
    date(2026, 11, 20): "Consciência Negra",
    date(2026, 12, 25): "Natal",
}


def test_holiday_on():
    assert holiday_on(HOLIDAYS, date(2026, 12, 25)) == "Natal"
    assert holiday_on(HOLIDAYS, date(2026, 12, 24)) == ""


def test_next_holiday_looks_forward():
    assert next_holiday(HOLIDAYS, date(2026, 11, 24)) == (
        date(2026, 12, 25),
        "Natal",
        31,
    )


def test_next_holiday_counts_today_and_can_run_out():
    assert next_holiday(HOLIDAYS, date(2026, 12, 25)) == (date(2026, 12, 25), "Natal", 0)
    assert next_holiday(HOLIDAYS, date(2026, 12, 26)) is None
    assert next_holiday({}, date(2026, 1, 1)) is None


def test_format_days_until():
    assert format_days_until(0) == "today"
    assert format_days_until(1) == "tomorrow"
    assert format_days_until(31) == "in 31 days"
