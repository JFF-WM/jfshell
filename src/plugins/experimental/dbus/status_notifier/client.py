def get_plugin_metadata(panel_instance):
    container = panel_instance.config_handler.get_root_setting(
        ["org.jfshell.plugin.status_notifier", "panel"], None
    )

    if container is None:
        container = "top-panel-center"
        plugin_id = ["org.jfshell.plugin.status_notifier"]
        panel_instance.config_handler.set_root_setting(plugin_id, container)

    about = (
        "This plugin acts as a system tray client for jfshell.",
        "It uses D-Bus to communicate with applications that implement the",
        "StatusNotifierItem specification, allowing it to display their icons,",
        "tooltips, and context menus.",
    )

    return {
        "id": "org.jfshell.plugin.status_notifier",
        "name": "Systray",
        "version": "1.0.0",
        "enabled": True,
        "container": container,
        "index": 5,
        "deps": ["event_manager", "css_generator"],
        "description": about,
    }


def get_plugin_class():
    import asyncio
    import os
    import re
    from gi.repository import Gdk, GLib, Gtk  # pyright: ignore
    from src.plugins.core._base import BasePlugin
    from ._service import StatusNotifierWatcher, WATCHER_BUS_NAME

    class SystrayClientPlugin(BasePlugin):
        """
        System tray (StatusNotifierItem host).

        Interaction follows the SNI spec:
          * left click   -> Activate (falls back to the menu when the item
                            has none / is menu-only)
          * middle click -> SecondaryActivate
          * right click  -> the item's dbusmenu (ContextMenu if it has none)
          * scroll       -> Scroll

        Threading: D-Bus callbacks arrive on the asyncio loop thread, every
        widget is created/changed on the GTK thread via schedule_in_gtk_thread.
        """

        ICON_SIZE = 20

        def __init__(self, panel_instance):
            super().__init__(panel_instance)
            self.panel = panel_instance

        # ---------------------------------------------------------- startup
        def on_start(self):
            self.items = {}  # item_id -> StatusNotifierItem
            self.tray_button = {}  # item_id -> Gtk.Button
            self._rebuild_pending = set()
            self._themes_added = set()

            self.subscribe_to_icon_updates()
            self.subscribe_to_removal_events()

            container_id = "org.jfshell.plugin.status_notifier"
            default_container_name = "top_panel_box_right"
            target_container_name, _ = self.panel.config_handler.get_plugin_container(
                default_container_name, container_id
            )
            attr_name = target_container_name.replace("-", "_")
            target_container = getattr(self._panel_instance, attr_name, None)

            self.tray_box = self.gtk.Box(
                orientation=self.gtk.Orientation.HORIZONTAL, spacing=4
            )
            self.tray_box.add_css_class("tray-box")
            self.tray_box.set_valign(self.gtk.Align.CENTER)

            if target_container and hasattr(target_container, "append"):
                target_container.append(self.tray_box)
            else:
                self._panel_instance.top_panel_box_center.append(self.tray_box)

            self.main_widget = (self.tray_box, "append")

            self.notifier_watcher = StatusNotifierWatcher(
                WATCHER_BUS_NAME, self.panel
            )
            self.notifier_watcher.run_server_in_background()
            self.plugins["css_generator"].install_css("status-notifier.css")

        def subscribe_to_icon_updates(self):
            self.ipc_server.add_event_subscriber(
                event_type="tray_icon_name_updated", callback=self.on_icon_updated
            )

        def subscribe_to_removal_events(self):
            self.ipc_server.add_event_subscriber(
                event_type="tray_icon_removed", callback=self.on_tray_icon_removed
            )

        # ------------------------------------------------- event callbacks
        # These run on the asyncio thread: only hop to the GTK thread here.
        def on_icon_updated(self, message: dict):
            self.schedule_in_gtk_thread(self._apply_item_update, message["data"])

        def on_tray_icon_removed(self, message: dict):
            self.schedule_in_gtk_thread(
                self._remove_button, message["data"]["item_id"]
            )

        # ------------------------------------------------------ GTK thread
        def _apply_item_update(self, data: dict):
            item_id = data["item_id"]
            self.items[item_id] = data["item"]
            self._add_theme_path(data.get("icon_theme_path"))
            button = self.tray_button.get(item_id)
            if button is None:
                button = self._create_button(data)
            self._refresh_button(button, data)

        def _add_theme_path(self, path):
            """Apps like Steam/Dropbox ship icons in their own directory."""
            if not path or path in self._themes_added or not os.path.isdir(path):
                return
            display = Gdk.Display.get_default()
            if display:
                Gtk.IconTheme.get_for_display(display).add_search_path(path)
                self._themes_added.add(path)

        def _remove_button(self, item_id: str):
            button = self.tray_button.pop(item_id, None)
            self.items.pop(item_id, None)
            self._rebuild_pending.discard(item_id)
            if button is None:
                return
            popover = getattr(button, "_tray_popover", None)
            if popover is not None:
                self._clear_listbox(popover.get_child())
                popover.unparent()
            self.tray_box.remove(button)

        # ------------------------------------------------------------ icons
        def _pixmap_to_texture(self, pixmap_data):
            """
            SNI pixmaps are ARGB32 in network byte order (bytes A,R,G,B);
            GdkPixbuf wants R,G,B,A. Passing the bytes through unchanged
            swapped the colour channels.
            """
            try:
                if not pixmap_data:
                    return None
                entries = [e for e in pixmap_data if len(e) == 3 and e[0] > 0]
                if not entries:
                    return None
                width, height, raw = min(
                    entries, key=lambda e: abs(e[0] - self.ICON_SIZE)
                )
                argb = bytes(raw)
                if len(argb) != width * height * 4:
                    self.logger.warning("Tray pixmap has the wrong size, ignoring.")
                    return None
                rgba = bytearray(len(argb))
                rgba[0::4] = argb[1::4]
                rgba[1::4] = argb[2::4]
                rgba[2::4] = argb[3::4]
                rgba[3::4] = argb[0::4]
                pixbuf = self.gdkpixbuf.Pixbuf.new_from_bytes(
                    GLib.Bytes.new(bytes(rgba)),
                    self.gdkpixbuf.Colorspace.RGB,
                    True,
                    8,
                    width,
                    height,
                    width * 4,
                )
                return Gdk.Texture.new_for_pixbuf(pixbuf)
            except Exception as e:
                self.logger.error(f"Failed to build tray pixmap: {e}")
                return None

        def _make_image(self, name, pixmap):
            """Icon name (or file path) if available, else the pixmap."""
            image = None
            if name:
                if os.path.isabs(name) and os.path.exists(name):
                    image = Gtk.Image.new_from_file(name)
                else:
                    display = Gdk.Display.get_default()
                    theme = Gtk.IconTheme.get_for_display(display) if display else None
                    if theme is None or theme.has_icon(name):
                        image = Gtk.Image.new_from_icon_name(name)
            if image is None:
                texture = self._pixmap_to_texture(pixmap)
                if texture is not None:
                    image = Gtk.Image.new_from_paintable(texture)
            if image is None:
                image = Gtk.Image.new_from_icon_name(
                    name or "application-x-executable-symbolic"
                )
            image.set_pixel_size(self.ICON_SIZE)
            return image

        def _refresh_button(self, button, data: dict):
            attention = data.get("status") == "NeedsAttention" and (
                data.get("attention_icon_name") or data.get("attention_icon_pixmap")
            )
            if attention:
                name, pixmap = (
                    data.get("attention_icon_name"),
                    data.get("attention_icon_pixmap"),
                )
                button.add_css_class("tray-attention")
            else:
                name, pixmap = data.get("icon_name"), data.get("icon_pixmap")
                button.remove_css_class("tray-attention")
            button.set_child(self._make_image(name, pixmap))

            title, text = data.get("tooltip") or ("", "")
            tooltip = "\n".join(p for p in (title, text) if p)
            button.set_tooltip_text(tooltip or None)
            # Passive items are meant to be hidden by the host
            button.set_visible(data.get("status") != "Passive")

        # ----------------------------------------------------------- buttons
        def _create_button(self, data: dict):
            item_id = data["item_id"]
            button = Gtk.Button()
            button.add_css_class("flat")
            button.add_css_class("panel-menu-button")
            button.add_css_class("tray-icon")
            self.add_cursor_effect(button)
            button.connect("clicked", self._on_primary_click, item_id)

            for mouse_button, handler in (
                (2, self._on_middle_click),
                (3, self._on_secondary_click),
            ):
                gesture = Gtk.GestureClick.new()
                gesture.set_button(mouse_button)
                gesture.connect("released", handler, item_id)
                button.add_controller(gesture)

            scroll = Gtk.EventControllerScroll.new(
                Gtk.EventControllerScrollFlags.VERTICAL
                | Gtk.EventControllerScrollFlags.DISCRETE
            )
            scroll.connect("scroll", self._on_scroll, item_id)
            button.add_controller(scroll)

            popover = Gtk.Popover()
            popover.set_parent(button)
            popover.set_child(self._new_listbox(item_id))
            button._tray_popover = popover  # pyright: ignore

            data["item"].on_layout_changed = lambda i=item_id: (
                self.schedule_in_gtk_thread(self._on_layout_changed, i)
            )
            self.tray_button[item_id] = button
            self.tray_box.append(button)
            return button

        def _new_listbox(self, item_id: str):
            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            listbox.connect("row-activated", self._on_row_activated, item_id)
            return listbox

        # ----------------------------------------------------------- clicks
        def _on_primary_click(self, _button, item_id: str):
            item = self.items.get(item_id)
            if item is None:
                return
            if item.item_is_menu:
                self.run_in_async_task(self._show_menu(item_id))
            else:
                self.run_in_async_task(self._activate_or_menu(item_id))

        async def _activate_or_menu(self, item_id: str):
            item = self.items.get(item_id)
            if item and not await item.activate(0, 0):
                await self._show_menu(item_id)

        def _on_middle_click(self, _gesture, _n_press, _x, _y, item_id: str):
            if item := self.items.get(item_id):
                self.run_in_async_task(item.secondary_activate(0, 0))

        def _on_secondary_click(self, _gesture, _n_press, _x, _y, item_id: str):
            self.run_in_async_task(self._show_menu(item_id))

        def _on_scroll(self, _controller, _dx, dy, item_id: str):
            if item := self.items.get(item_id):
                self.run_in_async_task(item.scroll(int(dy), "vertical"))
            return True

        # ------------------------------------------------------------- menus
        async def _show_menu(self, item_id: str):
            """Fetch the menu (asyncio thread), then show it (GTK thread)."""
            item = self.items.get(item_id)
            if item is None:
                return
            try:
                layout = await item.fetch_menu_layout()
            except Exception as e:
                self.logger.error(f"Failed to fetch menu for {item_id}: {e}")
                layout = []
            if not layout:
                # No dbusmenu: let the app draw its own context menu.
                await item.context_menu(0, 0)
                return
            self.schedule_in_gtk_thread(self._populate_and_popup, item_id, layout, True)

        def _on_layout_changed(self, item_id: str):
            """LayoutUpdated while the menu is open: refresh it (debounced)."""
            button = self.tray_button.get(item_id)
            popover = getattr(button, "_tray_popover", None)
            if popover is None or not popover.is_visible():
                return  # menus are fetched fresh on every open
            if item_id in self._rebuild_pending:
                return
            self._rebuild_pending.add(item_id)
            self.run_in_async_task(self._rebuild_open_menu(item_id))

        async def _rebuild_open_menu(self, item_id: str):
            try:
                await asyncio.sleep(0.25)  # debounce bursts of LayoutUpdated
                item = self.items.get(item_id)
                if item is None:
                    return
                layout = await item.fetch_menu_layout()
                self.schedule_in_gtk_thread(
                    self._populate_and_popup, item_id, layout, False
                )
            except Exception as e:
                self.logger.error(f"Menu rebuild failed for {item_id}: {e}")
            finally:
                self._rebuild_pending.discard(item_id)

        def _clear_listbox(self, listbox):
            if not isinstance(listbox, Gtk.ListBox):
                return
            while row := listbox.get_first_child():
                if sub := getattr(row, "_submenu_popover", None):
                    self._clear_listbox(sub.get_child())
                    sub.unparent()  # parented to the row: must go first
                listbox.remove(row)

        def _populate_and_popup(self, item_id: str, layout: list, popup: bool):
            button = self.tray_button.get(item_id)
            if button is None:
                return
            popover = button._tray_popover  # pyright: ignore
            listbox = popover.get_child()
            self._clear_listbox(listbox)
            self._build_menu(listbox, layout, item_id)
            if popup:
                popover.popup()

        def _on_row_activated(self, listbox, row, item_id: str):
            if sub := getattr(row, "_submenu_popover", None):
                sub.popup()
                return
            menu_item_id = row.get_name()
            if not menu_item_id or not (item := self.items.get(item_id)):
                return
            if inner := listbox.get_ancestor(Gtk.Popover):
                inner.popdown()
            if button := self.tray_button.get(item_id):
                button._tray_popover.popdown()  # pyright: ignore
            self.run_in_async_task(item.menu_event(int(menu_item_id), "clicked"))

        @staticmethod
        def _normalize_label(label: str) -> str:
            """Drop mnemonic underscores ("_Quit" -> "Quit", "__" -> "_")."""
            if not isinstance(label, str):
                return ""
            return re.sub(r"_(.)?", lambda m: m.group(1) or "", label).strip()

        def _build_menu(self, listbox, menu_data: list, item_id: str):
            visible = [i for i in menu_data if i.get("visible", True)]
            if not visible:
                row = Gtk.ListBoxRow()
                row.set_child(Gtk.Label(label="No actions available", xalign=0.5))
                row.set_sensitive(False)
                listbox.append(row)
                return
            for item in visible:
                if item.get("type") == "separator":
                    row = Gtk.ListBoxRow()
                    row.set_child(Gtk.Separator())
                    row.set_selectable(False)
                    row.set_activatable(False)
                    listbox.append(row)
                    continue

                label_text = self._normalize_label(item.get("label", ""))
                if not label_text and not item.get("submenu"):
                    continue

                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                for side in ("top", "bottom", "start", "end"):
                    getattr(box, f"set_margin_{side}")(8)

                if item.get("toggle_state") == 1:
                    box.append(Gtk.Image.new_from_icon_name("object-select-symbolic"))
                icon = self._menu_icon(item)
                if icon:
                    box.append(icon)
                label = Gtk.Label(label=label_text, xalign=0)
                label.set_hexpand(True)
                box.append(label)

                row = Gtk.ListBoxRow()
                row.set_child(box)
                if submenu_data := item.get("submenu"):
                    sub_popover = Gtk.Popover()
                    sub_listbox = self._new_listbox(item_id)
                    sub_popover.set_child(sub_listbox)
                    box.append(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                    sub_popover.set_parent(row)
                    row._submenu_popover = sub_popover  # pyright: ignore
                    self._build_menu(sub_listbox, submenu_data, item_id)
                row.set_name(str(item["id"]))
                enabled = bool(item.get("enabled", True))
                row.set_sensitive(enabled)
                row.set_activatable(enabled)
                listbox.append(row)

        def _menu_icon(self, item: dict):
            """dbusmenu icon-name, or PNG bytes in icon-data."""
            icon_name = item.get("icon_name")
            if isinstance(icon_name, str) and icon_name:
                return Gtk.Image.new_from_icon_name(icon_name)
            icon_data = item.get("icon_data")
            if isinstance(icon_data, (bytes, bytearray)) and icon_data[:4] == b"\x89PNG":
                try:
                    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(bytes(icon_data)))
                    image = Gtk.Image.new_from_paintable(texture)
                    image.set_pixel_size(16)
                    return image
                except GLib.Error:
                    return None
            return None

        # --------------------------------------------------------- lifecycle
        def on_stop(self):
            self.logger.info("SystrayClientPlugin has stopped.")

        def on_reload(self):
            self.logger.info("SystrayClientPlugin has been reloaded.")

        def on_cleanup(self):
            self.logger.info("SystrayClientPlugin is cleaning up resources.")

    return SystrayClientPlugin
