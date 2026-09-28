# mskb_mapper.py
#
# Runtime loop for `mskb.py run`: read hidraw, emit uinput keys, run exec.
# Each interface and report id keeps its own baseline and held keys, so
# one report going idle does not lift a key another report is still sending.
# Dual-fire when both key and exec are set stays here; the GUI never
# writes that shape.
#
# Used by: mskb.py (cmd_run)
# See also: mskb_hid.py, mskb_bindings.py

from __future__ import annotations

import os
import select
import subprocess
from typing import Iterable

from mskb_bindings import devices_from_config, ensure_config, load_config
from mskb_hid import (
    FAVORITE_TO_KEY,
    HidDescriptor,
    UInputKeyboard,
    load_key_table,
    open_hidraw,
    track_report,
)
from mskb_paths import config_path


def _run_exec(command: str) -> None:
    if not command.strip():
        return
    subprocess.Popen(
        command,
        shell=True,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _binding_for(ids: Iterable[str], bindings: dict) -> tuple[str, dict] | None:
    for key_id in ids:
        if key_id in bindings:
            return key_id, bindings[key_id]
    return None


def cmd_run(_: object) -> int:
    path = ensure_config()
    config = load_config(path)
    bindings = config.get("bindings", {})
    key_table = load_key_table()
    opened = open_hidraw(devices_from_config(config))
    uinput = UInputKeyboard()
    print(f"Mapper running. Config: {path}")

    baselines: dict[tuple[str, int], bytes] = {}
    active: dict[tuple[str, int], dict[str, str]] = {}
    seen_unbound: set[str] = set()
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
                # Report id is part of the key so one interface can hold
                # several reports without one idle frame lifting the others.
                source = (dev.iface, data[0] if data else 0)
                descriptor = HidDescriptor(device=f"{dev.vid}:{dev.pid}", raw=dev.descriptor)
                baseline, ids = track_report(baselines.get(source), data, descriptor)
                baselines[source] = baseline
                # 0x21 is the Wireless Keyboard 2000 awake bitmap, not a key.
                if source[1] == 0x21:
                    continue
                _handle_report(source, ids, bindings, key_table, uinput, active, seen_unbound)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        for source in list(active):
            _release_source(uinput, key_table, active, source)
        uinput.close()
        for fd, _ in opened:
            os.close(fd)
    return 0


def _release_source(
    uinput: UInputKeyboard,
    key_table: dict[str, int],
    active: dict[tuple[str, int], dict[str, str]],
    source: tuple[str, int],
    keep: str | None = None,
) -> None:
    """Key-up and drop bindings held by one interface and report id.

    Other sources stay down. A new press passes `keep` so the binding that
    is still the match is not released and then fired again.
    @tags: #action/parse #model/hid
    """
    bucket = active.get(source)
    if not bucket:
        return
    for name, key in list(bucket.items()):
        if keep is not None and name == keep:
            continue
        code = key_table.get(key.upper()) if key else None
        if code:
            uinput.emit(code, False)
        bucket.pop(name, None)
    if not bucket:
        active.pop(source, None)


def _default_favorite(ids: Iterable[str]) -> tuple[str, dict] | None:
    """Unconfigured My Favorites still emit F14–F18.

    report_ids puts favorites_N first, ahead of the generic usage id, so a
    saved binding under that name wins in _binding_for. This only covers the
    case where the report named a favorite and config has no entry yet.
    @tags: #action/parse #model/hid
    """
    for key_id in ids:
        if not key_id.startswith("favorites_"):
            continue
        suffix = key_id[len("favorites_") :]
        if not suffix.isdigit():
            continue
        fav = int(suffix)
        key = FAVORITE_TO_KEY.get(fav)
        if key:
            return key_id, {"key": key, "exec": ""}
    return None


def _handle_report(
    source: tuple[str, int],
    ids: list[str],
    bindings: dict,
    key_table: dict[str, int],
    uinput: UInputKeyboard,
    active: dict[tuple[str, int], dict[str, str]],
    seen_unbound: set[str],
) -> None:
    """Bind or release the ids already named for one source.

    An empty list means this report went idle, so only `active[source]` goes
    up. A name that stays in the source's map does not fire exec or uinput
    again. A different name on the same source releases the previous one.
    @tags: #action/parse #model/hid
    """
    if not ids:
        _release_source(uinput, key_table, active, source)
        return

    matched = _binding_for(ids, bindings)
    if not matched:
        matched = _default_favorite(ids)
    if not matched:
        token = ",".join(ids)
        if token not in seen_unbound:
            seen_unbound.add(token)
            print(f"unbound: {token}  (add this id to {config_path()})")
        return

    name, binding = matched
    _release_source(uinput, key_table, active, source, keep=name)
    key_name = (binding.get("key") or "").strip().upper()
    command = binding.get("exec") or ""
    bucket = active.setdefault(source, {})

    if name in bucket:
        return
    if command:
        _run_exec(command)
    if key_name:
        code = key_table.get(key_name)
        if code is None:
            print(f"unknown key name {key_name!r} for {name}")
            return
        uinput.emit(code, True)
        bucket[name] = key_name
        return
    if command:
        bucket[name] = ""
        return
    print(f"{name} has empty key and exec")
