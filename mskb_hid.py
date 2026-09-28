# mskb_hid.py
#
# HID decode for Wireless Keyboard 2000 reports, a short-item report
# descriptor walk that names a key by the usage that changed, hidraw/evdev
# discovery, and the virtual uinput keyboard.
# hidraw_devices lists every node. The caller chooses vid:pid and leaves
# boot keyboard and mouse interfaces closed.
# The 2000 names stay first on 045e:0745 so bindings already learned
# from that decoder keep winning. Other products get generic usage ids
# only; decode_report is that keyboard's layout, not a shared HID dialect.
# Stays free of config.json and systemd so probe/status can import it
# without pulling GUI or install paths.
#
# Used by: mskb_mapper.py, mskb.py (status/probe/learn)
# See also: udev/99-mskb.rules

from __future__ import annotations

import fcntl
import os
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

VID = 0x045E
PID = 0x0745
UINPUT_PATH = "/dev/uinput"

# Report 0x21 bit 0xfa1b stays high while the keyboard is awake. Report 7
# leaves vendor bit fe03 set after My Favorites release. Neither is a key.

# Linux evdev key codes we emit. Names match X11/GNOME shortcut recording.
KEY_CODES = {
    "F13": 183,
    "F14": 184,
    "F15": 185,
    "F16": 186,
    "F17": 187,
    "F18": 188,
    "F19": 189,
    "F20": 190,
    "F21": 191,
    "F22": 192,
    "F23": 193,
    "F24": 194,
    "PROG1": 148,
    "PROG2": 149,
    "PROG3": 202,
    "PROG4": 203,
    "CALC": 140,
    "MAIL": 155,
    "HOMEPAGE": 172,
    "WWW": 150,
    "MUTE": 113,
    "VOLUMEDOWN": 114,
    "VOLUMEUP": 115,
    "NEXTSONG": 163,
    "PREVIOUSSONG": 165,
    "PLAYPAUSE": 164,
    "STOPCD": 166,
    "ZOOMIN": 418,
    "ZOOMOUT": 419,
    "ZOOMRESET": 420,
    "CHAT": 216,
    "PHONE": 169,
    "MESSENGER": 430,
    "BOOKMARKS": 156,
    "REFRESH": 173,
    "FORWARD": 159,
    "BACK": 158,
    "SEARCH": 217,
    "COMPUTER": 157,
    "FAVORITES": 364,
}

# HID Consumer Page (0x0C) usages this keyboard is known to send.
CONSUMER = {
    0x00B5: "NEXTSONG",
    0x00B6: "PREVIOUSSONG",
    0x00B7: "STOPCD",
    0x00CD: "PLAYPAUSE",
    0x00E2: "MUTE",
    0x00E9: "VOLUMEUP",
    0x00EA: "VOLUMEDOWN",
    0x0182: "FAVORITES",  # My Favorites (star) on Wireless Keyboard 2000
    0x018A: "MAIL",
    0x0192: "CALC",
    0x0194: "COMPUTER",
    0x0196: "WWW",
    0x01A2: "BOOKMARKS",
    0x01AE: "MESSENGER",
    0x0221: "SEARCH",
    0x0223: "HOMEPAGE",
    0x0227: "REFRESH",
    0x022A: "BOOKMARKS",
    0x022D: "ZOOMRESET",
    0x022E: "ZOOMIN",
    0x022F: "ZOOMOUT",
    0x029D: "MS_OFFICE_HOME",
    0x029E: "MS_TASK_PANE",
}

# hid-microsoft maps 0xff05 bit values to F14–F18 (My Favorites 1–5).
FF05_TO_FAVORITE = {0x01: 1, 0x02: 2, 0x04: 3, 0x08: 4, 0x10: 5}
FAVORITE_TO_KEY = {1: "F14", 2: "F15", 3: "F16", 4: "F17", 5: "F18"}

EV_SYN = 0
EV_KEY = 1
SYN_REPORT = 0
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_DEV_SETUP = 0x405C5503
BUS_USB = 0x03


@dataclass(frozen=True)
class HidrawDevice:
    """One hidraw node, including the bytes needed to decide if it is a key.

    Defaults keep older callers that only knew path, iface, and phys working.
    `descriptor` rides along so status and open do not walk sysfs a second time.
    @tags: #model/hid
    """

    path: str
    iface: str
    phys: str
    vid: str = ""
    pid: str = ""
    name: str = ""
    descriptor: bytes = b""


@dataclass
class ParsedReport:
    report_id: int
    raw: bytes
    consumer: int = 0
    keyboard: int = 0
    ff05: int = 0
    fe03: int = 0
    fe04: int = 0
    vendor_fd: int = 0
    fa_bits: int = 0
    ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HidDescriptor:
    """Report descriptor bound to one device so a learned id can name both.

    Frozen so a captured descriptor cannot change while reports are matched
    against it. `device` is the lowercase vid:pid (`045e:0745`).
    @tags: #model/hid
    """

    device: str
    raw: bytes


def load_key_table() -> dict[str, int]:
    """Merge compiled KEY_* names from the kernel headers when available."""
    codes = dict(KEY_CODES)
    header = Path("/usr/include/linux/input-event-codes.h")
    if not header.exists():
        return codes
    for line in header.read_text(errors="ignore").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "#define" and parts[1].startswith("KEY_"):
            name = parts[1][4:]
            try:
                codes.setdefault(name, int(parts[2], 0))
            except ValueError:
                continue
    return codes


def _vid_pid_from_hid_id(hid_id: str) -> tuple[str, str]:
    """VID and PID as four lowercase hex digits.

    Kernel HID_ID is bus plus 32-bit fields (`0003:0000045E:00000745`). Config
    stores `vid:pid` in four digits, so the match does not depend on the
    leading zeros in uevent.
    @tags: #action/parse #model/hid
    """
    parts = hid_id.split(":")
    if len(parts) < 3:
        return "", ""
    try:
        vid = int(parts[-2], 16)
        pid = int(parts[-1], 16)
    except ValueError:
        return "", ""
    return f"{vid:04x}", f"{pid:04x}"


def hidraw_devices() -> list[HidrawDevice]:
    """List every hidraw node, not only the Wireless Keyboard 2000.

    Status has to show receivers this process will not open. Filtering the
    product id here made a second keyboard look unplugged. The caller selects
    vid:pid. A missing report descriptor stays empty so skip_interface can
    keep the node rather than pretend it was a boot keyboard.
    @tags: #model/hid
    """
    found: list[HidrawDevice] = []
    base = Path("/sys/class/hidraw")
    if not base.exists():
        return found
    for node in sorted(base.iterdir()):
        vid = ""
        pid = ""
        name = ""
        phys = ""
        uevent_path = node / "device" / "uevent"
        if uevent_path.exists():
            uevent = uevent_path.read_text(errors="ignore")
            for line in uevent.splitlines():
                if line.startswith("HID_ID="):
                    vid, pid = _vid_pid_from_hid_id(line.split("=", 1)[1])
                elif line.startswith("HID_NAME="):
                    name = line.split("=", 1)[1]
                elif line.startswith("HID_PHYS="):
                    phys = line.split("=", 1)[1]
        iface = phys.rsplit("/", 1)[-1] if phys else "?"
        descriptor_path = node / "device" / "report_descriptor"
        try:
            descriptor = descriptor_path.read_bytes()
        except OSError:
            descriptor = b""
        found.append(
            HidrawDevice(
                path=f"/dev/{node.name}",
                iface=iface,
                phys=phys,
                vid=vid,
                pid=pid,
                name=name,
                descriptor=descriptor,
            )
        )
    return found


def evdev_devices() -> list[Path]:
    """Return /dev/input/event* nodes that belong to the transceiver."""
    devices: list[Path] = []
    sys_input = Path("/sys/class/input")
    if not sys_input.exists():
        return devices
    for event in sorted(sys_input.glob("event*")):
        uevent = event / "device" / "uevent"
        if not uevent.exists():
            continue
        text = uevent.read_text(errors="ignore")
        if "045E" in text.upper() and "0745" in text:
            devices.append(Path("/dev/input") / event.name)
    return devices


def decode_report(data: bytes) -> ParsedReport | None:
    """Decode extra-key reports. Mouse motion reports are ignored."""
    if not data:
        return None
    rid = data[0]
    parsed = ParsedReport(report_id=rid, raw=data)

    # Interface 2, report 7: consumer + keyboard + My Favorites bitfield.
    if rid == 0x07 and len(data) >= 8:
        parsed.consumer = data[1] | (data[2] << 8)
        parsed.keyboard = data[3]
        packed = data[5]
        parsed.fe03 = packed & 0x01
        parsed.fe04 = (packed >> 1) & 0x01
        parsed.ff05 = (packed >> 2) & 0x1F
        parsed.vendor_fd = data[6]
    # Interface 1, report 0x16: consumer + vendor fd usage.
    elif rid == 0x16 and len(data) >= 4:
        parsed.consumer = data[1] | (data[2] << 8)
        parsed.vendor_fd = data[3]
    # Interface 2, report 0x21: 16 vendor bits (fa10–fa1f).
    elif rid == 0x21 and len(data) >= 3:
        parsed.fa_bits = data[1] | (data[2] << 8)
    else:
        return None

    parsed.ids = _ids_for(parsed)
    if rid in (0x07, 0x16, 0x21):
        return parsed
    return parsed if parsed.ids else None


def _ids_for(parsed: ParsedReport) -> list[str]:
    # fe03/fe04 and report 0x21 are status, not keys, on 045e:0745.
    ids: list[str] = []
    if parsed.ff05:
        fav = FF05_TO_FAVORITE.get(parsed.ff05)
        if fav:
            ids.append(f"favorites_{fav}")
        ids.append(f"ff05_0x{parsed.ff05:02x}")
    if parsed.consumer:
        ids.append(f"consumer_0x{parsed.consumer:04x}")
        name = CONSUMER.get(parsed.consumer)
        if name:
            ids.append(name.lower())
            if name in ("BOOKMARKS", "FAVORITES"):
                ids.append("favorites_star")
    if parsed.keyboard:
        ids.append(f"kbd_0x{parsed.keyboard:02x}")
    if parsed.vendor_fd:
        ids.append(f"fd_0x{parsed.vendor_fd:02x}")
        if parsed.vendor_fd == 0x06:
            ids.append("chat")
        if parsed.vendor_fd == 0x07:
            ids.append("phone")
    return ids


def primary_id(parsed: ParsedReport) -> str | None:
    for item in parsed.ids:
        if item.startswith("favorites_"):
            return item
    return parsed.ids[0] if parsed.ids else None


def format_report(parsed: ParsedReport) -> str:
    parts = [f"id=0x{parsed.report_id:02x}", parsed.raw.hex(" ")]
    if parsed.consumer:
        name = CONSUMER.get(parsed.consumer, "consumer")
        parts.append(f"consumer=0x{parsed.consumer:04x}({name})")
    if parsed.keyboard:
        parts.append(f"kbd=0x{parsed.keyboard:02x}")
    if parsed.ff05:
        fav = FF05_TO_FAVORITE.get(parsed.ff05)
        label = f"favorites_{fav}" if fav else "unknown"
        parts.append(f"ff05=0x{parsed.ff05:02x}({label})")
    if parsed.vendor_fd:
        parts.append(f"fd=0x{parsed.vendor_fd:02x}")
    if parsed.fe03 or parsed.fe04:
        parts.append(f"fe03={parsed.fe03} fe04={parsed.fe04}")
    if parsed.fa_bits:
        parts.append(f"fa_bits=0x{parsed.fa_bits:04x}")
    if parsed.ids:
        parts.append("ids=" + ",".join(parsed.ids))
    return " ".join(parts)


# Short-item prefix: size in bits 0-1 (3 means 4 data bytes), type in bits 2-3,
# tag in bits 4-7. 0xFE is the long-item prefix, not a short item with tag 15.
_ITEM_LONG = 0xFE
_TYPE_MAIN = 0
_TYPE_GLOBAL = 1
_TYPE_LOCAL = 2
_MAIN_INPUT = 8
_MAIN_COLLECTION = 0xA
_GLOBAL_USAGE_PAGE = 0
_GLOBAL_REPORT_SIZE = 7
_GLOBAL_REPORT_ID = 8
_GLOBAL_REPORT_COUNT = 9
_LOCAL_USAGE = 0
_LOCAL_USAGE_MIN = 1
_LOCAL_USAGE_MAX = 2
_COLLECTION_APPLICATION = 0x01
_INPUT_CONSTANT = 0x01
_PAGE_DESKTOP = 0x01
_USAGE_MOUSE = 0x02
_USAGE_KEYBOARD = 0x06


@dataclass(frozen=True)
class _InputField:
    """One non-constant control, already placed on its report's bit cursor.

    Constant inputs are left out: they still move the cursor during the walk,
    but they are not keys the caller can bind.
    """

    report_id: int
    bit_offset: int
    size: int
    page: int
    usage: int


@dataclass(frozen=True)
class _DescriptorWalk:
    """Fields and application-collection usages from one short-item walk."""

    fields: tuple[_InputField, ...]
    applications: tuple[tuple[int, int], ...]
    has_report_id: bool


def _usages_for_count(
    usages: list[int],
    usage_min: int | None,
    usage_max: int | None,
    count: int,
) -> list[int]:
    """Fill report slots from explicit usages, then an inclusive min/max range.

    HID repeats the last declared usage when the report has more slots than
    usages. The range is appended after individual Usage items so a min/max
    pair fills leftover slots instead of the usage that named the collection.
    """
    if count <= 0:
        return []
    queued = list(usages)
    if usage_min is not None and usage_max is not None and usage_max >= usage_min:
        remaining = count - len(queued)
        if remaining > 0:
            span = usage_max - usage_min + 1
            take = min(span, remaining)
            queued.extend(range(usage_min, usage_min + take))
    if not queued:
        return []
    if len(queued) < count:
        queued.extend([queued[-1]] * (count - len(queued)))
    return queued[:count]


def _declared_usage(usages: list[int], usage_min: int | None) -> int:
    """Usage attached to a collection: the one declared just before it."""
    if usages:
        return usages[-1]
    if usage_min is not None:
        return usage_min
    return 0


def _parse_descriptor(raw: bytes) -> _DescriptorWalk | None:
    """Walk short items into input fields and application-collection usages.

    Long items and unknown tags are skipped so a vendor descriptor can still
    yield the fields we understand. A truncated item returns None so callers
    fall back to the 2000 decoder instead of inventing usages from a partial
    walk. Locals clear on every Main item; Output and Feature do not move
    the input bit cursor, or the next Input would line up with the wrong bits.
    @tags: #action/parse #model/hid
    """
    usage_page = 0
    report_size = 0
    report_count = 0
    report_id = 0
    has_report_id = False
    usages: list[int] = []
    usage_min: int | None = None
    usage_max: int | None = None
    fields: list[_InputField] = []
    applications: list[tuple[int, int]] = []
    cursors: dict[int, int] = {}
    index = 0
    length = len(raw)

    while index < length:
        prefix = raw[index]
        if prefix == _ITEM_LONG:
            if index + 2 >= length:
                return None
            data_size = raw[index + 1]
            end = index + 3 + data_size
            if end > length:
                return None
            index = end
            continue
        size_code = prefix & 0x03
        size = 4 if size_code == 3 else size_code
        if index + 1 + size > length:
            return None
        data = raw[index + 1 : index + 1 + size]
        value = int.from_bytes(data, "little") if data else 0
        item_type = (prefix >> 2) & 0x03
        tag = (prefix >> 4) & 0x0F
        index += 1 + size

        if item_type == _TYPE_GLOBAL:
            if tag == _GLOBAL_USAGE_PAGE:
                usage_page = value
            elif tag == _GLOBAL_REPORT_SIZE:
                report_size = value
            elif tag == _GLOBAL_REPORT_ID:
                report_id = value
                has_report_id = True
            elif tag == _GLOBAL_REPORT_COUNT:
                report_count = value
            continue

        if item_type == _TYPE_LOCAL:
            if tag == _LOCAL_USAGE:
                usages.append(value)
            elif tag == _LOCAL_USAGE_MIN:
                usage_min = value
            elif tag == _LOCAL_USAGE_MAX:
                usage_max = value
            continue

        if item_type != _TYPE_MAIN:
            continue

        if tag == _MAIN_COLLECTION and value == _COLLECTION_APPLICATION:
            applications.append((usage_page, _declared_usage(usages, usage_min)))
        elif tag == _MAIN_INPUT:
            offset = cursors.get(report_id, 0)
            # Constant bit occupies space in the report but is not a key.
            assigned = (
                []
                if value & _INPUT_CONSTANT or report_size <= 0
                else _usages_for_count(usages, usage_min, usage_max, report_count)
            )
            for slot, usage in enumerate(assigned):
                fields.append(
                    _InputField(
                        report_id=report_id,
                        bit_offset=offset + slot * report_size,
                        size=report_size,
                        page=usage_page,
                        usage=usage,
                    )
                )
            cursors[report_id] = offset + report_size * report_count

        usages = []
        usage_min = None
        usage_max = None

    return _DescriptorWalk(
        fields=tuple(fields),
        applications=tuple(applications),
        has_report_id=has_report_id,
    )


def _read_bits(buf: bytes, bit_offset: int, size: int) -> int:
    """Read one field packed LSB-first, treating bytes past the buffer as 0.

    A shorter previous report is zero-padded so bits the caller never sent
    compare as released, not held.
    """
    value = 0
    for bit in range(size):
        absolute = bit_offset + bit
        byte_index = absolute // 8
        if byte_index < len(buf) and (buf[byte_index] >> (absolute % 8)) & 1:
            value |= 1 << bit
    return value


def _pressed_ids(
    data: bytes,
    device: str,
    walk: _DescriptorWalk,
    previous: bytes | None,
) -> list[str]:
    """Ids for controls that changed against the caller's baseline."""
    if walk.has_report_id:
        # Byte 0 selects the report; fields live in the bytes after it.
        if not data:
            return []
        report_id = data[0]
        payload = data[1:]
        previous_payload = b"" if previous is None else previous[1:]
    else:
        report_id = None
        payload = data
        previous_payload = b"" if previous is None else previous

    device_id = device.lower()
    found: list[str] = []
    for field in walk.fields:
        if walk.has_report_id and field.report_id != report_id:
            continue
        current = _read_bits(payload, field.bit_offset, field.size)
        prior = _read_bits(previous_payload, field.bit_offset, field.size)
        if field.size == 1:
            if current == 1 and prior == 0:
                found.append(f"{device_id}:{field.page:04x}:{field.usage:04x}")
        elif current != 0 and current != prior:
            found.append(f"{device_id}:{field.page:04x}:{field.usage:04x}:{current:x}")
    return found


# ponytail: one id list per report (no chords as a single binding), and a descriptor this walk cannot read falls back to decode_report only on the 2000.
def report_ids(data: bytes, descriptor: HidDescriptor, previous: bytes | None) -> list[str]:
    """Name keys that changed against the caller's baseline, 2000 ids first.

    `previous` is the baseline the caller already handled, not a frame this
    function remembers. A 1-bit that was already 1 there stays out, so a
    sticky vendor bit is not learned as a new key. decode_report is the
    Wireless Keyboard 2000 layout; another vid:pid must not inherit
    favorites_* names from a coincidental report 0x07.
    @tags: #action/parse #model/hid
    """
    walk = _parse_descriptor(descriptor.raw)
    generic = [] if walk is None else _pressed_ids(data, descriptor.device, walk, previous)
    if descriptor.device != "045e:0745":
        return generic
    parsed = decode_report(data)
    if parsed is None:
        return generic
    result = list(parsed.ids)
    seen = set(result)
    for item in generic:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def track_report(baseline: bytes | None, data: bytes, descriptor: HidDescriptor) -> tuple[bytes, list[str]]:
    """Hold the baseline still while a key in this report stays down.

    The first report is the idle snapshot, including sticky bits that are
    already high. A key held at process start is missed until release and
    press — a latch that was up before we listened must not be learned as a
    press. An empty id list advances the baseline so a newly latched sticky
    bit is not named on the next report. A non-empty list keeps the previous
    baseline, so a held key stays newly pressed and the mapper does not
    release it when the next report is identical.
    @tags: #action/parse #model/hid
    """
    if baseline is None:
        return data, []
    ids = report_ids(data, descriptor, baseline)
    if not ids:
        return data, []
    return baseline, ids


def skip_interface(descriptor: bytes) -> bool:
    """True when every application collection is boot keyboard or mouse.

    Those interfaces carry ordinary key and pointer traffic, not the extra
    keys learn should bind. An empty or unreadable descriptor returns False
    so a consumer collection hiding in bytes we could not classify is kept.
    @tags: #action/parse #model/hid
    """
    walk = _parse_descriptor(descriptor)
    if walk is None or not walk.applications:
        return False
    return all(
        page == _PAGE_DESKTOP and usage in (_USAGE_KEYBOARD, _USAGE_MOUSE)
        for page, usage in walk.applications
    )


class UInputKeyboard:
    """Minimal virtual keyboard. Wide KEY range so config can pick any code."""

    def __init__(self, name: str = "MS Keyboard 2000 extra keys") -> None:
        self.fd = os.open(UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for code in range(1, 768):
            try:
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            except OSError:
                continue
        setup = struct.pack(
            "HHHH80sI",
            BUS_USB,
            VID,
            PID,
            1,
            name.encode("utf-8"),
            0,
        )
        # struct uinput_setup may be 92 bytes (id + name[80] + ff_effects_max).
        # Fall back to the older write(uinput_user_dev) path if SETUP fails.
        try:
            fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
        except OSError:
            user_dev = struct.pack(
                "80sHHHHi" + "I" * 64 * 4,
                name.encode("utf-8"),
                BUS_USB,
                VID,
                PID,
                1,
                0,
                *([0] * 256),
            )
            os.write(self.fd, user_dev)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(0.2)

    def emit(self, keycode: int, pressed: bool) -> None:
        event = struct.pack("llHHi", 0, 0, EV_KEY, keycode, 1 if pressed else 0)
        syn = struct.pack("llHHi", 0, 0, EV_SYN, SYN_REPORT, 0)
        os.write(self.fd, event)
        os.write(self.fd, syn)

    def close(self) -> None:
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)


def open_hidraw(devices: list[str] | None = None) -> list[tuple[int, HidrawDevice]]:
    """Open selected extra-key hidraw nodes read-only and nonblocking.

    `devices` is lowercase vid:pid. None keeps every node so a caller that
    has not chosen yet still hears whatever is plugged in. Boot keyboard and
    mouse collections stay closed: ordinary typing must not enter the mapper.
    Nothing opened is the same install hint as before, without pinning one
    product id into the error.
    @tags: #model/hid
    """
    opened: list[tuple[int, HidrawDevice]] = []
    errors: list[str] = []
    for dev in hidraw_devices():
        if devices is not None and f"{dev.vid}:{dev.pid}".lower() not in devices:
            continue
        # skip_interface is True for a whole-interface boot keyboard or mouse.
        if skip_interface(dev.descriptor):
            continue
        try:
            fd = os.open(dev.path, os.O_RDONLY | os.O_NONBLOCK)
            opened.append((fd, dev))
        except OSError as exc:
            errors.append(f"{dev.path}: {exc}")
    if not opened:
        if devices is None:
            scope = "extra-key interfaces"
        else:
            scope = "extra-key interfaces for " + (", ".join(devices) or "the requested devices")
        msg = f"No hidraw access for {scope}. Run `sudo python3 mskb.py install`, then replug the dongle."
        if errors:
            msg += "\n" + "\n".join(errors)
        raise SystemExit(msg)
    return opened
