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
#     schematic.
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
    channel = re.sub(r"[^A-Za-z0-9_\-]+", "_", channel.strip())
    return channel.strip("_")

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
    m = flame.batch.create_node(MUX_CREATE)
    m.name = SET_PREFIX + channel
    m.schematic_colour = colour
    try:
        m.pos_x = source_node.pos_x + 220
        m.pos_y = source_node.pos_y
    except Exception:
        pass
    rgb, matte = _source_outputs(source_node)
    flame.batch.connect_nodes(source_node, rgb, m, "Input_0")
    if matte:
        flame.batch.connect_nodes(source_node, matte, m, "Matte_0")
    return m

def create_get(channel, near_node=None):
    """Create GET_<channel>, wire it to its Set, tint it, hide the pipe."""
    m = flame.batch.create_node(MUX_CREATE)
    m.name = GET_PREFIX + channel
    if near_node is not None:
        try:
            m.pos_x = near_node.pos_x
            m.pos_y = near_node.pos_y + 180
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
    """Re-wire every GET_<c> to SET_<c>, reassert colours, hide the pipes."""
    set_map = _muxes_by_channel(SET_PREFIX)
    get_map = _muxes_by_channel(GET_PREFIX)
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
    """Rename a channel across all of its Set and Get nodes."""
    renamed = 0
    for prefix in (SET_PREFIX, GET_PREFIX):
        for n in _muxes_by_channel(prefix).get(old, []):
            n.name = prefix + new
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
    """GUI: create a coloured SET_ MUX downstream of the selected node."""
    selection = [n for n in (selection or [])]
    if not selection:
        _console("Make Set: select the node you want to broadcast first.")
        return
    src = selection[0]
    if len(selection) > 1:
        _console("Make Set: multiple nodes selected -- using '{0}'."
                 .format(_node_name(src)))

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
    name_edit = QtWidgets.QLineEdit(_sanitize(_node_name(src)).lower())
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
    ok = QtWidgets.QPushButton("Create Set")
    ok.setStyleSheet(BTN_PRIMARY)
    ok.clicked.connect(dlg.accept)
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
    create_set(src, chan, PALETTE[chosen["idx"]][1])
    _console("Set '{0}' created ({1}). Drop Gets anywhere -- they link "
             "by name.".format(chan, PALETTE[chosen["idx"]][0]))


def make_get_dialog(selection):
    """GUI: pick an existing channel, create a tinted, pre-linked GET_ MUX."""
    set_map = _muxes_by_channel(SET_PREFIX)
    if not set_map:
        _console("Make Get: no Set nodes yet -- create a Set first.")
        return
    get_map = _muxes_by_channel(GET_PREFIX)

    QtCore, QtGui, QtWidgets = _qt()

    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle("FORGE — Wireless Get")
    dlg.setMinimumSize(360, 320)
    dlg.setStyleSheet(FORGE_SS)
    lay = QtWidgets.QVBoxLayout(dlg)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(10)

    lay.addWidget(_header(QtWidgets, "Create Get"))
    lay.addWidget(_hint(QtWidgets, "Channel to receive:"))

    lst = QtWidgets.QListWidget()
    for chan in sorted(set_map):
        colour = _set_colour(set_map[chan][0])
        n_gets = len(get_map.get(chan, []))
        item = QtWidgets.QListWidgetItem(
            "{0}   ({1} get{2})".format(chan, n_gets, "" if n_gets == 1 else "s"))
        if colour:
            item.setIcon(_swatch_icon(QtGui, colour))
        item.setData(QtCore.Qt.UserRole, chan)
        lst.addItem(item)
    lst.setCurrentRow(0)
    lst.itemDoubleClicked.connect(lambda _item: dlg.accept())
    lay.addWidget(lst, 1)

    btns = QtWidgets.QHBoxLayout()
    btns.addStretch()
    cancel = QtWidgets.QPushButton("Cancel")
    cancel.setStyleSheet(BTN_QUIET)
    cancel.clicked.connect(dlg.reject)
    ok = QtWidgets.QPushButton("Create Get")
    ok.setStyleSheet(BTN_PRIMARY)
    ok.clicked.connect(dlg.accept)
    btns.addWidget(cancel)
    btns.addWidget(ok)
    lay.addLayout(btns)

    if dlg.exec() != QtWidgets.QDialog.Accepted or lst.currentItem() is None:
        return
    chan = lst.currentItem().data(QtCore.Qt.UserRole)
    near = selection[0] if selection else None
    create_get(chan, near_node=near)
    _console("Get '{0}' created, linked and hidden.".format(chan))


def rename_channel_dialog(selection):
    """GUI: rename the channel of the selected SET_/GET_ node everywhere."""
    node = None
    for n in (selection or []):
        nm = _node_name(n)
        if _is_mux(n) and (nm.startswith(SET_PREFIX) or nm.startswith(GET_PREFIX)):
            node = n
            break
    if node is None:
        _console("Rename channel: select a SET_ or GET_ node first.")
        return
    nm = _node_name(node)
    old = nm[len(SET_PREFIX):] if nm.startswith(SET_PREFIX) else nm[len(GET_PREFIX):]

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

    btns = QtWidgets.QHBoxLayout()
    btns.addStretch()
    cancel = QtWidgets.QPushButton("Cancel")
    cancel.setStyleSheet(BTN_QUIET)
    cancel.clicked.connect(dlg.reject)
    ok = QtWidgets.QPushButton("Rename")
    ok.setStyleSheet(BTN_PRIMARY)
    ok.clicked.connect(dlg.accept)
    btns.addWidget(cancel)
    btns.addWidget(ok)
    lay.addLayout(btns)

    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return
    new = _sanitize(edit.text())
    if not new or new == old:
        return
    count = rename_channel(old, new)
    _console("Renamed '{0}' -> '{1}' across {2} node(s).".format(old, new, count))


# --- Flame hooks -----------------------------------------------------------

def get_batch_custom_ui_actions():
    return [
        {
            "name": "FORGE Wireless",
            "actions": [
                {"name": "Make Set from selected...", "execute": make_set_dialog},
                {"name": "Make Get...",               "execute": make_get_dialog},
                {"name": "Rename channel...",         "execute": rename_channel_dialog},
                {"name": "Relink all",                "execute": relink},
            ],
        }
    ]

def batch_setup_loaded(info):
    # Re-resolve links whenever a setup opens, so the wireless routing restores.
    try:
        relink()
    except Exception as e:
        _console("relink on load failed: {0}".format(e))
