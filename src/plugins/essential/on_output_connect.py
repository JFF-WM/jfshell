def get_plugin_metadata(_):
    about = "Automatically moves the panel to a valid monitor when the current one is disabled."
    return {
        "id": "org.jfshell.plugin.on_output_connect",
        "name": "Panel Output Mover",
        "version": "1.1.2",
        "enabled": True,
        "deps": ["event_manager"],
        "description": about,
    }


def get_plugin_class():
    from gi.repository import Gtk4LayerShell as LayerShell  # pyright: ignore
    from src.core.create_panel import get_monitor_info, get_target_monitor
    from src.plugins.core._base import BasePlugin

    class PanelOutputMoverPlugin(BasePlugin):
        """Move the panel to the first available output when its current one is disabled."""

        PLUGIN_NAME = "panel_output_mover"

        def __init__(self, panel_instance):
            super().__init__(panel_instance)
            self.panel = panel_instance

        def on_start(self):
            self.current_output_name = None
            self._debounce_timeout_id = None

            self.primary_output_name = self.config_handler.get_root_setting(
                ["org.jfshell.panel", "primary_output", "name"], None
            )

            self.debounce_delay_ms = self.get_plugin_setting_add_hint(
                ["debounce_delay_ms"],
                150,
                "Delay (ms) before moving the panel after an output change.",
            )

            # FIX: Access event_manager via self.plugins
            self.event_mgr = self.plugins.get("event_manager")
            if self.event_mgr:
                self.event_mgr.subscribe_to_event(
                    "OutputsChanged", self.on_outputs_changed
                )
            else:
                self.logger.error("Event Manager dependency not found.")

            self.glib.idle_add(self._apply_initial_output)

        def on_stop(self):
            """Cleanup subscriptions on disable."""
            if hasattr(self, "event_mgr") and self.event_mgr:
                self.event_mgr.unsubscribe_from_event(
                    "OutputsChanged", self.on_outputs_changed
                )

        def get_first_output(self):
            outputs = self.ipc.list_outputs()
            if outputs and len(outputs) > 0:
                return outputs[0]["name"]
            return None

        def _apply_initial_output(self):
            if not self.primary_output_name:
                self.primary_output_name = self.get_first_output()

            try:
                self._set_panel_on_output()
            except Exception as e:
                self.logger.error(f"Initial assignment failed: {e}")
            return False

        def on_outputs_changed(self, event_message):
            if self._debounce_timeout_id:
                self.glib.source_remove(self._debounce_timeout_id)

            self._debounce_timeout_id = self.glib.timeout_add(
                self.debounce_delay_ms, self._debounced_update
            )

        def _debounced_update(self):
            self._debounce_timeout_id = None
            try:
                active_outputs = self.ipc.list_outputs()
                if not active_outputs:
                    return False

                active_names = [o["name"] for o in active_outputs]

                if self.primary_output_name in active_names:
                    self.current_output_name = self.primary_output_name
                else:
                    self.current_output_name = active_names[0] if active_names else None

                if self.current_output_name:
                    self._set_panel_on_output()
            except Exception as e:
                self.logger.error(f"Error during debounced monitor move: {e}")

            return False

        def _set_panel_on_output(self):
            monitors = get_monitor_info()
            monitor = get_target_monitor(self.config_handler.config_data, monitors)

            if not monitor:
                return

            monitor_gdk_obj = monitor.get("monitor")
            if not monitor_gdk_obj:
                return

            LayerShell.set_monitor(self.top_panel, monitor_gdk_obj)
            LayerShell.set_monitor(self.left_panel, monitor_gdk_obj)
            LayerShell.set_monitor(self.right_panel, monitor_gdk_obj)
            LayerShell.set_monitor(self.bottom_panel, monitor_gdk_obj)

            self.logger.info(f"Panel anchored to: {monitor_gdk_obj.get_connector()}")

    return PanelOutputMoverPlugin
