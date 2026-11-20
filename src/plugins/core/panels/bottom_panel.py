def get_plugin_metadata(_):
    return {
        "id": "org.jfshell.plugin.bottom_panel",
        "name": "Bottom Panel",
        "version": "1.0.0",
        "enabled": True,
        "priority": 1,
        "container": "bottom-panel",
        "deps": ["event_manager", "css_generator"],
    }


def get_plugin_class():
    from src.plugins.core._base import BasePlugin

    class BottomPanelPlugin(BasePlugin):
        def __init__(self, panel_instance):
            """
            Initialize the BottomPanelPlugin with identical structure to Top Panel.
            """
            super().__init__(panel_instance)
            self._deferred_widgets_attached = False
            self.is_bottom_panel_ready = False

        def on_start(self):
            self._setup_panel_boxes()
            self.add_css_class()
            self.plugins["css_generator"].install_css("bottom-panel.css")
            # Plugins fill the side boxes while they load, so look again a bit later.
            self.glib.timeout_add(1000, self._sync_side_boxes)
            self.glib.timeout_add(4000, self._sync_side_boxes)

        def _has_content(self, widget, placeholders):
            """True when ``widget`` holds something besides the empty placeholder boxes."""
            child = widget.get_first_child()
            while child is not None:
                if child not in placeholders or self._has_content(child, placeholders):
                    return True
                child = child.get_next_sibling()
            return False

        def _sync_side_boxes(self):
            """Hide the left and right boxes while nothing is in them.

            With a panel width of -1 the window is as wide as its content, so an
            empty side box (its spacer, padding and margin) only widens the window,
            and the blur behind it, beyond the dock.
            """
            placeholders = {
                self.spacer,
                self.obj.bottom_panel_box_widgets_left,
                self.obj.bottom_panel_box_systray,
                self.obj.bottom_panel_box_for_buttons,
                self.obj.bottom_panel_box_right,
            }
            self.obj.bottom_panel_box_left.set_visible(
                self._has_content(self.obj.bottom_panel_box_left, placeholders)
            )
            self.obj.bottom_panel_grid_right.set_visible(
                self._has_content(self.obj.bottom_panel_grid_right, placeholders)
            )
            return False

        def _attach_widget_to_grid_next_to(
            self, grid, widget_to_attach, relative_widget, position_type, width, height
        ):
            if widget_to_attach.get_parent() is None:
                grid.attach_next_to(
                    widget_to_attach,
                    relative_widget,
                    position_type,
                    width,
                    height,
                )

        def _setup_panel_boxes(self):
            # Left Box Setup (Symmetrical to Top)
            self.obj.bottom_panel_box_widgets_left = self.gtk.Box.new(
                self.gtk.Orientation.HORIZONTAL, 6
            )
            self.obj.bottom_panel_box_left = self.gtk.Box.new(
                self.gtk.Orientation.HORIZONTAL, 0
            )
            self.obj.bottom_panel_box_widgets_left.set_homogeneous(True)
            self.obj.bottom_panel_box_left.append(
                self.obj.bottom_panel_box_widgets_left
            )

            # Right Box & Systray Setup (Symmetrical to Top)
            self.obj.bottom_panel_box_systray = self.gtk.Box()
            self.obj.bottom_panel_box_for_buttons = self.gtk.Box()
            self.obj.bottom_panel_box_right = self.gtk.Box()

            self.spacer = self.gtk.Separator(
                orientation=self.gtk.Orientation.HORIZONTAL
            )
            self.spacer.set_hexpand(True)
            self.spacer.add_css_class("bottom-right-box-spacer")
            self.obj.bottom_panel_box_right.append(self.spacer)

            self.obj.bottom_panel_grid_right = self.gtk.Grid()
            self.obj.bottom_panel_grid_right.attach(
                self.obj.bottom_panel_box_right, 1, 0, 1, 2
            )

            self._attach_widget_to_grid_next_to(
                self.obj.bottom_panel_grid_right,
                self.obj.bottom_panel_box_systray,
                self.obj.bottom_panel_box_right,
                self.gtk.PositionType.RIGHT,
                1,
                2,
            )
            self._attach_widget_to_grid_next_to(
                self.obj.bottom_panel_grid_right,
                self.obj.bottom_panel_box_for_buttons,
                self.obj.bottom_panel_box_systray,
                self.gtk.PositionType.RIGHT,
                1,
                2,
            )

            # Center Box Setup (Symmetrical to Top)
            self.obj.bottom_panel_box_center = self.gtk.Box()
            self.obj.bottom_panel_box_center.set_halign(self.gtk.Align.CENTER)
            self.obj.bottom_panel_box_center.set_valign(self.gtk.Align.CENTER)
            self.obj.bottom_panel_box_center.set_hexpand(False)

            # Main Grid Assembly (Symmetrical to Top)
            self.obj.bottom_panel_box_full = self.gtk.Grid()

            # Disable column homogeneity to allow children (like Taskbar)
            # to keep their natural width instead of forcing 33%/33%/33% split.
            self.obj.bottom_panel_box_full.set_column_homogeneous(False)

            # Left side: Fixed width or takes minimal space
            self.obj.bottom_panel_box_left.set_hexpand(False)
            self.obj.bottom_panel_box_full.attach(
                self.obj.bottom_panel_box_left, 0, 0, 1, 1
            )

            # Center side: Takes all remaining space to keep Taskbar centered
            self.obj.bottom_panel_box_center.set_hexpand(True)
            self.obj.bottom_panel_box_full.attach_next_to(
                self.obj.bottom_panel_box_center,
                self.obj.bottom_panel_box_left,
                self.gtk.PositionType.RIGHT,
                1,
                1,
            )

            # Right side: Fixed width or takes minimal space
            self.obj.bottom_panel_grid_right.set_hexpand(False)
            self.obj.bottom_panel_box_full.attach_next_to(
                self.obj.bottom_panel_grid_right,
                self.obj.bottom_panel_box_center,
                self.gtk.PositionType.RIGHT,
                1,
                1,
            )

            # Scrolled Window Wrapper (Symmetrical to Top)
            self.obj.bottom_panel_scrolled_window = self.gtk.ScrolledWindow()
            self.obj.bottom_panel_scrolled_window.set_policy(
                self.gtk.PolicyType.AUTOMATIC, self.gtk.PolicyType.NEVER
            )
            # The window is as wide as its content when the panel width is -1.
            self.obj.bottom_panel_scrolled_window.set_propagate_natural_width(True)
            self.obj.bottom_panel_scrolled_window.set_child(
                self.obj.bottom_panel_box_full
            )
            self.obj.bottom_panel_scrolled_window.set_focus_on_click(False)

            self.main_widget = (self.obj.bottom_panel_scrolled_window, "set_child")
            self.is_bottom_panel_ready = True

        def add_css_class(self):
            widgets = [
                self.obj.bottom_panel_box_left,
                self.obj.bottom_panel_box_widgets_left,
                self.obj.bottom_panel_box_right,
                self.obj.bottom_panel_box_systray,
                self.obj.bottom_panel_box_center,
                self.obj.bottom_panel_box_full,
                self.obj.bottom_panel_scrolled_window,
            ]

            if all(self.gtk_helper.is_widget_ready(w) for w in widgets):
                self.obj.bottom_panel_box_left.add_css_class("bottom-panel-box-left")
                self.obj.bottom_panel_box_widgets_left.add_css_class(
                    "bottom-panel-box-widgets-left"
                )
                self.obj.bottom_panel_box_right.add_css_class("bottom-panel-box-right")
                self.obj.bottom_panel_box_systray.add_css_class(
                    "bottom-panel-box-systray"
                )
                self.obj.bottom_panel_box_center.add_css_class(
                    "bottom-panel-box-center"
                )
                self.obj.bottom_panel_box_full.add_css_class("bottom-panel-box-full")
                self.obj.bottom_panel_scrolled_window.add_css_class(
                    "bottom-panel-scrolled-window"
                )
                return False
            else:
                self.glib.idle_add(self.add_css_class)
                return True

    return BottomPanelPlugin
