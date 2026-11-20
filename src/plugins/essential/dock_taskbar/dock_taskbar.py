def get_plugin_metadata(panel_instance):
    """Retrieves metadata for the Dock Taskbar plugin."""
    plugin_id = "org.jfshell.plugin.dock_taskbar"
    position = str(
        panel_instance.config_handler.get_root_setting(
            [plugin_id, "panel", "position"], "bottom"
        )
    ).lower()
    containers = {
        "left": "left-panel-center",
        "right": "right-panel-center",
        "top": "top-panel-center",
        "bottom": "bottom-panel-center",
    }
    return {
        "id": plugin_id,
        "name": "Dock Taskbar",
        "version": "1.0.0",
        "enabled": True,
        "container": containers.get(position, "bottom-panel-center"),
        "deps": ["event_manager", "css_generator", "app_launcher"],
        "description": (
            "Dock and taskbar in one: pinned launchers plus running apps, always "
            "grouped per application, with window thumbnails on hover."
        ),
    }


def get_plugin_class():
    from src.plugins.core._base import BasePlugin
    from ._audio import DockTaskbarAudio
    from ._config import DockTaskbarConfig
    from ._events import DockTaskbarEvents
    from ._menu import DockTaskbarMenu
    from ._model import (
        APP,
        build_entries,
        command_name,
        key_variants,
        moved,
        strip_field_codes,
        view_app_id,
        with_pinned,
        without,
    )
    from ._preview import DockTaskbarPreview
    from ._ui import DockTaskbarUI

    # The launcher list of the old dockbar, used until this plugin saves its own.
    DOCKBAR_ID = "org.jfshell.plugin.dockbar"

    class DockTaskbarPlugin(BasePlugin):
        """One bar for pinned launchers and running windows.

        Every application is a single icon, whether it is pinned, running or
        both. Its windows are reached by hovering the icon (thumbnails), by
        clicking (focus, or cycle when the app is already focused) or by
        scrolling over it.
        """

        # Wait before a burst of compositor events is turned into one sync.
        SYNC_DELAY_MS = 60
        # Wait before a burst of PulseAudio events is turned into one refresh.
        AUDIO_DELAY_MS = 150

        def __init__(self, panel_instance):
            super().__init__(panel_instance)

        def on_start(self):
            # entry key -> Entry of the last sync (only apps, no separators)
            self.entry_map = {}
            # entry key -> widget (button or separator)
            self.widgets = {}
            # Keys of the unpinned groups, so they keep their place.
            self.order = []
            # entry key -> id of the window focused last in that group
            self.last_focused = {}
            self._app_infos = {}
            self._sync_id = 0
            self._audio_id = 0
            self._config_monitor = None
            self._last_config_mtime = 0.0

            self.config = DockTaskbarConfig(self)
            self.config.register_settings()
            self.position = self.config.panel_position
            self.vertical = self.position in ("left", "right")
            self.audio = DockTaskbarAudio()

            self.ui = DockTaskbarUI(self)
            self.menu = DockTaskbarMenu(self)
            self.preview = DockTaskbarPreview(self)
            self.events = DockTaskbarEvents(self)

            self.ui.create_layout()
            self.ui.attach_launcher()
            self.sync()
            self.main_widget = (self.dock, "append")
            self.events.subscribe()
            if self.config.show_audio_indicator:
                self.audio.start_watch(lambda: self.glib.idle_add(self._audio_changed))
            self.watch_config()
            self.plugins["css_generator"].install_css("dock_taskbar.css")
            self._setup_exclusive_zone()

        def _setup_exclusive_zone(self):
            if not self.config.layer_always_exclusive:
                return
            window = self.panel_for_container(self.get_plugin_metadata()["container"])
            if window:
                self.layer_shell.set_layer(window, self.layer_shell.Layer.TOP)
                self.layer_shell.auto_exclusive_zone_enable(window)

        # --- pinned launchers (config) ---

        def load_pinned(self):
            """The pinned launchers: ``[{"id", "cmd", "icon", "app_id"?}, ...]``.

            Same shape as the dockbar plugin keeps, so its list is picked up
            until this plugin saves one of its own.
            """
            pid = str(self.plugin_id)
            items = self.config_handler.get_root_setting([pid, "app"], None)
            if items is None:
                items = self.config_handler.get_root_setting([DOCKBAR_ID, "app"], [])
            if isinstance(items, dict):
                items = [
                    {**data, "id": data.get("id", name)}
                    for name, data in items.items()
                    if isinstance(data, dict)
                ]
            return [i for i in (items or []) if isinstance(i, dict)]

        def save_pinned(self, items):
            """Stores the launcher list and redraws the bar."""
            self.config_handler.set_root_setting([str(self.plugin_id), "app"], items)
            self.sync()

        def pin(self, entry):
            """Adds a running app to the launchers."""
            cmd = self.command_for(entry)
            if not cmd:
                return
            info = self.app_info(entry.app_id)
            item = {
                "id": info["name"] or entry.app_id,
                "cmd": cmd,
                "icon": self.icon_for(entry),
                "app_id": entry.app_id,
            }
            self.save_pinned(with_pinned(self.load_pinned(), item))

        def unpin(self, entry):
            if entry.pinned:
                self.save_pinned(without(self.load_pinned(), entry.index))

        def move_pinned(self, entry, step):
            if entry.pinned:
                self.save_pinned(moved(self.load_pinned(), entry.index, step))

        def watch_config(self):
            """Redraws the bar when the config file is edited by hand."""
            try:
                config_file = self.config_handler.config_file
                gfile = self.gio.File.new_for_path(str(config_file))
                self._config_monitor = gfile.monitor_file(
                    self.gio.FileMonitorFlags.NONE, None
                )
                self._config_monitor.connect("changed", self._on_config_file_changed)
                self._last_config_mtime = self.os.path.getmtime(config_file)
            except Exception as e:
                self.logger.error(f"Dock taskbar config watcher failed: {e}")

        def _on_config_file_changed(self, monitor, file, other_file, event_type):
            Event = self.gio.FileMonitorEvent
            if event_type not in (
                Event.CHANGES_DONE_HINT,
                Event.MOVED,
                Event.CHANGED,
            ):
                return
            try:
                mtime = self.os.path.getmtime(self.config_handler.config_file)
            except OSError:
                return
            if mtime > self._last_config_mtime + 0.1:
                self._last_config_mtime = mtime
                self.glib.idle_add(self._reload_pinned)

        def _reload_pinned(self):
            self.config_handler.reload_config()
            self.sync()
            return False

        # --- syncing ---

        def is_valid_view(self, view: dict) -> bool:
            """Whether a window dict from the IPC belongs on the bar."""
            return bool(
                view
                and view.get("id") is not None
                and (view.get("app_id") or view.get("app-id")) not in ("nil", "", None)
            )

        def schedule_sync(self):
            """Syncs soon; a burst of events makes one sync."""
            if not self._sync_id:
                self._sync_id = self.glib.timeout_add(
                    self.SYNC_DELAY_MS, self._sync_timeout
                )

        def _sync_timeout(self):
            self._sync_id = 0
            self.sync()
            return False

        def schedule_audio(self):
            """Refreshes the speaker badges soon; a burst of events makes one."""
            if not self._audio_id:
                self._audio_id = self.glib.timeout_add(
                    self.AUDIO_DELAY_MS, self._audio_timeout
                )

        def _audio_changed(self):
            """Runs on the GTK loop when PulseAudio reported a change."""
            self.schedule_audio()
            return False

        def _audio_timeout(self):
            self._audio_id = 0
            self.refresh_audio()
            return False

        def refresh_audio(self):
            """Marks the icons of the apps that play sound (or play it muted)."""
            flags = {}
            if self.config.show_audio_indicator:
                flags = self.audio.flags(
                    {
                        key: [v.get("pid") for v in entry.views]
                        for key, entry in self.entry_map.items()
                    }
                )
            for key, widget in self.widgets.items():
                if key in self.entry_map:
                    self.ui.set_audio(widget, *flags.get(key, (False, False)))

        def sync(self):
            """Rebuilds the entries from the windows and the launchers, and redraws."""
            if self._sync_id:
                self.glib.source_remove(self._sync_id)
                self._sync_id = 0
            try:
                views = [v for v in self.ipc.list_windows() if self.is_valid_view(v)]
            except Exception as e:
                self.logger.error(f"Dock taskbar could not list windows: {e}")
                return
            extra = {}
            for view in views:
                app_id = view_app_id(view)
                if app_id not in extra:
                    extra[app_id] = self.app_info(app_id)["keys"]
            entries, self.order = build_entries(
                self.load_pinned(), views, self.order, self.config.show_unpinned, extra
            )
            self.entry_map = {e.key: e for e in entries if e.kind == APP}
            for entry in self.entry_map.values():
                focused = next((v for v in entry.views if v.get("is_focused")), None)
                if focused is not None:
                    self.last_focused[entry.key] = focused.get("id")
            self.ui.apply(entries)
            self.refresh_audio()
            open_key = self.preview.key
            if open_key in self.entry_map:
                self.preview.refresh_if_changed(
                    open_key, self.entry_map[open_key].views
                )

        # --- facts about an entry ---

        def app_info(self, app_id):
            """Desktop-entry facts for an app id: extra match keys, name and command."""
            cached = self._app_infos.get(app_id)
            if cached is not None:
                return cached
            info = {"keys": set(), "name": "", "cmd": ""}
            desktop = None
            if app_id:
                try:
                    for candidate in dict.fromkeys((app_id, app_id.lower())):
                        desktop = self.gio.DesktopAppInfo.new(f"{candidate}.desktop")
                        if desktop:
                            break
                except Exception as e:
                    self.logger.debug(f"Desktop entry lookup failed for {app_id}: {e}")
                    desktop = None
            if desktop:
                commandline = desktop.get_commandline() or ""
                info["name"] = desktop.get_display_name() or ""
                info["cmd"] = strip_field_codes(commandline)
                info["keys"] = key_variants(command_name(commandline)) | key_variants(
                    desktop.get_startup_wm_class()
                )
            self._app_infos[app_id] = info
            return info

        def command_for(self, entry):
            """The command that starts a new instance of the entry's app, or ''."""
            if entry.pinned:
                cmd = str(entry.item.get("cmd") or "").strip()
                if cmd:
                    return cmd
            return self.app_info(entry.app_id)["cmd"]

        def icon_for(self, entry):
            """Icon name (or absolute file path) of an entry."""
            if entry.pinned and entry.item.get("icon"):
                return str(entry.item["icon"])
            if entry.app_id:
                return self.gtk_helper.icon_exist(entry.app_id)
            return "application-x-executable"

        def name_for(self, entry):
            """Name shown in the tooltip (the bar itself shows no titles)."""
            if entry.pinned and entry.item.get("id"):
                return str(entry.item["id"])
            return self.app_info(entry.app_id)["name"] or entry.app_id

        # --- actions ---

        def activate(self, key):
            """Click on an icon: launch, focus the group, or cycle through it."""
            entry = self.entry_map.get(key)
            if entry is None:
                return
            if not entry.running:
                self.launch(entry)
            elif len(entry.views) > 1 and entry.focused and self.config.cycle_on_click:
                self.cycle(key, 1)
            else:
                self.focus_view(self._last_focused_view(entry))

        def _last_focused_view(self, entry):
            views = entry.views
            focused = next((v for v in views if v.get("is_focused")), None)
            if focused is not None:
                return focused
            last = self.last_focused.get(entry.key)
            return next((v for v in views if v.get("id") == last), views[0])

        def cycle(self, key, direction):
            """Focuses the next (1) or previous (-1) window of a group.

            Returns True when there was a group of several windows to cycle.
            """
            entry = self.entry_map.get(key)
            if entry is None or len(entry.views) < 2:
                return False
            views = entry.views
            current = next(
                (i for i, v in enumerate(views) if v.get("is_focused")), None
            )
            if current is None:
                target = views[0] if direction > 0 else views[-1]
            else:
                target = views[(current + direction) % len(views)]
            self.focus_view(target)
            return True

        def launch(self, entry):
            """Starts a new instance of the entry's app."""
            cmd = self.command_for(entry)
            if cmd:
                self.cmd.run(cmd)

        def focus_view(self, view):
            """Focuses one window through the jff IPC."""
            vid = view.get("id")
            if vid is None:
                return
            try:
                self.ipc.close_overview()
                self.ipc.set_focus(vid)
            except Exception as e:
                self.logger.error(f"Focus failed for ID {vid}: {e}")

        def close_view(self, view):
            """Asks one window to close (the app may still ask to save)."""
            vid = view.get("id")
            if vid is None:
                return
            try:
                self.ipc.close_window(vid)
            except Exception as e:
                self.logger.error(f"Close failed for ID {vid}: {e}")

        def close_all(self, entry):
            for view in list(entry.views):
                self.close_view(view)

        # --- lifecycle ---

        def on_disable(self):
            if self._sync_id:
                self.glib.source_remove(self._sync_id)
                self._sync_id = 0
            if self._audio_id:
                self.glib.source_remove(self._audio_id)
                self._audio_id = 0
            self.audio.stop_watch()
            self.audio.close()
            self.ui.remove_badge_css()
            if self._config_monitor:
                self._config_monitor.cancel()
                self._config_monitor = None
            self.events.unsubscribe()
            self.preview.destroy()
            self.menu.destroy()

    return DockTaskbarPlugin
