# mskb_bindings.py
#
# config.json load/save and GUI exclusive-mode helpers.
# The mapper still dual-fires when both key and exec are set; this
# module only projects that contract for the favorites window. An optional
# `label` is a display title and is not part of that pair.
#
# Used by: mskb_gui.py (via mskb), mskb_mapper.py, mskb_install.py
# See also: mskb_paths.py

from __future__ import annotations

import json
from pathlib import Path

from mskb_paths import REPO_ROOT, _chown_user, config_path

DEFAULT_CONFIG = {
    "_comment": (
        "Match keys from `mskb.py probe`. `key` is emitted via uinput "
        "(bind it in Zorin Settings → Keyboard). `exec` runs on press."
    ),
    "devices": ["045e:0745"],
    "bindings": {
        "favorites_1": {"key": "F14", "exec": ""},
        "favorites_2": {"key": "F15", "exec": ""},
        "favorites_3": {"key": "F16", "exec": ""},
        "favorites_4": {"key": "F17", "exec": ""},
        "favorites_5": {"key": "F18", "exec": ""},
        "favorites_star": {"key": "F13", "exec": ""},
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


def load_config(path: Path) -> dict:
    if not path.exists():
        return DEFAULT_CONFIG
    with path.open() as fh:
        data = json.load(fh)
    bindings = dict(DEFAULT_CONFIG["bindings"])
    bindings.update(data.get("bindings", {}))
    data["bindings"] = bindings
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


def key_card_text(key_id: str, binding: object, fallback: str) -> tuple[str, str]:
    """Title and caption for one key card.

    A custom title keeps the HID id underneath so two cards can share a name.
    Without a title the fallback is the only line: favorite names stay single,
    and a learned id with no title stays the raw id.
    @tags: #action/normalize #model/binding #subject/form #type/helper
    """
    raw = binding.get("label") if isinstance(binding, dict) else None
    label = binding_label(raw)
    if label:
        return label, key_id
    return fallback, ""


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
    picks a listed F-key. Apply then stores that choice instead.
    @tags: #model/shortcut #model/config #subject/form #type/helper
    """
    keys = list(SYSTEM_SHORTCUT_KEYS)
    current = (current or "").strip()
    if current and current.upper() not in {item.upper() for item in keys}:
        keys.append(current)
    return keys


def save_config(path: Path, config: dict, *, replace_bindings: bool = False) -> dict:
    """Atomically merge and write config so a crash cannot truncate the file.

    Bindings are merged by default: a favorite-only GUI save keeps ids that
    `learn` added. The favorites window can also delete a key; without
    `replace_bindings` that merge kept the removed id forever. Set the flag
    so the written map is exactly the payload bindings. Other top-level keys
    on disk are kept unless `config` sets them. A new file still starts from
    DEFAULT_CONFIG, but the flag drops seeded favorites the payload left out.
    Returns the merged document that was written.
    @tags: #action/save #action/merge #model/config #side-effect/file #side-effect/mutation
    """
    if path.exists():
        existing = load_config(path)
    else:
        existing = {
            "_comment": DEFAULT_CONFIG["_comment"],
            "devices": list(DEFAULT_CONFIG["devices"]),
            "bindings": dict(DEFAULT_CONFIG["bindings"]),
        }
    merged = dict(existing)
    for key, value in config.items():
        if key == "bindings":
            continue
        merged[key] = value
    if replace_bindings:
        merged["bindings"] = dict(config.get("bindings") or {})
    else:
        bindings = dict(existing.get("bindings") or {})
        bindings.update(config.get("bindings") or {})
        merged["bindings"] = bindings
    path.parent.mkdir(parents=True, exist_ok=True)
    _chown_user(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2) + "\n")
    tmp.replace(path)
    _chown_user(path)
    return merged
