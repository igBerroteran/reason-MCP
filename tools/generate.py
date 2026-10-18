"""Generate the Claude MCP Remote codec, remote maps and parameter catalog.

Sources (read-only):
  * Reason's bundled Remote codecs/maps  -> remotable item names per scope and
    the input type (button/value/delta/text) each one is normally driven with.
  * Strings extracted from Reason.exe    -> validation of built-in item names.

Usage:
  python tools/generate.py [--reason "C:/Program Files/Propellerhead/Reason 13"]
                           [--strings path/to/strset.txt]
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_REMOTE = ROOT / "remote"
OUT_CATALOG = ROOT / "src" / "reason_mcp" / "catalog.json"

MANUFACTURER = "Claude"
MODEL_MAIN = "MCP"
MODEL_MIXER = "MCP Mixer"

# Scopes that are not Reason 13 (Record / Essentials / old casing).
SKIP_SCOPE = re.compile(r"^(Record |Reason Essentials |THOR Polysonic)")
DOCUMENT = "Reason Document"
MASTER = "Reason Master Section"
MASTER_KB = "Master Keyboard"
MIXER_CHANNELS = 8  # Master Section "Channel N ..." items exposed (relative to Remote Base Channel)

# Locator items are jog-driven in every official map; absolute 0..16383 means nothing for them.
DOC_KIND_OVERRIDE = {n: "delta" for n in ("Left Loop", "Right Loop", "Song Position", "Sixteenth Position",
                                          "Left Loop 16th", "Right Loop 16th")}

TEXT_HINT = re.compile(r"(Name|Version|Reason App)$")
BUTTON_HINT = re.compile(
    r"(Button|Btn|Pad|Switch|Play|Stop|Record|Undo|Redo|Rewind|Fast Forward|Next|Prev|Previous|"
    r"Up|Down|Dn|Goto|Tap Tempo|Return To Zero|Reset|New |Mute|Solo|On/Off|Clear|Send All Notes Off|Select)"
)


def read_text(p):
    return p.read_text(encoding="utf-8-sig", errors="replace").replace("\r", "")


def parse_lua_codecs(codec_dir):
    """(manufacturer, model) -> {item name: kind}."""
    result = {}
    for luacodec in codec_dir.rglob("*.luacodec"):
        src = read_text(luacodec)
        for m in re.finditer(
            r'manufacturer\s*=\s*"([^"]+)"\s*,\s*model\s*=\s*"([^"]+)"\s*,\s*source\s*=\s*"([^"]+)"', src
        ):
            lua = luacodec.parent / m.group(3)
            if not lua.exists():
                continue
            items = {}
            for it in re.finditer(r'\{\s*name\s*=\s*"([^"]+)"([^}]*)\}', read_text(lua)):
                body = it.group(2)
                inp = re.search(r'input\s*=\s*"(\w+)"', body)
                out = re.search(r'output\s*=\s*"(\w+)"', body)
                if inp:
                    items[it.group(1)] = inp.group(1)
                elif out and out.group(1) == "text":
                    items[it.group(1)] = "text"
            result[(m.group(1), m.group(2))] = items
    return result


def parse_midi_codecs(codec_dir):
    result = {}
    for f in codec_dir.rglob("*.midicodec"):
        txt = read_text(f)
        man = re.search(r"^Control Surface Manufacturer\t([^\t\n]+)", txt, re.M)
        mod = re.search(r"^Control Surface Model\t([^\t\n]+)", txt, re.M)
        if not (man and mod):
            continue
        items = {}
        for line in txt.split("\n"):
            parts = line.split("\t")
            if parts[0] == "Item" and len(parts) > 2:
                items[parts[1].strip()] = parts[2].strip()
        result[(man.group(1).strip(), mod.group(1).strip())] = items
    return result


SCOPE_MANUFACTURER = {}  # scope -> manufacturer column as written in the official maps


def parse_maps(map_dir, codecs):
    """scope -> remotable -> Counter of kinds."""
    usage = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    # Reason compares scope names case-insensitively ("Scope already defined" for PX7 vs px7),
    # so every spelling of a scope is folded into the first one seen.
    spelling = {}
    for f in sorted(map_dir.rglob("*.remotemap")):
        txt = read_text(f)
        man = re.search(r"^Control Surface Manufacturer\t([^\t\n]+)", txt, re.M)
        mod = re.search(r"^Control Surface Model\t([^\t\n]+)", txt, re.M)
        items = codecs.get((man.group(1).strip(), mod.group(1).strip()), {}) if man and mod else {}
        scope = None
        for line in txt.split("\n"):
            parts = line.split("\t")
            if parts[0] == "Scope" and len(parts) > 2:
                scope = spelling.setdefault(parts[2].strip().lower(), parts[2].strip())
                SCOPE_MANUFACTURER.setdefault(scope, parts[1].strip())
            elif parts[0] == "Map" and len(parts) > 3 and scope:
                surface, remotable = parts[1].strip(), parts[3].strip()
                if not remotable or remotable.startswith('"') or "=" in remotable:
                    continue
                if SKIP_SCOPE.match(scope):
                    continue
                kind = items.get(surface)
                if kind is None:
                    kind = "button" if re.search(r"button|btn|pad|switch", surface, re.I) else "unknown"
                usage[scope][remotable][kind] += 1
    return usage


def classify(remotable, kinds):
    if TEXT_HINT.search(remotable) or (kinds.get("text") and len(kinds) == 1):
        return "text"
    if "Delta" in remotable:
        return "delta"
    if kinds.get("value"):
        return "value"
    if kinds.get("delta") and not kinds.get("button"):
        return "delta"  # jog-style items (Song Position, loop points) have no useful absolute range
    if kinds.get("button"):
        return "button"
    return "button" if BUTTON_HINT.search(remotable) else "value"


def is_builtin(scope):
    return not scope.startswith("se.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", default="C:/Program Files/Propellerhead/Reason 13")
    ap.add_argument("--strings", help="Sorted unique strings from Reason.exe (one per line) for validation")
    ap.add_argument("--device-cache", default="C:/ProgramData/Propellerhead Software/SharedResources/Reason_13/DeviceCache")
    args = ap.parse_args()

    remote = Path(args.reason) / "Remote"
    codecs = parse_lua_codecs(remote / "DefaultCodecs")
    codecs.update(parse_midi_codecs(remote / "DefaultCodecs"))
    usage = parse_maps(remote / "DefaultMaps", codecs)

    known = None
    known_lower = {}
    if args.strings:
        known = set(Path(args.strings).read_text(encoding="utf-8", errors="replace").split("\n"))
        known_lower = {s.lower(): s for s in known}

    # Built-in Rack Extensions ship their authoritative remote names in motherboard_def.lua.
    cache = Path(args.device_cache)
    re_scopes = {"Alligator": "Alligator", "Pulverizer": "Pulveriser", "TheEcho": "The Echo"}
    chart_items = {}
    for folder, scope in re_scopes.items():
        mb = cache / folder / "motherboard_def.lua"
        if mb.exists():
            chart = read_text(mb).split("remote_implementation_chart", 1)[-1]
            chart_items[scope] = re.findall(r'internal_name\s*=\s*"([^"]+)"', chart)
            for name in chart_items[scope]:
                usage[scope][name]  # ensure present; kind falls back to name heuristics

    def resolve(scope, name):
        """Return the verified spelling of a remotable item, or None to drop it."""
        # Unknown names are kept: shipped Reason 13 maps contain unverifiable names too. The
        # real hazard is a case variant of one item ("remotable item already mapped in this scope").
        if re.fullmatch(r"\d+", name):
            return None
        if known is None or not is_builtin(scope) or name in known:
            return name
        return known_lower.get(name.lower(), name)

    # Reason Document items found in Reason.exe that no bundled map uses.
    extra_doc = {
        "Document Name": "text", "Tick Position": "value", "Millisecond Position": "value",
        "Pre Count Bars": "value", "Send All Notes Off": "button", "Target Track Enable Monitoring": "button",
        "Target Track Enable Tuner": "button", "Target Track Tuner Note": "text", "Target Track Tuner Cents": "value",
        "Ableton Link Num Links": "value", "Time Position": "value", "Click Level": "value",
        "Move Loop Left": "button", "Move Loop Right": "button",
        "Move Loop One bar Left": "button", "Move Loop One bar Right": "button", "Punched-In": "button",
    }

    catalog = {}
    dropped = defaultdict(list)
    for scope, rems in sorted(usage.items()):
        entries = {}
        seen_lower = {}
        for name, kinds in sorted(rems.items()):
            if scope == MASTER:
                m = re.match(r"Channel (\d+) ", name)
                if m and int(m.group(1)) > MIXER_CHANNELS:
                    continue
            fixed = resolve(scope, name)
            if fixed is None:
                dropped[scope].append(name)
                continue
            name = fixed
            key = name.lower().strip()
            if key in seen_lower:
                continue
            seen_lower[key] = name
            entries[name] = classify(name, kinds)
        if scope == DOCUMENT:
            for name, kind in DOC_KIND_OVERRIDE.items():
                if name in entries:
                    entries[name] = kind
            for name, kind in extra_doc.items():
                if name.lower() not in seen_lower and (known is None or name in known):
                    entries[name] = kind
        if scope == MASTER_KB:
            continue
        if entries:
            catalog[scope] = entries

    # Slot allocation. Document scope gets the low slots on the main surface so it never
    # collides with the device scope that is active at the same time.
    kinds = ("button", "value", "delta", "text")
    doc_count = {k: sum(1 for v in catalog[DOCUMENT].values() if v == k) for k in kinds}
    base = {k: doc_count[k] + 1 for k in kinds}
    base["text"] += 1  # T(doc_count+1) is reserved for the scope-name literal

    slots = {}
    maxslot = dict(base)
    for scope, entries in catalog.items():
        start = {k: 1 for k in kinds} if scope in (DOCUMENT, MASTER) else dict(base)
        if scope == MASTER:
            start["text"] = 2  # T1 = scope literal on the mixer surface
        nxt = dict(start)
        s = {}
        for name, kind in entries.items():
            s[name] = {"kind": kind, "slot": nxt[kind]}
            nxt[kind] += 1
        for k in kinds:
            maxslot[k] = max(maxslot[k], nxt[k] - 1)
        slots[scope] = s

    prefix = {"button": "B", "value": "V", "delta": "D", "text": "T"}

    def item(kind, n):
        return f"{prefix[kind]}{n}"

    header = (
        "Propellerhead Remote Mapping File\nFile Format Version\t1.0.0\n"
        f"Control Surface Manufacturer\t{MANUFACTURER}\nControl Surface Model\t{{model}}\nMap Version\t1.0.0\n\n"
    )
    col = "//\tControl Surface Item\tKey\tRemotable Item\tScale\tMode\n"

    def scope_block(scope, scope_text_slot):
        manu = SCOPE_MANUFACTURER.get(scope) or ("Propellerhead Software" if scope.startswith("se.") else "Propellerheads")
        lines = [f"Scope\t{manu}\t{scope}", col]
        if scope_text_slot:
            lines.append(f'Map\tT{scope_text_slot}\t\t"{scope}"')
        for name, d in slots[scope].items():
            lines.append(f"Map\t{item(d['kind'], d['slot'])}\t\t{name}")
        return "\n".join(lines) + "\n\n"

    main_map = header.format(model=MODEL_MAIN)
    main_map += f"Scope\tPropellerheads\t{MASTER_KB}\n{col}Map\tKeyboard\t\tKeyboard\nMap\tPitch Bend\t\tPitch Bend\n" \
                "Map\tMod Wheel\t\tMod Wheel\nMap\tDamper Pedal\t\tDamper Pedal\n\n"
    main_map += scope_block(DOCUMENT, None)
    scope_slot = base["text"] - 1
    for scope in slots:
        if scope not in (DOCUMENT, MASTER):
            main_map += scope_block(scope, scope_slot)
    mixer_map = header.format(model=MODEL_MIXER) + scope_block(MASTER, 1)

    codec_dir = OUT_REMOTE / "Codecs" / "Lua Codecs" / MANUFACTURER
    map_dir = OUT_REMOTE / "Maps" / MANUFACTURER
    codec_dir.mkdir(parents=True, exist_ok=True)
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / "Claude MCP.remotemap").write_text(main_map, encoding="ascii", newline="\r\n")
    (map_dir / "Claude MCP Mixer.remotemap").write_text(mixer_map, encoding="ascii", newline="\r\n")

    lua = (ROOT / "tools" / "claude_mcp.lua.in").read_text(encoding="utf-8")
    def counts(scopes):
        m = {k: 1 for k in kinds}
        for sc in scopes:
            for d in slots[sc].values():
                m[d["kind"]] = max(m[d["kind"]], d["slot"])
        m["text"] = max(m["text"], scope_slot if MASTER not in scopes else 1)
        return m

    per_model = {
        "main": counts([sc for sc in slots if sc != MASTER]),
        "mixer": counts([MASTER]),
    }
    for model, key in (("MAIN", "main"), ("MIXER", "mixer")):
        m = per_model[key]
        lua = lua.replace(f"@{model}_COUNTS@", ", ".join(str(m[k]) for k in kinds))
    maxslot = per_model
    (codec_dir / "claude_mcp.lua").write_text(lua, encoding="ascii", newline="\r\n")
    (codec_dir / "claude.luacodec").write_text(
        (ROOT / "tools" / "claude.luacodec.in").read_text(encoding="utf-8"), encoding="ascii", newline="\r\n"
    )

    (codec_dir / "claude.png").write_bytes((ROOT / "tools" / "claude.png").read_bytes())

    OUT_CATALOG.parent.mkdir(parents=True, exist_ok=True)
    OUT_CATALOG.write_text(
        json.dumps(
            {
                "limits": maxslot,
                "document_text_slot_scope": scope_slot,
                "scopes": {s: {"surface": "mixer" if s == MASTER else "main", "items": v} for s, v in slots.items()},
            },
            indent=1,
        ),
        encoding="utf-8",
    )

    print("scopes:", len(slots), "slots:", maxslot)
    for s, names in dropped.items():
        print(f"dropped {len(names)} unverified in {s}: {names[:6]}")


if __name__ == "__main__":
    main()
