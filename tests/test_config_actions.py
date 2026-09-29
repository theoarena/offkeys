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
        self.assertEqual(binding, {"key": "", "exec": "", "kind": "none"})
        self.assertEqual(mskb.kind_for_binding(binding), "none")

    def test_round_trip_key(self) -> None:
        binding = mskb.binding_for_kind("key", "F15")
        self.assertEqual(binding, {"key": "F15", "exec": "", "kind": "key"})
        self.assertEqual(mskb.kind_for_binding(binding), "key")

    def test_round_trip_command(self) -> None:
        binding = mskb.binding_for_kind("command", "echo hi")
        self.assertEqual(binding, {"key": "", "exec": "echo hi", "kind": "command"})
        self.assertEqual(mskb.kind_for_binding(binding), "command")

    def test_same_exec_keeps_app_and_command_apart(self) -> None:
        app = mskb.binding_for_kind("app", "btop")
        command = mskb.binding_for_kind("command", "btop")
        self.assertEqual(app, {"key": "", "exec": "btop", "kind": "app"})
        self.assertEqual(command, {"key": "", "exec": "btop", "kind": "command"})
        self.assertEqual(mskb.kind_for_binding(app), "app")
        self.assertEqual(
            mskb.kind_for_binding(command, installed_execs=["btop"]),
            "command",
        )

    def test_missing_kind_matching_desktop_is_app(self) -> None:
        binding = {"key": "", "exec": "btop"}
        self.assertEqual(mskb.kind_for_binding(binding), "command")
        self.assertEqual(
            mskb.kind_for_binding(binding, installed_execs=["btop"]),
            "app",
        )

    def test_unknown_kind_falls_back(self) -> None:
        binding = {"key": "F15", "exec": "", "kind": "bogus"}
        self.assertEqual(mskb.kind_for_binding(binding), "key")

    def test_dual_fire_loads_as_command_and_save_clears_key(self) -> None:
        dual = {
            "key": "F14",
            "exec": "/usr/bin/flatpak run md.obsidian.Obsidian",
        }
        self.assertEqual(mskb.kind_for_binding(dual), "command")
        saved = mskb.binding_for_kind("command", dual["exec"])
        self.assertEqual(saved["key"], "")
        self.assertEqual(saved["kind"], "command")
        self.assertEqual(saved["exec"], dual["exec"])

    def test_label_does_not_change_kind(self) -> None:
        binding = {"key": "F15", "exec": "", "label": "Home"}
        self.assertEqual(mskb.kind_for_binding(binding), "key")
        self.assertEqual(
            mskb.binding_for_kind("key", "F15"),
            {"key": "F15", "exec": "", "kind": "key"},
        )

    def test_app_kind_strips_exec(self) -> None:
        saved = mskb.binding_for_kind(
            "app",
            "/usr/bin/flatpak run --file-forwarding md.obsidian.Obsidian @@u %U @@",
        )
        self.assertEqual(saved["key"], "")
        self.assertEqual(saved["kind"], "app")
        self.assertEqual(saved["exec"], "/usr/bin/flatpak run md.obsidian.Obsidian")


class AsBindingTests(unittest.TestCase):
    def test_keeps_label_and_known_kind(self) -> None:
        stored = mskb.as_binding(
            {"key": "", "exec": "btop", "label": "  Star  ", "kind": "command"}
        )
        self.assertEqual(
            stored,
            {"key": "", "exec": "btop", "label": "Star", "kind": "command"},
        )

    def test_drops_blank_label_and_unknown_kind(self) -> None:
        stored = mskb.as_binding(
            {"key": "F13", "exec": "", "label": "   ", "kind": "desktop"}
        )
        self.assertEqual(stored, {"key": "F13", "exec": ""})

    def test_non_dict_is_empty_pair(self) -> None:
        self.assertEqual(mskb.as_binding(None), {"key": "", "exec": ""})


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
                data["bindings"]["045e:0745"]["consumer_0x0223"],
                {"key": "", "exec": "", "label": "Home"},
            )


class ActionSummaryTests(unittest.TestCase):
    def test_app_uses_caller_name(self) -> None:
        self.assertEqual(mskb.action_summary("app", "Arquivos"), "Opens Arquivos")

    def test_app_without_name(self) -> None:
        self.assertEqual(mskb.action_summary("app", "  "), "Opens an app")

    def test_shortcut(self) -> None:
        self.assertEqual(mskb.action_summary("key", "F15"), "Sends F15")
        self.assertEqual(mskb.action_summary("key", ""), "Sends a shortcut")

    def test_short_command_is_the_command(self) -> None:
        self.assertEqual(mskb.action_summary("command", "notify-send hi"), "notify-send hi")
        self.assertEqual(mskb.action_summary("command", "x" * 32), "x" * 32)

    def test_long_or_empty_command_is_generic(self) -> None:
        self.assertEqual(mskb.action_summary("command", "x" * 33), "Runs a command")
        self.assertEqual(mskb.action_summary("command", "   "), "Runs a command")

    def test_nothing(self) -> None:
        self.assertEqual(mskb.action_summary("none", ""), "Does nothing")
        self.assertEqual(mskb.action_summary("other", "x"), "Does nothing")


class KeyCardTextTests(unittest.TestCase):
    def test_custom_title_shows_summary_not_id(self) -> None:
        binding = {"key": "", "exec": "", "label": "Home"}
        self.assertEqual(
            mskb.key_card_text(
                "consumer_0x0223",
                binding,
                "consumer_0x0223",
                "Opens Arquivos",
            ),
            ("Home", "Opens Arquivos", ""),
        )

    def test_shared_title_keeps_id_line(self) -> None:
        binding = {"label": "Home"}
        self.assertEqual(
            mskb.key_card_text(
                "consumer_0x0223",
                binding,
                "consumer_0x0223",
                "Opens Arquivos",
                show_id=True,
            ),
            ("Home", "Opens Arquivos", "consumer_0x0223"),
        )

    def test_favorite_without_label_uses_fallback(self) -> None:
        binding = {"key": "F14", "exec": ""}
        self.assertEqual(
            mskb.key_card_text("favorites_1", binding, "Favorite 1", "Sends F14"),
            ("Favorite 1", "Sends F14", ""),
        )

    def test_blank_label_uses_fallback(self) -> None:
        binding = {"key": "", "exec": "", "label": "   "}
        self.assertEqual(
            mskb.key_card_text(
                "consumer_0x0223",
                binding,
                "consumer_0x0223",
                "Does nothing",
            ),
            ("consumer_0x0223", "Does nothing", ""),
        )


class KeyboardLabelTests(unittest.TestCase):
    def test_drops_repeated_vendor_and_marks(self) -> None:
        self.assertEqual(
            mskb.keyboard_display_name("Microsoft Microsoft® 2.4GHz Transceiver"),
            "Microsoft 2.4GHz Transceiver",
        )
        self.assertEqual(
            mskb.keyboard_display_name("Microsoft® Microsoft 2.4GHz"),
            "Microsoft 2.4GHz",
        )

    def test_plain_name_stays(self) -> None:
        self.assertEqual(
            mskb.keyboard_display_name("Logitech USB Receiver"),
            "Logitech USB Receiver",
        )
        self.assertEqual(mskb.keyboard_display_name("Foo™ Bar"), "Foo Bar")

    def test_blank_is_empty(self) -> None:
        self.assertEqual(mskb.keyboard_display_name("   "), "")
        self.assertEqual(mskb.keyboard_display_name(""), "")

    def test_subtitle_plugged_saved_and_missing(self) -> None:
        self.assertEqual(
            mskb.keyboard_row_subtitle("045e:0745", present=True),
            "045e:0745",
        )
        self.assertEqual(
            mskb.keyboard_row_subtitle("045e:0745", present=False),
            "045e:0745 · Unplugged",
        )
        self.assertEqual(
            mskb.keyboard_row_subtitle("  ", present=True),
            "No keyboard selected",
        )

    def test_shared_titles(self) -> None:
        titles = {"a": "Home", "b": "Home", "c": "Favorite 1"}
        self.assertEqual(mskb.ids_sharing_title(titles), {"a", "b"})
        self.assertEqual(mskb.ids_sharing_title({"a": "Home"}), set())


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
            bucket = data["bindings"]["045e:0745"]
            self.assertEqual(data["_comment"], "keep me")
            self.assertEqual(
                bucket["favorites_1"],
                {"key": "", "exec": "/usr/bin/true"},
            )
            self.assertEqual(bucket["chat"], {"key": "CHAT", "exec": ""})
            self.assertEqual(bucket["favorites_star"]["key"], "F13")

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
            bucket = on_disk["bindings"]["045e:0745"]
            self.assertEqual(bucket["favorites_1"], payload["bindings"]["favorites_1"])
            self.assertEqual(
                written["bindings"]["045e:0745"]["favorites_1"],
                payload["bindings"]["favorites_1"],
            )
            self.assertIn("favorites_star", bucket)

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
                {"045e:0745": {"favorites_1": {"key": "F14", "exec": ""}}},
            )
            self.assertNotIn("chat", data["bindings"]["045e:0745"])
            self.assertNotIn("favorites_star", data["bindings"]["045e:0745"])

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
            bucket = data["bindings"]["045e:0745"]
            self.assertEqual(bucket["chat"], {"key": "CHAT", "exec": ""})
            self.assertIn("favorites_star", bucket)
            self.assertEqual(bucket["favorites_1"], {"key": "F14", "exec": ""})

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
                {"045e:0745": {"favorites_1": {"key": "F14", "exec": ""}}},
            )
            self.assertNotIn("favorites_star", data["bindings"]["045e:0745"])


class NestedBindingsTests(unittest.TestCase):
    def test_flat_2000_migrates_under_keyboard_2000(self) -> None:
        maps = mskb.nested_bindings(
            {
                "devices": ["045e:0745"],
                "bindings": {
                    "favorites_1": {"key": "F14", "exec": ""},
                    "favorites_star": {"key": "F13", "exec": ""},
                },
            }
        )
        self.assertEqual(
            maps["045e:0745"]["favorites_1"],
            {"key": "F14", "exec": ""},
        )
        self.assertEqual(maps["045e:0745"]["favorites_star"]["key"], "F13")
        self.assertEqual(list(maps), ["045e:0745"])

    def test_generic_id_migrates_under_its_vidpid(self) -> None:
        maps = mskb.nested_bindings(
            {
                "devices": ["046d:c52b"],
                "bindings": {
                    "046d:c52b:000c:0182": {"key": "F13", "exec": ""},
                },
            }
        )
        self.assertEqual(
            maps["046d:c52b"]["046d:c52b:000c:0182"],
            {"key": "F13", "exec": ""},
        )
        self.assertNotIn("045e:0745", maps)

    def test_nested_file_round_trips(self) -> None:
        payload = {
            "devices": ["045e:0745", "046d:c52b"],
            "bindings": {
                "045e:0745": {"favorites_1": {"key": "F14", "exec": ""}},
                "046d:c52b": {"046d:c52b:000c:0182": {"key": "F13", "exec": ""}},
            },
        }
        self.assertEqual(mskb.nested_bindings(payload), payload["bindings"])

    def test_load_empty_bindings_stays_empty_until_ensure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"devices": ["045e:0745"], "bindings": {}}) + "\n"
            )
            data = mskb.load_config(path)
            self.assertEqual(data["bindings"], {})
            original = dict(data["bindings"])
            mskb.ensure_device_map(data["bindings"], "045e:0745")
            self.assertIn("favorites_star", data["bindings"]["045e:0745"])
            self.assertNotEqual(data["bindings"], original)

    def test_load_does_not_inject_favorites_into_empty_logitech(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "devices": ["046d:c52b"],
                        "bindings": {"046d:c52b": {}},
                    }
                )
                + "\n"
            )
            data = mskb.load_config(path)
            self.assertEqual(data["bindings"], {"046d:c52b": {}})
            self.assertNotIn("045e:0745", data["bindings"])

    def test_ensure_device_map_seeds_2000_once(self) -> None:
        maps: dict = {}
        first = mskb.ensure_device_map(maps, "045e:0745")
        self.assertIn("favorites_star", first)
        first.clear()
        again = mskb.ensure_device_map(maps, "045e:0745")
        self.assertEqual(again, {})
        self.assertIs(again, maps["045e:0745"])

    def test_ensure_device_map_other_keyboard_starts_empty(self) -> None:
        maps: dict = {}
        bucket = mskb.ensure_device_map(maps, "046d:c52b")
        self.assertEqual(bucket, {})
        self.assertNotIn("favorites_1", bucket)

    def test_bindings_for_device_missing_is_empty(self) -> None:
        self.assertEqual(mskb.bindings_for_device({}, "046d:c52b"), {})

    def test_bindings_for_device_isolates_slices(self) -> None:
        maps = {
            "045e:0745": {"favorites_1": {"key": "F14", "exec": ""}},
            "046d:c52b": {},
        }
        self.assertEqual(mskb.bindings_for_device(maps, "046d:c52b"), {})
        self.assertIn("favorites_1", mskb.bindings_for_device(maps, "045e:0745"))

    def test_merge_stays_in_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "devices": ["045e:0745", "046d:c52b"],
                        "bindings": {
                            "045e:0745": {"favorites_1": {"key": "F14", "exec": ""}},
                            "046d:c52b": {
                                "046d:c52b:000c:0182": {"key": "F13", "exec": ""}
                            },
                        },
                    }
                )
                + "\n"
            )
            mskb.save_config(
                path,
                {
                    "bindings": {
                        "045e:0745": {"favorites_1": {"key": "F15", "exec": ""}},
                    }
                },
            )
            data = json.loads(path.read_text())
            self.assertEqual(
                data["bindings"]["045e:0745"]["favorites_1"]["key"],
                "F15",
            )
            self.assertEqual(
                data["bindings"]["046d:c52b"]["046d:c52b:000c:0182"]["key"],
                "F13",
            )

    def test_put_learned_binding_writes_captured_device(self) -> None:
        config: dict = {
            "devices": ["045e:0745"],
            "bindings": {"045e:0745": {"favorites_1": {"key": "F14", "exec": ""}}},
        }
        mskb.put_learned_binding(
            config,
            "046d:c52b",
            "home",
            "046d:c52b:000c:0182",
            {"key": "", "exec": ""},
        )
        self.assertIn("046d:c52b", config["devices"])
        logitech = config["bindings"]["046d:c52b"]
        self.assertEqual(logitech["home"], {"key": "", "exec": ""})
        self.assertEqual(logitech["046d:c52b:000c:0182"], {"key": "", "exec": ""})
        self.assertEqual(
            config["bindings"]["045e:0745"]["favorites_1"]["key"],
            "F14",
        )


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
