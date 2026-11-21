def get_plugin_metadata(_) -> dict:
    return {
        "id": "org.jfshell.plugin.overview_only_panels",
        "name": "Overview Only Panels",
        "version": "1.0.1",
        "enabled": True,
        "container": "background",
        "deps": ["event_manager"],
        "description": "Hides every non-exclusive panel window unless the overview is open.",
    }


def get_plugin_class():
    from src.plugins.core._base import BasePlugin

    class OverviewOnlyPanels(BasePlugin):
        """Shows non-exclusive panel windows only while the overview is open."""

        PANELS = ("top_panel", "bottom_panel")
        # Config key holding the exclusive flag for each panel type.
        EXCLUSIVE_KEY = "Exclusive"

        VIEW_EVENTS = (
            "workspace-changed",
            "view-mapped",
            "view-unmapped",
            "view-workspace-changed",
            "view-focused",
        )

        def on_start(self) -> None:
            self._hidden = set()
            self._apply_pending = False
            self.overview_open = False
            self.event_mgr = self.plugins.get("event_manager")
            enabled = self.get_plugin_setting_add_hint(
                ["enabled"],
                True,
                "Hide every non-exclusive panel unless the overview is open.",
            )
            if not enabled or not self.event_mgr:
                return
            self.event_mgr.subscribe_to_event(
                "overview-changed", self._on_overview, "overview_only_panels"
            )
            for ev in self.VIEW_EVENTS:
                self.event_mgr.subscribe_to_event(
                    ev, self._on_view_event, "overview_only_panels"
                )
            self.overview_open = bool(self.ipc.is_overview_active())
            self.glib.timeout_add(500, self._apply_once)

        def _on_overview(self, msg: dict) -> None:
            self.overview_open = bool(msg.get("state", False))
            self._schedule_apply()

        def _on_view_event(self, _msg: dict) -> None:
            self._schedule_apply()

        def _schedule_apply(self) -> None:
            if self._apply_pending:
                return
            self._apply_pending = True
            self.glib.idle_add(self._apply_debounced)

        def _apply_debounced(self) -> bool:
            self._apply_pending = False
            self._apply()
            return False

        def _apply_once(self) -> bool:
            self._apply()
            return False

        def _is_exclusive(self, panel_name: str) -> bool:
            """Return True when the configured panel reserves screen space.

            panel_name is e.g. "top_panel"; the config section is
            ``org.jfshell.panel.<type>`` with an ``Exclusive`` key.
            """
            panel_type = panel_name.rsplit("_", 1)[0]
            try:
                value = self._config_handler.get_root_setting(
                    ["org.jfshell.panel", panel_type, self.EXCLUSIVE_KEY],
                    False,
                )
            except Exception:
                return False
            return bool(value)

        def _current_workspace_is_empty(self) -> bool:
            """True when the focused workspace has no windows on it."""
            try:
                workspace = self.ipc.get_focused_workspace()
                if not workspace:
                    return False
                ws_id = workspace.get("id")
                if ws_id is None:
                    return False
                ws_id = int(ws_id)
                for view in self.ipc.list_windows() or []:
                    ws = view.get("workspace_id") or view.get("workspace")
                    if ws is not None and int(ws) == ws_id:
                        return False
            except Exception:
                return False
            return True

        def _apply(self) -> None:
            show_all = self.overview_open or self._current_workspace_is_empty()
            for name in self.PANELS:
                window = getattr(self._panel_instance, name, None)
                if window is None:
                    continue
                if self._is_exclusive(name):
                    # Exclusive panels stay visible at all times.
                    if name in self._hidden:
                        self._hidden.discard(name)
                        window.present()
                    continue
                if show_all:
                    if name in self._hidden:
                        self._hidden.discard(name)
                        window.present()
                elif window.get_visible():
                    window.set_visible(False)
                    self._hidden.add(name)

        def on_stop(self) -> None:
            if self.event_mgr:
                self.event_mgr.unsubscribe_from_event(
                    "overview-changed", self._on_overview
                )
                for ev in self.VIEW_EVENTS:
                    self.event_mgr.unsubscribe_from_event(ev, self._on_view_event)

        def on_disable(self) -> None:
            self.overview_open = True
            self._apply()

    return OverviewOnlyPanels
