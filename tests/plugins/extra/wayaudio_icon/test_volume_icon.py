from src.plugins.extra.wayaudio_icon._volume_icon import (
    icon_candidates,
    tooltip_text,
    volume_percent,
)


def test_volume_percent_rounds_and_clamps():
    assert volume_percent(0.5) == 50
    assert volume_percent(1.5) == 150
    assert volume_percent(-0.2) == 0


def test_muted_flag_wins_over_volume():
    assert icon_candidates(100, True)[0] == "audio-volume-muted-symbolic"


def test_zero_volume_and_missing_output_are_muted():
    assert icon_candidates(0, False)[0] == "audio-volume-muted-symbolic"
    assert icon_candidates(None, False)[0] == "audio-volume-muted-symbolic"


def test_low_below_twenty():
    assert icon_candidates(1, False)[0] == "audio-volume-low-symbolic"
    assert icon_candidates(19, False)[0] == "audio-volume-low-symbolic"


def test_medium_in_the_middle():
    assert icon_candidates(20, False)[0] == "audio-volume-medium-symbolic"
    assert icon_candidates(50, False)[0] == "audio-volume-medium-symbolic"
    assert icon_candidates(79, False)[0] == "audio-volume-medium-symbolic"


def test_high_from_eighty_including_overamplified():
    assert icon_candidates(80, False)[0] == "audio-volume-high-symbolic"
    assert icon_candidates(100, False)[0] == "audio-volume-high-symbolic"
    assert icon_candidates(150, False)[0] == "audio-volume-high-symbolic"


def test_every_candidate_list_ends_with_a_common_icon():
    for percent, muted in ((None, False), (10, False), (50, False), (100, False)):
        assert icon_candidates(percent, muted)[-1].startswith("audio-volume-")


def test_tooltip_states():
    assert tooltip_text(42, False) == "Volume 42%"
    assert tooltip_text(42, True) == "Muted (42%)"
    assert tooltip_text(None, False) == "No audio output"
