# forge-wireless

Wireless "Get / Set" nodes for Autodesk Flame Batch, in the spirit of ComfyUI's
Set/Get nodes: relate two nodes by name instead of by a visible pipe.

A **Set** is a MUX node named `SET_<channel>` fed by the real upstream. A
**Get** is a MUX node named `GET_<channel>` whose input is connected by the
script to the matching Set's output, then hidden so no noodle crosses the
schematic. Because the connection is real, Flame's render and dependency graph
stay correct — the hiding is purely cosmetic.

## Install

Drop `forge_getset.py` in a Flame python hooks path, e.g.

- `/opt/Autodesk/shared/python/` (site-wide)
- `~/.autodesk/<product>/.../python/` (per-user)

then refresh python hooks in Flame. A "Wireless Get/Set" submenu appears on
right-click in the Batch schematic.

## Usage

1. Select a node, right-click → Wireless Get/Set → **Make Set from selected**.
   Rename the created MUX's suffix to your channel (e.g. `SET_bg`).
2. Anywhere, **Make Get** and rename its suffix to match (`GET_bg`).
3. **Relink all** wires every Get to its Set and hides the pipes. This also
   runs automatically when a batch setup is loaded.

## Per-site verification

The exact attribute that hides a MUX input link varies by Flame version. Run
**Inspect selected node** from the menu on a MUX to confirm/adjust
`HIDE_ATTR_CANDIDATES` in `forge_getset.py`. If none works, the
connect-by-name still happens — you just flip the MUX "Input" toggle by hand
once per Get (it persists in the saved setup).
