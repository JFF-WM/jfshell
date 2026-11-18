def get_plugin_metadata(_):
    about = (
        "Adds an HDR page to the clock popover: turn HDR on or off per monitor "
        "and adjust SDR white, peak, contrast, black level, saturation, white "
        "balance, tone mapping and gamut mapping while jff runs."
    )

    return {
        "id": "org.jfshell.plugin.hdr",
        "name": "HDR",
        "version": "1.0.0",
        "enabled": True,
        "priority": 98,
        "deps": ["calendar", "css_generator"],
        "description": about,
    }


def get_plugin_class():
    from src.core.compositor.ipc import jff_msg
    from src.plugins.core._base import BasePlugin
    from ._client import HdrClient, describe
    from ._page import HdrPage

    PAGE_ID = "hdr"
    SETTINGS_KEY = ["settings"]
    RESTORE_KEY = ["restore_on_start"]

    class HdrPlugin(BasePlugin):
        """Plugin that adds an HDR control page to the calendar popover's sidebar."""

        def __init__(self, panel_instance):
            super().__init__(panel_instance)

        def on_start(self):
            """Read the settings, build the page and add it to the popover."""
            self.monitor = None
            self.last_state = None
            self.refresh_id = 0
            self.epoch = 0
            self.page = None
            self.calendar = None
            self._pending_restore = {}
            self._restore_done = False
            self.client = HdrClient(jff_msg)

            self.add_hint(["Settings of the HDR page in the clock popover."], None)
            icon = self.get_plugin_setting_add_hint(
                ["icon"],
                "video-display-symbolic",
                "Icon name of the HDR button in the popover's sidebar.",
            )
            peak_max = self.get_plugin_setting_add_hint(
                ["peak_max"],
                4000,
                "Upper end of the peak brightness slider, in nits (100 to 10000).",
            )
            max_height = self.get_plugin_setting_add_hint(
                ["max_height"],
                420,
                "Height in pixels the settings list grows to before it scrolls.",
            )
            self.refresh_seconds = self.get_plugin_setting_add_hint(
                ["refresh_seconds"],
                2,
                "How often the page re-reads the monitor while it is open, in seconds.",
            )
            self.restore_on_start = bool(self.get_plugin_setting(RESTORE_KEY, True))

            if "calendar" not in self.plugins:
                self.logger.error("Calendar plugin is not loaded. Cannot add HDR page.")
                return

            self.calendar = self.plugins["calendar"]
            self.page = HdrPage(
                self.gtk,
                self.glib,
                on_enabled=self.on_enabled,
                on_change=self.on_change,
                on_reset=self.on_reset,
                on_copy=self.on_copy,
                on_monitor=self.on_monitor,
                on_restore_toggled=self.on_restore_toggled,
                peak_max=min(max(int(peak_max), 100), 10000),
                max_height=int(max_height),
            )

            # Apply the persisted restore switch value BEFORE the switch is
            # connected (done in _page.build), so no spurious callback fires.
            self.page.set_restore_on_start(self.restore_on_start)

            # Preload the last-known page values into the widgets (visual only).
            saved = self.get_plugin_setting(SETTINGS_KEY, {}) or {}
            if saved:
                self.page.set_settings(saved)
                if self.restore_on_start:
                    self._pending_restore = dict(saved)

            self.calendar.add_page(
                PAGE_ID,
                [
                    icon,
                    "preferences-desktop-display-symbolic",
                    "video-display-symbolic",
                ],
                "HDR",
                self.page.root,
                on_show=self.on_page_show,
                on_hide=self.on_page_hide,
            )
            self.plugins["css_generator"].install_css("hdr.css")

            # Kick off a refresh now, so restore-to-jff runs on startup
            # instead of only when the user opens the HDR tab.
            if self._pending_restore:
                self.refresh()

        def _save_page_settings(self):
            if self.page is None:
                return
            try:
                self.set_plugin_setting(SETTINGS_KEY, self.page.get_settings())
            except Exception as e:
                self.logger.error(f"Failed to save HDR settings: {e}")

        def on_restore_toggled(self, value):
            """The user flipped the restore-on-start switch."""
            self.restore_on_start = bool(value)
            try:
                self.set_plugin_setting(RESTORE_KEY, self.restore_on_start)
            except Exception as e:
                self.logger.error(f"Failed to save restore_on_start: {e}")
            if not self.restore_on_start:
                self._pending_restore = {}

        def on_disable(self):
            self.stop_polling()
            if self.page:
                self.page.cancel_timers()
            if self.calendar:
                self.calendar.remove_page(PAGE_ID)

        def on_page_show(self):
            self.refresh()
            self.stop_polling()
            self.refresh_id = self.glib.timeout_add_seconds(
                max(int(self.refresh_seconds), 1), self.on_tick
            )

        def on_page_hide(self):
            self.stop_polling()

        def stop_polling(self):
            if self.refresh_id:
                self.glib.source_remove(self.refresh_id)
                self.refresh_id = 0

        def on_tick(self):
            self.refresh()
            return True

        def refresh(self, error=None):
            self.run_in_thread(self.fetch_state, self.monitor, self.epoch, error)

        def fetch_state(self, wanted, epoch, error):
            try:
                outputs = self.client.monitors()
                names = [str(o.get("name", "")) for o in outputs]
                if not names:
                    raise RuntimeError("jff reports no monitors")
                name = wanted if wanted in names else names[0]
                output = outputs[names.index(name)]
                state = self.client.state(name, output)
            except Exception as e:
                self.schedule_in_gtk_thread(self.apply_failure, str(e), epoch)
                return
            self.schedule_in_gtk_thread(self.apply_state, names, state, epoch, error)

        def apply_state(self, names, state, epoch, error):
            if epoch != self.epoch or self.page.timers:
                return
            self.monitor = state.name
            self.last_state = state
            self.page.enable_controls()
            self.page.set_monitors(names, state.name)
            self.page.set_state(state, describe(state))
            if error:
                self.page.set_error(error)

            # One-shot: after the first successful read, push saved values to
            # jff, but only if the restore switch is on.
            if self._pending_restore and not self._restore_done:
                self._restore_done = True
                pending = self._pending_restore
                self._pending_restore = {}
                if self.restore_on_start:
                    self._reapply_saved(pending)

        def apply_failure(self, message, epoch):
            if epoch != self.epoch:
                return
            self.page.set_unavailable(message)

        def run_command(self, action, *args):
            if not self.monitor:
                return
            self.epoch += 1
            self.run_in_thread(self.execute, action, args, self.epoch)

        def execute(self, action, args, epoch):
            error = None
            try:
                action(*args)
            except Exception as e:
                error = str(e)
            self.schedule_in_gtk_thread(self.after_command, error, epoch)

        def after_command(self, error, epoch):
            if epoch == self.epoch:
                self.refresh(error)

        def _reapply_saved(self, saved):
            """Push saved settings to jff after a restart, when jff lost them."""
            if not self.monitor:
                return
            try:
                if saved.get("enabled") is True:
                    self.run_command(self.client.set_enabled, self.monitor, True)
                if "sdr_white" in saved:
                    self.run_command(
                        self.client.set_sdr_white,
                        self.monitor,
                        int(saved["sdr_white"]),
                    )
                if "peak" in saved:
                    self.run_command(
                        self.client.set_value,
                        self.monitor,
                        "peak",
                        float(saved["peak"]),
                    )
                for key in ("contrast", "black_level", "saturation"):
                    if key in saved:
                        self.run_command(
                            self.client.set_value,
                            self.monitor,
                            key,
                            float(saved[key]),
                        )
                wb = saved.get("white_balance")
                if wb and len(wb) == 3:
                    self.run_command(
                        self.client.set_value,
                        self.monitor,
                        "white-balance",
                        *[float(v) for v in wb],
                    )
                if "tone_map" in saved:
                    self.run_command(
                        self.client.set_value,
                        self.monitor,
                        "tone-map",
                        saved["tone_map"],
                    )
                if "gamut_map" in saved:
                    self.run_command(
                        self.client.set_value,
                        self.monitor,
                        "gamut-map",
                        saved["gamut_map"],
                    )
            except Exception as e:
                self.logger.error(f"Failed to reapply HDR settings: {e}")

        def on_enabled(self, enabled):
            self._save_page_settings()
            self.run_command(self.client.set_enabled, self.monitor, bool(enabled))

        def on_change(self, key, values):
            self._save_page_settings()
            if key == "sdr_white":
                self.run_command(self.client.set_sdr_white, self.monitor, values[0])
            elif key == "white-balance":
                self.run_command(
                    self.client.set_value,
                    self.monitor,
                    key,
                    *[float(v) for v in values],
                )
            elif key in ("contrast", "black-level", "saturation"):
                self.run_command(
                    self.client.set_value, self.monitor, key, float(values[0])
                )
            else:
                self.run_command(self.client.set_value, self.monitor, key, values[0])

        def on_reset(self):
            self.run_command(self.client.reset, self.monitor)

        def on_monitor(self, name):
            self.monitor = name
            self.epoch += 1
            self.refresh()

        def on_copy(self):
            state = self.last_state
            if state is None or not state.kdl:
                self.page.set_note("Nothing differs from the config file.")
                return
            display = self.gdk.Display.get_default()
            clipboard = display.get_clipboard() if display else None
            if clipboard is None:
                self.page.set_error("No clipboard to copy to.")
                return
            try:
                clipboard.set(state.kdl)
            except Exception:
                clipboard.set_content(self.gdk.ContentProvider.new_for_value(state.kdl))
            self.page.set_note("Copied the changed lines for the output block.")

        def code_explanation(self):
            """
            The plugin builds one page for the clock popover and registers it
            with the calendar plugin, which owns the popover and its sidebar.
            While the page is visible it reads the monitor's HDR state from
            jff every few seconds on a background thread, and every change
            (switch, slider, dropdown, reset) is one short request to jff's
            socket on a background thread followed by a fresh reading, so the
            page always shows what the monitor really does. Each change is
            also written to the plugin's config, and the "Restore settings on
            start" switch in the page header decides whether those saved
            values are pushed back to jff after a jfshell restart.
            """
            return self.code_explanation.__doc__

    return HdrPlugin
