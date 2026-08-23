"""
scene_builder.py — procedural bench / breadboard / button-panel scene (CONTRACT.md §5).

Everything here is data-level (bmesh, bpy.data) so it runs headless and from inside a
modal operator — **no bpy.ops**.  Units: 1 Blender unit = 1 mm, hole plane at z = 0.
All hole / rail / extent geometry comes from `board_layout.BoardLayout`; nothing here
re-derives it.

Public API (names are binding, see CONTRACT.md §5):

    CARRIER_THICKNESS, BENCH_Z, BUTTONS
    get_collection(name)                      (re-export of bpy_utils.get_collection)
    clear_sim_scene()
    build_scene(layout=None, *, clear=True) -> dict
    set_button_armed(key)
    mesh_from_bmesh(name, bm, materials=())   (re-export of bpy_utils.mesh_from_bmesh)
    new_object(name, data, collection, ...)   (re-export of bpy_utils.new_object)
"""
from __future__ import annotations

import math
from typing import Optional

if __name__ == "__main__" and not __package__:
    # Run as a plain script (Blender -b --python blender_sim/scene_builder.py): make the
    # package importable and resolve the relative imports below against it.
    import os
    import sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import blender_sim  # noqa: F401  (registers the package in sys.modules)
    __package__ = "blender_sim"

import bpy
import bmesh
from mathutils import Matrix, Vector

from .board_layout import (BOARD_THICKNESS, RHS_OFFSET, logical_from_physical, HOLE_DEPTH, HOLE_SIZE, LAYOUT_STANDARD, NET_GND, NET_VCC,
                           RAIL_NETS, ROWS, BoardLayout, DEFAULT_LAYOUT, Hole)
from .materials import PALETTE, get_material, palette_material
from .bpy_utils import (get_collection, mesh_from_bmesh, new_object, set_props, remove_object_tree,
                        text_object, bm_cylinder, bm_box, bm_tube, bm_torus, arc_points, bezier,
                        finish_bmesh)

__all__ = [
    "CARRIER_THICKNESS", "BENCH_Z", "BUTTONS", "COLLECTIONS", "get_collection", "clear_sim_scene",
    "build_scene", "set_button_armed", "mesh_from_bmesh", "new_object",
]

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
CARRIER_THICKNESS = 3.0
BENCH_Z = -(BOARD_THICKNESS + CARRIER_THICKNESS)
CARRIER_TOP_Z = -BOARD_THICKNESS

COLLECTIONS = ("SIM_Env", "SIM_Board", "SIM_UI", "SIM_Components")

CARRIER_MARGIN_X = 12.0            # carrier plate beyond the board on ±X
CARRIER_MARGIN_Y_NEAR = 8.0        # ... on -Y
CARRIER_MARGIN_Y_FAR = 30.0        # ... on +Y (room for the binding posts)
BENCH_SIZE = (600.0, 500.0)
BENCH_THICKNESS = 4.0

BOARD_TOP_SLAB = 0.6               # thickness of the inset white top slab (gives a rim step)
BOARD_RIM_INSET = 0.5              # how far the top slab is inset from the body edge
CHANNEL_DEPTH = 2.0
HOLE_Z_BOTTOM, HOLE_Z_TOP = -1.8, 0.02
RAIL_LINE_WIDTH = 0.6
RAIL_LINE_OFFSET = 1.6             # line centre distance from the rail hole centre (away from the pair)
RAIL_LINE_Z = 0.05
LABEL_Z = 0.05
LABEL_SIZE = 2.2

POST_RADIUS = 3.0
POST_HEIGHT = 10.0
POST_CAP_RADIUS = 3.5
POST_CAP_HEIGHT = 2.5
POST_Y_OFFSET = {NET_GND: 11.0, NET_VCC: 21.0}   # beyond layout.y_max (staggered so Ø6 posts clear)
JUMPER_RADIUS = 0.5

PANEL_SIZE = (70.0, 62.0, 4.0)
PANEL_TILT_DEG = 18.0              # +X rotation: far (+Y) edge higher, top faces the -Y camera
PANEL_OFFSET_X = 48.0              # panel centre x = layout.x_max + PANEL_OFFSET_X
PANEL_TOP_Z = 2.0
BUTTON_SIZE = (18.0, 10.0, 3.0)
BUTTON_PROUD = 2.0                 # how far the button top stands above the panel surface
BUTTON_PRESS_DEPTH = 0.8
BUTTON_COLS_X = (-22.0, 0.0, 22.0)
BUTTON_ROWS_Y = (13.0, 1.0, -11.0, -23.0)
BUTTON_LABEL_SIZE = 2.6
BUTTON_ARMED_EMISSION = 1.5

MARKER_HIDDEN_Z = -50.0

CAMERA_LOCATION = (38.0, -285.0, 232.0)
CAMERA_TARGET = (20.0, -2.0, 0.0)
CAMERA_CLIP_START = 5.0            # nothing is closer than ~100 mm; keeps depth precision for the hole tops
CAMERA_LENS = 50.0

# Mode buttons: CONTRACT §3 order, 4 rows × 3 cols (row 0 = resistors, nearest the panel title).
_BUTTON_TABLE = (
    ("RESISTOR1", "R 250Ω"), ("RESISTOR2", "R 500Ω"), ("RESISTOR3", "R 1kΩ"),
    ("CAPACITOR1", "C 0.47µF"), ("CAPACITOR2", "C 47µF"), ("CAPACITOR3", "C 470µF"),
    ("INDUCTOR1", "L 2.2µH"), ("INDUCTOR2", "L 220µH"), ("INDUCTOR3", "L 220mH"),
    ("WIRE", "Jumper Wire"), ("PROBE", "Probe"), ("DELETE", "Delete"),
)
BUTTONS: list[dict] = [{"key": key, "label": label, "row": i // 3, "col": i % 3}
                       for i, (key, label) in enumerate(_BUTTON_TABLE)]


def _button_accent_key(key: str) -> str:
    """PALETTE key of the accent colour for a mode key ('RESISTOR2' -> 'accent_resistor')."""
    kind = key.rstrip("123").lower()
    return f"accent_{kind}"


def _button_material_name(key: str) -> str:
    return f"SIM_btn_{key}"


# ----------------------------------------------------------------------------
# Scene clearing
# ----------------------------------------------------------------------------
def _purge_orphans() -> None:
    """Remove unused meshes / curves / cameras / lights (leaves materials cached)."""
    for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.cameras, bpy.data.lights):
        for block in [b for b in coll if b.users == 0]:
            coll.remove(block)


def clear_sim_scene() -> None:
    """Remove every SIM_* collection together with its objects and orphaned data."""
    for name in COLLECTIONS:
        coll = bpy.data.collections.get(name)
        if coll is None:
            continue
        # look each object up by name: remove_object_tree removes children first, so a
        # cached reference to a child may already be dead by the time the loop reaches it
        for ob_name in [ob.name for ob in coll.all_objects]:
            ob = bpy.data.objects.get(ob_name)
            if ob is not None:
                remove_object_tree(ob)
        for child in list(coll.children):
            bpy.data.collections.remove(child)
        bpy.data.collections.remove(coll)
    _purge_orphans()


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def _aim(ob: bpy.types.Object, target) -> None:
    """Rotate `ob` so its local -Z axis points at `target` (cameras / lights)."""
    direction = Vector(target) - Vector(ob.location)
    ob.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _box_from_bounds(bm, x0, x1, y0, y1, z0, z1, material_index=0):
    """Axis-aligned box given its min/max bounds."""
    return bm_box(bm, ((x0 + x1) / 2.0, (y0 + y1) / 2.0, (z0 + z1) / 2.0),
                  (x1 - x0, y1 - y0, z1 - z0), material_index=material_index)


def _luminance(rgba) -> float:
    r, g, b = rgba[0], rgba[1], rgba[2]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _label(name: str, body: str, collection, location, size=LABEL_SIZE, material=None, parent=None,
           rotation=(0.0, 0.0, 0.0)) -> bpy.types.Object:
    """Flat text label lying in the XY plane (normal +Z)."""
    mat = material or palette_material("label_dark", roughness=0.8)
    return text_object(name, body, collection, size=size, location=location, rotation=rotation,
                       material=mat, parent=parent)


# ----------------------------------------------------------------------------
# Environment: bench, camera, lights, world, render / viewport settings
# ----------------------------------------------------------------------------
def _build_bench(env) -> bpy.types.Object:
    bm = bmesh.new()
    w, l = BENCH_SIZE
    _box_from_bounds(bm, -w / 2, w / 2, -l / 2, l / 2, BENCH_Z - BENCH_THICKNESS, BENCH_Z)
    finish_bmesh(bm)
    me = mesh_from_bmesh("BENCH_top", bm, [palette_material("bench_wood", roughness=0.75)])
    return new_object("BENCH_top", me, env)


CAMERA_REFERENCE_LENGTH = 162.6     # board length (mm) the camera constants were tuned for
CAMERA_MIN_SCALE = 0.85             # never come closer than this (the button panel must stay in frame)


def _build_camera(env, layout: Optional[BoardLayout] = None) -> bpy.types.Object:
    """Perspective camera framing board + console; pulled in/out with the board length."""
    cam_data = bpy.data.cameras.new("CAM_main")
    cam_data.type = "PERSP"
    cam_data.lens = CAMERA_LENS
    cam_data.sensor_width = 36.0
    cam_data.clip_start = CAMERA_CLIP_START
    cam_data.clip_end = 5000.0
    target = Vector(CAMERA_TARGET)
    scale = 1.0
    if layout is not None:
        scale = max(CAMERA_MIN_SCALE, layout.length / CAMERA_REFERENCE_LENGTH)
    location = target + (Vector(CAMERA_LOCATION) - target) * scale
    cam = new_object("CAM_main", cam_data, env, location=tuple(location))
    _aim(cam, tuple(target))
    return cam


def _build_lights(env) -> list[bpy.types.Object]:
    """Three-point area lighting scaled for a ~0.5 m scene expressed in mm."""
    spec = (
        ("LIGHT_key", (-170.0, -220.0, 320.0), 3.2e6, 300.0, (1.0, 0.97, 0.92)),
        ("LIGHT_fill", (280.0, -160.0, 220.0), 1.1e6, 320.0, (0.90, 0.94, 1.0)),
        ("LIGHT_rim", (60.0, 300.0, 260.0), 1.6e6, 260.0, (1.0, 1.0, 1.0)),
    )
    lights = []
    for name, loc, energy, size, color in spec:
        data = bpy.data.lights.new(name, "AREA")
        data.shape = "SQUARE"
        data.size = size
        data.energy = energy
        data.color = color
        ob = new_object(name, data, env, location=loc)
        _aim(ob, (10.0, 0.0, 0.0))
        lights.append(ob)
    return lights


def _setup_world(scene) -> None:
    world = scene.world
    if world is None:
        world = bpy.data.worlds.get("SIM_World") or bpy.data.worlds.new("SIM_World")
        scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is None:
        bg = world.node_tree.nodes.new("ShaderNodeBackground")
        out = world.node_tree.nodes.get("World Output") or world.node_tree.nodes.new("ShaderNodeOutputWorld")
        world.node_tree.links.new(bg.outputs["Background"], out.inputs["Surface"])
    bg.inputs["Color"].default_value = (0.25, 0.25, 0.25, 1.0)
    bg.inputs["Strength"].default_value = 1.0
    world.color = (0.25, 0.25, 0.25)


def _setup_scene(scene) -> None:
    """Units (1 BU = 1 mm), EEVEE with bloom / AO / soft shadows, viewport defaults."""
    us = scene.unit_settings
    us.system = "METRIC"
    us.scale_length = 0.001
    us.length_unit = "MILLIMETERS"
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x, scene.render.resolution_y = 1280, 800
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    ev = scene.eevee
    ev.use_bloom = True
    ev.bloom_intensity = 0.04
    ev.bloom_threshold = 1.2
    ev.use_gtao = True
    ev.gtao_distance = 6.0
    ev.use_soft_shadows = True
    ev.shadow_cube_size = "2048"
    ev.shadow_cascade_size = "2048"
    ev.taa_render_samples = 32
    ev.taa_samples = 16
    try:
        scene.view_settings.view_transform = "Standard"   # vivid rail red/blue + button accents
        scene.view_settings.look = "None"
        scene.view_settings.exposure = -0.25
    except TypeError:
        pass
    _setup_world(scene)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type != "VIEW_3D":
                    continue
                try:
                    space.shading.type = "MATERIAL"
                    space.clip_start = 0.5
                    space.clip_end = 5000.0
                    space.show_region_ui = True
                except (AttributeError, TypeError):
                    pass


# ----------------------------------------------------------------------------
# Board: carrier, body, holes, rail lines
# ----------------------------------------------------------------------------
def _carrier_bounds(layout: BoardLayout) -> tuple[float, float, float, float]:
    return (layout.x_min - CARRIER_MARGIN_X, layout.x_max + CARRIER_MARGIN_X,
            layout.y_min - CARRIER_MARGIN_Y_NEAR, layout.y_max + CARRIER_MARGIN_Y_FAR)


def _build_carrier(layout: BoardLayout, coll) -> bpy.types.Object:
    x0, x1, y0, y1 = _carrier_bounds(layout)
    bm = bmesh.new()
    _box_from_bounds(bm, x0, x1, y0, y1, BENCH_Z, CARRIER_TOP_Z, 0)
    # four corner screws (silver) so the plate reads as a mounted carrier
    for sx, sy in ((x0 + 4, y0 + 4), (x1 - 4, y0 + 4), (x0 + 4, y1 - 4), (x1 - 4, y1 - 4)):
        bm_cylinder(bm, (sx, sy, CARRIER_TOP_Z - 0.2), (sx, sy, CARRIER_TOP_Z + 0.5), 1.6,
                    segments=12, material_index=1)
    finish_bmesh(bm)
    me = mesh_from_bmesh("BOARD_carrier", bm, [palette_material("carrier_green", roughness=0.55),
                                               palette_material("lead_silver", metallic=0.9, roughness=0.35)])
    return new_object("BOARD_carrier", me, coll)


def _build_board_body(layout: BoardLayout, coll) -> bpy.types.Object:
    """White ABS body: darker lower body + inset white top slab, centre channel, rail lines."""
    x0, x1, y0, y1 = layout.x_min, layout.x_max, layout.y_min, layout.y_max
    z_mid = -CHANNEL_DEPTH
    z_top = -BOARD_TOP_SLAB
    ins = BOARD_RIM_INSET
    channel = layout.channel_x_range()
    bm = bmesh.new()
    # 0 = board_white, 1 = board_edge, 2 = rail_red, 3 = rail_blue
    _box_from_bounds(bm, x0, x1, y0, y1, -BOARD_THICKNESS, z_mid, 1)          # lower body
    if channel is None:
        x_spans = [(x0, x1, x0 + ins, x1 - ins)]
    else:
        c0, c1 = channel
        x_spans = [(x0, c0, x0 + ins, c0), (c1, x1, c1, x1 - ins)]
    for mx0, mx1, tx0, tx1 in x_spans:
        _box_from_bounds(bm, mx0, mx1, y0, y1, z_mid, z_top, 1)                 # mid (channel walls)
        _box_from_bounds(bm, tx0, tx1, y0 + ins, y1 - ins, z_top, 0.0, 0)      # inset white top
    # rail lines: red outside each VCC rail, blue on the strip side of each GND rail
    for side in ("L", "R"):
        for net in RAIL_NETS:
            holes = layout.rail_holes(net, side)
            ys = [h.y for h in holes]
            ly0, ly1 = min(ys) - 1.0, max(ys) + 1.0
            x_line = _rail_line_x(layout, side, net)
            _box_from_bounds(bm, x_line - RAIL_LINE_WIDTH / 2, x_line + RAIL_LINE_WIDTH / 2, ly0, ly1,
                             0.0, 2 * RAIL_LINE_Z, 2 if net == NET_VCC else 3)
    finish_bmesh(bm)
    me = mesh_from_bmesh("BOARD_breadboard", bm, [
        palette_material("board_white", roughness=0.62),
        palette_material("board_edge", roughness=0.7),
        palette_material("rail_red", roughness=0.6),
        palette_material("rail_blue", roughness=0.6),
    ])
    board = new_object("BOARD_breadboard", me, coll)
    params = layout.to_params()
    set_props(board, sim_role="BOARD", layout=str(params["layout"]), pitch=float(params["pitch"]),
              columns=int(params["columns"]), rows=str(params["rows"]),
              board_top_z=float(params["board_top_z"]))
    return board


def _rail_line_x(layout: BoardLayout, side: str, net: str) -> float:
    """X of the printed rail line: pushed away from the rail pair (red outermost, blue innermost)."""
    x = layout.rail_x(side, net)
    outward = -1.0 if side == "L" else 1.0
    # push the printed line away from the partner rail of the pair (outer rail -> outward)
    return x + outward * RAIL_LINE_OFFSET if layout.rail_is_outer(side, net) else x - outward * RAIL_LINE_OFFSET


def _build_holes(layout: BoardLayout, coll) -> bpy.types.Object:
    """ONE mesh with a dark square recess per hole (purely visual; snapping uses the layout)."""
    bm = bmesh.new()
    size = (HOLE_SIZE, HOLE_SIZE, HOLE_Z_TOP - HOLE_Z_BOTTOM)
    zc = (HOLE_Z_TOP + HOLE_Z_BOTTOM) / 2.0
    for h in layout.holes:
        bm_box(bm, (h.x, h.y, zc), size, material_index=0)
    finish_bmesh(bm)
    me = mesh_from_bmesh("BOARD_holes", bm, [palette_material("hole_dark", roughness=0.95)])
    return new_object("BOARD_holes", me, coll)


# ----------------------------------------------------------------------------
# Rails: binding posts and the fixed jumpers that pre-wire all four rails
# ----------------------------------------------------------------------------
def _post_position(layout: BoardLayout, net: str) -> Vector:
    return Vector((layout.rail_x("L", net), layout.y_max + POST_Y_OFFSET[net], CARRIER_TOP_Z))


def _build_post(layout: BoardLayout, net: str, coll) -> bpy.types.Object:
    base = _post_position(layout, net)
    colour = "post_red" if net == NET_VCC else "post_black"
    bm = bmesh.new()
    z0 = base.z
    bm_cylinder(bm, (base.x, base.y, z0 - 0.2), (base.x, base.y, z0 + 1.2), POST_CAP_RADIUS + 0.6,
                segments=6, material_index=1)                                      # hex nut
    bm_cylinder(bm, (base.x, base.y, z0), (base.x, base.y, z0 + POST_HEIGHT), POST_RADIUS,
                segments=20, material_index=0)                                     # body
    bm_cylinder(bm, (base.x, base.y, z0 + POST_HEIGHT - 0.01),
                (base.x, base.y, z0 + POST_HEIGHT + POST_CAP_HEIGHT), POST_CAP_RADIUS,
                segments=20, material_index=0)                                     # cap
    bm_cylinder(bm, (base.x, base.y, z0 + POST_HEIGHT + POST_CAP_HEIGHT - 0.01),
                (base.x, base.y, z0 + POST_HEIGHT + POST_CAP_HEIGHT + 0.4), 1.2,
                segments=12, material_index=1)                                     # metal stud
    finish_bmesh(bm)
    name = f"RAIL_post_{net.lower()}"
    me = mesh_from_bmesh(name, bm, [palette_material(colour, roughness=0.45),
                                    palette_material("lead_silver", metallic=0.9, roughness=0.3)])
    ob = new_object(name, me, coll)
    set_props(ob, sim_role="RAIL", rail=net)
    return ob


def _jumper_object(name: str, points, net: str, hole: Hole, coll) -> bpy.types.Object:
    bm = bmesh.new()
    bm_tube(bm, points, JUMPER_RADIUS, segments=10, material_index=0)
    finish_bmesh(bm)
    colour = "wire_red" if net == NET_VCC else "wire_black"
    me = mesh_from_bmesh(name, bm, [palette_material(colour, roughness=0.5)])
    ob = new_object(name, me, coll)
    set_props(ob, sim_role="RAIL", rail=net, hole=hole.pin)
    return ob


def _build_rail_jumpers(layout: BoardLayout, coll) -> dict:
    """Post -> last left-rail hole (L jumpers) and left -> right last rail hole arcs (R jumpers)."""
    out = {}
    for net in RAIL_NETS:
        hole_l = layout.rail_holes(net, "L")[-1]
        hole_r = layout.rail_holes(net, "R")[-1]
        sunk_l = (hole_l.x, hole_l.y, -HOLE_DEPTH)
        sunk_r = (hole_r.x, hole_r.y, -HOLE_DEPTH)
        # L jumper: leaves the post just under its cap, arcs down into the rail hole
        post = _post_position(layout, net)
        start = (post.x, post.y - POST_RADIUS, post.z + POST_HEIGHT - 1.5)
        pts = arc_points(start, hole_l.xyz, 6.0, n=20) + [sunk_l]
        out[f"{net}_L"] = _jumper_object(f"RAIL_jumper_{net.lower()}_L", pts, net, hole_l, coll)
        # R jumper: arc across the +Y end from the last left hole to the last right hole;
        # VCC (outer pair) arcs higher and further out so the two never touch.
        bow_y, bow_z = (9.0, 13.0) if net == NET_VCC else (5.0, 8.0)
        a, b = Vector(hole_l.xyz), Vector(hole_r.xyz)
        up_a = a + Vector((0.0, 0.0, 2.0))
        up_b = b + Vector((0.0, 0.0, 2.0))
        arc = bezier(up_a, up_a + Vector((0.0, bow_y, bow_z)), up_b + Vector((0.0, bow_y, bow_z)), up_b, n=28)
        pts = [sunk_l] + arc + [sunk_r]
        out[f"{net}_R"] = _jumper_object(f"RAIL_jumper_{net.lower()}_R", pts, net, hole_r, coll)
    return out


# ----------------------------------------------------------------------------
# Labels
# ----------------------------------------------------------------------------
def _strip_end_columns(layout: BoardLayout, side: str) -> tuple[int, int]:
    """First and last LOGICAL column of a half (RHS offset is the backend's fixed 60)."""
    n = layout.columns
    return (1, n) if side == "L" else (RHS_OFFSET + 1, RHS_OFFSET + n)


def _build_labels(layout: BoardLayout, coll) -> list[bpy.types.Object]:
    labels = []
    dark = palette_material("label_dark", roughness=0.8)
    pale = palette_material("cap_stripe", roughness=0.8)
    red = palette_material("rail_red", roughness=0.6)
    blue = palette_material("rail_blue", roughness=0.6)
    end_gap = 3.2
    standard = layout.layout == LAYOUT_STANDARD
    # row letters a..e at both ends of each strip
    for side in ("L", "R"):
        c_first, c_last = _strip_end_columns(layout, side)
        ends = [("near", layout.column_y(c_first) - end_gap), ("far", layout.column_y(c_last) + end_gap)]
        if not standard:   # END_TO_END: the two inner ends would coincide — keep the outer ends only
            ends = [ends[0]] if side == "L" else [ends[1]]
        for tag, y in ends:
            for row in ROWS:
                labels.append(_label(f"LBL_row_{side}_{tag}_{row}", row, coll,
                                     (layout.row_x(side, row), y, LABEL_Z), material=dark))
        # column numbers 1, 5, 10, ..., 60 on the strip's outer side (between strip and rails)
        outer_row = "a" if side == "L" else "e"
        x_num = layout.row_x(side, outer_row) + (-end_gap if side == "L" else end_gap)
        numbers = [1] + list(range(5, layout.columns + 1, 5))
        for pc in numbers:
            lc = logical_from_physical(side, pc)      # RHS offset is the backend's fixed 60
            labels.append(_label(f"LBL_col_{side}_{pc}", str(pc), coll,
                                 (x_num, layout.column_y(lc), LABEL_Z), size=2.0, material=dark))
        # LHS / RHS tag on the carrier at the near end of each half
        x_mid = (layout.row_x(side, "a") + layout.row_x(side, "e")) / 2.0
        y_tag = layout.column_y(c_first) - (CARRIER_MARGIN_Y_NEAR / 2.0 + 6.35) if standard \
            else layout.column_y(c_first) - 6.0
        z_tag = CARRIER_TOP_Z + 0.05 if standard else LABEL_Z
        labels.append(_label(f"LBL_half_{side}", f"{side}HS", coll, (x_mid, y_tag, z_tag),
                             size=3.0, material=pale if standard else dark))
    # + / − at both ends of each rail line
    for side in ("L", "R"):
        for net in RAIL_NETS:
            ys = [h.y for h in layout.rail_holes(net, side)]
            x_line = _rail_line_x(layout, side, net)
            sym, mat = ("+", red) if net == NET_VCC else ("−", blue)
            for tag, y in (("near", min(ys) - end_gap), ("far", max(ys) + end_gap)):
                labels.append(_label(f"LBL_rail_{net.lower()}_{side}_{tag}", sym, coll,
                                     (x_line, y, LABEL_Z), size=2.6, material=mat))
    # binding-post tags on the carrier, in one column to the +X side of both posts
    x_tag = layout.rail_x("L", NET_GND) + POST_CAP_RADIUS + 7.0
    for net, text in ((NET_VCC, "V+"), (NET_GND, "GND")):
        p = _post_position(layout, net)
        labels.append(_label(f"LBL_post_{net.lower()}", text, coll, (x_tag, p.y, CARRIER_TOP_Z + 0.05),
                             size=2.6, material=pale))
    return labels


# ----------------------------------------------------------------------------
# Button panel
# ----------------------------------------------------------------------------
def _panel_matrix(layout: BoardLayout) -> Matrix:
    """World matrix of the panel: local z=0 is the panel's top surface."""
    return (Matrix.Translation((layout.x_max + PANEL_OFFSET_X, 0.0, PANEL_TOP_Z))
            @ Matrix.Rotation(math.radians(PANEL_TILT_DEG), 4, "X"))


def _build_panel_base(layout: BoardLayout, coll) -> bpy.types.Object:
    """Wedge from the bench up to the tilted panel's underside so the console looks supported."""
    m = _panel_matrix(layout)
    w, l, t = PANEL_SIZE
    ins = 2.0
    top = [m @ Vector((sx * (w / 2 - ins), sy * (l / 2 - ins), -t))
           for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    bottom = [Vector((p.x, p.y, BENCH_Z)) for p in top]
    bm = bmesh.new()
    vt = [bm.verts.new(p) for p in top]
    vb = [bm.verts.new(p) for p in bottom]
    bm.faces.new(vt)
    bm.faces.new(list(reversed(vb)))
    for i in range(4):
        j = (i + 1) % 4
        bm.faces.new((vb[i], vb[j], vt[j], vt[i]))
    finish_bmesh(bm)
    me = mesh_from_bmesh("PANEL_base", bm, [palette_material("panel_grey", roughness=0.7)])
    return new_object("PANEL_base", me, coll)


def _button_mesh(key: str, material) -> bpy.types.Mesh:
    """Rounded box 18 × 10 × 3 mm, local origin on the panel surface, standing BUTTON_PROUD above it."""
    w, l, h = BUTTON_SIZE
    bm = bmesh.new()
    bm_box(bm, (0.0, 0.0, BUTTON_PROUD - h / 2.0), (w, l, h))
    bmesh.ops.bevel(bm, geom=bm.edges[:], offset=0.9, offset_type="OFFSET", segments=3, profile=0.5,
                    affect="EDGES", clamp_overlap=True)
    for f in bm.faces:
        f.smooth = True
    finish_bmesh(bm)
    me = mesh_from_bmesh(f"BTN_{key.lower()}", bm, [material])
    return me


def _build_panel(layout: BoardLayout, coll) -> tuple[bpy.types.Object, dict]:
    w, l, t = PANEL_SIZE
    bm = bmesh.new()
    bm_box(bm, (0.0, 0.0, -t / 2.0), (w, l, t), material_index=0)
    bmesh.ops.bevel(bm, geom=bm.edges[:], offset=1.2, offset_type="OFFSET", segments=3, profile=0.5,
                    affect="EDGES", clamp_overlap=True)
    for f in bm.faces:
        f.smooth = True
    finish_bmesh(bm)
    me = mesh_from_bmesh("PANEL_modes", bm, [palette_material("panel_grey", roughness=0.65)])
    panel = new_object("PANEL_modes", me, coll, location=(layout.x_max + PANEL_OFFSET_X, 0.0, PANEL_TOP_Z),
                       rotation=(math.radians(PANEL_TILT_DEG), 0.0, 0.0))
    _build_panel_base(layout, coll)
    title = _label("LBL_panel_title", "COMPONENTS", coll, (0.0, l / 2.0 - 6.0, 0.05), size=3.6,
                   material=palette_material("cap_stripe", roughness=0.7), parent=panel)
    title.data.extrude = 0.05
    buttons = {}
    for spec in BUTTONS:
        key = spec["key"]
        accent = PALETTE[_button_accent_key(key)]
        mat = get_material(_button_material_name(key), base_color=accent, roughness=0.45,
                           emission_color=accent, emission_strength=0.0)
        btn = new_object(f"BTN_{key.lower()}", _button_mesh(key, mat), coll,
                         location=(BUTTON_COLS_X[spec["col"]], BUTTON_ROWS_Y[spec["row"]], 0.0),
                         parent=panel)
        set_props(btn, sim_role="BUTTON", arms=key, label=spec["label"])
        text_mat = palette_material("label_dark", roughness=0.8) if _luminance(accent) > 0.3 \
            else palette_material("band_white", roughness=0.8)
        lbl = _label(f"LBL_btn_{key}", spec["label"], coll, (0.0, 0.0, BUTTON_PROUD + 0.06),
                     size=BUTTON_LABEL_SIZE, material=text_mat, parent=btn)
        lbl.data.extrude = 0.03
        buttons[key] = btn
    return panel, buttons


def set_button_armed(key: Optional[str]) -> None:
    """Highlight (emission) and depress the armed button; reset every other button."""
    for spec in BUTTONS:
        k = spec["key"]
        armed = (k == key)
        btn = bpy.data.objects.get(f"BTN_{k.lower()}")
        if btn is not None:
            btn.location.z = -BUTTON_PRESS_DEPTH if armed else 0.0
        mat = bpy.data.materials.get(_button_material_name(k))
        if mat is None or not mat.use_nodes:
            continue
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Emission Strength"].default_value = BUTTON_ARMED_EMISSION if armed else 0.0


# ----------------------------------------------------------------------------
# UI markers
# ----------------------------------------------------------------------------
def _build_marker(name: str, major: float, minor: float, colour_key: str, coll) -> bpy.types.Object:
    bm = bmesh.new()
    bm_torus(bm, (0.0, 0.0, 0.0), major, minor, major_segments=28, minor_segments=8)
    finish_bmesh(bm)
    mat = get_material(f"SIM_{colour_key}", base_color=PALETTE[colour_key], roughness=0.4,
                       emission_color=PALETTE[colour_key], emission_strength=3.0)
    me = mesh_from_bmesh(name, bm, [mat])
    ob = new_object(name, me, coll, location=(0.0, 0.0, MARKER_HIDDEN_Z))
    ob.hide_render = True
    try:
        ob.hide_set(True)
    except RuntimeError:
        ob.hide_viewport = True
    return ob


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------
def build_scene(layout: Optional[BoardLayout] = None, *, clear: bool = True) -> dict:
    """Build bench, carrier, breadboard, rails, labels, button panel, markers, camera and lights.

    Idempotent when `clear=True` (the default): every SIM_* object is removed first.
    Returns {"board", "holes", "carrier", "panel", "buttons": {key: obj}, "marker_a",
    "marker_hover", "camera", "layout"}.
    """
    layout = layout or DEFAULT_LAYOUT
    scene = bpy.context.scene
    if clear:
        clear_sim_scene()
    env = get_collection("SIM_Env")
    board_coll = get_collection("SIM_Board")
    ui = get_collection("SIM_UI")
    get_collection("SIM_Components")

    _setup_scene(scene)
    _build_bench(env)
    camera = _build_camera(env, layout)
    _build_lights(env)
    scene.camera = camera

    carrier = _build_carrier(layout, board_coll)
    board = _build_board_body(layout, board_coll)
    holes = _build_holes(layout, board_coll)
    for net in RAIL_NETS:
        _build_post(layout, net, board_coll)
    _build_rail_jumpers(layout, board_coll)
    _build_labels(layout, board_coll)

    panel, buttons = _build_panel(layout, ui)
    marker_hover = _build_marker("UI_marker_hover", 1.3, 0.18, "marker_cyan", ui)
    marker_a = _build_marker("UI_marker_a", 1.5, 0.25, "marker_orange", ui)
    set_button_armed(None)

    return {"board": board, "holes": holes, "carrier": carrier, "panel": panel, "buttons": buttons,
            "marker_a": marker_a, "marker_hover": marker_hover, "camera": camera, "layout": layout}


if __name__ == "__main__":
    # Headless smoke use:  Blender -b --python blender_sim/scene_builder.py
    build_scene()
