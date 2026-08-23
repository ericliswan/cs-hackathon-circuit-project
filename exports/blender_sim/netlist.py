"""
netlist.py — scene walk → raw netlist, terminal formatting, and the bridge to the backend.

Responsibilities (CONTRACT.md §7 and §9):

* ``build_raw(scene)`` walks the placed items (``sim_role`` in COMPONENT / WIRE / PROBE,
  ordered by ``uid``) and produces the backend's raw list ``[[key, net_a, net_b], ...]``.
* ``format_components(raw)`` / ``print_components(scene, reason)`` render that list exactly
  like the docstring in ``Breadboard_Circuit_Parser.py`` and print it to the terminal.
* ``solve(raw, amplitude, phase_deg, freq_hz)`` mirrors ``Breadboard_Circuit_Parser.__main__``
  (``check_complete`` → ``parse_raw_components`` → ``solve_nodal`` → ``find_output_voltage``)
  and never raises: every failure becomes ``{"ok": False, "reason": ...}``.
* ``report(scene)`` = ``build_raw`` + ``solve`` with the scene's ``bbsim_*`` source props,
  printed as one summary line.

Layout of this module
---------------------
The backend bridge (``load_backend``, ``classify``, ``solve``) and the formatter are pure
Python — ``bpy`` is **only** imported lazily inside the scene-dependent functions
(``get_layout``, ``placed_objects``, ``build_raw``, ``print_components``, ``report``,
``next_uid``) so the module can be imported and unit-tested with the system interpreter
(``tests/test_netlist_format.py``).

The backend files ``AC_Circuit_Solver.py`` / ``Breadboard_Circuit_Parser.py`` live in the
project root (the parent of this package) and are **never modified**.  Blender's Python has
no matplotlib, so a no-op stub is registered in ``sys.modules`` before importing them.
"""
from __future__ import annotations

import cmath
import copy
import importlib
import math
import os
import sys
import types
from typing import Any, Callable, Optional

from .board_layout import (
    BoardLayout,
    DEFAULT_LAYOUT,
    LAYOUT_STANDARD,
    NET_GND,
    NET_VCC,
    RAIL_NETS,
    Net,
    net_repr,
    string_to_net,
)

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
"""Directory that holds the backend scripts (parent of the ``blender_sim`` package)."""

BACKEND_MODULE = "Breadboard_Circuit_Parser"
SOLVER_MODULE = "AC_Circuit_Solver"

PLACED_ROLES = frozenset({"COMPONENT", "WIRE", "PROBE"})
"""``sim_role`` values of student-placed root objects (the ones that form the netlist)."""

BOARD_OBJECT_NAME = "BOARD_breadboard"
UID_PROP = "bbsim_next_uid"

# Result reasons (the exact strings the UI shows after "[BBSIM] circuit: ").
REASON_OK = "closed and valid"
REASON_OPEN = "open circuit — VCC not connected to GND"
REASON_SHORT = "VCC wired directly to GND"
REASON_NO_PROBE = "no probe placed"
REASON_PROBE_SAME_NODE = "probe ends on the same node (reads 0 V)"
REASON_FLOATING = "probe on a floating node"
REASON_ERROR = "solver error"
REASON_SINGULAR = "singular circuit — the source is shorted by an ideal element (zero impedance path)"

# Defaults used when interaction.py has not registered the scene properties.
DEFAULT_AMPLITUDE = 12.0
DEFAULT_PHASE_DEG = 0.0
DEFAULT_FREQ_HZ = 50.0

_PLT_NOOP_NAMES = ("figure", "plot", "xlim", "ylim", "minorticks_on", "grid", "show",
                   "savefig", "close")

# ----------------------------------------------------------------------------
# Backend bridge (bpy-free)
# ----------------------------------------------------------------------------
_BACKEND: Optional[types.ModuleType] = None
_LAYOUT_CACHE: dict[str, BoardLayout] = {}


def _noop(*_args: Any, **_kwargs: Any) -> None:
    """Accept anything, do nothing (stands in for every pyplot call)."""
    return None


class _StubModule(types.ModuleType):
    """A module whose every (non-dunder) attribute is a no-op function.

    Used to satisfy ``import matplotlib.pyplot as plt`` inside ``AC_Circuit_Solver.py`` when
    matplotlib is not installed (Blender's bundled Python).  Dunder lookups still raise so the
    import machinery sees an ordinary module.
    """

    def __getattr__(self, name: str) -> Callable[..., None]:
        if name.startswith("__"):
            raise AttributeError(name)
        return _noop


def make_matplotlib_stub() -> tuple[types.ModuleType, types.ModuleType]:
    """Build (but do not register) stub ``matplotlib`` and ``matplotlib.pyplot`` modules."""
    mpl = _StubModule("matplotlib")
    plt = _StubModule("matplotlib.pyplot")
    for name in _PLT_NOOP_NAMES:
        setattr(plt, name, _noop)
    mpl.pyplot = plt
    mpl.__path__ = []          # marks it as a package so "matplotlib.pyplot" resolves
    return mpl, plt


def install_matplotlib_stub(force: bool = False) -> bool:
    """Register stub matplotlib modules in ``sys.modules`` if the real one is missing.

    Returns True if a stub was installed, False if the real matplotlib is importable
    (or a stub is already in place).  ``force=True`` installs the stub unconditionally.
    """
    if not force:
        try:
            importlib.import_module("matplotlib.pyplot")
            return False
        except ImportError:
            pass
        except Exception:          # a broken install counts as missing
            pass
    mpl, plt = make_matplotlib_stub()
    sys.modules["matplotlib"] = mpl
    sys.modules["matplotlib.pyplot"] = plt
    return True


def load_backend(reload: bool = False) -> types.ModuleType:
    """Import (and cache) ``Breadboard_Circuit_Parser`` from the project root.

    The parser does ``from AC_Circuit_Solver import *`` itself, so its namespace carries
    everything ``solve`` needs (``check_complete``, ``parse_raw_components``, ``node``,
    ``VoltageSource``, ``solve_nodal``, ``find_output_voltage``, ``test`` ...).

    ``reload=True`` re-executes both backend files (handy while editing them).
    """
    global _BACKEND
    if _BACKEND is not None and not reload:
        return _BACKEND
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)
    install_matplotlib_stub()
    if reload and SOLVER_MODULE in sys.modules:
        importlib.reload(sys.modules[SOLVER_MODULE])
    if reload and BACKEND_MODULE in sys.modules:
        _BACKEND = importlib.reload(sys.modules[BACKEND_MODULE])
    else:
        _BACKEND = importlib.import_module(BACKEND_MODULE)
    return _BACKEND


def _as_net(value: Any) -> Net:
    """Normalise a netlist endpoint to a net: ``'VCC'``/``'GND'`` or ``(col, row)``.

    Accepts rail names, ``(col, row)`` tuples/lists, and pin strings such as ``"65c"``.
    """
    if isinstance(value, str):
        return value if value in RAIL_NETS else string_to_net(value)
    col, row = value
    return (int(col), str(row))


def _normalise_degrees(deg: float) -> float:
    """Wrap an angle into the principal range (-180, 180]."""
    wrapped = math.fmod(deg, 360.0)
    if wrapped > 180.0:
        wrapped -= 360.0
    elif wrapped <= -180.0:
        wrapped += 360.0
    return wrapped


def _rails_shorted_by_wires(raw: list, node: Callable[[Any], int]) -> bool:
    """True if WIRE rows alone connect the VCC node to the GND node.

    Union-find over wire rows using the backend's ``node()`` numbering, so it catches a
    direct ``["WIRE", "VCC", "GND"]`` row as well as a chain of wires that ties the rails
    together through a strip column (the solver would otherwise report a misleading result).
    """
    parent: dict[int, int] = {}

    def find(n: int) -> int:
        parent.setdefault(n, n)
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for row in raw:
        if row[0] != "WIRE":
            continue
        a, b = find(node(row[1])), find(node(row[2]))
        if a != b:
            parent[a] = b
    vcc, gnd = node(NET_VCC), node(NET_GND)
    return find(vcc) == find(gnd)


def classify(raw: list) -> Optional[str]:
    """Pre-solve topology check.  Returns a rejection reason or None if solvable.

    * not ``check_complete`` → ``REASON_OPEN``
    * wires tie VCC to GND → ``REASON_SHORT`` (union-find over WIRE rows, so a chain of
      wires through a strip column is caught as well as a direct ``["WIRE","VCC","GND"]``)
    """
    backend = load_backend()
    if not backend.check_complete(raw):
        return REASON_OPEN
    if _rails_shorted_by_wires(raw, backend.node):
        return REASON_SHORT
    return None


def solve(raw: list, amplitude: float, phase_deg: float, freq_hz: float) -> dict:
    """Run the backend on a raw netlist.  Never raises.

    Mirrors ``Breadboard_Circuit_Parser.__main__``: ``check_complete`` →
    ``parse_raw_components`` → append ``VoltageSource("V1", 1, 0, amplitude, phase_deg)`` →
    ``solve_nodal(omega=2πf)`` → ``find_output_voltage(probe, ...)``.

    Returns ``{"ok", "reason", "amp", "phase_deg", "v_nodes", "probe", "error"}`` where
    ``amp`` is the probe voltage magnitude (V), ``phase_deg`` its phase in (-180, 180],
    ``v_nodes`` a plain list of complex nodal voltages (index = backend node number) or None,
    ``probe`` the ``(node_p, node_m)`` pair or None, and ``error`` the exception text when
    ``reason == "solver error"``.
    """
    result: dict = {"ok": False, "reason": REASON_ERROR, "amp": None, "phase_deg": None,
                    "v_nodes": None, "probe": None, "error": None}
    try:
        backend = load_backend()
        rows = copy.deepcopy([list(row) for row in (raw or [])])
        rejection = classify(rows)
        if rejection is not None:
            result["reason"] = rejection
            return result

        comps, probe = backend.parse_raw_components(rows)
        comps.append(backend.VoltageSource("V1", 1, 0, float(amplitude), float(phase_deg)))
        omega = 2.0 * math.pi * float(freq_hz)
        v_nodes_raw, _i_sources = backend.solve_nodal(comps, omega=omega)
        v_nodes = [complex(v) for v in v_nodes_raw]
        result["v_nodes"] = v_nodes
        result["probe"] = tuple(probe) if probe is not None else None

        if probe is None:
            # The parser drops a PROBE row whose two ends merge onto one node, so tell the
            # student that rather than claiming no probe exists.
            has_probe_row = any(row[0] == "PROBE" for row in rows)
            result["reason"] = REASON_PROBE_SAME_NODE if has_probe_row else REASON_NO_PROBE
            return result
        node_p, node_m = int(probe[0]), int(probe[1])
        for idx in (node_p, node_m):
            if idx < 0 or idx >= len(v_nodes) or cmath.isnan(v_nodes[idx]):
                result["reason"] = REASON_FLOATING
                return result

        mag, _omega, phi = backend.find_output_voltage((node_p, node_m), v_nodes, omega)
        result.update(ok=True, reason=REASON_OK, amp=float(mag),
                      phase_deg=_normalise_degrees(math.degrees(phi)))
        return result
    except Exception as exc:          # noqa: BLE001 — the UI must never see an exception
        result["ok"] = False
        # solve_nodal raises ValueError("singular matrix") when an ideal zero-impedance branch
        # (a wire chain the parser could not merge, or an inductor at ~DC) shorts the source.
        result["reason"] = REASON_SINGULAR if "singular" in str(exc).lower() else REASON_ERROR
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


# ----------------------------------------------------------------------------
# Formatting (bpy-free)
# ----------------------------------------------------------------------------
def format_components(raw: list) -> str:
    """Render a raw list exactly like the parser docstring::

        components = [
            ["WIRE", 'VCC', (1,"b")],
            ["RESISTOR1", (2,"c"), (4,"c")],
        ]

    An empty list renders as ``components = []``.  Endpoints may be nets or pin strings.
    """
    rows = list(raw or [])
    if not rows:
        return "components = []"
    lines = ["components = ["]
    for row in rows:
        key, a, b = row[0], _as_net(row[1]), _as_net(row[2])
        lines.append(f'    ["{key}", {net_repr(a)}, {net_repr(b)}],')
    lines.append("]")
    return "\n".join(lines)


def format_report_line(result: dict, amplitude: float, phase_deg: float, freq_hz: float) -> str:
    """The one-line circuit summary printed by ``report`` (CONTRACT §9)."""
    line = f"[BBSIM] circuit: {result['reason']}"
    if result.get("ok"):
        line += (f" | Vout = {result['amp']:.4f} V ∠ {result['phase_deg']:.1f}°"
                 f"  @ {freq_hz:g} Hz  (source {amplitude:g} V ∠ {phase_deg:g}°)")
    elif result.get("error"):
        line += f" ({result['error']})"
    return line


# ----------------------------------------------------------------------------
# Scene access (bpy imported lazily)
# ----------------------------------------------------------------------------
def _scene_objects(scene: Any):
    """Objects to search: the scene's if given, else everything in ``bpy.data``."""
    import bpy
    return bpy.data.objects if scene is None else scene.objects


def get_layout(scene: Any) -> BoardLayout:
    """The board layout: rebuilt from ``BOARD_breadboard``'s props, else ``DEFAULT_LAYOUT``.

    Layouts are cached by name so repeated calls (every mouse move) are free.
    """
    import bpy
    board = _scene_objects(scene).get(BOARD_OBJECT_NAME) or bpy.data.objects.get(BOARD_OBJECT_NAME)
    if board is None:
        return DEFAULT_LAYOUT
    params = {k: board[k] for k in ("layout", "pitch", "columns", "rows", "board_top_z")
              if k in board.keys()}
    name = str(params.get("layout", LAYOUT_STANDARD))
    layout = _LAYOUT_CACHE.get(name)
    if layout is None:
        try:
            layout = BoardLayout.from_params(params)
        except ValueError:
            layout = DEFAULT_LAYOUT
        _LAYOUT_CACHE[name] = layout
    return layout


def _is_placed_root(obj: Any) -> bool:
    """True for an object that carries a placed-item ``sim_role`` and has no such ancestor."""
    if obj.get("sim_role") not in PLACED_ROLES:
        return False
    parent = obj.parent
    while parent is not None:
        if parent.get("sim_role") in PLACED_ROLES:
            return False
        parent = parent.parent
    return True


def placed_objects(scene: Any) -> list:
    """Root objects of everything the student placed, sorted by ``uid`` (then name)."""
    import bpy
    roots = [o for o in bpy.data.objects if _is_placed_root(o)]
    roots.sort(key=lambda o: (int(o.get("uid", 0)), o.name))
    return roots


def build_raw(scene: Any) -> list:
    """The backend's raw list: ``[[comp_type, net_a, net_b], ...]`` in placement order.

    Raises ``ValueError`` for an object whose pin strings are malformed (``report`` turns
    that into a ``solver error`` result rather than letting it reach the UI).
    """
    rows: list = []
    for obj in placed_objects(scene):
        comp_type = obj.get("comp_type") or obj.get("sim_role")
        if comp_type not in ("WIRE", "PROBE") and obj.get("comp_type") is None:
            raise ValueError(f"{obj.name}: placed COMPONENT without a comp_type property")
        try:
            net_a = string_to_net(str(obj["pin_a"]))
            net_b = string_to_net(str(obj["pin_b"]))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{obj.name}: bad pin properties ({exc})") from exc
        rows.append([str(comp_type), net_a, net_b])
    return rows


def print_components(scene: Any, reason: str = "") -> str:
    """Print ``[BBSIM] components (N) — <reason>`` plus the formatted list; return the text."""
    raw = build_raw(scene)
    header = f"[BBSIM] components ({len(raw)})"
    if reason:
        header += f" — {reason}"
    text = header + "\n" + format_components(raw)
    print(text)
    return text


def report(scene: Any) -> dict:
    """``build_raw`` + ``solve`` with the scene's source props; print one summary line.

    Reads ``scene.bbsim_amplitude`` / ``bbsim_phase`` / ``bbsim_frequency`` (defaults 12 V,
    0°, 50 Hz when interaction.py has not registered them).  Returns the ``solve`` dict with
    an extra ``"summary"`` key holding the printed line (handy for the HUD / status text).
    """
    amplitude = float(getattr(scene, "bbsim_amplitude", DEFAULT_AMPLITUDE))
    phase_deg = float(getattr(scene, "bbsim_phase", DEFAULT_PHASE_DEG))
    freq_hz = float(getattr(scene, "bbsim_frequency", DEFAULT_FREQ_HZ))
    try:
        raw = build_raw(scene)
    except Exception as exc:          # noqa: BLE001 — malformed props must not reach the UI
        result = {"ok": False, "reason": REASON_ERROR, "amp": None, "phase_deg": None,
                  "v_nodes": None, "probe": None, "error": f"{type(exc).__name__}: {exc}"}
    else:
        result = solve(raw, amplitude, phase_deg, freq_hz)
    line = format_report_line(result, amplitude, phase_deg, freq_hz)
    print(line)
    result["summary"] = line
    return result


def next_uid(scene: Any) -> int:
    """Increment and return the scene's placement counter (``scene["bbsim_next_uid"]``)."""
    uid = int(scene.get(UID_PROP, 0)) + 1
    scene[UID_PROP] = uid
    return uid


__all__ = [
    "PROJECT_ROOT", "PLACED_ROLES",
    "REASON_OK", "REASON_OPEN", "REASON_SHORT", "REASON_NO_PROBE", "REASON_PROBE_SAME_NODE",
    "REASON_FLOATING", "REASON_ERROR", "REASON_SINGULAR",
    "make_matplotlib_stub", "install_matplotlib_stub", "load_backend", "classify", "solve",
    "format_components", "format_report_line",
    "get_layout", "placed_objects", "build_raw", "print_components", "report", "next_uid",
]
