---
summary: "User-facing product name is OffKeys; technical identity stays mskb (CLI, config, unit filename, application_id). Do not rename those internals with the public brand."
created: 2026-09-28
category: architecture
domain: naming
tags: [offkeys, mskb, desktop, systemd, readme]
related: [mskb_gui.py, mskb_install.py, systemd/mskb.service, README.md, docs/operator.md, mskb-public-facade.md]
---

# OffKeys public name vs mskb identity

The product people see is **OffKeys**. GitHub About is: "Linux app to bind extra keyboard keys to shortcuts, apps, and commands."

User-facing surfaces that must say OffKeys:

| Surface | Value |
| --- | --- |
| Window title / header (`WINDOW_TITLE` in `mskb_gui.py`) | `OffKeys` |
| App-grid `.desktop` `Name=` | `OffKeys` |
| `.desktop` `Comment=` | `Bind extra keyboard keys to shortcuts, apps, and commands.` |
| systemd unit `Description=` | `OffKeys extra-key mapper` |
| `mskb.py --help` description | `OffKeys: bind extra keyboard keys to shortcuts, apps, and commands` |
| README title and copy | OffKeys |
| Operator guide when referring to the GTK window | OffKeys window |

Technical identity stays `mskb`. Do **not** rename these to OffKeys:

| Surface | Stays |
| --- | --- |
| CLI and import facade | `mskb.py` / `import mskb` |
| Config tree | `~/.config/mskb/` (`config.json`) |
| systemd unit file | `mskb.service` (`systemctl --user … mskb.service`) |
| GTK `application_id` | `dev.mskb.Favorites` |
| udev / hwdb prefixes | `99-mskb.rules`, `61-mskb.hwdb` |
| Domain modules | `mskb_*.py` |

Rationale: `mskb.py` cannot sit beside a package directory named `mskb/`, existing installs already use `~/.config/mskb/` and `mskb.service`, and the GTK id is a stable D-Bus/application identity. The public rename is display-only.

Tests that look for leftover autostart `.desktop` files must not key off `Name=OffKeys`. Autostart cleanup matches `Exec=` launching `mskb.py run`, not the display name.
