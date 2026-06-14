#!/usr/bin/env python3
# rsync GUI
# Graphical frontend for rsync with live progress and desktop notifications
# MIT License — NoCoderGHG

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Notify', '0.7')
from gi.repository import Gtk, Gdk, GLib, Pango, Notify

import json
import locale
import os
import re
import signal
import subprocess
import threading
from pathlib import Path

I18N_DIR = Path(__file__).parent / "i18n"

SUPPORTED_LANGUAGES = {
    "de": "Deutsch",
    "en": "English",
    "fr": "Français",
    "es": "Español",
    "it": "Italiano",
    "pt": "Português",
    "nl": "Nederlands",
    "pl": "Polski",
    "ru": "Русский",
    "tr": "Türkçe",
    "zh": "中文",
    "ja": "日本語",
}

CONFIG_DIR = Path.home() / ".config" / "rsync-gui"
CONFIG_FILE = CONFIG_DIR / "config.json"

LANG_CHOICES = ["de", "en", "system"]


def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"lang": "system"}


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def detect_system_lang():
    try:
        loc = locale.getlocale()[0] or ""
    except Exception:
        loc = ""
    if not loc:
        loc = os.environ.get("LANG", "")
    code = loc.lower().split("_")[0].split(".")[0]
    if code in SUPPORTED_LANGUAGES and (I18N_DIR / f"{code}.json").exists():
        return code
    return "de" if code == "de" else "en"


def resolve_lang(setting):
    if setting == "system":
        return detect_system_lang()
    return setting


def load_i18n(lang):
    # English is the base; other languages fall back key by key
    en = {}
    en_path = I18N_DIR / "en.json"
    if en_path.exists():
        with open(en_path) as f:
            en = json.load(f)
    if lang == "en":
        return en
    path = I18N_DIR / f"{lang}.json"
    if not path.exists():
        return en
    with open(path) as f:
        strings = json.load(f)
    for k, v in en.items():
        strings.setdefault(k, v)
    return strings

def build_lang_options(strings):
    """Liste (code, label) fuer das Sprachmenue. Sprachen ohne eigene
    i18n-Datei werden mit "(EN)" markiert (Fallback auf Englisch)."""
    opts = [("system", t(strings, "lang_system")),
            ("de", t(strings, "lang_de")),
            ("en", t(strings, "lang_en"))]
    for code, name in SUPPORTED_LANGUAGES.items():
        if code in ("de", "en"):
            continue
        label = name if (I18N_DIR / f"{code}.json").exists() else f"{name} (EN)"
        opts.append((code, label))
    return opts


def build_lang_lists(strings):
    """Wie build_lang_options, aber als getrennte Listen (codes, labels)."""
    codes, items = [], []
    for code, label in build_lang_options(strings):
        codes.append(code)
        items.append(label)
    return codes, items



def t(strings, key, **kwargs):
    s = strings.get(key, key)
    for k, v in kwargs.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def make_menu_button(items, on_select, min_width=150):
    btn = Gtk.MenuButton()
    btn.set_size_request(min_width, -1)
    lbl = Gtk.Label(label=items[0] if items else "")
    btn.add(lbl)
    menu = Gtk.Menu()

    def build_menu(items, current=None):
        for child in menu.get_children():
            menu.remove(child)
        group = []
        active = current if current in items else (items[0] if items else None)
        for text in items:
            item = Gtk.RadioMenuItem.new_with_label(group, text)
            group = item.get_group()
            if text == active:
                item.set_active(True)
            def _on_activate(i, t=text):
                if i.get_active():
                    lbl.set_text(t)
                    on_select(t)
            item.connect("activate", _on_activate)
            menu.append(item)
        menu.show_all()
        if active:
            lbl.set_text(active)

    build_menu(items)
    btn.set_popup(menu)

    def update(new_items, current=None):
        build_menu(new_items, current)

    return btn, lbl, update


class RsyncGUI:
    # Option definitions: (attribute, i18n title key, i18n description key, default)
    OPTIONS = [
        ("preserve",        "opt_preserve_title",        "opt_preserve_desc",        True),
        ("recursive",       "opt_recursive_title",       "opt_recursive_desc",       True),
        ("progress",        "opt_progress_title",        "opt_progress_desc",        True),
        ("verbose",         "opt_verbose_title",         "opt_verbose_desc",         True),
        ("no_compress",     "opt_no_compress_title",     "opt_no_compress_desc",     False),
        ("update",          "opt_update_title",          "opt_update_desc",          False),
        ("delete",          "opt_delete_title",          "opt_delete_desc",          False),
        ("dry_run",         "opt_dry_run_title",         "opt_dry_run_desc",         False),
        ("partial",         "opt_partial_title",         "opt_partial_desc",         True),
        ("append",          "opt_append_title",          "opt_append_desc",          False),
        ("sparse",          "opt_sparse_title",          "opt_sparse_desc",          False),
        ("compress",        "opt_compress_title",        "opt_compress_desc",        False),
        ("checksum",        "opt_checksum_title",        "opt_checksum_desc",        False),
        ("ignore_existing", "opt_ignore_existing_title", "opt_ignore_existing_desc", False),
    ]

    def __init__(self):
        self.current_process = None
        self.cfg = load_config()
        self.strings = load_i18n(resolve_lang(self.cfg.get("lang", "system")))

        Notify.init("rsync GUI")
        self.create_ui()

    def create_ui(self):
        # Main window
        self.window = Gtk.Window(title=t(self.strings, "app_title"))
        self.window.set_default_size(650, 540)
        self.window.set_size_request(500, 400)
        self.window.connect("destroy", self.on_destroy)

        # Main layout - VPaned for vertical split control
        main_paned = Gtk.VPaned()
        main_paned.set_margin_start(10)
        main_paned.set_margin_end(10)
        main_paned.set_margin_top(10)
        main_paned.set_margin_bottom(10)
        self.window.add(main_paned)

        # Top area - compact inputs
        top_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        main_paned.pack1(top_box, resize=True, shrink=False)

        # Language selector
        lang_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        lang_label = Gtk.Label(label=t(self.strings, "lbl_language"))
        lang_box.pack_start(lang_label, False, False, 0)
        _lang_codes, _lang_items = build_lang_lists(self.strings)
        _lang_current = _lang_items[_lang_codes.index(self.cfg.get("lang", "system"))] if self.cfg.get("lang", "system") in _lang_codes else _lang_items[0]
        self.lang_menu_btn, self._lang_lbl, _ = make_menu_button(
            _lang_items, lambda txt: self._on_lang_selected(txt, _lang_items, _lang_codes), min_width=170
        )
        self._lang_lbl.set_text(_lang_current)
        lang_box.pack_start(self.lang_menu_btn, False, False, 0)

        about_btn = Gtk.Button()
        about_btn.set_image(Gtk.Image.new_from_icon_name("help-about-symbolic", Gtk.IconSize.BUTTON))
        about_btn.set_tooltip_text(t(self.strings, "tooltip_about"))
        about_btn.connect("clicked", self._on_about)
        lang_box.pack_end(about_btn, False, False, 0)

        top_box.pack_start(lang_box, False, False, 0)

        # Source selection
        source_frame = Gtk.Frame(label=t(self.strings, "frame_source"))
        source_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        source_box.set_margin_start(8)
        source_box.set_margin_end(8)
        source_box.set_margin_top(8)
        source_box.set_margin_bottom(8)

        self.source_entry = Gtk.Entry()
        self.source_entry.set_hexpand(True)
        self.source_entry.connect("changed", self.on_entry_changed)
        source_box.pack_start(self.source_entry, True, True, 0)

        source_file_btn = Gtk.Button(label=t(self.strings, "btn_pick_file"))
        source_file_btn.connect("clicked", self.select_source_file)
        source_box.pack_start(source_file_btn, False, False, 0)

        source_dir_btn = Gtk.Button(label=t(self.strings, "btn_pick_folder"))
        source_dir_btn.connect("clicked", self.select_source_dir)
        source_box.pack_start(source_dir_btn, False, False, 0)

        source_frame.add(source_box)
        top_box.pack_start(source_frame, False, False, 0)

        # Destination selection
        dest_frame = Gtk.Frame(label=t(self.strings, "frame_dest"))
        dest_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        dest_box.set_margin_start(8)
        dest_box.set_margin_end(8)
        dest_box.set_margin_top(8)
        dest_box.set_margin_bottom(8)

        self.dest_entry = Gtk.Entry()
        self.dest_entry.set_hexpand(True)
        self.dest_entry.connect("changed", self.on_entry_changed)
        dest_box.pack_start(self.dest_entry, True, True, 0)

        dest_dir_btn = Gtk.Button(label=t(self.strings, "btn_pick_folder"))
        dest_dir_btn.connect("clicked", self.select_dest_dir)
        dest_box.pack_start(dest_dir_btn, False, False, 0)

        dest_frame.add(dest_box)
        top_box.pack_start(dest_frame, False, False, 0)

        # Options - smart sizing
        options_frame = Gtk.Frame(label=t(self.strings, "frame_options"))

        options_scrolled = Gtk.ScrolledWindow()
        options_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        options_scrolled.set_vexpand(False)
        options_scrolled.set_hexpand(True)
        options_scrolled.set_size_request(-1, 150)
        options_scrolled.set_max_content_height(280)

        options_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        options_box.set_margin_start(8)
        options_box.set_margin_end(8)
        options_box.set_margin_top(8)
        options_box.set_margin_bottom(8)

        options_scrolled.add(options_box)
        options_frame.add(options_scrolled)

        for attr, title_key, desc_key, default in self.OPTIONS:
            self.create_option_checkbox(
                options_box, attr,
                t(self.strings, title_key),
                t(self.strings, desc_key),
                default,
            )

        top_box.pack_start(options_frame, True, True, 0)

        # Progress bar - compact
        progress_frame = Gtk.Frame(label=t(self.strings, "frame_progress"))
        progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        progress_box.set_margin_start(8)
        progress_box.set_margin_end(8)
        progress_box.set_margin_top(4)
        progress_box.set_margin_bottom(4)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_text(t(self.strings, "lbl_ready"))
        progress_box.pack_start(self.progress_bar, False, False, 0)

        self.progress_detail_label = Gtk.Label()
        self.progress_detail_label.set_halign(Gtk.Align.START)
        self.progress_detail_label.set_markup(
            f'<span size="small">{GLib.markup_escape_text(t(self.strings, "lbl_waiting"))}</span>')
        progress_box.pack_start(self.progress_detail_label, False, False, 0)

        progress_frame.add(progress_box)
        top_box.pack_start(progress_frame, False, False, 0)

        # Command preview - compact
        cmd_frame = Gtk.Frame(label=t(self.strings, "frame_command"))
        cmd_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        cmd_box.set_margin_start(8)
        cmd_box.set_margin_end(8)
        cmd_box.set_margin_top(4)
        cmd_box.set_margin_bottom(4)

        scrolled_cmd = Gtk.ScrolledWindow()
        scrolled_cmd.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_cmd.set_size_request(-1, 50)
        scrolled_cmd.set_hexpand(True)

        self.cmd_textview = Gtk.TextView()
        self.cmd_textview.set_editable(False)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
        textview {
            font-family: monospace;
            font-size: 9pt;
        }
        """)
        self.cmd_textview.get_style_context().add_provider(
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        scrolled_cmd.add(self.cmd_textview)
        cmd_box.pack_start(scrolled_cmd, True, True, 0)
        cmd_frame.add(cmd_box)
        top_box.pack_start(cmd_frame, False, False, 0)

        # Buttons - compact
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

        update_btn = Gtk.Button(label=t(self.strings, "btn_update_cmd"))
        update_btn.connect("clicked", self.update_command_clicked)
        button_box.pack_start(update_btn, False, False, 0)

        self.run_btn = Gtk.Button(label=t(self.strings, "btn_run"))
        self.run_btn.connect("clicked", self.run_rsync)
        button_box.pack_start(self.run_btn, False, False, 0)

        self.cancel_btn = Gtk.Button(label=t(self.strings, "btn_cancel"))
        self.cancel_btn.connect("clicked", self.cancel_rsync)
        self.cancel_btn.set_sensitive(False)
        button_box.pack_start(self.cancel_btn, False, False, 0)

        clipboard_btn = Gtk.Button(label=t(self.strings, "btn_clipboard"))
        clipboard_btn.connect("clicked", self.copy_to_clipboard)
        button_box.pack_start(clipboard_btn, False, False, 0)

        top_box.pack_start(button_box, False, False, 0)

        # Status - compact
        self.status_label = Gtk.Label(label=t(self.strings, "lbl_ready"))
        self.status_label.set_halign(Gtk.Align.START)
        top_box.pack_start(self.status_label, False, False, 0)

        # Bottom area - output gets remaining space
        output_frame = Gtk.Frame(label=t(self.strings, "frame_output"))
        output_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        output_box.set_margin_start(8)
        output_box.set_margin_end(8)
        output_box.set_margin_top(8)
        output_box.set_margin_bottom(8)

        scrolled_output = Gtk.ScrolledWindow()
        scrolled_output.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_output.set_vexpand(True)
        scrolled_output.set_hexpand(True)

        self.output_textview = Gtk.TextView()
        self.output_textview.set_editable(False)
        self.output_textview.set_wrap_mode(Gtk.WrapMode.WORD)
        scrolled_output.add(self.output_textview)

        output_box.pack_start(scrolled_output, True, True, 0)
        output_frame.add(output_box)

        main_paned.pack2(output_frame, resize=True, shrink=False)

        # Set initial paned position (larger output area)
        GLib.idle_add(lambda: main_paned.set_position(400))

        # Show initial command
        self.update_command()

    def on_destroy(self, widget):
        Notify.uninit()
        Gtk.main_quit()

    def _on_about(self, _btn):
        dlg = Gtk.AboutDialog(transient_for=self.window, modal=True)
        dlg.set_program_name(t(self.strings, "app_title"))
        dlg.set_version("1.0")
        dlg.set_comments(t(self.strings, "about_comments"))
        dlg.set_license_type(Gtk.License.MIT_X11)
        dlg.run()
        dlg.destroy()

    def _on_lang_selected(self, text, items, codes):
        if text in items:
            code = codes[items.index(text)]
            if code != self.cfg.get("lang"):
                self.cfg["lang"] = code
                save_config(self.cfg)
                new_strings = load_i18n(resolve_lang(code))
                dialog = Gtk.MessageDialog(
                    parent=self.window,
                    flags=Gtk.DialogFlags.MODAL,
                    type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    message_format=t(new_strings, "restart_hint")
                )
                dialog.run()
                dialog.destroy()

    def send_notification(self, title, message, urgency=Notify.Urgency.NORMAL):
        # Send a desktop notification
        try:
            notification = Notify.Notification.new(title, message, "rsync")
            notification.set_urgency(urgency)
            notification.show()
        except Exception as e:
            print(t(self.strings, "notify_error_send", error=e))

    def create_option_checkbox(self, parent_box, attr_name, title, description, default_value):
        option_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)

        checkbox = Gtk.CheckButton(label=title)
        checkbox.set_active(default_value)
        checkbox.connect("toggled", self.on_checkbox_toggled)
        setattr(self, f"{attr_name}_cb", checkbox)
        option_box.pack_start(checkbox, False, False, 0)

        desc_label = Gtk.Label(label=description)
        desc_label.set_halign(Gtk.Align.START)
        desc_label.set_markup(
            f'<span color="gray" size="small">{GLib.markup_escape_text(description)}</span>')
        option_box.pack_start(desc_label, False, False, 0)

        parent_box.pack_start(option_box, False, False, 0)

    def select_source_file(self, widget):
        dialog = Gtk.FileChooserDialog(
            title=t(self.strings, "dlg_pick_src_file"),
            parent=self.window,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.source_entry.set_text(dialog.get_filename())

        dialog.destroy()

    def select_source_dir(self, widget):
        dialog = Gtk.FileChooserDialog(
            title=t(self.strings, "dlg_pick_src_dir"),
            parent=self.window,
            action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.source_entry.set_text(dialog.get_filename())

        dialog.destroy()

    def select_dest_dir(self, widget):
        dialog = Gtk.FileChooserDialog(
            title=t(self.strings, "dlg_pick_dst_dir"),
            parent=self.window,
            action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.dest_entry.set_text(dialog.get_filename())

        dialog.destroy()

    def on_entry_changed(self, widget):
        self.update_command()

    def on_checkbox_toggled(self, widget):
        self.update_command()

    def update_command_clicked(self, widget):
        self.update_command()

    def build_command(self):
        source = self.source_entry.get_text().strip()
        dest = self.dest_entry.get_text().strip()

        if not source or not dest:
            return []

        cmd = ["rsync"]

        # Add options
        if self.preserve_cb.get_active():
            cmd.append("-a")
        elif self.recursive_cb.get_active():
            cmd.append("-r")

        if self.verbose_cb.get_active():
            cmd.append("-v")

        if self.progress_cb.get_active():
            cmd.append("--progress")

        if self.no_compress_cb.get_active():
            cmd.append("--no-compress")

        if self.update_cb.get_active():
            cmd.append("-u")

        if self.delete_cb.get_active():
            cmd.append("--delete")

        if self.dry_run_cb.get_active():
            cmd.append("--dry-run")

        if self.partial_cb.get_active():
            cmd.append("--partial")

        if self.append_cb.get_active():
            cmd.append("--append")

        if self.sparse_cb.get_active():
            cmd.append("--sparse")

        if self.compress_cb.get_active():
            cmd.append("-z")

        if self.checksum_cb.get_active():
            cmd.append("-c")

        if self.ignore_existing_cb.get_active():
            cmd.append("--ignore-existing")

        # Add trailing slash for folders for consistent behaviour
        if os.path.isdir(source) and not source.endswith('/'):
            source += '/'

        cmd.append(source)
        cmd.append(dest)

        return cmd

    def update_command(self):
        buffer = self.cmd_textview.get_buffer()
        cmd = self.build_command()
        if cmd:
            buffer.set_text(' '.join(cmd))
        else:
            buffer.set_text(t(self.strings, "cmd_placeholder"))

    def copy_to_clipboard(self, widget):
        cmd = self.build_command()
        if cmd:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(' '.join(cmd), -1)

            dialog = Gtk.MessageDialog(
                parent=self.window,
                flags=Gtk.DialogFlags.MODAL,
                type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                message_format=t(self.strings, "dlg_clipboard_ok")
            )
            dialog.run()
            dialog.destroy()
        else:
            dialog = Gtk.MessageDialog(
                parent=self.window,
                flags=Gtk.DialogFlags.MODAL,
                type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                message_format=t(self.strings, "dlg_clipboard_fail")
            )
            dialog.run()
            dialog.destroy()

    def run_rsync(self, widget):
        cmd = self.build_command()
        if not cmd:
            dialog = Gtk.MessageDialog(
                parent=self.window,
                flags=Gtk.DialogFlags.MODAL,
                type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                message_format=t(self.strings, "dlg_no_src_dst")
            )
            dialog.run()
            dialog.destroy()
            return

        # Warn when --delete is active
        if self.delete_cb.get_active() and not self.dry_run_cb.get_active():
            dialog = Gtk.MessageDialog(
                parent=self.window,
                flags=Gtk.DialogFlags.MODAL,
                type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                message_format=t(self.strings, "dlg_delete_warning")
            )
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.YES:
                return

        # Update UI state
        self.run_btn.set_sensitive(False)
        self.cancel_btn.set_sensitive(True)
        self.status_label.set_markup(
            f'<span color="blue">{GLib.markup_escape_text(t(self.strings, "status_running"))}</span>')

        # Initialize progress bar
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_text(t(self.strings, "progress_starting"))
        self.progress_detail_label.set_markup(
            f'<span size="small">{GLib.markup_escape_text(t(self.strings, "progress_init"))}</span>')

        # Clear output
        buffer = self.output_textview.get_buffer()
        buffer.set_text(t(self.strings, "output_running", cmd=' '.join(cmd)) + "\n")
        buffer.insert_at_cursor("=" * 50 + "\n\n")

        # Run rsync in a thread
        thread = threading.Thread(target=self.execute_rsync, args=(cmd,))
        thread.daemon = True
        thread.start()

    def parse_progress_line(self, line):
        # Parse rsync progress output and extract percent, speed, remaining time.
        # Typical rsync --progress line:
        # "    1,234,567  89%  123.45kB/s    0:00:12  (xfr#1, to-chk=123/456)"
        progress_match = re.search(r'(\d+)%.*?([0-9,.]+[kKmMgG]?[Bb]/s).*?(\d+:\d+:\d+)', line)
        if progress_match:
            percent = int(progress_match.group(1))
            speed = progress_match.group(2)
            time_remaining = progress_match.group(3)
            return percent, speed, time_remaining

        # Alternative format for total progress
        total_progress_match = re.search(r'to-chk=(\d+)/(\d+)', line)
        if total_progress_match:
            remaining = int(total_progress_match.group(1))
            total = int(total_progress_match.group(2))
            if total > 0:
                percent = int((total - remaining) / total * 100)
                return percent, None, None

        return None, None, None

    def get_rsync_error_message(self, returncode):
        # Translate rsync exit codes into readable error messages
        known_codes = [1, 2, 3, 4, 5, 6, 10, 11, 12, 13, 14, 20, 21, 22, 23, 24, 25, 30, 35]
        if returncode in known_codes:
            return t(self.strings, f"rsync_err_{returncode}")
        return t(self.strings, "rsync_err_unknown", code=returncode)

    def execute_rsync(self, cmd):
        try:
            # Start process
            self.current_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                preexec_fn=os.setsid if os.name != 'nt' else None
            )

            # Read output line by line
            for line in iter(self.current_process.stdout.readline, ''):
                if line:
                    GLib.idle_add(self.append_output, line)

                    # Progress parsing
                    percent, speed, time_remaining = self.parse_progress_line(line)
                    if percent is not None:
                        GLib.idle_add(self.update_progress_bar, percent, speed, time_remaining)

            self.current_process.wait()

            # Check whether cancelled or finished normally
            if self.current_process.returncode == 0:
                GLib.idle_add(self.append_output, "\n" + "=" * 50 + "\n")
                GLib.idle_add(self.append_output, t(self.strings, "output_success") + "\n")
                GLib.idle_add(self.update_status, t(self.strings, "status_success"), "green")
                GLib.idle_add(self.final_progress_bar, t(self.strings, "progress_done"), 1.0, "green")
                GLib.idle_add(self.send_notification, t(self.strings, "notify_success_title"),
                              t(self.strings, "notify_success_body"),
                              Notify.Urgency.NORMAL)
            elif self.current_process.returncode == -signal.SIGTERM or self.current_process.returncode == -signal.SIGKILL:
                GLib.idle_add(self.append_output, "\n" + "=" * 50 + "\n")
                GLib.idle_add(self.append_output, t(self.strings, "output_cancelled") + "\n")
                GLib.idle_add(self.update_status, t(self.strings, "status_cancelled"), "orange")
                GLib.idle_add(self.final_progress_bar, t(self.strings, "progress_cancelled"), 0.0, "orange")
                GLib.idle_add(self.send_notification, t(self.strings, "notify_cancelled_title"),
                              t(self.strings, "notify_cancelled_body"),
                              Notify.Urgency.NORMAL)
            else:
                error_msg = self.get_rsync_error_message(self.current_process.returncode)
                GLib.idle_add(self.append_output, "\n" + "=" * 50 + "\n")
                GLib.idle_add(self.append_output, t(self.strings, "output_error", error=error_msg) + "\n")
                GLib.idle_add(self.update_status, t(self.strings, "status_error"), "red")
                GLib.idle_add(self.final_progress_bar, t(self.strings, "progress_error"), 0.0, "red")
                GLib.idle_add(self.send_notification, t(self.strings, "notify_error_title"),
                              t(self.strings, "notify_error_body", error=error_msg),
                              Notify.Urgency.CRITICAL)

        except Exception as e:
            error_detail = t(self.strings, "python_error", error=str(e))
            GLib.idle_add(self.append_output, f"\n{error_detail}\n")
            GLib.idle_add(self.update_status, t(self.strings, "status_exec_error"), "red")
            GLib.idle_add(self.final_progress_bar, t(self.strings, "progress_error"), 0.0, "red")
            GLib.idle_add(self.send_notification, t(self.strings, "notify_exec_error_title"),
                          error_detail,
                          Notify.Urgency.CRITICAL)
        finally:
            # Reset UI
            self.current_process = None
            GLib.idle_add(self.reset_ui)

    def update_progress_bar(self, percent, speed, time_remaining):
        # Update the progress bar with parsed values
        fraction = percent / 100.0
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(f"{percent}%")

        # Compose detail info
        details = []
        if speed:
            details.append(t(self.strings, "progress_speed", speed=speed))
        if time_remaining:
            details.append(t(self.strings, "progress_remaining", time=time_remaining))

        if details:
            detail_text = " | ".join(details)
            self.progress_detail_label.set_markup(
                f'<span size="small">{GLib.markup_escape_text(detail_text)}</span>')

    def final_progress_bar(self, text, fraction, color):
        # Set final state of the progress bar
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(text)
        self.progress_detail_label.set_markup(
            f'<span size="small" color="{color}">{GLib.markup_escape_text(t(self.strings, "progress_finished"))}</span>')

    def cancel_rsync(self, widget):
        if self.current_process:
            try:
                dialog = Gtk.MessageDialog(
                    parent=self.window,
                    flags=Gtk.DialogFlags.MODAL,
                    type=Gtk.MessageType.QUESTION,
                    buttons=Gtk.ButtonsType.YES_NO,
                    message_format=t(self.strings, "dlg_cancel_confirm")
                )
                response = dialog.run()
                dialog.destroy()

                if response == Gtk.ResponseType.YES:
                    if os.name == 'nt':  # Windows
                        self.current_process.terminate()
                    else:  # Unix/Linux/Mac
                        os.killpg(os.getpgid(self.current_process.pid), signal.SIGTERM)

                    self.append_output("\n" + t(self.strings, "output_cancel_requested") + "\n")
            except Exception as e:
                self.append_output("\n" + t(self.strings, "output_cancel_error", error=str(e)) + "\n")
                try:
                    self.current_process.kill()
                except Exception:
                    pass

    def append_output(self, text):
        buffer = self.output_textview.get_buffer()
        buffer.insert_at_cursor(text)

        # Auto-scroll to bottom
        mark = buffer.get_insert()
        self.output_textview.scroll_mark_onscreen(mark)

    def update_status(self, text, color):
        self.status_label.set_markup(f'<span color="{color}">{GLib.markup_escape_text(text)}</span>')

    def reset_ui(self):
        self.run_btn.set_sensitive(True)
        self.cancel_btn.set_sensitive(False)
        if "🔄" in self.status_label.get_text():
            self.status_label.set_text(t(self.strings, "lbl_ready"))
            # Reset progress bar
            self.progress_bar.set_fraction(0.0)
            self.progress_bar.set_text(t(self.strings, "lbl_ready"))
            self.progress_detail_label.set_markup(
                f'<span size="small">{GLib.markup_escape_text(t(self.strings, "lbl_waiting"))}</span>')


def main():
    app = RsyncGUI()
    app.window.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
