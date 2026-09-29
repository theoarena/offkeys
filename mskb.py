#!/usr/bin/env python3
# mskb.py
#
# Public CLI entry for OffKeys. Domain code lives in sibling modules;
# this file is the CLI, the `import mskb` facade, and the lazy GUI launch.
# Do not replace it with a package named mskb — systemd and the .desktop
# Exec point here. The window title is OffKeys; this filename stays mskb.
#
# Used by: systemd/mskb.service, mskb_gui.py
# See also: mskb_bindings.py, mskb_hid.py, mskb_mapper.py, mskb_lifecycle.py,
#           mskb_install.py, mskb_paths.py

from __future__ import annotations

import argparse
import os
import select
import sys

from mskb_bindings import (  # noqa: F401
    DEFAULT_CONFIG,
    FAVORITE_IDS,
    KEYBOARD_2000,
    SYSTEM_SHORTCUT_KEYS,
    action_summary,
    as_binding,
    binding_for_kind,
    binding_label,
    bindings_for_device,
    ids_sharing_title,
    key_card_text,
    keyboard_display_name,
    keyboard_row_subtitle,
    devices_from_config,
    ensure_config,
    ensure_device_map,
    is_binding_entry,
    kind_for_binding,
    load_config,
    nested_bindings,
    put_learned_binding,
    save_config,
    shortcut_key_choices,
    strip_field_codes,
)
from mskb_hid import (  # noqa: F401
    UINPUT_PATH,
    HidDescriptor,
    ParsedReport,
    decode_report,
    evdev_devices,
    format_report,
    hidraw_devices,
    open_hidraw,
    primary_id,
    report_ids,
    skip_interface,
    track_report,
)
from mskb_install import (  # noqa: F401
    cmd_bind_driver,
    cmd_install,
    ensure_desktop_file,
)
from mskb_lifecycle import (  # noqa: F401
    _cmdline_is_mapper_run,
    _stop_mapper_pids,
    _systemctl_user,
    desktop_launches_mapper_run,
    mapper_run_pids,
    mapper_status_message,
    remove_mskb_autostart,
    restart_mapper,
)
from mskb_mapper import cmd_run
from mskb_paths import (  # noqa: F401
    REPO_ROOT,
    _chown_user,
    config_dir,
    config_path,
    sudo_invoker,
    user_home,
)


def cmd_status(_: argparse.Namespace) -> int:
    """List every hidraw node and whether learn would open it.

    The role follows skip_interface so a boot keyboard is not labeled as
    extra keys. Any vid:pid can appear; this command does not treat one
    receiver as the only one.
    @tags: #subject/cli #type/command #model/hid
    """
    path = config_path()
    print("Kernel driver: hid-generic (hid-microsoft is NOT bound — Favorites are dropped)")
    print(f"Config: {path} {'(exists)' if path.exists() else '(missing)'}")
    print("hidraw:")
    devices = hidraw_devices()
    if not devices:
        print("  (none found — is a receiver plugged in?)")
    for dev in devices:
        readable = os.access(dev.path, os.R_OK)
        role = "[keyboard/mouse]" if skip_interface(dev.descriptor) else "[extra keys]"
        state = "readable" if readable else "permission denied"
        print(f"  {dev.vid}:{dev.pid}  {dev.name}  {dev.path}  {dev.iface}  {state}  {role}")
    print("evdev:")
    for event_path in evdev_devices():
        print(f"  {event_path}  {'readable' if os.access(event_path, os.R_OK) else 'permission denied'}")
    print(f"uinput: {UINPUT_PATH}  {'writable' if os.access(UINPUT_PATH, os.W_OK) else 'permission denied'}")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Print press/release from track_report ids, one held key per source.

    Report 0x21 is status. It must not count as the release of a key another
    report id on the same hidraw is still holding. The first frame of each
    source is an idle snapshot, so a key already down at start is silent
    until it is released and pressed.
    @tags: #model/config #model/hid #subject/cli #type/command
    """
    config = load_config(ensure_config())
    opened = open_hidraw(devices_from_config(config))
    print("Listening on extra-key interfaces. Press Favorites 1–5, star, Mail, Calc, Zoom, media.")
    print("Do not type in this terminal (Ctrl+C is enough to stop).")
    baselines: dict[tuple[str, int], bytes] = {}
    held: dict[tuple[str, int], str] = {}
    fds = {fd: dev for fd, dev in opened}
    try:
        while True:
            ready, _, _ = select.select(list(fds), [], [], 1.0)
            for fd in ready:
                try:
                    data = os.read(fd, 64)
                except BlockingIOError:
                    continue
                dev = fds[fd]
                source = (dev.path, data[0] if data else 0)
                if source[1] == 0x21:
                    if args.verbose:
                        parsed = decode_report(data)
                        if parsed is not None:
                            print(f"{dev.iface} status {format_report(parsed)}")
                    continue
                descriptor = HidDescriptor(device=f"{dev.vid}:{dev.pid}", raw=dev.descriptor)
                baseline, ids = track_report(baselines.get(source), data, descriptor)
                baselines[source] = baseline
                parsed = ParsedReport(report_id=source[1], raw=data, ids=ids)
                current = primary_id(parsed)
                previous = held.get(source)
                if current == previous:
                    if args.verbose and data and not ids and decode_report(data) is None:
                        print(f"{dev.iface} raw {data.hex(' ')}")
                    continue
                if previous and not current:
                    print(f"RELEASE {previous}")
                    held.pop(source, None)
                    continue
                if current:
                    if previous:
                        print(f"RELEASE {previous}")
                    print(f"PRESS   {current}  {data.hex(' ')}")
                    held[source] = current
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        for fd, _ in opened:
            os.close(fd)
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    """Capture the next extra key and store it under that hidraw's vid:pid bucket.
    @tags: #action/save #model/binding #model/config #model/hid #side-effect/file #side-effect/mutation #subject/cli #type/command
    """
    config = load_config(ensure_config())
    opened = open_hidraw(devices_from_config(config))
    target = args.name
    print(f"Press the physical key to bind as {target!r}. Ctrl+C to cancel.")
    fds = {fd: dev for fd, dev in opened}
    baselines: dict[tuple[str, int], bytes] = {}
    captured = None
    captured_dev = None
    try:
        while captured is None:
            ready, _, _ = select.select(list(fds), [], [], 1.0)
            for fd in ready:
                try:
                    data = os.read(fd, 64)
                except BlockingIOError:
                    continue
                dev = fds[fd]
                source = (dev.path, data[0] if data else 0)
                # Same status report probe ignores. It never names a key to save.
                if source[1] == 0x21:
                    continue
                descriptor = HidDescriptor(device=f"{dev.vid}:{dev.pid}", raw=dev.descriptor)
                baseline, ids = track_report(baselines.get(source), data, descriptor)
                baselines[source] = baseline
                if not ids:
                    continue
                captured = ParsedReport(report_id=source[1], raw=data, ids=ids)
                captured_dev = dev
                break
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 1
    finally:
        for fd, _ in opened:
            os.close(fd)

    assert captured is not None
    assert captured_dev is not None
    print(format_report(captured))
    primary = primary_id(captured) or captured.ids[0]
    print(f"Suggested config id: {primary}")
    if target:
        ensure_config()
        path = config_path()
        config = load_config(path)
        vidpid = f"{captured_dev.vid}:{captured_dev.pid}"
        bucket = bindings_for_device(config.get("bindings") or {}, vidpid)
        existing = bucket.get(target, {"key": "", "exec": ""})
        put_learned_binding(config, vidpid, target, primary, existing)
        save_config(path, config)
        print(f"Updated {path} with {target} / {primary}")
    return 0


def cmd_gui(_: argparse.Namespace) -> int:
    """Open the favorites window. GI is imported here so probe/run stay GI-free.
    @tags: #action/launch #scope/gui #subject/cli #type/command
    """
    ensure_desktop_file()
    try:
        from mskb_gui import run_gui
    except (ImportError, ValueError, ModuleNotFoundError):
        print(
            "GTK4 / libadwaita is not available. Install:\n"
            "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1",
            file=sys.stderr,
        )
        return 1
    return run_gui()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OffKeys: bind extra keyboard keys to shortcuts, apps, and commands"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Show receiver, hidraw permissions, driver").set_defaults(
        func=cmd_status
    )
    probe = sub.add_parser("probe", help="Print extra-key HID reports (press keys)")
    probe.add_argument(
        "-v", "--verbose", action="store_true", help="Show status reports and raw leftover HID"
    )
    probe.set_defaults(func=cmd_probe)
    learn = sub.add_parser("learn", help="Capture one physical key into the config")
    learn.add_argument("name", help="Binding id, e.g. favorites_star")
    learn.set_defaults(func=cmd_learn)
    sub.add_parser("run", help="Emit key events / run commands from config").set_defaults(
        func=cmd_run
    )
    sub.add_parser("install", help="Install udev + hwdb (root)").set_defaults(func=cmd_install)
    sub.add_parser("gui", help="Assign My Favorites in a GTK window").set_defaults(func=cmd_gui)
    bind = sub.add_parser("bind-driver", help="Experimental hid-microsoft quirk bind")
    bind.add_argument("--yes", action="store_true")
    bind.set_defaults(func=cmd_bind_driver)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
