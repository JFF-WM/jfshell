"""Tests: one thumbnail per jff container (preview_views in _model.py).

_model.py is loaded by file path because the plugin folder has no __init__.py.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

_MODEL = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "plugins"
    / "essential"
    / "dock_taskbar"
    / "_model.py"
)
_NAME = "dock_taskbar_model_container_under_test"
_spec = importlib.util.spec_from_file_location(_NAME, _MODEL)
model = importlib.util.module_from_spec(_spec)
sys.modules[_NAME] = model  # dataclasses look their module up here
_spec.loader.exec_module(model)


def win(wid, container=None, **extra):
    """A minimal window dict, in a container when an id is given."""
    view = {"id": wid, "app_id": "kitty", **extra}
    if container is not None:
        view["container"] = {"id": container, "name": None}
    else:
        view["container"] = None
    return view


def ids(views):
    return [v["id"] for v in views]


class PreviewViewsTest(unittest.TestCase):
    def test_empty_and_none(self):
        self.assertEqual(model.preview_views([]), [])
        self.assertEqual(model.preview_views(None), [])

    def test_windows_without_container_are_all_kept(self):
        views = [win(1), win(2), win(3)]
        self.assertEqual(ids(model.preview_views(views)), [1, 2, 3])

    def test_whole_container_is_one_thumbnail(self):
        views = [win(i, container=1) for i in range(10)]
        self.assertEqual(ids(model.preview_views(views)), [0])

    def test_container_plus_loose_window_gives_two(self):
        views = [win(i, container=1) for i in range(10)] + [win(99)]
        self.assertEqual(ids(model.preview_views(views)), [0, 99])

    def test_two_containers_give_two(self):
        views = [win(1, container=1), win(2, container=2), win(3, container=1)]
        self.assertEqual(ids(model.preview_views(views)), [1, 2])

    def test_focused_window_represents_its_container(self):
        views = [win(1, container=1), win(2, container=1, is_focused=True), win(3)]
        self.assertEqual(ids(model.preview_views(views)), [2, 3])

    def test_container_id_zero_is_still_a_container(self):
        views = [win(1, container=0), win(2, container=0)]
        self.assertEqual(ids(model.preview_views(views)), [1])

    def test_missing_or_malformed_container_counts_as_none(self):
        views = [
            {"id": 1},
            {"id": 2, "container": "x"},
            {"id": 3, "container": {"name": "n"}},
        ]
        self.assertEqual(ids(model.preview_views(views)), [1, 2, 3])

    def test_limit_applies_after_grouping(self):
        views = [win(i, container=1) for i in range(5)] + [win(10), win(11)]
        self.assertEqual(ids(model.preview_views(views, 2)), [0, 10])

    def test_non_dict_items_are_ignored(self):
        self.assertEqual(ids(model.preview_views([None, win(1)])), [1])


if __name__ == "__main__":
    unittest.main()
