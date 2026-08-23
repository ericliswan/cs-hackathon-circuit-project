"""
component_meshes.py — procedural meshes for everything a student can place on the board
(resistors, capacitors, inductors, jumper wires, the oscilloscope probe).

Public API (CONTRACT.md §6):

    COMPONENT_INFO                      labels / values / kinds for the 12 mode keys
    spawn_component(scene, comp_type, hole_a, hole_b, uid, collection=None) -> Object
    remove_component(obj) -> None
    placed_root(obj) -> Object | None

Conventions
-----------
* Units are millimetres (1 BU = 1 mm); the hole plane is z = 0 (``board_layout.BOARD_TOP_Z``).
* Every placed item is ONE mesh object with several material slots (``face.material_index``)
  so ray-casting, selection and deletion are trivial.  The object origin is at the chord
  midpoint on the board plane; the mesh vertices are stored relative to it.
* Pin tips are swept as Ø0.6 tubes whose first ring is centred exactly on ``(hole.x, hole.y)``
  at ``z = -HOLE_DEPTH``, so the geometry always reaches both holes.
* Geometry is built with ``bmesh`` only (no ``bpy.ops``) via the helpers in ``bpy_utils`` —
  it works headless and from inside a modal operator.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import materials
from .board_layout import HOLE_DEPTH, Hole, span_mm
from .bpy_utils import (arc_points, bezier, bm_box, bm_cylinder, bm_tube, finish_bmesh,
                        get_collection, helix_points, mesh_from_bmesh, new_object,
                        remove_object_tree, set_props)

# ----------------------------------------------------------------------------
# Palette of placeable items (labels / values are the single source of truth)
# ----------------------------------------------------------------------------
COMPONENT_INFO: dict[str, dict] = {
    "RESISTOR1": {"label": "R 250Ω", "kind": "RESISTOR", "value": 250.0, "unit": "Ω",
                  "bands": ("red", "green", "brown"), "accent": "accent_resistor"},
    "RESISTOR2": {"label": "R 500Ω", "kind": "RESISTOR", "value": 500.0, "unit": "Ω",
                  "bands": ("green", "black", "brown"), "accent": "accent_resistor"},
    "RESISTOR3": {"label": "R 1kΩ", "kind": "RESISTOR", "value": 1000.0, "unit": "Ω",
                  "bands": ("brown", "black", "red"), "accent": "accent_resistor"},
    "CAPACITOR1": {"label": "C 0.47µF", "kind": "CAPACITOR", "value": 0.47e-6, "unit": "F",
                   "accent": "accent_capacitor"},
    "CAPACITOR2": {"label": "C 47µF", "kind": "CAPACITOR", "value": 47e-6, "unit": "F",
                   "accent": "accent_capacitor"},
    "CAPACITOR3": {"label": "C 470µF", "kind": "CAPACITOR", "value": 470e-6, "unit": "F",
                   "accent": "accent_capacitor"},
    "INDUCTOR1": {"label": "L 2.2µH", "kind": "INDUCTOR", "value": 2.2e-6, "unit": "H",
                  "accent": "accent_inductor"},
    "INDUCTOR2": {"label": "L 220µH", "kind": "INDUCTOR", "value": 220e-6, "unit": "H",
                  "accent": "accent_inductor"},
    "INDUCTOR3": {"label": "L 220mH", "kind": "INDUCTOR", "value": 220e-3, "unit": "H",
                  "accent": "accent_inductor"},
    "WIRE": {"label": "Jumper Wire", "kind": "WIRE", "value": None, "unit": "",
             "accent": "accent_wire"},
    "PROBE": {"label": "Probe", "kind": "PROBE", "value": None, "unit": "",
              "accent": "accent_probe"},
    "DELETE": {"label": "Delete", "kind": "DELETE", "value": None, "unit": "",
               "accent": "accent_delete"},
}

PLACED_ROLES = frozenset({"COMPONENT", "WIRE", "PROBE"})
COMPONENTS_COLLECTION = "SIM_Components"

# ----------------------------------------------------------------------------
# Geometry constants (mm)
# ----------------------------------------------------------------------------
LEAD_RADIUS = 0.3          # Ø0.6 component leads
LEAD_Z = 3.5               # height of the horizontal lead run above the board
LEAD_BEND_RADIUS = 0.8     # rounding radius of lead corners
WIRE_RADIUS = 0.5          # Ø1.0 jumper insulation
PROBE_CABLE_END = Vector((120.0, 0.0, 25.0))   # where the scope cable will be picked up later
PROBE_LEAN_DEG = 35.0

_RESISTOR_BODY_RADIUS = 1.2
_RESISTOR_BAND_RADIUS = 1.275
_RESISTOR_BAND_WIDTH = 0.45
_RESISTOR_STANDING_SPAN = 5.0     # below this span the resistor stands on end

# (core radius, nominal core length, turns)
_INDUCTOR_SIZES = {
    "INDUCTOR1": (0.8, 4.0, 7),
    "INDUCTOR2": (1.1, 6.0, 8),
    "INDUCTOR3": (1.6, 8.0, 10),
}
_COIL_WIRE_RADIUS = 0.35

# (radius, height, body palette key, lead spacing)
_CAN_SIZES = {
    "CAPACITOR2": (2.5, 7.0, "cap_blue", 2.0),
    "CAPACITOR3": (4.0, 11.0, "cap_black", 3.5),
}
_CAN_BOTTOM_Z = 2.5

# jumper colour by span, like a real jumper kit
_WIRE_COLOURS = ((5.0, "wire_red"), (10.0, "wire_yellow"), (20.0, "wire_green"),
                 (40.0, "wire_blue"), (math.inf, "wire_black"))


# ----------------------------------------------------------------------------
# Materials
# ----------------------------------------------------------------------------
def _material_props(key: str) -> dict:
    """PBR parameters for a PALETTE key (metals vs plastics vs ferrite)."""
    if key == "lead_silver":
        return {"metallic": 1.0, "roughness": 0.3}
    if key == "copper":
        return {"metallic": 1.0, "roughness": 0.35}
    if key == "ferrite":
        return {"roughness": 0.85}
    if key == "resistor_tan" or key == "cap_ceramic":
        return {"roughness": 0.65}
    if key.startswith("band_"):
        return {"roughness": 0.5, "metallic": 0.6 if key == "band_gold" else 0.0}
    if key.startswith("cap_"):
        return {"roughness": 0.45}
    return {"roughness": 0.4}


class _Slots:
    """Lazily builds the ordered material list of one mesh; ``slots('copper')`` -> slot index."""

    def __init__(self) -> None:
        self.materials: list[bpy.types.Material] = []
        self._index: dict[str, int] = {}

    def __call__(self, key: str) -> int:
        if key not in self._index:
            self._index[key] = len(self.materials)
            self.materials.append(materials.palette_material(key, **_material_props(key)))
        return self._index[key]


# ----------------------------------------------------------------------------
# Small geometry helpers
# ----------------------------------------------------------------------------
def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _flat_ngons(faces: Iterable[bmesh.types.BMFace]) -> None:
    """Cap n-gons of cylinders/tubes shade flat so the curved sides keep clean rims."""
    for f in faces:
        if len(f.verts) > 4:
            f.smooth = False


def _cylinder(bm, p0, p1, radius, mat, segments=16, radius2=None, cap=True):
    faces = bm_cylinder(bm, p0, p1, radius, segments=segments, material_index=mat,
                        radius2=radius2, cap=cap)
    _flat_ngons(faces)
    return faces


def _tube(bm, points, radius, mat, segments=8, cap=True):
    faces = bm_tube(bm, points, radius, segments=segments, material_index=mat, cap=cap)
    _flat_ngons(faces)
    return faces


def _rounded_path(points: Sequence, radius: float = LEAD_BEND_RADIUS, steps: int = 4) -> list[Vector]:
    """Polyline with every interior corner replaced by a quadratic-Bézier fillet.

    The fillet radius is limited to half of each adjacent segment, so arbitrarily short
    segments (e.g. adjacent holes) degrade gracefully to sharp corners.
    """
    pts: list[Vector] = []
    for p in points:
        v = Vector(p)
        if not pts or (v - pts[-1]).length > 1e-6:
            pts.append(v)
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        prev_p, p, next_p = pts[i - 1], pts[i], pts[i + 1]
        d0, d1 = p - prev_p, next_p - p
        r = min(radius, 0.5 * d0.length, 0.5 * d1.length)
        if r < 1e-3:
            out.append(p)
            continue
        a = p - d0.normalized() * r
        b = p + d1.normalized() * r
        for k in range(steps + 1):
            t = k / steps
            u = 1.0 - t
            out.append(u * u * a + 2.0 * u * t * p + t * t * b)
    out.append(pts[-1])
    return out


def _hole_bottom(hole: Hole) -> Vector:
    return Vector((hole.x, hole.y, hole.z - HOLE_DEPTH))


def _hole_top(hole: Hole, z: float) -> Vector:
    return Vector((hole.x, hole.y, hole.z + z))


def _lead(bm, hole: Hole, path: Sequence, mat: int, radius: float = LEAD_RADIUS) -> None:
    """A Ø0.6 lead: hole bottom -> straight up through the hole -> `path` (rounded corners)."""
    _tube(bm, _rounded_path([_hole_bottom(hole), *path]), radius, mat)


def _chord(hole_a: Hole, hole_b: Hole) -> tuple[Vector, Vector, Vector, Vector, float]:
    """(A, B, midpoint, unit chord direction, span) on the board plane."""
    a = Vector((hole_a.x, hole_a.y, hole_a.z))
    b = Vector((hole_b.x, hole_b.y, hole_b.z))
    span = span_mm(hole_a, hole_b)
    if span < 1e-6:
        raise ValueError("a component needs two different holes")
    d = (b - a) / span
    return a, b, (a + b) * 0.5, d, span


def _horizontal_normal(d: Vector) -> Vector:
    """Horizontal unit vector perpendicular to the chord direction."""
    return Vector((d.y, -d.x, 0.0)).normalized()


# ----------------------------------------------------------------------------
# Resistors
# ----------------------------------------------------------------------------
def _resistor_bands(bm, e0: Vector, axis: Vector, length: float, bands: Sequence[str], slots: _Slots) -> None:
    """Three value bands near one end plus a gold tolerance band near the other."""
    spacing = _clamp(0.18 * length, _RESISTOR_BAND_WIDTH + 0.02, 0.9)
    offsets = [0.2 * length + i * spacing for i in range(3)] + [0.8 * length]
    keys = [f"band_{name}" for name in bands] + ["band_gold"]
    half = _RESISTOR_BAND_WIDTH * 0.5
    for off, key in zip(offsets, keys):
        c = e0 + axis * off
        _cylinder(bm, c - axis * half, c + axis * half, _RESISTOR_BAND_RADIUS, slots(key), segments=16)


def _build_resistor(bm, comp_type: str, hole_a: Hole, hole_b: Hole, slots: _Slots) -> None:
    a, b, mid, d, span = _chord(hole_a, hole_b)
    length = _clamp(span - 3.0, 2.6, 6.5)
    silver, tan = slots("lead_silver"), slots("resistor_tan")
    bands = COMPONENT_INFO[comp_type]["bands"]

    if span >= _RESISTOR_STANDING_SPAN:
        # axial: body centred on the chord at lead height
        e0 = Vector((mid.x, mid.y, LEAD_Z)) - d * (length * 0.5)
        e1 = e0 + d * length
        _cylinder(bm, e0, e1, _RESISTOR_BODY_RADIUS, tan, segments=16)
        _resistor_bands(bm, e0, d, length, bands, slots)
        _lead(bm, hole_a, [_hole_top(hole_a, LEAD_Z), e0], silver)
        _lead(bm, hole_b, [_hole_top(hole_b, LEAD_Z), e1], silver)
        return

    # standing: body vertical over hole A, lead B loops over the top and down into hole B
    e0 = Vector((a.x, a.y, 2.0))
    e1 = Vector((a.x, a.y, 2.0 + length))
    _cylinder(bm, e0, e1, _RESISTOR_BODY_RADIUS, tan, segments=16)
    _resistor_bands(bm, e0, Vector((0, 0, 1)), length, bands, slots)
    _lead(bm, hole_a, [e0], silver)
    loop_z = e1.z + 1.6
    _lead(bm, hole_b, [Vector((b.x, b.y, loop_z)), Vector((a.x, a.y, loop_z)), e1], silver)


# ----------------------------------------------------------------------------
# Capacitors
# ----------------------------------------------------------------------------
def _build_ceramic_disc(bm, hole_a: Hole, hole_b: Hole, slots: _Slots) -> None:
    """Ø5 x 1.4 disc standing vertically in the plane of the chord, bottom at z = 3."""
    a, b, mid, d, span = _chord(hole_a, hole_b)
    n = _horizontal_normal(d)
    radius, thickness, bottom_z = 2.5, 1.4, 3.0
    centre = Vector((mid.x, mid.y, bottom_z + radius))
    _cylinder(bm, centre - n * (thickness * 0.5), centre + n * (thickness * 0.5), radius,
              slots("cap_ceramic"), segments=24)
    silver = slots("lead_silver")
    pin_offset = min(1.25, span * 0.5)   # leads enter the disc bottom, converging from the holes
    run_z = 2.0
    for hole, sign in ((hole_a, -1.0), (hole_b, 1.0)):
        foot = Vector((mid.x, mid.y, 0.0)) + d * (sign * pin_offset)
        _lead(bm, hole, [_hole_top(hole, run_z), Vector((foot.x, foot.y, run_z)),
                         Vector((foot.x, foot.y, bottom_z + 0.4))], silver)


def _build_electrolytic_can(bm, comp_type: str, hole_a: Hole, hole_b: Hole, slots: _Slots) -> None:
    """Vertical can at the midpoint with a pale polarity stripe and a '-' marker."""
    a, b, mid, d, span = _chord(hole_a, hole_b)
    radius, height, body_key, lead_spacing = _CAN_SIZES[comp_type]
    n = _horizontal_normal(d)
    base = Vector((mid.x, mid.y, _CAN_BOTTOM_Z))
    top = base + Vector((0, 0, height))
    _cylinder(bm, base, top, radius, slots(body_key), segments=24)
    # aluminium top with a thin rim, dark rubber bung below
    _cylinder(bm, top, top + Vector((0, 0, 0.25)), radius * 0.94, slots("lead_silver"), segments=24)
    _cylinder(bm, base - Vector((0, 0, 0.3)), base, radius * 0.9, slots("cap_black"), segments=24)
    # polarity stripe with a small '-' marker, facing the student (-Y, tie-break -X)
    if n.y > 1e-6 or (abs(n.y) <= 1e-6 and n.x > 0):
        n = -n
    stripe_h = height * 0.85
    stripe_c = base + n * (radius - 0.02) + Vector((0, 0, height * 0.5))
    rot = Matrix.Translation(stripe_c) @ n.to_track_quat("Y", "Z").to_matrix().to_4x4() \
        @ Matrix.Translation(-stripe_c)
    bm_box(bm, stripe_c, (radius * 0.45, 0.24, stripe_h), slots("cap_stripe"), matrix=rot)
    bm_box(bm, stripe_c + Vector((0, 0, height * 0.22)), (radius * 0.25, 0.30, 0.16),
           slots("cap_black"), matrix=rot)
    # leads out of the bottom, spaced like real can pins
    silver = slots("lead_silver")
    pin_offset = min(lead_spacing * 0.5, span * 0.5)
    run_z = 1.2
    for hole, sign in ((hole_a, -1.0), (hole_b, 1.0)):
        foot = Vector((mid.x, mid.y, 0.0)) + d * (sign * pin_offset)
        _lead(bm, hole, [_hole_top(hole, run_z), Vector((foot.x, foot.y, run_z)),
                         Vector((foot.x, foot.y, _CAN_BOTTOM_Z))], silver)


# ----------------------------------------------------------------------------
# Inductors
# ----------------------------------------------------------------------------
def _build_inductor(bm, comp_type: str, hole_a: Hole, hole_b: Hole, slots: _Slots) -> None:
    """Drum-core ferrite with a copper helix; the core shortens to fit short spans (min 2.5)."""
    a, b, mid, d, span = _chord(hole_a, hole_b)
    core_r, nominal_len, turns = _INDUCTOR_SIZES[comp_type]
    core_len = _clamp(span - 1.6, 2.5, nominal_len)
    flange_t = 0.5 if core_len >= nominal_len * 0.75 else 0.3
    centre = Vector((mid.x, mid.y, LEAD_Z))
    e0 = centre - d * (core_len * 0.5)
    e1 = centre + d * (core_len * 0.5)
    ferrite, copper, silver = slots("ferrite"), slots("copper"), slots("lead_silver")

    # coil geometry: shrink the wire (never below Ø0.36) so turns stay distinguishable
    coil_len = core_len - 2.0 * flange_t - 0.3
    turns_eff = max(6, round(turns * min(1.0, coil_len / (nominal_len - 1.3))))
    wire_r = _clamp(0.45 * coil_len / turns_eff, 0.18, _COIL_WIRE_RADIUS)
    helix_r = core_r + wire_r + 0.05
    flange_r = helix_r + wire_r + 0.15

    _cylinder(bm, e0, e1, core_r, ferrite, segments=14)
    _cylinder(bm, e0, e0 + d * flange_t, flange_r, ferrite, segments=18)
    _cylinder(bm, e1 - d * flange_t, e1, flange_r, ferrite, segments=18)

    c0 = e0 + d * (flange_t + 0.15)
    c1 = e1 - d * (flange_t + 0.15)
    pts = helix_points(centre, c0, c1, helix_r, turns_eff, samples_per_turn=12)
    _tube(bm, pts, wire_r, copper, segments=6)
    # tie the helix ends into the core so the winding does not float
    _tube(bm, [pts[0], c0], wire_r, copper, segments=6)
    _tube(bm, [pts[-1], c1], wire_r, copper, segments=6)

    _lead(bm, hole_a, [_hole_top(hole_a, LEAD_Z), e0], silver)
    _lead(bm, hole_b, [_hole_top(hole_b, LEAD_Z), e1], silver)


# ----------------------------------------------------------------------------
# Jumper wire
# ----------------------------------------------------------------------------
def wire_colour_key(span: float) -> str:
    """PALETTE key of the jumper insulation for a given span (mm)."""
    for limit, key in _WIRE_COLOURS:
        if span <= limit:
            return key
    return "wire_black"


def _build_wire(bm, hole_a: Hole, hole_b: Hole, slots: _Slots) -> None:
    a, b, mid, d, span = _chord(hole_a, hole_b)
    height = _clamp(0.25 * span, 3.0, 14.0)
    silver = slots("lead_silver")
    insulation = slots(wire_colour_key(span))
    pin_top, ins_z = 1.0, 0.6
    for hole in (hole_a, hole_b):
        _tube(bm, [_hole_bottom(hole), _hole_top(hole, pin_top)], LEAD_RADIUS, silver)
    n = int(_clamp(span / 2.5, 16, 48))
    arc = arc_points(_hole_top(hole_a, ins_z), _hole_top(hole_b, ins_z), height, n=n)
    _tube(bm, arc, WIRE_RADIUS, insulation, segments=10)


# ----------------------------------------------------------------------------
# Oscilloscope probe
# ----------------------------------------------------------------------------
def _build_probe(bm, hole_a: Hole, hole_b: Hole, slots: _Slots) -> None:
    """Yellow barrel leaning over hole A (tip in the hole), ground clip on hole B, scope cable."""
    a, b, mid, d, span = _chord(hole_a, hole_b)
    lean = math.radians(PROBE_LEAN_DEG)
    u = Vector((math.sin(lean), 0.0, math.cos(lean)))        # barrel axis, leaning toward +X
    silver, yellow, black = slots("lead_silver"), slots("probe_yellow"), slots("probe_black")

    tip0 = _hole_bottom(hole_a)
    tip1 = tip0 + u * 3.2
    _cylinder(bm, tip0, tip1, 0.15, silver, segments=10, radius2=0.55)
    collar1 = tip1 + u * 1.5
    _cylinder(bm, tip1, collar1, 1.0, black, segments=14)
    barrel1 = collar1 + u * 14.0
    _cylinder(bm, collar1, barrel1, 1.5, yellow, segments=16)
    grip0 = collar1 + u * 4.0
    _cylinder(bm, grip0, grip0 + u * 1.2, 1.62, black, segments=16)
    cap1 = barrel1 + u * 1.5
    _cylinder(bm, barrel1, cap1, 1.6, black, segments=16)

    # ground clip over hole B: pin, silver jaw, black insulated body aligned toward the barrel
    _tube(bm, [_hole_bottom(hole_b), _hole_top(hole_b, 1.4)], LEAD_RADIUS, silver)
    clip_c = Vector((b.x, b.y, 0.0))
    toward = Vector((a.x - b.x, a.y - b.y, 0.0))
    ang = math.atan2(toward.y, toward.x) if toward.length > 1e-6 else 0.0
    rot = Matrix.Translation(clip_c) @ Matrix.Rotation(ang, 4, "Z") @ Matrix.Translation(-clip_c)
    bm_box(bm, clip_c + Vector((0, 0, 1.2)), (1.3, 1.3, 1.0), silver, matrix=rot)
    bm_box(bm, clip_c + Vector((0, 0, 3.6)), (3.0, 2.0, 4.0), black, matrix=rot)

    # thin ground lead from the collar to the clip top
    lead_from = tip1 + u * 1.0 - Vector((0, 1.0, 0))
    clip_top = clip_c + Vector((0, 0, 5.6))
    dist = (clip_top - lead_from).length
    _tube(bm, arc_points(lead_from, clip_top, _clamp(0.25 * dist, 2.0, 7.0), n=18), 0.4, black)

    # Ø1.5 cable from the barrel top towards the oscilloscope pick-up point
    end = PROBE_CABLE_END
    cable = bezier(cap1, cap1 + u * 25.0, Vector((end.x - 30.0, end.y, end.z - 6.0)), end, n=28)
    _tube(bm, cable, 0.75, black, segments=10)


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------
_BUILDERS = {
    "RESISTOR": lambda bm, ct, a, b, s: _build_resistor(bm, ct, a, b, s),
    # All capacitors are the same ceramic disc (no electrolytic cans); value differs only in the label.
    "CAPACITOR": lambda bm, ct, a, b, s: _build_ceramic_disc(bm, a, b, s),
    "INDUCTOR": lambda bm, ct, a, b, s: _build_inductor(bm, ct, a, b, s),
    "WIRE": lambda bm, ct, a, b, s: _build_wire(bm, a, b, s),
    "PROBE": lambda bm, ct, a, b, s: _build_probe(bm, a, b, s),
}


def object_name(comp_type: str, uid: int) -> str:
    """``COMP_{comp_type}_{uid:04d}`` / ``WIRE_{uid:04d}`` / ``PROBE_{uid:04d}``."""
    if comp_type in ("WIRE", "PROBE"):
        return f"{comp_type}_{int(uid):04d}"
    return f"COMP_{comp_type}_{int(uid):04d}"


def spawn_component(scene: bpy.types.Scene, comp_type: str, hole_a: Hole, hole_b: Hole, uid: int,
                    collection: Optional[bpy.types.Collection] = None) -> bpy.types.Object:
    """Build the mesh for `comp_type` spanning `hole_a` -> `hole_b` and link it to the scene.

    Returns the root object carrying the §2 custom props (``sim_role``, ``comp_type``,
    ``pin_a``, ``pin_b``, ``pin_a_xyz``, ``pin_b_xyz``, ``uid``).  Raises ``ValueError`` for
    an unknown/non-placeable key or identical holes.
    """
    info = COMPONENT_INFO.get(comp_type)
    if info is None or info["kind"] not in _BUILDERS:
        raise ValueError(f"not a placeable component type: {comp_type!r}")
    if collection is None:
        collection = get_collection(COMPONENTS_COLLECTION, parent=scene.collection)

    _, _, mid, _, _ = _chord(hole_a, hole_b)
    slots = _Slots()
    bm = bmesh.new()
    _BUILDERS[info["kind"]](bm, comp_type, hole_a, hole_b, slots)
    origin = Vector((mid.x, mid.y, 0.0))
    bmesh.ops.translate(bm, verts=bm.verts[:], vec=-origin)
    finish_bmesh(bm)

    name = object_name(comp_type, uid)
    mesh = mesh_from_bmesh(name, bm, slots.materials)
    obj = new_object(name, mesh, collection, location=origin)
    # write the world matrix directly: headless/modal callers read `matrix_world` before any
    # depsgraph evaluation would otherwise propagate `location`.
    obj.matrix_world = Matrix.Translation(origin)
    role = info["kind"] if info["kind"] in ("WIRE", "PROBE") else "COMPONENT"
    set_props(obj, sim_role=role, comp_type=comp_type, pin_a=hole_a.pin, pin_b=hole_b.pin,
              pin_a_xyz=[float(hole_a.x), float(hole_a.y), float(hole_a.z)],
              pin_b_xyz=[float(hole_b.x), float(hole_b.y), float(hole_b.z)], uid=int(uid))
    return obj


def placed_root(obj) -> Optional[bpy.types.Object]:
    """Climb parents until an object whose ``sim_role`` is COMPONENT/WIRE/PROBE; else None."""
    while isinstance(obj, bpy.types.Object):
        try:
            if obj.get("sim_role") in PLACED_ROLES:
                return obj
        except ReferenceError:
            return None
        obj = obj.parent
    return None


def remove_component(obj) -> None:
    """Delete a placed item (root + children + orphaned mesh data).

    Any object of the item's hierarchy may be passed.  Objects that are not part of a
    placed item (board, buttons, labels, ...) are refused with ``ValueError`` so the
    breadboard itself can never be deleted through this path.
    """
    root = placed_root(obj)
    if root is None:
        raise ValueError(f"{getattr(obj, 'name', obj)!r} is not a placed component")
    remove_object_tree(root)
