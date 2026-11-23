def get_plugin_metadata(_):
    """
    Returns the metadata for the Clipboard Client plugin.
    """
    about = (
        "This plugin serves as the graphical user interface (GUI) for the"
        "asynchronous clipboard history server. It allows users to view,"
        "search, and manage their clipboard history through a dialog centered "
        "on the screen."
    )
    return {
        "id": "org.jfshell.plugin.clipboard",
        "name": "Clipboard Client",
        "version": "2.0.0",
        "enabled": True,
        "container": "top-panel-systray",
        "index": 5,
        "deps": ["clipboard_server", "css_generator"],
        "description": about,
    }


def get_plugin_class():
    """
    Returns the ClipboardClient class with necessary imports scoped inside.
    """
    import os
    import mimetypes
    import shutil
    import subprocess
    from pathlib import Path
    from urllib.parse import unquote, urlparse
    from gi.repository import GdkPixbuf, Gtk, Gdk, Pango
    from src.plugins.core._base import BasePlugin
    from .clipboard_server import get_plugin_class as get_server_class
    from ._clipboard_template import Helpers
    from ._clipboard_format import format_age, format_size, first_line
    from ._clipboard_helpers import ClipboardHelpers, ClipboardManager
    import gc

    class ClipboardClient(BasePlugin):
        """
        GTK-based clipboard client handling history display and interaction.
        """

        def __init__(self, panel_instance):
            super().__init__(panel_instance)
            self.panel = panel_instance

        def on_start(self):
            self.manager = ClipboardManager(self.panel, get_server_class)
            self.dialog = None
            self.listbox = None
            self.clipboard_helper = ClipboardHelpers(self)
            self._is_populating = False
            self._selected_id = None

            self.dialog_width = self.get_plugin_setting_add_hint(
                ["client", "dialog_width"], 720, "Clipboard dialog width"
            )
            self.dialog_height = self.get_plugin_setting_add_hint(
                ["client", "dialog_height"], 560, "Clipboard dialog height"
            )
            self.row_thumbnail_size = self.get_plugin_setting_add_hint(
                ["client", "row_thumbnail_size"], 40, "Image thumbnail size in the list"
            )
            self.preview_text_length = self.get_plugin_setting_add_hint(
                ["client", "preview_text_length"], 50, "Text preview limit"
            )
            self.preview_max_chars = self.get_plugin_setting_add_hint(
                ["client", "preview_max_chars"], 20000, "Max characters in the preview"
            )

            helpers = Helpers(self)
            helpers.apply_hints()
            self.main_icon = self.get_plugin_setting(
                ["main_icon"], "edit-paste-symbolic"
            )
            icon = self.gtk_helper.icon_exist
            self._icons = {
                "text": icon("text-x-generic-symbolic", ["document-symbolic"]),
                "image": icon("image-x-generic-symbolic", ["image-x-generic"]),
                "copy": icon("edit-copy-symbolic", ["edit-copy"]),
                "pin": icon("pin-symbolic", ["view-pin-symbolic"]),
                "delete": icon("user-trash-symbolic", ["edit-delete-symbolic"]),
                "close": icon("window-close-symbolic", ["window-close"]),
            }
            self.run_in_async_task(self.manager.initialize())
            self.create_clipboard_ui()
            self.plugins["css_generator"].install_css("clipboard.css")
            self.plugins["css_generator"].install_css("clipboard-dialog.css")

        def _resolve_local_path(self, content: str) -> str | None:
            """Return the existing local file that ``content`` points to, else None.

            Accepts a plain path, a ``file://`` URI, and the file manager format
            (``copy`` or ``cut`` on the first line, then the URI). Several files
            are not resolved, they stay plain text.
            """
            if not content:
                return None
            lines = [line.strip() for line in content.strip().splitlines()]
            lines = [line for line in lines if line]
            if len(lines) == 2 and lines[0] in ("copy", "cut"):
                lines = lines[1:]
            if len(lines) != 1 or not lines[0].startswith(("file://", "/")):
                return None
            clean_content = lines[0]
            path_candidate = (
                unquote(urlparse(clean_content).path)
                if clean_content.startswith("file://")
                else clean_content
            )
            return path_candidate if Path(path_candidate).exists() else None

        def _image_as_png_bytes(self, path: str) -> bytes:
            """PNG bytes of the image at ``path``: most paste targets only take image/png."""
            if mimetypes.guess_type(path)[0] == "image/png":
                with open(path, "rb") as image_file:
                    return image_file.read()
            file_obj = self.gio.File.new_for_path(path)
            texture = Gdk.Texture.new_from_file(file_obj)
            return texture.save_to_png_bytes().get_data()

        def _copy_image_with_wl_copy(self, path: str) -> bool:
            """Put the image on the clipboard as image/png only, no path text."""
            if not shutil.which("wl-copy"):
                return False
            try:
                subprocess.run(
                    ["wl-copy", "--type", "image/png"],
                    input=self._image_as_png_bytes(path),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=True,
                )
                return True
            except Exception as e:
                self.logger.error(f"Clipboard: wl-copy image copy failed: {e}")
                return False

        def copy_to_clipboard(self, content):
            display = Gdk.Display.get_default()
            if not display:
                return
            clipboard = display.get_clipboard()
            real_path = self._resolve_local_path(content)

            if real_path and self.clipboard_helper.is_image_content(real_path):
                # The server would capture this copy as a new history item.
                self.manager.server.mark_self_copy(content)
                if self._copy_image_with_wl_copy(real_path):
                    return
                # Fallback: offer the texture only. The path must not be added as
                # text, or paste targets that prefer text/plain get the path.
                try:
                    file_obj = self.gio.File.new_for_path(real_path)
                    texture = Gdk.Texture.new_from_file(file_obj)
                    clipboard.set_texture(texture)
                except Exception as e:
                    self.logger.error(f"Clipboard: Native image copy failed: {e}")
            else:
                self._active_clipboard_provider = Gdk.ContentProvider.new_for_value(
                    content
                )
                clipboard.set_content(self._active_clipboard_provider)

        # ------------------------------------------------------------------
        # Dialog
        # ------------------------------------------------------------------
        def create_clipboard_ui(self):
            self.button_clipboard = Gtk.Button()
            self.button_clipboard.add_css_class("panel-button")
            icon_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            icon_image = Gtk.Image.new_from_icon_name(
                self.gtk_helper.icon_exist(self.main_icon)
            )
            icon_box.append(icon_image)
            self.button_clipboard.set_child(icon_box)

            if hasattr(self.gtk_helper, "add_cursor_effect"):
                self.gtk_helper.add_cursor_effect(self.button_clipboard)

            self.button_clipboard.connect("clicked", self.toggle_dialog)
            self.main_widget = (self.button_clipboard, "append")

        def toggle_dialog(self, *_):
            if self.dialog is not None and self.dialog.get_visible():
                self.close_dialog()
            else:
                self.open_dialog()

        def open_dialog(self):
            if self.dialog is None:
                self._build_dialog()
            self.searchbar.set_text("")
            self.update_clipboard_list()
            self.dialog.present()
            self.searchbar.grab_focus()

        def close_dialog(self):
            if self.dialog is not None:
                self.dialog.set_visible(False)

        def _on_close_request(self, _window):
            self.close_dialog()
            return True

        def _build_dialog(self):
            window = Gtk.Window()
            window.add_css_class("clipboard-dialog")
            # The theme's `background` class paints an opaque rectangle behind the
            # window, which shows as square corners around the rounded dialog.
            window.remove_css_class("background")
            window.set_default_size(self.dialog_width, self.dialog_height)

            # No anchor: layer-shell centers the surface on the output.
            self.layer_shell.init_for_window(window)
            self.layer_shell.set_namespace(window, "JFShell-Clipboard")
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
            root.add_css_class("clipboard-dialog-box")
            root.set_overflow(Gtk.Overflow.HIDDEN)
            root.append(self._build_sidebar())
            root.append(self._build_detail())
            window.set_child(root)
            self.dialog = window

        def _icon_button(self, icon_key, tooltip, callback, danger=False):
            button = Gtk.Button.new_from_icon_name(self._icons[icon_key])
            button.set_tooltip_text(tooltip)
            button.set_focus_on_click(False)
            button.add_css_class("clipboard-dialog-button")
            if danger:
                button.add_css_class("danger")
            button.connect("clicked", callback)
            if hasattr(self.gtk_helper, "add_cursor_effect"):
                self.gtk_helper.add_cursor_effect(button)
            return button

        def _build_sidebar(self):
            sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            sidebar.add_css_class("clipboard-dialog-sidebar")
            sidebar.set_size_request(280, -1)

            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            title = Gtk.Label(label="Clipboard", xalign=0, hexpand=True)
            title.add_css_class("clipboard-dialog-title")
            header.append(title)
            header.append(
                self._icon_button(
                    "delete", "Clear history", self.on_clear_clicked, danger=True
                )
            )

            self.searchbar = Gtk.SearchEntry(placeholder_text="Filter clipboard...")
            self.searchbar.add_css_class("clipboard-dialog-search")
            self.searchbar.connect("search-changed", self._on_search_changed)
            self.searchbar.connect("activate", lambda _: self._copy_selected())

            self.sw = Gtk.ScrolledWindow(vexpand=True)
            self.sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.listbox = self._new_listbox()
            self.sw.set_child(self.listbox)

            sidebar.append(header)
            sidebar.append(self.searchbar)
            sidebar.append(self.sw)
            return sidebar

        def _build_detail(self):
            detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            detail.add_css_class("clipboard-dialog-detail")
            detail.set_hexpand(True)

            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            titles.set_hexpand(True)
            titles.set_valign(Gtk.Align.START)
            self.detail_title = Gtk.Label(xalign=0, wrap=True)
            self.detail_title.add_css_class("clipboard-dialog-title")
            self.detail_subtitle = Gtk.Label(xalign=0)
            self.detail_subtitle.add_css_class("clipboard-dialog-subtitle")
            titles.append(self.detail_title)
            titles.append(self.detail_subtitle)

            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.set_valign(Gtk.Align.START)
            self.btn_copy = self._icon_button("copy", "Copy", self._on_copy_clicked)
            self.btn_pin = self._icon_button("pin", "Pin", self._on_pin_clicked)
            self.btn_delete = self._icon_button(
                "delete", "Delete", self._on_delete_clicked, danger=True
            )
            self.btn_close = self._icon_button(
                "close", "Close", lambda *_: self.close_dialog()
            )
            for button in (
                self.btn_copy,
                self.btn_pin,
                self.btn_delete,
                self.btn_close,
            ):
                actions.append(button)

            header.append(titles)
            header.append(actions)

            self.detail_stack = Gtk.Stack(vexpand=True)
            self.detail_stack.add_css_class("clipboard-dialog-preview")
            self.detail_stack.set_overflow(Gtk.Overflow.HIDDEN)

            self.preview_text = Gtk.TextView(
                editable=False,
                cursor_visible=False,
                monospace=True,
                wrap_mode=Gtk.WrapMode.WORD_CHAR,
            )
            self.preview_text.add_css_class("clipboard-dialog-text")
            self.preview_text.set_top_margin(14)
            self.preview_text.set_bottom_margin(14)
            self.preview_text.set_left_margin(14)
            self.preview_text.set_right_margin(14)
            self.text_scroller = Gtk.ScrolledWindow()
            self.text_scroller.set_child(self.preview_text)
            self.detail_stack.add_named(self.text_scroller, "text")

            self.preview_picture = Gtk.Picture()
            self.preview_picture.set_can_shrink(True)
            self.preview_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            self.detail_stack.add_named(self.preview_picture, "image")

            empty = Gtk.Label(label="Nothing to show")
            empty.add_css_class("clipboard-dialog-subtitle")
            self.detail_stack.add_named(empty, "empty")
            self.detail_stack.set_visible_child_name("empty")

            detail.append(header)
            detail.append(self.detail_stack)
            self._set_actions_sensitive(False)
            return detail

        def _set_actions_sensitive(self, sensitive):
            for button in (self.btn_copy, self.btn_pin, self.btn_delete):
                button.set_sensitive(sensitive)

        def _new_listbox(self):
            listbox = Gtk.ListBox()
            listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
            # A single click only selects (shows the preview), double click or
            # Enter copies.
            listbox.set_activate_on_single_click(False)
            listbox.add_css_class("clipboard-dialog-listbox")
            listbox.set_filter_func(self._row_matches)
            listbox.connect("row-selected", self._on_row_selected)
            listbox.connect("row-activated", self.on_copy_row)
            return listbox

        # ------------------------------------------------------------------
        # List
        # ------------------------------------------------------------------
        async def populate_listbox_async(self):
            """Rebuilds the list and swaps it in at once (no per-row reflow)."""
            if self._is_populating:
                return
            self._is_populating = True
            try:
                history = await self.manager.get_history()
                self.listbox = self._new_listbox()
                for item in history:
                    self.listbox.append(self._build_row(item))
                self.sw.set_child(self.listbox)
                self._restore_selection()
                gc.collect()
            except Exception as e:
                self.logger.error(f"Clipboard: Population failed: {e}")
            finally:
                self._is_populating = False

        def _build_row(self, item):
            item_id, content, label, is_pinned = item[0], item[1], item[2], item[3]
            thumbnail = item[4] if len(item) > 4 else None
            timestamp = item[5] if len(item) > 5 else None
            size_px = self.row_thumbnail_size

            real_path = self._resolve_local_path(content)
            image_path = (
                real_path
                if real_path and self.clipboard_helper.is_image_content(real_path)
                else None
            )
            if image_path:
                title = label or "Image"
                try:
                    size = os.path.getsize(image_path)
                except OSError:
                    size = len(content.encode("utf-8", "replace"))
            else:
                title = (
                    label or first_line(content, self.preview_text_length) or "(empty)"
                )
                size = len(content.encode("utf-8", "replace"))
            subtitle = " \u2022 ".join(
                part for part in (format_age(timestamp), format_size(size)) if part
            )

            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row_box.add_css_class("clipboard-dialog-row-box")
            row_box.ITEM_ID = item_id
            row_box.IS_PINNED = is_pinned
            row_box.RAW_CONTENT = content
            row_box.LABEL = label
            row_box.IMAGE_PATH = image_path
            row_box.SUBTITLE = subtitle

            lead = Gtk.Box()
            lead.set_size_request(size_px, size_px)
            lead.set_hexpand(False)
            lead.set_halign(Gtk.Align.START)
            lead.set_valign(Gtk.Align.CENTER)
            lead.set_overflow(Gtk.Overflow.HIDDEN)
            lead.add_css_class("clipboard-dialog-thumb-frame")
            if image_path:
                # Picture + COVER center-crops to a square. Fixed size, and not
                # styled by .clipboard-dialog-thumb, so no CSS can resize it.
                thumb = Gtk.Picture()
                thumb.set_size_request(size_px, size_px)
                thumb.set_can_shrink(True)
                thumb.set_content_fit(Gtk.ContentFit.COVER)
                thumb.set_halign(Gtk.Align.FILL)
                thumb.set_valign(Gtk.Align.FILL)
                lead.append(thumb)
                source = (
                    thumbnail if thumbnail and Path(thumbnail).exists() else image_path
                )
                self._load_thumbnail_async(source, thumb)
            else:
                icon = Gtk.Image.new_from_icon_name(self._icons["text"])
                icon.set_pixel_size(22)
                icon.set_hexpand(True)
                icon.set_halign(Gtk.Align.CENTER)
                icon.set_valign(Gtk.Align.CENTER)
                icon.add_css_class("clipboard-dialog-icon-text")
                lead.append(icon)
            row_box.append(lead)

            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            texts.set_hexpand(True)
            texts.set_halign(Gtk.Align.FILL)
            texts.set_valign(Gtk.Align.CENTER)
            title_label = Gtk.Label(label=title, xalign=0)
            title_label.set_ellipsize(Pango.EllipsizeMode.END)
            title_label.set_max_width_chars(28)
            title_label.set_hexpand(True)
            title_label.add_css_class("clipboard-dialog-row-title")
            subtitle_label = Gtk.Label(label=subtitle, xalign=0)
            subtitle_label.add_css_class("clipboard-dialog-row-subtitle")
            texts.append(title_label)
            texts.append(subtitle_label)
            row_box.append(texts)

            if is_pinned:
                pin = Gtk.Image.new_from_icon_name(self._icons["pin"])
                pin.add_css_class("clipboard-dialog-row-pin")
                row_box.append(pin)

            row = Gtk.ListBoxRow(child=row_box)
            row.add_css_class("clipboard-dialog-row")
            gesture = Gtk.GestureClick(button=3)
            gesture.connect("pressed", self.on_right_click_row)
            row.add_controller(gesture)
            return row

        def _load_thumbnail_async(self, path, picture):
            """Thumbnail decoded off the main thread; the Picture crops it square."""
            size = self.row_thumbnail_size

            def on_scaled(_source, res):
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_stream_finish(res)
                    picture.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
                except Exception as e:
                    self.logger.error(f"Clipboard: thumbnail decode failed: {e}")

            def on_read(file_obj, res):
                try:
                    stream = file_obj.read_finish(res)
                except Exception as e:
                    self.logger.error(f"Clipboard: thumbnail read failed: {e}")
                    return
                GdkPixbuf.Pixbuf.new_from_stream_at_scale_async(
                    stream, size * 3, size * 3, True, None, on_scaled
                )

            self.gio.File.new_for_path(path).read_async(
                self.glib.PRIORITY_DEFAULT, None, on_read
            )

        def update_clipboard_list(self):
            self.run_in_async_task(self.populate_listbox_async())

        def _row_matches(self, row):
            searchbar = getattr(self, "searchbar", None)
            query = searchbar.get_text().strip().lower() if searchbar else ""
            if not query:
                return True
            child = row.get_child()
            haystack = f"{child.RAW_CONTENT}\n{child.LABEL or ''}".lower()
            return query in haystack

        def _visible_rows(self):
            rows = []
            row = self.listbox.get_first_child() if self.listbox else None
            while row is not None:
                if isinstance(row, Gtk.ListBoxRow) and self._row_matches(row):
                    rows.append(row)
                row = row.get_next_sibling()
            return rows

        def _row_for_id(self, item_id):
            for row in self._visible_rows():
                if row.get_child().ITEM_ID == item_id:
                    return row
            return None

        def _restore_selection(self):
            rows = self._visible_rows()
            target = self._row_for_id(self._selected_id) or (rows[0] if rows else None)
            if target is None:
                self._show_detail(None)
                return
            self._select_and_scroll(target)

        def _select_and_scroll(self, row):
            self.listbox.select_row(row)
            self.glib.idle_add(self._scroll_to_row, row)

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
            current = self.listbox.get_selected_row()
            if current is not None and self._row_matches(current):
                return
            rows = self._visible_rows()
            if rows:
                self._select_and_scroll(rows[0])
            else:
                self.listbox.unselect_all()
                self._show_detail(None)

        def _on_key_pressed(self, _controller, keyval, _keycode, _state):
            if keyval == Gdk.KEY_Escape:
                if self.searchbar.get_text():
                    self.searchbar.set_text("")
                else:
                    self.close_dialog()
                return True
            if isinstance(self.dialog.get_focus(), Gtk.TextView):
                return False
            if keyval == Gdk.KEY_Down:
                self._move_selection(1)
                return True
            if keyval == Gdk.KEY_Up:
                self._move_selection(-1)
                return True
            return False

        # ------------------------------------------------------------------
        # Detail
        # ------------------------------------------------------------------
        def _on_row_selected(self, _listbox, row):
            self._show_detail(row)

        def _show_detail(self, row):
            child = row.get_child() if row is not None else None
            if child is None:
                self._selected_id = None
                self.detail_title.set_label("Clipboard")
                self.detail_subtitle.set_label("")
                self.detail_stack.set_visible_child_name("empty")
                self._set_actions_sensitive(False)
                return

            self._selected_id = child.ITEM_ID
            self._set_actions_sensitive(True)
            self.detail_subtitle.set_label(child.SUBTITLE)
            self.btn_pin.set_tooltip_text("Unpin" if child.IS_PINNED else "Pin")
            if child.IS_PINNED:
                self.btn_pin.add_css_class("pinned")
            else:
                self.btn_pin.remove_css_class("pinned")

            if child.IMAGE_PATH:
                self.detail_title.set_label("Clipboard Image Entry")
                self.preview_picture.set_filename(child.IMAGE_PATH)
                self.detail_stack.set_visible_child_name("image")
            else:
                self.detail_title.set_label("Clipboard Text Entry")
                text = child.RAW_CONTENT
                if len(text) > self.preview_max_chars:
                    text = text[: self.preview_max_chars] + "..."
                self.preview_text.get_buffer().set_text(text)
                self.text_scroller.get_vadjustment().set_value(0)
                self.detail_stack.set_visible_child_name("text")

        def _selected_child(self):
            row = self.listbox.get_selected_row() if self.listbox else None
            return row.get_child() if row is not None else None

        # ------------------------------------------------------------------
        # Actions
        # ------------------------------------------------------------------
        def on_copy_row(self, _, row):
            if not row:
                return
            self.copy_to_clipboard(row.get_child().RAW_CONTENT)
            self.close_dialog()

        def _copy_selected(self):
            child = self._selected_child()
            if child is None:
                return
            self.copy_to_clipboard(child.RAW_CONTENT)
            self.close_dialog()

        def _on_copy_clicked(self, *_):
            self._copy_selected()

        def _on_pin_clicked(self, *_):
            child = self._selected_child()
            if child is not None:
                self.run_in_async_task(
                    self._do_toggle_pin(child.ITEM_ID, child.IS_PINNED)
                )

        def _on_delete_clicked(self, *_):
            child = self._selected_child()
            if child is not None:
                self.run_in_async_task(self._do_delete(child.ITEM_ID))

        async def _do_delete(self, iid):
            rows = self._visible_rows()
            row = self._row_for_id(iid)
            index = rows.index(row) if row in rows else 0
            await self.manager.delete_item(iid)
            if row is not None:
                self.listbox.remove(row)
            rows = self._visible_rows()
            if not rows:
                self._show_detail(None)
                return
            self._select_and_scroll(rows[min(index, len(rows) - 1)])

        def on_clear_clicked(self, *_):
            self.run_in_async_task(self._do_clear())

        async def _do_clear(self):
            await self.manager.clear_history()
            self._selected_id = None
            self.listbox = self._new_listbox()
            self.sw.set_child(self.listbox)
            self._show_detail(None)
            gc.collect()

        def on_right_click_row(self, gesture, *args):
            row = gesture.get_widget()
            child = row.get_child()

            menu = Gtk.Popover()
            menu.set_parent(row)
            menu.set_autohide(True)
            menu.connect("closed", lambda p: p.unparent())

            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            vbox.set_margin_end(10)

            pin_label = "Unstick from Top" if child.IS_PINNED else "Stick to Top"
            btn_pin = Gtk.Button(label=pin_label)
            btn_pin.add_css_class("clipboard-menu-item-button")
            btn_pin.connect(
                "clicked", self.on_toggle_pin, child.ITEM_ID, child.IS_PINNED, menu
            )
            vbox.append(btn_pin)
            vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            vbox.append(Gtk.Label(label="Edit Alias:", xalign=0))

            entry = Gtk.Entry()
            entry.set_placeholder_text("Enter label...")
            entry.connect(
                "activate", self.on_label_entry_activated, child.ITEM_ID, menu
            )
            vbox.append(entry)

            menu.set_child(vbox)
            if not row.get_realized():
                row.realize()
            menu.popup()

        def on_label_entry_activated(self, entry, iid, menu):
            label = entry.get_text().strip()
            menu.popdown()
            self.run_in_async_task(self._do_update_label(iid, label if label else None))

        async def _do_update_label(self, iid, label):
            await self.manager.update_item_label(iid, label)
            self._selected_id = iid
            await self.populate_listbox_async()

        def on_toggle_pin(self, _, iid, pinned, menu):
            menu.popdown()
            self.run_in_async_task(self._do_toggle_pin(iid, pinned))

        async def _do_toggle_pin(self, iid, pinned):
            await self.manager.update_item_pin_status(iid, not pinned)
            self._selected_id = iid
            await self.populate_listbox_async()

        def on_disable(self):
            self.run_in_async_task(self.manager.server.stop())
            if self.dialog is not None:
                self.dialog.destroy()
                self.dialog = None

    return ClipboardClient
