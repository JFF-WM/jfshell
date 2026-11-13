class TaskbarViews:
    """Handles view logic, grouping, focusing, and focus state management for jff."""

    def __init__(self, plugin_instance):
        self.plugin = plugin_instance

    def handle_focus_change(self, view: dict):
        """Updates internal state and UI classes when a view gains focus."""
        if not view:
            return

        # jff normalization: check id and app_id (fallback to app-id for shim compatibility)
        fid = view.get("id")
        aid = view.get("app_id") or view.get("app-id")

        if fid is None or aid is None:
            return

        self.plugin.group_last_focused[aid] = fid

        # Update visual focused classes on buttons
        for ident, btn in self.plugin.in_use_buttons.items():
            is_focused = (self.plugin.group_apps and ident == aid) or (
                not self.plugin.group_apps and ident == fid
            )

            if is_focused:
                btn.add_css_class("focused")
            else:
                btn.remove_css_class("focused")

        # Debounce taskbar sync to update labels/counts
        if not self.plugin._debounce_pending:
            from gi.repository import GLib

            self.plugin._debounce_pending = True
            self.plugin._debounce_timer_id = GLib.timeout_add(
                self.plugin._debounce_interval, self.plugin.Taskbar
            )

    def restore_group_focus(self, identifier):
        """Logic for middle-click restoration of last known view in a group."""
        # Use jff specific list_windows logic
        views = [v for v in self.plugin.ipc.list_views() if self.is_valid_view(v)]
        target_views = [
            v
            for v in views
            if (
                (v.get("app_id") or v.get("app-id")) == identifier
                if self.plugin.group_apps
                else v.get("id") == identifier
            )
        ]

        if not target_views:
            return

        last_id = self.plugin.group_last_focused.get(identifier)
        target = next(
            (v for v in target_views if v.get("id") == last_id), target_views[0]
        )
        self.set_view_focus(target)

    def set_view_focus(self, view: dict) -> None:
        """Focuses a specific view through the jff IPC."""
        vid = None
        try:
            vid = view.get("id")
            if vid is None:
                return

            self.plugin.ipc.close_overview()
            self.plugin.ipc.set_focus(vid)

        except Exception as e:
            self.plugin.logger.error(f"Focus failed for ID {vid}: {e}")

    def is_valid_view(self, v: dict) -> bool:
        """Validate a view dict from the IPC."""
        # id, app_id and title are the primary valid markers
        return bool(
            v
            and v.get("id") is not None
            and (v.get("app_id") or v.get("app-id")) not in ("nil", None)
            # jff windows are mapped by default if in the list
        )

    def cycle_group_focus(self, identifier, direction):
        """Cycles focus among views in a jff group."""
        views = [v for v in self.plugin.ipc.list_views() if self.is_valid_view(v)]
        target_views = [
            v
            for v in views
            if (
                (v.get("app_id") or v.get("app-id")) == identifier
                if self.plugin.group_apps
                else v.get("id") == identifier
            )
        ]

        if len(target_views) <= 1:
            return

        current_focus_id = self.plugin.group_last_focused.get(identifier)

        try:
            current_idx = next(
                i for i, v in enumerate(target_views) if v.get("id") == current_focus_id
            )
            next_idx = (current_idx + direction) % len(target_views)
            self.set_view_focus(target_views[next_idx])
        except (StopIteration, ValueError):
            self.set_view_focus(target_views[0])
