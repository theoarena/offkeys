# mskb_install.py
#
# Root/user install: udev + hwdb, systemd user unit, app-grid .desktop,
# and the experimental hid-microsoft bind-driver path.
#
# Used by: mskb.py (cmd_install, cmd_bind_driver, ensure_desktop_file for gui)
# See also: mskb_lifecycle.py, mskb_bindings.py, mskb_paths.py

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from mskb_bindings import ensure_config
from mskb_lifecycle import _systemctl_user, remove_mskb_autostart
from mskb_paths import REPO_ROOT, _chown_user, sudo_invoker


def _install_commands() -> list[list[str]]:
    udev_src = REPO_ROOT / "udev" / "99-mskb.rules"
    hwdb_src = REPO_ROOT / "udev" / "61-mskb.hwdb"
    return [
        ["install", "-m", "644", str(udev_src), "/etc/udev/rules.d/99-mskb.rules"],
        ["install", "-m", "644", str(hwdb_src), "/etc/udev/hwdb.d/61-mskb.hwdb"],
        ["systemd-hwdb", "update"],
        ["udevadm", "control", "--reload"],
        ["udevadm", "trigger", "-s", "hidraw"],
        ["udevadm", "trigger", "-s", "input"],
    ]


def _install_user_service() -> None:
    """Write and enable the user systemd unit (OffKeys unit description).
    @tags: #action/install #model/desktop #scope/user #side-effect/file #side-effect/mutation #side-effect/process #subject/cli #type/helper
    """
    inv = sudo_invoker()
    home = Path(inv.pw_dir) if inv else Path.home()
    unit_dir = home / ".config/systemd/user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    _chown_user(home / ".config")
    _chown_user(home / ".config/systemd")
    _chown_user(unit_dir)
    unit = unit_dir / "mskb.service"
    python = sys.executable
    if python.startswith("/root/") or (inv and python == "/usr/bin/python3"):
        python = "/usr/bin/python3"
    unit.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=OffKeys extra-key mapper",
                "After=graphical-session.target",
                "PartOf=graphical-session.target",
                "",
                "[Service]",
                f"ExecStart={python} {REPO_ROOT / 'mskb.py'} run",
                "Restart=on-failure",
                "RestartSec=2",
                "",
                "[Install]",
                "WantedBy=graphical-session.target",
                "",
            ]
        )
    )
    _chown_user(unit)
    rc = _systemctl_user(["daemon-reload"])
    print(f"Wrote {unit}")
    if rc != 0:
        print("systemctl --user daemon-reload failed (ok if this was sudo). Run as your user:")
        print("  systemctl --user daemon-reload")
    removed = remove_mskb_autostart()
    for path in removed:
        print(f"Removed conflicting autostart: {path}")
    enable_rc = _systemctl_user(["enable", "--now", "mskb.service"])
    if enable_rc != 0:
        print(
            "Could not enable mskb.service (ok if this was sudo without a user bus). "
            "Run as your user:"
        )
        print("  systemctl --user enable --now mskb.service")
    else:
        print("Enabled and started mskb.service")


def desktop_path() -> Path:
    """Path to the user `.desktop` launcher for the OffKeys window.
    @tags: #format/path #model/desktop #scope/user #subject/desktop #type/helper
    """
    inv = sudo_invoker()
    home = Path(inv.pw_dir) if inv else Path.home()
    return home / ".local/share/applications/mskb.desktop"


def _desktop_python() -> str:
    """Python interpreter path for `.desktop` Exec when install runs under sudo.
    @tags: #format/path #scope/user #type/helper
    """
    inv = sudo_invoker()
    python = sys.executable
    if python.startswith("/root/") or (inv and python == "/usr/bin/python3"):
        return "/usr/bin/python3"
    return python


def ensure_desktop_file() -> Path:
    """Install a user .desktop so the GUI shows up in the app grid.

    Same $SUDO_USER home as the systemd unit: sudo install must not write
    into /root/.local.
    @tags: #action/save #format/string #model/desktop #scope/user #side-effect/file #side-effect/mutation #subject/desktop #type/helper
    """
    path = desktop_path()
    share_dir = path.parent
    local_dir = share_dir.parent
    home_local = local_dir.parent
    for directory in (home_local, local_dir, share_dir):
        directory.mkdir(parents=True, exist_ok=True)
        _chown_user(directory)
    python = _desktop_python()
    path.write_text(
        "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                "Name=OffKeys",
                "Comment=Bind extra keyboard keys to shortcuts, apps, and commands.",
                f"Exec={python} {REPO_ROOT / 'mskb.py'} gui",
                "Icon=input-keyboard",
                "Terminal=false",
                "Categories=Settings;HardwareSettings;",
                "StartupNotify=true",
                "",
            ]
        )
    )
    _chown_user(path)
    return path


def cmd_install(_: argparse.Namespace) -> int:
    """Install udev/hwdb so the session user can read hidraw and map unknown consumer keys.
    @tags: #action/install #model/desktop #side-effect/file #side-effect/mutation #subject/cli #type/command
    """
    _install_user_service()
    desktop = ensure_desktop_file()
    print(f"Wrote {desktop}")
    ensure_config()
    if os.geteuid() != 0:
        print("Root is required to install udev rules. Re-run:")
        print(f"  sudo python3 {REPO_ROOT / 'mskb.py'} install")
        print("Then unplug and replug the Microsoft dongle (or reboot).")
        return 1
    for cmd in _install_commands():
        print("+", " ".join(cmd))
        subprocess.check_call(cmd)
    print("Installed udev + hwdb.")
    print(f"  python3 {REPO_ROOT / 'mskb.py'} gui")
    print(f"  python3 {REPO_ROOT / 'mskb.py'} probe")
    print("  systemctl --user enable --now mskb.service")
    return 0


def cmd_bind_driver(args: argparse.Namespace) -> int:
    """Experimental: bind hid-microsoft with MS_ERGONOMY (same as Natural 7000)."""
    if not args.yes:
        print(
            "This unbinds hid-generic from ALL 045e:0745 interfaces (keyboard AND mouse)\n"
            "and binds hid-microsoft with the MS_ERGONOMY quirk so My Favorites become F14–F18.\n"
            "Input will glitch for a second. Pass --yes to actually do it (needs root)."
        )
        return 1
    if os.geteuid() != 0:
        print("Need root: sudo python3 mskb.py bind-driver --yes")
        return 1
    subprocess.check_call(["modprobe", "hid-microsoft"])
    new_id = Path("/sys/bus/hid/drivers/microsoft/new_id")
    # bus=USB(3) vendor product version driver_data=MS_ERGONOMY(2)
    new_id.write_text("3 0x045e 0x0745 0 2")
    hid_generic = Path("/sys/bus/hid/drivers/hid-generic")
    microsoft = Path("/sys/bus/hid/drivers/microsoft")
    for dev in Path("/sys/bus/hid/devices").glob("0003:045E:0745.*"):
        name = dev.name
        unbind = hid_generic / "unbind"
        bind = microsoft / "bind"
        if (hid_generic / name).exists():
            unbind.write_text(name)
        bind.write_text(name)
        print(f"bound {name} -> microsoft")
    print("Favorites 1–5 should now appear as F14–F18 in Settings → Keyboard.")
    return 0
