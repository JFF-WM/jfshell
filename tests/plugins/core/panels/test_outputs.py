from src.plugins.core.panels._outputs import in_overview, plan_mirrors


def test_every_monitor_but_the_primary_gets_a_copy():
    assert plan_mirrors(["DP-1", "DP-2", "HDMI-A-1"], "DP-1", []) == (
        ["DP-2", "HDMI-A-1"],
        [],
    )


def test_single_monitor_needs_no_copy():
    assert plan_mirrors(["DP-1"], "DP-1", []) == ([], [])


def test_existing_copies_are_kept():
    assert plan_mirrors(["DP-1", "DP-2"], "DP-1", ["DP-2"]) == ([], [])


def test_unplugged_monitor_loses_its_copy():
    assert plan_mirrors(["DP-1"], "DP-1", ["DP-2"]) == ([], ["DP-2"])


def test_copy_on_the_new_primary_is_dropped_and_the_old_primary_gets_one():
    assert plan_mirrors(["DP-1", "DP-2"], "DP-2", ["DP-2"]) == (["DP-1"], ["DP-2"])


def test_unknown_primary_gives_every_monitor_a_copy():
    assert plan_mirrors(["DP-1", "DP-2"], None, []) == (["DP-1", "DP-2"], [])


def test_duplicates_and_empty_names_are_ignored():
    assert plan_mirrors(["DP-2", "DP-2", "", None], "DP-1", ["DP-3", "DP-3"]) == (
        ["DP-2"],
        ["DP-3"],
    )


def test_overview_shows_only_the_dock_of_its_monitor():
    assert in_overview("DP-1", "DP-1") is True
    assert in_overview("DP-2", "DP-1") is False


def test_overview_with_an_unknown_side_shows_the_dock():
    assert in_overview(None, "DP-1") is True
    assert in_overview("DP-1", None) is True
    assert in_overview(None, None) is True
