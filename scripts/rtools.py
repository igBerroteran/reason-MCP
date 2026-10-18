"""Helpers for scripted sessions against a live Reason (uses the MCP server internals)."""

import time

from reason_mcp import protocol as P
from reason_mcp import server as S

L = S.MAIN


def init():
    S._surface("main")
    L.request_dump()


def item(name):
    return S._require(L, name)


def text(name):
    return L.get(item(name).key).text


def set_raw(name, raw, wait=True):
    it = item(name)
    t0 = time.monotonic()
    L.send_input(P.KIND_VALUE, it.slot, int(raw))
    if wait:
        st = S._settled(L, it.key, t0)
        return st.text if st else None


def set_norm(name, x):
    return set_raw(name, round(max(0, min(1, x)) * P.VALUE_MAX))


def options(name, steps=64):
    """Scan a value control and return {text: raw} for every distinct label."""
    seen = {}
    for i in range(steps + 1):
        raw = round(i * P.VALUE_MAX / steps)
        t = set_raw(name, raw)
        seen.setdefault(t, raw)
    return seen


def set_text(name, wanted, steps=64):
    opts = options(name, steps)
    for t, raw in opts.items():
        if t and t.lower() == wanted.lower():
            set_raw(name, raw)
            return t
    raise ValueError(f"{name}: {wanted!r} not in {list(opts)}")


def press_until(name, wanted, limit=24):
    it = item(name)
    for _ in range(limit):
        cur = L.get(it.key).text
        if cur.lower() == wanted.lower():
            return cur
        t0 = time.monotonic()
        S._press(L, it)
        S._settled(L, it.key, t0)
    raise ValueError(f"{name}: never reached {wanted!r}")


def number(name, target):
    return S._seek_number(L, item(name), target)
