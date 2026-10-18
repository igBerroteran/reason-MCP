"""Run the generated Remote codec in Lua 5.1 against a fake `remote` API and the Python protocol."""

from pathlib import Path

import pytest

lua51 = pytest.importorskip("lupa.lua51")

from reason_mcp import protocol as P  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CODEC = ROOT / "remote" / "Codecs" / "Lua Codecs" / "Claude" / "claude_mcp.lua"
LUACODEC = CODEC.with_name("claude.luacodec")

FAKE_REMOTE = """
remote = {}
defined_items = nil
handled = {}
state = {}
function remote.define_items(t) defined_items = t end
function remote.define_auto_inputs(t) auto_inputs = t end
function remote.handle_input(m) table.insert(handled, m) end
function remote.get_item_state(i)
  local s = state[i] or {}
  return {is_enabled = s.enabled or false, remote_item_name = s.name or "", text_value = s.text or ""}
end
function remote.get_item_value(i) return (state[i] or {}).value or 0 end
function remote.get_item_text_value(i) return (state[i] or {}).text or "" end
function remote.make_midi(h)
  local ev = {}
  local n = 0
  for byte in string.gmatch(h, "%x%x") do n = n + 1; ev[n] = tonumber(byte, 16) end
  ev.size = n
  return ev
end
"""


SANDBOX = "pcall = nil; xpcall = nil; error = nil; unpack = nil; select = nil; loadstring = nil; require = nil"


@pytest.fixture()
def lua():
    rt = lua51.LuaRuntime(unpack_returned_tuples=True)
    rt.execute(FAKE_REMOTE)
    # Reason's Remote sandbox lacks these ("attempt to call global 'pcall' (a nil value)").
    rt.execute(SANDBOX)
    rt.execute(CODEC.read_text(encoding="ascii"))
    rt.globals().remote_init("Claude", "MCP")
    return rt


def event(rt, data):
    ev = rt.table_from({i + 1: b for i, b in enumerate(data)})
    ev.size = len(data)
    ev.time_stamp = 0
    return ev


def item_index(rt, name):
    items = rt.globals().defined_items
    for i in range(1, len(items) + 1):
        if items[i].name == name:
            return i
    raise KeyError(name)


def to_list(ev):
    return [ev[i] for i in range(1, ev.size + 1)]


def deliver_all(g, max_bytes=100000):
    """Reason calls remote_deliver_midi repeatedly; collect until the codec has nothing left."""
    out = []
    for _ in range(200):  # a call may skip only disabled items and return nothing
        events = g.remote_deliver_midi(max_bytes, 1)
        out += [to_list(events[i]) for i in range(1, len(events) + 1)]
    return out


def test_luacodec_declares_both_surfaces():
    rt = lua51.LuaRuntime()
    rt.execute(LUACODEC.read_text(encoding="ascii"))
    surfaces = rt.globals().remote_supported_control_surfaces()
    models = [surfaces[i].model for i in range(1, len(surfaces) + 1)]
    assert models == ["MCP", "MCP Mixer"]
    assert all(surfaces[i].source == CODEC.name for i in range(1, len(surfaces) + 1))


def test_value_input_reaches_the_right_item(lua):
    data = [0xF0, *P.input_message(P.KIND_VALUE, 12, 9000), 0xF7]
    assert lua.globals().remote_process_midi(event(lua, data)) is True
    handled = lua.globals().handled
    assert handled[1].item == item_index(lua, "V12")
    assert handled[1].value == 9000


def test_delta_input_is_signed(lua):
    data = [0xF0, *P.input_message(P.KIND_DELTA, 3, -5), 0xF7]
    lua.globals().remote_process_midi(event(lua, data))
    h = lua.globals().handled[1]
    assert (h.item, h.value) == (item_index(lua, "D3"), -5)


def test_foreign_midi_is_not_consumed(lua):
    assert lua.globals().remote_process_midi(event(lua, [0x90, 60, 100])) is False


def test_state_report_round_trip(lua):
    idx = item_index(lua, "V7")
    lua.globals().state[idx] = lua.table_from({"enabled": True, "name": "Filter Freq", "text": "1.2 kHz", "value": 4242})
    g = lua.globals()
    g.remote_prepare_for_use()
    g.remote_set_state(lua.table_from({1: idx}))
    parsed = [P.parse(m[1:-1]) for m in deliver_all(g)]
    pong = [p for p in parsed if isinstance(p, P.Pong)][0]
    assert pong.values > 0 and pong.texts > 0
    v7 = [p for p in parsed if isinstance(p, P.ItemState) and p.key == "V7"][0]
    assert (v7.enabled, v7.value, v7.name, v7.text) == (True, 4242, "Filter Freq", "1.2 kHz")


def test_deliver_work_is_bounded(lua):
    """Reason inactivates surfaces whose callbacks run 'too many instructions'."""
    g = lua.globals()
    g.remote_prepare_for_use()
    events = g.remote_deliver_midi(10**6, 1)
    assert len(events) <= 5  # pong + MAX_PER_CALL states


def test_deliver_respects_byte_budget(lua):
    g = lua.globals()
    g.remote_prepare_for_use()
    events = g.remote_deliver_midi(512, 1)
    assert sum(events[i].size for i in range(1, len(events) + 1)) <= 512


def test_non_ascii_text_is_sanitized(lua):
    idx = item_index(lua, "T3")
    lua.globals().state[idx] = lua.table_from({"enabled": True, "name": "Patch Name", "text": "Caf\xe9"})
    g = lua.globals()
    g.remote_set_state(lua.table_from({1: idx}))
    t3 = [P.parse(m[1:-1]) for m in deliver_all(g)][0]
    assert t3.key == "T3" and all(32 <= ord(c) < 127 for c in t3.text)
