# Operator guide

Install the mapper, assign actions (GUI or CLI), wire systemd, and debug
hidraw. End users should use the README and the GTK app instead.

The mapper opens whatever `vid:pid` list you save. The Microsoft Wireless
Keyboard 2000 (`045e:0745`) is the default when that list is absent.

Repo commands assume you are in the project directory:

```bash
cd ~/ms-keyboard-linux
```

## 1. One-time install

Needs sudo once so this user can read hidraw nodes (`plugdev`). The udev
rule matches every hidraw node. The mapper still opens only devices listed
in config, and keyboard and mouse interfaces are skipped in software.
`sudo` writes the systemd user unit into **your** home (`$SUDO_USER`), not `/root`.

```bash
sudo python3 mskb.py install
python3 mskb.py status
```

Unplug and plug the receiver back in once if a node stays permission denied.

`python3 mskb.py status` lists every hidraw and marks keyboard/mouse
interfaces separately from extra keys. It also prints the config path and
whether uinput is writable.

## 2. See what a physical key is called

`probe` and `learn` follow that same `devices` list.

```bash
python3 mskb.py probe
```

Press each extra key once. Do **not** type in that terminal (the probe is
listening to the keyboard). Stop with Ctrl+C.

On the Wireless Keyboard 2000, these names still work. They win over a
generic id for the same press (`favorites_star` wins over
`045e:0745:000c:0182`):

| Physical key | Config id | Default `key` |
| --- | --- | --- |
| My Favorites 1–5 | `favorites_1` … `favorites_5` | F14–F18 |
| My Favorites (star) | `favorites_star` | F13 |

A learned id looks like `{vid}:{pid}:{page}:{usage}`, plus `:{value}` when
the field is wider than 1 bit:

- `045e:0745:000c:0182`
- `045e:0745:ff05:0001:4`

Unknown extra key:

```bash
python3 mskb.py learn some_name
```

`learn some_name` still writes the config, under the `vid:pid` of the
hidraw that produced the press.

Report `0x21` on the 2000 is status, not a key. `probe -v` prints that
report; it is not a binding id.

Volume and other keys the kernel already emits are still configured in the
desktop settings, not here.

## 3. Assign shortcuts

`devices` in `~/.config/mskb/config.json` is the list of `vid:pid` the
mapper opens. Absent means `045e:0745`. `bindings` is nested by that same
id: a press on one hidraw only matches keys stored under its `vid:pid`.

The default way to assign keys is the GTK window:

```bash
python3 mskb.py gui
```

Needs GTK4 and libadwaita 1.5 (Ubuntu 24.04 / Zorin 18):

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```

Choose the keyboard, pick a listed key or add one by pressing it, choose
Open app, System shortcut, Command, or Nothing, then **Apply**. Cards are
only the keys for the selected keyboard, and each card shows the action
(open an app, send F13–F24, run a command, or do nothing). Application
names in the combo are the desktop display names, not a list this app
translates. Add key shows a bar asking for the press; Cancel stops it.
Removing a key can be undone until you remove another one. Closing with
unsaved edits asks before discarding them. Apply in the GUI replaces the
nested bindings maps (removed keys stay removed on that device; other
keyboards keep theirs) and restarts
`mskb.service` (`systemctl --user restart`, or `enable --now` if the unit
was inactive) so the binding works immediately. **Restart mapper** in the
window menu reloads without saving. Apply also removes any GNOME
Startup Applications entry that launches `mskb.py run` (those conflict
with the systemd unit and dual-fire keys).

The first launch also installs a menu entry
(`~/.local/share/applications/mskb.desktop`). `sudo python3 mskb.py install`
writes the same desktop file into the session user’s home.

JSON is still the source of truth. Edit it by hand if you want; restart
the mapper afterwards (section 4).

```json
{
  "devices": ["045e:0745", "046d:c52b"],
  "bindings": {
    "045e:0745": {
      "favorites_1": { "key": "", "exec": "/usr/bin/flatpak run md.obsidian.Obsidian" },
      "favorites_2": { "key": "F15", "exec": "" },
      "favorites_3": { "key": "F16", "exec": "" },
      "favorites_4": { "key": "F17", "exec": "" },
      "favorites_5": { "key": "F18", "exec": "" },
      "favorites_star": { "key": "F13", "exec": "" }
    },
    "046d:c52b": {
      "046d:c52b:000c:0182": { "key": "F13", "exec": "" }
    }
  }
}
```

| Field | Meaning |
| --- | --- |
| `devices` | `vid:pid` values the mapper, `probe`, and `learn` open. Absent means `045e:0745`. |
| `bindings` | Map of `vid:pid` to that keyboard's key ids. A flat file from before this nest is migrated on load. Empty `bindings` stay empty: factory Favorites are not re-inserted on load. |
| `key` | Virtual key via uinput. Record it in Zorin Settings → Keyboard → Shortcuts. Empty string = do not emit. |
| `exec` | Shell command on press. Empty string = do not run a command. |
| `label` | Optional title shown on the key card. Empty or absent uses the favorite name or the raw id. The mapper ignores it; the object key stays the HID id. |

The GUI writes **one** of `key` or `exec` on a binding, never both. It may
also write `label`. The mapper still fires both if a hand-edited file sets
them (Favorite 1 with Obsidian **and** F14).

`exec` is a normal command, not a `.desktop` Exec line. Do not copy
`--file-forwarding @@u %U @@` from Flatpak desktop files; there is no URI
on a hotkey. The GUI strips those tokens. Flatpak apps:

```text
/usr/bin/flatpak run md.obsidian.Obsidian
```

GNOME/Zorin path for the emitted F-keys: Settings → Keyboard → Keyboard
Shortcuts → Custom Shortcuts. The mapper must be running while you record
the key.

## 4. Run it

Foreground (debug, dies when the terminal closes):

```bash
python3 mskb.py run
```

**Supported login autostart** is the systemd user unit only. Run **once**:

```bash
systemctl --user enable --now mskb.service
```

`sudo python3 mskb.py install` writes the unit, removes conflicting GNOME
autostart entries that launch `mskb.py run`, and tries `enable --now` when
the user bus is reachable. The unit description is `HID extra-key mapper`.

- `enable` — start on future graphical logins (`graphical-session.target`)
- `--now` — start immediately

This is a **user** unit, not a boot service. It starts when you reach the
desktop, which is what GUI `exec` lines need.

Check:

```bash
systemctl --user is-enabled mskb.service
systemctl --user status mskb.service
```

You want `enabled` and `active (running)`. Reload after editing the config
(or use **Apply** / **Restart Mapper** in the GUI):

```bash
systemctl --user restart mskb.service
```

### Do not use GNOME Startup Applications for the mapper

Do **not** add `mskb.py run` (or `systemctl enable`) under GNOME “Startup
Applications” / “Aplicativos iniciais”. That starts a second mapper beside
the unit; keys fire twice. Install and the GUI delete those conflicting
`.desktop` files automatically when they launch `mskb.py run`.

## 5. CLI reference

| Command | Role |
| --- | --- |
| `mskb.py status` | Every hidraw, marked `[keyboard/mouse]` or `[extra keys]`, plus config path |
| `mskb.py probe` | Print `PRESS` / `RELEASE` for extra keys on the `devices` list |
| `mskb.py learn NAME` | Capture the next extra key and write it into the config |
| `mskb.py gui` | Assign extra keys (GTK4) |
| `mskb.py run` | Mapper (uinput + `exec`) on the `devices` list |
| `sudo mskb.py install` | udev + hwdb + user unit + desktop entry |
| `sudo mskb.py bind-driver --yes` | Experimental, `045e:0745` only: bind `hid-microsoft` (glitches mouse; do not combine with `run`) |

`bind-driver` and `udev/61-mskb.hwdb` stay specific to `045e:0745`. The
broad hidraw rule does not change that driver binding.
