def get_plugin_metadata(_):
    return {
        "id": "org.jfshell.plugin.advanced_volume",
        "name": "Advanced Volume Control",
        "version": "3.0.0",
        "deps": ["css_generator", "calendar"],
        "enabled": True,
        "description": (
            "Professional audio manager. Adds an Audio page to the clock popover. "
            "Enable the wayaudio_icon plugin for a systray icon that opens it."
        ),
    }


def get_plugin_class():
    from src.plugins.core._base import BasePlugin
    from gi.repository import GLib, Gtk
    import pulsectl
    import soundcard as sc
    from functools import partial

    PAGE_ID = "wayaudio"

    class AdvancedVolumePlugin(BasePlugin):
        def on_start(self):
            """Initializes the PulseAudio controller and registers settings hints."""
            self.pulse = pulsectl.Pulse("jfshell-volume-mgr")
            self.sc = sc
            self.calendar = None
            self.sw = None
            self.show_ignored = False

            if "css_generator" in self.plugins:
                self.plugins["css_generator"].install_css("wayaudio.css")
                self.plugins["css_generator"].install_css("wayaudio-cards.css")

            self.max_name_lenght = self.get_plugin_setting_add_hint(
                ["name_lenght"], 35, "Maximum character length for device names."
            )
            self.soundcard_blacklist = self.get_plugin_setting_add_hint(
                ["soundcard", "blacklist"], ["Navi"], "Keywords to hide output devices."
            )
            self.mic_blacklist = self.get_plugin_setting_add_hint(
                ["microphone", "blacklist"], ["Navi"], "Keywords to hide input devices."
            )
            self.ignored_streams = self.get_plugin_setting_add_hint(
                ["ignored_streams"], [], "List of application names currently hidden."
            )

            self.init_page()

        def init_page(self) -> None:
            """Build the audio page and add it to the clock popover's sidebar."""
            if "calendar" not in self.plugins:
                self.logger.error(
                    "Calendar plugin is not loaded. Cannot add the audio page."
                )
                return
            self.calendar = self.plugins["calendar"]
            self.sw = Gtk.ScrolledWindow()
            self.sw.add_css_class("volume-popover-root")
            self.sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.sw.set_propagate_natural_height(True)
            self.sw.set_max_content_height(600)
            self.sw.set_min_content_width(380)
            # Force the scrollbar to occupy its own space (Non-overlay)
            self.sw.set_overlay_scrolling(False)
            self.calendar.add_page(
                PAGE_ID,
                ["audio-volume-high-symbolic"],
                "Audio",
                self.sw,
                on_show=self.update_ui,
            )

        def _get_filtered_devices(self, is_mic=False):
            """Filters devices based on the user-defined blacklist."""
            devices = self.sc.all_microphones() if is_mic else self.sc.all_speakers()
            blacklist = self.mic_blacklist if is_mic else self.soundcard_blacklist
            return [d for d in devices if not any(b in d.name for b in blacklist)]

        def update_ui(self):
            """Rebuilds the UI structure and synchronizes current system devices."""
            root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            root_box.add_css_class("volume-content-wrapper")
            root_box.set_margin_start(12)
            root_box.set_margin_end(12)
            root_box.set_margin_top(12)
            root_box.set_margin_bottom(12)

            s_info = self.pulse.server_info()
            curr_sink_name = s_info.default_sink_name  # pyright: ignore
            curr_source_name = s_info.default_source_name  # pyright: ignore

            # --- OUTPUT / INPUT CARDS ---
            cards = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=12, homogeneous=True
            )
            cards.append(
                self._build_device_card(
                    "Output",
                    self._get_filtered_devices(is_mic=False),
                    self._lookup(self.pulse.get_sink_by_name, curr_sink_name),
                    curr_sink_name,
                    is_mic=False,
                )
            )
            cards.append(
                self._build_device_card(
                    "Input",
                    self._get_filtered_devices(is_mic=True),
                    self._lookup(self.pulse.get_source_by_name, curr_source_name),
                    curr_source_name,
                    is_mic=True,
                )
            )
            root_box.append(cards)

            # --- APPLICATIONS ---
            root_box.append(self._build_apps_card(curr_sink_name))
            if self.sw:
                self.sw.set_child(root_box)

        def _lookup(self, getter, name):
            """Returns ``getter(name)`` or None when PulseAudio has no such object."""
            try:
                return getter(name)
            except pulsectl.PulseError:
                return None

        def _refresh_later(self) -> None:
            """Rebuild the page shortly after an action, once the popover has closed
            and asynchronous pactl commands had a moment to apply."""
            GLib.timeout_add(200, self._refresh_once)

        def _refresh_once(self) -> bool:
            try:
                self.update_ui()
            except Exception as e:
                self.logger.error(f"Audio page refresh failed: {e}")
            return GLib.SOURCE_REMOVE

        def _build_choice_button(
            self, icon_name: str, choices: list, css_classes: list[str]
        ) -> Gtk.MenuButton:
            """Icon button that opens a popover list.

            ``choices`` holds ``(label, checked, callback)`` tuples; ``None`` adds a
            separator.
            """
            btn = Gtk.MenuButton(css_classes=css_classes)
            btn.set_icon_name(icon_name)
            btn.set_valign(Gtk.Align.CENTER)

            popover = Gtk.Popover()
            popover.add_css_class("volume-device-popover")
            popover.set_has_arrow(False)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for choice in choices:
                if choice is None:
                    box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
                    continue
                label, checked, callback = choice
                item = Gtk.Button(css_classes=["volume-device-item"])
                item_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                check = Gtk.Image(icon_name="object-select-symbolic", pixel_size=14)
                check.add_css_class("volume-device-item-check")
                check.set_opacity(1.0 if checked else 0.0)
                item_row.append(check)
                item_row.append(Gtk.Label(label=label, xalign=0, hexpand=True))
                item.set_child(item_row)
                item.connect("clicked", self._on_choice_clicked, popover, callback)
                box.append(item)
            popover.set_child(box)
            btn.set_popover(popover)
            return btn

        def _on_choice_clicked(self, _btn, popover, callback) -> None:
            popover.popdown()
            callback()

        def _build_device_card(
            self, title: str, devices: list, audio_obj, current_name: str, is_mic: bool
        ) -> Gtk.Box:
            """Card with the active device, a menu to switch it and a volume row."""
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.add_css_class("volume-device-card")
            card.set_hexpand(True)

            active_idx = next(
                (i for i, d in enumerate(devices) if d.id in current_name), None
            )
            if active_idx is not None:
                active_name = devices[active_idx].name
            elif audio_obj is not None:
                active_name = audio_obj.description
            else:
                active_name = ""

            default_cmd = (
                "pactl set-default-source" if is_mic else "pactl set-default-sink"
            )
            choices = [
                (
                    d.name[: self.max_name_lenght],
                    i == active_idx,
                    partial(self._set_default_device, default_cmd, d.id),
                )
                for i, d in enumerate(devices)
            ]

            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            labels.set_hexpand(True)
            labels.append(
                Gtk.Label(label=title, xalign=0, css_classes=["volume-device-title"])
            )
            subtitle = Gtk.Label(
                label=active_name[: self.max_name_lenght],
                xalign=0,
                css_classes=["volume-device-subtitle"],
            )
            subtitle.set_ellipsize(3)  # pyright: ignore
            labels.append(subtitle)
            head.append(labels)
            if choices:
                head.append(
                    self._build_choice_button(
                        "view-more-symbolic", choices, ["volume-card-menu-btn"]
                    )
                )
            card.append(head)

            if audio_obj is not None:
                scale, percent_lbl, mute_btn = self.create_volume_controls(
                    [audio_obj], is_stream=False, is_mic=is_mic, type_class="master"
                )
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                row.add_css_class("volume-row-master")
                row.append(scale)
                row.append(percent_lbl)
                row.append(mute_btn)
                card.append(row)
            return card

        def _set_default_device(self, cmd: str, device_id: str) -> None:
            self.run_cmd(f"{cmd} {device_id}")
            self._refresh_later()

        def _build_apps_card(self, curr_sink_name: str) -> Gtk.Box:
            """Card listing every playback stream as one compact row."""
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            card.add_css_class("volume-apps-card")

            apps_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            albl = Gtk.Label(
                label="Application Volume",
                xalign=0,
                hexpand=True,
                css_classes=["volume-header-text"],
            )
            ignore_toggle = Gtk.Button(css_classes=["volume-ignore-toggle"])
            ignore_toggle.set_icon_name(
                "view-conceal-symbolic"
                if not self.show_ignored
                else "view-visible-symbolic"
            )
            ignore_toggle.connect("clicked", self._on_toggle_ignored)
            apps_header.append(albl)
            apps_header.append(ignore_toggle)
            card.append(apps_header)

            sinks = self.pulse.sink_list()
            default_sink = self._lookup(self.pulse.get_sink_by_name, curr_sink_name)
            default_idx = default_sink.index if default_sink is not None else None

            # One row per application: an app can own several streams (e.g. Firefox)
            # and they are controlled together. Active streams come first so the
            # row shows what is actually playing.
            grouped_apps: dict[str, list] = {}
            for stream in self.pulse.sink_input_list():
                app_name = stream.proplist.get("application.name", "Unknown")
                grouped_apps.setdefault(app_name, []).append(stream)

            apps_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            shown = 0
            for app_name, streams in grouped_apps.items():
                is_ignored = app_name in self.ignored_streams
                if is_ignored and not self.show_ignored:
                    continue
                streams.sort(key=lambda st: bool(getattr(st, "corked", 0)))
                apps_container.append(
                    self._build_app_row(
                        streams, app_name, is_ignored, sinks, default_idx
                    )
                )
                shown += 1

            if shown == 0:
                apps_container.append(
                    Gtk.Label(
                        label="No applications playing audio",
                        css_classes=["volume-stream-title"],
                    )
                )
            card.append(apps_container)
            return card

        def _build_app_row(
            self, streams: list, app_name: str, is_ignored: bool, sinks: list, default_idx
        ) -> Gtk.Box:
            """One application: icon, name + media title, slider, output picker, mute."""
            stream = streams[0]
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.add_css_class("volume-app-entry")
            row.add_css_class("volume-app-row")
            if is_ignored:
                row.add_css_class("ignored")

            icon_lookup = app_name.lower().replace(" ", "-")
            app_icon = Gtk.Image(pixel_size=32)
            app_icon.set_from_icon_name(self.icon_exist(icon_lookup))
            app_icon.set_valign(Gtk.Align.CENTER)

            media_title = (
                stream.proplist.get("media.title")
                or stream.proplist.get("media.name")
                or "Playback Stream"
            )
            scale, percent_lbl, mute_btn = self.create_volume_controls(
                streams, is_stream=True, is_mic=False, type_class="app"
            )

            name_lbl = Gtk.Label(
                label=app_name, xalign=0, css_classes=["volume-app-name-label"]
            )
            title_lbl = Gtk.Label(
                label=media_title,
                xalign=0,
                hexpand=True,
                css_classes=["volume-stream-title"],
            )
            title_lbl.set_ellipsize(3)  # pyright: ignore
            top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            top.append(name_lbl)
            top.append(title_lbl)
            top.append(percent_lbl)

            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            body.set_hexpand(True)
            body.append(top)
            body.append(scale)

            route_btn = self.create_sink_selector(
                streams, sinks, default_idx, app_name, is_ignored
            )
            mute_btn.set_valign(Gtk.Align.CENTER)

            row.append(app_icon)
            row.append(body)
            row.append(route_btn)
            row.append(mute_btn)
            return row

        def _on_toggle_ignored(self, _):
            self.show_ignored = not self.show_ignored
            self.update_ui()

        def _toggle_ignored_app(self, app_name: str) -> None:
            if app_name in self.ignored_streams:
                self.ignored_streams.remove(app_name)
            else:
                self.ignored_streams.append(app_name)
            self.set_plugin_setting(["ignored_streams"], self.ignored_streams)
            self._refresh_later()

        def create_volume_controls(
            self, audio_objs: list, is_stream: bool, is_mic: bool, type_class: str = "app"
        ):
            """Returns ``(scale, percent_label, mute_button)`` bound to ``audio_objs``.

            Every object in the list is changed together; the first one provides the
            initial state.
            """
            audio_obj = audio_objs[0]
            icon_on, icon_off = (
                ("audio-input-microphone-symbolic", "microphone-disabled-symbolic")
                if is_mic
                else ("audio-volume-high-symbolic", "audio-volume-muted-symbolic")
            )
            muted = [all(bool(o.mute) for o in audio_objs)]

            mute_btn = Gtk.Button(css_classes=[f"volume-mute-button-{type_class}"])
            mute_btn.set_icon_name(icon_off if muted[0] else icon_on)
            mute_btn.set_valign(Gtk.Align.CENTER)

            def on_mute_clicked(btn):
                muted[0] = not muted[0]
                for obj in audio_objs:
                    self.pulse.mute(obj, muted[0])
                btn.set_icon_name(icon_off if muted[0] else icon_on)

            mute_btn.connect("clicked", on_mute_clicked)

            vol_percent = int(audio_obj.volume.value_flat * 100)
            percent_lbl = Gtk.Label(
                label=f"{vol_percent}%", css_classes=["volume-percentage-label"]
            )
            percent_lbl.set_size_request(40, -1)

            adj = Gtk.Adjustment(
                value=vol_percent, lower=0, upper=150, step_increment=1
            )
            scale = Gtk.Scale(
                orientation=Gtk.Orientation.HORIZONTAL,
                adjustment=adj,
                hexpand=True,
                draw_value=False,
            )
            scale.add_css_class(f"volume-scale-{type_class}")

            def on_val_changed(s):
                val_raw = s.get_adjustment().get_value()
                val_norm = val_raw / 100.0
                percent_lbl.set_label(f"{int(val_raw)}%")
                for obj in audio_objs:
                    if is_stream:
                        self.pulse.sink_input_volume_set(
                            obj.index,
                            pulsectl.PulseVolumeInfo(val_norm, len(obj.volume.values)),
                        )
                    else:
                        self.pulse.volume_set_all_chans(obj, val_norm)

            scale.connect("value-changed", on_val_changed)
            return scale, percent_lbl, mute_btn

        def create_sink_selector(
            self, streams: list, sinks: list, default_idx, app_name: str, is_ignored: bool
        ) -> Gtk.MenuButton:
            """Headphones button whose popover lists the output devices for an app."""
            stream_indexes = [st.index for st in streams]
            on_default = default_idx is not None and all(
                st.sink == default_idx for st in streams
            )
            choices = []
            if default_idx is not None:
                choices.append(
                    (
                        "Default device",
                        on_default,
                        partial(self._move_streams, stream_indexes, default_idx),
                    )
                )
                choices.append(None)
            for s in sinks:
                choices.append(
                    (
                        s.description,
                        all(st.sink == s.index for st in streams) and not on_default,
                        partial(self._move_streams, stream_indexes, s.index),
                    )
                )
            choices.append(None)
            choices.append(
                (
                    "Show application" if is_ignored else "Hide application",
                    False,
                    partial(self._toggle_ignored_app, app_name),
                )
            )
            return self._build_choice_button(
                "audio-headphones-symbolic", choices, ["volume-app-route-btn"]
            )

        def _move_streams(self, stream_indexes: list[int], sink_index: int) -> None:
            for index in stream_indexes:
                self.pulse.sink_input_move(index, sink_index)
            self._refresh_later()

        def on_disable(self):
            if self.calendar:
                self.calendar.remove_page(PAGE_ID)
            if self.pulse:
                self.pulse.close()

    return AdvancedVolumePlugin