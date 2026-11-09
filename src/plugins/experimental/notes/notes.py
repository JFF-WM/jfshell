def get_plugin_metadata(panel):
    about = "Notes plugin with quick-access, search, and inline edit/delete."

    id = "org.jfshell.plugin.notes"
    default_container = "top-panel-systray"
    container, id = panel.config_handler.get_plugin_container(default_container, id)

    return {
        "id": id,
        "name": "Notes",
        "version": "1.0.0",
        "enabled": True,
        "index": 1,
        "container": container,
        "deps": ["css_generator"],
        "description": about,
    }


def get_plugin_class():
    import aiosqlite
    import datetime
    import asyncio
    from typing import List, Tuple
    from gi.repository import Gio, Gtk, GLib  # pyright: ignore
    from src.plugins.core._base import BasePlugin

    class NotesManager:
        """
        Manages all asynchronous database operations for the Notes plugin.
        This class abstracts the low-level aiosqlite interactions, providing a clean,
        async interface for CRUD operations on notes.
        """

        def __init__(self, path_handler, db_path):
            """
            Initializes the NotesManager with path handlers.
            Args:
                path_handler: The system path utility handler.
                db_path: The full path to the SQLite database file.
            """
            self.path_handler = path_handler
            self.db_path = db_path

        async def initialize_db(self):
            """Initializes the SQLite database schema if it doesn't exist."""
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS notes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        description TEXT DEFAULT '',
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        remind_at INTEGER DEFAULT 0,
                        remind_interval INTEGER DEFAULT 0,
                        remind_remaining INTEGER DEFAULT 0
                    )
                """)
                cursor = await db.execute("PRAGMA table_info(notes)")
                columns = [row[1] for row in await cursor.fetchall()]
                if "description" not in columns:
                    await db.execute(
                        "ALTER TABLE notes ADD COLUMN description TEXT DEFAULT ''"
                    )
                if "remind_at" not in columns:
                    await db.execute(
                        "ALTER TABLE notes ADD COLUMN remind_at INTEGER DEFAULT 0"
                    )
                if "remind_interval" not in columns:
                    await db.execute(
                        "ALTER TABLE notes ADD COLUMN remind_interval INTEGER DEFAULT 0"
                    )
                if "remind_remaining" not in columns:
                    await db.execute(
                        "ALTER TABLE notes ADD COLUMN remind_remaining INTEGER DEFAULT 0"
                    )
                await db.commit()

        async def get_notes(self) -> list[tuple[int, str, str, int, int, int]]:
            """Fetches all notes, ordered by timestamp descending."""
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT id, content, description, remind_at, remind_interval, remind_remaining FROM notes ORDER BY timestamp DESC"
                )
                return await cursor.fetchall()  # pyright: ignore

        async def add_note(self, content: str, description: str = ""):
            """Inserts a new note into the database."""
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO notes (content, description) VALUES (?, ?)",
                    (content, description),
                )
                await db.commit()

        async def delete_note(self, note_id: int):
            """Deletes a note by its ID."""
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
                await db.commit()

        async def edit_note(
            self, note_id: int, new_content: str, new_description: str = ""
        ):
            """Updates the content, description, and timestamp of an existing note."""
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE notes SET content = ?, description = ?, timestamp = CURRENT_TIMESTAMP WHERE id = ?",
                    (new_content, new_description, note_id),
                )
                await db.commit()

        async def set_reminder(
            self, note_id: int, remind_at: int, interval: int, remaining: int
        ):
            """Sets reminder timing and repeat limits."""
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE notes SET remind_at = ?, remind_interval = ?, remind_remaining = ? WHERE id = ?",
                    (remind_at, interval, remaining, note_id),
                )
                await db.commit()

        async def clear_notes(self):
            """Deletes all notes from the database."""
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM notes")
                await db.commit()

    def get_notes_sync(
        path_handler, db_path
    ) -> List[Tuple[int, str, str, int, int, int]]:
        """Synchronous wrapper to fetch notes, blocking the calling thread."""

        async def _fetch_notes():
            manager = NotesManager(path_handler, db_path)
            await manager.initialize_db()
            return await manager.get_notes()

        return asyncio.run(_fetch_notes())

    class MenuNotes(BasePlugin):
        """
        GUI client for the Notes plugin, displayed in a Gtk.Popover.
        Handles note creation, display, searching, editing, and deletion.
        """

        def __init__(self, panel_instance):
            """Initializes the plugin and loads configurable settings."""
            super().__init__(panel_instance)

        def on_start(self):
            self.db_path = self.path_handler.get_data_path("db/notes/notes.db")
            self.notes_manager = NotesManager(self.path_handler, self.db_path)
            self.popover_notes = None
            self.find_text_using_button = {}
            self.note_row_widgets = {}
            self.is_editing = False
            self.row_content = None
            self.listbox = None
            self.reminder_timer_id = None
            self.popover_max_height = self.get_plugin_setting_add_hint(
                ["popover_max_height"],
                600,
                "The maximum height (in pixels) the notes list can grow before scrolling starts.",
            )
            self.popover_min_height = self.get_plugin_setting_add_hint(
                ["popover_min_height"],
                50,
                "The minimum height (in pixels) for the scrolling list, even if empty.",
            )
            self.popover_main_margin = self.get_plugin_setting_add_hint(
                ["popover_main_margin"],
                10,
                "The margin (in pixels) around the main popover content box (Top, Bottom, Start, End).",
            )
            self.search_placeholder = self.get_plugin_setting_add_hint(
                ["search_placeholder_text"],
                "Search notes...",
                "The placeholder text displayed in the note search bar.",
            )
            self.add_entry_placeholder = self.get_plugin_setting_add_hint(
                ["add_entry_placeholder_text"],
                "Add new note title...",
                "The placeholder text displayed in the new note entry field.",
            )
            self.add_desc_placeholder = self.get_plugin_setting_add_hint(
                ["add_desc_placeholder_text"],
                "Add note description (optional)...",
                "The placeholder text displayed in the new note description entry field.",
            )
            self.add_button_label = self.get_plugin_setting_add_hint(
                ["add_button_label"],
                "Add",
                "The label text for the 'Add' button.",
            )
            self.clear_button_label = self.get_plugin_setting_add_hint(
                ["clear_button_label"],
                "Clear All",
                "The label text for the 'Clear All' button.",
            )
            self.delete_icon_name = self.get_plugin_setting_add_hint(
                ["delete_icon"],
                "edit-delete",
                "The icon name used for the delete button on a note row. Falls back to 'edit-delete'.",
            )
            self.edit_icon_name = self.get_plugin_setting_add_hint(
                ["edit_icon"],
                "document-edit",
                "The icon name used for the edit button on a note row. Falls back to 'document-edit'.",
            )
            self.alarm_icon_name = self.get_plugin_setting_add_hint(
                ["alarm_icon"],
                "alarm-symbolic",
                "The icon name used for the reminder button on a note row.",
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

            self.button_open_popover = Gtk.Button()
            self.button_open_popover.add_css_class("notes-menubutton")
            self.button_open_popover.connect("clicked", self.open_popover_notes)
            self.main_widget = (self.button_open_popover, "append")
            self.gtk_helper.add_cursor_effect(self.button_open_popover)
            self.plugins["css_generator"].install_css("notes.css")

            self.reminder_timer_id = GLib.timeout_add_seconds(10, self._check_reminders)

        def on_disable(self):
            if self.reminder_timer_id is not None:
                GLib.source_remove(self.reminder_timer_id)
                self.reminder_timer_id = None

        def _check_reminders(self) -> bool:
            self.run_in_thread(self._process_due_reminders)
            return True

        def _process_due_reminders(self):
            import time

            now = int(time.time())
            notes = get_notes_sync(self.path_handler, self.db_path)
            for note_id, content, description, remind_at, interval, remaining in notes:
                if remaining > 0 and 0 < remind_at <= now:
                    parts = content.split()
                    title = " ".join(parts[2:]) if len(parts) >= 3 else content
                    body = f"{title}\n{description}".strip() if description else title
                    self.notify_send(
                        title="Note Reminder",
                        message=body,
                        icon="alarm-symbolic",
                    )
                    next_remaining = remaining - 1
                    next_remind_at = (now + interval) if next_remaining > 0 else 0
                    asyncio.run(
                        self.notes_manager.set_reminder(
                            note_id, next_remind_at, interval, next_remaining
                        )
                    )
                    self.schedule_in_gtk_thread(self.update_notes_list)

        def create_popover_notes(self):
            """Constructs the primary UI components of the popover."""
            self.popover_notes = Gtk.Popover.new()
            self.popover_notes.add_css_class("notes-popover")
            self.popover_notes.set_has_arrow(False)
            self.popover_notes.connect("closed", self.popover_is_closed)
            self.popover_notes.connect("notify::visible", self.popover_is_open)
            show_searchbar_action = Gio.SimpleAction.new("show_searchbar")
            show_searchbar_action.connect(
                "activate", self.on_show_searchbar_action_actived
            )
            self.obj.add_action(show_searchbar_action)
            self.scrolled_window = Gtk.ScrolledWindow()
            self.scrolled_window.add_css_class("notes-scrolledwindow")
            self.scrolled_window.set_vexpand(False)
            self.scrolled_window.set_propagate_natural_height(True)
            self.scrolled_window.set_propagate_natural_width(True)
            self.scrolled_window.set_min_content_height(self.popover_min_height)
            self.scrolled_window.set_max_content_height(self.popover_max_height)
            self.scrolled_window.set_policy(
                Gtk.PolicyType.NEVER,
                Gtk.PolicyType.AUTOMATIC,
            )
            self.main_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 10)
            self.main_box.add_css_class("notes-main-box")
            self.main_box.set_size_request(380, -1)  # wide enough for placeholders
            self.main_box.set_margin_top(self.popover_main_margin)
            self.main_box.set_margin_bottom(self.popover_main_margin)
            self.main_box.set_margin_start(self.popover_main_margin)
            self.main_box.set_margin_end(self.popover_main_margin)
            self.searchbar = Gtk.SearchEntry.new()
            self.searchbar.add_css_class("notes-searchbar")
            self.searchbar.set_placeholder_text(self.search_placeholder)
            self.searchbar.connect("search_changed", self.on_search_entry_changed)
            self.main_box.append(self.searchbar)

            self.entry_add_note = Gtk.Entry.new()
            self.entry_add_note.add_css_class("notes-entry-add")
            self.entry_add_note.set_placeholder_text(self.add_entry_placeholder)
            self.entry_add_note.connect("activate", self.on_add_note)
            self.main_box.append(self.entry_add_note)

            # Secondary Description Entry Widget
            self.entry_add_desc = Gtk.Entry.new()
            self.entry_add_desc.add_css_class("notes-entry-add-desc")
            self.entry_add_desc.set_placeholder_text(self.add_desc_placeholder)
            self.entry_add_desc.connect("activate", self.on_add_note)
            self.main_box.append(self.entry_add_desc)

            buttons_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
            buttons_box.add_css_class("notes-buttons-box")
            buttons_box.set_homogeneous(True)
            self.button_add = Gtk.Button.new_with_label(self.add_button_label)
            self.button_add.add_css_class("notes_button_add")
            self.button_add.connect("clicked", self.on_add_note)
            self.gtk_helper.add_cursor_effect(self.button_add)
            buttons_box.append(self.button_add)
            self.button_clear = Gtk.Button.new_with_label(self.clear_button_label)
            self.button_clear.add_css_class("notes_button_clear")
            self.button_clear.connect("clicked", self.clear_notes)
            self.gtk_helper.add_cursor_effect(self.button_clear)
            buttons_box.append(self.button_clear)
            self.main_box.append(buttons_box)
            self.listbox = Gtk.ListBox.new()
            self.listbox.add_css_class("notes-listbox")
            self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
            self.listbox.set_show_separators(True)
            self.listbox.set_filter_func(self.on_filter_invalidate)
            self.scrolled_window.set_child(self.listbox)
            self.main_box.append(self.scrolled_window)
            self.popover_notes.set_child(self.main_box)
            self.run_in_thread(self.update_notes_list)
            self.popover_notes.set_parent(self.button_open_popover)
            return self.popover_notes

        def clear_notes(self, *_):
            """Displays a confirmation dialog before clearing all notes."""
            dialog = Gtk.AlertDialog(
                message="Clear all notes?",
                detail="This will permanently delete all your notes. Are you sure?",
                buttons=["_Cancel", f"_{self.clear_button_label}"],
            )
            dialog.set_default_button(1)
            dialog.set_cancel_button(0)
            dialog.choose(
                callback=self.on_clear_confirmation_response,
            )

        def on_clear_confirmation_response(self, dialog, result, *_):
            """Callback to execute clear operation if confirmed by the user."""
            try:
                response = dialog.choose_finish(result)
                if response == 1:
                    asyncio.run(self.async_clear_notes())
                    self.update_notes_list()
            except Exception as e:
                self.logger.error(f"Dialog error: {e}")

        def update_notes_list(self):
            """
            Fetches notes from the database and rebuilds the Gtk.ListBox content.
            Complexity:
                Time: O(N) where N is the number of notes, dominated by widget creation.
                Space: O(N) for storing note widget references.
            """
            if self.listbox is not None:
                self._gtk_helper.clear_listbox(self.listbox)
            self.is_editing = False
            self.note_row_widgets = {}
            notes = get_notes_sync(self.path_handler, self.db_path)
            # Dynamic height: hide the (empty) list area so the popover only
            # is as tall as its entries/buttons; it grows with the notes up to
            # popover_max_height, then scrolls.
            sw = getattr(self, "scrolled_window", None)
            if sw is not None:
                sw.set_visible(any(n[1] for n in notes))
            delete_button_icon = self.gtk_helper.icon_exist(
                self.delete_icon_name, ["edit-delete"]
            )
            edit_button_icon = self.gtk_helper.icon_exist(
                self.edit_icon_name, ["document-edit"]
            )
            alarm_button_icon = self.gtk_helper.icon_exist(
                self.alarm_icon_name, ["alarm-symbolic"]
            )
            for note_id, content, description, remind_at, interval, remaining in notes:
                if not content:
                    continue
                row = Gtk.ListBoxRow()
                row.add_css_class("notes-listbox-row")
                row_hbox = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 5)
                row_hbox.add_css_class("notes-row-hbox")
                row_hbox.set_margin_start(10)
                row_hbox.set_margin_end(10)
                row_hbox.note_id = note_id  # pyright: ignore
                row.set_child(row_hbox)

                spacer = Gtk.Label(label="  ")
                spacer.add_css_class("notes-spacer-label")
                row_hbox.append(spacer)

                text_vbox = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
                text_vbox.set_hexpand(True)
                text_vbox.set_valign(Gtk.Align.CENTER)
                row_hbox.append(text_vbox)

                note_label = Gtk.Label.new()
                note_label.add_css_class("notes-note-label")
                note_label.set_wrap(True)
                parts = content.split()
                if len(parts) >= 3:
                    timestamp_str = f"{parts[0]} {parts[1]}"
                    message = " ".join(parts[2:])
                    if message.strip():
                        markup = f'<span font="DejaVu Sans Mono"><b>{timestamp_str}</b> {message}</span>'
                        note_label.original_content = message  # pyright: ignore
                    else:
                        markup = f'<span font="DejaVu Sans Mono">{content}</span>'
                        note_label.original_content = content  # pyright: ignore
                else:
                    message = content
                    markup = f'<span font="DejaVu Sans Mono">{content}</span>'
                    note_label.original_content = content  # pyright: ignore
                note_label.set_markup(markup)
                note_label.set_halign(Gtk.Align.START)
                note_label.set_xalign(0)
                text_vbox.append(note_label)

                # Description Widget (subtitle)
                desc_label = Gtk.Label.new()
                desc_label.add_css_class("notes-desc-label")
                desc_label.set_wrap(True)
                desc_label.set_halign(Gtk.Align.START)
                desc_label.set_xalign(0)
                desc_label.original_desc = description or ""  # pyright: ignore
                if description and description.strip():
                    desc_label.set_markup(
                        f'<span size="small" alpha="70%">{GLib.markup_escape_text(description)}</span>'
                    )
                    desc_label.set_visible(True)
                else:
                    desc_label.set_visible(False)
                text_vbox.append(desc_label)

                # Alarm / Reminder Button
                alarm_button = Gtk.Button()
                alarm_button.add_css_class("notes_button_alarm")
                alarm_button.set_icon_name(alarm_button_icon)
                if remaining > 0:
                    alarm_button.set_tooltip_text(
                        f"Reminder active: {remaining} remaining"
                    )
                    alarm_button.add_css_class("suggested-action")
                else:
                    alarm_button.set_tooltip_text("Set reminder")
                self.gtk_helper.add_cursor_effect(alarm_button)
                alarm_button.connect(
                    "clicked",
                    self.on_open_reminder_popover,
                    note_id,
                    interval,
                    remaining,
                )
                row_hbox.append(alarm_button)

                edit_button = Gtk.Button()
                edit_button.add_css_class("notes_button_edit")
                edit_button.set_icon_name(edit_button_icon)
                self.gtk_helper.add_cursor_effect(edit_button)
                edit_button.connect("clicked", self.on_start_edit_note, note_id)
                row_hbox.append(edit_button)

                delete_button = Gtk.Button()
                delete_button.add_css_class("notes_button_delete")
                delete_button.set_icon_name(delete_button_icon)
                self.gtk_helper.add_cursor_effect(delete_button)
                delete_button.connect("clicked", self.on_delete_note)
                row_hbox.append(delete_button)

                self.listbox.append(row)  # pyright: ignore
                self.find_text_using_button[delete_button] = row_hbox
                self.note_row_widgets[note_id] = {
                    "row": row,
                    "hbox": row_hbox,
                    "text_vbox": text_vbox,
                    "label": note_label,
                    "desc_label": desc_label,
                    "alarm_button": alarm_button,
                    "edit_button": edit_button,
                    "delete_button": delete_button,
                }

        def on_open_reminder_popover(
            self, button, note_id, cur_interval, cur_remaining
        ):
            """Displays an inline setup popover to configure interval and repetition count."""
            popover = Gtk.Popover.new()
            popover.set_parent(button)

            box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.set_margin_start(8)
            box.set_margin_end(8)

            title = Gtk.Label.new("Set Reminder")
            title.add_css_class("title-4")
            box.append(title)

            lbl_int = Gtk.Label.new("Frequency (minutes):")
            lbl_int.set_halign(Gtk.Align.START)
            box.append(lbl_int)

            adj_int = Gtk.Adjustment(
                value=max(1, cur_interval // 60) if cur_interval else 5,
                lower=1,
                upper=1440,
                step_increment=1,
                page_increment=5,
            )
            spin_int = Gtk.SpinButton.new(adj_int, 1.0, 0)
            box.append(spin_int)

            lbl_cnt = Gtk.Label.new("Times to appear:")
            lbl_cnt.set_halign(Gtk.Align.START)
            box.append(lbl_cnt)

            adj_cnt = Gtk.Adjustment(
                value=cur_remaining if cur_remaining > 0 else 1,
                lower=1,
                upper=100,
                step_increment=1,
                page_increment=1,
            )
            spin_cnt = Gtk.SpinButton.new(adj_cnt, 1.0, 0)
            box.append(spin_cnt)

            btn_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 6)
            btn_save = Gtk.Button.new_with_label("Set")
            btn_save.add_css_class("suggested-action")
            self.gtk_helper.add_cursor_effect(btn_save)

            btn_cancel = Gtk.Button.new_with_label("Clear")
            self.gtk_helper.add_cursor_effect(btn_cancel)

            def _on_save(_):
                import time

                interval_sec = int(spin_int.get_value()) * 60
                count = int(spin_cnt.get_value())
                remind_at = int(time.time()) + interval_sec
                asyncio.run(
                    self.notes_manager.set_reminder(
                        note_id, remind_at, interval_sec, count
                    )
                )
                popover.popdown()
                self.update_notes_list()

            def _on_clear(_):
                asyncio.run(self.notes_manager.set_reminder(note_id, 0, 0, 0))
                popover.popdown()
                self.update_notes_list()

            btn_save.connect("clicked", _on_save)
            btn_cancel.connect("clicked", _on_clear)
            btn_box.append(btn_save)
            btn_box.append(btn_cancel)
            box.append(btn_box)

            popover.set_child(box)
            popover.popup()

        def on_start_edit_note(self, button, note_id):
            """Swaps the note title and description widgets for editable entry fields."""
            if self.is_editing:
                return
            if note_id not in self.note_row_widgets:
                self.logger.error(f"Cannot find widgets for note_id: {note_id}")
                return
            self.is_editing = True
            widgets = self.note_row_widgets[note_id]
            text_vbox = widgets["text_vbox"]
            old_label = widgets["label"]
            old_desc = widgets["desc_label"]

            initial_text = old_label.original_content
            initial_desc = old_desc.original_desc

            edit_entry = Gtk.Entry.new()
            edit_entry.add_css_class("notes-edit-entry")
            edit_entry.set_text(initial_text)
            edit_entry.set_hexpand(True)
            edit_entry.set_halign(Gtk.Align.FILL)

            edit_desc_entry = Gtk.Entry.new()
            edit_desc_entry.add_css_class("notes-edit-desc-entry")
            edit_desc_entry.set_placeholder_text("Description...")
            edit_desc_entry.set_text(initial_desc)
            edit_desc_entry.set_hexpand(True)
            edit_desc_entry.set_halign(Gtk.Align.FILL)

            edit_entry.connect("activate", lambda *_: edit_desc_entry.grab_focus())
            edit_desc_entry.connect(
                "activate", self.on_finish_edit_note, note_id, widgets
            )

            old_label.set_visible(False)
            old_desc.set_visible(False)

            text_vbox.append(edit_entry)
            text_vbox.append(edit_desc_entry)

            edit_entry.grab_focus()
            edit_entry.select_region(0, -1)

            widgets["alarm_button"].set_sensitive(False)
            widgets["edit_button"].set_sensitive(False)
            widgets["delete_button"].set_sensitive(False)
            widgets["edit_entry"] = edit_entry
            widgets["edit_desc_entry"] = edit_desc_entry

        def on_finish_edit_note(self, entry, note_id, widgets):
            """Saves the edited title and description to the database."""
            edit_entry = widgets["edit_entry"]
            edit_desc = widgets["edit_desc_entry"]

            new_content_message = edit_entry.get_text().strip()
            new_description = edit_desc.get_text().strip()

            if not new_content_message:
                self.is_editing = False
                self.update_notes_list()
                return

            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_full_content = f"{now} {new_content_message}"
            asyncio.run(
                self.async_edit_note(note_id, new_full_content, new_description)
            )
            self.is_editing = False
            self.update_notes_list()

        async def async_edit_note(
            self, note_id: int, new_content: str, new_description: str = ""
        ):
            """Asynchronously edits a note."""
            await self.notes_manager.initialize_db()
            await self.notes_manager.edit_note(note_id, new_content, new_description)

        async def async_add_note(self, content, description=""):
            """Asynchronously adds a note."""
            await self.notes_manager.initialize_db()
            await self.notes_manager.add_note(content, description)

        def on_add_note(self, *_):
            """Handler to add a new note from entry and description fields."""
            content = self.entry_add_note.get_text().strip()
            description = self.entry_add_desc.get_text().strip()
            if content and not self.is_editing:
                now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                content_with_time = f"{now} {content}"
                asyncio.run(self.async_add_note(content_with_time, description))
                self.entry_add_note.set_text("")
                self.entry_add_desc.set_text("")
                self.update_notes_list()

        async def async_delete_note(self, note_id):
            """Asynchronously deletes a note."""
            await self.notes_manager.initialize_db()
            await self.notes_manager.delete_note(note_id)

        def on_delete_note(self, button):
            """Deletes the note corresponding to the clicked button and refreshes the list."""
            if self.is_editing:
                return
            if button not in self.find_text_using_button:
                self.logger.info("Note delete button not found")
                return
            row_hbox = self.find_text_using_button[button]
            note_id = row_hbox.note_id
            if note_id:
                asyncio.run(self.async_delete_note(note_id))
                self.update_notes_list()

        async def async_clear_notes(self):
            """Asynchronously clears all notes."""
            await self.notes_manager.initialize_db()
            await self.notes_manager.clear_notes()

        def open_popover_notes(self, *_):
            """Opens or closes the notes popover."""
            if self.popover_notes and self.popover_notes.is_visible():
                self.popover_notes.popdown()
            elif self.popover_notes and not self.popover_notes.is_visible():
                self.popover_notes.popup()
            else:
                self.popover_notes = self.create_popover_notes()
                GLib.timeout_add(100, self.popover_notes.popup)

        def popover_is_open(self, *_):
            """Adjusts keyboard mode when the popover opens."""
            self.layer_shell.set_keyboard_mode(
                self.obj.top_panel, self.layer_shell.KeyboardMode.ON_DEMAND
            )

        def popover_is_closed(self, *_):
            """Adjusts keyboard mode when the popover closes."""
            self.layer_shell.set_keyboard_mode(
                self.obj.top_panel, self.layer_shell.KeyboardMode.NONE
            )

        def on_show_searchbar_action_actived(self, action, parameter):
            """Shows the search bar when the action is triggered."""
            self.searchbar.set_search_mode(True)  # pyright: ignore

        def on_search_entry_changed(self, searchentry):
            """Invalidates the list filter when the search text changes."""
            self.listbox.invalidate_filter()  # pyright: ignore

        def on_filter_invalidate(self, row):
            """Filter function for the ListBox matching against title and description."""
            search_text = self.searchbar.get_text().strip().lower()
            if not search_text:
                return True
            row_hbox = row.get_child()
            title_text = ""
            desc_text = ""
            for child in row_hbox:
                if isinstance(child, Gtk.Box):
                    for sub in child:
                        if isinstance(sub, Gtk.Label):
                            if hasattr(sub, "original_content"):
                                title_text = sub.original_content.lower()
                            elif hasattr(sub, "original_desc"):
                                desc_text = sub.original_desc.lower()
            combined = f"{title_text} {desc_text}"
            return search_text in combined

        def about(self):
            """
            This plugin provides a quick-access Gtk.Popover for viewing, adding,
            searching, and managing personal notes persisted to an SQLite database.
            It is designed for rapid interaction from the panel systray.
            """
            return self.about.__doc__

        def code_explanation(self):
            """
            This plugin implements the Model-View-Controller (MVC) pattern:
            1. Model (`NotesManager`): Handles asynchronous database I/O using aiosqlite.
            2. View/Controller (`MenuNotes`): Manages the GTK UI (Gtk.Popover, Gtk.ListBox) and user interaction.
            Architectural Enhancements:
            - **Configuration Externalization**: Core constants (sizing, labels, icons) are exposed via `self.get_plugin_setting_add_hint` in `__init__`, making the plugin robust to user preference changes.
            - **Optimized Layout Setting**: GTK size negotiation properties (`set_vexpand`, `set_propagate_natural_height`, `set_max_content_height`) are set only once in `create_popover_notes`, preventing redundant calls during list updates (`update_notes_list`) and ensuring correct initial size calculation.
            - **Asynchronous Data Access**: Database operations are safely offloaded using `asyncio.run` within thread wrappers, preventing UI thread blocking.
            """
            return self.code_explanation.__doc__

    return MenuNotes
