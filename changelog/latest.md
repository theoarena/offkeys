## [latest]

### Added
- GTK4/libadwaita window (`mskb.py gui`) to assign My Favorites, plus a Restart Mapper header action (`92222a58dc8bdad687461a187cd8c740e27bd7a8`)
- systemd-only mapper restart: Apply/`enable --now` reload `mskb.service` and remove GNOME autostart entries that launch `mskb.py run` (`92222a58dc8bdad687461a187cd8c740e27bd7a8`)
- Mapper learns an extra key from any selected HID device as usage id `vid:pid:page:usage` (wider fields include the value); Wireless Keyboard 2000 favorite ids still match first (`a737e6082c2b22c3f9aafa9067b8b7f014cd4255`)
- GTK window lists saved keys, adds one by pressing it, and removes one (`a737e6082c2b22c3f9aafa9067b8b7f014cd4255`)
- Optional binding `label` is the title on a key card; the mapper still matches the HID id (`66ec7e29c8fe23231e25ad9e6ac8a15c28011ec3`)

### Changed
- Bindings are nested by `vid:pid` so each keyboard has its own map; the window shows only the selected device; the mapper matches the hidraw that produced the report (`4ba865dca2bdeb1589088f075690957ffcb690cb`)
- Key chooser is a wrapping grid of cards (at most three per row); the HID id is the second line only when a custom title is set (`66ec7e29c8fe23231e25ad9e6ac8a15c28011ec3`)
- Split `mskb.py` into domain sibling modules (`mskb_paths`, `mskb_bindings`, `mskb_hid`, `mskb_mapper`, `mskb_lifecycle`, `mskb_install`) while keeping `mskb.py` as the CLI and `import mskb` facade (`ae244db4468cee59056ce9451c341f8011dc0c44`)
- Renamed `docs/usage.md` to `docs/operator.md` and reframed it as the operator guide (CLI, systemd, hidraw); README stays the end-user app guide
- Config `devices` (default `045e:0745`) chooses which receivers are opened; `save_config(..., replace_bindings=True)` lets the window remove a key (`a737e6082c2b22c3f9aafa9067b8b7f014cd4255`)
- udev grants hidraw read for every device; `udev/61-mskb.hwdb` and `bind-driver` stay on `045e:0745` (`a737e6082c2b22c3f9aafa9067b8b7f014cd4255`)
