# tests/test_hid_identify.py
#
# A changed usage becomes a binding id. Wireless Keyboard 2000 names stay
# ahead of those ids so older learned bindings still match first.

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mskb_hid import HidDescriptor, report_ids, skip_interface  # noqa: E402

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


class ReportIdsTests(unittest.TestCase):
    def test_consumer_bit_press_and_release(self) -> None:
        desc = HidDescriptor(device=DEVICE, raw=CONSUMER_TWO_BITS)
        self.assertEqual(
            report_ids(bytes([0x01, 0x01]), desc, bytes([0x01, 0x00])),
            ["045e:0745:000c:00b5"],
        )
        self.assertEqual(
            report_ids(bytes([0x01, 0x00]), desc, bytes([0x01, 0x01])),
            [],
        )

    def test_wide_field_value(self) -> None:
        desc = HidDescriptor(
            device=DEVICE,
            raw=bytes(
                [
                    0x06,
                    0x05,
                    0xFF,
                    0x09,
                    0x01,
                    0xA1,
                    0x01,
                    0x85,
                    0x01,
                    0x09,
                    0x01,
                    0x75,
                    0x08,
                    0x95,
                    0x01,
                    0x81,
                    0x02,
                    0xC0,
                ]
            ),
        )
        self.assertEqual(
            report_ids(bytes([0x01, 0x04]), desc, bytes([0x01, 0x00])),
            ["045e:0745:ff05:0001:4"],
        )

    def test_sticky_bit_already_down_is_ignored(self) -> None:
        desc = HidDescriptor(device=DEVICE, raw=CONSUMER_TWO_BITS)
        self.assertEqual(
            report_ids(bytes([0x01, 0x03]), desc, bytes([0x01, 0x01])),
            ["045e:0745:000c:00e9"],
        )
        self.assertEqual(
            report_ids(bytes([0x01, 0x01]), desc, bytes([0x01, 0x01])),
            [],
        )

    def test_keyboard_2000_favorites_lead(self) -> None:
        # Report id 7, first payload bit is consumer usage 0x00B5, so a
        # generic id exists beside the favorites name the 2000 decoder emits.
        desc = HidDescriptor(
            device=DEVICE,
            raw=bytes(
                [
                    0x05,
                    0x0C,
                    0x09,
                    0x01,
                    0xA1,
                    0x01,
                    0x85,
                    0x07,
                    0x09,
                    0xB5,
                    0x75,
                    0x01,
                    0x95,
                    0x01,
                    0x81,
                    0x02,
                    0xC0,
                ]
            ),
        )
        data = bytes([0x07, 0x01, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00])
        ids = report_ids(data, desc, None)
        self.assertEqual(ids[0], "favorites_1")
        self.assertTrue(any(item.startswith("045e:0745:") for item in ids[1:]))


class SkipInterfaceTests(unittest.TestCase):
    def test_keyboard_only_is_skipped(self) -> None:
        self.assertTrue(skip_interface(bytes([0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0xC0])))

    def test_empty_descriptor_is_not_skipped(self) -> None:
        self.assertFalse(skip_interface(b""))

    def test_mouse_and_consumer_is_not_skipped(self) -> None:
        raw = bytes(
            [
                0x05,
                0x01,
                0x09,
                0x02,
                0xA1,
                0x01,
                0xC0,
                0x05,
                0x0C,
                0x09,
                0x01,
                0xA1,
                0x01,
                0xC0,
            ]
        )
        self.assertFalse(skip_interface(raw))


if __name__ == "__main__":
    unittest.main()
