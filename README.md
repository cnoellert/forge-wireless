# forge-wireless

Wireless "Get / Set" nodes for Autodesk Flame Batch, in the spirit of ComfyUI's
Set/Get nodes: relate two nodes by name instead of by a visible pipe.

A **Set** is a MUX node named `SET_<channel>` fed by the real upstream — RGB
into `Input_0`, and the upstream's matte/alpha output (when it has one) into
`Matte_0`, so alpha rides the channel too. A **Get** is a MUX node named
`GET_<channel>` whose inputs are connected by the script to the matching
Set's `Result` and `OutMatte`, then hidden (`node.hide_input`) so no noodle
crosses the schematic. Because the connections are real, Flame's render and
dependency graph stay correct — the hiding is purely cosmetic.

Flame enforces unique node names, so additional Gets on one channel carry a
number in the prefix: `GET_bg`, `GET2_bg`, `GET3_bg`, … — the channel string
stays pristine and can't be confused with channels that end in digits.
Channel names are sanitized to `[A-Za-z0-9_]` (Flame silently coerces other
characters to `_` anyway). Errors in any menu action are reported to the
Flame console — the hook system otherwise swallows exceptions silently.

## Colours

Every channel gets a colour from the FORGE palette (12 hues, ember first).
The Set node carries the full colour; every Get is painted a lighter tint of
the same hue, so you can read the routing at a glance. The colour lives on the
Set node itself (`schematic_colour`, saved with the setup) — no external
state, so colours survive save/reload.

## Install

Drop `forge_wireless.py` in a Flame python hooks path, e.g.

- `/opt/Autodesk/shared/python/` (site-wide)
- `~/.autodesk/<product>/.../python/` (per-user)

then refresh python hooks in Flame. A **FORGE Wireless** submenu appears on
right-click in the Batch schematic.

## Usage

1. Select a node, right-click → FORGE Wireless → **Make Set from selected…**
   Name the channel and pick its colour in the dialog (the next free palette
   colour is pre-selected).

   Bigger selections open a preview table instead — one proposed Set per
   row, channels editable, uncheck to skip, colours auto-assigned:
   - **multiple nodes** → one Set per node (channel = node name)
   - **multichannel EXR clip** → one Set per layer, `<layer>_alpha` paired
     into the Set's matte, channels named from the layer stems (`rgba`,
     `z`, `ao`, `cryptomatte_mat`, …)
   - **Action** → one Set per output pair (`[ Comp ]`/`[ Matte ]` paired)
   - **Group** → one Set per published output, no pairing (the socket list
     is a user-authored contract). Flame's API cannot originate connections
     from a Group node (silent no-op), so Sets are wired from the internal
     node that owns the published socket; when several identically-named
     internal sockets make that ambiguous, the Set is created named and
     coloured but unwired — connect it by hand once (the console says
     which) and Relink preserves it.
   - **Compass** → expands to its member nodes
2. Anywhere, **Make Get…** — channels are grouped under the node that feeds
   their Set (read live from the connection, no stored metadata) with a
   type-to-filter box matching channel or source names; colour chips,
   double-click works. The Get lands at the spot where you right-clicked,
   pre-linked, tinted, and hidden.
3. **Relink all** re-wires every Get to its Set, reasserts colours, and hides
   the pipes. It also runs automatically whenever a batch setup is loaded.
4. **Rename channel…** with a `SET_`/`GET_` node selected renames the channel
   across all of its nodes.

## Compatibility

Verified against Flame 2026.2 (PySide6): `node.type` reads `"MUX"`,
`hide_input` / `schematic_colour` are the real dynamic attributes (note that
`hasattr()` is useless on PyNode — it resolves any name; the true list is
`node.attributes`), and `connect_nodes(src, "Default", dst, "Default")` lands
on `Input_0`.
