# reason-mcp

Control **Reason 13** (Reason Studios) from Claude or any other MCP client.

reason-mcp has two parts that talk to each other. One is an MCP server written in Python. The other is a custom
**Remote** control-surface codec that runs inside Reason. Remote is the official API that hardware controllers
(Mackie Control, Launchkey, …) use, so the codec never patches or injects anything into Reason.

```
Claude ──MCP (stdio)──> reason_mcp (Python)
                           │ SysEx  F0 7D 43 4D …          ▲ item state: remotable name,
                           ▼ (loopMIDI virtual ports)      │ value, text shown by Reason
                    Reason 13 ── Lua codec "Claude MCP" ───┘
                                 + remote maps (141 device scopes)
```

The codec reports what is mapped right now: the parameter name, its value and the text Reason shows. So the agent
always knows which controls exist for the selected device, and can drive them in a closed loop. For example, it
can set "Filter Freq" to exactly 600 Hz or "Tempo" to 140 BPM.

## What it can do

| Tool | What it does |
|---|---|
| `reason_status` | Connection, selected track/device, tempo, position, play/loop/click |
| `list_controls`, `get_control` | Parameters available **now** (the main surface follows the selected track's device) |
| `set_control` | Set by normalized `value` 0..1, or by `number` as displayed (Hz, ms, dB, %, BPM), using a closed-loop search |
| `press_control`, `nudge_control` | Buttons and delta (jog) controls |
| `transport` | play, stop, record, loop, click, undo, redo, tap tempo, all notes off, … |
| `set_tempo`, `locate`, `set_loop` | Exact tempo (incl. decimals), go to bar, loop locators |
| `select_track`, `track` | Next/previous track or by name; mute/solo/monitor/next patch |
| `mixer_overview`, `mixer_set`, `mixer_bank` | Main mixer (banks of 8 channels from the Remote Base Channel) |
| `play_notes` | Play notes into the selected track |
| `catalog` | Offline list of mappable parameters per device |

Parameter changes made while Reason is recording (with automation recording enabled) are written as automation. Notes
are recorded too, so the agent can record parts and filter sweeps into a song.

### What Remote cannot do

Remote cannot **create or delete devices**, load patches by name, or edit clips. For device creation there is an
experimental UI-automation helper in `scripts/` (see below).

## Requirements

* Windows 10/11 with Reason 13 (Reason 12 probably works; untested)
* Python ≥ 3.10
* [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) for virtual MIDI ports (`winget install TobiasErichsen.loopMIDI`)
* An MCP client (Claude Code, Claude Desktop, …)

macOS should work with IAC Driver ports instead of loopMIDI; the install script knows the macOS Remote folder, but this is untested.

## Setup

### 1. Virtual MIDI ports

In loopMIDI create four ports with exactly these names (loopMIDI does not allow `>` in names):

```
Claude to Reason
Reason to Claude
Claude to Reason Mixer
Reason to Claude Mixer
```

You can use other names; see *Configuration*.

### 2. Install the package and the Remote codec

```bash
git clone https://github.com/igBerroteran/reason-MCP.git
cd reason-MCP
pip install -e .
python tools/install_remote.py      # copies codec + maps to %APPDATA%\Propellerhead Software\Remote
```

Restart Reason afterwards. Reason only reads codecs at start-up.

### 3. Add the control surfaces in Reason

*Preferences → MIDI → Remote keyboards and control surfaces → Add manually*

| Manufacturer | Model | In | Out |
|---|---|---|---|
| Claude | MCP | `Claude to Reason` | `Reason to Claude` |
| Claude | MCP Mixer | `Claude to Reason Mixer` | `Reason to Claude Mixer` |

* Tick **Master Keyboard** for `Claude MCP` if the agent should play/record notes.
* Under *Easy MIDI Inputs*, untick the two `Reason to Claude…` ports. They carry Reason's own output.
* *Options → Surface Locking*: lock `Claude MCP Mixer` to **Master Section**. The lock is saved in the song.

### 4. Register the MCP server

Claude Code:

```bash
claude mcp add reason --scope user -- python -m reason_mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{ "mcpServers": { "reason": { "command": "python", "args": ["-m", "reason_mcp"] } } }
```

Then ask the agent for `reason_status`.

## Configuration

Port names can be overridden with environment variables:
`REASON_MCP_OUT`, `REASON_MCP_IN`, `REASON_MCP_MIXER_OUT`, `REASON_MCP_MIXER_IN`.

## Regenerating the codec and maps

`tools/generate.py` builds the codec, the two remote maps and `src/reason_mcp/catalog.json` from your Reason
installation:

* Remotable item names per device come from Reason's bundled remote maps.
* The type of each item (button / value / delta / text) is inferred from the bundled codecs.
* The built-in Rack Extensions' `remote_implementation_chart` (Alligator, Pulveriser, The Echo) supply their parameters.

```bash
python tools/extract_strings.py "C:/Program Files/Propellerhead/Reason 13/Reason.exe" strings.txt   # optional
python tools/generate.py --strings strings.txt
python tools/install_remote.py
```

The optional strings file is a list of strings extracted from `Reason.exe`. With it, the generator fixes the
spelling and casing of item names. It also adds Document items that no bundled map uses, such as `Document Name`
and `Send All Notes Off`. Neither the strings file nor any Reason file is part of this repository.

## Tests

```bash
pip install -e ".[test]"
python -m pytest tests
```

The tests run the real Lua codec in Lua 5.1 (via `lupa`). Missing globals and per-call limits are set up the way
Reason sets them. On top of that, a full simulation goes MCP tool → SysEx → codec → fake Remote engine → state.

## Experimental: creating devices via UI automation

Remote cannot create devices, so `scripts/ui.ps1` drives Reason's window directly with screenshots, clicks and
keystrokes. The device palette works this way: search, then **drag** the device into the sequencer. Double-clicking
does not create anything. `scripts/record_drums.py` and `scripts/record_wobble.py` are example sessions: they
record a half-time drum beat into Kong and a wobble bass (notes plus live filter automation) into Europa.

These scripts depend on screen coordinates and Reason's layout, and they take over the mouse while running. Treat
them as examples, not as a stable API.

## Notes for codec developers

What we learned while building this:

* Reason's Remote Lua is **5.1 without `pcall`** (`attempt to call global 'pcall' (a nil value)`).
* Callbacks that run too many instructions get the surface **inactivated** ("did not respond properly"). Keep
  `remote_init`, `remote_set_state` and `remote_deliver_midi` lean: no per-item `pairs` copies, and bounded work
  per call.
* `remote_deliver_midi` got a `max_bytes` budget of only **104 bytes** per call (~20 calls/s) in testing.
* Remote maps reject a remotable mapped twice in one scope, and **scope names are case-insensitive**
  ("Scope already defined" for `PX7` vs `px7`).
* Literal strings can be mapped to text items (`Map T6 "Some text"`). This is how the codec reports which device
  scope is active.
* The surface shown in *Add manually* uses the `description` of each port as its label; keep it short.

## Limitations

* The real range of a parameter is not exposed by Remote. `set_control(number=…)` searches against the displayed
  text and assumes it grows with the value.
* Third-party Rack Extensions without a bundled map have no mapped parameters.
* A full state dump of a large device takes ~10 s because of the 104-byte budget.
* Notes only reach the selected track when `Claude MCP` is the Master Keyboard (Reason allows one).

## Disclaimer

Not affiliated with or endorsed by Reason Studios. "Reason", "Rack Extension" and device names are trademarks of
their respective owners. This project uses only the documented Remote control-surface mechanism. It contains no
Reason code.

## License

MIT
