# tests/test_mapper_lifecycle.py
#
# Unit tests for systemd-only mapper restart and GNOME autostart cleanup.
# Mocks systemctl / PID helpers — does not touch the live user bus.

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mskb  # noqa: E402
import mskb_lifecycle  # noqa: E402


class DesktopExecTests(unittest.TestCase):
    def test_run_exec_matches(self) -> None:
        text = (
            "[Desktop Entry]\n"
            "Exec=/usr/bin/python3 /home/theoarena/Dev/microsoft-keyboard/mskb.py run\n"
        )
        self.assertTrue(mskb.desktop_launches_mapper_run(text))

    def test_gui_exec_does_not_match(self) -> None:
        text = "Exec=/usr/bin/python3 /home/x/mskb.py gui\n"
        self.assertFalse(mskb.desktop_launches_mapper_run(text))


class RemoveAutostartTests(unittest.TestCase):
    def test_removes_only_mapper_run_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            conflict = base / "systemctl.desktop"
            conflict.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Exec=/usr/bin/python3 /home/theoarena/Dev/microsoft-keyboard/mskb.py run\n"
                "Name=Old extra-key mapper\n"
            )
            keep = base / "other.desktop"
            keep.write_text(
                "[Desktop Entry]\n"
                "Exec=/usr/bin/true\n"
                "Name=Other\n"
            )
            gui = base / "mskb-gui.desktop"
            gui.write_text(
                "Exec=/usr/bin/python3 /home/theoarena/Dev/microsoft-keyboard/mskb.py gui\n"
            )
            removed = mskb.remove_mskb_autostart(base)
            self.assertEqual(removed, [conflict])
            self.assertFalse(conflict.exists())
            self.assertTrue(keep.exists())
            self.assertTrue(gui.exists())


class MapperStatusMessageTests(unittest.TestCase):
    def test_ok_and_failures(self) -> None:
        self.assertEqual(mskb.mapper_status_message("ok", "restarted"), "Mapper restarted")
        self.assertEqual(mskb.mapper_status_message("ok", "started"), "Mapper started")
        self.assertIn("permission", mskb.mapper_status_message("failed", "permission"))
        self.assertIn("systemctl", mskb.mapper_status_message("failed", "systemctl"))
        self.assertIn("not active", mskb.mapper_status_message("failed", "not_active"))


class RestartMapperTests(unittest.TestCase):
    def test_active_unit_restarts(self) -> None:
        calls: list[list[str]] = []

        def fake_systemctl(args: list[str]) -> int:
            calls.append(list(args))
            if args[:2] == ["is-active", "--quiet"]:
                # First check: active before; after restart: active
                return 0
            if args[0] == "restart":
                return 0
            return 1

        with (
            mock.patch.object(mskb_lifecycle, "remove_mskb_autostart", return_value=[]),
            mock.patch.object(mskb_lifecycle, "mapper_run_pids", return_value=[]),
            mock.patch.object(mskb_lifecycle, "_systemctl_user", side_effect=fake_systemctl),
        ):
            status, reason = mskb.restart_mapper()
        self.assertEqual((status, reason), ("ok", "restarted"))
        self.assertIn(["restart", "mskb.service"], calls)

    def test_inactive_enable_now(self) -> None:
        active_checks = {"n": 0}

        def fake_systemctl(args: list[str]) -> int:
            if args[:2] == ["is-active", "--quiet"]:
                active_checks["n"] += 1
                # Before: inactive; after enable: active
                return 1 if active_checks["n"] == 1 else 0
            if args[:2] == ["enable", "--now"]:
                return 0
            return 1

        with (
            mock.patch.object(mskb_lifecycle, "remove_mskb_autostart", return_value=[]),
            mock.patch.object(mskb_lifecycle, "mapper_run_pids", return_value=[]),
            mock.patch.object(mskb_lifecycle, "_systemctl_user", side_effect=fake_systemctl),
        ):
            status, reason = mskb.restart_mapper()
        self.assertEqual((status, reason), ("ok", "started"))

    def test_stray_permission_failure(self) -> None:
        def fake_systemctl(args: list[str]) -> int:
            if args[:2] == ["is-active", "--quiet"]:
                return 1  # inactive
            return 1

        with (
            mock.patch.object(mskb_lifecycle, "remove_mskb_autostart", return_value=[]),
            mock.patch.object(mskb_lifecycle, "mapper_run_pids", return_value=[111]),
            mock.patch.object(mskb_lifecycle, "_stop_mapper_pids", return_value=False),
            mock.patch.object(mskb_lifecycle, "_systemctl_user", side_effect=fake_systemctl),
        ):
            status, reason = mskb.restart_mapper()
        self.assertEqual((status, reason), ("failed", "permission"))

    def test_systemctl_restart_failure(self) -> None:
        def fake_systemctl(args: list[str]) -> int:
            if args[:2] == ["is-active", "--quiet"]:
                return 0
            if args[0] == "restart":
                return 1
            return 1

        with (
            mock.patch.object(mskb_lifecycle, "remove_mskb_autostart", return_value=[]),
            mock.patch.object(mskb_lifecycle, "mapper_run_pids", return_value=[]),
            mock.patch.object(mskb_lifecycle, "_systemctl_user", side_effect=fake_systemctl),
        ):
            status, reason = mskb.restart_mapper()
        self.assertEqual((status, reason), ("failed", "systemctl"))

    def test_not_active_after_enable(self) -> None:
        def fake_systemctl(args: list[str]) -> int:
            if args[:2] == ["is-active", "--quiet"]:
                return 1  # never becomes active
            if args[:2] == ["enable", "--now"]:
                return 0
            return 1

        with (
            mock.patch.object(mskb_lifecycle, "remove_mskb_autostart", return_value=[]),
            mock.patch.object(mskb_lifecycle, "mapper_run_pids", return_value=[]),
            mock.patch.object(mskb_lifecycle, "_systemctl_user", side_effect=fake_systemctl),
        ):
            status, reason = mskb.restart_mapper()
        self.assertEqual((status, reason), ("failed", "not_active"))


if __name__ == "__main__":
    unittest.main()
