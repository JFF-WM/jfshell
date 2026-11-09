def get_plugin_metadata(_):
    """
    Metadata for the Notify Server UI plugin.
    """
    about = """
            This module provides the user-facing graphical interface (UI)
            for the notification server, responsible for rendering ephemeral
            pop-up notifications on the desktop.
            """
    return {
        "id": "org.jfshell.plugin.notify_server_ui",
        "name": "Notify Server UI",
        "version": "1.0.1",
        "enabled": True,
        "deps": ["top_panel", "notify_client"],
        "description": about,
    }


def get_plugin_class():
    """
    Returns the UI plugin class.
    """
    import re
    from src.plugins.core._base import BasePlugin
    from ._utils import NotifyUtils
    from ._activate import activate_notification

    class UI(BasePlugin):
        """
        GTK4 LayerShell based UI for desktop notifications.
        """

        def __init__(self, panel_instance) -> None:
            """
            Initializes the UI component and the notification utilities.
            """
            super().__init__(panel_instance)
            self.panel = panel_instance
            self.notify_utils = NotifyUtils(self.panel)
            self.active_popups = {}
            self.server = None  # set by the NotificationDaemon that owns us

        def on_start(self):
            # Lifecycle initialization
            self.notify_reload_config()

        def notify_reload_config(self):
            """
            Reloads configuration settings for notifications and DND mode.
            """
            self.show_messages = self.get_root_setting(
                ["org.jfshell.plugin.notify_client", "show_messages"], True
            )
            self.timeout = self.get_root_setting(
                ["org.jfshell.plugin.notify_client", "timeout"], 10
            )

        def _extract_first_uri_from_text(self, text: str) -> str | None:
            url_regex = r"(?:https?|ftp|file)://\S+|www\.\S+"
            matches = re.findall(url_regex, text)
            if matches:
                uri = matches[0].rstrip(".,;")
                if uri.startswith("www.") and "://" not in uri:
                    uri = "http://" + uri
                return uri
            return None

        def _strip_html_tags(self, text: str) -> str:
            return re.sub(r"<[^>]*>", "", text)

        def _get_server(self):
            return self.server

        def dismiss_notification(self, notification_id, reason=2):
            """Close the toast. reason: 1 expired, 2 dismissed by the user."""
            window = self.active_popups.pop(notification_id, None)
            if window is None:
                return False  # already closed (e.g. timer fired after a click)
            window.destroy()

            server = self._get_server()
            if server:
                server.emit_closed(notification_id, reason)
            return False

        def _expire_notification(self, notification_id):
            return self.dismiss_notification(notification_id, reason=1)

        def on_notification_click(self, gesture, n_press, x, y, notification, *args):
            nid = notification.get("id", 0)
            # Right click just dismisses; left click opens the app/link.
            if gesture.get_current_button() == 1:
                server = self._get_server()
                try:
                    activate_notification(
                        self,
                        notification,
                        emit_action=server.emit_action if server else None,
                    )
                except Exception as e:
                    self.logger.error(f"Failed to open notification {nid}: {e}")
            self.dismiss_notification(nid)

        def on_action_clicked(self, action_id, notification, window):
            try:
                server = self._get_server()
                if server:
                    server.emit_action(notification.get("id", 0), action_id)
            except Exception as e:
                self.logger.error(f"Failed to invoke action {action_id}: {e}")
            self.dismiss_notification(notification.get("id", 0))

        def show_popup(self, notification):
            """
            Renders a transient notification window centered on the focused output.
            """
            self.notify_reload_config()
            if not self.show_messages:
                return

            nid = notification.get("id", 0)
            popup_width = self.get_plugin_setting(["popup_width"], 399)
            popup_height = self.get_plugin_setting(["popup_height"], 150)

            # --- Centering Logic ---
            focused_output = self.ipc.get_focused_output()
            # Output geometry is nested under "logic"
            output_w = focused_output.get("logic", {}).get("width", 1920)

            # Calculate the left margin required to center a window of 'popup_width'
            center_x_offset = (output_w - popup_width) // 2
            # FIXME: remove this hardcoded value at some point
            hardcoded_y_offset_plus = 8
            top_y_offset = (
                self.get_plugin_setting(["popup_position_y"], 32)
                + hardcoded_y_offset_plus
            )

            # A replacing notification (same id) supersedes the old toast
            old_window = self.active_popups.pop(nid, None)
            if old_window is not None:
                old_window.destroy()

            window = self.gtk.Window()
            window.add_css_class("notify-window")

            self.layer_shell.init_for_window(window)
            self.layer_shell.set_layer(window, self.layer_shell.Layer.TOP)

            # Anchor only to TOP and LEFT to use the calculated center offset as a margin
            self.layer_shell.set_anchor(window, self.layer_shell.Edge.TOP, True)
            self.layer_shell.set_anchor(window, self.layer_shell.Edge.LEFT, True)

            self.layer_shell.set_margin(window, self.layer_shell.Edge.TOP, top_y_offset)
            self.layer_shell.set_margin(
                window, self.layer_shell.Edge.LEFT, center_x_offset
            )

            # --- UI Construction ---
            vbox = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=10)
            vbox.set_margin_end(10)
            vbox.add_css_class("notification-box")

            click_gesture = self.gtk.GestureClick.new()
            click_gesture.set_button(0)
            click_gesture.connect("released", self.on_notification_click, notification)
            vbox.add_controller(click_gesture)

            icon = self.notify_utils.load_icon(notification)
            if icon:
                icon.set_pixel_size(48)
                vbox.append(icon)

            summary_label = self.gtk.Label(label=notification.get("summary", ""))
            summary_label.add_css_class("notification-summary")
            summary_label.set_wrap(True)
            vbox.append(summary_label)

            body_text = self._strip_html_tags(notification.get("body", ""))
            body_label = self.gtk.Label(label=body_text)
            body_label.add_css_class("notification-body")
            body_label.set_wrap(True)
            body_label.set_max_width_chars(100)
            body_label.set_lines(5)
            body_label.set_halign(self.gtk.Align.CENTER)
            vbox.append(body_label)

            # Actions
            actions = notification.get("actions", [])
            if actions:
                action_box = self.gtk.Box(
                    orientation=self.gtk.Orientation.HORIZONTAL, spacing=5
                )
                action_box.set_halign(self.gtk.Align.CENTER)
                for i in range(0, len(actions), 2):
                    if i + 1 < len(actions):
                        action_id, action_label = actions[i], actions[i + 1]
                        if action_id == "default":
                            continue
                        btn = self.gtk.Button(label=action_label)
                        btn.add_css_class("notification-action-button")
                        btn.connect(
                            "clicked",
                            lambda _, aid=action_id: self.on_action_clicked(
                                aid, notification, window
                            ),
                        )
                        action_box.append(btn)
                if action_box.get_first_child():
                    vbox.append(action_box)

            close_button = self.gtk.Button(label="Close")
            close_button.add_css_class("notify-close-button")
            close_button.connect("clicked", lambda _: self.dismiss_notification(nid))
            vbox.append(close_button)

            window.set_child(vbox)
            window.set_default_size(popup_width, popup_height)

            self.active_popups[nid] = window
            window.present()

            self.glib.timeout_add_seconds(self.timeout, self._expire_notification, nid)

    return UI
