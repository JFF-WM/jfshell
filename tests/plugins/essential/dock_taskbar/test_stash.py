"""Tests: launchers hidden in the revealer of the dock taskbar.

The model part (_model.py) is plain Python. The UI part runs DockTaskbarUI.apply
against small fake widgets, so no GTK or display is needed. Both modules are
loaded by path because the plugin folder has no __init__.py.
"""

import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

_DIR = (
    Path(__file__).resolve().parents[4] / "src" / "plugins" / "essential" / "dock_taskbar"
)
_PKG = "dock_taskbar_stash_under_test"
_pkg = types.ModuleType(_PKG)
_pkg.__path__ = [str(_DIR)]
sys.modules[_PKG] = _pkg
model = importlib.import_module(f"{_PKG}._model")
ui_module = importlib.import_module(f"{_PKG}._ui")


def launcher(name, **extra):
    return {"id": name, "cmd": name, **extra}


def window(wid, app_id):
    return {"id": wid, "app_id": app_id}


class EntryTest(unittest.TestCase):
    def entries(self, pinned, views):
        entries, _ = model.build_entries(pinned, views, [])
        return {e.app_id: e for e in entries if e.kind == model.APP}

    def test_hidden_launcher_without_windows_is_stashed(self):
        entry = self.entries([launcher("foot", hidden=True)], [])["foot"]
        self.assertTrue(entry.hidden)
        self.assertTrue(entry.stashed)

    def test_hidden_launcher_with_windows_stays_on_the_bar(self):
        entry = self.entries([launcher("foot", hidden=True)], [window(1, "foot")])[
            "foot"
        ]
        self.assertTrue(entry.hidden)
        self.assertFalse(entry.stashed)

    def test_plain_launcher_is_never_stashed(self):
        entry = self.entries([launcher("foot")], [])["foot"]
        self.assertFalse(entry.hidden)
        self.assertFalse(entry.stashed)

    def test_unpinned_app_is_never_hidden(self):
        entry = self.entries([], [window(1, "foot")])["foot"]
        self.assertFalse(entry.hidden)
        self.assertFalse(entry.stashed)

    def test_hidden_false_counts_as_shown(self):
        entry = self.entries([launcher("foot", hidden=False)], [])["foot"]
        self.assertFalse(entry.stashed)


class WithHiddenTest(unittest.TestCase):
    def test_hides_only_the_given_launcher(self):
        items = [launcher("a"), launcher("b")]
        result = model.with_hidden(items, 1, True)
        self.assertEqual(result, [launcher("a"), launcher("b", hidden=True)])
        self.assertNotIn("hidden", items[1])  # the input is not changed

    def test_showing_again_removes_the_key(self):
        items = [launcher("a", hidden=True)]
        self.assertEqual(model.with_hidden(items, 0, False), [launcher("a")])

    def test_showing_a_shown_launcher_changes_nothing(self):
        items = [launcher("a")]
        self.assertEqual(model.with_hidden(items, 0, False), items)

    def test_separator_and_bad_index_leave_the_list_alone(self):
        items = [{"type": model.SEPARATOR}, "junk", launcher("a")]
        self.assertEqual(model.with_hidden(items, 0, True), items)
        self.assertEqual(model.with_hidden(items, 1, True), items)
        self.assertEqual(model.with_hidden(items, 9, True), items)
        self.assertEqual(model.with_hidden(items, -1, True), items)

    def test_hidden_flag_survives_a_drag(self):
        items = [launcher("a", hidden=True), launcher("b")]
        self.assertEqual(
            model.moved_to(items, 0, 2), [launcher("b"), launcher("a", hidden=True)]
        )


class Widget:
    """Just enough of a Gtk widget for DockTaskbarUI.apply."""

    def __init__(self, name):
        self.name = name
        self.parent = None
        self.kids = []
        self.visible = True
        self.revealed = False
        self.icon = None
        self.tooltip = None

    def __repr__(self):
        return self.name

    def append(self, child):
        child.parent = self
        self.kids.append(child)

    def prepend(self, child):
        child.parent = self
        self.kids.insert(0, child)

    def remove(self, child):
        self.kids.remove(child)
        child.parent = None

    def get_parent(self):
        return self.parent

    def get_first_child(self):
        return self.kids[0] if self.kids else None

    def _sibling(self, step):
        if self.parent is None:
            return None
        index = self.parent.kids.index(self) + step
        return self.parent.kids[index] if 0 <= index < len(self.parent.kids) else None

    def get_next_sibling(self):
        return self._sibling(1)

    def get_prev_sibling(self):
        return self._sibling(-1)

    def reorder_child_after(self, child, sibling):
        self.kids.remove(child)
        self.kids.insert(0 if sibling is None else self.kids.index(sibling) + 1, child)

    def set_visible(self, value):
        self.visible = value

    def set_tooltip_text(self, text):
        self.tooltip = text

    def set_icon_name(self, name):
        self.icon = name

    def get_reveal_child(self):
        return self.revealed

    def set_reveal_child(self, value):
        self.revealed = value


def make_bar():
    bar = SimpleNamespace(
        dock=Widget("dock"),
        widgets={},
        launcher_button=Widget("launcher"),
        stash_group=Widget("group"),
        stash_toggle=Widget("toggle"),
        stash_revealer=Widget("revealer"),
        stash_box=Widget("box"),
    )
    bar.stash_group.visible = False
    bar.stash_group.append(bar.stash_toggle)
    bar.stash_group.append(bar.stash_revealer)
    bar.stash_revealer.append(bar.stash_box)
    bar.dock.append(bar.stash_group)
    bar.dock.prepend(bar.launcher_button)  # the launcher comes first
    return bar


def make_ui():
    ui = ui_module.DockTaskbarUI.__new__(ui_module.DockTaskbarUI)
    ui.plugin = SimpleNamespace(
        preview=SimpleNamespace(forget=lambda w: None),
        menu=SimpleNamespace(forget=lambda w: None),
    )
    ui._drag = None
    ui.create_button = lambda entry: Widget(entry.key)
    ui.create_separator = lambda: Widget("separator")
    ui.update_button = lambda widget, entry: None
    return ui


def names(widget):
    return [w.name for w in widget.kids]


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.ui = make_ui()
        self.bar = make_bar()

    def apply(self, pinned, views):
        entries, _ = model.build_entries(pinned, views, [])
        self.ui.apply(entries, self.bar)

    def test_hidden_launcher_goes_to_the_revealer(self):
        self.apply([launcher("a"), launcher("b", hidden=True)], [])
        self.assertEqual(names(self.bar.dock), ["launcher", "pin:0:a", "group"])
        self.assertEqual(names(self.bar.stash_box), ["pin:1:b"])
        self.assertTrue(self.bar.stash_group.visible)
        self.assertEqual(self.bar.stash_toggle.tooltip, "Hidden apps (1)")

    def test_toggle_is_hidden_without_hidden_launchers(self):
        self.apply([launcher("a")], [])
        self.assertFalse(self.bar.stash_group.visible)
        self.assertEqual(names(self.bar.stash_box), [])

    def test_running_hidden_app_leaves_the_revealer_until_it_closes(self):
        pinned = [launcher("a"), launcher("b", hidden=True), launcher("c")]
        self.apply(pinned, [])
        self.assertEqual(names(self.bar.stash_box), ["pin:1:b"])

        self.apply(pinned, [window(1, "b")])
        self.assertEqual(
            names(self.bar.dock), ["launcher", "pin:0:a", "pin:1:b", "pin:2:c", "group"]
        )
        self.assertEqual(names(self.bar.stash_box), [])
        self.assertFalse(self.bar.stash_group.visible)

        self.apply(pinned, [])
        self.assertEqual(names(self.bar.dock), ["launcher", "pin:0:a", "pin:2:c", "group"])
        self.assertEqual(names(self.bar.stash_box), ["pin:1:b"])
        self.assertTrue(self.bar.stash_group.visible)

    def test_revealer_stays_last_when_apps_come_and_go(self):
        pinned = [launcher("a", hidden=True)]
        self.apply(pinned, [])
        self.apply(pinned, [window(1, "x"), window(2, "y")])
        self.assertEqual(names(self.bar.dock)[-1], "group")
        self.assertEqual(names(self.bar.dock)[0], "launcher")

    def test_revealer_closes_when_it_runs_empty(self):
        pinned = [launcher("a", hidden=True)]
        self.apply(pinned, [])
        self.bar.stash_revealer.revealed = True
        self.apply(pinned, [window(1, "a")])
        self.assertFalse(self.bar.stash_revealer.revealed)
        self.assertEqual(self.bar.stash_toggle.icon, "pan-end-symbolic")

    def test_revealer_keeps_the_launcher_order(self):
        pinned = [launcher("a", hidden=True), launcher("b"), launcher("c", hidden=True)]
        self.apply(pinned, [])
        self.assertEqual(names(self.bar.stash_box), ["pin:0:a", "pin:2:c"])
        self.apply(pinned, [window(1, "a")])
        self.apply(pinned, [])
        self.assertEqual(names(self.bar.stash_box), ["pin:0:a", "pin:2:c"])

    def test_removed_launcher_leaves_the_revealer(self):
        self.apply([launcher("a", hidden=True)], [])
        self.apply([], [])
        self.assertEqual(names(self.bar.stash_box), [])
        self.assertEqual(self.bar.widgets, {})


if __name__ == "__main__":
    unittest.main()
