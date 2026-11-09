def get_plugin_metadata(_):
    about = "Monitors the jff 'overview' to enable or disable exclusive layer mode on panels."
    return {
        "id": "org.jfshell.plugin.scale_exclusive",
        "name": "Panel Scale Exclusive",
        "version": "1.1.0",
        "enabled": True,
        "priority": 99,
        "deps": [
            "event_manager",
            "on_output_connect",
            "top_panel",
            "bottom_panel",
            "left_panel",
            "right_panel",
        ],
        "description": about,
    }


def get_plugin_class():
    from src.core.create_panel import get_monitor_info, get_target_monitor
    from src.plugins.core._base import BasePlugin

    class PanelScaleExclusivePlugin(BasePlugin):
        def __init__(self, panel_instance):
            super().__init__(panel_instance)

        def on_start(self):
            self.logger.info("PanelScaleExclusivePlugin started.")
            self.exclusive_state = {
                k: False for k in ["top", "bottom", "left", "right"]
            }
            self.on_output_plugin = self.plugins.get("on_output_connect")

            self.event_mgr = self.plugins.get("event_manager")

            if self.event_mgr:
                self.event_mgr.subscribe_to_event(
                    "overview-changed", self.handle_overview_event, "scale_exclusive"
                )

        def on_stop(self):
            if hasattr(self, "event_mgr") and self.event_mgr:
                self.event_mgr.unsubscribe_from_event(
                    "overview-changed", self.handle_overview_event
                )

        def set_panels_exclusive(self, exclusive=True):
            """Helper method to set/unset exclusive mode on all four panels."""

            panels = [
                self.bottom_panel,
                self.left_panel,
                self.right_panel,
            ]

            action = (
                self.set_layer_pos_exclusive
                if exclusive
                else self.unset_layer_pos_exclusive
            )

            for panel in panels:
                if panel:
                    self.update_widget_safely(action, panel)

            for k in self.exclusive_state:
                self.exclusive_state[k] = exclusive

        def handle_overview_event(self, msg):
            """
            Handle jff overview events via the translated bus.
            """
            is_open = msg.get("payload").get("is_open", False)

            if is_open:
                self.on_overview_activated()
            else:
                self.on_overview_deactivated()

        def _panel_output_name(self):
            """Name of the output the panels are configured on, or None."""
            try:
                monitors = get_monitor_info()
                monitor = get_target_monitor(self.config_handler.config_data, monitors)
                gdk_monitor = monitor.get("monitor") if monitor else None
                return gdk_monitor.get_connector() if gdk_monitor else None
            except Exception as e:
                self.logger.error(f"Could not resolve the panel output: {e}")
                return None

        def _focused_output_name(self):
            """Name of the output jff reports as focused, or None."""
            try:
                output = self.ipc.get_focused_output()
            except Exception as e:
                self.logger.error(f"Could not resolve the focused output: {e}")
                return None
            return output.get("name") if output else None

        def on_overview_activated(self):
            """Enable exclusive zones if focused output matches the target."""
            if not self.on_output_plugin:
                return

            focused = self._focused_output_name()
            target = self._panel_output_name()
            if not focused or not target or focused != target:
                self.logger.debug(
                    f"Overview opened on '{focused}', panels live on '{target}'; skipping."
                )
                return

            self.set_panels_exclusive(exclusive=True)

        def on_overview_deactivated(self):
            """Disable exclusive zones when leaving overview."""
            self.set_panels_exclusive(exclusive=False)

    return PanelScaleExclusivePlugin
