from src.core.panel_mode import SINGLE_DEFAULT_HEIGHT, single_panel_metrics


class _Config:
    def __init__(self, values):
        self.values = values

    def get_root_setting(self, path, default=None):
        return self.values.get(tuple(path), default)


def _top(height):
    return _Config({("org.jfshell.panel", "top", "height"): height})


def test_height_comes_from_the_named_panel():
    assert single_panel_metrics(_top(28), 0, "top")["panel_height"] == 28


def test_default_height_is_used_when_the_panel_has_none():
    metrics = single_panel_metrics(_Config({}), 0, "top", default_height=32)
    assert metrics["panel_height"] == 32


def test_single_panel_default_height_is_unchanged():
    assert single_panel_metrics(_Config({}))["panel_height"] == SINGLE_DEFAULT_HEIGHT


def test_unparsable_height_falls_back_to_the_given_default():
    metrics = single_panel_metrics(_top("tall"), 0, "top", default_height=32)
    assert metrics["panel_height"] == 32


def test_a_shorter_panel_gives_a_shorter_button():
    short = single_panel_metrics(_top(26), 0, "top")
    tall = single_panel_metrics(_top(40), 0, "top")
    assert short["button_height"] < tall["button_height"]
