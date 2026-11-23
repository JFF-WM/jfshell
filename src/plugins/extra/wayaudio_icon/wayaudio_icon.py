def get_plugin_metadata(panel):
    id = "org.jfshell.plugin.wayaudio_icon"
    container, id = panel.config_handler.get_plugin_container("top-panel-systray", id)
    return {
        "id": id,
        "name": "Audio Icon",
        "version": "1.0.0",
        "deps": ["calendar", "advanced_volume"],
        "index": 11,
        "container": container,
        "enabled": True,
        "description": (
            "Systray icon that opens the clock popover on the audio page "
            "added by the wayaudio plugin."
        ),
    }


def get_plugin_class():
    from src.plugins.core._base import BasePlugin
    from gi.repository import Gtk

    PAGE_ID = "wayaudio"

    class WayAudioIconPlugin(BasePlugin):
        def on_start(self):
            """Create the systray button."""
            self.btn = Gtk.Button()
            self.btn.add_css_class("volume-panel-button")
            self.btn.set_icon_name("audio-volume-high-symbolic")
            self.btn.connect("clicked", self.on_clicked)
            self.add_cursor_effect(self.btn)
            self.main_widget = (self.btn, "append")

        def on_clicked(self, *_):
            """Open the clock popover on the audio page, or close it when the
            audio page is already showing."""
            if "calendar" not in self.plugins:
                self.logger.error("Calendar plugin is not loaded.")
                return
            self.plugins["calendar"].open_page(PAGE_ID)

    return WayAudioIconPlugin
