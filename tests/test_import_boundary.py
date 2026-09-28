# tests/test_import_boundary.py
#
# Locks the domain-module split: mskb stays GI-free at import time,
# bindings/lifecycle never import HID, and the public facade keeps
# every name the GUI and existing tests call.

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mskb  # noqa: E402

PUBLIC_NAMES = (
    "strip_field_codes",
    "ensure_config",
    "load_config",
    "FAVORITE_IDS",
    "KEYBOARD_2000",
    "SYSTEM_SHORTCUT_KEYS",
    "kind_for_binding",
    "shortcut_key_choices",
    "binding_for_kind",
    "binding_label",
    "key_card_text",
    "is_binding_entry",
    "nested_bindings",
    "bindings_for_device",
    "ensure_device_map",
    "put_learned_binding",
    "restart_mapper",
    "mapper_status_message",
    "save_config",
    "_cmdline_is_mapper_run",
    "desktop_launches_mapper_run",
    "remove_mskb_autostart",
    "mapper_run_pids",
    "_systemctl_user",
    "_stop_mapper_pids",
    "report_ids",
    "skip_interface",
    "HidDescriptor",
    "track_report",
    "devices_from_config",
)


def _toplevel_imported_modules(path: Path) -> set[str]:
    """Module-body imports only — nested imports (lazy GUI) are allowed."""
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


class ImportBoundaryTests(unittest.TestCase):
    def test_import_mskb_does_not_load_gi(self) -> None:
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "import mskb\n"
            "assert 'gi' not in sys.modules, sorted(sys.modules)\n"
            "assert 'mskb_gui' not in sys.modules, sorted(sys.modules)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_bindings_and_lifecycle_do_not_import_hid(self) -> None:
        for name in ("mskb_bindings.py", "mskb_lifecycle.py"):
            imports = _toplevel_imported_modules(ROOT / name)
            self.assertNotIn("mskb_hid", imports, f"{name} must not import mskb_hid")

    def test_mskb_has_no_toplevel_gi_or_gui_import(self) -> None:
        imports = _toplevel_imported_modules(ROOT / "mskb.py")
        self.assertNotIn("gi", imports)
        self.assertNotIn("mskb_gui", imports)

    def test_public_facade_exports(self) -> None:
        for name in PUBLIC_NAMES:
            self.assertTrue(hasattr(mskb, name), f"mskb missing {name}")


if __name__ == "__main__":
    unittest.main()
