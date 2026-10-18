"""MCP server that controls Propellerhead/Reason Studios Reason 13 through Remote.

Reason sees this server as two control surfaces ("Claude MCP" and "Claude MCP Mixer")
connected through loopMIDI ports. Everything goes through the official Remote API, so it is
stable across Reason updates and needs no patching of Reason itself.
"""

import json
import os
import re
import time
from importlib import resources

import mido
from mcp.server.fastmcp import FastMCP

from . import protocol as P
from .link import SurfaceLink

mcp = FastMCP(
    "reason",
    instructions=(
        "Controls Reason 13 via its Remote control-surface API. The 'main' surface follows the "
        "selected track/device (transport + that device's parameters); the 'mixer' surface is locked "
        "to the Master Section (main mixer). Call reason_status first. list_controls shows the "
        "parameters available right now; names are Reason remotable item names."
    ),
)

MAIN = SurfaceLink(
    "main",
    os.environ.get("REASON_MCP_OUT", "Claude to Reason"),
    os.environ.get("REASON_MCP_IN", "Reason to Claude"),
)
MIXER = SurfaceLink(
    "mixer",
    os.environ.get("REASON_MCP_MIXER_OUT", "Claude to Reason Mixer"),
    os.environ.get("REASON_MCP_MIXER_IN", "Reason to Claude Mixer"),
)
SURFACES = {"main": MAIN, "mixer": MIXER}

CATALOG = json.loads(resources.files(__package__).joinpath("catalog.json").read_text(encoding="utf-8"))
SCOPE_SLOT_MAIN = f"T{CATALOG['document_text_slot_scope']}"
SCOPE_SLOT_MIXER = "T1"
KIND_NAMES = {v: k for k, v in P.KIND_BY_NAME.items()}


def _scope_name(shown):
    """Full scope name from the (possibly truncated) literal the codec reports."""
    if not shown:
        return None
    return next((s for s in CATALOG["scopes"] if s == shown), None) or next(
        (s for s in CATALOG["scopes"] if s.startswith(shown)), shown
    )


def _surface(name):
    link = SURFACES.get(name)
    if link is None:
        raise ValueError(f"surface must be one of {list(SURFACES)}")
    link.connect()
    if not link.items:
        link.request_dump()
    return link


def _describe(s):
    d = {"name": s.name, "type": KIND_NAMES[s.kind], "slot": s.key, "text": s.text}
    if s.kind in (P.KIND_VALUE, P.KIND_DELTA):
        d["value"] = round(s.value / P.VALUE_MAX, 4)
    elif s.kind == P.KIND_BUTTON:
        d["value"] = s.value
    return d


def _require(link, name):
    item = link.find(name)
    if item is None:
        raise ValueError(
            f"'{name}' is not available on the {link.label} surface right now. "
            "Use list_controls to see the current names (the main surface follows the selected device)."
        )
    return item


def _press(link, item):
    if item.kind == P.KIND_BUTTON:
        link.send_input(P.KIND_BUTTON, item.slot, 1)
        time.sleep(0.03)
        link.send_input(P.KIND_BUTTON, item.slot, 0)
    elif item.kind == P.KIND_VALUE:
        # Value-type toggles (e.g. Click On/Off) – flip between min and max.
        link.send_input(P.KIND_VALUE, item.slot, 0 if item.value > P.VALUE_MAX // 2 else P.VALUE_MAX)
    else:
        raise ValueError(f"'{item.name}' is a {KIND_NAMES[item.kind]} item and cannot be pressed")


def _settled(link, key, since):
    link.wait_item(key, since, timeout=0.8)
    # Reports come in bursts; let the burst finish so the newest value wins.
    link.wait_quiet(quiet=0.12, timeout=0.8)
    return link.get(key)


def _number(text):
    """Displayed number in base units: kHz -> Hz, s -> ms ('1.2 kHz' -> 1200, '1.5 s' -> 1500)."""
    m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*([a-zA-Z%]*)", text or "")
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2)
    if unit.startswith("k") and len(unit) > 1:
        value *= 1000
    elif unit == "s":
        value *= 1000
    return value


def _seek_number(link, item, target, tolerance=None, iterations=10):
    """Find the absolute 0..16383 value whose displayed number is closest to target.

    Remote does not expose an item's real range, so the displayed text is the only
    authoritative reading. Secant steps on (raw, shown) pairs; assumes monotonic display.
    """

    def probe(raw):
        raw = max(0, min(P.VALUE_MAX, int(round(raw))))
        t0 = time.monotonic()
        link.send_input(P.KIND_VALUE, item.slot, raw)
        state = _settled(link, item.key, t0) or item
        shown = _number(state.text)
        if shown is None:
            raise ValueError(f"'{item.name}' shows no number ({state.text!r}); use set_control with a 0..1 value")
        return raw, shown, state.text

    tol = 0.5 if tolerance is None else tolerance
    a, b = probe(4096), probe(12288)
    best = min((a, b), key=lambda p: abs(p[1] - target))
    for _ in range(iterations):
        if abs(best[1] - target) <= tol:
            break
        (r1, s1, _), (r2, s2, _) = a, b
        if s2 == s1:
            nxt = r2 + (P.VALUE_MAX // 8 if target > s2 else -P.VALUE_MAX // 8)
        else:
            nxt = r2 + (target - s2) * (r2 - r1) / (s2 - s1)
        nxt = max(0, min(P.VALUE_MAX, round(nxt)))
        if nxt == b[0]:
            nxt += 1 if target > b[1] else -1
        a, b = b, probe(nxt)
        if abs(b[1] - target) < abs(best[1] - target):
            best = b
    if best[0] != b[0]:
        best = probe(best[0])
    return best[2]


def _nudge(link, item, steps):
    remaining = int(steps)
    while remaining:
        chunk = max(-P.DELTA_LIMIT, min(P.DELTA_LIMIT, remaining))
        link.send_input(P.KIND_DELTA, item.slot, chunk)
        remaining -= chunk
        time.sleep(0.01)


# --------------------------------------------------------------------------------------------
# Status / discovery
# --------------------------------------------------------------------------------------------


@mcp.tool()
def reason_status() -> dict:
    """Connection state, current track/device, tempo, position and transport toggles."""
    out = {}
    for label, link in SURFACES.items():
        try:
            link.connect()
            if not link.items:
                link.request_dump()
            out[label] = {"connected": True, "codec_answered": link.pong is not None, "items_reported": len(link.items)}
        except ConnectionError as e:
            out[label] = {"connected": False, "error": str(e)}
    if out["main"].get("connected"):
        scope = MAIN.get(SCOPE_SLOT_MAIN)
        out["device_scope"] = _scope_name(scope.text) if scope and scope.enabled else None
        for name in ("Document Name", "Target Track Name", "Tempo BPM", "Tempo Decimal", "Bar Position",
                     "Beat Position", "Play", "Record", "Loop On/Off", "Click On/Off", "Reason Version"):
            item = MAIN.find(name)
            if item is not None:
                out[name] = item.text if item.text else item.value
    if out["mixer"].get("connected"):
        scope = MIXER.get(SCOPE_SLOT_MIXER)
        out["mixer_locked_to_master_section"] = bool(scope and scope.enabled and scope.text)
    return out


@mcp.tool()
def list_controls(surface: str = "main", contains: str = "", include_disabled: bool = False, refresh: bool = False) -> list:
    """List the controls Reason currently exposes on a surface.

    surface: 'main' (transport + selected device) or 'mixer' (Master Section).
    contains: optional case-insensitive filter on the name.
    refresh: ask Reason to resend every value first.
    """
    link = _surface(surface)
    if refresh:
        link.request_dump()
    f = contains.lower()
    items = [s for s in link.snapshot().values() if (s.enabled or include_disabled) and s.name and f in s.name.lower()]
    items.sort(key=lambda s: (s.kind, s.slot))
    return [_describe(s) for s in items]


@mcp.tool()
def get_control(name: str, surface: str = "main") -> dict:
    """Read one control by its Reason remotable name (e.g. 'Filter Freq', 'Tempo BPM')."""
    return _describe(_require(_surface(surface), name))


@mcp.tool()
def set_control(name: str, value: float | None = None, number: float | None = None, surface: str = "main") -> dict:
    """Set a value control.

    value: normalized 0..1 position of the control.
    number: target as shown by Reason, in base units: Hz (not kHz), ms (not s), dB, %, BPM.
            Found by closed-loop search against Reason's displayed text.
    """
    link = _surface(surface)
    item = _require(link, name)
    if item.kind != P.KIND_VALUE:
        raise ValueError(f"'{name}' is a {KIND_NAMES[item.kind]} item; use press_control or nudge_control")
    if number is not None:
        _seek_number(link, item, number)
    elif value is not None:
        t0 = time.monotonic()
        link.send_input(P.KIND_VALUE, item.slot, round(max(0.0, min(1.0, value)) * P.VALUE_MAX))
        _settled(link, item.key, t0)
    else:
        raise ValueError("give value (0..1) or number")
    return _describe(link.get(item.key))


@mcp.tool()
def press_control(name: str, surface: str = "main") -> dict:
    """Press a button control (Play, Mute, Select Next Patch, ...). Value toggles are flipped."""
    link = _surface(surface)
    item = _require(link, name)
    t0 = time.monotonic()
    _press(link, item)
    _settled(link, item.key, t0)
    return _describe(link.get(item.key))


@mcp.tool()
def nudge_control(name: str, steps: int, surface: str = "main") -> dict:
    """Move a delta control (jog-style) by a number of steps, e.g. 'Bar Position', 'Target Track (Delta)'."""
    link = _surface(surface)
    item = _require(link, name)
    if item.kind != P.KIND_DELTA:
        raise ValueError(f"'{name}' is a {KIND_NAMES[item.kind]} item, not a delta")
    t0 = time.monotonic()
    _nudge(link, item, steps)
    _settled(link, item.key, t0)
    return _describe(link.get(item.key))


@mcp.tool()
def catalog(device: str = "", contains: str = "") -> dict:
    """Offline list of the parameters mapped for each Reason device (from the generated remote map).

    device: substring of a device scope name ('Europa', 'SubTractor', 'Master Section'...). Empty lists scopes.
    """
    scopes = CATALOG["scopes"]
    if not device:
        return {"scopes": sorted(scopes)}
    d, f = device.lower(), contains.lower()
    return {
        s: sorted(n for n in v["items"] if f in n.lower())
        for s, v in scopes.items()
        if d in s.lower()
    }


# --------------------------------------------------------------------------------------------
# Transport / song
# --------------------------------------------------------------------------------------------

TRANSPORT = {
    "play": "Play", "stop": "Stop", "record": "Record", "rewind": "Rewind", "fast_forward": "Fast Forward",
    "return_to_zero": "Return To Zero", "loop": "Loop On/Off", "click": "Click On/Off",
    "precount": "Precount On/Off", "undo": "Undo", "redo": "Redo", "tap_tempo": "Tap Tempo",
    "goto_left_locator": "Goto Left Locator", "goto_right_locator": "Goto Right Locator",
    "new_overdub": "New Overdub", "new_alt_take": "New Alternative Take", "auto_quantize": "Auto-quantize",
    "all_notes_off": "Send All Notes Off", "reset_automation_override": "Reset Automation Override",
}


@mcp.tool()
def transport(action: str) -> dict:
    """Transport/document command. action: play, stop, record, rewind, fast_forward, return_to_zero,
    loop, click, precount, undo, redo, tap_tempo, goto_left_locator, goto_right_locator, new_overdub,
    new_alt_take, auto_quantize, all_notes_off, reset_automation_override."""
    name = TRANSPORT.get(action)
    if name is None:
        raise ValueError(f"unknown action; choose from {sorted(TRANSPORT)}")
    if action in ("rewind", "fast_forward"):
        link = _surface("main")
        item = _require(link, name)
        link.send_input(item.kind, item.slot, P.VALUE_MAX if item.kind == P.KIND_VALUE else 1)
        time.sleep(0.5)
        link.send_input(item.kind, item.slot, 0)
        return _describe(item)
    return press_control(name)


@mcp.tool()
def set_tempo(bpm: float) -> dict:
    """Set the song tempo in BPM (integer part via 'Tempo BPM', decimals via 'Tempo Decimal')."""
    link = _surface("main")
    item = _require(link, "Tempo BPM")
    whole = int(bpm)
    _seek_number(link, item, whole, tolerance=0)
    frac = round((bpm - whole) * 1000)
    dec = link.find("Tempo Decimal")
    if dec is not None and dec.kind == P.KIND_DELTA:
        current = _number(dec.text) or 0
        if current != frac:
            t0 = time.monotonic()
            _nudge(link, dec, frac - int(current))
            _settled(link, dec.key, t0)
    return {"Tempo BPM": link.find("Tempo BPM").text, "Tempo Decimal": (link.find("Tempo Decimal") or dec).text}


def _goto_number(link, name, target, attempts=4):
    item = _require(link, name)
    for _ in range(attempts):
        current = _number(link.get(item.key).text)
        if current is None:
            raise ValueError(f"'{name}' shows no number")
        diff = int(round(target - current))
        if diff == 0:
            break
        t0 = time.monotonic()
        if item.kind == P.KIND_DELTA:
            _nudge(link, item, diff)
        else:
            raise ValueError(f"'{name}' is not a delta item")
        _settled(link, item.key, t0)
    return link.get(item.key).text


@mcp.tool()
def locate(bar: int, beat: int = 1) -> dict:
    """Move the song position to bar/beat (1-based)."""
    link = _surface("main")
    result = {"bar": _goto_number(link, "Bar Position", bar)}
    if link.find("Beat Position") is not None:
        result["beat"] = _goto_number(link, "Beat Position", beat)
    return result


@mcp.tool()
def set_loop(left_bar: int, right_bar: int, enabled: bool | None = None) -> dict:
    """Set loop locators (bars, 1-based) and optionally switch loop on/off."""
    if right_bar <= left_bar:
        raise ValueError("right_bar must be greater than left_bar")
    link = _surface("main")
    # Move the right locator first when extending, the left first when shrinking, so they never cross.
    order = [("Right Loop Bar", right_bar), ("Left Loop Bar", left_bar)]
    current_left = _number(_require(link, "Left Loop Bar").text) or 1
    if left_bar > current_left:
        order.reverse()
    result = {name: _goto_number(link, name, target) for name, target in order}
    if enabled is not None:
        loop = _require(link, "Loop On/Off")
        if bool(loop.value) != enabled:
            press_control("Loop On/Off")
        result["Loop On/Off"] = enabled
    return result


# --------------------------------------------------------------------------------------------
# Tracks / devices
# --------------------------------------------------------------------------------------------


@mcp.tool()
def select_track(direction: str = "", name: str = "", max_steps: int = 128) -> dict:
    """Select the target track: direction 'next'/'prev', or search by (partial) track name.
    The main surface then follows the selected track's device."""
    link = _surface("main")
    if direction:
        item = _require(link, "Target Next Track" if direction == "next" else "Target Previous Track")
        _press(link, item)
        time.sleep(0.15)
        link.request_dump()
        return {"track": (link.find("Target Track Name") or item).text, "device_scope": _scope_name(link.get(SCOPE_SLOT_MAIN).text)}
    if not name:
        raise ValueError("give direction or name")
    wanted = name.lower()
    track = _require(link, "Target Track Name")
    if wanted in track.text.lower():
        return {"track": track.text}
    # Walk to the top, then down through every track once.
    delta = link.find("Target Track (Delta)")
    seen = []
    for step in ("prev", "next"):
        for _ in range(max_steps):
            before = link.find("Target Track Name").text
            t0 = time.monotonic()
            if delta is not None and delta.kind == P.KIND_DELTA:
                _nudge(link, delta, -1 if step == "prev" else 1)
            else:
                _press(link, _require(link, "Target Previous Track" if step == "prev" else "Target Next Track"))
            current = _settled(link, track.key, t0)
            text = current.text if current else before
            if step == "next":
                seen.append(text)
                if wanted in text.lower():
                    time.sleep(0.1)
                    link.request_dump()
                    return {"track": text, "device_scope": _scope_name(link.get(SCOPE_SLOT_MAIN).text)}
            if text == before:
                break
    raise ValueError(f"no track matching '{name}'. Tracks seen: {seen}")


@mcp.tool()
def track(action: str) -> dict:
    """Act on the selected track: mute, solo, monitor, automation_record, tuner, next_patch, prev_patch."""
    names = {
        "mute": "Target Track Mute", "solo": "Target Track Solo", "monitor": "Target Track Enable Monitoring",
        "automation_record": "Target Track Enable Automation Recording", "tuner": "Target Track Enable Tuner",
        "next_patch": "Select Next Patch for Target Device", "prev_patch": "Select Prev Patch for Target Device",
    }
    if action not in names:
        raise ValueError(f"choose from {sorted(names)}")
    result = press_control(names[action])
    if action.endswith("patch"):
        time.sleep(0.2)
        MAIN.request_dump()
        patch = MAIN.find("Patch Name")
        result["patch"] = patch.text if patch else None
    return result


# --------------------------------------------------------------------------------------------
# Mixer (Master Section, surface locked by the user)
# --------------------------------------------------------------------------------------------

CHANNEL_PARAMS = ("Channel Name", "Level", "Pan", "Mute", "Solo", "Width")


@mcp.tool()
def mixer_overview() -> dict:
    """Channels of the current mixer bank (8 channels from the Remote Base Channel) + master level."""
    link = _surface("mixer")
    channels = []
    for n in range(1, 9):
        row = {}
        for p in CHANNEL_PARAMS:
            item = link.find(f"Channel {n} {p}")
            if item is not None:
                row[p] = item.text if item.text else item.value
        if row:
            channels.append({"channel": n, **row})
    master = link.find("Master Level")
    return {"channels": channels, "master_level": master.text if master else None}


@mcp.tool()
def mixer_set(channel: int, param: str, value: float | None = None, number: float | None = None, toggle: bool = False) -> dict:
    """Set a parameter of mixer channel 1..8 in the current bank, e.g. param 'Level', 'Pan',
    'FX1 Send Level', 'LF Gain', 'Mute' (toggle=True for buttons). Use channel=0 for master items
    like 'Master Level'."""
    name = param if channel == 0 else f"Channel {channel} {param}"
    if toggle:
        return press_control(name, surface="mixer")
    return set_control(name, value=value, number=number, surface="mixer")


@mcp.tool()
def mixer_bank(direction: str) -> dict:
    """Shift the mixer bank: 'next' / 'prev' moves the Remote Base Channel by 8 channels."""
    link = _surface("mixer")
    name = "Next 8 Remote Base Channel" if direction == "next" else "Previous 8 Remote Base Channel"
    item = link.find(name) or _require(link, "Next Remote Base Channel" if direction == "next" else "Previous Remote Base Channel")
    _press(link, item)
    time.sleep(0.15)
    link.request_dump()
    return mixer_overview()


# --------------------------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------------------------


@mcp.tool()
def play_notes(notes: list[dict], bpm: float = 120.0, channel: int = 1) -> dict:
    """Play notes into the selected track (requires 'Claude MCP' to be the Master Keyboard).

    notes: [{"note": 60 or "C4", "start": beats, "length": beats, "velocity": 1..127}]
    Blocking; total length is limited to 64 beats.
    """
    link = MAIN
    link.connect()
    beat = 60.0 / bpm
    events = []
    for n in notes:
        pitch = _note_number(n["note"])
        start, length = float(n.get("start", 0)), float(n.get("length", 1))
        vel = int(n.get("velocity", 100))
        events.append((start, 1, mido.Message("note_on", note=pitch, velocity=vel, channel=channel - 1)))
        events.append((start + length, 0, mido.Message("note_off", note=pitch, velocity=0, channel=channel - 1)))
    events.sort(key=lambda e: (e[0], e[1]))
    if events and events[-1][0] > 64:
        raise ValueError("longer than 64 beats; split it up")
    t0 = time.monotonic()
    try:
        for at, _, msg in events:
            delay = t0 + at * beat - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            link.send(msg)
    finally:
        for n in notes:
            link.send(mido.Message("note_off", note=_note_number(n["note"]), velocity=0, channel=channel - 1))
    return {"played": len(notes), "seconds": round(time.monotonic() - t0, 2)}


NOTE_NAMES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _note_number(note):
    """60 -> 60, 'C4' -> 60 (Reason/Yamaha style: C3 = 60 would differ; we use C4 = 60)."""
    if isinstance(note, int):
        return max(0, min(127, note))
    m = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d)", str(note).strip())
    if not m:
        raise ValueError(f"bad note {note!r}")
    n = NOTE_NAMES[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)] + (int(m.group(3)) + 1) * 12
    return max(0, min(127, n))


def main():
    mcp.run()


if __name__ == "__main__":
    main()
