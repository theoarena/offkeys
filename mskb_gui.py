# mskb_gui.py
#
# GTK4/libadwaita window to assign actions to extra keys.
# Writes the same config.json the mapper reads, then restarts the user unit.
# Lives in a separate module so `probe`/`run` never import GI.
#
# The key cards are every binding in the file. Apply replaces that map so a
# removed id stays gone. Add key learns from the keyboard chosen in the combo.
# An optional label is only the card title; the dict key stays the id the
# mapper matches. The id is the second line only when that title is set.
#
# ComboRows stay siblings in one PreferencesGroup (shown/hidden) because a
# ComboRow is a ListBoxRow and cannot live inside a Stack that is also a row.

from __future__ import annotations

import json
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

import mskb  # noqa: E402

ACTION_LABELS = ("Open app", "System shortcut", "Command", "Nothing")
ACTION_KINDS = ("app", "key", "command", "none")
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


def _stored_bindings(path: Path) -> dict[str, dict[str, str]]:
    """Bindings written in the file, not the favorites load_config merges in.

    An empty or missing map stays empty. Filling the six defaults here would
    show keys Apply had already removed.
    @tags: #model/binding #model/config #type/helper
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    raw = data.get("bindings") if isinstance(data, dict) else None
    if not isinstance(raw, dict) or not raw:
        return {}
    return {str(key_id): _as_binding(value) for key_id, value in raw.items()}


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
        @tags: #model/config #scope/gui #subject/form #type/window
        """
        super().__init__(application=app, title="Microsoft Keyboard")
        self.set_default_size(560, 640)
        self.set_icon_name("input-keyboard")

        self._syncing = False
        self._watch_id = 0
        self._watch_fds: list[tuple[int, object]] = []
        self._watch_baselines: dict[tuple[str, int], bytes] = {}
        self._keyboard_ids: list[str] = []
        self._keyboard_labels: list[str] = []
        self.config_path = mskb.ensure_config()
        loaded = mskb.load_config(self.config_path)
        self.devices = list(mskb.devices_from_config(loaded))
        self.original_devices = list(self.devices)
        self.working = _stored_bindings(self.config_path)
        self.original = _copy_bindings(self.working)
        self.current_id = next(iter(self.working), None)
        self.apps = _installed_apps()
        self._app_commands = [command for _, command in self.apps]
        self._key_choices = list(mskb.SYSTEM_SHORTCUT_KEYS)

        overlay = Adw.ToastOverlay()
        self.overlay = overlay
        self.set_content(overlay)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay.set_child(root)

        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(title="Microsoft Keyboard", subtitle="Extra keys")
        )
        self.restart_btn = Gtk.Button()
        self.restart_btn.set_icon_name("view-refresh-symbolic")
        self.restart_btn.set_tooltip_text("Restart Mapper")
        self.restart_btn.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Restart Mapper"]
        )
        self.restart_btn.connect("clicked", self._on_restart_mapper)
        header.pack_start(self.restart_btn)

        self.apply_btn = Gtk.Button(label="Apply")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.set_sensitive(False)
        self.apply_btn.set_receives_default(True)
        self.apply_btn.connect("clicked", self._on_apply)
        header.pack_end(self.apply_btn)
        root.append(header)
        self.set_default_widget(self.apply_btn)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        root.append(scrolled)

        clamp = Adw.Clamp(maximum_size=500, tightening_threshold=400)
        scrolled.set_child(clamp)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        inner.set_margin_top(24)
        inner.set_margin_bottom(24)
        inner.set_margin_start(12)
        inner.set_margin_end(12)
        clamp.set_child(inner)

        inner.append(self._build_keyboard())
        inner.append(self._build_keys())
        inner.append(self._build_form())
        self._rebuild_keys()
        self.connect("close-request", self._on_close_request)

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
        choices = _keyboard_choices(self.devices, nodes)
        self._keyboard_ids = [vidpid for vidpid, _label in choices]
        self._keyboard_labels = [label for _vidpid, label in choices]
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
        vidpid = self._keyboard_ids[index]
        label.set_text(text)
        label.set_tooltip_text(vidpid)
        label.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [text, vidpid],
        )

    def _build_keys(self) -> Gtk.Box:
        """Key cards plus Add key and Remove for the working map.

        The flow is its own selection widget. Nesting it in a preferences
        list would hand arrow keys to that list, so the cards never select.
        @tags: #model/binding #subject/form #type/window
        """
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header = Gtk.Box(spacing=6)
        title = Gtk.Label(label="Keys", xalign=0, hexpand=True)
        title.add_css_class("heading")
        header.append(title)

        controls = Gtk.Box(spacing=6)
        self.add_key_btn = Gtk.Button(label="Add key")
        self.add_key_btn.connect("clicked", self._on_add_key)
        self._set_add_button(watching=False)
        self.remove_btn = Gtk.Button(label="Remove")
        self.remove_btn.set_tooltip_text("Remove the selected key")
        self.remove_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Remove"])
        self.remove_btn.connect("clicked", self._on_remove)
        controls.append(self.add_key_btn)
        controls.append(self.remove_btn)
        header.append(controls)
        section.append(header)

        self.key_flow = Gtk.FlowBox()
        self.key_flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.key_flow.set_homogeneous(True)
        self.key_flow.set_min_children_per_line(1)
        self.key_flow.set_max_children_per_line(3)
        self.key_flow.set_column_spacing(6)
        self.key_flow.set_row_spacing(6)
        self.key_flow.set_hexpand(True)
        self.key_flow.set_vexpand(False)
        self.key_flow.set_valign(Gtk.Align.START)
        self.key_flow.update_property([Gtk.AccessibleProperty.LABEL], ["Keys"])
        self.key_flow.connect("selected-children-changed", self._on_card_selected)
        section.append(self.key_flow)
        return section

    def _build_form(self) -> Adw.PreferencesGroup:
        """Name plus action type and value rows (app, shortcut, command).
        @tags: #model/binding #subject/form #type/window
        """
        group = Adw.PreferencesGroup()
        self.form_group = group

        self.name_row = Adw.EntryRow(title="Name")
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
        _fill_combo(self.key_row, self._key_choices)
        self.key_row.connect("notify::selected", self._on_form_changed)
        group.add(self.key_row)

        self.command_row = Adw.EntryRow(title="Command")
        self.command_row.connect("changed", self._on_form_changed)
        group.add(self.command_row)
        return group

    def _card_label(self, child: Gtk.FlowBoxChild, name: str) -> Gtk.Label | None:
        """Find a named label inside a key card.
        @tags: #subject/form #type/helper
        """
        box = child.get_child()
        if box is None:
            return None
        widget = box.get_first_child()
        while widget is not None:
            if isinstance(widget, Gtk.Label) and widget.get_name() == name:
                return widget
            widget = widget.get_next_sibling()
        return None

    def _paint_key_card(self, child: Gtk.FlowBoxChild, key_id: str) -> None:
        """Show the custom title, and the id under it when one is set.

        Two keys can share a title. The caption keeps the HID id visible
        without making it the name the grid is scanned by.
        @tags: #subject/form #side-effect/mutation #type/window
        """
        title, caption = mskb.key_card_text(
            key_id,
            self.working.get(key_id),
            FAVORITE_A11Y.get(key_id, key_id),
        )
        title_label = self._card_label(child, "title")
        caption_label = self._card_label(child, "caption")
        if title_label is not None:
            title_label.set_label(title)
        if caption_label is not None:
            caption_label.set_label(caption)
            caption_label.set_visible(bool(caption))
        child.set_tooltip_text(key_id if caption else title)
        child.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [title, caption],
        )

    def _make_key_card(self, key_id: str) -> Gtk.FlowBoxChild:
        """One selectable card whose widget name is the HID id.
        @tags: #subject/form #type/window
        """
        title_label = Gtk.Label(xalign=0.5, hexpand=True)
        title_label.set_name("title")
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_label.set_max_width_chars(16)
        caption_label = Gtk.Label(xalign=0.5, hexpand=True)
        caption_label.set_name("caption")
        caption_label.set_ellipsize(Pango.EllipsizeMode.END)
        caption_label.set_max_width_chars(18)
        caption_label.add_css_class("caption")
        caption_label.add_css_class("dimmed")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(title_label)
        box.append(caption_label)

        child = Gtk.FlowBoxChild()
        child.set_name(key_id)
        child.add_css_class("card")
        child.set_child(box)
        self._paint_key_card(child, key_id)
        return child

    def _refresh_selected_card(self) -> None:
        """Repaint the selected key card from the working map without rebuilding.

        Rebuilding on each keystroke drops focus from the Name field.
        @tags: #subject/form #side-effect/mutation #type/window
        """
        if not self.current_id:
            return
        selected = self.key_flow.get_selected_children()
        if not selected:
            return
        child = selected[0]
        if not isinstance(child, Gtk.FlowBoxChild) or child.get_name() != self.current_id:
            return
        self._paint_key_card(child, self.current_id)

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
        self.remove_btn.set_sensitive(select is not None)
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
        """Put the selected vid:pid on the combo's accessible description.
        @tags: #model/hid #subject/form #type/window
        """
        vidpid = self._selected_keyboard()
        text = vidpid or "No keyboard selected"
        self.keyboard_row.set_tooltip_text(text)
        self.keyboard_row.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION], [text]
        )

    def _on_keyboard_selected(self, *_args) -> None:
        """Append a newly chosen vid:pid so Apply opens that keyboard.
        @tags: #model/hid #side-effect/mutation #subject/form #type/window
        """
        if self._syncing:
            return
        self._describe_keyboard()
        vidpid = self._selected_keyboard()
        if not vidpid:
            return
        if any(str(item).strip().lower() == vidpid for item in self.devices):
            return
        self.devices.append(vidpid)
        self._update_dirty()

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
        self._update_dirty()

    def _set_add_button(self, *, watching: bool) -> None:
        """Toggle Add key between capture and cancel so the action stays named.
        @tags: #subject/form #side-effect/mutation #type/window
        """
        label = "Cancel" if watching else "Add key"
        self.add_key_btn.set_label(label)
        self.add_key_btn.set_tooltip_text(label)
        self.add_key_btn.update_property([Gtk.AccessibleProperty.LABEL], [label])

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
                source = (dev.iface, report_id)
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
        """Insert a new id as an empty binding, select it, and name it in a toast.

        An id already in the map keeps its action. Capture never writes the
        whole id list.
        @tags: #model/binding #side-effect/mutation #subject/form #type/window
        """
        self._write_form_to_working()
        if key_id not in self.working:
            self.working[key_id] = {"key": "", "exec": ""}
        self._rebuild_keys(select=key_id)
        self._toast(key_id)

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

    def _stop_watch(self) -> None:
        """Cancel capture and close the hidraw nodes it opened.
        @tags: #model/hid #side-effect/mutation #subject/form #type/window
        """
        watch_id = self._watch_id
        self._watch_id = 0
        if watch_id:
            GLib.source_remove(watch_id)
        self._close_watch_fds()
        self._set_add_button(watching=False)

    def _on_remove(self, *_args) -> None:
        """Drop the selected id from the working map. The file changes on Apply.
        @tags: #model/binding #side-effect/mutation #subject/form #type/window
        """
        if not self.current_id or self.current_id not in self.working:
            return
        ids = list(self.working)
        index = ids.index(self.current_id)
        del self.working[self.current_id]
        remaining = list(self.working)
        next_id = None
        if remaining:
            next_id = remaining[min(index, len(remaining) - 1)]
        self._rebuild_keys(select=next_id)

    def _on_close_request(self, *_args) -> bool:
        """Close capture fds before the window goes away. False lets GTK close.
        @tags: #model/hid #side-effect/mutation #type/window
        """
        self._stop_watch()
        return False

    def _on_form_changed(self, *_args) -> None:
        """React to form edits: sync working bindings and dirty state.
        @tags: #side-effect/mutation #subject/form #type/window
        """
        if self._syncing:
            return
        self._write_form_to_working()
        self._show_value_rows(self._selected_kind())
        self._refresh_selected_card()
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

    def _show_value_rows(self, kind: str) -> None:
        """Show app, shortcut, or command row for the selected action kind.
        @tags: #subject/form #side-effect/mutation #type/window
        """
        self.app_row.set_visible(kind == "app")
        self.key_row.set_visible(kind == "key")
        self.command_row.set_visible(kind == "command")

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

    def _update_dirty(self) -> None:
        """Enable Apply when the key map or device list differs from the last save.
        @tags: #side-effect/mutation #subject/form #type/window
        """
        dirty = self.working != self.original or self.devices != self.original_devices
        self.apply_btn.set_sensitive(dirty)

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
        """Header action: reload mapper without rewriting config.
        @tags: #side-effect/process #subject/form #type/window
        """
        self._reload_mapper(after_save=False)

    def _on_apply(self, *_args) -> None:
        """Replace bindings and devices in config, then restart the mapper.

        replace_bindings drops ids removed from the working map. A merge would
        put those keys back on the next load.
        @tags: #action/save #model/config #side-effect/file #side-effect/mutation #side-effect/process #subject/form #type/window
        """
        if not self.apply_btn.get_sensitive():
            return
        self._write_form_to_working()
        try:
            mskb.save_config(
                self.config_path,
                {"bindings": dict(self.working), "devices": list(self.devices)},
                replace_bindings=True,
            )
        except OSError:
            self._toast("Could not save keys.")
            return
        self.original = _copy_bindings(self.working)
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
