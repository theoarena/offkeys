# mskb_gui.py
#
# GTK4/libadwaita window to assign actions to extra keys.
# Writes the same config.json the mapper reads, then restarts the user unit.
# Lives in a separate module so `probe`/`run` never import GI.
#
# The key cards are the bindings for the keyboard chosen in the combo.
# Apply replaces the nested maps so a removed id stays gone on that
# device, and the other keyboards keep theirs. Add key learns from the
# selected hidraw. An optional label is only the card title; the dict
# key stays the id the mapper matches. The card caption is what the key
# does. The id is a third line only when two cards share a title, and
# always the tooltip, so the grid is scanned by the action.
#
# The key flow stays outside any preferences list. Arrow keys on a list
# would select rows instead of cards.
#
# ComboRows stay siblings in one PreferencesGroup (shown/hidden) because a
# ComboRow is a ListBoxRow and cannot live inside a Stack that is also a row.

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

import mskb  # noqa: E402

ACTION_LABELS = ("Open app", "System shortcut", "Command", "Nothing")
ACTION_KINDS = ("app", "key", "command", "none")
ACTION_ICONS = {
    "app": "system-run-symbolic",
    "key": "input-keyboard-symbolic",
    "command": "utilities-terminal-symbolic",
    "none": "action-unavailable-symbolic",
}
WINDOW_TITLE = "OffKeys"
"""Public window and app-grid title; CLI, config, and unit name stay mskb.
@tags: #format/string #model/desktop #scope/gui #subject/desktop #subject/form #type/constant
"""
WINDOW_SUBTITLE = ""
UNSAVED_SUBTITLE = "Unsaved changes"
# Selected cards use the accent fill. `.dimmed` at partial opacity fails
# contrast on that fill, so the caption returns to full opacity.
# `.dimmed` lowers opacity. The selected card removes that class; this rule
# covers a paint that happens before the selection state is applied.
# Adw.Banner's revealer keeps the height of its first allocation on 1.5, so
# the listen bar is a normal box that can show and hide.
_WINDOW_CSS = """
.key-card:selected .caption { opacity: 1; }
.mskb-banner {
  background-color: @accent_bg_color;
  padding: 6px 12px;
}
.mskb-banner label { color: @accent_fg_color; }
"""
FAVORITE_A11Y = {
    "favorites_star": "Favorite star",
    "favorites_1": "Favorite 1",
    "favorites_2": "Favorite 2",
    "favorites_3": "Favorite 3",
    "favorites_4": "Favorite 4",
    "favorites_5": "Favorite 5",
}
# One hidraw poll while Add key is watching. A tap arrives well inside this
# gap, and the UI thread still paints between reads.
WATCH_INTERVAL_MS = 50


def _installed_apps() -> list[tuple[str, str]]:
    """List visible desktop apps as (display name, stripped Exec command).
    @tags: #action/normalize #model/desktop #subject/form #type/helper
    """
    seen: set[str] = set()
    apps: list[tuple[str, str]] = []
    for info in Gio.AppInfo.get_all():
        if not info.should_show():
            continue
        name = info.get_display_name() or info.get_name() or ""
        raw = info.get_commandline() or info.get_executable() or ""
        command = mskb.strip_field_codes(raw)
        if not name or not command or command in seen:
            continue
        seen.add(command)
        apps.append((name, command))
    apps.sort(key=lambda item: item[0].casefold())
    return apps


def _fill_combo(row: Adw.ComboRow, items: list[str], searchable: bool = False) -> None:
    """Populate a ComboRow model from string labels.
    @tags: #subject/form #side-effect/mutation #type/helper
    """
    row.set_model(Gtk.StringList.new(items))
    row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
    if searchable and hasattr(row, "set_enable_search"):
        row.set_enable_search(True)


def _copy_bindings(bindings: dict[str, dict]) -> dict[str, dict]:
    """Shallow-copy binding dicts for dirty-state comparison.
    @tags: #model/binding #model/config #type/helper
    """
    return {key: dict(value) for key, value in bindings.items()}


def _copy_maps(maps: dict[str, dict[str, dict]]) -> dict[str, dict[str, dict]]:
    """Copy every device bucket so Apply dirty-state does not alias working maps.
    @tags: #model/binding #model/config #type/helper
    """
    return {vidpid: _copy_bindings(bucket) for vidpid, bucket in maps.items()}


def _as_binding(value: object) -> dict[str, str]:
    """Project one config entry onto the key/exec pair the form edits.

    A non-empty `label` is kept. Dropping it here would erase a title on the
    next Apply, because the working map is what gets written.
    @tags: #action/normalize #model/binding #type/helper
    """
    if isinstance(value, dict):
        key = value.get("key")
        command = value.get("exec")
        binding = {
            "key": "" if key is None else str(key),
            "exec": "" if command is None else str(command),
        }
        label = mskb.binding_label(value.get("label"))
        if label:
            binding["label"] = label
        return binding
    return {"key": "", "exec": ""}


def _keyboard_choices(saved: list[str], nodes: list) -> list[tuple[str, str]]:
    """Union of saved vid:pid values and plugged extra-key interfaces.

    Boot keyboard and mouse collections are not discovery options. A saved id
    stays listed while that receiver is unplugged so Apply can still open it.
    The label is the product name when any interface reported one.
    @tags: #action/normalize #model/hid #subject/form #type/helper
    """
    names: dict[str, str] = {}
    discovered: list[str] = []
    seen_found: set[str] = set()
    for node in nodes:
        vid = getattr(node, "vid", "") or ""
        pid = getattr(node, "pid", "") or ""
        if not vid or not pid:
            continue
        vidpid = f"{vid}:{pid}".lower()
        name = (getattr(node, "name", "") or "").strip()
        if name and vidpid not in names:
            names[vidpid] = name
        descriptor = getattr(node, "descriptor", b"") or b""
        if mskb.skip_interface(descriptor):
            continue
        if vidpid not in seen_found:
            seen_found.add(vidpid)
            discovered.append(vidpid)
    ordered: list[str] = []
    seen: set[str] = set()
    for item in saved:
        key = str(item).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    for key in discovered:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return [(key, names.get(key) or key) for key in ordered]


def _plugged_keyboards(nodes: list) -> set[str]:
    """vid:pid values that had an extra-key interface in this scan.

    Same skip as the combo's discovery list. A saved id missing from the
    set is still shown, with an unplugged subtitle.
    @tags: #action/normalize #model/hid #scope/gui #subject/favorites #subject/form #type/helper
    """
    found: set[str] = set()
    for node in nodes:
        vid = getattr(node, "vid", "") or ""
        pid = getattr(node, "pid", "") or ""
        if not vid or not pid:
            continue
        descriptor = getattr(node, "descriptor", b"") or b""
        if mskb.skip_interface(descriptor):
            continue
        found.add(f"{vid}:{pid}".lower())
    return found


def _exit_text(exc: SystemExit) -> str:
    """Toast text from an hidraw open that would have quit the process.
    @tags: #action/normalize #model/hid #subject/form #type/helper
    """
    code = exc.code
    if not isinstance(code, str) or not code.strip():
        return "Could not open the keyboard."
    return " ".join(code.split())


class FavoritesWindow(Adw.ApplicationWindow):
    """GTK window to edit extra-key bindings and apply them to config.json.
    @tags: #model/config #scope/gui #subject/favorites #subject/form #type/window
    """

    def __init__(self, app: Adw.Application) -> None:
        """Build the keyboard picker, key cards, and action form.
        @tags: #model/binding #model/config #model/hid #scope/gui #subject/form #type/window
        """
        super().__init__(application=app, title=WINDOW_TITLE)
        # 520 stays under the 720 breakpoint, so the window opens stacked.
        self.set_default_size(520, 680)
        # Breakpoints ignore a window with no minimum. 360 still fits the stack.
        self.set_size_request(360, 480)
        self.set_icon_name("input-keyboard")

        self._syncing = False
        self._css_installed = False
        self._allow_close = False
        self._undo: tuple[str, str, dict, int] | None = None
        self._undo_toast: Adw.Toast | None = None
        self._watch_id = 0
        self._watch_fds: list[tuple[int, object]] = []
        self._watch_baselines: dict[tuple[str, int], bytes] = {}
        self._keyboard_ids: list[str] = []
        self._keyboard_labels: list[str] = []
        self._keyboard_full: list[str] = []
        self._plugged: set[str] = set()
        self.config_path = mskb.ensure_config()
        loaded = mskb.load_config(self.config_path)
        self.devices = list(mskb.devices_from_config(loaded))
        self.original_devices = list(self.devices)
        self.maps = {
            vid: {kid: _as_binding(bind) for kid, bind in bucket.items()}
            for vid, bucket in (loaded.get("bindings") or {}).items()
        }
        self.original_maps = _copy_maps(self.maps)
        self.working: dict[str, dict] = {}
        self.current_id = None
        self.apps = _installed_apps()
        self._app_commands = [command for _, command in self.apps]
        self._key_choices = list(mskb.SYSTEM_SHORTCUT_KEYS)

        overlay = Adw.ToastOverlay()
        self.overlay = overlay
        self.set_content(overlay)

        toolbar = Adw.ToolbarView()
        overlay.set_child(toolbar)

        header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(
            title=WINDOW_TITLE, subtitle=WINDOW_SUBTITLE
        )
        header.set_title_widget(self.window_title)

        menu = Gio.Menu()
        menu.append("Restart mapper", "win.restart-mapper")
        restart = Gio.SimpleAction.new("restart-mapper", None)
        restart.connect("activate", self._on_restart_mapper)
        self.add_action(restart)
        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.menu_btn.set_primary(True)
        self.menu_btn.set_menu_model(menu)
        self.menu_btn.set_tooltip_text("Main menu")
        self.menu_btn.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Main menu"]
        )
        self.apply_btn = Gtk.Button(label="Apply")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.set_sensitive(False)
        self.apply_btn.set_receives_default(True)
        self.apply_btn.connect("clicked", self._on_apply)
        # First pack_end sits against the window controls. Apply stays there;
        # the menu is the one closer to the title.
        header.pack_end(self.apply_btn)
        header.pack_end(self.menu_btn)
        toolbar.add_top_bar(header)
        self.set_default_widget(self.apply_btn)

        self.listen_banner = self._build_listen_banner()

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(self.listen_banner)
        page.append(scrolled)
        toolbar.set_content(page)

        self._clamp = Adw.Clamp(maximum_size=1200, tightening_threshold=1200)
        scrolled.set_child(self._clamp)

        self._columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        self._columns.set_margin_top(24)
        self._columns.set_margin_bottom(24)
        self._columns.set_margin_start(12)
        self._columns.set_margin_end(12)
        self._clamp.set_child(self._columns)

        keys_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        keys_column.set_size_request(320, -1)
        keys_column.set_valign(Gtk.Align.START)
        keys_column.append(self._build_keyboard())
        keys_column.append(self._build_keys())
        self._columns.append(keys_column)

        form = self._build_form()
        form.set_valign(Gtk.Align.START)
        form.set_hexpand(True)
        self._columns.append(form)

        breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 720px")
        )
        breakpoint.add_setter(self._columns, "orientation", Gtk.Orientation.VERTICAL)
        breakpoint.add_setter(self.key_flow, "max-children-per-line", 3)
        breakpoint.add_setter(self._clamp, "maximum-size", 500)
        breakpoint.add_setter(self._clamp, "tightening-threshold", 500)
        self.add_breakpoint(breakpoint)

        self._sync_working_from_keyboard()
        self._rebuild_keys()
        self.connect("realize", self._install_css)
        self.connect("close-request", self._on_close_request)

    def _install_css(self, *_args) -> None:
        """Selected-card contrast and the listen bar color.
        @tags: #action/install #scope/gui #side-effect/mutation #subject/favorites #subject/form #type/window
        """
        if self._css_installed:
            return
        display = self.get_display() or Gdk.Display.get_default()
        if display is None:
            return
        provider = Gtk.CssProvider()
        provider.load_from_string(_WINDOW_CSS)
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._css_installed = True

    def _build_listen_banner(self) -> Gtk.Box:
        """Full-width bar shown only while Add key is listening.

        Adw.Banner stays at whatever height it had on the first allocate, so
        a later reveal never appears. Visibility on a plain box does.
        @tags: #model/hid #scope/gui #subject/favorites #subject/form #type/window
        """
        bar = Gtk.Box(spacing=12)
        bar.add_css_class("mskb-banner")
        label = Gtk.Label(
            label="Press the extra key on the keyboard.",
            hexpand=True,
            xalign=0,
        )
        label.add_css_class("heading")
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", self._stop_watch)
        bar.append(label)
        bar.append(cancel)
        # A hidden-then-shown bar inside the toolbar gets a 0 allocate unless
        # it already has a minimum height. 40 matches the accent bar.
        bar.set_size_request(-1, 40)
        bar.set_visible(False)
        return bar

    def _build_keyboard(self) -> Adw.PreferencesGroup:
        """Keyboard combo: saved devices plus plugged extra-key interfaces.
        @tags: #model/hid #subject/form #type/window
        """
        group = Adw.PreferencesGroup()
        self.keyboard_row = Adw.ComboRow(title="Keyboard")
        try:
            nodes = mskb.hidraw_devices()
        except OSError:
            nodes = []
        self._plugged = _plugged_keyboards(nodes)
        choices = _keyboard_choices(self.devices, nodes)
        self._keyboard_ids = []
        self._keyboard_labels = []
        self._keyboard_full = []
        for vidpid, label in choices:
            short = mskb.keyboard_display_name(label) or label
            self._keyboard_ids.append(vidpid)
            self._keyboard_full.append(label)
            self._keyboard_labels.append(short)
        _fill_combo(self.keyboard_row, self._keyboard_labels)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_keyboard_item)
        factory.connect("bind", self._bind_keyboard_item)
        self.keyboard_row.set_list_factory(factory)
        if self._keyboard_ids:
            self.keyboard_row.set_selected(0)
        self._describe_keyboard()
        self.keyboard_row.connect("notify::selected", self._on_keyboard_selected)
        group.add(self.keyboard_row)
        return group

    def _setup_keyboard_item(self, _factory, item: Gtk.ListItem) -> None:
        """Create the popup label for one keyboard option.
        @tags: #subject/form #type/window
        """
        label = Gtk.Label(xalign=0, halign=Gtk.Align.START)
        label.set_margin_top(6)
        label.set_margin_bottom(6)
        label.set_margin_start(12)
        label.set_margin_end(12)
        item.set_child(label)

    def _bind_keyboard_item(self, _factory, item: Gtk.ListItem) -> None:
        """Show the product name and put vid:pid on the accessible description.
        @tags: #model/hid #subject/form #type/window
        """
        label = item.get_child()
        if not isinstance(label, Gtk.Label):
            return
        index = item.get_position()
        if index < 0 or index >= len(self._keyboard_ids):
            label.set_text("")
            return
        text = self._keyboard_labels[index]
        full = self._keyboard_full[index]
        vidpid = self._keyboard_ids[index]
        description = vidpid if full == text else f"{full} {vidpid}"
        label.set_text(text)
        label.set_tooltip_text(description)
        label.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [text, description],
        )

    def _build_keys(self) -> Gtk.Box:
        """Key cards plus Add key and Remove for the working map.

        The flow is its own selection widget. Nesting it in a preferences
        list would hand arrow keys to that list, so the cards never select.
        @tags: #model/binding #subject/form #type/window
        """
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        header = Gtk.Box(spacing=6)
        title = Gtk.Label(label="Keys", xalign=0, hexpand=True)
        title.add_css_class("heading")
        header.append(title)

        controls = Gtk.Box(spacing=6)
        self.add_key_btn = Gtk.Button(icon_name="list-add-symbolic")
        self.add_key_btn.add_css_class("flat")
        self.add_key_btn.connect("clicked", self._on_add_key)
        self.remove_btn = Gtk.Button(icon_name="user-trash-symbolic")
        self.remove_btn.add_css_class("flat")
        self.remove_btn.add_css_class("destructive-action")
        self.remove_btn.connect("clicked", self._on_remove)
        controls.append(self.add_key_btn)
        controls.append(self.remove_btn)
        header.append(controls)
        section.append(header)

        self.key_flow = Gtk.FlowBox()
        self.key_flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.key_flow.set_homogeneous(True)
        self.key_flow.set_min_children_per_line(1)
        self.key_flow.set_max_children_per_line(2)
        self.key_flow.set_column_spacing(12)
        self.key_flow.set_row_spacing(12)
        self.key_flow.set_hexpand(True)
        self.key_flow.set_vexpand(False)
        self.key_flow.set_valign(Gtk.Align.START)
        self.key_flow.update_property([Gtk.AccessibleProperty.LABEL], ["Keys"])
        self.key_flow.connect("selected-children-changed", self._on_card_selected)

        self.empty_page = Adw.StatusPage(
            icon_name="input-keyboard-symbolic",
            title="No extra keys",
            description="Press Add key, then press the extra key on the keyboard.",
        )
        self.empty_add_btn = Gtk.Button(label="Add key")
        self.empty_add_btn.add_css_class("pill")
        self.empty_add_btn.add_css_class("suggested-action")
        self.empty_add_btn.connect("clicked", self._on_add_key)
        self.empty_page.set_child(self.empty_add_btn)
        self._set_add_button(watching=False)

        self.key_stack = Gtk.Stack()
        self.key_stack.add_named(self.key_flow, "keys")
        self.key_stack.add_named(self.empty_page, "empty")
        section.append(self.key_stack)
        return section

    def _build_form(self) -> Adw.PreferencesGroup:
        """Name plus action type and value rows (app, shortcut, command).
        @tags: #model/binding #subject/form #type/window
        """
        group = Adw.PreferencesGroup(title="Key", description="Select a key.")
        self.form_group = group

        self.name_row = Adw.EntryRow(title="Name")
        # EntryRow in libadwaita 1.5 is not an ActionRow: it has no subtitle,
        # and set_child replaces the entry. The hint stays a tooltip.
        self.name_row.set_tooltip_text("Shown on the card.")
        self.name_row.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION], ["Shown on the card."]
        )
        self.name_row.connect("changed", self._on_form_changed)
        group.add(self.name_row)

        self.action_row = Adw.ComboRow(title="Action")
        _fill_combo(self.action_row, list(ACTION_LABELS))
        self.action_row.connect("notify::selected", self._on_form_changed)
        group.add(self.action_row)

        self.app_row = Adw.ComboRow(title="Application")
        if self.apps:
            _fill_combo(self.app_row, [name for name, _ in self.apps], searchable=True)
        else:
            _fill_combo(self.app_row, ["No applications"])
            self.app_row.set_sensitive(False)
        self.app_row.connect("notify::selected", self._on_form_changed)
        group.add(self.app_row)

        self.key_row = Adw.ComboRow(title="Shortcut")
        self.key_row.set_subtitle("Sends F13–F24 so the desktop can record it.")
        _fill_combo(self.key_row, self._key_choices)
        self.key_row.connect("notify::selected", self._on_form_changed)
        group.add(self.key_row)

        self.command_row = Adw.EntryRow(title="Command")
        self.command_row.set_tooltip_text("Runs when the key is pressed.")
        self.command_row.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION], ["Runs when the key is pressed."]
        )
        self.command_row.connect("changed", self._on_form_changed)
        group.add(self.command_row)
        return group

    def _card_named(self, child: Gtk.FlowBoxChild, name: str) -> Gtk.Widget | None:
        """Find a named widget inside a key card.
        @tags: #model/binding #scope/gui #subject/favorites #subject/form #type/helper
        """
        box = child.get_child()
        if box is None:
            return None

        def walk(widget: Gtk.Widget | None) -> Gtk.Widget | None:
            while widget is not None:
                if widget.get_name() == name:
                    return widget
                found = walk(widget.get_first_child())
                if found is not None:
                    return found
                widget = widget.get_next_sibling()
            return None

        return walk(box)

    def _card_title(self, key_id: str) -> str:
        """Visible title for one id, before the action line.
        @tags: #action/normalize #model/binding #scope/gui #subject/favorites #subject/form #type/helper
        """
        title, _caption, _id_line = mskb.key_card_text(
            key_id,
            self.working.get(key_id),
            FAVORITE_A11Y.get(key_id, key_id),
        )
        return title

    def _kind_and_detail(self, key_id: str) -> tuple[str, str]:
        """Action kind plus the string the summary should name.

        Open app uses the desktop display name. The stored value stays the
        command, so a locale change does not rewrite config.
        @tags: #action/normalize #model/binding #model/desktop #scope/gui #subject/favorites #subject/form #type/helper
        """
        binding = self.working.get(key_id) or {}
        if not isinstance(binding, dict):
            binding = {}
        kind = self._kind_for_form(binding)
        if kind == "app":
            index = self._app_index_for(binding.get("exec") or "")
            detail = self.apps[index][0] if index is not None else ""
            return kind, detail
        if kind == "key":
            return kind, binding.get("key") or ""
        if kind == "command":
            return kind, binding.get("exec") or ""
        return "none", ""

    def _summary_for(self, key_id: str) -> str:
        """Action line for one working binding.
        @tags: #action/normalize #format/string #model/binding #scope/gui #subject/favorites #subject/form #type/helper
        """
        kind, detail = self._kind_and_detail(key_id)
        return mskb.action_summary(kind, detail)

    def _paint_key_card(self, child: Gtk.FlowBoxChild, key_id: str) -> None:
        """Show the title and what the key does. The id stays in the tooltip.

        A second card with the same title also shows the id, otherwise two
        Home cards could not be told apart.
        @tags: #action/normalize #model/binding #scope/gui #side-effect/mutation #subject/favorites #subject/form #type/window
        """
        titles = {
            item: self._card_title(item) for item in self.working
        }
        show_id = key_id in mskb.ids_sharing_title(titles)
        summary = self._summary_for(key_id)
        kind, _detail = self._kind_and_detail(key_id)
        title, caption, id_line = mskb.key_card_text(
            key_id,
            self.working.get(key_id),
            FAVORITE_A11Y.get(key_id, key_id),
            summary,
            show_id=show_id,
        )
        title_label = self._card_named(child, "title")
        caption_label = self._card_named(child, "caption")
        id_label = self._card_named(child, "id")
        icon = self._card_named(child, "icon")
        if isinstance(title_label, Gtk.Label):
            title_label.set_label(title)
        if isinstance(caption_label, Gtk.Label):
            caption_label.set_label(caption)
            caption_label.set_visible(bool(caption))
        if isinstance(id_label, Gtk.Label):
            id_label.set_label(id_line)
            id_label.set_visible(bool(id_line))
        if isinstance(icon, Gtk.Image):
            icon.set_from_icon_name(ACTION_ICONS.get(kind, ACTION_ICONS["none"]))
        child.set_tooltip_text(key_id)
        child.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [title, f"{caption}. {key_id}".strip()],
        )

    def _make_key_card(self, key_id: str) -> Gtk.FlowBoxChild:
        """One selectable card whose widget name is the HID id.
        @tags: #subject/form #type/window
        """
        icon = Gtk.Image()
        icon.set_name("icon")
        icon.set_pixel_size(24)
        icon.set_valign(Gtk.Align.CENTER)

        title_label = Gtk.Label(xalign=0, hexpand=True)
        title_label.set_name("title")
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        caption_label = Gtk.Label(xalign=0, hexpand=True)
        caption_label.set_name("caption")
        caption_label.set_ellipsize(Pango.EllipsizeMode.END)
        caption_label.add_css_class("caption")
        caption_label.add_css_class("dimmed")
        id_label = Gtk.Label(xalign=0, hexpand=True)
        id_label.set_name("id")
        id_label.set_ellipsize(Pango.EllipsizeMode.END)
        id_label.add_css_class("caption")
        id_label.add_css_class("dimmed")

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        text.append(title_label)
        text.append(caption_label)
        text.append(id_label)

        box = Gtk.Box(spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(icon)
        box.append(text)

        child = Gtk.FlowBoxChild()
        child.set_name(key_id)
        child.add_css_class("card")
        child.add_css_class("key-card")
        child.set_child(box)
        self._paint_key_card(child, key_id)
        return child

    def _repaint_cards(self) -> None:
        """Repaint every card from the working map without rebuilding.

        Rebuilding on each keystroke drops focus from the Name field. Every
        card is painted because a renamed title can show or hide the id line
        on a sibling.
        @tags: #model/binding #scope/gui #side-effect/mutation #subject/favorites #subject/form #type/window
        """
        child = self.key_flow.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.FlowBoxChild):
                key_id = child.get_name()
                if key_id in self.working:
                    self._paint_key_card(child, key_id)
            child = child.get_next_sibling()
        self._sync_card_contrast()

    def _sync_card_contrast(self) -> None:
        """Drop the dimmed caption on the selected card so it stays readable.
        @tags: #scope/gui #side-effect/mutation #subject/favorites #subject/form #type/window
        """
        child = self.key_flow.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.FlowBoxChild):
                selected = child.is_selected()
                for name in ("caption", "id"):
                    label = self._card_named(child, name)
                    if isinstance(label, Gtk.Label):
                        if selected:
                            label.remove_css_class("dimmed")
                        else:
                            label.add_css_class("dimmed")
            child = child.get_next_sibling()

    def _rebuild_keys(self, select: str | None = None) -> None:
        """Repaint the key cards and load the form for the card that stays selected.

        Selection changes while cards are removed are ignored so a rebuild does
        not write the previous form onto the wrong id.
        @tags: #model/binding #side-effect/mutation #subject/form #type/window
        """
        self._syncing = True
        child = self.key_flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.key_flow.remove(child)
            child = nxt
        if select is None:
            select = self.current_id
        if select not in self.working:
            select = next(iter(self.working), None)
        selected_child = None
        for key_id in self.working:
            card = self._make_key_card(key_id)
            self.key_flow.insert(card, -1)
            if key_id == select:
                selected_child = card
        self.current_id = select
        if selected_child is not None:
            self.key_flow.select_child(selected_child)
        self._syncing = False
        self.key_stack.set_visible_child_name("keys" if self.working else "empty")
        self.remove_btn.set_sensitive(select is not None)
        self._sync_remove_a11y()
        self._sync_card_contrast()
        self._load_form()
        self._update_dirty()

    def _selected_keyboard(self) -> str:
        """vid:pid of the keyboard combo, or empty when nothing is selected.
        @tags: #model/hid #subject/form #type/helper
        """
        index = int(self.keyboard_row.get_selected())
        if index < 0 or index >= len(self._keyboard_ids):
            return ""
        return self._keyboard_ids[index]

    def _describe_keyboard(self) -> None:
        """Short name in the combo; full HID name and plug state beside it.

        The subtitle is the scan from when the window opened. There is no
        hotplug watch.
        @tags: #model/hid #subject/form #type/window
        """
        vidpid = self._selected_keyboard()
        index = int(self.keyboard_row.get_selected())
        full = ""
        if 0 <= index < len(self._keyboard_full):
            full = self._keyboard_full[index]
        subtitle = mskb.keyboard_row_subtitle(vidpid, present=vidpid in self._plugged)
        description = f"{full} {vidpid}".strip() if full else subtitle
        self.keyboard_row.set_subtitle(subtitle)
        self.keyboard_row.set_tooltip_text(description)
        self.keyboard_row.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION], [description]
        )

    def _sync_working_from_keyboard(self) -> None:
        """Point working at the selected device's bucket, seeding 2000 once.

        A missing 2000 slot is created here, after original_maps was snapshotted
        from disk, so Apply stays on until that seed is saved. An explicit
        empty bucket is left empty.
        @tags: #model/binding #model/hid #side-effect/mutation #subject/form #type/window
        """
        vidpid = self._selected_keyboard()
        if not vidpid:
            self.working = {}
            return
        self.working = mskb.ensure_device_map(self.maps, vidpid)

    def _on_keyboard_selected(self, *_args) -> None:
        """Switch the visible map to the chosen vid:pid, and list it for Apply.

        Appends a newly chosen receiver so the mapper opens it. Already-saved
        devices still swap cards; skipping that left Favorites on a Logitech.
        @tags: #model/binding #model/hid #side-effect/mutation #subject/form #type/window
        """
        if self._syncing:
            return
        self._describe_keyboard()
        vidpid = self._selected_keyboard()
        if not vidpid:
            return
        self._write_form_to_working()
        if not any(str(item).strip().lower() == vidpid for item in self.devices):
            self.devices.append(vidpid)
        bucket = mskb.ensure_device_map(self.maps, vidpid)
        if self.working is bucket:
            self._update_dirty()
            return
        self.working = bucket
        self._rebuild_keys()

    def _on_card_selected(self, flow: Gtk.FlowBox) -> None:
        """Save the form onto the previous id, then load the newly selected one.
        @tags: #model/binding #side-effect/mutation #subject/form #type/window
        """
        if self._syncing:
            return
        selected = flow.get_selected_children()
        if not selected:
            return
        key_id = selected[0].get_name()
        if not key_id or key_id == self.current_id:
            return
        self._write_form_to_working()
        self.current_id = key_id
        self._load_form()
        self._sync_remove_a11y()
        self._sync_card_contrast()
        self._update_dirty()

    def _sync_remove_a11y(self) -> None:
        """Name the trash button after the selected card.
        @tags: #action/normalize #format/string #scope/gui #side-effect/mutation #subject/favorites #subject/form #type/window
        """
        if self.current_id and self.current_id in self.working:
            label = f"Remove {self._card_title(self.current_id)}"
        else:
            label = "Remove"
        self.remove_btn.set_tooltip_text(label)
        self.remove_btn.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def _set_add_button(self, *, watching: bool) -> None:
        """Reveal the listen banner and keep a single Cancel control.

        Both Add buttons go insensitive so capture is not started twice.
        The banner button is the way out.
        @tags: #model/hid #scope/gui #side-effect/mutation #subject/favorites #subject/form #type/window
        """
        self.listen_banner.set_visible(watching)
        tip = "Listening for a key" if watching else "Add key"
        for button in (self.add_key_btn, self.empty_add_btn):
            button.set_sensitive(not watching)
            button.set_tooltip_text(tip)
            button.update_property([Gtk.AccessibleProperty.LABEL], [tip])

    def _on_add_key(self, *_args) -> None:
        """Watch the selected keyboard, or cancel a watch already in progress.

        SystemExit from a failed open is a toast. It must not end the GTK loop.
        @tags: #action/launch #model/hid #side-effect/mutation #subject/form #type/window
        """
        if self._watch_id:
            self._stop_watch()
            return
        selected = self._selected_keyboard()
        if not selected:
            self._toast("No keyboard is selected")
            return
        try:
            opened = mskb.open_hidraw([selected])
        except SystemExit as exc:
            self._toast(_exit_text(exc), timeout=8)
            return
        self._watch_fds = list(opened)
        self._watch_baselines = {}
        self._set_add_button(watching=True)
        self._watch_id = GLib.timeout_add(WATCH_INTERVAL_MS, self._on_watch_tick)

    # ponytail: capture learns a single id per press (no chords), and the first hidraw frame is treated as idle by track_report.
    def _on_watch_tick(self) -> bool:
        """Read pending hidraw bytes and store the first learned key id.

        False stops the timeout. The watch id is cleared first so this return
        is the only removal; calling source_remove from inside the tick would
        race that.
        @tags: #action/parse #model/hid #side-effect/mutation #subject/form #type/window
        """
        if not self._watch_id:
            return False
        for fd, dev in self._watch_fds:
            while True:
                try:
                    data = os.read(fd, 64)
                except BlockingIOError:
                    break
                except OSError:
                    break
                if not data:
                    break
                report_id = data[0]
                # Report 0x21 is status on the 2000. Counting it would move
                # the idle baseline for the reports that actually name keys.
                if report_id == 0x21:
                    continue
                source = (dev.path, report_id)
                descriptor = mskb.HidDescriptor(
                    device=f"{dev.vid}:{dev.pid}", raw=dev.descriptor
                )
                baseline, ids = mskb.track_report(
                    self._watch_baselines.get(source), data, descriptor
                )
                self._watch_baselines[source] = baseline
                if not ids:
                    continue
                parsed = mskb.ParsedReport(report_id=report_id, raw=data, ids=ids)
                learned = mskb.primary_id(parsed) or ids[0]
                self._watch_id = 0
                self._close_watch_fds()
                self._set_add_button(watching=False)
                self._store_learned(learned)
                return False
        return True

    def _store_learned(self, key_id: str) -> None:
        """Insert a new id as an empty binding and select it.

        An id already in the map keeps its action. The toast says what
        happened; the raw id stays on the card tooltip. Capture never writes
        the whole id list.
        @tags: #model/binding #side-effect/mutation #subject/form #type/window
        """
        self._write_form_to_working()
        if key_id not in self.working:
            self.working[key_id] = {"key": "", "exec": ""}
            message = "Key added. Name it below."
        else:
            message = "That key is already here."
        self._rebuild_keys(select=key_id)
        self._toast(message)

    def _close_watch_fds(self) -> None:
        """Close hidraw nodes opened for capture. Safe to call twice.
        @tags: #model/hid #side-effect/mutation #type/window
        """
        for fd, _dev in self._watch_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self._watch_fds = []
        self._watch_baselines = {}

    def _stop_watch(self, *_args) -> None:
        """Cancel capture and close the hidraw nodes it opened.
        @tags: #model/hid #side-effect/mutation #subject/form #type/window
        """
        watch_id = self._watch_id
        self._watch_id = 0
        if watch_id:
            GLib.source_remove(watch_id)
        self._close_watch_fds()
        self._set_add_button(watching=False)

    def _insert_binding(self, bucket: dict, key_id: str, binding: dict, index: int) -> None:
        """Put a binding back at index. `bucket` stays the same dict.
        @tags: #model/binding #scope/gui #side-effect/mutation #subject/favorites #type/helper
        """
        items = list(bucket.items())
        items.insert(max(0, min(index, len(items))), (key_id, binding))
        bucket.clear()
        bucket.update(items)

    def _clear_undo(self) -> None:
        """Drop the pending removal so Undo cannot target another keyboard or a saved file.
        @tags: #model/binding #scope/gui #side-effect/mutation #subject/favorites #type/window
        """
        self._undo = None
        toast = self._undo_toast
        self._undo_toast = None
        if toast is not None:
            toast.dismiss()

    def _on_remove(self, *_args) -> None:
        """Drop the selected id from the working map. The file changes on Apply.

        Undo puts that binding back on the same vid:pid, even if the combo
        has moved on. A second remove replaces the undo slot. Apply drops it
        so a saved removal is not put back as a new edit.
        @tags: #model/binding #scope/gui #side-effect/mutation #subject/favorites #subject/form #type/window
        """
        if not self.current_id or self.current_id not in self.working:
            return
        self._write_form_to_working()
        key_id = self.current_id
        title = self._card_title(key_id)
        binding = dict(self.working[key_id])
        index = list(self.working).index(key_id)
        del self.working[key_id]
        remaining = list(self.working)
        next_id = None
        if remaining:
            next_id = remaining[min(index, len(remaining) - 1)]
        vidpid = self._selected_keyboard()
        self._clear_undo()
        self._undo = (vidpid, key_id, binding, index)
        self._rebuild_keys(select=next_id)
        toast = Adw.Toast.new(f"Removed {title}")
        toast.set_button_label("Undo")
        toast.connect("button-clicked", self._on_undo_remove)
        self._undo_toast = toast
        self.overlay.add_toast(toast)

    def _on_undo_remove(self, toast: Adw.Toast) -> None:
        """Restore the binding captured by the latest Remove toast.
        @tags: #model/binding #scope/gui #side-effect/mutation #subject/favorites #subject/form #type/window
        """
        toast.dismiss()
        undo = self._undo
        self._undo = None
        self._undo_toast = None
        if undo is None:
            return
        vidpid, key_id, binding, index = undo
        bucket = self.maps.get(vidpid)
        if not isinstance(bucket, dict) or key_id in bucket:
            return
        self._write_form_to_working()
        self._insert_binding(bucket, key_id, binding, index)
        if self._selected_keyboard() == vidpid:
            self._rebuild_keys(select=key_id)
            return
        self._update_dirty()

    def _on_close_request(self, *_args) -> bool:
        """Stop capture. A dirty map asks before the window actually closes.

        True keeps the window. Discard sets a flag and closes again so this
        handler does not open a second dialog.
        @tags: #model/config #model/hid #scope/gui #side-effect/mutation #subject/favorites #subject/form #type/window
        """
        self._stop_watch()
        if self._allow_close:
            return False
        self._write_form_to_working()
        if not self._is_dirty():
            return False
        dialog = Adw.AlertDialog.new(
            "Discard unsaved changes?",
            "Apply saves them and restarts the mapper.",
        )
        dialog.add_response("keep", "Keep editing")
        dialog.add_response("discard", "Discard")
        dialog.set_response_appearance(
            "discard", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("keep")
        dialog.set_close_response("keep")
        dialog.connect("response", self._on_discard_response)
        dialog.present(self)
        return True

    def _on_discard_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        """Close only when the user picks Discard.
        @tags: #model/config #scope/gui #side-effect/mutation #subject/favorites #subject/form #type/window
        """
        if response != "discard":
            return
        self._allow_close = True
        self.close()

    def _on_form_changed(self, *_args) -> None:
        """React to form edits: sync working bindings and dirty state.
        @tags: #side-effect/mutation #subject/form #type/window
        """
        if self._syncing:
            return
        self._write_form_to_working()
        self._show_value_rows(self._selected_kind())
        self._repaint_cards()
        self._refresh_form_heading()
        self._sync_remove_a11y()
        self._update_dirty()

    def _selected_kind(self) -> str:
        """Map action combo selection to binding kind id.
        @tags: #action/normalize #model/binding #subject/form #type/helper
        """
        index = int(self.action_row.get_selected())
        if index < 0 or index >= len(ACTION_KINDS):
            return "none"
        return ACTION_KINDS[index]

    def _app_index_for(self, command: str) -> int | None:
        """Resolve installed-app combo index for a stored exec command.
        @tags: #action/normalize #model/desktop #subject/form #type/helper
        """
        stripped = mskb.strip_field_codes(command)
        for index, stored in enumerate(self._app_commands):
            if stored == stripped:
                return index
        return None

    def _kind_for_form(self, binding: dict) -> str:
        """Classify binding for the action combo, treating known exec as Open app.
        @tags: #action/normalize #model/binding #subject/form #type/helper
        """
        kind = mskb.kind_for_binding(binding)
        if kind == "command" and self._app_index_for(binding.get("exec") or "") is not None:
            return "app"
        return kind

    def _load_form(self) -> None:
        """Populate form widgets from the selected working binding.
        @tags: #model/binding #model/config #side-effect/mutation #subject/form #type/window
        """
        if not self.current_id or self.current_id not in self.working:
            self._syncing = True
            self.name_row.set_text("")
            self.action_row.set_selected(ACTION_KINDS.index("none"))
            self.command_row.set_text("")
            self._syncing = False
            self._show_value_rows("none")
            self.form_group.set_sensitive(False)
            self._refresh_form_heading()
            return
        binding = self.working[self.current_id]
        kind = self._kind_for_form(binding)
        exec_val = binding.get("exec") or ""
        key_val = binding.get("key") or ""

        self.form_group.set_sensitive(True)
        self._syncing = True
        self.name_row.set_text(mskb.binding_label(binding.get("label")))
        self.action_row.set_selected(ACTION_KINDS.index(kind))

        if self.apps:
            app_index = self._app_index_for(exec_val)
            self.app_row.set_selected(0 if app_index is None else app_index)

        choices = mskb.shortcut_key_choices(key_val)
        if choices != self._key_choices:
            self._key_choices = choices
            _fill_combo(self.key_row, self._key_choices)
        selected_key = 0
        target = key_val.strip()
        if target:
            for index, name in enumerate(self._key_choices):
                if name.upper() == target.upper():
                    selected_key = index
                    break
        self.key_row.set_selected(selected_key)
        self.command_row.set_text(exec_val)
        self._syncing = False
        self._show_value_rows(kind)
        self._refresh_form_heading()

    def _refresh_form_heading(self) -> None:
        """Title the form with the selected key and what it currently does.
        @tags: #action/normalize #format/string #model/binding #scope/gui #side-effect/mutation #subject/favorites #subject/form #type/window
        """
        if not self.current_id or self.current_id not in self.working:
            self.form_group.set_title("Key")
            self.form_group.set_description("Select a key.")
            return
        self.form_group.set_title(self._card_title(self.current_id))
        self.form_group.set_description(self._summary_for(self.current_id))

    def _show_value_rows(self, kind: str) -> None:
        """Show app, shortcut, or command row for the selected action kind.

        Nothing is explained on the Action row. The other kinds use the
        value row's own subtitle, so this one stays blank.
        @tags: #subject/form #side-effect/mutation #type/window
        """
        self.app_row.set_visible(kind == "app")
        self.key_row.set_visible(kind == "key")
        self.command_row.set_visible(kind == "command")
        editing = bool(self.current_id and self.current_id in self.working)
        if editing and kind == "none":
            self.action_row.set_subtitle("The key stays quiet.")
        else:
            self.action_row.set_subtitle("")

    def _combo_string(self, row: Adw.ComboRow, fallback: list[str]) -> str:
        """Read the selected label from a ComboRow model.
        @tags: #subject/form #type/helper
        """
        selected = int(row.get_selected())
        if selected < 0 or selected >= len(fallback):
            return ""
        return fallback[selected]

    def _write_form_to_working(self) -> None:
        """Persist the visible form into working bindings for current_id.

        `binding_for_kind` stays exclusive on key and exec. The title is
        attached afterward so an action edit does not drop it, and a blank
        title is left off the dict.
        @tags: #action/normalize #model/binding #side-effect/mutation #subject/form #type/window
        """
        if not self.current_id or self.current_id not in self.working:
            return
        kind = self._selected_kind()
        if kind == "app":
            value = self._combo_string(self.app_row, self._app_commands)
        elif kind == "key":
            value = self._combo_string(self.key_row, self._key_choices)
        elif kind == "command":
            value = self.command_row.get_text()
        else:
            value = ""
        binding = mskb.binding_for_kind(kind, value)
        label = mskb.binding_label(self.name_row.get_text())
        if label:
            binding["label"] = label
        self.working[self.current_id] = binding

    def _is_dirty(self) -> bool:
        """True when the key map or device list differs from the last save.
        @tags: #model/binding #model/config #scope/gui #subject/favorites #subject/form #type/helper
        """
        return self.maps != self.original_maps or self.devices != self.original_devices

    def _update_dirty(self) -> None:
        """Enable Apply and retitle the window when there is something to save.
        @tags: #side-effect/mutation #subject/form #type/window
        """
        dirty = self._is_dirty()
        self.apply_btn.set_sensitive(dirty)
        self.window_title.set_subtitle(UNSAVED_SUBTITLE if dirty else WINDOW_SUBTITLE)

    def _toast(self, title: str, timeout: int = 5) -> None:
        """Show a transient status toast on the window overlay.
        @tags: #side-effect/mutation #subject/form #type/window
        """
        toast = Adw.Toast(title=title)
        toast.set_timeout(timeout)
        self.overlay.add_toast(toast)

    def _reload_mapper(self, *, after_save: bool) -> None:
        """Restart the systemd mapper and toast status/reason.
        @tags: #side-effect/process #subject/form #type/window
        """
        status, reason = mskb.restart_mapper()
        if status == "ok":
            if after_save:
                self._toast("Keys updated")
            else:
                self._toast(mskb.mapper_status_message(status, reason))
            return
        detail = mskb.mapper_status_message(status, reason)
        if after_save:
            self._toast(f"Keys saved. {detail}", timeout=8)
        else:
            self._toast(detail, timeout=8)

    def _on_restart_mapper(self, *_args) -> None:
        """Menu action: reload mapper without rewriting config.
        @tags: #side-effect/process #subject/form #type/window
        """
        self._reload_mapper(after_save=False)

    def _on_apply(self, *_args) -> None:
        """Replace nested bindings and devices in config, then restart the mapper.

        The payload is every device map, not only the visible slice, so Apply
        cannot wipe the other keyboard. replace_bindings drops ids removed
        from a bucket. A merge would put those keys back on the next load.
        @tags: #action/save #model/binding #model/config #side-effect/file #side-effect/mutation #side-effect/process #subject/form #type/window
        """
        if not self.apply_btn.get_sensitive():
            return
        self._write_form_to_working()
        try:
            mskb.save_config(
                self.config_path,
                {"bindings": _copy_maps(self.maps), "devices": list(self.devices)},
                replace_bindings=True,
            )
        except OSError:
            self._toast("Could not save keys.")
            return
        self._clear_undo()
        self.original_maps = _copy_maps(self.maps)
        self.original_devices = list(self.devices)
        self._update_dirty()
        self._reload_mapper(after_save=True)


class FavoritesApp(Adw.Application):
    """Adw application entry for the favorites configuration window.
    @tags: #scope/gui #subject/favorites #type/window
    """

    def __init__(self) -> None:
        super().__init__(application_id="dev.mskb.Favorites")
        self.connect("activate", self._on_activate)

    def _on_activate(self, _app: Adw.Application) -> None:
        """Present the main FavoritesWindow on application activate.
        @tags: #action/launch #scope/gui #type/window
        """
        window = self.props.active_window
        if window is None:
            window = FavoritesWindow(self)
        window.present()


def run_gui() -> int:
    """Run the GTK main loop for the favorites UI.
    @tags: #action/launch #scope/gui #subject/form #type/command
    """
    app = FavoritesApp()
    return app.run(["mskb"])
