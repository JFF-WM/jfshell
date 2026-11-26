"""Settings of the dock taskbar."""


def _as_int(value, default, minimum=0):
    """An int setting, tolerating strings and junk from a hand-edited config."""
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


class DockTaskbarConfig:
    """Registers every setting (with hints for the Control Center) and keeps the values."""

    POSITIONS = ("bottom", "top")

    def __init__(self, plugin_instance):
        """Initializes the config handler.

        Args:
            plugin_instance: The DockTaskbarPlugin instance.
        """
        self.plugin = plugin_instance
        self.h = plugin_instance.get_plugin_setting_add_hint

    def register_settings(self):
        """Reads all settings, adding the missing ones with their hint."""
        h = self.h
        self.panel_position = str(
            h(
                ["panel", "position"],
                "bottom",
                "Which panel the bar sits on (bottom, top).",
            )
        ).lower()
        if self.panel_position not in self.POSITIONS:
            self.panel_position = "bottom"
        self.layer_always_exclusive = bool(
            h(
                ["panel", "layer_always_exclusive"],
                False,
                "If True, the panel is set to Layer.TOP and reserves space.",
            )
        )
        self.all_monitors = bool(
            h(
                ["panel", "all_monitors"],
                True,
                "Show the dock on every monitor, not only on the one of the panel "
                "(bottom panel only).",
            )
        )
        self.icon_size = _as_int(
            h(["layout", "icon_size"], 40, "Size of the application icons."), 40, 8
        )
        self.spacing = _as_int(
            h(["layout", "spacing"], 6, "Spacing in pixels between the icons."), 6
        )
        self.show_indicators = bool(
            h(
                ["layout", "show_indicators"],
                True,
                "Show one dot per open window next to the icon.",
            )
        )
        self.show_audio_indicator = bool(
            h(
                ["layout", "show_audio_indicator"],
                True,
                "Show a small speaker badge on the icon of an application that is "
                "playing sound.",
            )
        )
        self.show_state_badges = bool(
            h(
                ["layout", "show_state_badges"],
                True,
                "Show small badges on the icon of an application that has a window "
                "that is always on top and/or sticky (visible on all workspaces).",
            )
        )
        self.blur_to_dock = bool(
            h(
                ["layout", "blur_to_dock"],
                True,
                "Make only the dock take input in its panel window, so the compositor "
                "blurs just the dock (not the whole window). Round its corners with "
                "`layer-radius` in the blur block of the jff config.",
            )
        )
        self.max_indicators = _as_int(
            h(
                ["layout", "max_indicators"],
                4,
                "Most dots drawn for one application.",
            ),
            4,
            1,
        )
        self.show_unpinned = bool(
            h(
                ["layout", "show_running_apps"],
                True,
                "Also show running applications that are not pinned.",
            )
        )
        self.cycle_on_click = bool(
            h(
                ["behavior", "cycle_on_click"],
                True,
                "Clicking the icon of the focused application with several windows "
                "focuses the next window.",
            )
        )
        self.hide_on_overview_drag = bool(
            h(
                ["behavior", "hide_on_overview_drag"],
                True,
                "Hide the panel (its layer) while a window is dragged from the overview.",
            )
        )
        self.preview_enabled = bool(
            h(
                ["preview", "enabled"],
                True,
                "Show the window thumbnails when hovering an icon.",
            )
        )
        self.preview_delay = _as_int(
            h(
                ["preview", "delay_ms"],
                300,
                "Hover time before the thumbnails open (ms).",
            ),
            300,
        )
        self.preview_fps = _as_int(
            h(
                ["preview", "fps"],
                24,
                "Frames per second open thumbnails are redrawn at (0 = still pictures).",
            ),
            24,
        )
        self.preview_width = _as_int(
            h(
                ["preview", "max_width"],
                320,
                "Widest thumbnail, in logical pixels.",
            ),
            320,
            32,
        )
        self.preview_height = _as_int(
            h(
                ["preview", "max_height"],
                200,
                "Tallest thumbnail, in logical pixels.",
            ),
            200,
            32,
        )
        self.preview_max_windows = _as_int(
            h(
                ["preview", "max_windows"],
                6,
                "Most windows of one application shown in the thumbnails.",
            ),
            6,
            1,
        )
