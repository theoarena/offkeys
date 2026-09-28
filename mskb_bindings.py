# mskb_bindings.py
#
# config.json load/save and GUI exclusive-mode helpers.
# Bindings are nested by vid:pid so two keyboards do not share a map.
# A flat file from before that nest is migrated on load. The mapper still
# dual-fires when both key and exec are set; this module only projects
# that contract for the favorites window. An optional `label` is a display
# title and is not part of that pair.
# Card captions and keyboard labels are pure strings so the window tests
# stay free of GTK.
#
# Used by: mskb_gui.py (via mskb), mskb_mapper.py, mskb_install.py
# See also: mskb_paths.py

from __future__ import annotations

import json
from pathlib import Path

from mskb_paths import REPO_ROOT, _chown_user, config_path

"""Default vid:pid for the Wireless Keyboard 2000.
@tags: #format/string #model/config #model/hid #type/constant
"""
KEYBOARD_2000 = "045e:0745"
"""Factory favorite bindings seeded only for KEYBOARD_2000.
@tags: #model/binding #model/config #model/favorite #type/constant
"""
DEFAULT_2000_BINDINGS = {
    "favorites_1": {"key": "F14", "exec": ""},
    "favorites_2": {"key": "F15", "exec": ""},
    "favorites_3": {"key": "F16", "exec": ""},
    "favorites_4": {"key": "F17", "exec": ""},
    "favorites_5": {"key": "F18", "exec": ""},
    "favorites_star": {"key": "F13", "exec": ""},
}
DEFAULT_CONFIG = {
    "_comment": (
        "Match keys from `mskb.py probe`. `key` is emitted via uinput "
        "(bind it in Zorin Settings → Keyboard). `exec` runs on press."
    ),
    "devices": [KEYBOARD_2000],
    "bindings": {
        KEYBOARD_2000: {
            key_id: dict(binding)
            for key_id, binding in DEFAULT_2000_BINDINGS.items()
        },
    },
}

# Strip order for the GUI. Defaults still list 1–5 then star to match the example JSON.
"""Favorite binding ids in strip order (star, then 1–5).
@tags: #model/favorite #model/config #subject/favorites #subject/form #type/constant
"""
FAVORITE_IDS = (
    "favorites_star",
    "favorites_1",
    "favorites_2",
    "favorites_3",
    "favorites_4",
    "favorites_5",
)
"""F13–F24 names offered as system shortcut bindings in the GUI.
@tags: #model/shortcut #model/config #subject/form #type/constant
"""
SYSTEM_SHORTCUT_KEYS = tuple(f"F{n}" for n in range(13, 25))
"""`.desktop` Exec field codes stripped before hotkey commands run.
@tags: #model/desktop #format/string #type/constant
"""
_DESKTOP_FIELD_CODES = {
    "%f",
    "%F",
    "%u",
    "%U",
    "%d",
    "%D",
    "%n",
    "%N",
    "%i",
    "%c",
    "%k",
    "%v",
    "%m",
}


def devices_from_config(config: dict) -> list[str]:
    """HID ids a saved config wants opened, as `vid:pid` strings.

    Missing, empty, or non-list `devices` falls back to the list on
    DEFAULT_CONFIG so an older config.json still names the keyboard.
    This module does not open the devices.
    @tags: #action/normalize #model/config #type/helper
    """
    devices = config.get("devices")
    if isinstance(devices, list) and devices:
        return [str(item).strip().lower() for item in devices if str(item).strip()]
    return [str(item) for item in DEFAULT_CONFIG["devices"]]


def is_binding_entry(value: object) -> bool:
    """True when a dict is one key/exec binding, not a per-device map.

    Nested config stores maps under vid:pid. A value with `key` or `exec`
    is the old flat shape (or one binding inside a device map).
    @tags: #action/normalize #model/binding #model/config #type/helper
    """
    return isinstance(value, dict) and ("key" in value or "exec" in value)


def _vidpid_prefix(key_id: str) -> str:
    """Leading vid:pid on a generic learned id, or empty.

    `046d:c52b:000c:0182` names its product. `favorites_1` does not.
    """
    parts = str(key_id).strip().lower().split(":")
    if len(parts) < 2 or any(len(part) != 4 for part in parts[:2]):
        return ""
    try:
        int(parts[0], 16)
        int(parts[1], 16)
    except ValueError:
        return ""
    return f"{parts[0]}:{parts[1]}"


def _device_for_flat_id(key_id: str, devices: list[str]) -> str:
    """Which product owns a flat binding id while migrating old files.

    Prefixed generic ids stay on that product. Unprefixed 2000 names
    (`favorites_*`, `chat`) go to KEYBOARD_2000 when that device is listed,
    else to the only listed device, else KEYBOARD_2000.
    @tags: #action/normalize #model/binding #model/config #model/hid #type/helper
    """
    prefix = _vidpid_prefix(key_id)
    if prefix:
        return prefix
    if KEYBOARD_2000 in devices:
        return KEYBOARD_2000
    if len(devices) == 1:
        return devices[0]
    return KEYBOARD_2000


def nested_bindings(config: dict) -> dict[str, dict[str, dict]]:
    """Normalize `bindings` to `{vid:pid: {key_id: binding}}`.

    A flat map (today's files) is split into device buckets. A nested map
    is copied with lowercase device keys. Empty stays empty: this must not
    seed Wireless Keyboard 2000 favorites into a Logitech file.
    @tags: #action/normalize #model/binding #model/config #type/helper
    """
    raw = config.get("bindings")
    if not isinstance(raw, dict) or not raw:
        return {}
    if any(is_binding_entry(value) for value in raw.values()):
        devices = devices_from_config(config)
        maps: dict[str, dict[str, dict]] = {}
        for key_id, value in raw.items():
            if not is_binding_entry(value):
                continue
            vidpid = _device_for_flat_id(str(key_id), devices)
            maps.setdefault(vidpid, {})[str(key_id)] = dict(value)
        return maps
    maps = {}
    for vidpid, bucket in raw.items():
        key = str(vidpid).strip().lower()
        if not key or not isinstance(bucket, dict):
            continue
        maps[key] = {
            str(kid): dict(bind) if isinstance(bind, dict) else {"key": "", "exec": ""}
            for kid, bind in bucket.items()
        }
    return maps


def bindings_for_device(maps: dict, vidpid: str) -> dict:
    """Slice of nested bindings for one product, or empty.

    A missing device is not seeded. An empty stored map is returned as-is
    so the GUI can keep a wiped 2000 slot empty.
    @tags: #action/normalize #model/binding #model/config #type/helper
    """
    bucket = maps.get(str(vidpid).strip().lower())
    if bucket is None:
        return {}
    return bucket


def ensure_device_map(maps: dict, vidpid: str) -> dict:
    """Return the device bucket, creating it when the slot is new.

    KEYBOARD_2000 gets the factory Favorites only the first time the slot
    appears. An existing empty map stays empty so Remove+Apply sticks.
    ponytail: identity is product vid:pid, not USB serial. Two identical
    2000 dongles share one map; key by phys/serial if that is ever required.
    @tags: #action/normalize #model/binding #model/config #side-effect/mutation #type/helper
    """
    key = str(vidpid).strip().lower()
    if key in maps:
        return maps[key]
    if key == KEYBOARD_2000:
        maps[key] = {
            kid: dict(bind) for kid, bind in DEFAULT_2000_BINDINGS.items()
        }
    else:
        maps[key] = {}
    return maps[key]


def put_learned_binding(
    config: dict,
    vidpid: str,
    target: str,
    primary: str,
    binding: dict,
) -> dict:
    """Write a learned id into that hidraw's bucket and list the device.

    `learn` captures one press. The human name and the raw primary id both
    live on the product that produced the report, not on a global map.
    @tags: #action/save #model/binding #model/config #side-effect/mutation #type/helper
    """
    maps = nested_bindings(config)
    bucket = ensure_device_map(maps, vidpid)
    stored = dict(binding)
    bucket[target] = stored
    if primary != target:
        bucket[primary] = {
            "key": stored.get("key", ""),
            "exec": stored.get("exec", ""),
        }
    config["bindings"] = maps
    devices = devices_from_config(config)
    key = str(vidpid).strip().lower()
    if key not in devices:
        devices = list(devices)
        devices.append(key)
    config["devices"] = devices
    return config


def _copy_nested_maps(maps: dict) -> dict:
    return {
        vid: {kid: dict(bind) for kid, bind in bucket.items()}
        for vid, bucket in maps.items()
    }


def _default_document() -> dict:
    return {
        "_comment": DEFAULT_CONFIG["_comment"],
        "devices": list(DEFAULT_CONFIG["devices"]),
        "bindings": _copy_nested_maps(DEFAULT_CONFIG["bindings"]),
    }


def load_config(path: Path) -> dict:
    """Read config and nest bindings. Do not overlay factory Favorites.

    Merging DEFAULT_CONFIG bindings into every file put 2000 keys onto a
    Logitech map in mapper memory. Missing file still returns the 2000 seed.
    @tags: #action/normalize #model/binding #model/config #side-effect/file #type/helper
    """
    if not path.exists():
        return _default_document()
    with path.open() as fh:
        data = json.load(fh)
    data["bindings"] = nested_bindings(data)
    return data


def ensure_config() -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _chown_user(path.parent)
    if not path.exists():
        example = REPO_ROOT / "config.example.json"
        payload = example.read_text() if example.exists() else json.dumps(DEFAULT_CONFIG, indent=2)
        path.write_text(payload)
        _chown_user(path)
        print(f"Wrote default config: {path}")
    return path


def strip_field_codes(command: str) -> str:
    """Drop .desktop field codes and Flatpak file-forwarding from an Exec line.

    A hotkey has no URI or file list. Copying Exec as-is would leave `%U` / `@@`
    tokens that make Flatpak wait for a file that never arrives.
    @tags: #action/normalize #format/string #model/desktop #subject/desktop #type/helper
    """
    tokens: list[str] = []
    skipping_at = False
    for token in command.split():
        if skipping_at:
            if token == "@@":
                skipping_at = False
            continue
        if token == "--file-forwarding" or token in _DESKTOP_FIELD_CODES:
            continue
        if token.startswith("@@"):
            skipping_at = True
            continue
        tokens.append(token)
    return " ".join(tokens)


def kind_for_binding(binding: dict) -> str:
    """Exclusive GUI mode for a JSON binding.

    Runtime still dual-fires when both fields are set. The GUI projects that
    as Command (exec wins) so the next Apply on this key drops `key`.
    @tags: #action/normalize #model/binding #model/config #subject/form #type/helper
    """
    command = (binding.get("exec") or "").strip()
    key = (binding.get("key") or "").strip()
    if command:
        return "command"
    if key:
        return "key"
    return "none"


def binding_label(value: object) -> str:
    """Display title stored on a binding, or empty when none is set.

    Whitespace is not a title. The mapper never reads this field; the HID
    id stays the dict key that matches a report.
    @tags: #action/normalize #model/binding #subject/form #type/helper
    """
    if value is None:
        return ""
    return str(value).strip()


# A longer shell line does not fit a card. The form still stores the full command.
_COMMAND_SUMMARY_MAX = 32


def action_summary(kind: str, detail: str = "") -> str:
    """One line for what a key does, using a name the caller already resolved.

    App names come from the desktop (`Gio.AppInfo` display name). This helper
    does not translate them. A command longer than a card caption becomes a
    generic line so the grid stays scannable.
    @tags: #action/normalize #format/string #model/binding #model/shortcut #subject/form #type/helper
    """
    detail = (detail or "").strip()
    if kind == "app":
        return f"Opens {detail}" if detail else "Opens an app"
    if kind == "key":
        return f"Sends {detail}" if detail else "Sends a shortcut"
    if kind == "command":
        if detail and len(detail) <= _COMMAND_SUMMARY_MAX:
            return detail
        return "Runs a command"
    return "Does nothing"


def keyboard_display_name(raw: str) -> str:
    """Short product label for the keyboard combo.

    HID names often repeat the vendor and append a mark (`Microsoft Microsoft®`).
    The full string stays available as the tooltip; the closed row needs the
    short form so it is not ellipsized into noise.
    @tags: #action/normalize #format/string #model/hid #subject/form #type/helper
    """
    text = raw or ""
    for mark in ("®", "™", "©"):
        text = text.replace(mark, "")
    words = text.split()
    if len(words) >= 2 and words[0].casefold() == words[1].casefold():
        del words[1]
    return " ".join(words)


def keyboard_row_subtitle(vidpid: str, *, present: bool) -> str:
    """vid:pid under the keyboard row, marked when the scan did not see it.

    The window does not watch hotplug. `present` is the scan from when the
    combo was built, so a saved receiver can stay selectable while unplugged.
    @tags: #action/normalize #format/string #model/hid #subject/form #type/helper
    """
    vidpid = (vidpid or "").strip()
    if not vidpid:
        return "No keyboard selected"
    if present:
        return vidpid
    return f"{vidpid} · Unplugged"


def ids_sharing_title(titles: dict[str, str]) -> set[str]:
    """Ids whose card title is used by another id on the same keyboard.

    The HID id stays off the card until two titles would look identical.
    @tags: #action/normalize #model/binding #model/favorite #subject/form #type/helper
    """
    counts: dict[str, int] = {}
    for title in titles.values():
        counts[title] = counts.get(title, 0) + 1
    return {key_id for key_id, title in titles.items() if counts[title] > 1}


def key_card_text(
    key_id: str,
    binding: object,
    fallback: str,
    summary: str = "",
    *,
    show_id: bool = False,
) -> tuple[str, str, str]:
    """Title, action line, and optional id line for one key card.

    The action line is the caption. The id is a third line only when
    `show_id` is set (two cards share a title). Without a custom title the
    fallback is the name: favorite labels stay, and a learned id with no
    title stays the raw id.
    @tags: #action/normalize #format/string #model/binding #model/favorite #subject/form #type/helper
    """
    raw = binding.get("label") if isinstance(binding, dict) else None
    label = binding_label(raw)
    title = label or fallback
    caption = (summary or "").strip()
    id_line = key_id if show_id else ""
    return title, caption, id_line


def binding_for_kind(kind: str, value: str = "") -> dict:
    """Build a binding with only one of `key` or `exec` set.

    Dual-fire is a mapper feature, not a GUI mode. A later checkbox can opt
    back into both fields; until then writes stay exclusive. The display
    title is not part of this pair; callers attach `label` afterward.
    @tags: #action/normalize #model/binding #model/config #subject/form #type/helper
    """
    if kind in ("command", "app"):
        return {"key": "", "exec": strip_field_codes(value)}
    if kind == "key":
        return {"key": (value or "").strip().upper(), "exec": ""}
    return {"key": "", "exec": ""}


def shortcut_key_choices(current: str = "") -> list[str]:
    """F13–F24 for the shortcut combo, plus a non-standard current value.

    Leaving an unknown name in the list keeps it selectable until the user
    picks a listed F-key. Apply then stores that choice instead of dropping it.
    @tags: #model/shortcut #model/config #subject/form #type/helper
    """
    keys = list(SYSTEM_SHORTCUT_KEYS)
    current = (current or "").strip()
    if current and current.upper() not in {item.upper() for item in keys}:
        keys.append(current)
    return keys


def save_config(path: Path, config: dict, *, replace_bindings: bool = False) -> dict:
    """Atomically merge and write config so a crash cannot truncate the file.

    Bindings are nested by vid:pid. Default merge updates keys inside each
    device bucket so `learn` cannot wipe the other keyboard. `replace_bindings`
    writes the nested tree as given so the GUI can delete a key. Flat payloads
    are nested before write. Other top-level keys on disk are kept unless
    `config` sets them. Returns the merged document that was written.
    @tags: #action/save #action/merge #model/binding #model/config #side-effect/file #side-effect/mutation
    """
    if path.exists():
        existing = load_config(path)
    else:
        existing = _default_document()
    merged = dict(existing)
    for key, value in config.items():
        if key == "bindings":
            continue
        merged[key] = value
    incoming_cfg = dict(merged)
    incoming_cfg["bindings"] = config.get("bindings") or {}
    incoming = nested_bindings(incoming_cfg)
    if replace_bindings:
        merged["bindings"] = incoming
    else:
        result = _copy_nested_maps(existing.get("bindings") or {})
        for vidpid, bucket in incoming.items():
            dest = result.setdefault(vidpid, {})
            dest.update({kid: dict(bind) for kid, bind in bucket.items()})
        merged["bindings"] = result
    path.parent.mkdir(parents=True, exist_ok=True)
    _chown_user(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2) + "\n")
    tmp.replace(path)
    _chown_user(path)
    return merged
