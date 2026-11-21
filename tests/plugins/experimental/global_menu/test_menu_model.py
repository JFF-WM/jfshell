import unittest

from src.plugins.experimental.global_menu._menu_model import (
    DBUSMENU_INTERFACE,
    MenuEntry,
    entry_label,
    normalize_label,
    parse_layout,
    select_menu_address,
    split_sections,
)


class _Variant:
    """Stands in for dbus_fast.Variant, which only needs a ``value``."""

    def __init__(self, value):
        self.value = value


def _node(item_id, children=(), **props):
    return (item_id, {k: _Variant(v) for k, v in props.items()}, list(children))


def _child(item_id, children=(), **props):
    """Children come wrapped in a variant ('av')."""
    return _Variant(_node(item_id, children, **props))


def _annotation(
    aid, window, service=":1.5", path="/menu", interface=DBUSMENU_INTERFACE
):
    return {
        "id": aid,
        "window": window,
        "pid": 1,
        "interface": interface,
        "service_name": service,
        "object_path": path,
    }


class SelectAddressTests(unittest.TestCase):
    def test_picks_the_menu_of_the_focused_window(self):
        table = [_annotation(1, 6, ":1.1", "/a"), _annotation(2, 9, ":1.2", "/b")]
        assert select_menu_address(table, 9) == (":1.2", "/b")

    def test_nothing_focused(self):
        assert select_menu_address([_annotation(1, 6)], None) is None

    def test_window_without_a_menu(self):
        assert select_menu_address([_annotation(1, 6)], 7) is None

    def test_ignores_other_interfaces(self):
        table = [_annotation(1, 6, interface="org.example.Other")]
        assert select_menu_address(table, 6) is None

    def test_ignores_an_annotation_without_an_address(self):
        table = [_annotation(1, 6, service=None, path=None)]
        assert select_menu_address(table, 6) is None

    def test_ignores_an_annotation_of_the_app_itself(self):
        assert select_menu_address([_annotation(1, None)], None) is None

    def test_the_newest_annotation_of_a_window_wins(self):
        table = [_annotation(1, 6, ":1.1", "/old"), _annotation(4, 6, ":1.1", "/new")]
        assert select_menu_address(table, 6) == (":1.1", "/new")


class NormalizeLabelTests(unittest.TestCase):
    def test_drops_the_mnemonic_marker(self):
        assert normalize_label("_File") == "File"

    def test_double_underscore_is_a_literal_one(self):
        assert normalize_label("Save__As") == "Save_As"

    def test_plain_label_is_unchanged(self):
        assert normalize_label("Edit") == "Edit"

    def test_trailing_marker(self):
        assert normalize_label("Open_") == "Open"


class ParseLayoutTests(unittest.TestCase):
    def test_top_level_rows_and_nested_children(self):
        layout = _node(
            0,
            [
                _child(
                    1,
                    [_child(11, label="_New"), _child(12, label="_Open")],
                    label="_File",
                ),
                _child(2, label="_Help"),
            ],
        )
        rows = parse_layout(layout)
        assert [r.label for r in rows] == ["File", "Help"]
        assert rows[0].has_submenu
        assert [c.id for c in rows[0].children] == [11, 12]
        assert not rows[1].has_submenu

    def test_children_display_marks_a_submenu_without_children(self):
        layout = _node(0, [_child(1, label="File", **{"children-display": "submenu"})])
        assert parse_layout(layout)[0].has_submenu

    def test_invisible_rows_are_dropped(self):
        layout = _node(0, [_child(1, label="A"), _child(2, label="B", visible=False)])
        assert [r.id for r in parse_layout(layout)] == [1]

    def test_disabled_and_separator(self):
        layout = _node(
            0, [_child(1, label="A", enabled=False), _child(2, type="separator")]
        )
        rows = parse_layout(layout)
        assert not rows[0].enabled
        assert rows[1].separator

    def test_toggle_state(self):
        layout = _node(
            0,
            [
                _child(1, label="A", **{"toggle-type": "checkmark", "toggle-state": 1}),
                _child(2, label="B", **{"toggle-type": "radio", "toggle-state": 0}),
                _child(3, label="C", **{"toggle-type": "bogus", "toggle-state": 1}),
            ],
        )
        a, b, c = parse_layout(layout)
        assert (a.toggle, a.checked) == ("checkmark", True)
        assert (b.toggle, b.checked) == ("radio", False)
        assert c.toggle == ""

    def test_missing_properties_get_defaults(self):
        layout = _node(0, [_child(1)])
        row = parse_layout(layout)[0]
        assert (row.label, row.enabled, row.separator, row.toggle) == (
            "",
            True,
            False,
            "",
        )

    def test_empty_menu(self):
        assert parse_layout(_node(0)) == []


class SectionTests(unittest.TestCase):
    def _rows(self, spec):
        return [MenuEntry(id=i, label=str(i), separator=s) for i, s in enumerate(spec)]

    def test_separators_split_the_rows(self):
        sections = split_sections(self._rows([False, False, True, False]))
        assert [[r.id for r in s] for s in sections] == [[0, 1], [3]]

    def test_leading_trailing_and_doubled_separators_vanish(self):
        sections = split_sections(self._rows([True, False, True, True, False, True]))
        assert [[r.id for r in s] for s in sections] == [[1], [4]]

    def test_only_separators(self):
        assert split_sections(self._rows([True, True])) == []


class EntryLabelTests(unittest.TestCase):
    def test_checked_marks(self):
        assert entry_label(MenuEntry(1, "A", toggle="checkmark", checked=True)) == "✓ A"
        assert entry_label(MenuEntry(1, "A", toggle="radio", checked=True)) == "● A"

    def test_unchecked_and_plain_rows_keep_their_label(self):
        assert entry_label(MenuEntry(1, "A", toggle="checkmark")) == "A"
        assert entry_label(MenuEntry(1, "A")) == "A"
