"""End-to-end: MCP tools -> SysEx -> real Lua codec -> fake Reason Remote engine -> SysEx -> state."""


import mido
import pytest

lua51 = pytest.importorskip("lupa.lua51")

from reason_mcp import server  # noqa: E402
from reason_mcp.link import SurfaceLink  # noqa: E402

from test_codec import CODEC, FAKE_REMOTE  # noqa: E402


class FakeReason:
    """Minimal Remote engine: maps codec items to remotables with real ranges and text."""

    def __init__(self, link, mapping):
        self.rt = lua51.LuaRuntime(unpack_returned_tuples=True)
        self.rt.execute(FAKE_REMOTE)
        self.rt.execute(CODEC.read_text(encoding="ascii"))
        self.g = self.rt.globals()
        self.g.remote_init("Claude", "MCP")
        items = self.g.defined_items
        self.index = {items[i].name: i for i in range(1, len(items) + 1)}
        self.link = link
        self.rem = {}  # codec item index -> remotable dict
        for slot, rem in mapping.items():
            self.rem[self.index[slot]] = dict(rem)
        self.notes = []
        self.g.remote_prepare_for_use()
        self.sync(list(self.rem))

    def text(self, r):
        return r.get("fmt", "{:g}").format(r["v"]) if "v" in r else r.get("text", "")

    def sync(self, changed):
        for i, r in self.rem.items():
            value = 0
            if "v" in r:
                lo, hi = r["min"], r["max"]
                item = self.g.defined_items[i]
                value = round((r["v"] - lo) / (hi - lo) * item.max) if item.max else r["v"]
            self.g.state[i] = self.rt.table_from(
                {"enabled": True, "name": r["name"], "text": self.text(r), "value": value}
            )
        self.g.remote_set_state(self.rt.table_from({k + 1: v for k, v in enumerate(changed)}))
        for _ in range(40):  # Reason keeps calling; empty calls happen while skipping disabled items
            events = self.g.remote_deliver_midi(1024, 1)
            for k in range(1, len(events) + 1):
                ev = events[k]
                data = [ev[j] for j in range(1, ev.size + 1)]
                self.link._on_message(mido.Message("sysex", data=data[1:-1]))

    # mido output port interface used by SurfaceLink
    def send(self, msg):
        if msg.type != "sysex":
            self.notes.append(msg)
            return
        data = [0xF0, *msg.data, 0xF7]
        ev = self.rt.table_from({i + 1: b for i, b in enumerate(data)})
        ev.size, ev.time_stamp = len(data), 0
        n_before = len(self.g.handled)
        self.g.remote_process_midi(ev)
        changed = []
        for k in range(n_before + 1, len(self.g.handled) + 1):
            h = self.g.handled[k]
            r = self.rem.get(h.item)
            if r is None:
                continue
            item = self.g.defined_items[h.item]
            if item.input == "value":
                r["v"] = round(r["min"] + h.value / item.max * (r["max"] - r["min"]))
            elif item.input == "delta":
                r["v"] = max(r["min"], min(r["max"], r["v"] + h.value))
            elif item.input == "button" and h.value == 1:
                r["presses"] = r.get("presses", 0) + 1
                if "v" in r:
                    r["v"] = 0 if r["v"] else 1
            changed.append(h.item)
        self.sync(changed)


@pytest.fixture()
def reason(monkeypatch):
    link = SurfaceLink("main", "x", "y")
    slots = server.CATALOG["scopes"]["Reason Document"]["items"]

    def slot(name):
        s = slots[name]
        return {"button": "B", "value": "V", "delta": "D", "text": "T"}[s["kind"]] + str(s["slot"])

    fake = FakeReason(
        link,
        {
            slot("Tempo BPM"): {"name": "Tempo BPM", "v": 120, "min": 1, "max": 999},
            slot("Tempo Decimal"): {"name": "Tempo Decimal", "v": 0, "min": 0, "max": 999},
            slot("Bar Position"): {"name": "Bar Position", "v": 1, "min": 1, "max": 9999},
            slot("Play"): {"name": "Play"},
            slot("Loop On/Off"): {"name": "Loop On/Off", "v": 0, "min": 0, "max": 1},
            slot("Target Track Name"): {"name": "Target Track Name", "text": "Bass"},
            f"T{server.CATALOG['document_text_slot_scope']}": {"name": "", "text": "SubTractor Analog Synthesizer"},
        },
    )
    link.out = fake
    link.inp = object()
    monkeypatch.setattr(server, "MAIN", link)
    monkeypatch.setitem(server.SURFACES, "main", link)
    monkeypatch.setattr(link, "request_dump", lambda settle=0: None)
    monkeypatch.setattr(link, "wait_quiet", lambda quiet=0, timeout=0: None)
    return fake


def test_set_tempo_exact(reason):
    out = server.set_tempo(128.5)
    assert out["Tempo BPM"] == "128"
    assert out["Tempo Decimal"] == "500"


def test_locate_bar(reason):
    assert server.locate(17)["bar"] == "17"


def test_transport_play_presses_button(reason):
    server.transport("play")
    play = next(r for r in reason.rem.values() if r["name"] == "Play")
    assert play["presses"] == 1


def test_status_reports_scope_and_track(reason):
    st = server.reason_status()
    assert st["main"]["connected"] and st["main"]["codec_answered"]
    assert st["device_scope"] == "SubTractor Analog Synthesizer"
    assert st["Target Track Name"] == "Bass"
    assert st["Tempo BPM"] == "120"


def test_list_controls_filters(reason):
    names = [c["name"] for c in server.list_controls(contains="tempo")]
    assert set(names) == {"Tempo BPM", "Tempo Decimal"}


def test_play_notes_sends_on_and_off(reason):
    server.play_notes([{"note": "C4", "start": 0, "length": 0.01}], bpm=6000)
    kinds = [(m.type, m.note) for m in reason.notes]
    assert ("note_on", 60) in kinds and ("note_off", 60) in kinds
