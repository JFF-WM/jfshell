class TaskbarConfig:
    """Handles all plugin settings registration and central retrieval."""

    def __init__(self, plugin_instance):
        """Initializes the config handler with the plugin instance.

        Args:
            plugin_instance: The TaskbarPlugin instance.
        """
        self.plugin = plugin_instance
        self.h = self.plugin.get_plugin_setting_add_hint

    def register_settings(self):
        """Registers all settings with hints for the Control Center."""
        self.icon_size = self.h(["layout", "icon_size"], 42, "Icon size.")
        self.spacing = self.h(["layout", "spacing"], 5, "Spacing between items.")
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
        self.exclusive_zone = self.h(
            ["panel", "exclusive_zone"], True, "Reserve space on screen."
        )
        self.panel_position = self.h(
            ["panel", "position"], "bottom", "Placement on screen."
        )
        self.vertical_layout_width = self.h(
            ["panel", "vertical_layout_width"], 150, "Width when vertical."
        )
        self.layer_always_exclusive = self.h(
            ["panel", "layer_always_exclusive"], False, "Always stay exclusive."
        )
        self.hide_on_overview_drag = self.h(
            ["behavior", "hide_on_overview_drag"],
            True,
            "Hide the taskbar's panel (its layer) while a window is dragged from the overview.",
        )
        self.preview_enabled = self.h(
            ["preview", "enabled"], True, "Show a window preview when hovering a button."
        )
        self.preview_delay = self.h(
            ["preview", "delay_ms"], 400, "Hover time before the preview opens (ms)."
        )
        self.preview_fps = self.h(
            ["preview", "fps"],
            24,
            "Frames per second an open preview is redrawn at (0 = a still picture).",
        )
        self.preview_width = self.h(
            ["preview", "max_width"], 320, "Widest preview picture, in logical pixels."
        )
        self.preview_height = self.h(
            ["preview", "max_height"], 200, "Tallest preview picture, in logical pixels."
        )
        self.preview_max_windows = self.h(
            ["preview", "max_windows"],
            4,
            "Most windows of a group shown in one preview.",
        )
