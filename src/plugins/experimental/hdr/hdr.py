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
    """
    The main plugin class entry point. ALL imports are deferred here
    to comply with the JFShell loading architecture.
    Returns:
        type: The HdrPlugin class.
    """
    from src.core.compositor.ipc import jff_msg
    from src.plugins.core._base import BasePlugin
    from ._client import HdrClient, describe
    from ._page import HdrPage

    PAGE_ID = "hdr"

    class HdrPlugin(BasePlugin):
        """
        Plugin that adds an HDR control page to the calendar popover's sidebar.
        """

        def __init__(self, panel_instance):
            """
            Initialize the plugin.
            Args:
                panel_instance: The main panel instance provided by the framework.
            """
            super().__init__(panel_instance)

        def on_start(self):
            """Read the settings, build the page and add it to the popover."""
            self.monitor = None
            self.last_state = None
            self.refresh_id = 0
            self.epoch = 0
            self.applied_epoch = -1
            self.page = None
            self.calendar = None
            self.client = HdrClient(jff_msg)
            self.add_hint(
                ["Settings of the HDR page in the clock popover."],
                None,
            )
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
                peak_max=min(max(int(peak_max), 100), 10000),
                max_height=int(max_height),
            )
            self.calendar.add_page(
                PAGE_ID,
                [icon, "preferences-desktop-display-symbolic", "video-display-symbolic"],
                "HDR",
                self.page.root,
                on_show=self.on_page_show,
                on_hide=self.on_page_hide,
            )
            self.plugins["css_generator"].install_css("hdr.css")

        def on_disable(self):
            """Stop polling and take the page out of the popover."""
            self.stop_polling()
            if self.page:
                self.page.cancel_timers()
            if self.calendar:
                self.calendar.remove_page(PAGE_ID)

        def on_page_show(self):
            """The page became visible: read the monitor now and keep reading it."""
            self.refresh()
            self.stop_polling()
            self.refresh_id = self.glib.timeout_add_seconds(
                max(int(self.refresh_seconds), 1), self.on_tick
            )

        def on_page_hide(self):
            """The page is no longer visible: stop reading the monitor."""
            self.stop_polling()

        def stop_polling(self):
            """Remove the refresh timer."""
            if self.refresh_id:
                self.glib.source_remove(self.refresh_id)
                self.refresh_id = 0

        def on_tick(self):
            """Timer callback of the refresh."""
            self.refresh()
            return True

        def refresh(self, error=None):
            """Read the monitors in the background and show the result."""
            self.run_in_thread(self.fetch_state, self.monitor, self.epoch, error)

        def fetch_state(self, wanted, epoch, error):
            """Background part of refresh: runs off the GTK thread."""
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
            """Show a monitor's state. A reading older than the last change is dropped."""
            if epoch != self.epoch or self.page.timers:
                return
            self.monitor = state.name
            self.last_state = state
            self.page.enable_controls()
            self.page.set_monitors(names, state.name)
            self.page.set_state(state, describe(state))
            if error:
                self.page.set_error(error)

        def apply_failure(self, message, epoch):
            """Reading failed: say why and grey the page out."""
            if epoch != self.epoch:
                return
            self.page.set_unavailable(message)

        def run_command(self, action, *args):
            """Send a change in the background, then read the monitor again."""
            if not self.monitor:
                return
            self.epoch += 1
            self.run_in_thread(self.execute, action, args, self.epoch)

        def execute(self, action, args, epoch):
            """Background part of run_command: runs off the GTK thread."""
            error = None
            try:
                action(*args)
            except Exception as e:
                error = str(e)
            self.schedule_in_gtk_thread(self.after_command, error, epoch)

        def after_command(self, error, epoch):
            """A change was sent: read the monitor to show what it really does."""
            if epoch == self.epoch:
                self.refresh(error)

        def on_enabled(self, enabled):
            """The HDR switch was flipped."""
            self.run_command(self.client.set_enabled, self.monitor, bool(enabled))

        def on_change(self, key, values):
            """A slider or dropdown settled on a value."""
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
            """The reset button: back to what the config file says."""
            self.run_command(self.client.reset, self.monitor)

        def on_monitor(self, name):
            """Another monitor was picked."""
            self.monitor = name
            self.epoch += 1
            self.refresh()

        def on_copy(self):
            """Copy the lines that give the config file the settings in use."""
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
            page always shows what the monitor really does. Changes last until
            jff reloads its config, the copy button gives the lines that make
            them permanent.
            """
            return self.code_explanation.__doc__

    return HdrPlugin
