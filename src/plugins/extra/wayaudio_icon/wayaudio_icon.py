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
            "added by the wayaudio plugin. The icon follows the volume and "
            "mute state of the default output."
        ),
    }


def get_plugin_class():
    import threading
    import pulsectl
    from src.plugins.core._base import BasePlugin
    from gi.repository import Gtk
    from ._volume_icon import icon_candidates, tooltip_text, volume_percent

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
            self.stopping = threading.Event()
            self.state = None
            self.run_in_thread(self.watch_volume)

        def read_state(self, pulse) -> tuple[int, bool] | None:
            """Volume percent and mute flag of the default output, None without one."""
            name = pulse.server_info().default_sink_name
            try:
                sink = pulse.get_sink_by_name(name)
            except pulsectl.PulseError:
                return None
            return volume_percent(sink.volume.value_flat), bool(sink.mute)

        def watch_volume(self) -> None:
            """Worker thread: follow the default output until the plugin stops.

            The connection is only used from this thread; changes reach the GTK
            thread through schedule_in_gtk_thread.
            """
            changed = []

            def on_event(_event):
                changed.append(True)
                raise pulsectl.PulseLoopStop

            while not self.stopping.is_set():
                try:
                    with pulsectl.Pulse("jfshell-volume-icon") as pulse:
                        pulse.event_mask_set("sink", "server")
                        pulse.event_callback_set(on_event)
                        self.publish(self.read_state(pulse))
                        while not self.stopping.is_set():
                            pulse.event_listen(timeout=2)
                            if changed:
                                changed.clear()
                                self.publish(self.read_state(pulse))
                except Exception as e:
                    self.logger.error(f"Audio icon lost PulseAudio: {e}")
                self.stopping.wait(2)

        def publish(self, state) -> None:
            """Hand a changed state over to the GTK thread."""
            if state == self.state:
                return
            self.state = state
            self.schedule_in_gtk_thread(self.apply, state)

        def apply(self, state) -> None:
            """Set icon and tooltip from the volume state (GTK thread)."""
            percent, muted = state if state else (None, True)
            names = list(icon_candidates(percent, muted))
            self.btn.set_icon_name(self.plugins["calendar"].resolve_icon(names))
            self.btn.set_tooltip_text(tooltip_text(percent, muted))

        def on_clicked(self, *_):
            """Open the clock popover on the audio page, or close it when the
            audio page is already showing."""
            if "calendar" not in self.plugins:
                self.logger.error("Calendar plugin is not loaded.")
                return
            self.plugins["calendar"].open_page(PAGE_ID)

        def on_disable(self) -> None:
            """Stop the PulseAudio watcher thread."""
            self.stopping.set()

    return WayAudioIconPlugin
