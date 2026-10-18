import json
from pathlib import Path

import pytest

from reason_mcp import protocol as P
from reason_mcp.link import find_port

ROOT = Path(__file__).resolve().parent.parent


def test_state_parse():
    data = [*P.HEADER, P.CMD_STATE, P.KIND_VALUE, 0, 5, 1, 0x40, 0x00, *b"Tempo BPM", 0, *b"120", 0]
    s = P.parse(data)
    assert (s.key, s.enabled, s.value, s.name, s.text) == ("V5", True, 0x40 << 7, "Tempo BPM", "120")


def test_input_limits():
    assert P.input_message(P.KIND_DELTA, 1, 8191)[-2:] == [127, 127]
    with pytest.raises(ValueError):
        P.input_message(P.KIND_DELTA, 1, 9000)


def test_find_port_does_not_confuse_mixer_ports():
    names = ["Claude to Reason Mixer 2", "Claude to Reason 1", "Some Interface MIDI Out 1"]
    assert find_port(names, "Claude to Reason") == "Claude to Reason 1"
    assert find_port(names, "Claude to Reason Mixer") == "Claude to Reason Mixer 2"


def test_map_has_no_duplicate_remotables_per_scope():
    """Reason rejects 'remotable item already mapped in this scope'."""
    for f in (ROOT / "remote" / "Maps" / "Claude").glob("*.remotemap"):
        scope, seen, items = None, set(), set()
        scopes = set()
        for line in f.read_text(encoding="ascii").splitlines():
            parts = line.split("\t")
            if parts[0] == "Scope":
                # Reason: "Scope already defined" is case-insensitive.
                assert parts[2].lower() not in scopes, (f.name, parts[2])
                scopes.add(parts[2].lower())
                scope, seen, items = parts[2], set(), set()
            elif parts[0] == "Map":
                rem = parts[3].lower()
                assert rem not in seen, (f.name, scope, parts[3])
                assert parts[1] not in items, (f.name, scope, parts[1])
                seen.add(rem)
                items.add(parts[1])


def test_catalog_slots_fit_codec():
    cat = json.loads((ROOT / "src" / "reason_mcp" / "catalog.json").read_text(encoding="utf-8"))
    for scope in cat["scopes"].values():
        for it in scope["items"].values():
            assert it["slot"] <= cat["limits"][scope["surface"]][it["kind"]]
