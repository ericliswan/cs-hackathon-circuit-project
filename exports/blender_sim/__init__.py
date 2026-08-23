"""
blender_sim — Breadboard circuit simulator frontend for Blender 4.1.

Phase 1: procedural breadboard, 3D mode buttons, two-click component placement,
delete mode, terminal netlist output in the backend's format, and a bridge to the
(unmodified) backend scripts `AC_Circuit_Solver.py` / `Breadboard_Circuit_Parser.py`.

Usage inside Blender (Scripting workspace, or via start_sim.py):
    import sys; sys.path.append("<project dir>")
    import blender_sim; blender_sim.register()
    blender_sim.scene_builder.build_scene()        # once, builds the bench/board/buttons
    bpy.ops.bbsim.session('INVOKE_DEFAULT')        # starts the interactive session
"""
bl_info = {
    "name": "Breadboard Circuit Simulator",
    "author": "Oscilloscope Circuits project",
    "version": (0, 1, 0),
    "blender": (4, 1, 0),
    "location": "View3D > Sidebar > Breadboard Sim",
    "description": "Interactive breadboard: arm a component, click two holes, netlist to terminal",
    "category": "Education",
}

import importlib
import sys

_SUBMODULES = ("board_layout", "materials", "bpy_utils", "scene_builder", "component_meshes", "netlist", "interaction")

# Reload-safe imports so the package can be re-run from the Text editor repeatedly.
if "board_layout" in locals():
    for _name in _SUBMODULES:
        _mod = sys.modules.get(__name__ + "." + _name)
        if _mod is not None:
            importlib.reload(_mod)

from . import board_layout  # noqa: E402  (pure Python, always importable)

try:
    import bpy  # noqa: F401
    _HAS_BPY = True
except ImportError:      # system python (unit tests): only the bpy-free modules are available
    _HAS_BPY = False

if _HAS_BPY:
    from . import materials, bpy_utils, scene_builder, component_meshes, netlist, interaction  # noqa: E402
else:
    from . import netlist  # noqa: E402  (bpy-free at module level)


def register():
    if not _HAS_BPY:
        raise RuntimeError("blender_sim.register() needs Blender's Python (bpy)")
    interaction.register()


def unregister():
    if _HAS_BPY:
        interaction.unregister()


__all__ = list(_SUBMODULES) + ["register", "unregister", "bl_info"]
