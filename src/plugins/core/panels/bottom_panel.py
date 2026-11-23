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
            Initialize the BottomPanelPlugin.
            """
            super().__init__(panel_instance)
            self._deferred_widgets_attached = False
            self.is_bottom_panel_ready = False

        def on_start(self):
            self._setup_panel_boxes()
            self.add_css_class()
            self.plugins["css_generator"].install_css("bottom-panel.css")

        def _setup_panel_boxes(self):
            """Build the bottom panel out of one box, ``bottom_panel_box_full``.

            The top panel splits its window in left, center and right boxes. The
            bottom panel does not: a box inside another box paints its own square
            corners over the rounded one, so a rounded dock was never possible. Here
            the window holds a single box, centered, that is as wide as what the
            plugins put in it, and that one box can be rounded.

            There is no ScrolledWindow wrapper either: the box is handed to the
            window directly, so nothing clips or scrolls it.

            Every other name a plugin may ask for (``bottom-panel-left``,
            ``bottom-panel-center``, ``bottom-panel-right`` and the rest) is the
            same box, so plugins keep working; they are placed in the order they
            load instead of in a side.
            """
            box = self.gtk.Box.new(self.gtk.Orientation.HORIZONTAL, 0)
            box.set_halign(self.gtk.Align.CENTER)
            box.set_valign(self.gtk.Align.CENTER)
            box.set_hexpand(False)
            box.set_focus_on_click(False)
            self.obj.bottom_panel_box_full = box

            for name in (
                "left",
                "widgets_left",
                "center",
                "right",
                "systray",
                "for_buttons",
            ):
                setattr(self.obj, f"bottom_panel_box_{name}", box)

            self.main_widget = (box, "set_child")
            self.is_bottom_panel_ready = True

        def add_css_class(self):
            box = self.obj.bottom_panel_box_full

            if self.gtk_helper.is_widget_ready(box):
                box.add_css_class("bottom-panel-box-full")
                return False
            else:
                self.glib.idle_add(self.add_css_class)
                return True

    return BottomPanelPlugin
