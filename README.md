# forge-wireless

Wireless "Get / Set" nodes for Autodesk Flame Batch, in the spirit of
ComfyUI's Set/Get nodes: relate two nodes by name instead of by a visible
pipe.

A **Set** is a MUX node named `SET_<channel>` fed by the real upstream — RGB
into `Input_0`, and the upstream's matte/alpha output (when it has one) into
`Matte_0`, so alpha rides the channel too. A **Get** is a MUX node named
`GET_<channel>` whose inputs are connected by the script to the matching
Set's `Result` and `OutMatte`, then hidden (`node.hide_input`) so no noodle
crosses the schematic.

Because every connection is real, Flame's render and dependency graph stay
correct — no evaluation-order hazard, no frame staleness, no render-farm
breakage. The hiding is purely cosmetic, and everything survives a batch
save/reload (verified end-to-end: nodes, connections, hidden state, and
colours all round-trip).

## Colours

Every channel takes a colour from the FORGE palette (12 hues, ember
`#E87E24` first). The Set node carries the full colour; every Get is painted
a 45%-lightened tint of the same hue, so routing reads at a glance. The
colour lives on the Set node itself (`schematic_colour`, saved with the
setup) — no external state. Relink reasserts Get tints from whatever colour
the Set currently has.

## Naming

- Channels sanitize to `[A-Za-z0-9_]` with no consecutive underscores
  (Flame silently coerces anything else to `_` in node names anyway).
- Flame enforces unique node names, so additional Gets on one channel are
  numbered in the prefix: `GET_bg`, `GET2_bg`, `GET3_bg`, … — the channel
  string stays pristine and can't collide with channels ending in digits.
- Channel identity is the flat name. Grouping/filtering by source node in
  the Get picker is derived live from the Set's input connection — the
  graph is the database, so it survives renames, rewires, and reloads with
  no stored metadata.

## Install

Drop `forge_wireless.py` in a Flame python hooks path, e.g.

- `/opt/Autodesk/shared/python/` (site-wide)
- `~/.autodesk/<product>/.../python/` (per-user)

then refresh python hooks in Flame. A **FORGE Wireless** submenu appears on
right-click in the Batch schematic.

## Usage

### Make Set from selected…

A single node with one output pair gets a simple dialog: channel name
(pre-filled from the node) and a colour swatch row with the next free
palette colour pre-selected, with live duplicate-name validation.

Bigger selections open a preview table — one proposed Set per row, channel
names editable, uncheck to skip, colours auto-assigned, and **row numbers
draggable to reorder the created column**:

- **multiple nodes** → one Set per node (channel = node name)
- **multichannel EXR clip** → one Set per layer, `<layer>_alpha` paired
  into the Set's matte; channels named from the layer stems (`rgba`, `z`,
  `ao`, `cryptomatte_mat`, …) rather than the clip's versioned filename
- **Action** → one Set per output pair (`output1 [ Comp ]` /
  `output1 [ Matte ]` paired by stem)
- **Group** → one Set per published output, no pairing (the published
  socket list is a user-authored contract; a published `X` and `X_alpha`
  are separate cases)
- **Compass** → expands to its member nodes

Placement is collision-aware: each source's Sets stack in a descending
column to its right, and the whole column slides further right until it
overlaps no existing node (expanded Groups draw a large box, and artists
park nodes beside their sources).

Outputs that can't feed a MUX are skipped with a console note rather than
leaving a dead Set behind — Flame's motion-vector pipes are a distinct link
type, and `connect_nodes()` from a vector output into a MUX is a silent
no-op. Detection is by verifying the wire, not by socket name: an EXR layer
*named* `motionVector` is a plain image and works fine.

### Make Get…

Channels are grouped under the node that feeds their Set (read live from
the connection) with a type-to-filter box matching channel or source names;
colour chips, double-click, and filter+Enter all work. The Get lands at the
spot where you right-clicked, pre-linked on both pipes, tinted, and hidden.
Unwired Sets group under `(unwired)`.

### Change Set input…

Re-feed an existing channel from a different node — select only the **new
source** (the Set can stay wherever it lives; that's the point of
wireless), pick the channel from the same grouped picker (which shows each
channel's current feeder), and both pipes move atomically: RGB and its
stem-paired matte connect together, and a source with no matte *clears*
the old matte instead of leaving it pointing at the previous node. Sources
with no usable outputs, or with several output pairs, warn and bail. A
failed wire (vector output) restores the previous feed best-effort.

This closes the trap manual rewiring leaves open: dragging one noodle by
hand can leave a Set with RGB from the new node and matte from the old —
**Relink now warns about such split feeds** whenever it runs.

### Rename channel…

With any `SET_`/`GET…_` node of the channel selected (numbered Gets
included) — renames the Set and renumbers every Get, with live validation
against existing channels. A rename that would touch zero nodes says so
loudly instead of pretending it worked.

### Relink all

Re-wires every Get to its Set (both pipes), reasserts colours and tints,
hides the pipes, and reports duplicates and missing Sets. Runs
automatically when a batch setup loads. Relink never moves nodes, so manual
arrangements stick.

## Groups: what to know

Flame's API cannot originate a connection *from* a Group node (silent
no-op) — a published-output connection actually belongs to the node
*inside* the group. Sets are therefore wired by **trial resolution**:
candidates nearest the group are connected one at a time until the group's
published tab confirms it routes the Set (wrong candidates disconnect with
no residue on the tabs). This disambiguates identically-named internal
sockets that no name-based rule ever could. If nothing routes, the Set is
left named, coloured, and unwired — connect it by hand once and Relink
preserves it.

Selecting a collapsed Group silently selects its hidden members too; the
expansion drops them (the group's published contract is the interface).
The same quirk doubles as the membership oracle the API otherwise lacks.
Members selected deliberately *without* their group expand normally.

One caveat: an **expanded** group's drawn row order — and therefore its tab
heights — is UI state the API does not expose (it matches neither node
positions nor socket enumeration order, and can change between sessions).
A collapsed group's tabs match the column order automatically; for an
expanded group, drag the preview-table rows to match what you see, or drag
the created MUXes after — Relink won't fight you.

## Flame API notes (2026.2, all verified live)

Hard-won findings that apply to any Flame hook, not just this one:

- `hasattr()` on a PyNode always returns True — attributes resolve
  dynamically. The genuine list is `node.attributes`.
- `connect_nodes()` **no-ops silently** on type-incompatible links
  (vector→image), and when the source is a Group node. Verify a connection
  landed by re-reading `node.sockets`; never trust the call.
- `flame.batch.disconnect_node(node, "Input_0")` disconnects an input.
- Node names are unique per container; Flame coerces `-`, `.`, spaces, etc.
  to `_` on assignment.
- Menu callbacks swallow exceptions silently — wrap every action to print
  the traceback and surface the error in the Flame console, or bugs become
  invisible no-ops.
- Qt dialogs: with no explicit default button, Enter activates the first
  auto-default button in the layout — if that's Cancel, "type name, press
  Enter" silently cancels. Set `setDefault(True)` on the primary button and
  `setAutoDefault(False)` on Cancel.
- `flame.batch.cursor_position` gives the schematic position of the last
  click — capture it when a menu action starts, before any dialog opens.
- Schematic +Y renders upward.
- Selecting a Group also selects its hidden members; isolating the group in
  the selection and reading who came along is the only membership query
  available (`.nodes` raises on Groups).
- Creating unfamiliar node types blind can hard-crash Flame — inspect
  existing nodes, or create one well-known type at a time.
