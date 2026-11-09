def get_plugin_metadata(_):
    about = """

        A plugin that cycles through jff workspaces with active windows.

        Includes state-locking to prevent race conditions during rapid clicking.

        """

    return {
        "id": "org.jfshell.plugin.next_workspace_with_views",
        "name": "Next Workspace",
        "version": "1.1.0",
        "enabled": True,
        "description": about,
    }


def get_plugin_class():
    from src.plugins.core._base import BasePlugin

    class GoNextWorkspaceWithViewsPlugin(BasePlugin):
        def __init__(self, panel_instance):
            super().__init__(panel_instance)

            self.gestures_setup_plugin = None

            self._is_switching = False  # Lock to prevent race conditions

        def on_start(self):
            # Check for gestures dependency

            self.glib.timeout_add_seconds(1, self.check_for_gestures_setup)

        def check_for_gestures_setup(self):
            if "gestures_setup" in self.obj.plugin_loader.plugins:
                self.gestures_setup_plugin = self.obj.plugin_loader.plugins[
                    "gestures_setup"
                ]

                self.append_right_click_action()

                return False

            return True

        def append_right_click_action(self):
            # Register the action to the panel's right-click gesture

            self.gestures_setup_plugin.append_action(  # pyright: ignore
                callback_name="pos_full_right_click",
                action=self.go_next_workspace_with_views,
            )

        def get_workspaces_with_views(self):
            """

            Queries jff for workspaces on the active monitor that aren't empty.

            """

            workspaces = self.ipc.list_workspaces()

            windows = self.ipc.list_views()

            # Find the monitor currently holding focus

            focused_output = next(
                (ws["output"] for ws in workspaces if ws["is_focused"]), None
            )

            # Identify which workspace IDs actually have windows

            ws_ids_with_windows = {
                win["workspace_id"] for win in windows if win.get("workspace_id")
            }

            # Filter for active monitor and (populated OR currently focused)

            valid_ws = [
                ws
                for ws in workspaces
                if ws["output"] == focused_output
                and (ws["id"] in ws_ids_with_windows or ws["is_focused"])
            ]

            # Sort by jff index (1, 2, 3...)

            return sorted(valid_ws, key=lambda x: x["idx"])

        def go_next_workspace_with_views(self):
            """

            Main logic for cycling. Includes a 100ms lockout for rapid clicks.

            """

            if self._is_switching:
                return

            self._is_switching = True

            def release_lock():
                self._is_switching = False

                return False

            try:
                populated_workspaces = self.get_workspaces_with_views()

                if len(populated_workspaces) <= 1:
                    self._is_switching = False

                    return

                # Determine current position in the populated list

                current_pos = next(
                    (
                        i
                        for i, ws in enumerate(populated_workspaces)
                        if ws["is_focused"]
                    ),
                    None,
                )

                if current_pos is None:
                    self._is_switching = False

                    return

                # Calculate target (loops to start if at end)

                next_pos = (current_pos + 1) % len(populated_workspaces)

                target_ws = populated_workspaces[next_pos]

                self.logger.info(
                    f"Cycling to Workspace {target_ws['idx']} on {target_ws['output']}"
                )

                # Use IPC to command jff to switch

                self.ipc.set_workspace(target_ws["idx"])

                # Wait 150ms for jff's internal state to catch up before allowing next click

                self.glib.timeout_add(100, release_lock)

            except Exception as e:
                self.logger.error(f"Workspace cycle failure: {e}")

                self._is_switching = False

    return GoNextWorkspaceWithViewsPlugin
