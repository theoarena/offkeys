# tests/test_config_actions.py
#
# Unit tests for exclusive-mode helpers and atomic config merge.
# Imports mskb only — the GUI (gi) is not loaded here.

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mskb  # noqa: E402
import mskb_bindings  # noqa: E402


class StripFieldCodesTests(unittest.TestCase):
    def test_flatpak_file_forwarding_and_percent_u(self) -> None:
        raw = (
            "/usr/bin/flatpak run --file-forwarding md.obsidian.Obsidian @@u %U @@"
        )
        self.assertEqual(
            mskb.strip_field_codes(raw),
            "/usr/bin/flatpak run md.obsidian.Obsidian",
        )

    def test_desktop_percent_f(self) -> None:
        self.assertEqual(mskb.strip_field_codes("foo %F"), "foo")

    def test_plain_command_unchanged(self) -> None:
        self.assertEqual(
            mskb.strip_field_codes("/usr/bin/true"),
            "/usr/bin/true",
        )


class KindBindingTests(unittest.TestCase):
    def test_round_trip_none(self) -> None:
        binding = mskb.binding_for_kind("none")
        self.assertEqual(binding, {"key": "", "exec": ""})
        self.assertEqual(mskb.kind_for_binding(binding), "none")

    def test_round_trip_key(self) -> None:
        binding = mskb.binding_for_kind("key", "F15")
        self.assertEqual(binding, {"key": "F15", "exec": ""})
        self.assertEqual(mskb.kind_for_binding(binding), "key")

    def test_round_trip_command(self) -> None:
        binding = mskb.binding_for_kind("command", "echo hi")
        self.assertEqual(binding, {"key": "", "exec": "echo hi"})
        self.assertEqual(mskb.kind_for_binding(binding), "command")

    def test_dual_fire_loads_as_command_and_save_clears_key(self) -> None:
        dual = {
            "key": "F14",
            "exec": "/usr/bin/flatpak run md.obsidian.Obsidian",
        }
        self.assertEqual(mskb.kind_for_binding(dual), "command")
        saved = mskb.binding_for_kind("command", dual["exec"])
        self.assertEqual(saved["key"], "")
        self.assertEqual(saved["exec"], dual["exec"])

    def test_label_does_not_change_kind(self) -> None:
        binding = {"key": "F15", "exec": "", "label": "Home"}
        self.assertEqual(mskb.kind_for_binding(binding), "key")
        self.assertEqual(mskb.binding_for_kind("key", "F15"), {"key": "F15", "exec": ""})

    def test_app_kind_strips_exec(self) -> None:
        saved = mskb.binding_for_kind(
            "app",
            "/usr/bin/flatpak run --file-forwarding md.obsidian.Obsidian @@u %U @@",
        )
        self.assertEqual(saved["key"], "")
        self.assertEqual(saved["exec"], "/usr/bin/flatpak run md.obsidian.Obsidian")


class BindingLabelTests(unittest.TestCase):
    def test_strips_and_blank_is_empty(self) -> None:
        self.assertEqual(mskb.binding_label("  Home  "), "Home")
        self.assertEqual(mskb.binding_label("   "), "")
        self.assertEqual(mskb.binding_label(None), "")

    def test_replace_bindings_keeps_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            stored = mskb.binding_label("  Home  ")
            payload = {
                "bindings": {
                    "consumer_0x0223": {"key": "", "exec": "", "label": stored},
                }
            }
            mskb.save_config(path, payload, replace_bindings=True)
            data = json.loads(path.read_text())
            self.assertEqual(
                data["bindings"]["consumer_0x0223"],
                {"key": "", "exec": "", "label": "Home"},
            )


class KeyCardTextTests(unittest.TestCase):
    def test_custom_title_keeps_id_as_caption(self) -> None:
        binding = {"key": "", "exec": "", "label": "Home"}
        self.assertEqual(
            mskb.key_card_text("consumer_0x0223", binding, "consumer_0x0223"),
            ("Home", "consumer_0x0223"),
        )

    def test_favorite_without_label_has_empty_caption(self) -> None:
        binding = {"key": "F14", "exec": ""}
        self.assertEqual(
            mskb.key_card_text("favorites_1", binding, "Favorite 1"),
            ("Favorite 1", ""),
        )

    def test_blank_label_uses_fallback(self) -> None:
        binding = {"key": "", "exec": "", "label": "   "}
        self.assertEqual(
            mskb.key_card_text("consumer_0x0223", binding, "consumer_0x0223"),
            ("consumer_0x0223", ""),
        )


class ShortcutChoicesTests(unittest.TestCase):
    def test_standard_f_keys(self) -> None:
        keys = mskb.shortcut_key_choices("F14")
        self.assertEqual(keys[0], "F13")
        self.assertEqual(keys[-1], "F24")
        self.assertEqual(keys.count("F14"), 1)

    def test_current_outside_range_is_preserved(self) -> None:
        keys = mskb.shortcut_key_choices("PROG1")
        self.assertIn("PROG1", keys)
        self.assertIn("F13", keys)
        self.assertIn("F24", keys)


class SaveConfigTests(unittest.TestCase):
    def test_merge_keeps_learned_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "_comment": "keep me",
                        "bindings": {
                            "favorites_1": {"key": "F14", "exec": ""},
                            "favorites_star": {"key": "F13", "exec": ""},
                            "chat": {"key": "CHAT", "exec": ""},
                        },
                    },
                    indent=2,
                )
                + "\n"
            )
            mskb.save_config(
                path,
                {
                    "bindings": {
                        "favorites_1": {"key": "", "exec": "/usr/bin/true"},
                        "favorites_2": {"key": "F15", "exec": ""},
                    }
                },
            )
            data = json.loads(path.read_text())
            self.assertEqual(data["_comment"], "keep me")
            self.assertEqual(
                data["bindings"]["favorites_1"],
                {"key": "", "exec": "/usr/bin/true"},
            )
            self.assertEqual(
                data["bindings"]["chat"],
                {"key": "CHAT", "exec": ""},
            )
            self.assertEqual(data["bindings"]["favorites_star"]["key"], "F13")

    def test_atomic_json_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            payload = {
                "bindings": {
                    "favorites_1": {"key": "", "exec": "echo one"},
                }
            }
            written = mskb.save_config(path, payload)
            self.assertFalse(path.with_name("config.json.tmp").exists())
            on_disk = json.loads(path.read_text())
            self.assertEqual(on_disk["bindings"]["favorites_1"], payload["bindings"]["favorites_1"])
            self.assertEqual(written["bindings"]["favorites_1"], payload["bindings"]["favorites_1"])
            self.assertIn("favorites_star", on_disk["bindings"])

    def test_replace_bindings_drops_omitted_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "bindings": {
                            "chat": {"key": "CHAT", "exec": ""},
                            "favorites_star": {"key": "F13", "exec": ""},
                        }
                    }
                )
                + "\n"
            )
            payload = {"bindings": {"favorites_1": {"key": "F14", "exec": ""}}}
            mskb.save_config(path, payload, replace_bindings=True)
            data = json.loads(path.read_text())
            self.assertEqual(
                data["bindings"],
                {"favorites_1": {"key": "F14", "exec": ""}},
            )
            self.assertNotIn("chat", data["bindings"])
            self.assertNotIn("favorites_star", data["bindings"])

    def test_same_payload_without_replace_keeps_chat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "bindings": {
                            "chat": {"key": "CHAT", "exec": ""},
                            "favorites_star": {"key": "F13", "exec": ""},
                        }
                    }
                )
                + "\n"
            )
            payload = {"bindings": {"favorites_1": {"key": "F14", "exec": ""}}}
            mskb.save_config(path, payload)
            data = json.loads(path.read_text())
            self.assertEqual(data["bindings"]["chat"], {"key": "CHAT", "exec": ""})
            self.assertIn("favorites_star", data["bindings"])
            self.assertEqual(
                data["bindings"]["favorites_1"],
                {"key": "F14", "exec": ""},
            )

    def test_replace_on_new_file_omits_default_favorites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            mskb.save_config(
                path,
                {"bindings": {"favorites_1": {"key": "F14", "exec": ""}}},
                replace_bindings=True,
            )
            data = json.loads(path.read_text())
            self.assertEqual(
                data["bindings"],
                {"favorites_1": {"key": "F14", "exec": ""}},
            )
            self.assertNotIn("favorites_star", data["bindings"])


class DevicesFromConfigTests(unittest.TestCase):
    def test_missing_or_empty_uses_microsoft_default(self) -> None:
        self.assertEqual(mskb_bindings.devices_from_config({}), ["045e:0745"])
        self.assertEqual(
            mskb_bindings.devices_from_config({"devices": []}),
            ["045e:0745"],
        )
        self.assertEqual(
            mskb_bindings.devices_from_config({"devices": "045e:0745"}),
            ["045e:0745"],
        )

    def test_explicit_list_is_kept(self) -> None:
        self.assertEqual(
            mskb_bindings.devices_from_config({"devices": ["046d:c52b"]}),
            ["046d:c52b"],
        )
        self.assertEqual(
            mskb_bindings.devices_from_config({"devices": ["045E:0745"]}),
            ["045e:0745"],
        )


class MapperCmdlineTests(unittest.TestCase):
    def test_run_matches(self) -> None:
        cmdline = b"/usr/bin/python3\0/home/theoarena/Dev/microsoft-keyboard/mskb.py\0run\0"
        self.assertTrue(mskb._cmdline_is_mapper_run(cmdline))

    def test_gui_does_not_match(self) -> None:
        cmdline = b"/usr/bin/python3\0/home/theoarena/Dev/microsoft-keyboard/mskb.py\0gui\0"
        self.assertFalse(mskb._cmdline_is_mapper_run(cmdline))

    def test_probe_does_not_match(self) -> None:
        cmdline = b"python3\0mskb.py\0probe\0"
        self.assertFalse(mskb._cmdline_is_mapper_run(cmdline))


if __name__ == "__main__":
    unittest.main()
