---
summary: "Bindings nested by vid:pid; load does not merge factory Favorites; decode_report only on 045e:0745; mapper source is hidraw path plus report id."
created: 2026-09-28
updated: 2026-09-28
category: architecture
tags: [hid, bindings, mapper, tested-devices]
related: [mskb_hid.py, mskb_bindings.py, mskb_mapper.py, mskb_gui.py, udev/99-mskb.rules, README.md]
status: resolved
---

# Generic HID key identity

Device id is lowercase `vid:pid` with four hex digits (`045e:0745`). `devices_from_config` lowercases entries and treats a missing, empty, or non-list `devices` as `["045e:0745"]`.

`bindings` is nested: `{vid:pid: {key_id: {key, exec, optional label}}}`. `load_config` migrates a flat map into those buckets and does **not** overlay `DEFAULT_CONFIG` favorites onto an existing file. `ensure_device_map` copies the six Wireless Keyboard 2000 favorites only when the `045e:0745` slot is absent. An explicit `{}` bucket stays empty. `bindings_for_device` returns `{}` for a missing product and does not seed. The GUI snapshots maps from disk before that seed, so a missing 2000 slot marks Apply dirty; seeding before the snapshot made the cards look saved while the mapper still saw `{}`.

A learned key is `{vid}:{pid}:{page:04x}:{usage:04x}`, plus `:{value:x}` when the input field is wider than one bit. On `045e:0745`, `report_ids` puts Wireless Keyboard 2000 ids from `decode_report` ahead of generic usage ids, so `favorites_1` … `favorites_star` still win. Other `vid:pid` values get generic ids only.

`track_report` keeps the first frame as the idle baseline, so a key already held at startup is missed until release and press. An empty press list updates that baseline and absorbs newly stuck bits. Release is scoped to `(hidraw path, report id)`. Report `0x21` is status on the 2000 and is never bound or released.

`skip_interface` is true only when every application collection is a boot keyboard (`0x01/0x06`) or mouse (`0x01/0x02`). The mapper opens only selected devices that fail that test, then looks up that hidraw's `vid:pid` slice.

`save_config(..., replace_bindings=True)` replaces the nested bindings tree so the GUI can delete a key on one device. Default merge updates keys inside each device bucket (`learn`). Apply in the window uses the replace flag and writes every device map.

`udev/99-mskb.rules` grants seat read on every hidraw. `udev/61-mskb.hwdb` and `bind-driver` stay `045e:0745` only.

Ceiling: one matched binding per report, no chords. Identity is product `vid:pid`, not USB serial. A descriptor the parser cannot read falls back to `decode_report` only on the 2000.

README **Tested devices** is the public support list (today only Wireless Keyboard 2000 / `045e:0745`). Other extra-key HID devices can still be learned; they stay off that table until actually tested.
