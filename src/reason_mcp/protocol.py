"""SysEx protocol shared with the Claude MCP Remote codec (remote/.../claude_mcp.lua)."""

from dataclasses import dataclass

HEADER = (0x7D, 0x43, 0x4D)  # non-commercial manufacturer id, 'C', 'M'

KIND_BUTTON, KIND_VALUE, KIND_DELTA, KIND_TEXT = 0, 1, 2, 3
KIND_BY_NAME = {"button": KIND_BUTTON, "value": KIND_VALUE, "delta": KIND_DELTA, "text": KIND_TEXT}
KIND_PREFIX = {KIND_BUTTON: "B", KIND_VALUE: "V", KIND_DELTA: "D", KIND_TEXT: "T"}

CMD_INPUT, CMD_DUMP, CMD_PING = 0x01, 0x02, 0x03
CMD_STATE, CMD_PONG = 0x10, 0x11

VALUE_MAX = 16383
MAX_NAME = 48  # codec truncates remotable names to this many characters
DELTA_LIMIT = 8191


def _split14(n):
    n = max(0, min(VALUE_MAX, int(n)))
    return [n >> 7, n & 0x7F]


def _join14(hi, lo):
    return (hi << 7) | lo


def input_message(kind, slot, value):
    """SysEx data bytes (without F0/F7) for one input event."""
    if kind == KIND_DELTA:
        if not -DELTA_LIMIT <= value <= DELTA_LIMIT:
            raise ValueError("delta out of range")
        value += 8192
    return [*HEADER, CMD_INPUT, kind, *_split14(slot), *_split14(value)]


def dump_message():
    return [*HEADER, CMD_DUMP]


def ping_message():
    return [*HEADER, CMD_PING]


@dataclass
class ItemState:
    kind: int
    slot: int
    enabled: bool
    value: int
    name: str
    text: str

    @property
    def key(self):
        return f"{KIND_PREFIX[self.kind]}{self.slot}"


@dataclass
class Pong:
    buttons: int
    values: int
    deltas: int
    texts: int


def parse(data):
    """Parse SysEx data bytes (mido strips F0/F7). Returns ItemState, Pong or None."""
    data = list(data)
    if len(data) < 4 or tuple(data[:3]) != HEADER:
        return None
    cmd = data[3]
    if cmd == CMD_STATE and len(data) >= 12:
        kind, slot = data[4], _join14(data[5], data[6])
        enabled, value = bool(data[7]), _join14(data[8], data[9])
        rest = bytes(data[10:])
        name, _, rest = rest.partition(b"\x00")
        text, _, _ = rest.partition(b"\x00")
        return ItemState(kind, slot, enabled, value, name.decode("ascii", "replace"), text.decode("ascii", "replace"))
    if cmd == CMD_PONG and len(data) >= 12:
        return Pong(*(_join14(data[i], data[i + 1]) for i in range(4, 12, 2)))
    return None
