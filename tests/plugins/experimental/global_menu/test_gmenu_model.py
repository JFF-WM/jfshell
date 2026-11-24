import unittest

from src.plugins.experimental.global_menu._menu_model import (
    GAction,
    GTK_ACTIONS_APPLICATION,
    GTK_ACTIONS_WINDOW,
    gmenu_children,
    gmenu_entries,
    gmenu_top,
    select_gmenu_address,
    split_sections,
)


class _Variant:
    """Stands in for dbus_fast.Variant, which only needs a ``value``."""

    def __init__(self, value):
        self.value = value


def _item(label=None, action=None, target=None, section=None, submenu=None, **extra):
    item = {}
    if label is not None:
        item["label"] = _Variant(label)
    if action is not None:
        item["action"] = _Variant(action)
    if target is not None:
        item["target"] = _Variant(target)
    if section is not None:
        item[":section"] = _Variant(list(section))
    if submenu is not None:
        item[":submenu"] = _Variant(list(submenu))
    item.update({k.replace("_", "-"): _Variant(v) for k, v in extra.items()})
    return item


def _ids():
    seen = {}
    return lambda key: seen.setdefault(key, len(seen) + 1)


def _annotation(aid, window, interface, service=":1.5", path="/app"):
    return {
        "id": aid,
        "window": window,
        "pid": 1,
        "interface": interface,
        "service_name": service,
        "object_path": path,
    }


class SelectGmenuAddressTests(unittest.TestCase):
    def test_application_and_window_paths(self):
        table = [
            _annotation(1, 6, GTK_ACTIONS_WINDOW, path="/app/window/1"),
            _annotation(2, 6, GTK_ACTIONS_APPLICATION, path="/app"),
        ]
        assert select_gmenu_address(table, 6) == (":1.5", "/app", "/app/window/1")

    def test_nothing_focused(self):
        table = [_annotation(1, 6, GTK_ACTIONS_APPLICATION)]
        assert select_gmenu_address(table, None) is None

    def test_window_without_an_application_group(self):
        table = [_annotation(1, 6, GTK_ACTIONS_WINDOW)]
        assert select_gmenu_address(table, 6) is None

    def test_other_windows_are_ignored(self):
        table = [_annotation(1, 7, GTK_ACTIONS_APPLICATION)]
        assert select_gmenu_address(table, 6) is None

    def test_window_group_of_another_service_is_not_used(self):
        table = [
            _annotation(1, 6, GTK_ACTIONS_APPLICATION, ":1.5", "/app"),
            _annotation(2, 6, GTK_ACTIONS_WINDOW, ":1.9", "/other/window/1"),
        ]
        assert select_gmenu_address(table, 6) == (":1.5", "/app", None)

    def test_the_newest_annotation_wins(self):
        table = [
            _annotation(1, 6, GTK_ACTIONS_APPLICATION, path="/old"),
            _annotation(4, 6, GTK_ACTIONS_APPLICATION, path="/new"),
        ]
        assert select_gmenu_address(table, 6) == (":1.5", "/new", None)


class GmenuEntriesTests(unittest.TestCase):
    def test_sections_become_separated_rows(self):
        menus = {
            (0, 0): [_item(section=(0, 1)), _item(section=(0, 2))],
            (0, 1): [_item("_New", "app.new")],
            (0, 2): [_item("_Quit", "app.quit")],
        }
        actions = {"app.new": GAction(), "app.quit": GAction()}
        rows = gmenu_entries(menus, (0, 0), actions, _ids())
        sections = split_sections(rows)
        assert [[r.label for r in s] for s in sections] == [["New"], ["Quit"]]

    def test_missing_action_is_disabled(self):
        menus = {(0, 0): [_item("Save", "win.save"), _item("Open")]}
        rows = gmenu_entries(menus, (0, 0), {}, _ids())
        assert [r.enabled for r in rows] == [False, False]

    def test_disabled_action(self):
        menus = {(0, 0): [_item("Save", "win.save")]}
        rows = gmenu_entries(menus, (0, 0), {"win.save": GAction(enabled=False)}, _ids())
        assert rows[0].enabled is False

    def test_hidden_when_action_is_disabled(self):
        menus = {(0, 0): [_item("Save", "win.save", hidden_when="action-disabled")]}
        rows = gmenu_entries(menus, (0, 0), {"win.save": GAction(enabled=False)}, _ids())
        assert rows == []

    def test_boolean_state_is_a_checkmark(self):
        menus = {(0, 0): [_item("Wrap", "win.wrap")]}
        rows = gmenu_entries(menus, (0, 0), {"win.wrap": GAction(state=True)}, _ids())
        assert (rows[0].toggle, rows[0].checked) == ("checkmark", True)

    def test_string_state_with_a_target_is_a_radio(self):
        menus = {
            (0, 0): [
                _item("Tab", "win.width", target="4"),
                _item("Tab", "win.width", target="8"),
            ]
        }
        actions = {"win.width": GAction(state="8")}
        rows = gmenu_entries(menus, (0, 0), actions, _ids())
        assert [(r.toggle, r.checked) for r in rows] == [("radio", False), ("radio", True)]

    def test_mnemonics_are_stripped(self):
        menus = {(0, 0): [_item("_File", submenu=(1, 0))], (1, 0): []}
        assert gmenu_entries(menus, (0, 0), {}, _ids())[0].label == "File"

    def test_depth_one_leaves_submenus_without_children(self):
        menus = {
            (0, 0): [_item("File", submenu=(1, 0))],
            (1, 0): [_item("New", "app.new")],
        }
        row = gmenu_entries(menus, (0, 0), {}, _ids(), 1)[0]
        assert row.has_submenu and row.children == ()
        full = gmenu_entries(menus, (0, 0), {}, _ids(), -1)[0]
        assert [c.label for c in full.children] == ["New"]

    def test_ids_are_stable_between_calls(self):
        menus = {(0, 0): [_item("File", submenu=(1, 0))], (1, 0): []}
        id_of = _ids()
        first = gmenu_entries(menus, (0, 0), {}, id_of)[0].id
        assert gmenu_entries(menus, (0, 0), {}, id_of)[0].id == first


class GmenuTopTests(unittest.TestCase):
    def test_a_menubar_is_returned_as_it_is(self):
        menus = {(0, 0): [_item("File", submenu=(1, 0))], (1, 0): []}
        rows = gmenu_top(menus, {}, _ids(), 1)
        assert [r.label for r in rows] == ["File"]

    def test_an_app_menu_goes_under_one_row(self):
        menus = {(0, 0): [_item("About", "app.about"), _item("Quit", "app.quit")]}
        id_of = _ids()
        rows = gmenu_top(menus, {}, id_of, 1)
        assert [r.label for r in rows] == ["Application"]
        assert rows[0].has_submenu and rows[0].children == ()
        below = gmenu_children(menus, ("app",), {}, id_of)
        assert [r.label for r in below] == ["About", "Quit"]

    def test_nothing_exported(self):
        assert gmenu_top({}, {}, _ids()) == []


class GmenuChildrenTests(unittest.TestCase):
    def test_rows_of_a_submenu(self):
        menus = {
            (0, 0): [_item("File", submenu=(1, 0))],
            (1, 0): [_item("New", "app.new")],
        }
        assert [r.label for r in gmenu_children(menus, (0, 0, 0), {}, _ids())] == ["New"]

    def test_a_plain_row_has_no_children(self):
        menus = {(0, 0): [_item("New", "app.new")]}
        assert gmenu_children(menus, (0, 0, 0), {}, _ids()) == []

    def test_an_unknown_key(self):
        assert gmenu_children({}, (0, 0, 5), {}, _ids()) == []
        assert gmenu_children({}, ("sep",), {}, _ids()) == []
