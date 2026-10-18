"""Record 4 bars of half-time dubstep drums into the selected (record-enabled) Kong track."""

import sys
import time

import mido

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import rtools as R  # noqa: E402
from reason_mcp import server as S  # noqa: E402

BPM = 140.0
BEAT = 60.0 / BPM
# Kong default kit: pad n -> note 35 + n
BD1, SD1, HHC, HHO, BD2, SD2, CLAP, HHT = 36, 37, 38, 39, 40, 41, 42, 43


def bar(n, fill=False):
    """Events (beat, note, velocity) for bar n (0-based)."""
    b = n * 4
    ev = [(b + 0, BD1, 127), (b + 2, SD1, 120), (b + 2, CLAP, 100)]
    ev += [(b + 0.75 * 4 - 0.5, BD2, 96)] if n % 2 else []
    for i in range(8):
        ev.append((b + i * 0.5, HHC, 90 if i % 2 == 0 else 64))
    ev.append((b + 3.5, HHO, 85))
    if fill:
        ev += [(b + 3 + i * 0.25, SD2, 70 + i * 12) for i in range(4)]
    return ev


def main():
    R.init()
    link = R.L
    events = sorted(bar(0) + bar(1) + bar(2) + bar(3, fill=True))
    total = 16
    loop = R.item("Loop On/Off")
    if link.get(loop.key).value:
        S._press(link, loop)
    S._press(link, R.item("Return To Zero"))
    time.sleep(0.3)
    S._press(link, R.item("Record"))
    t0 = time.monotonic()
    offs = []
    i = 0
    while True:
        beat = (time.monotonic() - t0) / BEAT
        if beat >= total + 0.5:
            break
        while i < len(events) and events[i][0] <= beat:
            _, note, vel = events[i]
            link.send(mido.Message("note_on", note=note, velocity=vel, channel=0))
            offs.append((beat + 0.1, note))
            i += 1
        for o in [o for o in offs if o[0] <= beat]:
            link.send(mido.Message("note_off", note=o[1], velocity=0, channel=0))
            offs.remove(o)
        time.sleep(0.004)
    for _, note in offs:
        link.send(mido.Message("note_off", note=note, velocity=0, channel=0))
    S._press(link, R.item("Stop"))
    time.sleep(0.3)
    S._press(link, R.item("Return To Zero"))
    S.set_loop(1, 5, enabled=True)
    print("recorded", len(events), "hits")


if __name__ == "__main__":
    import os

    main()
    os._exit(0)  # rtmidi's input thread can keep the interpreter alive on Windows
