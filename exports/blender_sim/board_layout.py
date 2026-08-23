"""
board_layout.py — the breadboard coordinate anchor (spec §5.3).

Pure Python (NO bpy import) so it can be unit-tested with the system Python and
imported both by the Blender scene builder and by the runtime interaction layer.

Units: millimetres.  The Blender scene is configured so 1 Blender unit = 1 mm.

Physical vs logical layout
--------------------------
The board shows two halves ("LHS" and "RHS"), each numbered 1..60 with rows a..e.
The backend (`Breadboard_Circuit_Parser.py`) wants a single logical column range:

    LHS physical column c  ->  logical c          (1..60)
    RHS physical column c  ->  logical c + 60     (61..120)

This module emits logical columns directly, so nothing downstream redoes the
transform.  Power rails are the two fixed nets "VCC" and "GND"; every hole on a
VCC rail reports the net name "VCC" (likewise "GND"), which is exactly what the
parser's `node()` expects.

World frame (matches scene_builder.py)
--------------------------------------
* Board centred on the origin, top surface at z = 0 (BOARD_TOP_Z).
* Columns run along +Y; column 1 is at the most negative Y (nearest the camera).
* Rows run along X.  LAYOUT "STANDARD" puts the LHS strip at negative X and the RHS
  strip at positive X, flanking the centre channel at X = 0 — i.e. a real
  breadboard seen in portrait orientation.  Both halves read "a b c d e" from
  left to right.  LAYOUT "END_TO_END" instead lays all 120 logical columns along
  one line (LHS then a gap then RHS).
* Power rails: a VCC(+) / GND(-) pair on each outer edge, arranged ``+ − | + −`` like the
  reference board (left: VCC outer / GND inner; right: VCC inner / GND outer).

Hole keys
---------
Strip hole:  key = (logical_col, row)         net = (logical_col, row)
Rail hole:   key = ("VCC"|"GND", side, i)      net = "VCC" | "GND"

Pin strings (stored in Blender custom properties, which cannot hold mixed lists):
    "65c"  -> (65, "c")          "VCC" -> "VCC"          "GND" -> "GND"
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Union

# ----------------------------------------------------------------------------
# Constants (real 830-tie-point breadboard proportions, 0.1" pitch)
# ----------------------------------------------------------------------------
PITCH = 2.54                 # hole pitch (mm)
COLUMNS_PER_HALF = 60        # physical columns per half
ROWS: tuple[str, ...] = ("a", "b", "c", "d", "e")
RHS_OFFSET = 60              # logical column offset for the RHS half
CHANNEL = 7.62               # centre channel: LHS row e -> RHS row a centre distance (0.3")
RAIL_GAP = 7.62              # inner rail -> nearest terminal row (centre-to-centre)
RAIL_PAIR = 2.54             # spacing between the + and - rail of a pair
RAIL_GROUP = 5               # rail holes come in groups of 5 ...
RAIL_GROUP_STRIDE = 6        # ... with one empty pitch between groups
BOARD_TOP_Z = 0.0            # z of the board's top surface (hole plane)
HOLE_DEPTH = 1.6             # how far a pin tip is sunk below the surface
HOLE_SIZE = 1.0              # square hole side (mm)
BOARD_THICKNESS = 8.5
END_MARGIN = 6.35            # board material beyond the first/last column
SIDE_MARGIN = 3.5            # board material beyond the outer rail
END_TO_END_GAP = 3 * PITCH   # col 60 -> col 61 centre distance in END_TO_END layout

LAYOUT_STANDARD = "STANDARD"
LAYOUT_END_TO_END = "END_TO_END"

NET_VCC = "VCC"
NET_GND = "GND"
RAIL_NETS = (NET_VCC, NET_GND)

Net = Union[str, tuple]          # "VCC" | "GND" | (col, row)
PinStr = str                     # "VCC" | "GND" | "65c"

_PIN_RE = re.compile(r"^(\d{1,3})([a-e])$")


@dataclass(frozen=True)
class Hole:
    """One physical hole on the board."""
    key: tuple                   # unique physical key, see module docstring
    net: Net                     # what the netlist row should contain
    x: float
    y: float
    z: float = BOARD_TOP_Z
    kind: str = "STRIP"          # "STRIP" | "RAIL"
    side: str = "L"              # "L" (LHS half / left rails) | "R"

    @property
    def pin(self) -> PinStr:
        return net_to_string(self.net)

    @property
    def xyz(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    @property
    def is_rail(self) -> bool:
        return self.kind == "RAIL"


# ----------------------------------------------------------------------------
# Pin / net string conversions
# ----------------------------------------------------------------------------
def net_to_string(net: Net) -> PinStr:
    """(65, "c") -> "65c";  "VCC" -> "VCC"."""
    if isinstance(net, str):
        if net in RAIL_NETS:
            return net
        if _PIN_RE.match(net):
            return net
        raise ValueError(f"not a net: {net!r}")
    col, row = net
    return f"{int(col)}{row}"


def string_to_net(pin: PinStr) -> Net:
    """"65c" -> (65, "c");  "VCC" -> "VCC"."""
    pin = str(pin).strip()
    if pin in RAIL_NETS:
        return pin
    m = _PIN_RE.match(pin)
    if not m:
        raise ValueError(f"bad pin string {pin!r}")
    return (int(m.group(1)), m.group(2))


def net_repr(net: Net) -> str:
    """Render a net exactly like the parser docstring: 'VCC' or (65,"c")."""
    if isinstance(net, str):
        return f"'{net}'"
    col, row = net
    return f'({int(col)},"{row}")'


def physical_from_logical(col: int) -> tuple[str, int]:
    """logical 1..120 -> ("L"|"R", physical 1..60)."""
    col = int(col)
    if 1 <= col <= COLUMNS_PER_HALF:
        return ("L", col)
    if COLUMNS_PER_HALF < col <= 2 * COLUMNS_PER_HALF:
        return ("R", col - RHS_OFFSET)
    raise ValueError(f"logical column out of range: {col}")


def logical_from_physical(side: str, col: int) -> int:
    col = int(col)
    if not 1 <= col <= COLUMNS_PER_HALF:
        raise ValueError(f"physical column out of range: {col}")
    return col if side == "L" else col + RHS_OFFSET


# ----------------------------------------------------------------------------
# Layout
# ----------------------------------------------------------------------------
class BoardLayout:
    """Computes every hole position for a given layout style.

    All geometry the scene builder and the runtime need comes from here so that
    the rendered board and the click-snapping table can never disagree.
    """

    def __init__(self, layout: str = LAYOUT_STANDARD, columns: int = COLUMNS_PER_HALF):
        """`columns` = physical columns per half (60 per the spec; 30 for a 400-point board).
        The RHS logical offset stays 60 regardless, as the backend expects."""
        if layout not in (LAYOUT_STANDARD, LAYOUT_END_TO_END):
            raise ValueError(layout)
        if not 1 <= int(columns) <= COLUMNS_PER_HALF:
            raise ValueError(f"columns must be 1..{COLUMNS_PER_HALF}, got {columns}")
        self.layout = layout
        self.pitch = PITCH
        self.columns = int(columns)
        self.rows = ROWS
        self._holes: list[Hole] = []
        self._by_key: dict = {}
        self._build()

    # -- column / row axes ---------------------------------------------------
    def column_y(self, logical_col: int) -> float:
        """Y coordinate of a logical column (1..120)."""
        side, pcol = physical_from_logical(logical_col)
        if self.layout == LAYOUT_STANDARD:
            # both halves share the same column axis
            return (pcol - 1) * PITCH - (self.columns - 1) * PITCH / 2.0
        # END_TO_END: LHS then gap then RHS along +Y, centred as a whole
        span = (2 * self.columns - 2) * PITCH + END_TO_END_GAP
        y0 = -span / 2.0
        if side == "L":
            return y0 + (pcol - 1) * PITCH
        return y0 + (self.columns - 1) * PITCH + END_TO_END_GAP + (pcol - 1) * PITCH

    def row_x(self, side: str, row: str) -> float:
        """X coordinate of a row letter on a given half."""
        ri = ROWS.index(row)
        if self.layout == LAYOUT_STANDARD:
            if side == "L":      # a (outer, most negative) ... e (inner)
                return -(CHANNEL / 2.0) - (4 - ri) * PITCH
            return (CHANNEL / 2.0) + ri * PITCH        # a (inner) ... e (outer)
        # END_TO_END: single strip centred on x = 0, a..e from -X to +X
        return (ri - 2) * PITCH

    # -- rails ---------------------------------------------------------------
    def rail_x(self, side: str, net: str) -> float:
        """X of a rail line.

        Rail pairs run ``+ − | + −`` across the board like the reference breadboard
        (``Breadboard model example.png``): on the left side VCC(+) is the OUTER rail and
        GND(−) the inner one; on the right side VCC(+) is the INNER rail and GND(−) the
        outer one.  Use :meth:`rail_is_outer` rather than assuming which is which.
        """
        if self.layout == LAYOUT_STANDARD:
            inner_L = self.row_x("L", "a") - RAIL_GAP
            inner_R = self.row_x("R", "e") + RAIL_GAP
        else:
            inner_L = self.row_x("L", "a") - RAIL_GAP
            inner_R = self.row_x("L", "e") + RAIL_GAP
        if side == "L":
            return inner_L if net == NET_GND else inner_L - RAIL_PAIR
        return inner_R if net == NET_VCC else inner_R + RAIL_PAIR

    def rail_is_outer(self, side: str, net: str) -> bool:
        """True if this rail is the outermost one of its pair (VCC on the left, GND on the right)."""
        return net == (NET_VCC if side == "L" else NET_GND)

    def outer_rail_net(self, side: str) -> str:
        return NET_VCC if side == "L" else NET_GND

    def rail_column_indices(self) -> list[int]:
        """Physical column indices (1-based) that carry a rail hole (groups of 5)."""
        return [c for c in range(1, self.columns + 1) if c % RAIL_GROUP_STRIDE != 0]

    def rail_ys(self) -> list[float]:
        """Y of every rail hole along the board (both halves in END_TO_END)."""
        if self.layout == LAYOUT_STANDARD:
            return [self.column_y(c) for c in self.rail_column_indices()]
        ys = [self.column_y(c) for c in self.rail_column_indices()]
        ys += [self.column_y(c + RHS_OFFSET) for c in self.rail_column_indices()]
        return ys

    # -- extents -------------------------------------------------------------
    @property
    def x_min(self) -> float:
        return self.rail_x("L", NET_VCC) - SIDE_MARGIN

    @property
    def x_max(self) -> float:
        return self.rail_x("R", self.outer_rail_net("R")) + SIDE_MARGIN

    @property
    def y_min(self) -> float:
        return self.column_y(1) - END_MARGIN

    @property
    def y_max(self) -> float:
        last = self.columns if self.layout == LAYOUT_STANDARD else 2 * self.columns
        return self.column_y(last) + END_MARGIN

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def length(self) -> float:
        return self.y_max - self.y_min

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.x_min + self.x_max) / 2.0, (self.y_min + self.y_max) / 2.0)

    def channel_x_range(self) -> Optional[tuple[float, float]]:
        """X extent of the centre channel groove (STANDARD only)."""
        if self.layout != LAYOUT_STANDARD:
            return None
        half = (CHANNEL - PITCH) / 2.0
        return (-half, half)

    # -- hole table ----------------------------------------------------------
    def _build(self) -> None:
        holes: list[Hole] = []
        for side in ("L", "R"):
            for pcol in range(1, self.columns + 1):
                lcol = logical_from_physical(side, pcol)
                y = self.column_y(lcol)
                for row in ROWS:
                    x = self.row_x(side, row)
                    holes.append(Hole(key=(lcol, row), net=(lcol, row), x=x, y=y,
                                      z=BOARD_TOP_Z, kind="STRIP", side=side))
        for side in ("L", "R"):
            for net in RAIL_NETS:
                x = self.rail_x(side, net)
                for i, y in enumerate(self.rail_ys()):
                    holes.append(Hole(key=(net, side, i), net=net, x=x, y=y,
                                      z=BOARD_TOP_Z, kind="RAIL", side=side))
        self._holes = holes
        self._by_key = {h.key: h for h in holes}
        self._xy = None  # lazy numpy cache

    @property
    def holes(self) -> Sequence[Hole]:
        return self._holes

    def strip_holes(self) -> list[Hole]:
        return [h for h in self._holes if h.kind == "STRIP"]

    def rail_holes(self, net: Optional[str] = None, side: Optional[str] = None) -> list[Hole]:
        return [h for h in self._holes if h.kind == "RAIL"
                and (net is None or h.net == net) and (side is None or h.side == side)]

    def hole(self, key) -> Hole:
        """Look up by physical key, by net tuple (col,row), or by pin string.

        For rail nets ("VCC"/"GND") the first hole of that rail on the left side
        is returned (any hole on the rail is electrically identical)."""
        if isinstance(key, str):
            if key in RAIL_NETS:
                return self.rail_holes(net=key, side="L")[0]
            key = string_to_net(key)
        if isinstance(key, (tuple, list)):
            key = tuple(key)
            if len(key) == 2:
                key = (int(key[0]), str(key[1]))
        return self._by_key[key]

    def nearest(self, x: float, y: float, tol: float = 0.48 * PITCH) -> Optional[Hole]:
        """Nearest hole to a point on the board plane, or None if farther than tol."""
        try:
            import numpy as np
            if self._xy is None:
                self._xy = np.array([(h.x, h.y) for h in self._holes])
            d2 = (self._xy[:, 0] - x) ** 2 + (self._xy[:, 1] - y) ** 2
            i = int(d2.argmin())
            return self._holes[i] if d2[i] <= tol * tol else None
        except ImportError:      # pure-python fallback
            best, best_d2 = None, tol * tol
            for h in self._holes:
                d2 = (h.x - x) ** 2 + (h.y - y) ** 2
                if d2 <= best_d2:
                    best, best_d2 = h, d2
            return best

    # -- serialisation -------------------------------------------------------
    def to_params(self) -> dict:
        """Small parameter dict stored on the board object so the runtime can
        rebuild an identical layout (cheaper and safer than storing 700 holes)."""
        return {"layout": self.layout, "pitch": PITCH, "columns": self.columns,
                "rows": "".join(ROWS), "board_top_z": BOARD_TOP_Z}

    @classmethod
    def from_params(cls, params: dict) -> "BoardLayout":
        return cls(layout=str(params.get("layout", LAYOUT_STANDARD)),
                   columns=int(params.get("columns", COLUMNS_PER_HALF)))

    def export_table(self) -> dict:
        """JSON-able table: {"65c": [x,y,z], ..., "rails": {"VCC": [[x,y,z],...], "GND": [...]}}"""
        table: dict = {"layout": self.layout, "units": "mm", "board_top_z": BOARD_TOP_Z,
                       "strip": {}, "rails": {NET_VCC: [], NET_GND: []}}
        for h in self._holes:
            if h.kind == "STRIP":
                table["strip"][h.pin] = [round(h.x, 4), round(h.y, 4), round(h.z, 4)]
            else:
                table["rails"][h.net].append([round(h.x, 4), round(h.y, 4), round(h.z, 4)])
        return table


DEFAULT_LAYOUT = BoardLayout(LAYOUT_STANDARD)


def span_mm(a: Hole, b: Hole) -> float:
    return math.dist((a.x, a.y), (b.x, b.y))


if __name__ == "__main__":
    import json
    for name in (LAYOUT_STANDARD, LAYOUT_END_TO_END):
        L = BoardLayout(name)
        print(f"{name}: {len(L.holes)} holes, board {L.width:.1f} x {L.length:.1f} mm, "
              f"x[{L.x_min:.1f},{L.x_max:.1f}] y[{L.y_min:.1f},{L.y_max:.1f}]")
        print("  (5,c) ->", L.hole((5, "c")).xyz, " (65,c) ->", L.hole("65c").xyz,
              " VCC ->", L.hole("VCC").xyz)
    json.dump(DEFAULT_LAYOUT.export_table(), open("breadboard_holes.json", "w"), indent=1)
