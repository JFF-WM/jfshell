def get_plugin_metadata(_) -> dict:
    return {
        "id": "org.jfshell.plugin.overview_only_panels",
        "name": "Overview Only Panels",
        "version": "1.0.1",
        "enabled": True,
        "container": "background",
        "deps": ["event_manager"],
        "description": "Hides every non-exclusive panel window unless the overview is open. The bottom panel of a monitor shows only while the overview is open on that monitor.",
    }


def get_plugin_class():
    from src.plugins.core._base import BasePlugin
    from src.plugins.core.panels._outputs import in_overview

    class OverviewOnlyPanels(BasePlugin):
        """Shows non-exclusive panel windows only while the overview is open.

        The bottom panel (the dock) can have a window on every monitor. The
        overview shows only the one on the monitor the overview is open on.
        """

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
            # Output the overview is open on, None when closed or not known.
            self.overview_output = None
            self._mirror_host = None
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
            self.overview_output = (
                self.ipc.get_overview_output() if self.overview_open else None
            )
            self.glib.timeout_add(500, self._apply_once)

        def _on_overview(self, msg: dict) -> None:
            self.overview_open = bool(msg.get("state", False))
            # jff names the monitor the overview is open on, and sends the event
            # again when the overview moves to another monitor.
            self.overview_output = (
                (msg.get("output") or self.ipc.get_overview_output())
                if self.overview_open
                else None
            )
            self._schedule_apply()

        def _on_mirror(self, kind: str, output: str, _box) -> None:
            """A copy of the bottom panel came or went: show or hide it as needed."""
            if kind == "removed":
                self._hidden.discard(f"bottom_panel@{output}")
            self._schedule_apply()

        def _attach_mirror_host(self) -> None:
            """Listens to the bottom panel for its windows on other monitors."""
            if self._mirror_host is not None:
                return
            host = self.plugins.get("bottom_panel")
            if host is not None and hasattr(host, "add_mirror_listener"):
                host.add_mirror_listener(self._on_mirror)
                self._mirror_host = host

        def _panel_windows(self):
            """``(name, window, output)`` of every panel window.

            The bottom panel windows on other monitors are named
            ``bottom_panel@<output>``. ``output`` is None when the window's
            monitor is not known.
            """
            for name in self.PANELS:
                window = getattr(self._panel_instance, name, None)
                if window is None:
                    continue
                try:
                    monitor = self.layer_shell.get_monitor(window)
                except Exception:
                    monitor = None
                yield name, window, monitor.get_connector() if monitor else None
            host = self._mirror_host
            mirrors = host.mirrors() if host is not None else {}
            for output, (window, _box) in mirrors.items():
                yield f"bottom_panel@{output}", window, output

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
            self._attach_mirror_host()
            workspace_empty = self._current_workspace_is_empty()
            for name, window, output in self._panel_windows():
                panel = name.split("@", 1)[0]
                show_all = workspace_empty or self.overview_open
                if panel == "bottom_panel" and not workspace_empty:
                    # The dock of each monitor belongs to the overview opened there.
                    show_all = self.overview_open and in_overview(
                        output, self.overview_output
                    )
                if self._is_exclusive(panel):
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
            if self._mirror_host is not None:
                self._mirror_host.remove_mirror_listener(self._on_mirror)
                self._mirror_host = None
            if self.event_mgr:
                self.event_mgr.unsubscribe_from_event(
                    "overview-changed", self._on_overview
                )
                for ev in self.VIEW_EVENTS:
                    self.event_mgr.unsubscribe_from_event(ev, self._on_view_event)

        def on_disable(self) -> None:
            self.overview_open = True
            self.overview_output = None
            self._apply()

    return OverviewOnlyPanels
