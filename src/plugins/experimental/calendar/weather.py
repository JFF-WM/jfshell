def get_plugin_metadata(_):
    about = """
    This plugin adds a weather page to the calendar popover,
    fetching weather data asynchronously and updating the
    display periodically. The location, units and refresh
    interval can be changed from the page itself or in the config.
    """
    return {
        "id": "org.jfshell.plugin.weather",
        "name": "Weather",
        "version": "2.0.0",
        "enabled": True,
        "priority": 99,
        "deps": ["calendar", "css_generator"],
        "description": about,
    }


def get_plugin_class():
    """
    The main plugin class entry point. ALL imports are deferred here
    to comply with the JFShell loading architecture.
    Returns:
        type: The WeatherPlugin class.
    """
    from datetime import datetime, timedelta, timezone
    from typing import Any, Dict, List, Optional
    from src.plugins.core._base import BasePlugin
    from ._weather_model import (
        DEFAULT_LATITUDE,
        DEFAULT_LOCATION_NAME,
        DEFAULT_LONGITUDE,
        DEFAULT_REFRESH_MINUTES,
        DEFAULT_USER_AGENT,
        MAX_HOURS_SHOWN,
        MAX_REFRESH_MINUTES,
        MIN_HOURS_SHOWN,
        MIN_REFRESH_MINUTES,
        PRECIPITATION_UNITS,
        TEMPERATURE_UNITS,
        WIND_UNITS,
        Forecast,
        Place,
        WeatherSettings,
        build_settings,
        describe_rain_soon,
        forecast_url,
        format_clock,
        format_percent,
        format_precipitation,
        format_pressure,
        format_temperature,
        format_uv,
        format_wind,
        geocode_url,
        legacy_coordinates,
        parse_altitude,
        parse_http_date,
        parse_latitude,
        parse_longitude,
        parse_places,
        summarize_forecast,
        symbol_emoji,
        symbol_label,
    )

    CALENDAR_ID = "org.jfshell.plugin.calendar"
    STATS = (
        ("wind", "Wind"),
        ("humidity", "Humidity"),
        ("pressure", "Pressure"),
        ("clouds", "Cloud cover"),
        ("uv", "UV index"),
        ("rain", "Rain (24h)"),
    )

    class WeatherPlugin(BasePlugin):
        """
        Plugin to fetch and display a weather summary, shown as its own page
        of the calendar popover: current conditions, an hourly strip and a
        few detail tiles. A second view lets the user pick the location
        (city search or coordinates), units and refresh interval.
        """

        PAGE_ID = "weather"

        def __init__(self, panel_instance):
            """
            Initialize the plugin state. Widgets are built later, once the
            calendar popover exists.
            Args:
                panel_instance: The main panel instance provided by the framework.
            """
            super().__init__(panel_instance)
            self.weather_settings = WeatherSettings()
            self.forecast: Optional[Forecast] = None
            self.calendar: Any = None
            self.update_task = None
            self.places: List[Place] = []
            self._fetching = False
            self._pending_force = False
            self._fetched_at: Optional[datetime] = None
            self._cache: Optional[Dict[str, Any]] = None
            self._cache_url: Optional[str] = None
            self._last_modified: Optional[str] = None
            self._expires: Optional[datetime] = None
            self.page_root: Any = None
            self.view_stack: Any = None

        def delay_on_start(self):
            self.weather_settings = self._read_settings()
            self.logger.info(
                f"Weather location: {self.weather_settings.place_label} "
                f"({self.weather_settings.latitude}, {self.weather_settings.longitude})"
            )
            self.plugins["css_generator"].install_css("weather_ui.css")
            self.run_in_async_task(self.setup_weather_async())
            return False

        def on_start(self):
            """
            Starts the asynchronous weather setup after the calendar is ready.
            """

            # This plugin doesn't need to start immediately.
            self.glib.timeout_add_seconds(3, self.delay_on_start)

        # ------------------------------------------------------------------ #
        # Settings
        # ------------------------------------------------------------------ #

        def _read_settings(self) -> WeatherSettings:
            """Read the settings from the config, registering hints and defaults.

            Coordinates saved by older versions under the calendar plugin are
            used as the default location, so existing setups keep working.
            """
            legacy = legacy_coordinates(
                self.get_root_setting([CALENDAR_ID, "weather", "coordinates"], None)
            )
            latitude, longitude = legacy or (DEFAULT_LATITUDE, DEFAULT_LONGITUDE)
            name = "" if legacy else DEFAULT_LOCATION_NAME
            setting = self.get_plugin_setting_add_hint
            raw = {
                "location_name": setting(
                    ["location", "name"],
                    name,
                    "Name shown in the weather header. Filled in by the city search on the weather page.",
                ),
                "latitude": setting(
                    ["location", "latitude"],
                    latitude,
                    "Latitude in decimal degrees, -90 to 90. Example: -23.5505",
                ),
                "longitude": setting(
                    ["location", "longitude"],
                    longitude,
                    "Longitude in decimal degrees, -180 to 180. Example: -46.6333",
                ),
                "altitude": setting(
                    ["location", "altitude"],
                    "",
                    "Optional altitude in metres. Improves the temperature in hilly terrain. Leave empty to let MET Norway use its own terrain model.",
                ),
                "temperature_unit": setting(
                    ["units", "temperature"], "celsius", "celsius or fahrenheit"
                ),
                "wind_unit": setting(
                    ["units", "wind"], "m/s", "Wind speed unit: m/s, km/h, mph or kn"
                ),
                "precipitation_unit": setting(
                    ["units", "precipitation"], "mm", "Rain amount unit: mm or in"
                ),
                "refresh_minutes": setting(
                    ["refresh_minutes"],
                    DEFAULT_REFRESH_MINUTES,
                    f"Minutes between forecast updates ({MIN_REFRESH_MINUTES} to {MAX_REFRESH_MINUTES}). MET Norway asks clients not to poll more often.",
                ),
                "hours_shown": setting(
                    ["hours_shown"],
                    12,
                    f"Number of hourly slots in the forecast strip ({MIN_HOURS_SHOWN} to {MAX_HOURS_SHOWN}).",
                ),
                "clock_24h": setting(
                    ["clock_24h"], True, "Show times in 24-hour format."
                ),
                "user_agent": setting(
                    ["user_agent"],
                    DEFAULT_USER_AGENT,
                    "Sent to MET Norway, whose terms require a User-Agent that identifies the app and a contact (URL or email). Put your own here if you fork the plugin.",
                ),
            }
            return build_settings(raw)

        # ------------------------------------------------------------------ #
        # Page construction (runs once, in the GTK thread)
        # ------------------------------------------------------------------ #

        async def setup_weather_async(self):
            """Asynchronously set up the weather functionality."""
            await self.asyncio.sleep(0)
            if "calendar" not in self.plugins:
                self.logger.error(
                    "Calendar plugin is not loaded. Cannot initialize weather."
                )
                return
            calendar_plugin = self.plugins["calendar"]
            if (
                not hasattr(calendar_plugin, "popover_calendar")
                or not calendar_plugin.popover_calendar
            ):
                self.logger.error("Calendar popover not found. Cannot attach weather.")
                return
            await self.attach_weather_to_calendar_async(calendar_plugin)

        async def attach_weather_to_calendar_async(self, calendar_plugin):
            """
            Attach the weather page to the calendar popover, then start the
            first fetch and the periodic refresh.
            """
            self.schedule_in_gtk_thread(self._build_page, calendar_plugin)
            self.run_in_async_task(self.fetch_and_update_weather_async())
            self.update_task = self.global_loop.create_task(
                self.periodic_weather_update()
            )

        def _label(self, text: str = "", css: Optional[str] = None, xalign: float = 0.0):
            label = self.gtk.Label(label=text)
            label.set_xalign(xalign)
            if css:
                label.add_css_class(css)
            return label

        def _icon_button(self, icon: str, tooltip: str, handler):
            button = self.gtk.Button.new_from_icon_name(icon)
            button.add_css_class("wx-icon-button")
            button.set_tooltip_text(tooltip)
            button.connect("clicked", handler)
            self.gtk_helper.add_cursor_effect(button)
            return button

        def _clear_children(self, container) -> None:
            child = container.get_first_child()
            while child is not None:
                following = child.get_next_sibling()
                container.remove(child)
                child = following

        def _build_page(self, calendar_plugin) -> None:
            """Build both views and add the page to the calendar popover."""
            self.calendar = calendar_plugin
            self.view_stack = self.gtk.Stack()
            self.view_stack.set_transition_type(self.gtk.StackTransitionType.CROSSFADE)
            self.view_stack.set_transition_duration(150)
            self.view_stack.set_hhomogeneous(True)
            self.view_stack.set_vhomogeneous(False)
            self.view_stack.add_named(self._build_view(), "view")
            self.view_stack.add_named(self._build_settings_view(), "settings")
            self.page_root = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL)
            self.page_root.add_css_class("wx-page")
            self.page_root.append(self.view_stack)
            calendar_plugin.add_page(
                self.PAGE_ID,
                ["weather-few-clouds-symbolic", "weather-clear-symbolic"],
                "Weather",
                self.page_root,
                on_show=self.on_page_show,
                on_hide=self.on_page_hide,
            )
            self._render()

        def _build_view(self):
            """The forecast view: header, current conditions, hours, details."""
            card = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=14)
            card.add_css_class("wx-card")

            header = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL, spacing=4)
            titles = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=1)
            titles.set_hexpand(True)
            self.location_label = self._label("", "wx-location")
            self.updated_label = self._label("Loading…", "wx-updated")
            titles.append(self.location_label)
            titles.append(self.updated_label)
            self.refresh_spinner = self.gtk.Spinner()
            self.refresh_stack = self.gtk.Stack()
            self.refresh_stack.add_named(
                self.gtk.Image.new_from_icon_name("view-refresh-symbolic"), "icon"
            )
            self.refresh_stack.add_named(self.refresh_spinner, "spinner")
            refresh_button = self.gtk.Button()
            refresh_button.set_child(self.refresh_stack)
            refresh_button.add_css_class("wx-icon-button")
            refresh_button.set_tooltip_text("Refresh")
            refresh_button.connect("clicked", self._on_refresh_clicked)
            self.gtk_helper.add_cursor_effect(refresh_button)
            settings_icon = self.gtk_helper.icon_exist(
                "preferences-system-symbolic",
                ["emblem-system-symbolic", "applications-system-symbolic"],
            )
            header.append(titles)
            header.append(refresh_button)
            header.append(
                self._icon_button(settings_icon, "Weather settings", self._open_settings)
            )
            card.append(header)

            hero = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL, spacing=14)
            hero.set_halign(self.gtk.Align.START)
            self.hero_icon = self._label("🌡️", "wx-hero-icon")
            summary = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=2)
            self.temp_label = self._label("–", "wx-hero-temp")
            self.condition_label = self._label("", "wx-condition")
            self.range_label = self._label("", "wx-range")
            summary.append(self.temp_label)
            summary.append(self.condition_label)
            summary.append(self.range_label)
            hero.append(self.hero_icon)
            hero.append(summary)
            card.append(hero)

            self.hours_box = self.gtk.Box(
                orientation=self.gtk.Orientation.HORIZONTAL, spacing=6
            )
            self.hours_box.add_css_class("wx-hours")
            scroller = self.gtk.ScrolledWindow()
            scroller.set_policy(self.gtk.PolicyType.AUTOMATIC, self.gtk.PolicyType.NEVER)
            scroller.set_propagate_natural_height(True)
            scroller.set_min_content_width(320)
            scroller.set_child(self.hours_box)
            card.append(scroller)

            grid = self.gtk.Grid()
            grid.set_column_spacing(8)
            grid.set_row_spacing(8)
            grid.set_column_homogeneous(True)
            self.tile_values: Dict[str, Any] = {}
            for index, (key, title) in enumerate(STATS):
                tile = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=2)
                tile.add_css_class("wx-tile")
                value = self._label("–", "wx-tile-value")
                tile.append(self._label(title, "wx-tile-title"))
                tile.append(value)
                grid.attach(tile, index % 2, index // 2, 1, 1)
                self.tile_values[key] = value
            card.append(grid)

            self.rain_label = self._label("", "wx-rain")
            self.rain_label.set_wrap(True)
            card.append(self.rain_label)
            self.status_label = self._label("", "wx-status")
            self.status_label.set_wrap(True)
            self.status_label.set_visible(False)
            card.append(self.status_label)
            card.append(self._label("Forecast: MET Norway", "wx-footer"))
            return card

        def _build_settings_view(self):
            """The settings view: location search, coordinates, units, refresh."""
            card = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=10)
            card.add_css_class("wx-card")

            header = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL, spacing=4)
            header.append(self._icon_button("go-previous-symbolic", "Back", self._close_settings))
            title = self._label("Weather settings", "wx-location")
            title.set_hexpand(True)
            header.append(title)
            card.append(header)

            card.append(self._label("Location", "wx-section"))
            search_row = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL, spacing=6)
            self.search_entry = self.gtk.SearchEntry()
            self.search_entry.set_hexpand(True)
            self.search_entry.set_placeholder_text("Search for a city…")
            self.search_entry.connect("activate", self._on_search)
            search_button = self.gtk.Button.new_with_label("Search")
            search_button.add_css_class("wx-button")
            search_button.connect("clicked", self._on_search)
            self.gtk_helper.add_cursor_effect(search_button)
            search_row.append(self.search_entry)
            search_row.append(search_button)
            card.append(search_row)

            self.search_status = self._label("", "wx-hint")
            self.search_status.set_wrap(True)
            self.search_status.set_visible(False)
            card.append(self.search_status)
            self.results_list = self.gtk.ListBox()
            self.results_list.add_css_class("wx-results")
            self.results_list.connect("row-activated", self._on_place_picked)
            self.results_scroll = self.gtk.ScrolledWindow()
            self.results_scroll.set_policy(
                self.gtk.PolicyType.NEVER, self.gtk.PolicyType.AUTOMATIC
            )
            self.results_scroll.set_max_content_height(150)
            self.results_scroll.set_propagate_natural_height(True)
            self.results_scroll.set_child(self.results_list)
            self.results_scroll.set_visible(False)
            card.append(self.results_scroll)

            form = self.gtk.Grid()
            form.set_column_spacing(10)
            form.set_row_spacing(6)
            self.name_entry = self.gtk.Entry()
            self.name_entry.set_placeholder_text("Shown in the header")
            self.lat_entry = self.gtk.Entry()
            self.lat_entry.set_placeholder_text("-23.5505")
            self.lon_entry = self.gtk.Entry()
            self.lon_entry.set_placeholder_text("-46.6333")
            self.alt_entry = self.gtk.Entry()
            self.alt_entry.set_placeholder_text("optional")
            for row, (text, entry) in enumerate(
                (
                    ("Name", self.name_entry),
                    ("Latitude", self.lat_entry),
                    ("Longitude", self.lon_entry),
                    ("Altitude (m)", self.alt_entry),
                )
            ):
                entry.set_hexpand(True)
                form.attach(self._label(text, "wx-field"), 0, row, 1, 1)
                form.attach(entry, 1, row, 1, 1)
            card.append(form)

            card.append(self._label("Units and updates", "wx-section"))
            options = self.gtk.Grid()
            options.set_column_spacing(10)
            options.set_row_spacing(6)
            self.temp_dropdown = self.gtk.DropDown.new_from_strings(["°C", "°F"])
            self.wind_dropdown = self.gtk.DropDown.new_from_strings(list(WIND_UNITS))
            self.rain_dropdown = self.gtk.DropDown.new_from_strings(
                list(PRECIPITATION_UNITS)
            )
            self.refresh_spin = self.gtk.SpinButton.new_with_range(
                MIN_REFRESH_MINUTES, MAX_REFRESH_MINUTES, 5
            )
            self.clock_switch = self.gtk.Switch()
            self.clock_switch.set_halign(self.gtk.Align.START)
            self.clock_switch.set_valign(self.gtk.Align.CENTER)
            for row, (text, widget) in enumerate(
                (
                    ("Temperature", self.temp_dropdown),
                    ("Wind speed", self.wind_dropdown),
                    ("Rain", self.rain_dropdown),
                    ("Refresh (min)", self.refresh_spin),
                    ("24-hour clock", self.clock_switch),
                )
            ):
                if widget is not self.clock_switch:
                    widget.set_hexpand(True)
                options.attach(self._label(text, "wx-field"), 0, row, 1, 1)
                options.attach(widget, 1, row, 1, 1)
            card.append(options)

            self.settings_error = self._label("", "wx-error")
            self.settings_error.set_wrap(True)
            self.settings_error.set_visible(False)
            card.append(self.settings_error)

            buttons = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL, spacing=8)
            buttons.set_halign(self.gtk.Align.END)
            cancel = self.gtk.Button.new_with_label("Cancel")
            cancel.add_css_class("wx-button")
            cancel.connect("clicked", self._close_settings)
            save = self.gtk.Button.new_with_label("Save")
            save.add_css_class("wx-button")
            save.add_css_class("suggested-action")
            save.connect("clicked", self._on_save)
            for button in (cancel, save):
                self.gtk_helper.add_cursor_effect(button)
                buttons.append(button)
            card.append(buttons)
            return card

        # ------------------------------------------------------------------ #
        # Rendering
        # ------------------------------------------------------------------ #

        def _render(self) -> None:
            """Draw the current settings and forecast into the widgets."""
            if self.view_stack is None:
                return
            settings = self.weather_settings
            self.location_label.set_label(settings.place_label)
            forecast = self.forecast
            if forecast is None:
                return
            unit = settings.temperature_unit
            hour24 = settings.clock_24h
            self.hero_icon.set_label(symbol_emoji(forecast.symbol))
            self.temp_label.set_label(
                format_temperature(forecast.temperature, unit, with_unit=True)
            )
            self.condition_label.set_label(
                symbol_label(forecast.symbol) if forecast.symbol else ""
            )
            self.range_label.set_label(
                f"↑ {format_temperature(forecast.high, unit)}   "
                f"↓ {format_temperature(forecast.low, unit)}   ·   next 24 h"
            )
            values = {
                "wind": format_wind(
                    forecast.wind_speed, settings.wind_unit, forecast.wind_direction
                ),
                "humidity": format_percent(forecast.humidity),
                "pressure": format_pressure(forecast.pressure),
                "clouds": format_percent(forecast.cloud_cover),
                "uv": format_uv(forecast.uv_index),
                "rain": format_precipitation(
                    forecast.rain_total, settings.precipitation_unit
                ),
            }
            for key, text in values.items():
                self.tile_values[key].set_label(text)
            self.rain_label.set_label(
                "🌦️  "
                + describe_rain_soon(
                    forecast.rain_soon, settings.precipitation_unit, hour24
                )
            )
            if forecast.updated is not None:
                self.updated_label.set_label(
                    f"Updated {format_clock(forecast.updated, hour24)}"
                )
            self._clear_children(self.hours_box)
            for index, hour in enumerate(forecast.hours):
                chip = self.gtk.Box(
                    orientation=self.gtk.Orientation.VERTICAL, spacing=3
                )
                chip.add_css_class("wx-hour")
                if index == 0:
                    chip.add_css_class("wx-hour-now")
                when = "Now" if index == 0 else format_clock(hour.time, hour24)
                rain = (
                    format_precipitation(hour.precipitation, settings.precipitation_unit)
                    if hour.precipitation > 0
                    else " "
                )
                chip.append(self._label(when, "wx-hour-time", 0.5))
                chip.append(self._label(symbol_emoji(hour.symbol), "wx-hour-icon", 0.5))
                chip.append(
                    self._label(
                        format_temperature(hour.temperature, unit), "wx-hour-temp", 0.5
                    )
                )
                chip.append(self._label(rain, "wx-hour-rain", 0.5))
                self.hours_box.append(chip)

        def _set_loading(self, loading: bool) -> None:
            if self.view_stack is None:
                return
            self.refresh_stack.set_visible_child_name("spinner" if loading else "icon")
            if loading:
                self.refresh_spinner.start()
            else:
                self.refresh_spinner.stop()

        def _show_status(self, text: str) -> None:
            self.status_label.set_label(text)
            self.status_label.set_visible(bool(text))

        # ------------------------------------------------------------------ #
        # Page events
        # ------------------------------------------------------------------ #

        def on_page_show(self) -> None:
            """Pick up config edits and refresh when the data is stale."""
            self._sync_settings(only_if_stale=True)

        def on_page_hide(self) -> None:
            if self.view_stack is not None:
                self.view_stack.set_visible_child_name("view")

        def _on_refresh_clicked(self, _button) -> None:
            self._sync_settings(only_if_stale=False, force=True)

        def _sync_settings(self, only_if_stale: bool, force: bool = False) -> None:
            """Re-read settings in the GTK thread and fetch when needed."""
            current = self._read_settings()
            changed = current != self.weather_settings
            self.weather_settings = current
            if changed:
                self._render()
            limit = timedelta(minutes=current.refresh_minutes)
            stale = (
                self._fetched_at is None
                or datetime.now(timezone.utc) - self._fetched_at > limit
            )
            if changed or force or stale or not only_if_stale:
                self.run_in_async_task(
                    self.fetch_and_update_weather_async(force=changed or force)
                )

        # ------------------------------------------------------------------ #
        # Settings view events
        # ------------------------------------------------------------------ #

        def _open_settings(self, _button) -> None:
            settings = self.weather_settings
            self.name_entry.set_text(settings.location_name)
            self.lat_entry.set_text(f"{settings.latitude:.4f}")
            self.lon_entry.set_text(f"{settings.longitude:.4f}")
            self.alt_entry.set_text(
                "" if settings.altitude is None else str(settings.altitude)
            )
            self.temp_dropdown.set_selected(
                TEMPERATURE_UNITS.index(settings.temperature_unit)
            )
            self.wind_dropdown.set_selected(WIND_UNITS.index(settings.wind_unit))
            self.rain_dropdown.set_selected(
                PRECIPITATION_UNITS.index(settings.precipitation_unit)
            )
            self.refresh_spin.set_value(settings.refresh_minutes)
            self.clock_switch.set_active(settings.clock_24h)
            self.search_entry.set_text("")
            self.settings_error.set_visible(False)
            self.search_status.set_visible(False)
            self.results_scroll.set_visible(False)
            self.view_stack.set_visible_child_name("settings")

        def _close_settings(self, _button=None) -> None:
            self.view_stack.set_visible_child_name("view")

        def _on_search(self, _widget) -> None:
            query = self.search_entry.get_text().strip()
            if len(query) < 2:
                self._set_search_status("Type at least two letters.")
                return
            self._set_search_status("Searching…")
            self.run_in_async_task(
                self._search_places_async(query), on_finish=self._show_places
            )

        async def _search_places_async(self, query: str) -> Optional[List[Place]]:
            """Look the query up with the geocoding service; None on failure."""
            headers = {"User-Agent": self.weather_settings.user_agent}
            try:
                response = await self.asyncio.to_thread(
                    self.requests.get, geocode_url(query), headers=headers, timeout=10
                )
                response.raise_for_status()
                return parse_places(response.json())
            except Exception as e:
                self.logger.error(f"Location search failed: {e}")
                return None

        def _set_search_status(self, text: str) -> None:
            self.search_status.set_label(text)
            self.search_status.set_visible(bool(text))

        def _show_places(self, places: Optional[List[Place]]) -> None:
            self._clear_children(self.results_list)
            self.places = places or []
            if places is None:
                self.results_scroll.set_visible(False)
                self._set_search_status("Search failed. Check your connection.")
                return
            if not places:
                self.results_scroll.set_visible(False)
                self._set_search_status("No matching places found.")
                return
            for place in places:
                row = self.gtk.ListBoxRow()
                box = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=1)
                box.append(self._label(place.label, "wx-result-name"))
                box.append(
                    self._label(
                        f"{place.latitude:.4f}, {place.longitude:.4f}", "wx-result-coords"
                    )
                )
                row.set_child(box)
                self.results_list.append(row)
            self.results_scroll.set_visible(True)
            self._set_search_status("Pick a result to fill in the location.")

        def _on_place_picked(self, _listbox, row) -> None:
            index = row.get_index()
            if not 0 <= index < len(self.places):
                return
            place = self.places[index]
            self.name_entry.set_text(place.name)
            self.lat_entry.set_text(f"{place.latitude:.4f}")
            self.lon_entry.set_text(f"{place.longitude:.4f}")
            self.alt_entry.set_text("" if place.elevation is None else str(place.elevation))
            self.results_scroll.set_visible(False)
            self._set_search_status(f"Selected {place.label}.")

        def _fail_settings(self, message: str) -> None:
            self.settings_error.set_label(message)
            self.settings_error.set_visible(True)

        def _on_save(self, _button) -> None:
            latitude = parse_latitude(self.lat_entry.get_text())
            longitude = parse_longitude(self.lon_entry.get_text())
            if latitude is None or longitude is None:
                self._fail_settings(
                    "Enter a latitude between -90 and 90 and a longitude between -180 and 180."
                )
                return
            altitude_text = self.alt_entry.get_text().strip()
            altitude = parse_altitude(altitude_text)
            if altitude_text and altitude is None:
                self._fail_settings("Altitude must be in metres, from -500 to 9000.")
                return
            self.set_plugin_setting(
                ["location"],
                {
                    "name": self.name_entry.get_text().strip(),
                    "latitude": latitude,
                    "longitude": longitude,
                    "altitude": "" if altitude is None else altitude,
                },
            )
            self.set_plugin_setting(
                ["units"],
                {
                    "temperature": TEMPERATURE_UNITS[self.temp_dropdown.get_selected()],
                    "wind": WIND_UNITS[self.wind_dropdown.get_selected()],
                    "precipitation": PRECIPITATION_UNITS[self.rain_dropdown.get_selected()],
                },
            )
            self.set_plugin_setting(
                ["refresh_minutes"], self.refresh_spin.get_value_as_int()
            )
            self.set_plugin_setting(["clock_24h"], self.clock_switch.get_active())
            self.settings_error.set_visible(False)
            self.view_stack.set_visible_child_name("view")
            self._sync_settings(only_if_stale=False, force=True)

        # ------------------------------------------------------------------ #
        # Fetching
        # ------------------------------------------------------------------ #

        async def fetch_weather_data_async(
            self, force: bool = False
        ) -> Optional[Dict[str, Any]]:
            """
            Asynchronously fetch full weather data from the API.
            This runs the blocking network request in a separate thread
            to avoid blocking the main asyncio event loop. Follows the MET
            Norway terms: it sends a User-Agent, waits for the Expires time
            and uses If-Modified-Since so unchanged data costs a 304.
            Args:
                force: Ignore the Expires time (still sends If-Modified-Since).
            Returns:
                dict | None: The parsed JSON response, or None on failure.
            """
            settings = self.weather_settings
            url = forecast_url(settings.latitude, settings.longitude, settings.altitude)
            cached = self._cache is not None and self._cache_url == url
            now = datetime.now(timezone.utc)
            if not force and cached and self._expires and now < self._expires:
                return self._cache
            headers = {"User-Agent": settings.user_agent}
            if cached and self._last_modified:
                headers["If-Modified-Since"] = self._last_modified
            try:
                response = await self.asyncio.to_thread(
                    self.requests.get, url, headers=headers, timeout=10
                )
                if response.status_code == 304 and cached:
                    self._expires = (
                        parse_http_date(response.headers.get("Expires")) or self._expires
                    )
                    return self._cache
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                self.logger.error(f"Failed to fetch weather data: {e}")
                return None
            self._cache = data
            self._cache_url = url
            self._last_modified = response.headers.get("Last-Modified")
            self._expires = parse_http_date(response.headers.get("Expires"))
            return data

        async def fetch_and_update_weather_async(self, force: bool = False):
            """Asynchronously fetch weather data and update the page."""
            if self._fetching:
                self._pending_force = self._pending_force or force
                return
            self._fetching = True
            self.schedule_in_gtk_thread(self._set_loading, True)
            forecast: Optional[Forecast] = None
            error = ""
            try:
                data = await self.fetch_weather_data_async(force)
                if data is None:
                    error = "Couldn't reach the weather service."
                else:
                    forecast = summarize_forecast(
                        data, hours=self.weather_settings.hours_shown
                    )
                    if forecast is None:
                        error = "The weather service returned unreadable data."
            finally:
                self._fetching = False
            self.schedule_in_gtk_thread(self._finish_update, forecast, error)
            if self._pending_force:
                self._pending_force = False
                await self.fetch_and_update_weather_async(force=True)

        def _finish_update(self, forecast: Optional[Forecast], error: str) -> None:
            """Show the result of a fetch. Runs in the GTK thread."""
            self._set_loading(False)
            if forecast is not None:
                self.forecast = forecast
                self._fetched_at = datetime.now(timezone.utc)
                self._show_status("")
                self._render()
                return
            if self.forecast is not None:
                self._show_status(f"{error} Showing the last forecast.")
            else:
                self._show_status(f"{error} Check your connection and location.")
                self.updated_label.set_label("Not available")

        async def periodic_weather_update(self):
            """Periodically refresh the forecast; the interval comes from the settings."""
            while True:
                try:
                    await self.asyncio.sleep(self.weather_settings.refresh_minutes * 60)
                    self.schedule_in_gtk_thread(self._sync_settings, False)
                except self.asyncio.CancelledError:
                    self.logger.info("Periodic weather update task was cancelled.")
                    break
                except Exception as e:
                    self.logger.error(f"Error in periodic weather update: {e}")
                    await self.asyncio.sleep(300)

        def on_stop(self):
            """Cleanup method to cancel the periodic task when the plugin is stopped."""
            if self.update_task and not self.update_task.done():
                self.global_loop.call_soon_threadsafe(self.update_task.cancel)
                self.logger.info("Cancelled weather periodic update task.")
            calendar_plugin = self.plugins.get("calendar")
            if calendar_plugin:
                calendar_plugin.remove_page(self.PAGE_ID)

    return WeatherPlugin
