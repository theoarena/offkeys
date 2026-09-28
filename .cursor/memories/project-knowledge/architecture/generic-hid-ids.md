---
summary: "Generic HID key ids are vid:pid:page:usage, 2000 favorites ids stay first, missing devices defaults to 045e:0745."
created: 2026-09-28
updated: 2026-09-28
category: architecture
tags: [hid, bindings, mapper]
related: [mskb_hid.py, mskb_bindings.py, mskb_mapper.py, mskb_gui.py, udev/99-mskb.rules]
status: resolved
---

# Generic HID key identity

Device id is lowercase `vid:pid` with four hex digits (`045e:0745`). `devices_from_config` lowercases entries and treats a missing, empty, or non-list `devices` as `["045e:0745"]`.

A learned key is `{vid}:{pid}:{page:04x}:{usage:04x}`, plus `:{value:x}` when the input field is wider than one bit. `report_ids` puts Wireless Keyboard 2000 ids from `decode_report` ahead of generic usage ids, so `favorites_1` … `favorites_star` still win.

`track_report` keeps the first frame as the idle baseline, so a key already held at startup is missed until release and press. An empty press list updates that baseline and absorbs newly stuck bits. Release is scoped to `(iface, report id)`. Report `0x21` is status on the 2000 and is never bound or released.

`skip_interface` is true only when every application collection is a boot keyboard (`0x01/0x06`) or mouse (`0x01/0x02`). The mapper opens only selected devices that fail that test.

`save_config(..., replace_bindings=True)` replaces the bindings map so the GUI can delete a key. The default merge keeps learned ids. Apply in the window uses the replace flag.

`udev/99-mskb.rules` grants seat read on every hidraw. `udev/61-mskb.hwdb` and `bind-driver` stay `045e:0745` only.

Ceiling: one matched binding per report, no chords. A descriptor the parser cannot read falls back to the 2000 decoder only.
