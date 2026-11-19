class SingleTaskbarUI:
    """Builds the single taskbar widgets, sized to fit the single panel."""

    def __init__(self, plugin_instance):
        """Stores the plugin and its config."""
        self.plugin = plugin_instance
        self.config = plugin_instance.config

    def create_main_layout(self):
        """Builds a horizontal button row inside a scroller that never grows the panel."""
        from gi.repository import Gtk

        align = {
            "start": Gtk.Align.START,
            "end": Gtk.Align.END,
        }.get(self.config.alignment, Gtk.Align.CENTER)

        self.plugin.taskbar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=self.config.spacing
        )
        self.plugin.taskbar.set_halign(align)
        self.plugin.taskbar.set_valign(Gtk.Align.CENTER)
        self.plugin.taskbar.add_css_class("single-taskbar")

        scrolled = Gtk.ScrolledWindow()
        # Overflow scrolls sideways without a scrollbar; height is never negotiable.
        scrolled.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        scrolled.set_propagate_natural_width(False)
        scrolled.set_propagate_natural_height(False)
        scrolled.set_min_content_width(0)
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_valign(Gtk.Align.FILL)
        scrolled.set_child(self.plugin.taskbar)
        scrolled.add_css_class("single-taskbar-container")
        self.plugin.scrolled_window = scrolled

        self.plugin.Taskbar()

    def create_button(self):
        """Creates a button whose height and icon come from the panel-derived sizes."""
        from gi.repository import Gtk, Pango

        cfg = self.config
        button = Gtk.Button()
        button.set_halign(Gtk.Align.CENTER)
        button.set_valign(Gtk.Align.CENTER)
        button.set_hexpand(False)
        button.set_vexpand(False)
        button.set_size_request(-1, cfg.button_height)
        button.set_margin_top(cfg.BUTTON_MARGIN)
        button.set_margin_bottom(cfg.BUTTON_MARGIN)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=cfg.spacing)
        button.icon = Gtk.Image()  # pyright: ignore
        button.label = Gtk.Label()  # pyright: ignore
        button.icon.set_pixel_size(cfg.icon_size)  # pyright: ignore
        button.label.set_ellipsize(Pango.EllipsizeMode.END)  # pyright: ignore
        button.label.set_max_width_chars(max(int(cfg.max_title_length), 1))  # pyright: ignore
        button.label.set_single_line_mode(True)  # pyright: ignore

        # State badges, shown by update_button: always on top, sticky and muted.
        button.badge_atop = Gtk.Image.new_from_icon_name("go-top-symbolic")  # pyright: ignore
        button.badge_sticky = Gtk.Image.new_from_icon_name("pin-symbolic")  # pyright: ignore
        button.badge_muted = Gtk.Image.new_from_icon_name("audio-volume-muted-symbolic")  # pyright: ignore
        button.badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)  # pyright: ignore
        button.badges.set_valign(Gtk.Align.CENTER)  # pyright: ignore
        button.badges.set_visible(False)  # pyright: ignore
        for badge, css in (
            (button.badge_atop, "taskbar-badge-atop"),  # pyright: ignore
            (button.badge_sticky, "taskbar-badge-sticky"),  # pyright: ignore
            (button.badge_muted, "taskbar-badge-muted"),  # pyright: ignore
        ):
            badge.set_pixel_size(12)
            badge.add_css_class("taskbar-badge")
            badge.add_css_class(css)
            badge.set_visible(False)
            button.badges.append(badge)  # pyright: ignore

        box.append(button.icon)  # pyright: ignore
        box.append(button.badges)  # pyright: ignore
        if cfg.show_label:
            box.append(button.label)  # pyright: ignore

        button.set_child(box)
        button.add_css_class("single-taskbar-button")
        return button
