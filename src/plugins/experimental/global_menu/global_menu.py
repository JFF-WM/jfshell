def get_plugin_metadata(panel):
    about = (
        "Shows the menu bar of the focused window in the panel. Apps export it over "
        "com.canonical.dbusmenu and jff tells the panel where each window's menu lives."
    )
    id = "org.jfshell.plugin.global_menu"
    default_container = "top-panel-left"
    container, id = panel.config_handler.get_plugin_container(default_container, id)

    return {
        "id": id,
        "name": "Global Menu",
        "version": "1.0.0",
        "enabled": True,
        "container": container,
        "index": 2,
        "deps": [],
        "description": about,
    }


def get_plugin_class():
    import asyncio
    from dataclasses import replace
    from functools import partial
    from typing import Any, Dict, List, Optional, Set

    from src.plugins.core._base import BasePlugin
    from ._adaptive import AdaptiveMenuBar
    from ._menu_model import (
        MenuEntry,
        entry_label,
        normalize_mode,
        select_menu_address,
        split_sections,
    )
    from ._dbus import DBusMenuClient, connect_session_bus

    class GlobalMenuPlugin(BasePlugin):
        """
        Global menu bar.

        Flow: jff reports ``dbus-annotation`` events (the dbusmenu address of each
        window) and focus changes through the ipc server. The address of the focused
        window is bound with DBusMenuClient, its top level rows become buttons, and a
        click fetches that row's submenu and pops it up.

        Responsive layout: the buttons live in an AdaptiveMenuBar. In ``auto`` mode it
        swaps them for one Menu button when the width it is given does not fit them,
        and that button pops up every top level row as a submenu. The ``mode``
        setting can pin ``full`` or ``compact``.

        Threading: ipc callbacks arrive on the ipc server thread and D-Bus calls run on
        the global asyncio loop, every widget is touched on the GTK thread via
        schedule_in_gtk_thread.
        """

        REFRESH_DELAY_MS = 80

        def __init__(self, panel_instance):
            super().__init__(panel_instance)
            self._annotations: Dict[int, Dict[str, Any]] = {}
            self._removed_ids: Set[int] = set()
            self._focused: Optional[int] = None
            self._bus: Any = None
            self._bus_lock: Optional[asyncio.Lock] = None
            self._client: Optional[DBusMenuClient] = None
            self._address: Optional[tuple] = None
            self._generation = 0
            self._buttons: List[Any] = []
            self._popovers: Dict[int, Any] = {}
            self._refresh_source = 0
            self._box: Any = None
            self._actions: Any = None
            self._bar: Any = None
            self._compact_button: Any = None
            self._compact_popover: Any = None
            self._entries: List[MenuEntry] = []
            self._mode = normalize_mode(
                self.get_plugin_setting_add_hint(
                    ["mode"],
                    "auto",
                    "How the menu bar is shown: 'auto' collapses it into a single Menu "
                    "button when it does not fit the panel, 'full' always shows every "
                    "entry, 'compact' always shows the Menu button.",
                )
            )

        # ---------------------------------------------------------- startup
        def on_start(self):
            self._box = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL)
            self._box.add_css_class("global-menu-box")
            self._box.set_valign(self.gtk.Align.CENTER)

            self._compact_button = self.gtk.Button.new_from_icon_name(
                "open-menu-symbolic"
            )
            self._compact_button.add_css_class("global-menu-button")
            self._compact_button.add_css_class("global-menu-compact")
            self._compact_button.add_css_class("flat")
            self._compact_button.set_valign(self.gtk.Align.CENTER)
            self._compact_button.set_tooltip_text("Menu")
            self._compact_button.connect("clicked", self._on_compact_clicked)
            self.gtk_helper.add_cursor_effect(self._compact_button)

            self._bar = AdaptiveMenuBar(self._box, self._compact_button, self._mode)
            self._bar.set_valign(self.gtk.Align.CENTER)
            self._bar.set_visible(False)
            self.main_widget = (self._bar, "append")

            self._actions = self.gio.SimpleActionGroup()
            click = self.gio.SimpleAction.new("click", self.glib.VariantType.new("i"))
            click.connect("activate", self._on_item_activated)
            self._actions.add_action(click)
            # On the bar, not the row: the compact popover is not below the row.
            self._bar.insert_action_group("gmenu", self._actions)

            # Subscribe before asking for the snapshot so no event falls in between.
            for name, callback in (
                ("view-focused", self._on_view_focused),
                ("view-unmapped", self._on_view_unmapped),
                ("dbus-annotation", self._on_annotation),
                ("dbus-annotation-removed", self._on_annotation_removed),
            ):
                try:
                    self.ipc_server.add_event_subscriber(name, callback)
                except Exception as e:
                    self.logger.error(f"global_menu could not subscribe to {name}: {e}")

            self.run_in_async_task(self._get_bus())
            self.global_executor.submit(self._load_snapshot)

        def on_stop(self):
            self._generation += 1
            if self._refresh_source:
                self.glib.source_remove(self._refresh_source)
                self._refresh_source = 0
            self._drop_client()
            self._clear_bar()
            bus, self._bus = self._bus, None
            if bus is not None:
                # Letting go of the registrar name makes apps show their own menu bar again.
                try:
                    bus.disconnect()
                except Exception as e:
                    self.logger.warning(f"global_menu could not leave the bus: {e}")

        # ------------------------------------------------------------- state
        def _load_snapshot(self):
            """Thread: what jff knows right now, the events only report changes."""
            try:
                from src.core.compositor.ipc import jff_snapshot

                snap = jff_snapshot()
            except Exception as e:
                self.logger.error(f"global_menu could not read jff's snapshot: {e}")
                return
            self.schedule_in_gtk_thread(self._apply_snapshot, snap)

        def _apply_snapshot(self, snap: dict):
            for a in snap.get("dbus_annotations", []):
                if a["id"] not in self._removed_ids:
                    self._annotations.setdefault(a["id"], a)
            if self._focused is None:
                self._focused = snap.get("focused")
            self._refresh()

        def _on_view_focused(self, msg: dict):
            self.schedule_in_gtk_thread(self._set_focused, msg["view"]["id"])

        def _on_view_unmapped(self, msg: dict):
            self.schedule_in_gtk_thread(self._window_gone, msg["view"]["id"])

        def _on_annotation(self, msg: dict):
            self.schedule_in_gtk_thread(self._set_annotation, msg["annotation"])

        def _on_annotation_removed(self, msg: dict):
            self.schedule_in_gtk_thread(self._remove_annotation, msg["id"])

        def _set_focused(self, window_id):
            self._focused = window_id
            self._refresh()

        def _window_gone(self, window_id):
            if self._focused == window_id:
                self._focused = None
                self._refresh()

        def _set_annotation(self, annotation: dict):
            self._annotations[annotation["id"]] = annotation
            self._refresh()

        def _remove_annotation(self, annotation_id: int):
            self._annotations.pop(annotation_id, None)
            self._removed_ids.add(annotation_id)
            self._refresh()

        def _refresh(self):
            """Show the menu of the focused window, or nothing when it has none."""
            address = select_menu_address(self._annotations.values(), self._focused)
            if address == self._address:
                return
            self._address = address
            self._generation += 1
            self._drop_client()
            self._clear_bar()
            if address is not None:
                self.run_in_async_task(self._attach(self._generation, *address))

        # -------------------------------------------------------------- D-Bus
        async def _get_bus(self):
            if self._bus_lock is None:
                self._bus_lock = asyncio.Lock()
            async with self._bus_lock:
                if self._bus is None:
                    self._bus = await connect_session_bus(self.logger)
            return self._bus

        async def _attach(self, generation: int, service_name: str, object_path: str):
            try:
                bus = await self._get_bus()
                client = DBusMenuClient(bus, service_name, object_path, self.logger)
                await client.connect(partial(self._layout_updated, generation))
                entries = await client.fetch(0, 1)
            except Exception as e:
                self.logger.warning(
                    f"global_menu could not read {service_name}{object_path}: {e}"
                )
                return
            self.schedule_in_gtk_thread(self._show_bar, generation, client, entries)

        def _layout_updated(self, generation: int, parent: int):
            """Asyncio thread: the app changed its menu."""
            if parent == 0:
                self.schedule_in_gtk_thread(self._queue_bar_refresh, generation)

        def _queue_bar_refresh(self, generation: int):
            if generation != self._generation or self._refresh_source:
                return
            self._refresh_source = self.glib.timeout_add(
                self.REFRESH_DELAY_MS, self._refresh_bar, generation
            )

        def _refresh_bar(self, generation: int):
            self._refresh_source = 0
            client = self._client
            if generation == self._generation and client is not None:
                self.run_in_async_task(
                    client.fetch(0, 1), on_finish=partial(self._fill_bar, generation)
                )
            return self.glib.SOURCE_REMOVE

        def _drop_client(self):
            client, self._client = self._client, None
            if client is not None:
                client.close()

        # --------------------------------------------------------------- widgets
        def _show_bar(self, generation: int, client: DBusMenuClient, entries: list):
            if generation != self._generation:
                client.close()
                return
            self._client = client
            self._fill_bar(generation, entries)

        def _fill_bar(self, generation: int, entries: List[MenuEntry]):
            if generation != self._generation:
                return
            self._clear_bar()
            self._entries = list(entries)
            for entry in entries:
                if entry.separator or not entry.label:
                    continue
                button = self.gtk.Button(label=entry.label)
                button.add_css_class("global-menu-button")
                button.add_css_class("flat")
                button.set_sensitive(entry.enabled)
                button.connect("clicked", self._on_top_clicked, entry, generation)
                self.gtk_helper.add_cursor_effect(button)
                self._box.append(button)
                self._buttons.append(button)
            self._bar.set_visible(bool(self._buttons))

        def _clear_bar(self):
            for popover in self._popovers.values():
                popover.unparent()
            self._popovers.clear()
            if self._compact_popover is not None:
                self._compact_popover.unparent()
                self._compact_popover = None
            for button in self._buttons:
                self._box.remove(button)
            self._buttons.clear()
            self._entries = []
            if self._bar is not None:
                self._bar.set_visible(False)

        def _on_top_clicked(self, button, entry: MenuEntry, generation: int):
            client = self._client
            if generation != self._generation or client is None:
                return
            # Not every app sets children-display on its top level rows, so always ask.
            self.run_in_async_task(
                client.fetch(entry.id, -1),
                on_finish=partial(self._popup, generation, button, entry.id),
            )

        def _on_compact_clicked(self, button) -> None:
            client = self._client
            if client is None or not self._entries:
                return
            self.run_in_async_task(
                self._fetch_tree(client, list(self._entries)),
                on_finish=partial(self._popup_compact, self._generation, button),
            )

        async def _fetch_tree(
            self, client: DBusMenuClient, entries: List[MenuEntry]
        ) -> List[tuple]:
            """Each top level row with its submenu, fetched as a click on its button would."""
            shown = [e for e in entries if not e.separator and e.label]
            submenus = await asyncio.gather(*(client.fetch(e.id, -1) for e in shown))
            return list(zip(shown, submenus))

        def _popup_compact(self, generation: int, button, tree: List[tuple]) -> None:
            if generation != self._generation:
                return
            menu = self.gio.Menu()
            for entry, children in tree:
                # Top level rows often lack children-display, the children are the proof.
                row = replace(
                    entry,
                    children=tuple(children),
                    has_submenu=entry.has_submenu or bool(children),
                )
                menu.append_item(self._build_item(row))
            if not menu.get_n_items():
                return
            popover = self._compact_popover
            if popover is None:
                popover = self.gtk.PopoverMenu.new_from_model(menu)
                popover.set_has_arrow(False)
                popover.set_halign(self.gtk.Align.START)
                popover.set_parent(button)
                self._compact_popover = popover
            else:
                popover.set_menu_model(menu)
            popover.popup()

        def _popup(
            self, generation: int, button, entry_id: int, entries: List[MenuEntry]
        ):
            if generation != self._generation:
                return
            if not entries:
                # A plain row with nothing below it: it is the action itself.
                if self._client is not None:
                    self.run_in_async_task(self._client.click(entry_id))
                return
            model = self._build_model(entries)
            popover = self._popovers.get(entry_id)
            if popover is None:
                popover = self.gtk.PopoverMenu.new_from_model(model)
                popover.set_has_arrow(False)
                popover.set_halign(self.gtk.Align.START)
                popover.set_parent(button)
                self._popovers[entry_id] = popover
            else:
                popover.set_menu_model(model)
            popover.popup()

        def _build_model(self, entries: List[MenuEntry]):
            menu = self.gio.Menu()
            for section in split_sections(entries):
                part = self.gio.Menu()
                for entry in section:
                    part.append_item(self._build_item(entry))
                menu.append_section(None, part)
            return menu

        def _build_item(self, entry: MenuEntry):
            if entry.has_submenu and entry.children:
                item = self.gio.MenuItem.new_submenu(
                    entry_label(entry), self._build_model(list(entry.children))
                )
                return item
            # An item whose action doesn't exist is shown greyed out.
            item = self.gio.MenuItem.new(entry_label(entry), "gmenu.unavailable")
            if entry.enabled and not entry.has_submenu:
                item.set_action_and_target_value(
                    "gmenu.click", self.glib.Variant.new_int32(entry.id)
                )
            return item

        def _on_item_activated(self, _action, parameter):
            client = self._client
            if client is not None and parameter is not None:
                self.run_in_async_task(client.click(parameter.get_int32()))

    return GlobalMenuPlugin
