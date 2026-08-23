"""
interaction.py — operators, modal session, HUD, sidebar panel and scene properties
(CONTRACT.md §8).

This is the runtime layer the student talks to:

* **Scene properties** (``scene.bbsim_*``): armed mode key, session flag, status line,
  pending endpoint A, and the source amplitude / phase / frequency (changing a source value
  re-runs the backend through ``netlist.report``).
* **Helpers** usable headless and from tests: ``arm``, ``place``, ``delete``,
  ``resolve_click`` (plus the pure ``resolve_ray`` it is built on).
* **Operators**: ``bbsim.session`` (the modal game loop), ``bbsim.stop``, ``bbsim.arm``,
  ``bbsim.clear_board``, ``bbsim.print_list``, ``bbsim.solve``, ``bbsim.build_scene``,
  ``bbsim.place``, ``bbsim.delete_item``.
* **Panel** ``VIEW3D_PT_bbsim`` in the sidebar (category "Breadboard Sim").

Everything that needs a window, a GPU or an event loop (the modal body, the draw
handlers) is guarded so that importing and registering the package headless never fails,
and every exception inside ``modal()`` is caught and turned into a status message.

Geometry conventions: 1 Blender unit = 1 mm, hole plane at ``board_layout.BOARD_TOP_Z``.
No ``bpy.ops`` is used in geometry / object code (``bpy.ops`` is only *defined* here).
"""
from __future__ import annotations

import traceback
from typing import Any, Optional

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty
from mathutils import Vector
from bpy.app.handlers import persistent
from mathutils.geometry import intersect_line_plane

from . import component_meshes, netlist, scene_builder
from .board_layout import BOARD_TOP_Z, Hole, net_repr
from .bpy_utils import arc_points
from .component_meshes import COMPONENT_INFO

__all__ = [
    "MODE_KEYS", "arm", "place", "delete", "resolve_click", "resolve_ray", "register", "unregister",
    "BBSIM_OT_session", "BBSIM_OT_stop", "BBSIM_OT_arm", "BBSIM_OT_clear_board", "BBSIM_OT_print_list",
    "BBSIM_OT_solve", "BBSIM_OT_build_scene", "BBSIM_OT_place", "BBSIM_OT_delete_item", "VIEW3D_PT_bbsim",
]

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
MODE_KEYS: list[str] = ["RESISTOR1", "RESISTOR2", "RESISTOR3",
                        "CAPACITOR1", "CAPACITOR2", "CAPACITOR3",
                        "INDUCTOR1", "INDUCTOR2", "INDUCTOR3",
                        "WIRE", "PROBE", "DELETE"]
"""The 12 mode keys in palette order (CONTRACT §3)."""

PLACEABLE_KEYS: tuple[str, ...] = tuple(k for k in MODE_KEYS if k != "DELETE")

MARKER_HOVER = "UI_marker_hover"
MARKER_A = "UI_marker_a"
MARKER_HOVER_LIFT = 0.1          # marker z above the hole plane (mm)
MARKER_A_LIFT = 0.15
MARKER_HIDDEN_Z = -50.0          # parking depth for hidden markers (matches scene_builder)
RUBBER_BAND_LIFT = 2.0           # rubber-band line starts this far above endpoint A (mm)
RAY_LENGTH = 1.0e5               # length of the segment used for the plane intersection (mm)

# Any non-WINDOW region of a VIEW_3D area (UI/HEADER/TOOLS/TOOL_HEADER/NAVIGATION_BAR/...) overlaps
# the WINDOW region; the mouse over one of them means "over UI" and the event is passed through.
# Viewport navigation events that must always reach Blender.
NAV_EVENTS = frozenset({"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE", "WHEELOUTMOUSE",
                        "TRACKPADPAN", "TRACKPADZOOM", "MOUSEROTATE", "MOUSESMARTZOOM", "NDOF_MOTION",
                        "NDOF_BUTTON_MENU", "NDOF_BUTTON_FIT"})
# Keyboard shortcuts (PRESS, no ctrl/alt) -> mode kind / action.
KEY_KINDS = {"R": "RESISTOR", "C": "CAPACITOR", "L": "INDUCTOR"}
KEY_DIRECT = {"W": "WIRE", "P": "PROBE", "X": "DELETE", "DEL": "DELETE"}
KEY_VARIANTS = {"ONE": 1, "TWO": 2, "THREE": 3}
VARIANT_KINDS = ("RESISTOR", "CAPACITOR", "INDUCTOR")

HUD_FONT_ID = 0
HUD_FONT_SIZE = 14
HUD_LINE_HEIGHT = 20
HUD_MARGIN = (16, 18)
HUD_COLOUR_TITLE = (1.0, 0.85, 0.25, 1.0)
HUD_COLOUR_TEXT = (0.92, 0.92, 0.92, 1.0)
HUD_COLOUR_DIM = (0.65, 0.65, 0.65, 0.9)
HUD_COLOUR_PENDING = (1.0, 0.6, 0.15, 1.0)
RUBBER_BAND_COLOUR = (1.0, 0.6, 0.15, 0.95)
RUBBER_BAND_WIDTH = 3.0

HINT_LINE = "Esc/RMB cancel · 1/2/3 variant · MMB orbit · Stop in the N-panel"
DRIVER_NS_KEY = "bbsim_draw_handles"     # bpy.app.driver_namespace slot mirroring the handler handles


# ----------------------------------------------------------------------------
# Session state shared between the modal operator and the draw handlers
# ----------------------------------------------------------------------------
class _SessionState:
    """Transient, per-process state (not saved in the .blend)."""

    def __init__(self) -> None:
        self.hover_hole: Optional[Hole] = None          # hole under the cursor (or None)
        self.hover_point: Optional[Vector] = None       # board-plane point under the cursor
        self.hover_button: Optional[str] = None         # mode key of the hovered 3D button
        self.hover_obj: str = ""                        # name of the object under the cursor
        self.pending_hole: Optional[Hole] = None        # the exact Hole clicked as endpoint A
        self.variants: dict[str, int] = {k: 1 for k in VARIANT_KINDS}   # last variant per kind
        self.current_kind: str = "RESISTOR"             # kind the 1/2/3 keys act on
        self.last_error: str = ""

    def clear_hover(self) -> None:
        self.hover_hole = None
        self.hover_point = None
        self.hover_button = None
        self.hover_obj = ""


_STATE = _SessionState()


# ----------------------------------------------------------------------------
# Small utilities
# ----------------------------------------------------------------------------
def _log(msg: str) -> None:
    print(f"[BBSIM] {msg}")


def _set_status(scene: Any, msg: str) -> None:
    """Store the last message for the HUD / panel (never raises)."""
    try:
        scene.bbsim_status = str(msg)
    except (AttributeError, TypeError):
        pass


def _armed(scene: Any) -> str:
    return str(getattr(scene, "bbsim_armed", "") or "")


def _pending(scene: Any) -> str:
    return str(getattr(scene, "bbsim_pending", "") or "")


def mode_label(key: Optional[str]) -> str:
    """Human label of a mode key ('RESISTOR1' -> 'R 250Ω'); '' / None -> 'none'."""
    if not key:
        return "none"
    info = COMPONENT_INFO.get(key)
    return str(info["label"]) if info else str(key)


def _result_text(result: dict) -> str:
    """Short human summary of a ``netlist.report`` result for the status line."""
    if result.get("ok"):
        return f"Vout = {result['amp']:.3f} V ∠ {result['phase_deg']:.1f}°"
    text = str(result.get("reason", ""))
    if result.get("error"):
        text += f" ({result['error']})"
    return text


def _tag_redraw_all() -> None:
    """Redraw every area of every window (panel + HUD); safe headless."""
    try:
        wm = bpy.context.window_manager
        for window in wm.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except (AttributeError, RuntimeError):
        pass


def _after_change(scene: Any, reason: str) -> dict:
    """Print the component list and the backend verdict after a board change."""
    netlist.print_components(scene, reason=reason)
    return netlist.report(scene)


# ----------------------------------------------------------------------------
# Markers
# ----------------------------------------------------------------------------
def _marker(name: str) -> Optional[bpy.types.Object]:
    return bpy.data.objects.get(name)


def _show_marker(name: str, hole: Hole, lift: float) -> None:
    ob = _marker(name)
    if ob is None:
        return
    ob.location = (hole.x, hole.y, hole.z + lift)
    try:
        ob.hide_set(False)
    except RuntimeError:
        ob.hide_viewport = False


def _hide_marker(name: str) -> None:
    ob = _marker(name)
    if ob is None:
        return
    ob.location = (0.0, 0.0, MARKER_HIDDEN_Z)
    try:
        ob.hide_set(True)
    except RuntimeError:
        ob.hide_viewport = True


def _hide_markers() -> None:
    _hide_marker(MARKER_HOVER)
    _hide_marker(MARKER_A)


# ----------------------------------------------------------------------------
# Helpers exposed for tests (CONTRACT §8)
# ----------------------------------------------------------------------------
def arm(scene: Any, key: Optional[str]) -> bool:
    """Arm a mode (or disarm with ``None``/``""``).

    Sets ``scene.bbsim_armed``, clears the pending endpoint, hides the endpoint-A marker
    and pushes / highlights the 3D button via ``scene_builder.set_button_armed``.  Returns
    False (and leaves the state unchanged) for an unknown key.  Never raises.
    """
    key = str(key or "")
    if key and key not in MODE_KEYS:
        _set_status(scene, f"unknown mode {key!r}")
        _log(f"unknown mode {key!r}")
        return False
    scene.bbsim_armed = key
    scene.bbsim_pending = ""
    _STATE.pending_hole = None
    _hide_marker(MARKER_A)
    if key:
        kind = COMPONENT_INFO[key]["kind"]
        if kind in VARIANT_KINDS:
            _STATE.current_kind = kind
            _STATE.variants[kind] = int(key[-1])
    try:
        scene_builder.set_button_armed(key or None)
    except Exception as exc:          # noqa: BLE001 — scene may not be built yet
        _STATE.last_error = f"set_button_armed: {exc}"
    _set_status(scene, f"armed {mode_label(key)} — click endpoint A" if key and key != "DELETE"
                else ("delete mode — click a component, wire or probe" if key else "disarmed"))
    return True


def _toggle_arm(scene: Any, key: str) -> bool:
    """Clicking the armed button again disarms it; anything else arms `key`."""
    return arm(scene, None if key and _armed(scene) == key else key)


def _lookup_holes(scene: Any, pin_a: str, pin_b: str, *,
                  holes: Optional[tuple[Hole, Hole]] = None) -> tuple[Optional[Hole], Optional[Hole], str]:
    """Resolve two pin strings (or take `holes` verbatim); returns (a, b, error), error '' on success."""
    if holes is not None:
        a, b = holes
    else:
        layout = netlist.get_layout(scene)
        try:
            a = layout.hole(str(pin_a).strip())
            b = layout.hole(str(pin_b).strip())
        except (KeyError, ValueError, TypeError) as exc:
            return None, None, f"bad pin: {exc}"
    if a.key == b.key or (a.is_rail and b.is_rail and a.net == b.net):
        return a, b, "same hole — pick a different one"
    return a, b, ""


def _remove_probes(scene: Any) -> list[str]:
    """Remove every placed probe (single-probe invariant); returns the removed names."""
    removed = []
    for ob in netlist.placed_objects(scene):
        if ob.get("sim_role") == "PROBE":
            removed.append(ob.name)
            component_meshes.remove_component(ob)
    return removed


def place(scene: Any, comp_type: str, pin_a: str, pin_b: str, *,
          holes: Optional[tuple[Hole, Hole]] = None) -> Optional[bpy.types.Object]:
    """Spawn `comp_type` between two pin strings; print the list and the verdict.

    ``holes`` optionally supplies the exact ``Hole`` objects (the modal passes the clicked
    rail holes, whose pin string — ``"VCC"``/``"GND"`` — would otherwise resolve to the
    first hole of that rail).  Returns the new root object, or None when the request is
    invalid (unknown type, DELETE, bad pin, same hole) or spawning failed.  Never raises.
    """
    try:
        comp_type = str(comp_type or "").strip().upper()
        if comp_type not in PLACEABLE_KEYS:
            _set_status(scene, f"cannot place {comp_type!r}")
            _log(f"cannot place {comp_type!r}")
            return None
        a, b, error = _lookup_holes(scene, pin_a, pin_b, holes=holes)
        if error:
            _set_status(scene, error)
            _log(f"rejected {comp_type} {pin_a}->{pin_b}: {error}")
            return None
        if comp_type == "PROBE":
            for name in _remove_probes(scene):
                _log(f"replaced probe {name}")
        uid = netlist.next_uid(scene)
        obj = component_meshes.spawn_component(scene, comp_type, a, b, uid)
        result = _after_change(scene, f"placed {comp_type} {net_repr(a.net)}->{net_repr(b.net)}")
        _set_status(scene, f"placed {obj.name} {net_repr(a.net)}->{net_repr(b.net)} | {_result_text(result)}")
        return obj
    except Exception as exc:          # noqa: BLE001 — the UI must never see an exception
        traceback.print_exc()
        _set_status(scene, f"place failed: {exc}")
        _log(f"place failed: {exc}")
        return None


def delete(scene: Any, obj: Any) -> bool:
    """Delete the placed item `obj` belongs to.  Board / buttons / labels are refused.

    Returns True when something was removed (list + verdict re-printed).  Never raises.
    """
    try:
        root = component_meshes.placed_root(obj) if obj is not None else None
        if root is None:
            _set_status(scene, "cannot delete the breadboard")
            return False
        name = root.name
        component_meshes.remove_component(root)
        result = _after_change(scene, f"deleted {name}")
        _set_status(scene, f"deleted {name} | {_result_text(result)}")
        return True
    except Exception as exc:          # noqa: BLE001
        traceback.print_exc()
        _set_status(scene, f"delete failed: {exc}")
        return False


def clear_board(scene: Any) -> int:
    """Remove every placed item; returns how many were removed."""
    names = [ob.name for ob in netlist.placed_objects(scene)]
    for name in names:
        ob = bpy.data.objects.get(name)
        if ob is not None:
            component_meshes.remove_component(ob)
    scene.bbsim_pending = ""
    _hide_marker(MARKER_A)
    result = _after_change(scene, f"cleared board ({len(names)} removed)")
    _set_status(scene, f"cleared board | {_result_text(result)}")
    return len(names)


# ----------------------------------------------------------------------------
# Ray / click resolution
# ----------------------------------------------------------------------------
def _view3d_under_mouse(context: Any, event: Any) -> tuple:
    """(area, WINDOW region, RegionView3D, region type under the mouse) or (None,)*4.

    Scans ``context.window.screen.areas`` by rectangle, because inside a modal operator
    ``context.region`` always refers to the region the operator was invoked in.
    """
    try:
        mx, my = int(event.mouse_x), int(event.mouse_y)
        window = context.window
        screen = window.screen if window is not None else getattr(context, "screen", None)
        areas = screen.areas if screen is not None else ()
    except (AttributeError, TypeError):
        return None, None, None, None
    for area in areas:
        if area.type != "VIEW_3D":
            continue
        if not (area.x <= mx < area.x + area.width and area.y <= my < area.y + area.height):
            continue
        win_region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if win_region is None:
            return None, None, None, None
        rv3d = area.spaces.active.region_3d
        over = [r.type for r in area.regions
                if r.type != "WINDOW" and r.width > 1 and r.height > 1
                and r.x <= mx < r.x + r.width and r.y <= my < r.y + r.height]
        return area, win_region, rv3d, (over[0] if over else "WINDOW")
    return None, None, None, None


def _mouse_ray(region: Any, rv3d: Any, event: Any) -> tuple[Vector, Vector]:
    """World-space (origin, unit direction) of the mouse ray through a VIEW_3D WINDOW region."""
    from bpy_extras import view3d_utils
    coord = (event.mouse_x - region.x, event.mouse_y - region.y)
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    return Vector(origin), Vector(direction).normalized()


def button_key(obj: Any) -> Optional[str]:
    """Climb parents to a ``BTN_`` root carrying ``arms``; return its mode key or None."""
    while isinstance(obj, bpy.types.Object):
        try:
            key = obj.get("arms")
        except ReferenceError:
            return None
        if key is not None:
            return str(key)
        obj = obj.parent
    return None


def _empty_resolution() -> dict:
    return {"button": None, "placed": None, "hole": None, "hit_obj": None, "point": None, "over": None}


def resolve_ray(scene: Any, depsgraph: Any, origin: Vector, direction: Vector, layout=None) -> dict:
    """Pure resolution of a world-space ray against the scene (no window needed).

    Returns ``{"button": key|None, "placed": obj|None, "hole": Hole|None, "hit_obj": obj|None,
    "point": Vector|None, "over": None}`` — ``point`` is the board-plane point under the ray
    and ``hole`` the snapped hole (``layout.nearest``).  Hole snapping is suppressed when the
    ray hits a button or the button panel so a click on the console never places anything.
    """
    out = _empty_resolution()
    layout = layout or netlist.get_layout(scene)
    origin, direction = Vector(origin), Vector(direction)
    hit, _loc, _nrm, _idx, obj, _mat = scene.ray_cast(depsgraph, origin, direction)
    if hit and obj is not None:
        out["hit_obj"] = obj
        out["button"] = button_key(obj)
        out["placed"] = component_meshes.placed_root(obj)
    point = intersect_line_plane(origin, origin + direction * RAY_LENGTH,
                                 Vector((0.0, 0.0, BOARD_TOP_Z)), Vector((0.0, 0.0, 1.0)))
    if point is not None and (point - origin).dot(direction) > 0.0:
        out["point"] = point
        on_console = out["button"] is not None or (out["hit_obj"] is not None
                                                   and out["hit_obj"].name.startswith("PANEL_"))
        if not on_console:
            out["hole"] = layout.nearest(point.x, point.y)
    return out


def resolve_click(context: Any, event: Any) -> dict:
    """Resolve the mouse position of `event` (CONTRACT §8).

    ``{"button": key|None, "placed": obj|None, "hole": Hole|None, "hit_obj": obj|None,
    "point": Vector|None, "over": region type|None}``.  ``over`` is the VIEW_3D region type
    under the mouse ('WINDOW' for the viewport proper) or None when the mouse is not over a
    3D view at all; everything else is None in that case.  Never raises.
    """
    out = _empty_resolution()
    try:
        area, region, rv3d, over = _view3d_under_mouse(context, event)
        out["over"] = over
        if region is None or rv3d is None or over != "WINDOW":
            return out
        origin, direction = _mouse_ray(region, rv3d, event)
        scene = context.scene
        depsgraph = context.evaluated_depsgraph_get()
        out.update(resolve_ray(scene, depsgraph, origin, direction))
    except Exception as exc:          # noqa: BLE001
        _STATE.last_error = f"resolve_click: {exc}"
    return out


# ----------------------------------------------------------------------------
# Draw handlers (GUI only; every call is guarded)
# ----------------------------------------------------------------------------
def hud_lines(scene: Any) -> list[tuple[str, tuple]]:
    """The HUD text lines as (text, rgba) — pure, testable headless."""
    armed = _armed(scene)
    pending = _pending(scene)
    lines = [("BREADBOARD SIM  [session running]", HUD_COLOUR_TITLE)]
    if armed:
        lines.append((f"Mode: {mode_label(armed)}  ({armed})", HUD_COLOUR_TEXT))
    else:
        lines.append(("Mode: none — click a button or press R/C/L/W/P/X", HUD_COLOUR_TEXT))
    if pending:
        lines.append((f"A: {pending}  → click endpoint B", HUD_COLOUR_PENDING))
    status = str(getattr(scene, "bbsim_status", "") or "")
    if status:
        lines.append((status, HUD_COLOUR_TEXT))
    lines.append((HINT_LINE, HUD_COLOUR_DIM))
    return lines


def _ui_scale() -> float:
    """Pixel scale of the UI (Retina pixel_size x user UI scale); 1.0 when unavailable."""
    try:
        return max(0.5, float(bpy.context.preferences.system.dpi) / 72.0)
    except Exception:                 # noqa: BLE001
        return 1.0


def _tools_region_width() -> float:
    """Width of the 3D view's toolbar (TOOLS) region when it overlaps the viewport, else 0."""
    try:
        area = bpy.context.area
        if area is None:
            return 0.0
        for r in area.regions:
            if r.type == "TOOLS" and r.width > 1:
                return float(r.width)
    except Exception:                 # noqa: BLE001
        pass
    return 0.0


def _draw_hud() -> None:
    """POST_PIXEL: text block in the top-left corner of every 3D view."""
    try:
        import blf
        scene = bpy.context.scene
        region = bpy.context.region
        if scene is None or region is None or not getattr(scene, "bbsim_running", False):
            return
        scale = _ui_scale()
        blf.size(HUD_FONT_ID, HUD_FONT_SIZE * scale)
        try:                          # soft shadow so the text stays legible over the bench
            blf.enable(HUD_FONT_ID, blf.SHADOW)
            blf.shadow(HUD_FONT_ID, 3, 0.0, 0.0, 0.0, 0.8)
            blf.shadow_offset(HUD_FONT_ID, 1, -1)
        except Exception:             # noqa: BLE001
            pass
        # Bottom-left corner of the viewport: Blender draws nothing there, and we stay clear
        # of the (overlapping) toolbar on the left by offsetting past the TOOLS region width.
        lines = hud_lines(scene)
        x = HUD_MARGIN[0] * scale + _tools_region_width()
        y = HUD_MARGIN[1] * scale + (len(lines) - 1) * HUD_LINE_HEIGHT * scale
        for i, (text, colour) in enumerate(lines):
            blf.position(HUD_FONT_ID, x, y - i * HUD_LINE_HEIGHT * scale, 0)
            blf.color(HUD_FONT_ID, *colour)
            blf.draw(HUD_FONT_ID, text)
        try:
            blf.disable(HUD_FONT_ID, blf.SHADOW)
        except Exception:             # noqa: BLE001
            pass
    except Exception as exc:          # noqa: BLE001 — a draw error must never break the viewport
        _STATE.last_error = f"hud: {exc}"


def rubber_band_points(scene: Any) -> list[tuple[float, float, float]]:
    """Polyline from endpoint A (lifted) to the hovered hole / board point; [] when idle."""
    pending = _pending(scene)
    if not pending:
        return []
    a = _STATE.pending_hole
    if a is None or a.pin != pending:
        try:
            a = netlist.get_layout(scene).hole(pending)
        except (KeyError, ValueError):
            return []
    target = _STATE.hover_hole.xyz if _STATE.hover_hole is not None else _STATE.hover_point
    if target is None:
        return []
    start = Vector((a.x, a.y, a.z + RUBBER_BAND_LIFT))
    end = Vector(target) + Vector((0.0, 0.0, RUBBER_BAND_LIFT * 0.5))
    span = (end - start).length
    height = max(3.0, min(14.0, 0.25 * span))
    return [tuple(p) for p in arc_points(start, end, height, n=16)]


def _draw_rubber_band() -> None:
    """POST_VIEW: anti-aliased arc from endpoint A to the cursor while a placement is pending."""
    try:
        scene = bpy.context.scene
        region = bpy.context.region
        if scene is None or region is None or not getattr(scene, "bbsim_running", False):
            return
        pts = rubber_band_points(scene)
        if len(pts) < 2:
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")
        try:
            shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
            batch = batch_for_shader(shader, "LINE_STRIP", {"pos": pts})
            shader.bind()
            shader.uniform_float("viewportSize", (float(region.width), float(region.height)))
            shader.uniform_float("lineWidth", RUBBER_BAND_WIDTH)
            shader.uniform_float("color", RUBBER_BAND_COLOUR)
            batch.draw(shader)
        except Exception:             # noqa: BLE001 — fall back to the plain line shader
            shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            batch = batch_for_shader(shader, "LINE_STRIP", {"pos": pts})
            gpu.state.line_width_set(RUBBER_BAND_WIDTH)
            shader.bind()
            shader.uniform_float("color", RUBBER_BAND_COLOUR)
            batch.draw(shader)
            gpu.state.line_width_set(1.0)
        finally:
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set("LESS_EQUAL")
    except Exception as exc:          # noqa: BLE001
        _STATE.last_error = f"rubber band: {exc}"


def _draw_handles() -> dict:
    """The live draw-handler handles (class attributes mirrored in the driver namespace)."""
    handles = {"hud": BBSIM_OT_session.hud_handle, "line": BBSIM_OT_session.line_handle}
    try:
        stored = bpy.app.driver_namespace.get(DRIVER_NS_KEY) or {}
    except (AttributeError, TypeError):
        stored = {}
    for k, v in stored.items():
        if handles.get(k) is None:
            handles[k] = v
    return handles


def _add_draw_handlers() -> None:
    if BBSIM_OT_session.hud_handle is not None or BBSIM_OT_session.line_handle is not None:
        _remove_draw_handlers()
    try:
        BBSIM_OT_session.hud_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_hud, (), "WINDOW", "POST_PIXEL")
        BBSIM_OT_session.line_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_rubber_band, (), "WINDOW",
                                                                              "POST_VIEW")
        bpy.app.driver_namespace[DRIVER_NS_KEY] = {"hud": BBSIM_OT_session.hud_handle,
                                                   "line": BBSIM_OT_session.line_handle}
    except Exception as exc:          # noqa: BLE001 — no GPU / headless
        _STATE.last_error = f"draw handlers: {exc}"


def _remove_draw_handlers() -> None:
    for handle in _draw_handles().values():
        if handle is None:
            continue
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        except (ValueError, RuntimeError, TypeError, AttributeError):
            pass
    BBSIM_OT_session.hud_handle = None
    BBSIM_OT_session.line_handle = None
    try:
        bpy.app.driver_namespace.pop(DRIVER_NS_KEY, None)
    except (AttributeError, TypeError):
        pass


# ----------------------------------------------------------------------------
# Scene properties
# ----------------------------------------------------------------------------
def _on_source_changed(self: Any, _context: Any) -> None:
    """Update callback of the three source props: re-run the backend, refresh the status."""
    try:
        result = netlist.report(self)
        _set_status(self, f"source changed | {_result_text(result)}")
    except Exception as exc:          # noqa: BLE001
        _set_status(self, f"solver error: {exc}")
    _tag_redraw_all()


_SCENE_PROPS = {
    "bbsim_armed": StringProperty(name="Armed mode", description="Mode key armed for placement", default=""),
    "bbsim_running": BoolProperty(name="Session running", description="The modal session is active",
                                  default=False),
    "bbsim_status": StringProperty(name="Status", description="Last simulator message", default=""),
    "bbsim_pending": StringProperty(name="Pending endpoint", description="Pin string of endpoint A",
                                    default=""),
    "bbsim_amplitude": FloatProperty(name="Amplitude (V)", description="Source amplitude", default=12.0,
                                     min=0.0, max=24.0, update=_on_source_changed),
    "bbsim_phase": FloatProperty(name="Phase (°)", description="Source phase", default=0.0,
                                 min=-180.0, max=180.0, update=_on_source_changed),
    "bbsim_frequency": FloatProperty(name="Frequency (Hz)", description="Source frequency", default=50.0,
                                     min=1.0, max=1.0e6, update=_on_source_changed),
}


# ----------------------------------------------------------------------------
# Modal session operator
# ----------------------------------------------------------------------------
class BBSIM_OT_session(bpy.types.Operator):
    """Start the interactive breadboard session (click buttons and holes in the 3D view)"""
    bl_idname = "bbsim.session"
    bl_label = "Start Simulation"
    bl_options = {"REGISTER"}

    hud_handle = None        # SpaceView3D draw handler (POST_PIXEL)
    line_handle = None       # SpaceView3D draw handler (POST_VIEW)

    _timer = None

    # -- lifecycle -----------------------------------------------------------
    def invoke(self, context, event):
        scene = context.scene
        if getattr(scene, "bbsim_running", False):
            self.report({"WARNING"}, "session already running")
            return {"CANCELLED"}
        area = getattr(context, "area", None)
        if area is None or area.type != "VIEW_3D":
            self.report({"ERROR"}, "start the session from a 3D Viewport")
            return {"CANCELLED"}
        scene.bbsim_running = True
        scene.bbsim_pending = ""
        _STATE.clear_hover()
        _hide_markers()
        _add_draw_handlers()
        wm = context.window_manager
        try:
            self._timer = wm.event_timer_add(0.1, window=context.window)
        except (TypeError, RuntimeError):
            self._timer = None
        wm.modal_handler_add(self)
        _set_status(scene, "session started — arm a component")
        _log("session started")
        self.report({"INFO"}, "breadboard session started")
        area.tag_redraw()
        return {"RUNNING_MODAL"}

    def execute(self, context):
        self.report({"ERROR"}, "bbsim.session must be invoked from the 3D Viewport (INVOKE_DEFAULT)")
        return {"CANCELLED"}

    def finish(self, context) -> None:
        """Tear the session down (idempotent)."""
        wm = context.window_manager
        if self._timer is not None:
            try:
                wm.event_timer_remove(self._timer)
            except (TypeError, RuntimeError):
                pass
            self._timer = None
        _remove_draw_handlers()
        scene = context.scene
        try:
            arm(scene, None)
        except Exception:             # noqa: BLE001
            pass
        _hide_markers()
        _STATE.clear_hover()
        scene.bbsim_running = False
        scene.bbsim_pending = ""
        _set_status(scene, "session stopped")
        _log("session stopped")
        _tag_redraw_all()

    def cancel(self, context):
        self.finish(context)

    # -- event loop ----------------------------------------------------------
    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception as exc:      # noqa: BLE001 — keep the session alive no matter what
            traceback.print_exc()
            _STATE.last_error = str(exc)
            _set_status(context.scene, f"error: {exc}")
            return {"RUNNING_MODAL"}

    def _modal(self, context, event):
        scene = context.scene
        if not getattr(scene, "bbsim_running", False):
            self.finish(context)
            return {"FINISHED"}
        if event.type == "TIMER":
            area = getattr(context, "area", None)
            if area is not None:
                area.tag_redraw()
            return {"PASS_THROUGH"}
        if event.type in NAV_EVENTS or event.alt or event.ctrl or event.oskey:
            return {"PASS_THROUGH"}

        area, region, _rv3d, over = _view3d_under_mouse(context, event)
        if region is None or over != "WINDOW":
            if _STATE.hover_hole is not None or _STATE.hover_point is not None:
                _STATE.clear_hover()
                _hide_marker(MARKER_HOVER)
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            self._update_hover(context, event, area)
            return {"PASS_THROUGH"}
        if event.value != "PRESS":
            # consume the release of a click we handled, hand everything else on
            return {"RUNNING_MODAL"} if event.type == "LEFTMOUSE" else {"PASS_THROUGH"}

        if event.type == "LEFTMOUSE":
            self._handle_click(context, event)
            area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type in {"RIGHTMOUSE", "ESC"}:
            if _pending(scene):
                scene.bbsim_pending = ""
                _STATE.pending_hole = None
                _hide_marker(MARKER_A)
                _set_status(scene, "placement cancelled")
            elif _armed(scene):
                arm(scene, None)
            else:
                return {"PASS_THROUGH"}
            area.tag_redraw()
            return {"RUNNING_MODAL"}
        if not event.shift and self._handle_key(scene, event.type):
            area.tag_redraw()
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    # -- hover ---------------------------------------------------------------
    def _update_hover(self, context, event, area) -> None:
        info = resolve_click(context, event)
        _STATE.hover_button = info["button"]
        _STATE.hover_obj = info["hit_obj"].name if info["hit_obj"] is not None else ""
        _STATE.hover_point = info["point"]
        hole = info["hole"]
        changed = (hole is None) != (_STATE.hover_hole is None) or (
            hole is not None and _STATE.hover_hole is not None and hole.key != _STATE.hover_hole.key)
        _STATE.hover_hole = hole
        if changed:
            if hole is not None:
                _show_marker(MARKER_HOVER, hole, MARKER_HOVER_LIFT)
            else:
                _hide_marker(MARKER_HOVER)
        if changed or _pending(context.scene):
            area.tag_redraw()

    # -- clicks --------------------------------------------------------------
    def _handle_click(self, context, event) -> None:
        scene = context.scene
        info = resolve_click(context, event)
        if info["button"] is not None:                      # 1. 3D mode button
            _toggle_arm(scene, info["button"])
            return
        armed = _armed(scene)
        if armed == "DELETE":                               # 2. delete mode
            if info["placed"] is not None:
                delete(scene, info["placed"])
                _STATE.clear_hover()
            elif info["hit_obj"] is not None:
                _set_status(scene, "cannot delete the breadboard")
            else:
                _set_status(scene, "nothing there to delete")
            return
        hole = info["hole"]                                 # 3. hole snapping
        if hole is None:
            _set_status(scene, "no hole there")
            return
        if not armed:
            _set_status(scene, "arm a component first")
            return
        pending = _pending(scene)
        hole_a = _STATE.pending_hole
        if not pending or hole_a is None or hole_a.pin != pending:
            scene.bbsim_pending = hole.pin
            _STATE.pending_hole = hole
            _show_marker(MARKER_A, hole, MARKER_A_LIFT)
            _set_status(scene, f"A = {hole.pin} — click endpoint B")
            return
        _a, _b, error = _lookup_holes(scene, pending, hole.pin, holes=(hole_a, hole))
        if error:
            _set_status(scene, error)
            return
        obj = place(scene, armed, pending, hole.pin, holes=(hole_a, hole))
        if obj is not None:
            scene.bbsim_pending = ""
            _STATE.pending_hole = None
            _hide_marker(MARKER_A)

    # -- keyboard ------------------------------------------------------------
    @staticmethod
    def _handle_key(scene, key_type: str) -> bool:
        """Apply a shortcut; returns True if the key was consumed."""
        if key_type in KEY_DIRECT:
            arm(scene, KEY_DIRECT[key_type])
            return True
        if key_type in KEY_KINDS:
            kind = KEY_KINDS[key_type]
            arm(scene, f"{kind}{_STATE.variants[kind]}")
            return True
        if key_type in KEY_VARIANTS:
            n = KEY_VARIANTS[key_type]
            armed = _armed(scene)
            kind = COMPONENT_INFO[armed]["kind"] if armed else _STATE.current_kind
            if kind not in VARIANT_KINDS:
                kind = _STATE.current_kind
            _STATE.variants[kind] = n
            if armed and COMPONENT_INFO[armed]["kind"] == kind:
                arm(scene, f"{kind}{n}")
            else:
                _set_status(scene, f"variant {n} selected for {kind.lower()} (press "
                                   f"{'R' if kind == 'RESISTOR' else 'C' if kind == 'CAPACITOR' else 'L'})")
            return True
        return False


# ----------------------------------------------------------------------------
# Simple operators
# ----------------------------------------------------------------------------
class BBSIM_OT_stop(bpy.types.Operator):
    """Stop the interactive session (the modal exits on its next event)"""
    bl_idname = "bbsim.stop"
    bl_label = "Stop Simulation"

    def execute(self, context):
        scene = context.scene
        scene.bbsim_running = False
        _set_status(scene, "stopping…")
        _tag_redraw_all()
        return {"FINISHED"}


class BBSIM_OT_arm(bpy.types.Operator):
    """Arm a placement mode (click again to disarm)"""
    bl_idname = "bbsim.arm"
    bl_label = "Arm Mode"
    bl_options = {"REGISTER", "UNDO"}

    key: StringProperty(name="Mode key", default="")

    def execute(self, context):
        ok = _toggle_arm(context.scene, self.key)
        _tag_redraw_all()
        return {"FINISHED"} if ok else {"CANCELLED"}


class BBSIM_OT_clear_board(bpy.types.Operator):
    """Remove every placed component, wire and probe"""
    bl_idname = "bbsim.clear_board"
    bl_label = "Clear Board"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        n = clear_board(context.scene)
        self.report({"INFO"}, f"removed {n} item(s)")
        _tag_redraw_all()
        return {"FINISHED"}


class BBSIM_OT_print_list(bpy.types.Operator):
    """Print the component list to the terminal in the backend's format"""
    bl_idname = "bbsim.print_list"
    bl_label = "Print List"

    def execute(self, context):
        text = netlist.print_components(context.scene, reason="print list")
        _set_status(context.scene, f"printed {len(netlist.placed_objects(context.scene))} item(s) to the terminal")
        self.report({"INFO"}, text.splitlines()[0])
        return {"FINISHED"}


class BBSIM_OT_solve(bpy.types.Operator):
    """Run the backend solver on the current board"""
    bl_idname = "bbsim.solve"
    bl_label = "Solve"

    def execute(self, context):
        result = netlist.report(context.scene)
        _set_status(context.scene, _result_text(result))
        self.report({"INFO"}, result.get("summary", _result_text(result)))
        _tag_redraw_all()
        return {"FINISHED"}


class BBSIM_OT_build_scene(bpy.types.Operator):
    """(Re)build the bench, breadboard and button panel — removes placed items"""
    bl_idname = "bbsim.build_scene"
    bl_label = "Rebuild Scene"

    def execute(self, context):
        scene = context.scene
        try:
            scene_builder.build_scene()
        except Exception as exc:      # noqa: BLE001
            traceback.print_exc()
            self.report({"ERROR"}, f"build_scene failed: {exc}")
            _set_status(scene, f"build failed: {exc}")
            return {"CANCELLED"}
        scene.bbsim_pending = ""
        scene.bbsim_armed = ""
        _STATE.clear_hover()
        _after_change(scene, "scene rebuilt")
        _set_status(scene, "scene rebuilt")
        _tag_redraw_all()
        return {"FINISHED"}


class BBSIM_OT_place(bpy.types.Operator):
    """Place a component between two pin strings (e.g. RESISTOR1 1c 5c)"""
    bl_idname = "bbsim.place"
    bl_label = "Place Component"
    bl_options = {"REGISTER", "UNDO"}

    comp_type: StringProperty(name="Component", default="WIRE")
    pin_a: StringProperty(name="Pin A", default="")
    pin_b: StringProperty(name="Pin B", default="")

    def execute(self, context):
        obj = place(context.scene, self.comp_type, self.pin_a, self.pin_b)
        _tag_redraw_all()
        if obj is None:
            self.report({"WARNING"}, str(getattr(context.scene, "bbsim_status", "placement rejected")))
            return {"CANCELLED"}
        return {"FINISHED"}


class BBSIM_OT_delete_item(bpy.types.Operator):
    """Delete a placed item by object name"""
    bl_idname = "bbsim.delete_item"
    bl_label = "Delete Item"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Object name", default="")

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None:
            self.report({"WARNING"}, f"no object named {self.name!r}")
            return {"CANCELLED"}
        ok = delete(context.scene, obj)
        _tag_redraw_all()
        if not ok:
            self.report({"WARNING"}, str(getattr(context.scene, "bbsim_status", "cannot delete")))
            return {"CANCELLED"}
        return {"FINISHED"}


# ----------------------------------------------------------------------------
# Sidebar panel
# ----------------------------------------------------------------------------
class VIEW3D_PT_bbsim(bpy.types.Panel):
    """Breadboard simulator controls (mirrors the 3D button console)."""
    bl_label = "Breadboard Sim"
    bl_idname = "VIEW3D_PT_bbsim"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Breadboard Sim"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        running = bool(getattr(scene, "bbsim_running", False))
        armed = _armed(scene)

        row = layout.row()
        row.scale_y = 1.4
        if running:
            row.operator("bbsim.stop", text="Stop", icon="PAUSE")
        else:
            row.operator("bbsim.session", text="Start Simulation", icon="PLAY")
        layout.label(text=f"Mode: {mode_label(armed)}" + (f"  ({armed})" if armed else ""),
                     icon="RESTRICT_SELECT_OFF" if armed else "RESTRICT_SELECT_ON")

        grid = layout.column(align=True)
        for start in range(0, len(MODE_KEYS), 3):
            row = grid.row(align=True)
            for key in MODE_KEYS[start:start + 3]:
                op = row.operator("bbsim.arm", text=mode_label(key), depress=(armed == key))
                op.key = key

        pending = _pending(scene)
        if pending:
            layout.label(text=f"A = {pending} — click endpoint B", icon="PIVOT_CURSOR")
        status = str(getattr(scene, "bbsim_status", "") or "")
        if status:
            box = layout.box()
            for part in status.split(" | "):
                box.label(text=part)

        col = layout.column(align=True)
        col.label(text="Source")
        col.prop(scene, "bbsim_amplitude")
        col.prop(scene, "bbsim_phase")
        col.prop(scene, "bbsim_frequency")

        row = layout.row(align=True)
        row.operator("bbsim.print_list", text="Print list", icon="CONSOLE")
        row.operator("bbsim.solve", text="Solve", icon="PLAY_REVERSE")
        row = layout.row(align=True)
        row.operator("bbsim.clear_board", text="Clear board", icon="TRASH")
        row.operator("bbsim.build_scene", text="Rebuild scene", icon="FILE_REFRESH")


# ----------------------------------------------------------------------------
# Registration
# ----------------------------------------------------------------------------
_CLASSES = (
    BBSIM_OT_session, BBSIM_OT_stop, BBSIM_OT_arm, BBSIM_OT_clear_board, BBSIM_OT_print_list,
    BBSIM_OT_solve, BBSIM_OT_build_scene, BBSIM_OT_place, BBSIM_OT_delete_item, VIEW3D_PT_bbsim,
)


def _unregister_stale(cls) -> None:
    """Unregister a previously registered class of the same name (module reloads)."""
    stale = getattr(bpy.types, cls.__name__, None)
    if stale is not None and stale is not cls:
        try:
            bpy.utils.unregister_class(stale)
        except (RuntimeError, ValueError):
            pass


@persistent
def _on_load_post(_dummy=None) -> None:
    """File-load hook: a modal session never survives a file load, so clear the session flags
    that may have been saved in the .blend (otherwise Start would refuse with "already running")."""
    try:
        for scene in bpy.data.scenes:
            if hasattr(scene, "bbsim_running"):
                scene.bbsim_running = False
                scene.bbsim_pending = ""
    except Exception:                 # noqa: BLE001
        pass
    _remove_draw_handlers()
    _STATE.clear_hover()
    _STATE.pending_hole = None


def _install_load_handler() -> None:
    handlers = bpy.app.handlers.load_post
    for h in list(handlers):          # drop stale copies left by a module reload
        if getattr(h, "__name__", "") == "_on_load_post":
            handlers.remove(h)
    handlers.append(_on_load_post)


def _remove_load_handler() -> None:
    handlers = bpy.app.handlers.load_post
    for h in list(handlers):
        if getattr(h, "__name__", "") == "_on_load_post":
            handlers.remove(h)


def register() -> None:
    """Register operators, the panel and the ``scene.bbsim_*`` properties (reload-safe)."""
    for cls in _CLASSES:
        _unregister_stale(cls)
        try:
            bpy.utils.register_class(cls)
        except ValueError:            # already registered (same class object)
            pass
    for name, prop in _SCENE_PROPS.items():
        setattr(bpy.types.Scene, name, prop)
    _install_load_handler()


def unregister() -> None:
    """Remove draw handlers, unregister classes and delete the scene properties (idempotent)."""
    _remove_draw_handlers()
    _remove_load_handler()
    for name in _SCENE_PROPS:
        if hasattr(bpy.types.Scene, name):
            try:
                delattr(bpy.types.Scene, name)
            except (AttributeError, RuntimeError):
                pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, ValueError):
            pass
