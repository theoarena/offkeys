# tests/test_mapper_reports.py
#
# One report going idle lifts only the keys from that interface and report
# id. A status report must not lift a key another report is holding.

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mskb_hid import HidDescriptor, track_report  # noqa: E402
from mskb_mapper import _handle_report  # noqa: E402

DEVICE = "045e:0745"

# Consumer Control, report id 1, two 1-bit usages 0xB5 then 0xE9.
CONSUMER_TWO_BITS = bytes(
    [
        0x05,
        0x0C,
        0x09,
        0x01,
        0xA1,
        0x01,
        0x85,
        0x01,
        0x09,
        0xB5,
        0x09,
        0xE9,
        0x75,
        0x01,
        0x95,
        0x02,
        0x81,
        0x02,
        0xC0,
    ]
)

KEY_TABLE = {"F13": 183}
BINDING_ID = "045e:0745:000c:00b5"


class _FakeUinput:
    """Records emit calls. The real uinput device needs root and a node."""

    def __init__(self) -> None:
        self.events: list[tuple[int, bool]] = []

    def emit(self, code: int, pressed: bool) -> None:
        self.events.append((code, pressed))


class MapperReportTests(unittest.TestCase):
    def setUp(self) -> None:
        patch = mock.patch("mskb_mapper._run_exec")
        self.run_exec = patch.start()
        self.addCleanup(patch.stop)

    def test_generic_exec_fires_once_while_held(self) -> None:
        desc = HidDescriptor(device=DEVICE, raw=CONSUMER_TWO_BITS)
        # Already-down bits in the first frame are the idle snapshot, not a press.
        idle, idle_ids = track_report(None, bytes([0x01, 0x01]), desc)
        self.assertEqual((idle, idle_ids), (bytes([0x01, 0x01]), []))
        baseline, ids = track_report(bytes([0x01, 0x00]), bytes([0x01, 0x01]), desc)
        self.assertEqual(ids, [BINDING_ID])
        self.assertEqual(baseline, bytes([0x01, 0x00]))
        again, again_ids = track_report(baseline, bytes([0x01, 0x01]), desc)
        self.assertEqual(again, baseline)
        self.assertEqual(again_ids, ids)

        bindings = {BINDING_ID: {"key": "", "exec": "echo hi"}}
        active: dict = {}
        source = ("input2", 1)
        _handle_report(source, ids, bindings, KEY_TABLE, _FakeUinput(), active, set())
        _handle_report(source, ids, bindings, KEY_TABLE, _FakeUinput(), active, set())
        self.run_exec.assert_called_once_with("echo hi")

    def test_release_only_that_source(self) -> None:
        bindings = {BINDING_ID: {"key": "F13", "exec": "echo hi"}}
        uinput = _FakeUinput()
        active: dict = {}
        ids = [BINDING_ID]
        _handle_report(("input2", 1), ids, bindings, KEY_TABLE, uinput, active, set())
        self.assertEqual(active[("input2", 1)], {BINDING_ID: "F13"})
        self.assertIn((183, True), uinput.events)

        _handle_report(("input2", 0x16), ids, bindings, KEY_TABLE, uinput, active, set())
        self.assertEqual(active[("input2", 0x16)], {BINDING_ID: "F13"})

        _handle_report(("input2", 1), [], bindings, KEY_TABLE, uinput, active, set())
        self.assertNotIn(("input2", 1), active)
        self.assertEqual(active[("input2", 0x16)], {BINDING_ID: "F13"})
        self.assertEqual(uinput.events.count((183, False)), 1)

    def test_label_does_not_change_firing(self) -> None:
        bindings = {
            BINDING_ID: {"key": "F13", "exec": "echo hi", "label": "Home"},
        }
        uinput = _FakeUinput()
        active: dict = {}
        _handle_report(
            ("input2", 1), [BINDING_ID], bindings, KEY_TABLE, uinput, active, set()
        )
        self.run_exec.assert_called_once_with("echo hi")
        self.assertEqual(uinput.events, [(183, True)])

    def test_status_report_does_not_release(self) -> None:
        bindings = {BINDING_ID: {"key": "F13", "exec": ""}}
        uinput = _FakeUinput()
        active: dict = {}
        _handle_report(("input2", 7), [BINDING_ID], bindings, KEY_TABLE, uinput, active, set())
        self.assertEqual(uinput.events, [(183, True)])
        self.assertEqual(active[("input2", 7)], {BINDING_ID: "F13"})

        _handle_report(("input2", 0x21), [], bindings, KEY_TABLE, uinput, active, set())
        self.assertEqual(active[("input2", 7)], {BINDING_ID: "F13"})
        self.assertEqual(uinput.events, [(183, True)])


if __name__ == "__main__":
    unittest.main()
