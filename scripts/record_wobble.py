"""Record a 4-bar dubstep wobble bass: notes + live Filter Freq automation on the selected track."""

import math
import sys
import time

import mido

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import rtools as R  # noqa: E402
from reason_mcp import protocol as P  # noqa: E402
from reason_mcp import server as S  # noqa: E402

BPM = 140.0
BEAT = 60.0 / BPM
LO, HI = 3556, 12248  # Filter Freq raw: ~140 Hz .. ~3.5 kHz

# (start beat, length beats, midi note, wobble rate in cycles per beat)
E1, G1, FS1, D2, A0, C2 = 28, 31, 30, 38, 21, 36
RIFF = [
    (0, 2, E1, 2), (2, 1, E1, 3), (3, 1, G1, 4),
    (4, 1.5, FS1, 1.5), (5.5, 0.5, E1, 4), (6, 2, E1, 1),
    (8, 2, E1, 2), (10, 1, E1, 3), (11, 1, D2, 4),
    (12, 1, C2, 4), (13, 1, A0, 2), (14, 2, E1, 0.5),
]
TOTAL = 16  # beats


def main():
    R.init()
    link = R.L
    freq = R.item("Filter Freq")
    loop = R.item("Loop On/Off")
    if link.get(loop.key).value:
        S._press(link, loop)
    S._press(link, R.item("Return To Zero"))
    time.sleep(0.3)

    S._press(link, R.item("Record"))
    t0 = time.monotonic()
    pending = sorted([(s, 1, n) for s, _, n, _ in RIFF] + [(s + l - 0.02, 0, n) for s, l, n, _ in RIFF])
    ei = 0
    last_raw = None
    while True:
        beat = (time.monotonic() - t0) / BEAT
        if beat >= TOTAL + 0.5:
            break
        while ei < len(pending) and pending[ei][0] <= beat:
            at, on, note = pending[ei]
            link.send(mido.Message("note_on" if on else "note_off", note=note, velocity=118 if on else 0))
            ei += 1
        # Wobble: LFO phase restarts at every note so each hit opens on the beat.
        cur = next(((s, r) for s, l, n, r in RIFF if s <= beat < s + l), None)
        if cur:
            phase = (beat - cur[0]) * cur[1]
            lfo = 0.5 - 0.5 * math.cos(2 * math.pi * phase)
            raw = round(LO + (HI - LO) * lfo ** 1.5)
        else:
            raw = LO
        if raw != last_raw:
            link.send_input(P.KIND_VALUE, freq.slot, raw)
            last_raw = raw
        time.sleep(0.008)
    for _, _, n, _ in RIFF:
        link.send(mido.Message("note_off", note=n, velocity=0))
    S._press(link, R.item("Stop"))
    time.sleep(0.3)
    R.number("Filter Freq", 900)
    S._press(link, R.item("Return To Zero"))
    S.set_loop(1, 5, enabled=True)
    print("recorded", TOTAL, "beats")


if __name__ == "__main__":
    import os

    main()
    os._exit(0)  # rtmidi's input thread can keep the interpreter alive on Windows
