"""Logic engine for the Dockbar plugin."""


class DockLogic:
    def __init__(self, plugin):
        self.p = plugin

    def is_overview_enabled(self):
        return False

    def get_orientation(self):
        from .dockbar import get_plugin_metadata

        meta = get_plugin_metadata(self.p._panel_instance)
        container = meta["container"]

        if "top-panel" in container or "bottom-panel" in container:
            return self.p.gtk.Orientation.HORIZONTAL
        return (
            self.p.gtk.Orientation.HORIZONTAL
            if self.p.panel_orientation.lower() == "h"
            else self.p.gtk.Orientation.VERTICAL
        )

    def on_left_click(self, cmd):
        self.p.cmd.run(cmd)

    def on_right_click(self, cmd):
        self.p.cmd.run(cmd)

    def on_middle_click(self, cmd):
        self.p.cmd.run(cmd)

    def setup_file_watcher(self):
        config_file = self.p.config_handler.config_file
        try:
            self.p.gio_config_file = self.p.gio.File.new_for_path(str(config_file))
            self.p._config_observer = self.p.gio_config_file.monitor_file(
                self.p.gio.FileMonitorFlags.NONE, None
            )
            self.p._config_observer.connect(
                "changed", self.p._on_gio_config_file_changed
            )
            self.p._last_config_mod_time = self.p.os.path.getmtime(config_file)
        except Exception as e:
            self.p.logger.error(f"Dockbar watcher failed: {e}")
