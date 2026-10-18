"""MIDI link to one Claude MCP control surface in Reason (one in port + one out port)."""

import re
import threading
import time

import mido

from . import protocol as P


def find_port(names, wanted):
    """loopMIDI ports show up as '<name> <n>' on Windows; match case-insensitively by prefix."""
    pattern = re.compile(re.escape(wanted) + r"( \d+)?$", re.I)
    return next((n for n in names if pattern.match(n)), None)


class SurfaceLink:
    def __init__(self, label, out_name, in_name):
        self.label = label
        self.out_name = out_name
        self.in_name = in_name
        self.out = None
        self.inp = None
        self.items = {}  # "V12" -> ItemState
        self.item_rx = {}  # "V12" -> monotonic time of last report
        self.pong = None
        self.last_rx = 0.0
        self._cond = threading.Condition()

    # -- connection -------------------------------------------------------------------------

    def connect(self):
        if self.out is not None and self.inp is not None:
            return
        outs, ins = mido.get_output_names(), mido.get_input_names()
        out_port, in_port = find_port(outs, self.out_name), find_port(ins, self.in_name)
        if out_port is None or in_port is None:
            raise ConnectionError(
                f"[{self.label}] MIDI ports not found (want out '{self.out_name}', in '{self.in_name}'). "
                f"Outputs: {outs}. Inputs: {ins}. Create them in loopMIDI."
            )
        self.out = mido.open_output(out_port)
        self.inp = mido.open_input(in_port, callback=self._on_message)
        self.send_raw(P.ping_message())
        self.send_raw(P.dump_message())

    def close(self):
        for port in (self.inp, self.out):
            if port is not None:
                port.close()
        self.inp = self.out = None

    @property
    def connected(self):
        return self.out is not None and self.inp is not None

    # -- receive ----------------------------------------------------------------------------

    def _on_message(self, msg):
        if msg.type != "sysex":
            return
        parsed = P.parse(msg.data)
        if parsed is None:
            return
        with self._cond:
            self.last_rx = time.monotonic()
            if isinstance(parsed, P.Pong):
                self.pong = parsed
            else:
                self.items[parsed.key] = parsed
                self.item_rx[parsed.key] = self.last_rx
            self._cond.notify_all()

    def wait_for(self, predicate, timeout=1.0):
        """Wait until predicate(self) is truthy; returns its last value."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                result = predicate(self)
                remaining = deadline - time.monotonic()
                if result or remaining <= 0:
                    return result
                self._cond.wait(remaining)

    def wait_quiet(self, quiet=0.08, timeout=1.0):
        """Wait until no state arrived for `quiet` seconds (Reason finished reporting)."""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if time.monotonic() - self.last_rx >= quiet:
                return
            time.sleep(quiet / 2)

    # -- send -------------------------------------------------------------------------------

    def send_raw(self, data):
        if self.out is None:
            self.connect()
        self.out.send(mido.Message("sysex", data=data))

    def send_input(self, kind, slot, value):
        self.send_raw(P.input_message(kind, slot, value))

    def send(self, msg):
        if self.out is None:
            self.connect()
        self.out.send(msg)

    def request_dump(self, settle=0.25):
        with self._cond:
            self.items.clear()
        self.send_raw(P.dump_message())
        self.wait_for(lambda s: len(s.items) > 0, timeout=1.5)
        # The codec skips disabled items silently, so gaps of a few hundred ms are normal.
        self.wait_quiet(quiet=0.6, timeout=15.0)
        time.sleep(settle / 4)

    # -- queries ----------------------------------------------------------------------------

    def snapshot(self):
        with self._cond:
            return dict(self.items)

    def find(self, name, enabled_only=True):
        """Item currently bound to a remotable called `name` (case-insensitive)."""
        n = name.lower().strip()

        def same(reported):
            # The codec truncates names to P.MAX_NAME characters.
            r = reported.lower().strip()
            return r == n or (len(reported) >= P.MAX_NAME and n.startswith(r))

        with self._cond:
            hits = [s for s in self.items.values() if s.name and same(s.name) and (s.enabled or not enabled_only)]
        hits.sort(key=lambda s: (not s.enabled, s.kind == P.KIND_TEXT))
        return hits[0] if hits else None

    def wait_item(self, key, since, timeout=0.6):
        """Wait for a report of item `key` newer than `since` (time.monotonic())."""
        return self.wait_for(lambda s: s.item_rx.get(key, 0) > since and s.items[key], timeout)

    def get(self, key):
        with self._cond:
            return self.items.get(key)
