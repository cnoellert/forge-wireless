# forge_getset.py
# ---------------------------------------------------------------------------
# Wireless "Get / Set" for Autodesk Flame Batch, in the spirit of ComfyUI's
# Set/Get nodes: relate two nodes by NAME instead of by a visible pipe.
#
# How it works
#   * A "Set" is a MUX node named  SET_<channel>  fed by the real upstream.
#   * A "Get" is a MUX node named  GET_<channel>  whose input is connected
#     (by this script) to the matching Set's output -- then the input link is
#     hidden so no noodle crosses the schematic.
#
# Why MUX + hidden link (and not a data side-channel):
#   The connect_nodes() call makes a REAL connection, so Flame's render and
#   dependency graph stay correct -- no evaluation-order hazard, no frame
#   staleness, no render-farm breakage. Hiding the MUX input link is purely
#   cosmetic (Autodesk: "Hide Input/Output Links of the MUX Node"), so you get
#   the wireless look with none of the correctness problems of a global buffer.
#
# Install
#   Drop this file in a Flame python hooks path, e.g.
#     /opt/Autodesk/shared/python/            (site-wide)
#     ~/.autodesk/<product>/... /python/      (per-user)
#   then Flame  ->  refresh python hooks.  A "Wireless Get/Set" submenu appears
#   on right-click in the Batch schematic.
#
# Usage
#   1. Select a node, right-click -> Wireless Get/Set -> "Make Set from selected".
#      Rename the created MUX's suffix to your channel (e.g. SET_bg).
#   2. Anywhere, "Make Get" and rename its suffix to match (GET_bg).
#   3. "Relink all" wires every Get to its Set and hides the pipes.
#      (Also runs automatically when a batch setup is loaded.)
#
# ! VERIFY ONCE PER SITE  -- two things this script can't confirm from docs:
#   (a) the exact attribute that hides a MUX input link, and
#   (b) the attribute idiom in your Flame version.
#   Run  "Inspect selected node"  from the menu; it prints the node's type and
#   attributes to the console so you can confirm/adjust HIDE_ATTR_CANDIDATES
#   below. If none works, the connect-by-name still happens -- you just flip the
#   MUX "Input" toggle by hand once per Get (it persists in the saved setup).
# ---------------------------------------------------------------------------

import flame

# --- configuration ---------------------------------------------------------

SET_PREFIX = "SET_"
GET_PREFIX = "GET_"
MUX_TYPE   = "Mux"          # node.type string for a MUX node

# Candidate attribute names for "hide this node's input link". The first one
# that exists on the node is used. Confirm/extend via "Inspect selected node".
HIDE_ATTR_CANDIDATES = ("hidden", "input_hidden", "hide_input", "input_link_hidden")


# --- attribute helpers (defensive across Flame versions) -------------------

def _attr_str(value):
    """Read a Flame PyAttribute (or plain value) as a python str."""
    for reader in (lambda v: v.get_value(), lambda v: str(v)):
        try:
            return reader(value)
        except Exception:
            continue
    return ""

def _node_name(node):
    return _attr_str(node.name)

def _node_type(node):
    try:
        return _attr_str(node.type)
    except Exception:
        return ""

def _try_hide_input(node):
    """Best-effort hide of a node's input link. Returns True on success."""
    for attr in HIDE_ATTR_CANDIDATES:
        if hasattr(node, attr):
            try:
                setattr(node, attr, True)
                return True
            except Exception:
                pass
    return False


# --- node discovery --------------------------------------------------------

def _muxes_by_channel(prefix):
    """channel -> [nodes]  for every MUX whose name starts with `prefix`."""
    out = {}
    for n in flame.batch.nodes:
        if _node_type(n) != MUX_TYPE:
            continue
        nm = _node_name(n)
        if nm.startswith(prefix):
            out.setdefault(nm[len(prefix):], []).append(n)
    return out


# --- core actions ----------------------------------------------------------

def relink(selection=None):
    """Wire every GET_<c> to its SET_<c> and hide the Get input links."""
    set_map = _muxes_by_channel(SET_PREFIX)
    get_map = _muxes_by_channel(GET_PREFIX)

    dupes   = sorted(c for c, ns in set_map.items() if len(ns) > 1)
    linked  = 0
    hidden  = 0
    missing = []

    for chan, gets in get_map.items():
        sets = set_map.get(chan)
        if not sets:
            missing.append(chan)
            continue
        src = sets[0]
        for g in gets:
            flame.batch.connect_nodes(src, "Default", g, "Default")
            linked += 1
            if _try_hide_input(g):
                hidden += 1

    msg = "Get/Set: linked {0}, hid {1} pipe(s)".format(linked, hidden)
    if missing:
        msg += " | no Set for: {0}".format(", ".join(sorted(set(missing))))
    if dupes:
        msg += " | DUPLICATE Set channels: {0}".format(", ".join(dupes))
    if linked and hidden == 0:
        msg += " | (couldn't auto-hide -- toggle MUX 'Input' by hand once)"
    _console(msg)


def make_set(selection):
    """Create a SET_ MUX downstream of each selected node and connect it."""
    made = 0
    for n in (selection or []):
        m = flame.batch.create_node(MUX_TYPE)
        m.name  = SET_PREFIX + "channel"      # <- rename the suffix per channel
        try:
            m.pos_x = n.pos_x + 200
            m.pos_y = n.pos_y
        except Exception:
            pass
        flame.batch.connect_nodes(n, "Default", m, "Default")
        made += 1
    _console("Created {0} Set node(s) -- rename the '{1}channel' suffix, "
             "then Relink all.".format(made, SET_PREFIX))


def make_get(selection=None):
    """Create a free-floating GET_ MUX to be linked by name."""
    m = flame.batch.create_node(MUX_TYPE)
    m.name = GET_PREFIX + "channel"           # <- rename to match a Set channel
    _console("Created a Get node -- rename the '{0}channel' suffix to match a "
             "Set, then Relink all.".format(GET_PREFIX))


def inspect_selected(selection):
    """Print a node's type and attributes so you can confirm the hide attr."""
    if not selection:
        _console("Inspect: select a node first (ideally a MUX).")
        return
    n = selection[0]
    attrs = [a for a in dir(n) if not a.startswith("__")]
    _console("Inspect '{0}' type={1}".format(_node_name(n), _node_type(n)))
    _console("Attributes: {0}".format(", ".join(attrs)))
    found = [a for a in HIDE_ATTR_CANDIDATES if hasattr(n, a)]
    _console("Hide-candidate attrs present: {0}".format(found or "NONE -- "
             "look in the list above for a link/hidden/input toggle and add it "
             "to HIDE_ATTR_CANDIDATES."))


# --- console output --------------------------------------------------------

def _console(text):
    try:
        flame.messages.show_in_console("[Get/Set] " + text, "info", 6)
    except Exception:
        print("[Get/Set] " + text)


# --- Flame hooks -----------------------------------------------------------

def get_batch_custom_ui_actions():
    return [
        {
            "name": "Wireless Get/Set",
            "actions": [
                {"name": "Make Set from selected", "execute": make_set},
                {"name": "Make Get",               "execute": make_get},
                {"name": "Relink all",             "execute": relink},
                {"name": "Inspect selected node",  "execute": inspect_selected},
            ],
        }
    ]

def batch_setup_loaded(info):
    # Re-resolve links whenever a setup opens, so the wireless routing restores.
    try:
        relink()
    except Exception as e:
        _console("relink on load failed: {0}".format(e))
