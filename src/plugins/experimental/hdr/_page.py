"""The HDR page of the clock popover: widgets only, the plugin does the talking.

Everything GTK comes in through the constructor (``gtk`` and ``glib``), so the
module has no top-level imports of its own.
"""

SLIDER_DELAY_MS = 180

# key, label, min, max, step, unit ("" shows a plain number), digits
SLIDERS = (
    ("sdr_white", "SDR white", 10.0, 1000.0, 1.0, "nits", 0),
    ("peak", "Peak brightness", 100.0, 4000.0, 10.0, "nits", 0),
    ("contrast", "Contrast", 0.5, 2.0, 0.05, "", 2),
    ("black_level", "Black level", -5.0, 5.0, 0.1, "nits", 1),
    ("saturation", "Saturation", 0.0, 2.0, 0.05, "", 2),
    ("wb_r", "Red gain", 0.5, 1.5, 0.01, "", 2),
    ("wb_g", "Green gain", 0.5, 1.5, 0.01, "", 2),
    ("wb_b", "Blue gain", 0.5, 1.5, 0.01, "", 2),
)

SECTIONS = (
    ("Brightness", ("sdr_white", "peak", "contrast", "black_level")),
    ("Color", ("saturation", "wb_r", "wb_g", "wb_b")),
)

# Choices of the two dropdowns: config value and the text shown.
TONE_MAPS = (("knee", "Knee"), ("bt2390", "BT.2390"))
GAMUT_MAPS = (("compress", "Compress"), ("clip", "Clip"))


class HdrPage:
    """Widgets of the HDR page.

    Args:
        gtk: The Gtk module.
        glib: The GLib module.
        on_enabled: Called with a bool when the HDR switch is flipped.
        on_change: Called with ``(key, values)`` once a slider or a dropdown
            settles. ``key`` is ``sdr_white``, ``peak``, ``contrast``,
            ``black-level``, ``saturation``, ``white-balance``, ``tone-map`` or
            ``gamut-map``, ``values`` a tuple.
        on_reset: Called when the reset button is pressed.
        on_copy: Called when the copy button is pressed.
        on_monitor: Called with the monitor name when another one is picked.
        peak_max: Upper end of the peak slider in nits.
        max_height: Height the settings list grows to before it scrolls.
    """

    def __init__(
        self,
        gtk,
        glib,
        on_enabled,
        on_change,
        on_reset,
        on_copy,
        on_monitor,
        peak_max=4000,
        max_height=420,
    ):
        self.gtk = gtk
        self.glib = glib
        self.on_enabled = on_enabled
        self.on_change = on_change
        self.on_reset = on_reset
        self.on_copy = on_copy
        self.on_monitor = on_monitor
        self.updating = False
        self.rows = {}
        self.timers = {}
        self.pills = {}
        self.pill_names = ()
        self.pill_group = None
        self.dropdowns = {}
        self.build(peak_max, max_height)

    def build(self, peak_max, max_height):
        """Create the widget tree."""
        gtk = self.gtk
        self.root = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=10)
        self.root.add_css_class("hdr-page")
        self.root.set_margin_top(10)
        self.root.set_margin_bottom(10)
        self.root.set_margin_start(10)
        self.root.set_margin_end(10)
        self.root.set_size_request(340, -1)

        header = gtk.Box(orientation=gtk.Orientation.HORIZONTAL, spacing=6)
        title = gtk.Label(label="HDR")
        title.add_css_class("hdr-title")
        title.set_halign(gtk.Align.START)
        title.set_hexpand(True)
        header.append(title)
        header.append(self.header_button("edit-undo-symbolic", "Reset to config", self.on_reset))
        header.append(
            self.header_button("edit-copy-symbolic", "Copy settings as config", self.on_copy)
        )
        self.root.append(header)

        self.monitor_row = gtk.Box(orientation=gtk.Orientation.HORIZONTAL, spacing=6)
        self.monitor_row.add_css_class("hdr-monitors")
        self.monitor_row.set_visible(False)
        self.root.append(self.monitor_row)

        card = gtk.Box(orientation=gtk.Orientation.HORIZONTAL, spacing=10)
        card.add_css_class("hdr-card")
        texts = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=2)
        texts.set_hexpand(True)
        card_title = gtk.Label(label="High dynamic range")
        card_title.add_css_class("hdr-card-title")
        card_title.set_halign(gtk.Align.START)
        self.status = gtk.Label(label="Looking for monitors...")
        self.status.add_css_class("hdr-status")
        self.status.set_xalign(0)
        self.status.set_wrap(True)
        self.status.set_max_width_chars(34)
        texts.append(card_title)
        texts.append(self.status)
        card.append(texts)
        self.switch = gtk.Switch()
        self.switch.set_valign(gtk.Align.CENTER)
        self.switch.connect("notify::active", self.on_switch)
        card.append(self.switch)
        self.root.append(card)

        self.stack = gtk.Stack()
        self.stack.set_transition_type(gtk.StackTransitionType.CROSSFADE)
        self.stack.set_hexpand(True)
        self.body = self.stack
        for heading, keys in SECTIONS:
            page = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=10)
            for key in keys:
                page.append(self.slider_row(key, peak_max))
            self.stack.add_titled(page, heading.lower(), heading)

        mapping = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=10)
        mapping.append(self.choice_row("tone-map", "Tone mapping", TONE_MAPS))
        mapping.append(self.choice_row("gamut-map", "Gamut mapping", GAMUT_MAPS))
        self.info = gtk.Label(label="")
        self.info.add_css_class("hdr-info")
        self.info.set_xalign(0)
        self.info.set_wrap(True)
        self.info.set_max_width_chars(40)
        mapping.append(self.info)
        self.stack.add_titled(mapping, "mapping", "Mapping")

        switcher = gtk.StackSwitcher()
        switcher.set_stack(self.stack)
        switcher.set_halign(gtk.Align.CENTER)
        switcher.add_css_class("hdr-tabs")
        self.root.append(switcher)
        self.root.append(self.stack)

    def header_button(self, icon, tooltip, callback):
        """A square icon button of the header."""
        button = self.gtk.Button.new_from_icon_name(icon)
        button.add_css_class("hdr-header-button")
        button.set_tooltip_text(tooltip)
        button.connect("clicked", lambda _b: callback())
        return button

    def slider_row(self, key, peak_max):
        """A name and value line over a slider, for the setting with that key."""
        gtk = self.gtk
        spec = next(s for s in SLIDERS if s[0] == key)
        _key, name, low, high, step, unit, digits = spec
        if key == "peak":
            high = float(peak_max)
        box = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("hdr-row")
        top = gtk.Box(orientation=gtk.Orientation.HORIZONTAL)
        label = gtk.Label(label=name)
        label.add_css_class("hdr-row-label")
        label.set_halign(gtk.Align.START)
        label.set_hexpand(True)
        value = gtk.Label(label="")
        value.add_css_class("hdr-row-value")
        value.set_halign(gtk.Align.END)
        top.append(label)
        top.append(value)
        scale = gtk.Scale.new_with_range(gtk.Orientation.HORIZONTAL, low, high, step)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.add_css_class("hdr-scale")
        scale.connect("value-changed", self.on_scale, key)
        box.append(top)
        box.append(scale)
        self.rows[key] = {
            "scale": scale,
            "value": value,
            "unit": unit,
            "digits": digits,
            "low": low,
            "high": high,
        }
        return box

    def choice_row(self, key, name, choices):
        """A name and a dropdown for a setting with a few fixed values."""
        gtk = self.gtk
        box = gtk.Box(orientation=gtk.Orientation.HORIZONTAL, spacing=6)
        box.add_css_class("hdr-row")
        label = gtk.Label(label=name)
        label.add_css_class("hdr-row-label")
        label.set_halign(gtk.Align.START)
        label.set_hexpand(True)
        dropdown = gtk.DropDown.new_from_strings([text for _v, text in choices])
        dropdown.add_css_class("hdr-dropdown")
        dropdown.connect("notify::selected", self.on_dropdown, key)
        box.append(label)
        box.append(dropdown)
        self.dropdowns[key] = (dropdown, [value for value, _t in choices])
        return box

    def format_value(self, key, value):
        """Text of a slider value."""
        row = self.rows[key]
        text = f"{value:.{row['digits']}f}"
        return f"{text} {row['unit']}" if row["unit"] else text

    def on_switch(self, switch, _param):
        """The HDR switch was flipped by the user."""
        if not self.updating:
            self.on_enabled(switch.get_active())

    def on_scale(self, scale, key):
        """A slider moved: show the value now, send it once it settles."""
        row = self.rows[key]
        row["value"].set_label(self.format_value(key, scale.get_value()))
        if self.updating:
            return
        pending = self.timers.pop(key, None)
        if pending:
            self.glib.source_remove(pending)
        self.timers[key] = self.glib.timeout_add(SLIDER_DELAY_MS, self.fire, key)

    def fire(self, key):
        """Send a slider's value, the three gains go together."""
        self.timers.pop(key, None)
        if key in ("wb_r", "wb_g", "wb_b"):
            gains = tuple(self.rows[k]["scale"].get_value() for k in ("wb_r", "wb_g", "wb_b"))
            self.on_change("white-balance", gains)
        else:
            value = self.rows[key]["scale"].get_value()
            if self.rows[key]["digits"] == 0:
                value = int(round(value))
            self.on_change("black-level" if key == "black_level" else key, (value,))
        return False

    def on_dropdown(self, dropdown, _param, key):
        """A dropdown changed by the user."""
        if self.updating:
            return
        _widget, values = self.dropdowns[key]
        index = dropdown.get_selected()
        if 0 <= index < len(values):
            self.on_change(key, (values[index],))

    def cancel_timers(self):
        """Drop slider values that were not sent yet."""
        for pending in self.timers.values():
            self.glib.source_remove(pending)
        self.timers.clear()

    def set_monitors(self, names, selected):
        """Show one button per monitor, only when there is a choice to make."""
        names = tuple(names)
        if names != self.pill_names:
            child = self.monitor_row.get_first_child()
            while child is not None:
                following = child.get_next_sibling()
                self.monitor_row.remove(child)
                child = following
            self.pills = {}
            self.pill_group = None
            for name in names:
                button = self.gtk.ToggleButton(label=name)
                button.add_css_class("hdr-monitor-pill")
                if self.pill_group is None:
                    self.pill_group = button
                else:
                    button.set_group(self.pill_group)
                button.connect("toggled", self.on_pill, name)
                self.monitor_row.append(button)
                self.pills[name] = button
            self.pill_names = names
        self.monitor_row.set_visible(len(names) > 1)
        self.updating = True
        try:
            if selected in self.pills:
                self.pills[selected].set_active(True)
        finally:
            self.updating = False

    def on_pill(self, button, name):
        """A monitor button became active."""
        if button.get_active() and not self.updating:
            self.on_monitor(name)

    def set_state(self, state, text):
        """Show the state of the selected monitor, without sending anything."""
        self.updating = True
        try:
            self.switch.set_active(state.requested)
            self.status.remove_css_class("hdr-error")
            self.status.set_label(text)
            self.body.set_sensitive(state.requested)
            values = {
                "sdr_white": state.sdr_white,
                "peak": state.peak,
                "contrast": state.contrast,
                "black_level": state.black_level,
                "saturation": state.saturation,
                "wb_r": state.white_balance[0],
                "wb_g": state.white_balance[1],
                "wb_b": state.white_balance[2],
            }
            for key, value in values.items():
                row = self.rows[key]
                if value is None:
                    value = row["low"]
                value = min(max(float(value), row["low"]), row["high"])
                row["scale"].set_value(value)
                row["value"].set_label(self.format_value(key, value))
            for key, current in (("tone-map", state.tone_map), ("gamut-map", state.gamut_map)):
                dropdown, options = self.dropdowns[key]
                if current in options:
                    dropdown.set_selected(options.index(current))
            self.info.set_label(self.info_text(state))
        finally:
            self.updating = False

    def info_text(self, state):
        """Content light levels and direct scanout, when the compositor reports them."""
        lines = []
        if state.max_cll or state.max_fall:
            lines.append(f"Window light: MaxCLL {state.max_cll or '-'} · MaxFALL {state.max_fall or '-'} nits")
        if state.active:
            if state.scanout_active:
                lines.append("Direct scanout: on")
            elif state.scanout_reason:
                lines.append(f"Direct scanout: off, {state.scanout_reason}")
        return "\n".join(lines)

    def set_error(self, message):
        """Show a failure in the status line."""
        self.status.add_css_class("hdr-error")
        self.status.set_label(message)

    def set_note(self, message):
        """Show a plain message in the status line."""
        self.status.remove_css_class("hdr-error")
        self.status.set_label(message)

    def set_unavailable(self, message):
        """No monitor to control: show why and grey the page out."""
        self.updating = True
        try:
            self.switch.set_active(False)
        finally:
            self.updating = False
        self.switch.set_sensitive(False)
        self.body.set_sensitive(False)
        self.set_error(message)

    def enable_controls(self):
        """Undo set_unavailable."""
        self.switch.set_sensitive(True)
