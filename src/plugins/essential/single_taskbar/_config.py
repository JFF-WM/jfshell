class SingleTaskbarConfig:
    """Registers single taskbar settings and derives every size from the single panel height."""

    def __init__(self, plugin_instance):
        """Stores the plugin and its hinted-setting helper."""
        self.plugin = plugin_instance
        self.h = self.plugin.get_plugin_setting_add_hint

    def register_settings(self):
        """Registers all settings with hints for the Control Center."""
        self.configured_icon_size = self._as_int(
            self.h(
                ["layout", "icon_size"],
                0,
                "Icon size in pixels. 0 fits the panel height; larger values are clamped to it.",
            ),
            0,
        )
        self.spacing = self.h(["layout", "spacing"], 4, "Spacing between items.")
        self.show_label = self.h(
            ["layout", "show_label"], True, "Globally show labels."
        )
        self.max_title_length = self.h(
            ["layout", "max_title_length"], 33, "Title character limit."
        )
        self.group_apps = self.h(
            ["layout", "group_apps"], True, "Group windows of same app."
        )
        self.hide_ungrouped_titles = self.h(
            ["layout", "hide_ungrouped_titles"],
            True,
            "Hide title if app is not grouped.",
        )
        self.show_focused_group_title = self.h(
            ["layout", "show_focused_group_title"],
            True,
            "Show focused window title in group.",
        )
        self.show_group_count = self.h(
            ["layout", "show_group_count"],
            True,
            "Show number of windows in group label.",
        )
        self.alignment = str(
            self.h(
                ["layout", "alignment"],
                "center",
                "Where the buttons sit in the free space: start, center or end.",
            )
        ).lower()
        self.hide_on_overview_drag = self.h(
            ["behavior", "hide_on_overview_drag"],
            True,
            "Hide the single panel while a window is dragged from the overview.",
        )
        self._compute_sizes()

    @staticmethod
    def _as_int(value, fallback):
        """Converts a setting to int, falling back on bad values."""
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return fallback

    def _compute_sizes(self):
        """Takes button height and icon size from the shared single panel metrics."""
        from src.core.panel_mode import single_panel_metrics

        m = single_panel_metrics(
            self.plugin._config_handler, self.configured_icon_size
        )
        self.panel_height = m["panel_height"]
        self.button_height = m["button_height"]
        self.icon_size = m["icon_size"]
        self.BUTTON_MARGIN = m["button_margin"]
