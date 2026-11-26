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
    from gi.repository import Gdk

    from src.core.create_panel import CreatePanel
    from src.plugins.core._base import BasePlugin
    from ._outputs import plan_mirrors

    class BottomPanelPlugin(BasePlugin):
        # Wait before a burst of monitor changes is turned into one update.
        SYNC_DELAY_MS = 500

        def __init__(self, panel_instance):
            """
            Initialize the BottomPanelPlugin.
            """
            super().__init__(panel_instance)
            self._deferred_widgets_attached = False
            self.is_bottom_panel_ready = False
            # output name -> (window, box) of the copies of the panel that sit
            # on the monitors other than the one of the main window.
            self._mirrors = {}
            self._mirror_listeners = []
            self._mirroring = False
            self._monitors_model = None
            self._monitors_handler = 0
            self._sync_id = 0

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
            box = self._new_box()
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

        def _new_box(self):
            """One centered box, as wide as what is put in it."""
            box = self.gtk.Box.new(self.gtk.Orientation.HORIZONTAL, 0)
            box.set_halign(self.gtk.Align.CENTER)
            box.set_valign(self.gtk.Align.CENTER)
            box.set_hexpand(False)
            box.set_focus_on_click(False)
            return box

        # --- one panel window per monitor ---

        def mirrors(self):
            """The copies of the panel on the other monitors: ``{output: (window, box)}``."""
            return dict(self._mirrors)

        def add_mirror_listener(self, callback):
            """Calls ``callback(kind, output, box)`` when a copy comes or goes.

            ``kind`` is ``"added"`` (the box is ready for widgets) or
            ``"removed"`` (called before the window is destroyed).
            """
            if callback not in self._mirror_listeners:
                self._mirror_listeners.append(callback)

        def remove_mirror_listener(self, callback):
            if callback in self._mirror_listeners:
                self._mirror_listeners.remove(callback)

        def set_mirroring(self, enabled):
            """Puts a copy of the bottom panel on every monitor, or removes them.

            The main window stays where it is; each other connected monitor gets
            a window of its own with the same box (``bottom-panel-box-full``), so
            a plugin can fill it with its own widgets (see ``add_mirror_listener``).
            The copies follow monitors that are plugged in and out.
            """
            self._mirroring = bool(enabled)
            if self._mirroring:
                self._follow_monitors()
                self._sync_mirrors()
            else:
                self._unfollow_monitors()
                for output in list(self._mirrors):
                    self._remove_mirror(output)

        def _follow_monitors(self):
            display = Gdk.Display.get_default()
            if display is None or self._monitors_handler:
                return
            self._monitors_model = display.get_monitors()
            self._monitors_handler = self._monitors_model.connect(
                "items-changed", self._schedule_sync
            )

        def _unfollow_monitors(self):
            if self._sync_id:
                self.glib.source_remove(self._sync_id)
                self._sync_id = 0
            if self._monitors_model is not None and self._monitors_handler:
                self._monitors_model.disconnect(self._monitors_handler)
            self._monitors_model = None
            self._monitors_handler = 0

        def _schedule_sync(self, *_):
            """Syncs soon; a burst of monitor changes makes one sync."""
            if self._mirroring and not self._sync_id:
                self._sync_id = self.glib.timeout_add(
                    self.SYNC_DELAY_MS, self._sync_mirrors
                )

        def _connected_outputs(self):
            display = Gdk.Display.get_default()
            if display is None:
                return []
            return [m.get_connector() for m in display.get_monitors()]

        def _primary_output(self):
            """Connector of the monitor the main bottom panel window is on."""
            window = getattr(self.obj, "bottom_panel", None)
            if window is None:
                return None
            try:
                monitor = self.layer_shell.get_monitor(window)
            except Exception:
                return None
            return monitor.get_connector() if monitor else None

        def _sync_mirrors(self):
            self._sync_id = 0
            if not self._mirroring:
                return False
            to_add, to_remove = plan_mirrors(
                self._connected_outputs(), self._primary_output(), self._mirrors
            )
            for output in to_remove:
                self._remove_mirror(output)
            for output in to_add:
                self._add_mirror(output)
            return False

        def _add_mirror(self, output):
            path = ["org.jfshell.panel", "bottom"]
            get = self.config_handler.get_root_setting
            if not get(path + ["enabled"], True):
                return
            try:
                window = CreatePanel(
                    self._panel_instance,
                    "BOTTOM",
                    "TOP",
                    bool(get(path + ["Exclusive"], True)),
                    -1,
                    int(get(path + ["height"], 32.0)),
                    "bottom-panel",
                    "JFShell-Bottom",
                    monitor_name=output,
                )
            except Exception as e:
                self.logger.error(f"Bottom panel copy for {output} failed: {e}")
                return
            box = self._new_box()
            box.add_css_class("bottom-panel-box-full")
            window.set_child(box)
            self._mirrors[output] = (window, box)
            window.present()
            self._notify("added", output, box)

        def _remove_mirror(self, output):
            entry = self._mirrors.pop(output, None)
            if entry is None:
                return
            window, box = entry
            self._notify("removed", output, box)
            try:
                window.destroy()
            except Exception as e:
                self.logger.error(f"Bottom panel copy for {output} not closed: {e}")

        def _notify(self, kind, output, box):
            for callback in list(self._mirror_listeners):
                try:
                    callback(kind, output, box)
                except Exception as e:
                    self.logger.error(f"Bottom panel listener failed: {e}")

        def on_disable(self):
            self.set_mirroring(False)
            self._mirror_listeners.clear()

        def add_css_class(self):
            box = self.obj.bottom_panel_box_full

            if self.gtk_helper.is_widget_ready(box):
                box.add_css_class("bottom-panel-box-full")
                return False
            else:
                self.glib.idle_add(self.add_css_class)
                return True

    return BottomPanelPlugin
