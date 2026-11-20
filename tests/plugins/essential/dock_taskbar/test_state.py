"""Tests: always-on-top / sticky flags of an icon group (state_flags in _model.py).

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
_NAME = "dock_taskbar_model_state_under_test"
_spec = importlib.util.spec_from_file_location(_NAME, _MODEL)
model = importlib.util.module_from_spec(_spec)
sys.modules[_NAME] = model  # dataclasses look their module up here
_spec.loader.exec_module(model)


def win(**flags):
    """A minimal window dict with the given flag keys."""
    return {"id": 1, "app_id": "app", **flags}


class StateFlagsTest(unittest.TestCase):
    def test_empty_group(self):
        self.assertEqual(model.state_flags([]), (False, False))

    def test_none_group(self):
        self.assertEqual(model.state_flags(None), (False, False))

    def test_no_window_flagged(self):
        views = [
            win(**{"always-on-top": False, "sticky": False}),
            win(**{"always-on-top": False, "sticky": False}),
        ]
        self.assertEqual(model.state_flags(views), (False, False))

    def test_one_always_on_top_among_several(self):
        views = [
            win(**{"always-on-top": False, "sticky": False}),
            win(**{"always-on-top": True, "sticky": False}),
            win(**{"always-on-top": False, "sticky": False}),
        ]
        self.assertEqual(model.state_flags(views), (True, False))

    def test_one_sticky_among_several(self):
        views = [
            win(**{"always-on-top": False, "sticky": False}),
            win(**{"always-on-top": False, "sticky": False}),
            win(**{"always-on-top": False, "sticky": True}),
        ]
        self.assertEqual(model.state_flags(views), (False, True))

    def test_flags_on_different_windows_combine(self):
        views = [win(**{"always-on-top": True}), win(**{"sticky": True})]
        self.assertEqual(model.state_flags(views), (True, True))

    def test_missing_keys(self):
        self.assertEqual(model.state_flags([win(), {}]), (False, False))
        self.assertEqual(
            model.state_flags([win(), win(sticky=True)]),
            (False, True),
        )

    def test_none_values(self):
        views = [win(**{"always-on-top": None, "sticky": None})]
        self.assertEqual(model.state_flags(views), (False, False))
        views.append(win(**{"always-on-top": True, "sticky": None}))
        self.assertEqual(model.state_flags(views), (True, False))

    def test_ignores_items_that_are_not_dicts(self):
        views = [None, "x", win(sticky=True)]
        self.assertEqual(model.state_flags(views), (False, True))

    def test_result_is_a_tuple_of_bools(self):
        result = model.state_flags([win(**{"always-on-top": 1, "sticky": 0})])
        self.assertEqual(result, (True, False))
        self.assertIsInstance(result, tuple)
        self.assertTrue(all(isinstance(flag, bool) for flag in result))

    def test_works_on_an_entry(self):
        entry = model.Entry(key="app:x", views=[win(sticky=True)])
        self.assertEqual(model.state_flags(entry.views), (False, True))


if __name__ == "__main__":
    unittest.main()
