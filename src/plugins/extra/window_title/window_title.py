def get_plugin_metadata(_):
    about = """
            A plugin that displays the title and icon of the currently
            focused window on the panel with a context menu to open
            the Window Rules manager.
            """
    return {
        "id": "org.jfshell.plugin.window_title",
        "name": "Window Title",
        "version": "2.0.0",  # Bumped for jff
        "enabled": True,
        "index": 1,
        "container": "top-panel-left",
        "deps": [
            "event_manager",
            "css_generator",
        ],
        "description": about,
    }


def get_plugin_class():
    from src.plugins.core.event_handler_decorator import subscribe_to_event
    from src.plugins.core._base import BasePlugin
    from typing import Any, Dict, Optional

    class WindowTitlePlugin(BasePlugin):
        def __init__(self, panel_instance):
            super().__init__(panel_instance)

        def on_start(self) -> None:
            self._load_config()
            self.window_title_content = self.gtk.Box()

            self.click_gesture = self.gtk.GestureClick()
            self.click_gesture.set_button(1)
            self.click_gesture.connect("pressed", self._on_left_click)
            self.window_title_content.add_controller(self.click_gesture)

            self.main_widget = (self.window_title_content, "append")
            self.window_title_label = self.gtk.Label()
            self.window_title_icon = self.gtk.Image.new_from_icon_name("None")
            self.window_title_icon.add_css_class("window-title-icon")

            self.window_title_content.append(self.window_title_icon)
            self.window_title_content.append(self.window_title_label)
            self.window_title_content.add_css_class("window-title-content")
            self.window_title_label.add_css_class("window-title-label")

            self._debounce_timer_id: Optional[int] = None
            self._debounce_interval: int = 50
            self._last_view_data: Optional[Dict[str, Any]] = None

            # Initial state
            first_view = self.ipc.get_focused_view()
            if first_view:
                self.update_title_icon(first_view)

            self.glib.idle_add(self._subscribe_to_events_with_retry)
            self.plugins["css_generator"].install_css("window-title.css")

        def _on_left_click(self, gesture, n_press, x, y):
            view = self._last_view_data
            if not view:
                return

            popover = self.gtk.Popover()
            popover.set_parent(self.window_title_content)
            vbox = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=6)
            for m in ["start", "end", "top", "bottom"]:
                getattr(vbox, f"set_margin_{m}")(10)

            rules_plugin = self.plugins.get("org.jfshell.plugin.window_rules")
            if rules_plugin:
                btn_rules = self.gtk.Button(label="Open Window Rules")
                btn_rules.add_css_class("suggested-action")
                btn_rules.connect(
                    "clicked",
                    lambda _: [rules_plugin.open_rules_manager(), popover.popdown()],
                )
                vbox.append(btn_rules)
            else:
                vbox.append(self.gtk.Label(label="Window Rules not found"))

            popover.set_child(vbox)
            popover.popup()

        def _subscribe_to_events_with_retry(self) -> bool:
            mgr_id = "org.jfshell.plugin.event_manager"
            if mgr_id not in self.plugins:
                return self.glib.SOURCE_CONTINUE

            event_manager = self.plugins[mgr_id]
            # Use the translated events from your EventManager
            event_manager.subscribe_to_event("view-focused", self.on_view_focused)
            event_manager.subscribe_to_event("view-closed", self.on_view_closed)
            event_manager.subscribe_to_event(
                "view-title-changed", self.on_view_title_changed
            )
            return self.glib.SOURCE_REMOVE

        def _load_config(self) -> None:
            config_data = self.config_handler.config_data.get("window_title", {})
            self.title_length: int = config_data.get("title_length", 30)

        @subscribe_to_event("view-focused")
        def on_view_focused(self, event_message: Dict[str, Any]) -> None:
            view = event_message.get("view")
            # If ID is None, jff has lost focus (Overview or Desktop)
            if not view or view.get("id") is None:
                self.clear_widget()
                return
            self.update_title_icon_debounced(view)

        @subscribe_to_event("view-closed")
        def on_view_closed(self, event_message: Dict[str, Any]) -> None:
            view_id = event_message.get("view", {}).get("id")
            if self._last_view_data and view_id == self._last_view_data.get("id"):
                self.clear_widget()

        @subscribe_to_event("view-title-changed")
        def on_view_title_changed(self, event_message: Dict[str, Any]) -> None:
            view = event_message.get("view")
            if view:
                self.update_title_icon_debounced(view)

        def update_title_icon_debounced(self, view: Dict[str, Any]) -> None:
            self._last_view_data = view
            if self._debounce_timer_id is not None:
                self.glib.source_remove(self._debounce_timer_id)
            self._debounce_timer_id = self.glib.timeout_add(
                self._debounce_interval, self._perform_debounced_update
            )

        def _perform_debounced_update(self) -> bool:
            self._debounce_timer_id = None
            if self._last_view_data:
                self.update_title_icon(self._last_view_data)
            return self.glib.SOURCE_REMOVE

        def update_title_icon(self, view: Dict[str, Any]) -> None:
            """Update UI using data provided in the event payload."""
            try:
                # jff events provide 'app_id' or 'app-id' depending on translation
                app_id = view.get("app-id") or view.get("app_id")
                title = view.get("title", "")

                # If we only have an ID but no metadata (typical of FocusChanged with just ID)
                # We query the full window state from IPC
                if not title and view.get("id"):
                    full_win = self.ipc.get_focused_view()
                    if full_win:
                        title = full_win.get("title", "")
                        app_id = full_win.get("app_id", "")

                filtered_title = self.filter_title(title)
                # Fallback to app_id if title is empty
                display_title = filtered_title if filtered_title else (app_id or "")

                self.update_ui(display_title, app_id or "None")
            except Exception as e:
                self.logger.error(f"Error updating title/icon: {e}")
                self.clear_widget()

        def filter_title(self, title: str) -> str:
            if not title:
                return ""
            title = self.gtk_helper.filter_utf_for_gtk(title)
            if len(title) > self.title_length:
                title = title[: self.title_length - 1] + "…"
            return title

        def update_ui(self, title: str, icon_name: str) -> None:
            self.window_title_label.set_label(title)
            # Use app_id as the icon name for Gtk theme lookup
            self.window_title_icon.set_from_icon_name(icon_name)

            if title:
                self.window_title_content.add_css_class("title-active")
            else:
                if self.window_title_content.has_css_class("title-active"):
                    self.window_title_content.remove_css_class("title-active")

        def clear_widget(self) -> None:
            self.update_ui("", "None")
            self._last_view_data = None

    return WindowTitlePlugin
