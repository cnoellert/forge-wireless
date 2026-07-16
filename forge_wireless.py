# forge_wireless.py
# ---------------------------------------------------------------------------
# FORGE Wireless — "Get / Set" for Autodesk Flame Batch, in the spirit of
# ComfyUI's Set/Get nodes: relate two nodes by NAME instead of a visible pipe.
#
# How it works
#   * A "Set" is a MUX node named  SET_<channel>  fed by the real upstream --
#     RGB into Input_0 and, when the upstream exposes a matte/alpha output,
#     that into Matte_0 as well.
#   * A "Get" is a MUX node named  GET_<channel>  whose inputs are connected
#     (by this script) to the matching Set's Result AND OutMatte -- then the
#     input links are hidden (node.hide_input) so no noodle crosses the
#     schematic. Flame node names are unique, so additional Gets on the same
#     channel are numbered in the prefix:  GET2_<channel>, GET3_<channel>, ...
#
# Why MUX + hidden link (and not a data side-channel):
#   connect_nodes() makes a REAL connection, so Flame's render and dependency
#   graph stay correct -- no evaluation-order hazard, no frame staleness, no
#   render-farm breakage. Hiding the MUX input link is purely cosmetic.
#
# Colour model
#   Each channel gets a colour from the FORGE palette. The colour is stored on
#   the Set node itself (schematic_colour, saved with the setup); every Get is
#   painted a lightened tint of its Set's colour at relink time. No external
#   state -- reload the setup and the colours come back with the nodes.
#
# Verified against Flame 2026.2 Python API:
#   node.type reads "MUX"; node.hide_input / node.schematic_colour are real
#   dynamic attributes (listed in node.attributes -- note hasattr() is useless
#   here because PyNode resolves any attribute name); node.delete() removes.
#
# Install
#   Drop this file in a Flame python hooks path, e.g.
#     /opt/Autodesk/shared/python/            (site-wide)
#     ~/.autodesk/<product>/... /python/      (per-user)
#   then Flame -> refresh python hooks. A "FORGE Wireless" submenu appears on
#   right-click in the Batch schematic.
# ---------------------------------------------------------------------------

import re

import flame

__version__ = "0.7.3"

# --- configuration ---------------------------------------------------------

SET_PREFIX = "SET_"
GET_PREFIX = "GET_"
MUX_CREATE = "Mux"          # name accepted by flame.batch.create_node()
MUX_TYPE   = "MUX"          # what node.type reads back as

# Channel palette (r, g, b in 0-1). FORGE ember first, then hues picked to
# stay distinct on the dark schematic.
PALETTE = (
    ("Ember",   (0.910, 0.494, 0.141)),   # #E87E24
    ("Teal",    (0.165, 0.631, 0.596)),   # #2AA198
    ("Azure",   (0.231, 0.510, 0.769)),   # #3B82C4
    ("Violet",  (0.557, 0.420, 0.784)),   # #8E6BC8
    ("Magenta", (0.769, 0.337, 0.604)),   # #C4569A
    ("Crimson", (0.753, 0.224, 0.169)),   # #C0392B
    ("Moss",    (0.420, 0.620, 0.243)),   # #6B9E3E
    ("Gold",    (0.788, 0.635, 0.153)),   # #C9A227
    ("Cyan",    (0.208, 0.722, 0.769)),   # #35B8C4
    ("Rose",    (0.831, 0.439, 0.478)),   # #D4707A
    ("Indigo",  (0.333, 0.376, 0.784)),   # #5560C8
    ("Slate",   (0.478, 0.557, 0.639)),   # #7A8EA3
)

GET_LIGHTEN = 0.45          # Get tint: blend Set colour toward white by this


# --- small helpers ---------------------------------------------------------

def _val(attr):
    """Read a Flame PyAttribute (or plain value) as its python value."""
    try:
        return attr.get_value()
    except Exception:
        return attr

def _node_name(node):
    return str(_val(node.name))

def _is_mux(node):
    return str(_val(node.type)).upper() == MUX_TYPE

def _sanitize(channel):
    """Channel charset: [A-Za-z0-9_], no consecutive underscores.

    Flame silently coerces other characters (incl. '-') to '_' in node
    names, so anything looser breaks the name mapping. Collapsing runs of
    underscores keeps coerced names (e.g. 'a - b') readable.
    """
    channel = re.sub(r"[^A-Za-z0-9]+", "_", channel.strip())
    return re.sub(r"_+", "_", channel).strip("_")

def _lighten(rgb, amount=GET_LIGHTEN):
    return tuple(c + (1.0 - c) * amount for c in rgb)

def _rgb_to_hex(rgb):
    return "#{0:02x}{1:02x}{2:02x}".format(
        *(max(0, min(255, int(round(c * 255)))) for c in rgb))

def _console(text):
    try:
        flame.messages.show_in_console("[FORGE Wireless] " + text, "info", 6)
    except Exception:
        print("[FORGE Wireless] " + text)


# --- node discovery --------------------------------------------------------

def _muxes_by_channel(prefix):
    """channel -> [nodes]  for every MUX whose name starts with `prefix`."""
    out = {}
    for n in flame.batch.nodes:
        if not _is_mux(n):
            continue
        nm = _node_name(n)
        if nm.startswith(prefix) and len(nm) > len(prefix):
            out.setdefault(nm[len(prefix):], []).append(n)
    return out

GET_RE = re.compile(r"^GET(\d*)_(.+)$")   # GET_bg, GET2_bg, GET3_bg, ...

def _get_channel_of(name):
    """Channel encoded in a Get node name, or None if not a Get name.

    Flame node names are unique, so multiple Gets on one channel carry a
    number in the PREFIX (GET2_bg = second Get of channel 'bg'), keeping the
    channel string pristine. Legacy v0.4 names (GET_bg__2) still resolve so
    existing setups migrate on their first relink.
    """
    m = GET_RE.match(name)
    if not m or not m.group(2):
        return None
    return re.sub(r"__\d+$", "", m.group(2))

def _gets_by_channel():
    """channel -> [nodes] for every Get MUX in the batch."""
    out = {}
    for n in flame.batch.nodes:
        if not _is_mux(n):
            continue
        chan = _get_channel_of(_node_name(n))
        if chan:
            out.setdefault(chan, []).append(n)
    return out

def _free_get_name(channel):
    """First unused Get node name for a channel (names are unique in Flame)."""
    taken = {_node_name(n) for n in flame.batch.nodes}
    if GET_PREFIX + channel not in taken:
        return GET_PREFIX + channel
    i = 2
    while "GET{0}_{1}".format(i, channel) in taken:
        i += 1
    return "GET{0}_{1}".format(i, channel)

def _source_outputs(node):
    """(rgb_socket, matte_socket_or_None) for an arbitrary upstream node.

    Output socket names vary by type (Result/OutMatte on most, custom labels
    like 'output1 [ Comp ]' on Action, Result-only on Colour Source); pick a
    matte-ish socket by name and treat the first non-matte socket as RGB.
    """
    try:
        outs = list(dict(node.sockets)["output"].keys())
    except Exception:
        outs = []
    matte = next((s for s in outs
                  if "matte" in s.lower() or "alpha" in s.lower()), None)
    rgb = next((s for s in outs if s != matte), None) or "Default"
    return rgb, matte


def _stem_of(sock):
    """'output1 [ Comp ]' -> 'output1'; other socket names pass through."""
    return re.sub(r"\s*\[.*\]\s*$", "", sock).strip()

def _stem_pairs(outs):
    """Pair RGB-ish outputs with their matte partner by naming convention:
    Result+OutMatte, X+X_alpha, 'output1 [ Comp ]'+'output1 [ Matte ]'.
    Unpaired matte-ish outputs become solo rows."""
    outs = list(outs)
    mattish = set()
    for sock in outs:
        low = sock.lower()
        if low == "outmatte" or low.endswith("_alpha") or "[ matte ]" in low:
            mattish.add(sock)
    used = set()
    pairs = []
    for sock in outs:
        if sock in mattish:
            continue
        low = sock.lower()
        cands = [sock + "_alpha", _stem_of(sock) + " [ Matte ]"]
        if low == "result":
            cands.append("OutMatte")
        matte = next((m for m in outs if m in mattish and m not in used
                      and any(m.lower() == c.lower() for c in cands)), None)
        if matte:
            used.add(matte)
        pairs.append((sock, matte))
    for sock in outs:
        if sock in mattish and sock not in used:
            pairs.append((sock, None))
    return pairs

def _expand_compasses(selection):
    """Replace Compass nodes in a selection with their member nodes."""
    nodes, seen = [], set()
    for n in (selection or []):
        members = [n]
        if str(_val(n.type)).upper() == "COMPASS":
            try:
                members = list(n.nodes)
            except Exception:
                members = []
        for m in members:
            nm = _node_name(m)
            if nm not in seen:
                seen.add(nm)
                nodes.append(m)
    return nodes

def _expand_selection(selection):
    """Flatten a selection into Set proposals:
    [{node, rgb, matte, channel}, ...]

    - Compass -> its member nodes
    - Group   -> one proposal per published output, NO matte pairing (the
                 socket list is a user-authored contract; a published matte
                 is its own case)
    - other   -> stem-paired outputs (multichannel clips/Action get one
                 proposal per pair)
    Wireless SET_/GET nodes and nodes without outputs are skipped.
    """
    rows = []
    for node in _expand_compasses(selection):
        t = str(_val(node.type)).upper()
        nm = _node_name(node)
        if t == MUX_TYPE and (nm.startswith(SET_PREFIX) or _get_channel_of(nm)):
            continue
        try:
            outs = list(dict(node.sockets)["output"].keys())
        except Exception:
            outs = []
        if not outs:
            continue
        if t == "GROUP":
            pairs = [(sock, None) for sock in outs]
        else:
            pairs = _stem_pairs(outs)
        base = _sanitize(nm).lower()
        for rgb, matte in pairs:
            if len(pairs) == 1:
                chan = base
            elif t == "CLIP":
                # multichannel EXR: the socket stems ARE the layer names
                # (rgba, Z, AO, Cryptomatte_MAT, ...) -- the clip's node name
                # is a versioned filename nobody wants inside a channel
                chan = _sanitize(_stem_of(rgb)).lower()
            else:
                chan = _sanitize("{0}_{1}".format(base, _stem_of(rgb))).lower()
            rows.append({"node": node, "rgb": rgb, "matte": matte,
                         "channel": chan})
    return rows


def _wire_group_set(group, sock, set_node):
    """Wire a Set to a Group's published output by trial resolution.

    connect_nodes() from a Group is a silent no-op, and Flame records
    published-output connections as originating from the node INSIDE the
    group -- but several internal nodes can expose identically-named
    sockets, and only one owns the published tab (verified live: two
    internal nodes both had 'Result'; connecting the wrong one made a
    plain connection that did NOT route through the group).

    So: try candidate nodes nearest the group first; after each connect,
    check whether the group's published socket now lists the Set as a
    destination. Wrong candidates are disconnected (no residue on the
    group's tabs -- verified). Returns True once wired.
    """
    try:
        gx, gy = group.pos_x.get_value(), group.pos_y.get_value()
    except Exception:
        gx = gy = 0
    set_name = _node_name(set_node)
    cands = []
    for n in flame.batch.nodes:
        t = str(_val(n.type)).upper()
        nm = _node_name(n)
        if t in ("GROUP", "COMPASS") or t == MUX_TYPE and (
                nm.startswith(SET_PREFIX) or _get_channel_of(nm)):
            continue
        if n is set_node:
            continue
        if sock in dict(n.sockets).get("output", {}):
            try:
                d = abs(n.pos_x.get_value() - gx) + abs(n.pos_y.get_value() - gy)
            except Exception:
                d = 1e9
            cands.append((d, nm, n))
    cands.sort(key=lambda c: (c[0], c[1]))
    for _, _, cand in cands[:12]:
        try:
            flame.batch.connect_nodes(cand, sock, set_node, "Input_0")
        except Exception:
            continue
        routed = set_name in str(dict(group.sockets)["output"].get(sock, []))
        if routed:
            return True
        try:
            flame.batch.disconnect_node(set_node, "Input_0")
        except Exception:
            pass
    return False


def _set_colour(node):
    """A Set node's channel colour, or None if never assigned."""
    try:
        c = _val(node.schematic_colour)
        return tuple(c) if c else None
    except Exception:
        return None

def _used_palette_indices():
    """Palette indices currently claimed by existing Set nodes."""
    used = set()
    for nodes in _muxes_by_channel(SET_PREFIX).values():
        c = _set_colour(nodes[0])
        if not c:
            continue
        for i, (_, p) in enumerate(PALETTE):
            if all(abs(a - b) < 0.02 for a, b in zip(c, p)):
                used.add(i)
    return used

def _next_palette_index():
    used = _used_palette_indices()
    for i in range(len(PALETTE)):
        if i not in used:
            return i
    return len(used) % len(PALETTE)


# --- core actions (GUI-free; the dialogs below call these) ------------------

def create_set(source_node, channel, colour):
    """Create SET_<channel> downstream of source_node, coloured and wired.

    RGB goes to the Set's Input_0; if the source exposes a matte/alpha
    output socket, it is wired to Matte_0 so alpha rides the channel too.
    """
    rgb, matte = _source_outputs(source_node)
    return _create_set_node(source_node, rgb, matte, channel, colour)

def _create_set_node(source_node, rgb, matte, channel, colour, dy=0, at=None):
    """Socket-explicit Set creation (multichannel sources make several).
    `at` places the node absolutely; otherwise source-relative +220/dy."""
    m = flame.batch.create_node(MUX_CREATE)
    m.name = SET_PREFIX + channel
    m.schematic_colour = colour
    try:
        if at is not None:
            m.pos_x, m.pos_y = int(at[0]), int(at[1])
        else:
            m.pos_x = source_node.pos_x + 220
            m.pos_y = source_node.pos_y + dy
    except Exception:
        pass
    flame.batch.connect_nodes(source_node, rgb, m, "Input_0")
    if matte:
        flame.batch.connect_nodes(source_node, matte, m, "Matte_0")
    return m

def _clear_column_x(base_x, ys):
    """First x at/right of base_x where a column of Sets at heights `ys`
    doesn't sit on existing nodes (expanded Groups/clips are tall, and
    artists park nodes next to their sources -- +220 alone lands inside)."""
    occupied = []
    for n in flame.batch.nodes:
        try:
            occupied.append((n.pos_x.get_value(), n.pos_y.get_value()))
        except Exception:
            pass
    x = base_x
    for _ in range(24):
        clear = all(not (abs(nx - x) < 200 and
                         any(abs(ny - y) < 110 for y in ys))
                    for nx, ny in occupied)
        if clear:
            return x
        x += 200
    return x

def _set_is_wired(set_node):
    """True if any input of the Set actually connected. connect_nodes() is a
    silent no-op for type-incompatible links (e.g. motion-vector outputs
    into a MUX image/matte input), so every wire must be verified."""
    try:
        return any(v for v in dict(set_node.sockets)["input"].values())
    except Exception:
        return False

def _apply_set_rows(rows):
    """Create a Set per proposal row; dedupe channels, auto-assign colours,
    stack each source's Sets in a collision-free column to its right.
    Returns created channels."""
    existing = set(_muxes_by_channel(SET_PREFIX))
    made = []
    unwired = []
    skipped = []

    buckets, order = {}, []
    for r in rows:
        k = id(r["node"])
        if k not in buckets:
            buckets[k] = []
            order.append(k)
        buckets[k].append(r)

    for k in order:
        rs = buckets[k]
        src = rs[0]["node"]
        try:
            base_x = src.pos_x.get_value() + 220
            base_y = src.pos_y.get_value()
        except Exception:
            base_x = base_y = None
        col = None
        if base_x is not None:
            ys = [base_y - i * 150 for i in range(len(rs))]
            col = _clear_column_x(base_x, ys)

        for i, r in enumerate(rs):
            chan = _sanitize(r["channel"]) or "channel"
            base, n2 = chan, 2
            while chan in existing:
                chan = "{0}_{1}".format(base, n2)
                n2 += 1
            existing.add(chan)
            colour = PALETTE[_next_palette_index()][1]
            at = (col, base_y - i * 150) if col is not None else None
            rgb = r["rgb"]
            if str(_val(src.type)).upper() == "GROUP":
                m = _create_set_node_unwired(src, chan, colour, at=at)
                if not _wire_group_set(src, rgb, m):
                    unwired.append((chan, rgb))
                made.append(chan)
                continue
            else:
                m = _create_set_node(src, rgb, r["matte"], chan, colour,
                                     at=at)
                if not _set_is_wired(m):
                    m.delete()
                    existing.discard(chan)
                    skipped.append((chan, rgb))
                    continue
            made.append(chan)

    if skipped:
        _console("SKIPPED (output can't feed a MUX -- vector-type sockets "
                 "can't ride a wireless channel): "
                 + ", ".join("{0} <- {1}".format(c, sock)
                             for c, sock in skipped))
    if unwired:
        _console("UNWIRED (Flame can't resolve which internal node owns the "
                 "published output -- connect by hand, Relink keeps it): "
                 + ", ".join("{0} <- {1}".format(c, sock)
                             for c, sock in unwired))
    return made

def _create_set_node_unwired(near_node, channel, colour, dy=0, at=None):
    """A named, coloured Set with no input -- for group outputs whose
    internal owner can't be resolved. The artist wires it by hand once."""
    m = flame.batch.create_node(MUX_CREATE)
    m.name = SET_PREFIX + channel
    m.schematic_colour = colour
    try:
        if at is not None:
            m.pos_x, m.pos_y = int(at[0]), int(at[1])
        else:
            m.pos_x = near_node.pos_x + 220
            m.pos_y = near_node.pos_y + dy
    except Exception:
        pass
    return m

def create_get(channel, near_node=None, at=None):
    """Create a Get for the channel, wire it to its Set, tint it, hide the
    pipes. Subsequent Gets on the same channel get numbered names.

    Placement: `at` (an (x, y) schematic position, e.g. the cursor position
    captured when the menu was invoked) wins over `near_node`.
    """
    m = flame.batch.create_node(MUX_CREATE)
    m.name = _free_get_name(channel)
    try:
        if at is not None:
            m.pos_x, m.pos_y = int(at[0]), int(at[1])
        elif near_node is not None:
            m.pos_x = near_node.pos_x
            m.pos_y = near_node.pos_y - 180
    except Exception:
        pass
    _link_gets({channel: [m]})
    return m

def _link_gets(get_map, set_map=None):
    """Wire every Get in get_map to its Set; tint and hide. Returns stats."""
    if set_map is None:
        set_map = _muxes_by_channel(SET_PREFIX)
    linked, hidden, missing = 0, 0, []

    for chan, gets in get_map.items():
        sets = set_map.get(chan)
        if not sets:
            missing.append(chan)
            continue
        src = sets[0]
        colour = _set_colour(src)
        if colour is None:
            colour = PALETTE[_next_palette_index()][1]
            src.schematic_colour = colour
        tint = _lighten(colour)
        for g in gets:
            flame.batch.connect_nodes(src, "Result", g, "Input_0")
            flame.batch.connect_nodes(src, "OutMatte", g, "Matte_0")
            g.schematic_colour = tint
            linked += 1
            try:
                g.hide_input = True
                hidden += 1
            except Exception:
                pass
    return linked, hidden, missing

def relink(selection=None):
    """Re-wire every Get to its Set, reassert colours, hide the pipes."""
    set_map = _muxes_by_channel(SET_PREFIX)
    get_map = _gets_by_channel()
    dupes = sorted(c for c, ns in set_map.items() if len(ns) > 1)

    linked, hidden, missing = _link_gets(get_map, set_map)

    msg = "linked {0}, hid {1} pipe(s)".format(linked, hidden)
    if missing:
        msg += " | no Set for: {0}".format(", ".join(sorted(set(missing))))
    if dupes:
        msg += " | DUPLICATE Set channels: {0}".format(", ".join(dupes))
    if linked and hidden == 0:
        msg += " | (couldn't auto-hide -- toggle MUX 'Input' by hand)"
    _console(msg)

def rename_channel(old, new):
    """Rename a channel across its Set and all of its (numbered) Get nodes.

    Caller must ensure `new` is not an existing channel -- Flame rejects
    duplicate node names, and a mid-loop rejection would leave the channel
    half-renamed.
    """
    set_map = _muxes_by_channel(SET_PREFIX)
    gets = _gets_by_channel().get(old, [])
    renamed = 0
    for n in set_map.get(old, []):
        n.name = SET_PREFIX + new
        renamed += 1
    for n in gets:
        n.name = _free_get_name(new)
        renamed += 1
    return renamed


# --- Qt / FORGE theme ------------------------------------------------------

def _qt():
    from PySide6 import QtCore, QtGui, QtWidgets  # Flame 2025+
    return QtCore, QtGui, QtWidgets

FORGE_SS = (
    "QDialog { background: #282c34; }"
    "QLabel { color: #ccc; font-size: 12px; }"
    "QLineEdit { background: #1e2028; color: #ccc; "
    "  border: 1px solid #555; border-radius: 3px; "
    "  padding: 4px 8px; font-size: 12px; }"
    "QLineEdit:focus { border: 1px solid #E87E24; }"
    "QListWidget { background: #1e2028; color: #ccc; border: none; "
    "  font-size: 12px; }"
    "QListWidget::item { padding: 5px 6px; }"
    "QListWidget::item:selected { background: #2d4f7a; color: #fff; }"
    "QTreeWidget { background: #1e2028; color: #ccc; border: none; "
    "  font-size: 12px; }"
    "QTreeWidget::item { padding: 4px 6px; }"
    "QTreeWidget::item:selected { background: #2d4f7a; color: #fff; }"
    "QTableWidget { background: #1e2028; color: #ccc; border: none; "
    "  font-size: 11px; gridline-color: #2e3240; }"
    "QTableWidget::item { padding: 2px 6px; }"
    "QTableWidget::item:selected { background: #2d4f7a; color: #fff; }"
    "QHeaderView::section { background: #23262f; color: #888; "
    "  border: none; border-right: 1px solid #3a3f4f; "
    "  border-bottom: 1px solid #3a3f4f; "
    "  padding: 5px 6px; font-size: 10px; font-weight: bold; }"
)

BTN_PRIMARY = (
    "QPushButton { background: #E87E24; color: #1a1a1a; font-weight: bold; "
    "  border: none; border-radius: 3px; padding: 6px 18px; font-size: 12px; }"
    "QPushButton:hover { background: #f09040; }"
    "QPushButton:disabled { background: #3a3f4f; color: #666; }"
)
BTN_QUIET = (
    "QPushButton { background: #3a3f4f; color: #ddd; "
    "  border: none; border-radius: 3px; padding: 6px 18px; font-size: 12px; }"
    "QPushButton:hover { background: #4a5062; }"
)

def _header(QtWidgets, text):
    lbl = QtWidgets.QLabel(text)
    lbl.setStyleSheet("color: #E87E24; font-weight: bold; font-size: 13px;")
    return lbl

def _hint(QtWidgets, text):
    lbl = QtWidgets.QLabel(text)
    lbl.setStyleSheet("color: #888; font-size: 11px;")
    return lbl

def _swatch_icon(QtGui, rgb, size=14):
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtGui.QColor(_rgb_to_hex(rgb)))
    return QtGui.QIcon(pix)


# --- dialogs ----------------------------------------------------------------

def make_set_dialog(selection):
    """GUI: create coloured Set MUXes for the selection.

    A single node with one output pair gets the simple dialog; anything
    bigger (multi-select, Group, Action, multichannel clip, Compass) gets
    the preview table with one row per proposed Set.
    """
    selection = [n for n in (selection or [])]
    if not selection:
        _console("Make Set: select the node(s) you want to broadcast first.")
        return
    rows = _expand_selection(selection)
    if not rows:
        _console("Make Set: selection has no usable outputs.")
        return
    if len(rows) > 1:
        return _multi_set_dialog(rows)
    row = rows[0]
    src = row["node"]

    QtCore, QtGui, QtWidgets = _qt()
    existing = set(_muxes_by_channel(SET_PREFIX))

    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle("FORGE — Wireless Set")
    dlg.setMinimumWidth(420)
    dlg.setStyleSheet(FORGE_SS)
    lay = QtWidgets.QVBoxLayout(dlg)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(10)

    lay.addWidget(_header(QtWidgets, "Create Set from '{0}'"
                          .format(_node_name(src))))

    # channel name
    row = QtWidgets.QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(_hint(QtWidgets, "Channel"))
    name_edit = QtWidgets.QLineEdit(row["channel"])
    name_edit.selectAll()
    row.addWidget(name_edit, 1)
    lay.addLayout(row)

    # colour swatches
    lay.addWidget(_hint(QtWidgets, "Channel colour  (Gets take a lighter tint)"))
    sw_row = QtWidgets.QHBoxLayout()
    sw_row.setSpacing(6)
    chosen = {"idx": _next_palette_index()}
    swatches = []

    def _restyle():
        for i, b in enumerate(swatches):
            border = "2px solid #fff" if i == chosen["idx"] else "1px solid #555"
            b.setStyleSheet(
                "QPushButton {{ background: {0}; border: {1}; "
                "border-radius: 3px; }}".format(
                    _rgb_to_hex(PALETTE[i][1]), border))

    for i, (pname, rgb) in enumerate(PALETTE):
        b = QtWidgets.QPushButton()
        b.setFixedSize(24, 24)
        b.setToolTip(pname)
        b.clicked.connect(lambda checked=False, i=i:
                          (chosen.update(idx=i), _restyle()))
        swatches.append(b)
        sw_row.addWidget(b)
    sw_row.addStretch()
    _restyle()
    lay.addLayout(sw_row)

    # validation + buttons
    warn = _hint(QtWidgets, "")
    warn.setStyleSheet("color: #C0392B; font-size: 11px;")
    lay.addWidget(warn)

    btns = QtWidgets.QHBoxLayout()
    btns.addStretch()
    cancel = QtWidgets.QPushButton("Cancel")
    cancel.setStyleSheet(BTN_QUIET)
    cancel.clicked.connect(dlg.reject)
    cancel.setAutoDefault(False)
    ok = QtWidgets.QPushButton("Create Set")
    ok.setStyleSheet(BTN_PRIMARY)
    ok.clicked.connect(dlg.accept)
    ok.setAutoDefault(True)
    ok.setDefault(True)
    btns.addWidget(cancel)
    btns.addWidget(ok)
    lay.addLayout(btns)

    def _validate():
        chan = _sanitize(name_edit.text())
        if not chan:
            warn.setText("Channel name is empty.")
            ok.setEnabled(False)
        elif chan in existing:
            warn.setText("Channel '{0}' already has a Set.".format(chan))
            ok.setEnabled(False)
        else:
            warn.setText("")
            ok.setEnabled(True)

    name_edit.textChanged.connect(_validate)
    _validate()

    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return
    chan = _sanitize(name_edit.text())
    m = _create_set_node(src, row["rgb"], row["matte"], chan,
                         PALETTE[chosen["idx"]][1])
    if not _set_is_wired(m):
        m.delete()
        _console("No Set created: output '{0}' of '{1}' can't feed a MUX "
                 "(vector-type socket) -- a wireless channel can't carry "
                 "it.".format(row["rgb"], _node_name(src)))
        return
    _console("Set '{0}' created ({1}). Drop Gets anywhere -- they link "
             "by name.".format(chan, PALETTE[chosen["idx"]][0]))


def _multi_set_dialog(rows):
    """Preview table: one row per proposed Set. Uncheck to skip, edit the
    channel names in place; colours auto-assign from the FORGE palette."""
    QtCore, QtGui, QtWidgets = _qt()

    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle("FORGE — Wireless Sets")
    dlg.setMinimumSize(680, 380)
    dlg.setStyleSheet(FORGE_SS)
    lay = QtWidgets.QVBoxLayout(dlg)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(10)

    lay.addWidget(_header(QtWidgets, "Create {0} Sets".format(len(rows))))
    lay.addWidget(_hint(QtWidgets, "One Set per output. Uncheck rows to "
                        "skip; channel names are editable. Colours "
                        "auto-assign from the FORGE palette."))

    tbl = QtWidgets.QTableWidget(len(rows), 4)
    tbl.setHorizontalHeaderLabels(["Node", "Output", "Matte", "Channel"])
    tbl.verticalHeader().setVisible(False)
    tbl.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
    for i, r in enumerate(rows):
        node_item = QtWidgets.QTableWidgetItem(_node_name(r["node"]))
        node_item.setFlags(QtCore.Qt.ItemIsUserCheckable
                           | QtCore.Qt.ItemIsEnabled
                           | QtCore.Qt.ItemIsSelectable)
        node_item.setCheckState(QtCore.Qt.Checked)
        tbl.setItem(i, 0, node_item)
        for col, txt in ((1, r["rgb"]), (2, r["matte"] or "—")):
            cell = QtWidgets.QTableWidgetItem(txt)
            cell.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            tbl.setItem(i, col, cell)
        tbl.setItem(i, 3, QtWidgets.QTableWidgetItem(r["channel"]))
    tbl.horizontalHeader().setStretchLastSection(True)
    tbl.setColumnWidth(0, 170)
    tbl.setColumnWidth(1, 150)
    tbl.setColumnWidth(2, 130)
    lay.addWidget(tbl, 1)

    btns = QtWidgets.QHBoxLayout()
    btns.addStretch()
    cancel = QtWidgets.QPushButton("Cancel")
    cancel.setStyleSheet(BTN_QUIET)
    cancel.clicked.connect(dlg.reject)
    cancel.setAutoDefault(False)
    ok = QtWidgets.QPushButton("Create All")
    ok.setStyleSheet(BTN_PRIMARY)
    ok.clicked.connect(dlg.accept)
    ok.setAutoDefault(True)
    ok.setDefault(True)
    btns.addWidget(cancel)
    btns.addWidget(ok)
    lay.addLayout(btns)

    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return
    todo = []
    for i, r in enumerate(rows):
        if tbl.item(i, 0).checkState() != QtCore.Qt.Checked:
            continue
        todo.append(dict(r, channel=tbl.item(i, 3).text()))
    if not todo:
        _console("Make Set: nothing checked, nothing created.")
        return
    made = _apply_set_rows(todo)
    _console("Created {0} Set(s): {1}".format(len(made), ", ".join(made)))


def make_get_dialog(selection):
    """GUI: pick an existing channel, create a tinted, pre-linked GET_ MUX."""
    set_map = _muxes_by_channel(SET_PREFIX)
    if not set_map:
        _console("Make Get: no Set nodes yet -- create a Set first.")
        return
    get_map = _gets_by_channel()

    # capture where the menu was invoked BEFORE the dialog opens -- by the
    # time it closes the mouse has moved to the dialog's buttons
    try:
        click_pos = tuple(flame.batch.cursor_position)
    except Exception:
        click_pos = None

    QtCore, QtGui, QtWidgets = _qt()

    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle("FORGE — Wireless Get")
    dlg.setMinimumSize(420, 440)
    dlg.setStyleSheet(FORGE_SS)
    lay = QtWidgets.QVBoxLayout(dlg)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(10)

    lay.addWidget(_header(QtWidgets, "Create Get"))

    filt = QtWidgets.QLineEdit()
    filt.setPlaceholderText("filter by channel or source node...")
    lay.addWidget(filt)

    # channels grouped under the node that feeds their Set (read live from
    # the Set's input connection -- no stored metadata)
    groups = {}
    for chan in set_map:
        src = _set_source_name(set_map[chan][0]) or "(unwired)"
        groups.setdefault(src, []).append(chan)

    tree = QtWidgets.QTreeWidget()
    tree.setHeaderHidden(True)
    first_chan_item = [None]
    for src in sorted(groups, key=str.lower):
        top = QtWidgets.QTreeWidgetItem(["{0}   ({1})".format(src, len(groups[src]))])
        top.setFlags(QtCore.Qt.ItemIsEnabled)
        top.setForeground(0, QtGui.QBrush(QtGui.QColor("#888")))
        for chan in sorted(groups[src], key=str.lower):
            n_gets = len(get_map.get(chan, []))
            item = QtWidgets.QTreeWidgetItem([
                "{0}   ({1} get{2})".format(chan, n_gets,
                                            "" if n_gets == 1 else "s")])
            colour = _set_colour(set_map[chan][0])
            if colour:
                item.setIcon(0, _swatch_icon(QtGui, colour))
            item.setData(0, QtCore.Qt.UserRole, chan)
            top.addChild(item)
            if first_chan_item[0] is None:
                first_chan_item[0] = item
        tree.addTopLevelItem(top)
    tree.expandAll()
    if first_chan_item[0] is not None:
        tree.setCurrentItem(first_chan_item[0])
    tree.itemDoubleClicked.connect(
        lambda item, col: item.data(0, QtCore.Qt.UserRole) and dlg.accept())
    lay.addWidget(tree, 1)

    def _apply_filter(text):
        text = text.strip().lower()
        first = None
        for i in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(i)
            src_match = text in top.text(0).lower()
            visible = 0
            for j in range(top.childCount()):
                ch = top.child(j)
                show = (not text) or src_match or text in ch.text(0).lower()
                ch.setHidden(not show)
                if show:
                    visible += 1
                    if first is None:
                        first = ch
            top.setHidden(visible == 0)
        if first is not None:
            tree.setCurrentItem(first)

    filt.textChanged.connect(_apply_filter)

    btns = QtWidgets.QHBoxLayout()
    btns.addStretch()
    cancel = QtWidgets.QPushButton("Cancel")
    cancel.setStyleSheet(BTN_QUIET)
    cancel.clicked.connect(dlg.reject)
    cancel.setAutoDefault(False)
    ok = QtWidgets.QPushButton("Create Get")
    ok.setStyleSheet(BTN_PRIMARY)
    ok.clicked.connect(dlg.accept)
    ok.setAutoDefault(True)
    ok.setDefault(True)
    btns.addWidget(cancel)
    btns.addWidget(ok)
    lay.addLayout(btns)

    if dlg.exec() != QtWidgets.QDialog.Accepted or tree.currentItem() is None:
        return
    chan = tree.currentItem().data(0, QtCore.Qt.UserRole)
    if not chan:
        return
    near = selection[0] if selection else None
    create_get(chan, near_node=near, at=click_pos)
    _console("Get '{0}' created, linked and hidden.".format(chan))


def rename_channel_dialog(selection):
    """GUI: rename the channel of the selected SET_/GET_ node everywhere."""
    node = None
    for n in (selection or []):
        nm = _node_name(n)
        if _is_mux(n) and (nm.startswith(SET_PREFIX) or _get_channel_of(nm)):
            node = n
            break
    if node is None:
        _console("Rename channel: select a SET_ or GET_ node first.")
        return
    nm = _node_name(node)
    if nm.startswith(SET_PREFIX):
        old = nm[len(SET_PREFIX):]
    else:
        old = _get_channel_of(nm)
    _console("Rename: node '{0}' -> channel '{1}'".format(nm, old))

    QtCore, QtGui, QtWidgets = _qt()

    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle("FORGE — Rename Channel")
    dlg.setMinimumWidth(380)
    dlg.setStyleSheet(FORGE_SS)
    lay = QtWidgets.QVBoxLayout(dlg)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(10)

    lay.addWidget(_header(QtWidgets, "Rename channel '{0}'".format(old)))
    edit = QtWidgets.QLineEdit(old)
    edit.selectAll()
    lay.addWidget(edit)

    warn = _hint(QtWidgets, "")
    warn.setStyleSheet("color: #C0392B; font-size: 11px;")
    lay.addWidget(warn)

    btns = QtWidgets.QHBoxLayout()
    btns.addStretch()
    cancel = QtWidgets.QPushButton("Cancel")
    cancel.setStyleSheet(BTN_QUIET)
    cancel.clicked.connect(dlg.reject)
    cancel.setAutoDefault(False)
    ok = QtWidgets.QPushButton("Rename")
    ok.setStyleSheet(BTN_PRIMARY)
    ok.clicked.connect(dlg.accept)
    ok.setAutoDefault(True)
    ok.setDefault(True)
    btns.addWidget(cancel)
    btns.addWidget(ok)
    lay.addLayout(btns)

    existing = set(_muxes_by_channel(SET_PREFIX)) - {old}

    def _validate():
        new = _sanitize(edit.text())
        if not new:
            warn.setText("Channel name is empty.")
            ok.setEnabled(False)
        elif new in existing:
            warn.setText("Channel '{0}' already exists.".format(new))
            ok.setEnabled(False)
        else:
            warn.setText("")
            ok.setEnabled(True)

    edit.textChanged.connect(_validate)
    _validate()

    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return
    new = _sanitize(edit.text())
    if not new or new == old or new in existing:
        return
    count = rename_channel(old, new)
    if count == 0:
        _console("Rename did NOTHING: no nodes found for channel '{0}'."
                 .format(old))
    else:
        _console("Renamed '{0}' -> '{1}' across {2} node(s).".format(old, new, count))


# --- Flame hooks -----------------------------------------------------------

def _safe(fn):
    """Surface action errors in the Flame console -- the hook system swallows
    exceptions from menu callbacks, which turns bugs into silent no-ops."""
    def wrapped(selection=None):
        try:
            return fn(selection)
        except Exception:
            import traceback
            traceback.print_exc()
            _console("ERROR in {0}: {1}".format(
                getattr(fn, "__name__", "action"),
                traceback.format_exc().strip().splitlines()[-1]))
    wrapped.__name__ = getattr(fn, "__name__", "action")
    return wrapped

def get_batch_custom_ui_actions():
    return [
        {
            "name": "FORGE Wireless",
            "actions": [
                {"name": "Make Set from selected...", "execute": _safe(make_set_dialog)},
                {"name": "Make Get...",               "execute": _safe(make_get_dialog)},
                {"name": "Rename channel...",         "execute": _safe(rename_channel_dialog)},
                {"name": "Relink all",                "execute": _safe(relink)},
            ],
        }
    ]

def batch_setup_loaded(info):
    # Re-resolve links whenever a setup opens, so the wireless routing restores.
    try:
        relink()
    except Exception as e:
        _console("relink on load failed: {0}".format(e))
