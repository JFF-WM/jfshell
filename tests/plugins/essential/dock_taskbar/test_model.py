"""Tests for the dock taskbar grouping model (pure logic, no GTK, no compositor)."""

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
_spec = importlib.util.spec_from_file_location("dock_taskbar_model", _MODEL)
model = importlib.util.module_from_spec(_spec)
sys.modules["dock_taskbar_model"] = model
_spec.loader.exec_module(model)


def view(vid, app_id, focused=False):
    return {"id": vid, "app_id": app_id, "app-id": app_id, "is_focused": focused}


def pin(pid, cmd="", **extra):
    return {"id": pid, "cmd": cmd, "icon": "x", **extra}


class KeyTests(unittest.TestCase):
    def test_norm_and_variants(self):
        self.assertEqual(model.norm("Foo-Bar_1"), "foobar1")
        self.assertEqual(
            model.key_variants("org.gnome.Nautilus"), {"orggnomenautilus", "nautilus"}
        )
        self.assertEqual(model.key_variants("firefox.desktop"), {"firefox"})
        self.assertEqual(model.key_variants(None), set())
        self.assertEqual(model.key_variants("  "), set())

    def test_command_name(self):
        self.assertEqual(model.command_name("firefox --new-window"), "firefox")
        self.assertEqual(model.command_name("/usr/bin/alacritty -e htop"), "alacritty")
        self.assertEqual(model.command_name("env FOO=1 BAR=2 kitty"), "kitty")
        self.assertEqual(
            model.command_name("flatpak run --branch=stable org.mozilla.firefox"),
            "org.mozilla.firefox",
        )
        self.assertEqual(model.command_name("'unterminated"), "'unterminated")
        self.assertEqual(model.command_name(""), "")
        self.assertEqual(model.command_name(None), "")

    def test_strip_field_codes(self):
        self.assertEqual(model.strip_field_codes("firefox %u"), "firefox")
        self.assertEqual(model.strip_field_codes("gedit %U --x"), "gedit --x")
        self.assertEqual(model.strip_field_codes(""), "")

    def test_pinned_keys_explicit_app_id_wins(self):
        item = pin("Terminal", "gnome-terminal", app_id="org.gnome.Terminal")
        self.assertEqual(model.pinned_keys(item), {"orggnometerminal", "terminal"})
        listed = pin("x", "y", app_id=["a.b", "c"])
        self.assertEqual(model.pinned_keys(listed), {"ab", "b", "c"})


class BuildTests(unittest.TestCase):
    def test_windows_are_always_grouped(self):
        views = [view(1, "kitty"), view(2, "kitty"), view(3, "firefox")]
        entries, order = model.build_entries([], views, [])
        self.assertEqual([e.key for e in entries], ["app:kitty", "app:firefox"])
        self.assertEqual([len(e.views) for e in entries], [2, 1])
        self.assertEqual(order, ["app:kitty", "app:firefox"])

    def test_pinned_launcher_collects_its_windows(self):
        pinned = [pin("firefox", "firefox"), pin("kitty", "kitty")]
        views = [view(1, "kitty"), view(2, "firefox"), view(3, "kitty")]
        entries, _ = model.build_entries(pinned, views, [])
        self.assertEqual([e.item["id"] for e in entries], ["firefox", "kitty"])
        self.assertEqual([[v["id"] for v in e.views] for e in entries], [[2], [1, 3]])
        self.assertTrue(all(e.pinned for e in entries))

    def test_pinned_launcher_without_windows_is_kept(self):
        entries, _ = model.build_entries([pin("firefox", "firefox")], [], [])
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0].running)

    def test_reverse_dns_app_id_matches_short_launcher(self):
        entries, _ = model.build_entries(
            [pin("nautilus", "nautilus")], [view(1, "org.gnome.Nautilus")], []
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].app_id, "org.gnome.Nautilus")

    def test_extra_keys_from_desktop_entries(self):
        pinned = [pin("Console", "gnome-terminal")]
        views = [view(1, "org.gnome.Terminal")]
        # No shared key: launcher, automatic separator and a group of its own.
        self.assertEqual(len(model.build_entries(pinned, views, [])[0]), 3)
        entries, _ = model.build_entries(
            pinned, views, [], extra_keys={"org.gnome.Terminal": {"gnometerminal"}}
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0].views), 1)

    def test_separator_between_pinned_and_running(self):
        entries, _ = model.build_entries(
            [pin("firefox", "firefox")], [view(1, "kitty")], []
        )
        self.assertEqual(
            [e.kind for e in entries], [model.APP, model.SEPARATOR, model.APP]
        )

    def test_no_double_separator(self):
        pinned = [pin("firefox", "firefox"), {"type": "separator"}]
        entries, _ = model.build_entries(pinned, [view(1, "kitty")], [])
        self.assertEqual(
            [e.kind for e in entries], [model.APP, model.SEPARATOR, model.APP]
        )
        self.assertNotIn("sep:auto", [e.key for e in entries])

    def test_no_separator_without_pinned_or_without_running(self):
        only_running, _ = model.build_entries([], [view(1, "kitty")], [])
        only_pinned, _ = model.build_entries([pin("a", "a")], [], [])
        self.assertTrue(all(e.kind == model.APP for e in only_running))
        self.assertTrue(all(e.kind == model.APP for e in only_pinned))

    def test_unpinned_groups_keep_their_order(self):
        first, order = model.build_entries([], [view(1, "a"), view(2, "b")], [])
        self.assertEqual([e.key for e in first], ["app:a", "app:b"])
        again, order = model.build_entries(
            [], [view(3, "b"), view(1, "a"), view(4, "c")], order
        )
        self.assertEqual([e.key for e in again], ["app:a", "app:b", "app:c"])
        gone, order = model.build_entries([], [view(4, "c"), view(3, "b")], order)
        self.assertEqual([e.key for e in gone], ["app:b", "app:c"])
        self.assertEqual(order, ["app:b", "app:c"])

    def test_hide_unpinned(self):
        entries, order = model.build_entries(
            [pin("a", "a")], [view(1, "a"), view(2, "other")], [], show_unpinned=False
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(order, [])

    def test_focused_property(self):
        entries, _ = model.build_entries(
            [], [view(1, "a"), view(2, "a", focused=True)], []
        )
        self.assertTrue(entries[0].focused)

    def test_bad_pinned_items_are_ignored(self):
        entries, _ = model.build_entries(["junk", None, pin("a", "a")], [], [])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].index, 2)

    def test_windows_do_not_mutate_input(self):
        pinned = [pin("a", "a")]
        model.build_entries(pinned, [view(1, "a")], [])
        self.assertEqual(pinned, [pin("a", "a")])


class EditTests(unittest.TestCase):
    def test_moved(self):
        self.assertEqual(model.moved([1, 2, 3], 0, 1), [2, 1, 3])
        self.assertEqual(model.moved([1, 2, 3], 2, -1), [1, 3, 2])
        self.assertEqual(model.moved([1, 2, 3], 0, -1), [1, 2, 3])
        self.assertEqual(model.moved([1, 2, 3], 2, 1), [1, 2, 3])
        items = [1, 2]
        self.assertIsNot(model.moved(items, 0, 0), items)

    def test_without(self):
        self.assertEqual(model.without([1, 2, 3], 1), [1, 3])
        self.assertEqual(model.without([1, 2, 3], 9), [1, 2, 3])

    def test_with_pinned_skips_duplicates(self):
        items = [pin("firefox", "firefox")]
        self.assertEqual(model.with_pinned(items, pin("Firefox", "firefox")), items)
        added = model.with_pinned(items, pin("kitty", "kitty"))
        self.assertEqual([i["id"] for i in added], ["firefox", "kitty"])
        with_sep = [{"type": "separator"}]
        self.assertEqual(len(model.with_pinned(with_sep, pin("a", "a"))), 2)

    def test_with_pinned_at_index(self):
        items = [pin("a", "a"), pin("b", "b")]
        added = model.with_pinned(items, pin("c", "c"), at=1)
        self.assertEqual([i["id"] for i in added], ["a", "c", "b"])
        far = model.with_pinned(items, pin("c", "c"), at=99)
        self.assertEqual([i["id"] for i in far], ["a", "b", "c"])
        self.assertEqual(model.with_pinned(items, pin("A", "a"), at=0), items)

    def test_moved_to(self):
        self.assertEqual(model.moved_to([1, 2, 3], 0, 3), [2, 3, 1])
        self.assertEqual(model.moved_to([1, 2, 3], 2, 0), [3, 1, 2])
        self.assertEqual(model.moved_to([1, 2, 3], 0, 2), [2, 1, 3])
        # The gaps on both sides of the item leave the list alone.
        self.assertEqual(model.moved_to([1, 2, 3], 1, 1), [1, 2, 3])
        self.assertEqual(model.moved_to([1, 2, 3], 1, 2), [1, 2, 3])
        self.assertEqual(model.moved_to([1, 2, 3], 0, -5), [1, 2, 3])
        self.assertEqual(model.moved_to([1, 2, 3], 0, 99), [2, 3, 1])
        self.assertEqual(model.moved_to([1, 2, 3], 7, 0), [1, 2, 3])
        items = [1, 2]
        self.assertIsNot(model.moved_to(items, 0, 0), items)


class DropTests(unittest.TestCase):
    """Dragging an icon next to another one (apply_drop / drop_slot)."""

    def setUp(self):
        self.pinned = [pin("a", "a"), pin("b", "b"), pin("c", "c")]
        views = [view(1, "a"), view(2, "x"), view(3, "y"), view(4, "z")]
        self.entries, self.order = model.build_entries(self.pinned, views, [])
        self.keys = {e.app_id or e.key: e.key for e in self.entries}

    def drop(self, source, target, after, make_item=None):
        return model.apply_drop(
            self.pinned,
            self.order,
            self.entries,
            self.keys[source],
            self.keys[target],
            after,
            make_item,
        )

    @staticmethod
    def ids(items):
        return [i["id"] for i in items]

    def test_slots(self):
        slot = model.drop_slot
        e, o = self.entries, self.order
        self.assertEqual(slot(e, o, 3, self.keys["b"], False), (model.PINNED, 1))
        self.assertEqual(slot(e, o, 3, self.keys["b"], True), (model.PINNED, 2))
        self.assertEqual(slot(e, o, 3, "sep:auto", False), (model.PINNED, 3))
        self.assertEqual(slot(e, o, 3, "sep:auto", True), (model.UNPINNED, 0))
        self.assertEqual(slot(e, o, 3, self.keys["y"], True), (model.UNPINNED, 2))
        self.assertIsNone(slot(e, o, 3, "nope", True))

    def test_launcher_moves_among_launchers(self):
        pinned, order = self.drop("a", "c", True)
        self.assertEqual(self.ids(pinned), ["b", "c", "a"])
        self.assertEqual(order, self.order)
        pinned, _ = self.drop("c", "a", False)
        self.assertEqual(self.ids(pinned), ["c", "a", "b"])

    def test_launcher_dropped_on_itself_stays(self):
        pinned, order = self.drop("b", "b", True)
        self.assertEqual(pinned, self.pinned)
        self.assertEqual(order, self.order)

    def test_launcher_dropped_among_other_apps_stays_pinned_and_goes_last(self):
        pinned, order = self.drop("a", "y", False)
        self.assertEqual(self.ids(pinned), ["b", "c", "a"])
        self.assertEqual(order, self.order)

    def test_other_apps_reorder(self):
        _, order = self.drop("z", "x", False)
        self.assertEqual(order, ["app:z", "app:x", "app:y"])
        pinned, order = self.drop("x", "z", True)
        self.assertEqual(order, ["app:y", "app:z", "app:x"])
        self.assertEqual(pinned, self.pinned)

    def test_other_app_dropped_among_launchers_is_pinned_there(self):
        def make(entry):
            return pin(entry.app_id, entry.app_id)

        pinned, order = self.drop("y", "a", True, make)
        self.assertEqual(self.ids(pinned), ["a", "y", "b", "c"])
        self.assertEqual(order, ["app:x", "app:z"])

    def test_other_app_that_cannot_be_pinned_goes_first_of_its_part(self):
        pinned, order = self.drop("y", "a", True, lambda entry: None)
        self.assertEqual(pinned, self.pinned)
        self.assertEqual(order, ["app:y", "app:x", "app:z"])

    def test_unknown_or_separator_source_changes_nothing(self):
        for source in ("nope", "sep:auto"):
            pinned, order = model.apply_drop(
                self.pinned, self.order, self.entries, source, self.keys["a"], True
            )
            self.assertEqual((pinned, order), (self.pinned, self.order))


if __name__ == "__main__":
    unittest.main()
