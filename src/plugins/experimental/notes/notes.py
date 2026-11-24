def get_plugin_metadata(panel):
    about = (
        "Notes plugin with a dialog centered on the screen: quick add, search, "
        "inline edit, reminders and undo."
    )

    id = "org.jfshell.plugin.notes"
    default_container = "top-panel-systray"
    container, id = panel.config_handler.get_plugin_container(default_container, id)

    return {
        "id": id,
        "name": "Notes",
        "version": "2.0.0",
        "enabled": True,
        "index": 1,
        "container": container,
        "deps": ["css_generator"],
        "description": about,
    }


def get_plugin_class():
    """
    Returns the MenuNotes class with necessary imports scoped inside.
    """
    import time
    from gi.repository import Gdk, Gio, GLib, Gtk, Pango
    from src.plugins.core._base import BasePlugin
    from ._notes_format import (
        first_line,
        format_age,
        format_created,
        join_content,
        matches_query,
        new_stamp,
        reminder_summary,
        split_content,
    )
    from ._notes_manager import NotesManager

    PRESET_MINUTES = (5, 15, 30, 60, 180)
    UNDO_SECONDS = 6
    TOAST_SECONDS = 2

    class MenuNotes(BasePlugin):
        """
        Notes client. A dialog centered on the screen: the list and a filter on
        the left, the selected note (or the editor) on the right.

        Database work runs on the shared asyncio loop (another thread), so every
        widget is touched only from ``on_finish`` callbacks, which run in the GTK
        thread.
        """

        def __init__(self, panel_instance):
            super().__init__(panel_instance)

        def on_start(self):
            self.db_path = self.path_handler.get_data_path("db/notes/notes.db")
            self.manager = NotesManager(self.db_path)
            self.dialog = None
            self.listbox = None
            self.reminder_timer_id = None
            self._notes = {}
            self._selected_id = None
            self._previous_id = None
            self._mode = "view"  # "view" | "edit" | "new"
            self._is_populating = False
            self._repopulate = False
            self._undo_note = None
            self._toast_source = None
            self._active_clipboard_provider = None
            self._calendar_grid = None
            self._calendar_popover = None
            self._calendar_popover_handler = None
            self._calendar_notes_box = None
            self._calendar_notes_scroll = None
            self._calendar_attach_id = None
            self._calendar_attach_tries = 0

            self.dialog_width = self.get_plugin_setting_add_hint(
                ["dialog_width"], 980, "The width (in pixels) of the notes dialog."
            )
            self.dialog_height = self.get_plugin_setting_add_hint(
                ["dialog_height"], 580, "The height (in pixels) of the notes dialog."
            )
            self.reminder_check_interval = self.get_plugin_setting_add_hint(
                ["reminder_check_interval"],
                10,
                "How often (in seconds) due note reminders are checked.",
            )
            self.search_placeholder = self.get_plugin_setting_add_hint(
                ["search_placeholder_text"],
                "Filter notes...",
                "The placeholder text displayed in the note search bar.",
            )
            self.add_entry_placeholder = self.get_plugin_setting_add_hint(
                ["add_entry_placeholder_text"],
                "Title",
                "The placeholder text displayed in the note title field.",
            )
            self.add_desc_placeholder = self.get_plugin_setting_add_hint(
                ["add_desc_placeholder_text"],
                "Write something... (optional)",
                "The placeholder text displayed in the note description field.",
            )
            self.add_button_label = self.get_plugin_setting_add_hint(
                ["add_button_label"],
                "Add",
                "The label text for the button that saves a new note.",
            )
            self.clear_button_label = self.get_plugin_setting_add_hint(
                ["clear_button_label"],
                "Clear All",
                "The label text for the button that confirms deleting all notes.",
            )
            self.delete_icon_name = self.get_plugin_setting_add_hint(
                ["delete_icon"],
                "user-trash-symbolic",
                "The icon name used for the delete buttons. Falls back to 'edit-delete'.",
            )
            self.edit_icon_name = self.get_plugin_setting_add_hint(
                ["edit_icon"],
                "document-edit-symbolic",
                "The icon name used for the edit button. Falls back to 'document-edit'.",
            )
            self.alarm_icon_name = self.get_plugin_setting_add_hint(
                ["alarm_icon"],
                "alarm-symbolic",
                "The icon name used for reminders.",
            )
            self.main_icon = self.get_plugin_setting_add_hint(
                ["main_icon"],
                "ymuse-replace-queue-symbolic",
                "The default icon name for the notes plugin.",
            )
            self.fallback_main_icons = self.get_plugin_setting_add_hint(
                ["fallback_main_icons"],
                [
                    "view-paged-symbolic",
                    "org.gnome.Notes-symbolic",
                    "notes-app-symbolic",
                    "accessories-notes-symbolic",
                    "xapp-annotations-text-symbolic",
                    "accessories-text-editor-symbolic",
                    "x-office-document-symbolic",
                    "ymuse-replace-queue-symbolic",
                ],
                "A prioritized list of fallback icons to use if the main icon is not found.",
            )

            icon = self.gtk_helper.icon_exist
            self._icons = {
                "main": icon(self.main_icon, self.fallback_main_icons),
                "note": icon("text-x-generic-symbolic", ["document-symbolic"]),
                "add": icon("list-add-symbolic", ["add-symbolic", "document-new"]),
                "alarm": icon(
                    self.alarm_icon_name,
                    ["alarm-symbolic", "appointment-soon-symbolic"],
                ),
                "edit": icon(
                    self.edit_icon_name, ["document-edit-symbolic", "document-edit"]
                ),
                "delete": icon(
                    self.delete_icon_name,
                    ["user-trash-symbolic", "edit-delete-symbolic", "edit-delete"],
                ),
                "copy": icon("edit-copy-symbolic", ["edit-copy"]),
                "close": icon("window-close-symbolic", ["window-close"]),
                "save": icon(
                    "object-select-symbolic",
                    ["emblem-ok-symbolic", "document-save-symbolic"],
                ),
                "undo": icon("edit-undo-symbolic", ["edit-undo"]),
            }

            self.run_in_async_task(self.manager.initialize_db())
            self.create_notes_ui()
            self._register_actions()
            self.plugins["css_generator"].install_css("notes.css")
            self.plugins["css_generator"].install_css("notes-dialog.css")
            self.plugins["css_generator"].install_css("notes-calendar.css")
            self.reminder_timer_id = GLib.timeout_add_seconds(
                max(1, int(self.reminder_check_interval)), self._check_reminders
            )
            self._calendar_attach_id = GLib.timeout_add_seconds(
                1, self._attach_calendar_notes
            )

        def on_disable(self):
            if self._calendar_attach_id is not None:
                GLib.source_remove(self._calendar_attach_id)
                self._calendar_attach_id = None
            self._detach_calendar_notes()
            if self.reminder_timer_id is not None:
                GLib.source_remove(self.reminder_timer_id)
                self.reminder_timer_id = None
            if self._toast_source is not None:
                GLib.source_remove(self._toast_source)
                self._toast_source = None
            if self.dialog is not None:
                self.dialog.destroy()
                self.dialog = None

        def _register_actions(self):
            """Keeps the ``show_searchbar`` action: open the dialog on the filter."""
            try:
                action = Gio.SimpleAction.new("show_searchbar")
                action.connect("activate", lambda *_: self._open_and_search())
                self.obj.add_action(action)
            except Exception as e:
                self.logger.error(f"Notes: could not register action: {e}")

        def _open_and_search(self):
            self.open_dialog()
            self.searchbar.grab_focus()

        # ------------------------------------------------------------------
        # Panel button and dialog window
        # ------------------------------------------------------------------
        def create_notes_ui(self):
            self.button_notes = Gtk.Button()
            self.button_notes.add_css_class("panel-button")
            self.button_notes.add_css_class("notes-menubutton")
            icon_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            icon_box.append(Gtk.Image.new_from_icon_name(self._icons["main"]))
            self.button_notes.set_child(icon_box)
            self.gtk_helper.add_cursor_effect(self.button_notes)
            self.button_notes.connect("clicked", self.toggle_dialog)
            self.main_widget = (self.button_notes, "append")

        def toggle_dialog(self, *_):
            if self.dialog is not None and self.dialog.get_visible():
                self.close_dialog()
            else:
                self.open_dialog()

        def open_popover_notes(self, *_):
            """Kept so existing shortcuts (``notes.open_popover_notes``) still work."""
            self.toggle_dialog()

        def open_dialog(self):
            if self.dialog is None:
                self._build_dialog()
            self.searchbar.set_text("")
            self._mode = "view"
            self.confirm_revealer.set_reveal_child(False)
            self.refresh_notes()
            self.dialog.present()
            self.searchbar.grab_focus()

        def close_dialog(self):
            if self.dialog is not None:
                self._mode = "view"
                self.dialog.set_visible(False)

        def _on_close_request(self, _window):
            self.close_dialog()
            return True

        def _build_dialog(self):
            window = Gtk.Window()
            window.add_css_class("notes-dialog")
            # The theme's `background` class paints an opaque rectangle behind the
            # window, which shows as square corners around the rounded dialog.
            window.remove_css_class("background")
            window.set_default_size(self.dialog_width, self.dialog_height)

            # No anchor: layer-shell centers the surface on the output.
            self.layer_shell.init_for_window(window)
            self.layer_shell.set_namespace(window, "JFShell-Notes")
            self.layer_shell.set_layer(window, self.layer_shell.Layer.TOP)
            self.layer_shell.set_keyboard_mode(
                window, self.layer_shell.KeyboardMode.EXCLUSIVE
            )

            window.connect("close-request", self._on_close_request)
            keys = Gtk.EventControllerKey()
            keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            keys.connect("key-pressed", self._on_key_pressed)
            window.add_controller(keys)

            root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            root.add_css_class("notes-dialog-box")
            root.set_overflow(Gtk.Overflow.HIDDEN)
            root.append(self._build_sidebar())
            root.append(self._build_detail())
            window.set_child(root)
            self.dialog = window

        def _icon_button(self, icon_key, tooltip, callback, danger=False):
            button = Gtk.Button.new_from_icon_name(self._icons[icon_key])
            button.set_tooltip_text(tooltip)
            button.set_focus_on_click(False)
            button.add_css_class("notes-dialog-button")
            if danger:
                button.add_css_class("danger")
            button.connect("clicked", callback)
            self.gtk_helper.add_cursor_effect(button)
            return button

        def _text_button(self, label, callback, css_class=None):
            button = Gtk.Button(label=label)
            button.set_focus_on_click(False)
            button.add_css_class("notes-dialog-text-button")
            if css_class:
                button.add_css_class(css_class)
            button.connect("clicked", callback)
            self.gtk_helper.add_cursor_effect(button)
            return button

        # ------------------------------------------------------------------
        # Sidebar
        # ------------------------------------------------------------------
        def _build_sidebar(self):
            sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            sidebar.add_css_class("notes-dialog-sidebar")
            sidebar.set_size_request(580, -1)

            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            title = Gtk.Label(label="Notes", xalign=0, hexpand=True)
            title.add_css_class("notes-dialog-title")
            header.append(title)
            self.btn_new = self._icon_button(
                "add", "New note (Ctrl+N)", lambda *_: self._start_new()
            )
            self.btn_new.add_css_class("accent")
            header.append(self.btn_new)
            self.btn_clear = self._icon_button(
                "delete", "Delete all notes", self._on_clear_clicked, danger=True
            )
            header.append(self.btn_clear)

            self.confirm_revealer = Gtk.Revealer(
                transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN
            )
            confirm = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            confirm.add_css_class("notes-dialog-confirm")
            self.confirm_label = Gtk.Label(
                label="Delete all notes?", xalign=0, hexpand=True
            )
            self.confirm_label.add_css_class("notes-dialog-confirm-label")
            confirm.append(self.confirm_label)
            confirm.append(
                self._text_button(
                    "Cancel", lambda *_: self.confirm_revealer.set_reveal_child(False)
                )
            )
            confirm.append(
                self._text_button(
                    self.clear_button_label, self._on_clear_confirmed, "danger"
                )
            )
            self.confirm_revealer.set_child(confirm)

            self.searchbar = Gtk.SearchEntry(placeholder_text=self.search_placeholder)
            self.searchbar.add_css_class("notes-dialog-search")
            self.searchbar.connect("search-changed", self._on_search_changed)
            self.searchbar.connect("activate", lambda _: self._start_edit())

            self.sw = Gtk.ScrolledWindow(vexpand=True)
            self.sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.listbox = self._new_listbox()
            self.sw.set_child(self.listbox)

            sidebar.append(header)
            sidebar.append(self.confirm_revealer)
            sidebar.append(self.searchbar)
            sidebar.append(self.sw)
            return sidebar

        def _placeholder_text(self):
            searchbar = getattr(self, "searchbar", None)
            if searchbar is not None and searchbar.get_text().strip():
                return "No matching notes"
            return "No notes yet"

        def _new_listbox(self):
            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
            # A single click only selects (shows the note), double click or Enter
            # opens the editor.
            listbox.set_activate_on_single_click(False)
            listbox.add_css_class("notes-dialog-listbox")
            listbox.set_filter_func(self._row_matches)
            # One label per list: a widget can only have a single parent.
            self.list_placeholder = Gtk.Label(label=self._placeholder_text())
            self.list_placeholder.add_css_class("notes-dialog-subtitle")
            self.list_placeholder.set_margin_top(24)
            listbox.set_placeholder(self.list_placeholder)
            listbox.connect("row-selected", self._on_row_selected)
            listbox.connect("row-activated", lambda *_: self._start_edit())
            return listbox

        # ------------------------------------------------------------------
        # Detail pane
        # ------------------------------------------------------------------
        def _build_detail(self):
            detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            detail.add_css_class("notes-dialog-detail")
            detail.set_hexpand(True)

            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            titles.set_hexpand(True)
            titles.set_valign(Gtk.Align.START)
            self.detail_title = Gtk.Label(xalign=0, wrap=True)
            self.detail_title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.detail_title.set_lines(2)
            self.detail_title.set_ellipsize(Pango.EllipsizeMode.END)
            self.detail_title.add_css_class("notes-dialog-title")
            self.detail_subtitle = Gtk.Label(xalign=0)
            self.detail_subtitle.add_css_class("notes-dialog-subtitle")
            titles.append(self.detail_title)
            titles.append(self.detail_subtitle)

            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.set_valign(Gtk.Align.START)
            self.btn_copy = self._icon_button("copy", "Copy", self._on_copy_clicked)
            self.btn_edit = self._icon_button(
                "edit", "Edit", lambda *_: self._start_edit()
            )
            self.btn_delete = self._icon_button(
                "delete", "Delete", self._on_delete_clicked, danger=True
            )
            self.btn_close = self._icon_button(
                "close", "Close", lambda *_: self.close_dialog()
            )
            for button in (
                self.btn_copy,
                self.btn_edit,
                self.btn_delete,
                self.btn_close,
            ):
                actions.append(button)

            header.append(titles)
            header.append(actions)

            self.detail_stack = Gtk.Stack(vexpand=True)
            self.detail_stack.add_css_class("notes-dialog-preview")
            self.detail_stack.set_overflow(Gtk.Overflow.HIDDEN)
            self.detail_stack.add_named(self._build_view_page(), "view")
            self.detail_stack.add_named(self._build_edit_page(), "edit")
            self.detail_stack.add_named(self._build_empty_page(), "empty")
            self.detail_stack.set_visible_child_name("empty")

            overlay = Gtk.Overlay()
            overlay.set_vexpand(True)
            overlay.set_child(self.detail_stack)
            overlay.add_overlay(self._build_toast())

            detail.append(header)
            detail.append(overlay)
            self._set_actions_sensitive(False)
            return detail

        def _set_actions_sensitive(self, sensitive):
            for button in (self.btn_copy, self.btn_edit, self.btn_delete):
                button.set_sensitive(sensitive)

        def _build_toast(self):
            self.toast_revealer = Gtk.Revealer(
                transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                halign=Gtk.Align.CENTER,
                valign=Gtk.Align.END,
            )
            self.toast_revealer.set_margin_bottom(16)
            toast = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            toast.add_css_class("notes-dialog-toast")
            self.toast_label = Gtk.Label(label="")
            self.toast_label.add_css_class("notes-dialog-toast-label")
            self.toast_undo = self._text_button("Undo", self._on_undo_clicked, "accent")
            toast.append(self.toast_label)
            toast.append(self.toast_undo)
            self.toast_revealer.set_child(toast)
            return self.toast_revealer

        def _build_view_page(self):
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
            body.add_css_class("notes-dialog-view")

            self.desc_label = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True)
            self.desc_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.desc_label.set_hexpand(True)
            self.desc_label.add_css_class("notes-dialog-desc")
            body.append(self.desc_label)
            body.append(self._build_reminder_card())
            scroller.set_child(body)
            return scroller

        def _build_reminder_card(self):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            card.add_css_class("notes-dialog-card")

            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            self.reminder_icon = Gtk.Image.new_from_icon_name(self._icons["alarm"])
            self.reminder_icon.set_pixel_size(20)
            self.reminder_icon.add_css_class("notes-dialog-card-icon")
            head.append(self.reminder_icon)
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            texts.set_hexpand(True)
            card_title = Gtk.Label(label="Reminder", xalign=0)
            card_title.add_css_class("notes-dialog-card-title")
            self.reminder_status = Gtk.Label(xalign=0)
            self.reminder_status.set_ellipsize(Pango.EllipsizeMode.END)
            self.reminder_status.add_css_class("notes-dialog-subtitle")
            texts.append(card_title)
            texts.append(self.reminder_status)
            head.append(texts)
            card.append(head)

            chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self._chips = {}
            for minutes in PRESET_MINUTES:
                label = f"{minutes} min" if minutes < 60 else f"{minutes // 60} h"
                chip = Gtk.Button(label=label)
                chip.set_focus_on_click(False)
                chip.set_hexpand(True)
                chip.add_css_class("notes-dialog-chip")
                chip.connect("clicked", self._on_chip_clicked, minutes)
                self.gtk_helper.add_cursor_effect(chip)
                chips.append(chip)
                self._chips[minutes] = chip
            card.append(chips)

            grid = Gtk.Grid(column_spacing=12, row_spacing=8)
            self.spin_interval = Gtk.SpinButton.new(
                Gtk.Adjustment(
                    value=5, lower=1, upper=1440, step_increment=1, page_increment=5
                ),
                1.0,
                0,
            )
            self.spin_interval.connect("value-changed", lambda *_: self._sync_chips())
            self.spin_count = Gtk.SpinButton.new(
                Gtk.Adjustment(
                    value=1, lower=1, upper=100, step_increment=1, page_increment=5
                ),
                1.0,
                0,
            )
            for row, (text, spin) in enumerate(
                (
                    ("Repeat every (minutes)", self.spin_interval),
                    ("Number of times", self.spin_count),
                )
            ):
                label = Gtk.Label(label=text, xalign=0, hexpand=True)
                label.add_css_class("notes-dialog-field-label")
                spin.add_css_class("notes-dialog-spin")
                grid.attach(label, 0, row, 1, 1)
                grid.attach(spin, 1, row, 1, 1)
            card.append(grid)

            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            buttons.set_halign(Gtk.Align.END)
            self.btn_reminder_clear = self._text_button(
                "Clear", self._on_clear_reminder_clicked
            )
            buttons.append(self.btn_reminder_clear)
            buttons.append(
                self._text_button(
                    "Set reminder", self._on_set_reminder_clicked, "primary"
                )
            )
            card.append(buttons)
            return card

        def _build_edit_page(self):
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            page.add_css_class("notes-dialog-edit")

            title_label = Gtk.Label(label="Title", xalign=0)
            title_label.add_css_class("notes-dialog-field-label")
            self.entry_title = Gtk.Entry(placeholder_text=self.add_entry_placeholder)
            self.entry_title.add_css_class("notes-dialog-entry")
            self.entry_title.connect("activate", lambda *_: self.text_desc.grab_focus())
            self.entry_title.connect("changed", lambda e: e.remove_css_class("error"))

            desc_label = Gtk.Label(label="Description", xalign=0)
            desc_label.add_css_class("notes-dialog-field-label")
            self.text_desc = Gtk.TextView(
                wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False
            )
            self.text_desc.add_css_class("notes-dialog-textarea")
            for side in ("top", "bottom", "left", "right"):
                getattr(self.text_desc, f"set_{side}_margin")(12)
            desc_scroller = Gtk.ScrolledWindow(vexpand=True)
            desc_scroller.add_css_class("notes-dialog-textarea-frame")
            desc_scroller.set_overflow(Gtk.Overflow.HIDDEN)
            desc_scroller.set_child(self.text_desc)

            self.desc_placeholder_label = Gtk.Label(
                label=self.add_desc_placeholder, xalign=0, yalign=0
            )
            self.desc_placeholder_label.add_css_class("notes-dialog-placeholder")
            self.desc_placeholder_label.set_can_target(False)
            self.desc_placeholder_label.set_margin_top(12)
            self.desc_placeholder_label.set_margin_start(12)
            self.text_desc.get_buffer().connect(
                "changed",
                lambda buf: self.desc_placeholder_label.set_visible(
                    buf.get_char_count() == 0
                ),
            )
            desc_overlay = Gtk.Overlay()
            desc_overlay.set_vexpand(True)
            desc_overlay.set_child(desc_scroller)
            desc_overlay.add_overlay(self.desc_placeholder_label)

            footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hint = Gtk.Label(label="Ctrl+Enter to save \u2022 Esc to cancel", xalign=0)
            hint.set_hexpand(True)
            hint.add_css_class("notes-dialog-subtitle")
            self.btn_cancel = self._text_button(
                "Cancel", lambda *_: self._cancel_edit()
            )
            self.btn_save = self._text_button(
                self.add_button_label, lambda *_: self._save(), "primary"
            )
            footer.append(hint)
            footer.append(self.btn_cancel)
            footer.append(self.btn_save)

            page.append(title_label)
            page.append(self.entry_title)
            page.append(desc_label)
            page.append(desc_overlay)
            page.append(footer)
            return page

        def _build_empty_page(self):
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            page.set_valign(Gtk.Align.CENTER)
            page.set_halign(Gtk.Align.CENTER)
            icon = Gtk.Image.new_from_icon_name(self._icons["note"])
            icon.set_pixel_size(56)
            icon.add_css_class("notes-dialog-empty-icon")
            self.empty_title = Gtk.Label(label="No notes yet")
            self.empty_title.add_css_class("notes-dialog-card-title")
            self.empty_subtitle = Gtk.Label(label="Capture a thought in a second.")
            self.empty_subtitle.add_css_class("notes-dialog-subtitle")
            self.empty_button = self._text_button(
                "New note", lambda *_: self._start_new(), "primary"
            )
            self.empty_button.set_halign(Gtk.Align.CENTER)
            self.empty_button.set_margin_top(8)
            for widget in (
                icon,
                self.empty_title,
                self.empty_subtitle,
                self.empty_button,
            ):
                page.append(widget)
            return page

        # ------------------------------------------------------------------
        # List
        # ------------------------------------------------------------------
        def refresh_notes(self):
            """Reloads the notes; at most one load runs, extra requests coalesce."""
            if self._is_populating:
                self._repopulate = True
                return
            self._is_populating = True
            self._repopulate = False
            self.run_in_async_task(self._load_notes(), on_finish=self._on_notes_loaded)

        async def _load_notes(self):
            try:
                return await self.manager.get_notes()
            except Exception as e:
                self.logger.error(f"Notes: load failed: {e}")
                return None

        def _on_notes_loaded(self, notes):
            self._is_populating = False
            if notes is not None:
                self._update_calendar_notes(notes)
            if notes is not None and self.dialog is not None:
                self._notes = {note.id: note for note in notes}
                # A fresh list is swapped in at once (no per-row reflow).
                self.listbox = self._new_listbox()
                for note in notes:
                    if note.content.strip():
                        self.listbox.append(self._build_row(note))
                self.sw.set_child(self.listbox)
                self._restore_selection()
            if self._repopulate:
                self.refresh_notes()

        def _attach_calendar_notes(self) -> bool:
            """Add the notes list below the calendar of the clock popover.

            Runs on a timer because the calendar plugin may load after this one.
            Returns True to be called again until the calendar is available.
            """
            calendar = self.plugins.get("calendar")
            grid = getattr(calendar, "grid", None)
            popover = getattr(calendar, "popover_calendar", None)
            if grid is None or popover is None:
                self._calendar_attach_tries += 1
                if self._calendar_attach_tries < 10:
                    return True
                self.logger.warning("Notes: calendar not available, list not added.")
                self._calendar_attach_id = None
                return False

            self._calendar_attach_id = None
            self._calendar_grid = grid
            self._calendar_popover = popover
            self._calendar_notes_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=6
            )
            self._calendar_notes_box.add_css_class("calendar-notes")
            self._calendar_notes_box.set_size_request(340, -1)
            self._calendar_notes_box.set_visible(False)
            heading = Gtk.Label(label="Notes", xalign=0)
            heading.add_css_class("calendar-notes-title")
            self._calendar_notes_box.append(heading)
            self._calendar_notes_scroll = Gtk.ScrolledWindow()
            self._calendar_notes_scroll.set_policy(
                Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
            )
            self._calendar_notes_scroll.set_propagate_natural_height(True)
            self._calendar_notes_scroll.set_max_content_height(160)
            self._calendar_notes_box.append(self._calendar_notes_scroll)
            grid.attach(self._calendar_notes_box, 0, 1, 1, 1)
            self._calendar_popover_handler = popover.connect(
                "notify::visible", self._on_calendar_visibility_changed
            )
            self.refresh_notes()
            return False

        def _detach_calendar_notes(self) -> None:
            """Remove the notes list from the calendar popover."""
            if self._calendar_popover is not None and self._calendar_popover_handler:
                self._calendar_popover.disconnect(self._calendar_popover_handler)
            if self._calendar_grid is not None and self._calendar_notes_box is not None:
                self._calendar_grid.remove(self._calendar_notes_box)
            self._calendar_grid = None
            self._calendar_popover = None
            self._calendar_popover_handler = None
            self._calendar_notes_box = None
            self._calendar_notes_scroll = None

        def _on_calendar_visibility_changed(self, popover, _param) -> None:
            if popover.get_visible():
                self.refresh_notes()

        def _update_calendar_notes(self, notes) -> None:
            """Rebuild the calendar list: one row per note, newest change first."""
            if self._calendar_notes_scroll is None:
                return
            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.NONE)
            listbox.add_css_class("calendar-notes-list")
            shown = 0
            for note in notes:
                if note.content.strip():
                    listbox.append(self._build_calendar_row(note))
                    shown += 1
            self._calendar_notes_scroll.set_child(listbox)
            self._calendar_notes_box.set_visible(shown > 0)

        def _build_calendar_row(self, note) -> Gtk.ListBoxRow:
            """Title with the created and edited times, without the description."""
            stamp, title = split_content(note.content)
            info = []
            if stamp:
                info.append(f"Created {format_created(stamp)}")
            info.append(f"Edited {format_age(note.updated).lower()}")

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.add_css_class("calendar-note-row")
            title_label = Gtk.Label(label=title or "Untitled", xalign=0)
            title_label.set_ellipsize(Pango.EllipsizeMode.END)
            title_label.set_max_width_chars(40)
            title_label.add_css_class("calendar-note-title")
            info_label = Gtk.Label(label=" \u2022 ".join(info), xalign=0)
            info_label.set_ellipsize(Pango.EllipsizeMode.END)
            info_label.set_max_width_chars(40)
            info_label.add_css_class("calendar-note-info")
            box.append(title_label)
            box.append(info_label)
            row = Gtk.ListBoxRow(child=box)
            row.set_activatable(False)
            return row

        def _build_row(self, note):
            _stamp, title = split_content(note.content)
            title = title or "Untitled"
            active = note.remind_remaining > 0 and note.remind_at > 0
            snippet = first_line(note.description, 60)
            subtitle = " \u2022 ".join(
                part for part in (format_age(note.updated), snippet) if part
            )

            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row_box.add_css_class("notes-dialog-row-box")
            row_box.ITEM_ID = note.id
            row_box.TITLE = title
            row_box.DESC = note.description or ""

            lead = Gtk.Box()
            lead.set_size_request(40, 40)
            lead.set_hexpand(False)
            lead.set_halign(Gtk.Align.START)
            lead.set_valign(Gtk.Align.CENTER)
            lead.add_css_class("notes-dialog-lead")
            icon = Gtk.Image.new_from_icon_name(self._icons["note"])
            icon.set_pixel_size(20)
            icon.set_hexpand(True)
            icon.set_halign(Gtk.Align.CENTER)
            icon.set_valign(Gtk.Align.CENTER)
            icon.add_css_class("notes-dialog-icon-note")
            lead.append(icon)
            row_box.append(lead)

            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            texts.set_hexpand(True)
            texts.set_halign(Gtk.Align.FILL)
            texts.set_valign(Gtk.Align.CENTER)
            title_label = Gtk.Label(label=title, xalign=0)
            title_label.set_ellipsize(Pango.EllipsizeMode.END)
            title_label.set_max_width_chars(120)
            title_label.set_hexpand(True)
            title_label.add_css_class("notes-dialog-row-title")
            subtitle_label = Gtk.Label(label=subtitle, xalign=0)
            subtitle_label.set_ellipsize(Pango.EllipsizeMode.END)
            subtitle_label.set_max_width_chars(50)
            subtitle_label.set_hexpand(True)
            subtitle_label.add_css_class("notes-dialog-row-subtitle")
            texts.append(title_label)
            texts.append(subtitle_label)
            row_box.append(texts)

            if active:
                bell = Gtk.Image.new_from_icon_name(self._icons["alarm"])
                bell.set_tooltip_text(
                    reminder_summary(
                        note.remind_at, note.remind_interval, note.remind_remaining
                    )
                )
                bell.add_css_class("notes-dialog-row-bell")
                row_box.append(bell)

            row = Gtk.ListBoxRow(child=row_box)
            row.add_css_class("notes-dialog-row")
            return row

        def _row_matches(self, row):
            searchbar = getattr(self, "searchbar", None)
            query = searchbar.get_text() if searchbar else ""
            child = row.get_child()
            return matches_query(query, child.TITLE, child.DESC)

        def _visible_rows(self):
            rows = []
            row = self.listbox.get_first_child() if self.listbox else None
            while row is not None:
                if isinstance(row, Gtk.ListBoxRow) and self._row_matches(row):
                    rows.append(row)
                row = row.get_next_sibling()
            return rows

        def _row_for_id(self, note_id):
            for row in self._visible_rows():
                if row.get_child().ITEM_ID == note_id:
                    return row
            return None

        def _editing(self):
            return self._mode in ("edit", "new")

        def _restore_selection(self):
            if self._mode == "new":
                self.listbox.unselect_all()
                return
            rows = self._visible_rows()
            target = self._row_for_id(self._selected_id) or (rows[0] if rows else None)
            if target is None:
                self._selected_id = None
                self._show_empty()
                return
            self._select_and_scroll(target)

        def _select_and_scroll(self, row):
            self.listbox.select_row(row)
            GLib.idle_add(self._scroll_to_row, row)

        def _scroll_to_row(self, row):
            try:
                found, bounds = row.compute_bounds(self.listbox)
                if found:
                    adjustment = self.sw.get_vadjustment()
                    top = bounds.get_y()
                    bottom = top + bounds.get_height()
                    if top < adjustment.get_value():
                        adjustment.set_value(top)
                    elif bottom > adjustment.get_value() + adjustment.get_page_size():
                        adjustment.set_value(bottom - adjustment.get_page_size())
            except Exception:
                pass
            return False

        def _move_selection(self, delta):
            rows = self._visible_rows()
            if not rows:
                return
            current = self.listbox.get_selected_row()
            index = rows.index(current) if current in rows else -1
            index = 0 if index < 0 else max(0, min(len(rows) - 1, index + delta))
            self._select_and_scroll(rows[index])

        def _on_search_changed(self, _entry):
            self.listbox.invalidate_filter()
            self.list_placeholder.set_label(self._placeholder_text())
            if self._editing():
                return
            current = self.listbox.get_selected_row()
            if current is not None and self._row_matches(current):
                return
            rows = self._visible_rows()
            if rows:
                self._select_and_scroll(rows[0])
            else:
                self.listbox.unselect_all()
                self._selected_id = None
                self._show_empty()

        def _on_key_pressed(self, _controller, keyval, _keycode, state):
            ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
            if keyval == Gdk.KEY_Escape:
                if self._editing():
                    self._cancel_edit()
                elif self.confirm_revealer.get_reveal_child():
                    self.confirm_revealer.set_reveal_child(False)
                elif self.searchbar.get_text():
                    self.searchbar.set_text("")
                else:
                    self.close_dialog()
                return True
            if ctrl and keyval in (Gdk.KEY_n, Gdk.KEY_N):
                self._start_new()
                return True
            if ctrl and keyval in (Gdk.KEY_f, Gdk.KEY_F):
                if not self._editing():
                    self.searchbar.grab_focus()
                return True
            if ctrl and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                if self._editing():
                    self._save()
                    return True
                return False
            if self._editing():
                return False
            focus = self.dialog.get_focus()
            if focus is not None and focus.get_ancestor(Gtk.SpinButton) is not None:
                return False
            if keyval == Gdk.KEY_Down:
                self._move_selection(1)
                return True
            if keyval == Gdk.KEY_Up:
                self._move_selection(-1)
                return True
            if keyval == Gdk.KEY_Delete and isinstance(focus, Gtk.ListBoxRow):
                self._on_delete_clicked()
                return True
            return False

        # ------------------------------------------------------------------
        # Detail
        # ------------------------------------------------------------------
        def _on_row_selected(self, _listbox, row):
            if row is None:
                return
            note_id = row.get_child().ITEM_ID
            if self._editing() and note_id == self._selected_id:
                return  # a background refresh must not drop the open editor
            self._show_note(note_id)

        def _selected_note(self):
            return self._notes.get(self._selected_id)

        def _show_empty(self):
            self._mode = "view"
            self.detail_title.set_label("Notes")
            self.detail_subtitle.set_label("")
            self._set_actions_sensitive(False)
            has_notes = bool(self._notes)
            self.empty_title.set_label(
                "No matching notes" if has_notes else "No notes yet"
            )
            self.empty_subtitle.set_label(
                "Try a different search."
                if has_notes
                else "Capture a thought in a second."
            )
            self.empty_button.set_visible(not has_notes)
            self.detail_stack.set_visible_child_name("empty")

        def _show_note(self, note_id):
            note = self._notes.get(note_id)
            if note is None:
                self._show_empty()
                return
            self._selected_id = note_id
            self._mode = "view"
            stamp, title = split_content(note.content)
            self.detail_title.set_label(title or "Untitled")
            parts = []
            if stamp:
                parts.append(f"Created {format_created(stamp)}")
            parts.append(f"Edited {format_age(note.updated).lower()}")
            self.detail_subtitle.set_label(" \u2022 ".join(parts))
            self._set_actions_sensitive(True)

            description = (note.description or "").strip()
            if description:
                self.desc_label.set_label(description)
                self.desc_label.remove_css_class("empty")
            else:
                self.desc_label.set_label(
                    "No description. Press the edit button to add one."
                )
                self.desc_label.add_css_class("empty")
            self._refresh_reminder_card(note)
            self.detail_stack.set_visible_child_name("view")

        # ------------------------------------------------------------------
        # Editor
        # ------------------------------------------------------------------
        def _start_new(self):
            if self.dialog is None or not self.dialog.get_visible():
                self.open_dialog()
            self.confirm_revealer.set_reveal_child(False)
            if self._mode != "new":
                self._previous_id = self._selected_id
            self._mode = "new"
            self._selected_id = None
            self.listbox.unselect_all()
            self.entry_title.set_text("")
            self.text_desc.get_buffer().set_text("")
            self.btn_save.set_label(self.add_button_label)
            self.detail_title.set_label("New note")
            self.detail_subtitle.set_label("")
            self._set_actions_sensitive(False)
            self.detail_stack.set_visible_child_name("edit")
            self.entry_title.grab_focus()

        def _start_edit(self):
            note = self._selected_note()
            if note is None or self._editing():
                return
            self._mode = "edit"
            _stamp, title = split_content(note.content)
            self.entry_title.set_text(title)
            self.text_desc.get_buffer().set_text(note.description or "")
            self.btn_save.set_label("Save")
            self.detail_title.set_label("Edit note")
            self.detail_subtitle.set_label(title)
            self._set_actions_sensitive(False)
            self.detail_stack.set_visible_child_name("edit")
            self.entry_title.grab_focus()
            self.entry_title.set_position(-1)

        def _cancel_edit(self):
            if not self._editing():
                return
            if self._mode == "new":
                self._selected_id = self._previous_id
                self._mode = "view"
                self._restore_selection()
            else:
                self._show_note(self._selected_id)
            self.searchbar.grab_focus()

        def _description_text(self):
            buffer = self.text_desc.get_buffer()
            return buffer.get_text(
                buffer.get_start_iter(), buffer.get_end_iter(), False
            ).strip()

        def _save(self):
            if not self._editing():
                return
            title = self.entry_title.get_text().strip()
            if not title:
                self.entry_title.add_css_class("error")
                self.entry_title.grab_focus()
                return
            description = self._description_text()
            if self._mode == "new":
                content = join_content(new_stamp(), title)
                note_id = None
            else:
                note = self._selected_note()
                if note is None:
                    self._cancel_edit()
                    return
                stamp, _old = split_content(note.content)
                content = join_content(stamp or new_stamp(), title)
                note_id = note.id
            self.btn_save.set_sensitive(False)
            self.run_in_async_task(
                self._save_note(note_id, content, description),
                on_finish=self._on_saved,
            )

        async def _save_note(self, note_id, content, description):
            try:
                if note_id is None:
                    return await self.manager.add_note(content, description)
                await self.manager.edit_note(note_id, content, description)
                return note_id
            except Exception as e:
                self.logger.error(f"Notes: save failed: {e}")
                return None

        def _on_saved(self, note_id):
            self.btn_save.set_sensitive(True)
            if note_id is None:
                self._show_toast(
                    "Could not save the note", undo=False, seconds=TOAST_SECONDS
                )
                return
            self._selected_id = note_id
            self._mode = "view"
            self.refresh_notes()

        # ------------------------------------------------------------------
        # Copy, delete, undo, clear
        # ------------------------------------------------------------------
        def _on_copy_clicked(self, *_):
            note = self._selected_note()
            if note is None:
                return
            _stamp, title = split_content(note.content)
            text = f"{title}\n\n{note.description}" if note.description else title
            display = Gdk.Display.get_default()
            if display is None:
                return
            self._active_clipboard_provider = Gdk.ContentProvider.new_for_value(text)
            display.get_clipboard().set_content(self._active_clipboard_provider)
            self._show_toast("Copied to clipboard", undo=False, seconds=TOAST_SECONDS)

        def _on_delete_clicked(self, *_):
            note = self._selected_note()
            if note is None or self._editing():
                return
            rows = self._visible_rows()
            row = self._row_for_id(note.id)
            index = rows.index(row) if row in rows else 0
            self.run_in_async_task(self.manager.delete_note(note.id))
            self._notes.pop(note.id, None)
            self._selected_id = None
            if row is not None:
                self.listbox.remove(row)
            rows = self._visible_rows()
            if rows:
                self._select_and_scroll(rows[min(index, len(rows) - 1)])
            else:
                self._show_empty()
            self._undo_note = note
            self._show_toast("Note deleted", undo=True, seconds=UNDO_SECONDS)

        def _on_undo_clicked(self, *_):
            note = self._undo_note
            self._undo_note = None
            self._hide_toast()
            if note is None:
                return
            self.run_in_async_task(
                self.manager.restore_note(note), on_finish=self._on_restored
            )

        def _on_restored(self, note_id):
            self._selected_id = note_id
            self._mode = "view"
            self.refresh_notes()

        def _on_clear_clicked(self, *_):
            if not self._notes:
                return
            count = len(self._notes)
            self.confirm_label.set_label(
                f"Delete all {count} notes?" if count > 1 else "Delete the note?"
            )
            self.confirm_revealer.set_reveal_child(
                not self.confirm_revealer.get_reveal_child()
            )

        def _on_clear_confirmed(self, *_):
            self.confirm_revealer.set_reveal_child(False)
            self._undo_note = None
            self._hide_toast()
            self._selected_id = None
            self._mode = "view"
            self.run_in_async_task(
                self.manager.clear_notes(), on_finish=lambda _r: self.refresh_notes()
            )

        def _show_toast(self, message, undo, seconds):
            if self._toast_source is not None:
                GLib.source_remove(self._toast_source)
            self.toast_label.set_label(message)
            self.toast_undo.set_visible(undo)
            self.toast_revealer.set_reveal_child(True)
            self._toast_source = GLib.timeout_add_seconds(seconds, self._expire_toast)

        def _expire_toast(self):
            self._toast_source = None
            self._undo_note = None
            self.toast_revealer.set_reveal_child(False)
            return False

        def _hide_toast(self):
            if self._toast_source is not None:
                GLib.source_remove(self._toast_source)
                self._toast_source = None
            self.toast_revealer.set_reveal_child(False)

        # ------------------------------------------------------------------
        # Reminders
        # ------------------------------------------------------------------
        def _refresh_reminder_card(self, note):
            active = note.remind_remaining > 0 and note.remind_at > 0
            summary = reminder_summary(
                note.remind_at, note.remind_interval, note.remind_remaining
            )
            self.reminder_status.set_label(summary if active else "No reminder set")
            if active:
                self.reminder_icon.add_css_class("active")
            else:
                self.reminder_icon.remove_css_class("active")
            self.btn_reminder_clear.set_sensitive(active)
            self.spin_interval.set_value(
                max(1, note.remind_interval // 60) if note.remind_interval else 5
            )
            self.spin_count.set_value(
                note.remind_remaining if note.remind_remaining > 0 else 1
            )
            self._sync_chips()

        def _on_chip_clicked(self, _button, minutes):
            self.spin_interval.set_value(minutes)

        def _sync_chips(self):
            value = int(self.spin_interval.get_value())
            for minutes, chip in self._chips.items():
                if minutes == value:
                    chip.add_css_class("selected")
                else:
                    chip.remove_css_class("selected")

        def _on_set_reminder_clicked(self, *_):
            note = self._selected_note()
            if note is None:
                return
            interval = int(self.spin_interval.get_value()) * 60
            count = int(self.spin_count.get_value())
            remind_at = int(time.time()) + interval
            self._selected_id = note.id
            self.run_in_async_task(
                self.manager.set_reminder(note.id, remind_at, interval, count),
                on_finish=lambda _r: self.refresh_notes(),
            )
            self._show_toast("Reminder set", undo=False, seconds=TOAST_SECONDS)

        def _on_clear_reminder_clicked(self, *_):
            note = self._selected_note()
            if note is None:
                return
            self._selected_id = note.id
            self.run_in_async_task(
                self.manager.set_reminder(note.id, 0, 0, 0),
                on_finish=lambda _r: self.refresh_notes(),
            )
            self._show_toast("Reminder cleared", undo=False, seconds=TOAST_SECONDS)

        def _check_reminders(self) -> bool:
            self.run_in_async_task(
                self._process_due_reminders(), on_finish=self._on_reminders_done
            )
            return True

        async def _process_due_reminders(self):
            """Advances every due reminder and returns what has to be announced."""
            now = int(time.time())
            announcements = []
            for note in await self.manager.get_due_reminders(now):
                _stamp, title = split_content(note.content)
                body = (
                    f"{title}\n{note.description}".strip()
                    if note.description
                    else title
                )
                announcements.append(body)
                remaining = note.remind_remaining - 1
                next_at = (now + note.remind_interval) if remaining > 0 else 0
                await self.manager.set_reminder(
                    note.id, next_at, note.remind_interval, remaining
                )
            return announcements

        def _on_reminders_done(self, announcements):
            for body in announcements:
                self.run_in_thread(
                    self.notify_send,
                    title="Note Reminder",
                    message=body,
                    icon="alarm-symbolic",
                )
            if self.dialog is None or not self.dialog.get_visible():
                return
            if announcements:
                self.refresh_notes()
            elif self._mode == "view" and self._selected_note() is not None:
                note = self._selected_note()
                self.reminder_status.set_label(
                    reminder_summary(
                        note.remind_at, note.remind_interval, note.remind_remaining
                    )
                    or "No reminder set"
                )

        def about(self):
            """
            This plugin provides a dialog centered on the screen for viewing,
            adding, searching and managing personal notes persisted to an SQLite
            database, with reminders and undo. It is opened from the panel systray.
            """
            return self.about.__doc__

    return MenuNotes
