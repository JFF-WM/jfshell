def get_plugin_metadata(panel):
    id = "org.jfshell.plugin.workspace_switcher"
    default_container = "top-panel-left"
    container, id = panel.config_handler.get_plugin_container(default_container, id)

    return {
        "id": id,
        "name": "Workspace Switcher",
        "version": "1.0.0",
        "enabled": True,
        "container": container,
        "index": 1,
        "deps": ["event_manager", "css_generator"],
        "description": "Shows the workspaces on the focused output and lets you switch between them.",
    }


def get_plugin_class():
    from src.plugins.core._base import BasePlugin

    class WorkspaceSwitcher(BasePlugin):
        """Workspace buttons for jff, laid out in the panel."""

        def on_start(self) -> None:
            self._buttons = {}
            self._refresh_pending = False
            self._debounce_timer_id = None
            self._debounce_interval = 100
            self.css_class = self.get_plugin_setting_add_hint(
                ["css_class"],
                "workspace-switcher",
                "CSS class applied to the workspace buttons container.",
            )
            self.show_numbers = self.get_plugin_setting_add_hint(
                ["show_numbers"],
                True,
                "Show the workspace number as the button label.",
            )
            self.active_css_class = self.get_plugin_setting_add_hint(
                ["active_css_class"],
                "workspace-active",
                "CSS class applied to the focused workspace button.",
            )
            self.use_icons = self.get_plugin_setting_add_hint(
                ["use_icons"],
                False,
                "Use a dot icon instead of the workspace number.",
            )

            # Vertical panel: buttons stacked vertically.
            self.container = self.gtk.Box(
                orientation=self.gtk.Orientation.VERTICAL, spacing=2
            )
            self.container.add_css_class(self.css_class)
            self.container.set_valign(self.gtk.Align.CENTER)
            self.main_widget = (self.container, "append")

            self.plugins["css_generator"].install_css("workspace_switcher.css")

            self.event_mgr = self.plugins.get("event_manager")
            if self.event_mgr:
                for ev in (
                    "workspace-changed",
                    "view-mapped",
                    "view-unmapped",
                    "view-workspace-changed",
                    "view-focused",
                    "overview-changed",
                ):
                    self.event_mgr.subscribe_to_event(
                        ev, self._on_event, "workspace_switcher"
                    )
            else:
                self.logger.warning(
                    "Workspace Switcher: event_manager not available; buttons will not refresh on events."
                )

            self._refresh()

        # ------------------------------------------------------------------ events
        def _on_event(self, msg: dict) -> None:
            ev = msg.get("event")
            if ev == "view-focused":
                self._update_active_state()
                return
            if ev == "overview-changed":
                return
            self._schedule_refresh()

        def _schedule_refresh(self) -> None:
            if self._refresh_pending:
                return
            self._refresh_pending = True
            self._debounce_timer_id = self.glib.timeout_add(
                self._debounce_interval, self._refresh_debounced
            )

        def _refresh_debounced(self) -> bool:
            self._refresh_pending = False
            self._debounce_timer_id = None
            self._refresh()
            return False

        # ------------------------------------------------------------------ data
        def _focused_output_name(self) -> str | None:
            try:
                output = self.ipc.get_focused_output()
            except Exception:
                return None
            return output.get("name") if output else None

        def _current_workspace_index(self) -> int | None:
            try:
                return self.ipc.get_active_workspace_number()
            except Exception:
                return None

        def _workspace_ids_with_views(self) -> set[int]:
            ids: set[int] = set()
            try:
                for view in self.ipc.list_windows():
                    ws = view.get("workspace_id") or view.get("workspace")
                    if ws:
                        ids.add(int(ws))
            except Exception:
                pass
            return ids

        def _workspaces(self) -> list[dict]:
            """Every workspace jff knows about, in jff index order."""
            try:
                workspaces = self.ipc.list_workspaces() or []
            except Exception:
                return []
            return sorted(workspaces, key=lambda w: w.get("idx", 0))

        # ------------------------------------------------------------------ ui
        def _refresh(self) -> None:
            workspaces = self._workspaces()
            if not workspaces:
                return
            current = self._current_workspace_index()
            populated = self._workspace_ids_with_views()

            desired_ids = [int(w.get("id")) for w in workspaces]
            for ws_id in list(self._buttons.keys()):
                if ws_id not in desired_ids:
                    button = self._buttons.pop(ws_id)
                    parent = button.get_parent()
                    if parent is not None:
                        parent.remove(button)

            for index, workspace in enumerate(workspaces):
                ws_id = int(workspace.get("id"))
                button = self._buttons.get(ws_id)
                if button is None:
                    button = self._create_button(workspace)
                    self._buttons[ws_id] = button
                self._update_button(
                    button,
                    workspace=workspace,
                    index=index,
                    is_active=(ws_id == current),
                    has_views=(ws_id in populated),
                )
                if button.get_parent() is None:
                    self.container.append(button)

        def _create_button(self, workspace: dict):
            button = self.gtk.Button()
            button.add_css_class("workspace-button")
            button.set_has_frame(False)
            button.set_valign(self.gtk.Align.CENTER)
            button.set_focus_on_click(False)
            ws_id = int(workspace.get("id"))
            button.connect("clicked", self._on_button_clicked, ws_id)
            self.add_cursor_effect(button)
            return button

        def _update_button(
            self,
            button,
            workspace: dict,
            index: int,
            is_active: bool,
            has_views: bool,
        ) -> None:
            label_widget = button.get_child()
            if label_widget is None:
                if self.use_icons:
                    label_widget = self.gtk.Image()
                else:
                    label_widget = self.gtk.Label()
                button.set_child(label_widget)

            if self.use_icons:
                icon_name = (
                    "media-record-symbolic" if has_views else "media-optical-symbolic"
                )
                label_widget.set_from_icon_name(icon_name)
                label_widget.set_pixel_size(12)
            else:
                label_widget.set_label(str(workspace.get("idx", index + 1)))

            if is_active:
                button.add_css_class(self.active_css_class)
            else:
                self.safe_remove_css_class(button, self.active_css_class)

            if has_views:
                button.add_css_class("workspace-has-views")
            else:
                self.safe_remove_css_class(button, "workspace-has-views")

        def _update_active_state(self) -> None:
            current = self._current_workspace_index()
            for ws_id, button in self._buttons.items():
                if ws_id == current:
                    button.add_css_class(self.active_css_class)
                else:
                    self.safe_remove_css_class(button, self.active_css_class)

        # ------------------------------------------------------------------ actions
        def _on_button_clicked(self, _button, workspace_id: int) -> None:
            try:
                self.ipc.focus_workspace(workspace_id)
            except Exception as e:
                self.logger.error(
                    f"Workspace Switcher: focus failed for {workspace_id}: {e}"
                )
                return
            self._update_active_state()

        # ------------------------------------------------------------------ lifecycle
        def on_stop(self) -> None:
            if getattr(self, "_debounce_timer_id", None):
                self.glib.source_remove(self._debounce_timer_id)
                self._debounce_timer_id = None
            if self.event_mgr:
                for ev in (
                    "workspace-changed",
                    "view-mapped",
                    "view-unmapped",
                    "view-workspace-changed",
                    "view-focused",
                    "overview-changed",
                ):
                    self.event_mgr.unsubscribe_from_event(ev, self._on_event)

        def on_disable(self) -> None:
            for button in self._buttons.values():
                parent = button.get_parent()
                if parent is not None:
                    parent.remove(button)
            self._buttons.clear()

    return WorkspaceSwitcher
