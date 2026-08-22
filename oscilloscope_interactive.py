"""
Interactive control scaffold for oscilloscope.blend.

Run this inside Blender (Scripting tab -> Open -> Run, or via
`blender oscilloscope.blend --python oscilloscope_interactive.py`).
It adds an "Oscilloscope" tab to the 3D Viewport sidebar (press N) with:

  - Power button toggle   -> shows/hides (lights up/dims) the Screen
  - Horizontal knob +/-   -> steps Time/Div through a 1-2-5 sequence, rotates the knob mesh
  - Vertical knob +/-     -> steps Volts/Div through a 1-2-5 sequence, rotates the knob mesh
  - Gen Out / CH1 Input   -> fixed float values you can set, meant to feed your circuit solver

Everything under "CIRCUIT SOLVER HOOKS" is a stub - wire your actual
circuit logic into those functions. Nothing else needs to change.
"""

import bpy
import math

# ============================================================
# CONFIG - object names must match those in oscilloscope.blend
# ============================================================
OBJ_SCREEN = "Screen"
OBJ_HORIZONTAL_KNOB = "Horizontal_Knob"
OBJ_CH1_KNOB = "CH1_Knob"
OBJ_CH2_KNOB = "CH2_Knob"

SCREEN_ON_COLOR = (0.05, 0.35, 0.25)
SCREEN_OFF_COLOR = (0.015, 0.02, 0.03)
SCREEN_ON_EMISSION_STRENGTH = 1.5

STEP_SEQUENCE = [1, 2, 5]      # standard oscilloscope 1-2-5 dial sequence
KNOB_STEP_DEGREES = 30.0       # how far the knob mesh visually rotates per click


# ============================================================
# CIRCUIT SOLVER HOOKS - plug your circuit logic in here
# ============================================================
def circuit_solver_get_gen_out():
    """TODO: return the fixed Gen Out (waveform generator) output value."""
    return 0.0


def circuit_solver_get_ch1_input():
    """TODO: return the fixed value being fed into the CH1 BNC input."""
    return 0.0


def circuit_solver_on_scale_change(h_scale, v_scale):
    """TODO: called whenever Time/Div or Volts/Div changes (knob turned)."""
    pass


def circuit_solver_on_power_change(power_on):
    """TODO: called whenever the power state toggles."""
    pass


# ============================================================
# STATE
# ============================================================
def _on_power_update(self, context):
    _apply_power_to_screen(self.power_on)
    circuit_solver_on_power_change(self.power_on)


class OscilloscopeState(bpy.types.PropertyGroup):
    power_on: bpy.props.BoolProperty(
        name="Power", default=False, update=_on_power_update)
    h_scale: bpy.props.FloatProperty(
        name="Time/Div (s)", default=1e-3, min=1e-9, max=10.0)
    v_scale: bpy.props.FloatProperty(
        name="Volts/Div (V)", default=1.0, min=1e-3, max=100.0)
    gen_out_value: bpy.props.FloatProperty(
        name="Gen Out (fixed input)", default=0.0)
    ch1_input_value: bpy.props.FloatProperty(
        name="CH1 Input (fixed input)", default=0.0)


# ============================================================
# HELPERS
# ============================================================
def _apply_power_to_screen(power_on):
    obj = bpy.data.objects.get(OBJ_SCREEN)
    if obj is None or not obj.data.materials:
        return
    mat = obj.data.materials[0]
    if not mat.use_nodes:
        return
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        return
    color = SCREEN_ON_COLOR if power_on else SCREEN_OFF_COLOR
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Emission Color"].default_value = (*color, 1.0)
    bsdf.inputs["Emission Strength"].default_value = (
        SCREEN_ON_EMISSION_STRENGTH if power_on else 0.0)


def _rotate_knob(obj_name, steps):
    obj = bpy.data.objects.get(obj_name)
    if obj is None:
        return
    obj.rotation_euler.y += math.radians(KNOB_STEP_DEGREES * steps)


def _step_value(current, direction):
    """Move current to the next/previous value in a 1-2-5-10-20-50... sequence."""
    current = max(current, 1e-12)
    decade = 10 ** math.floor(math.log10(current))
    mantissa = round(current / decade, 6)
    if mantissa not in STEP_SEQUENCE:
        mantissa = min(STEP_SEQUENCE, key=lambda m: abs(m - mantissa))
    idx = STEP_SEQUENCE.index(mantissa)
    if direction > 0:
        idx += 1
        if idx >= len(STEP_SEQUENCE):
            idx = 0
            decade *= 10
    else:
        idx -= 1
        if idx < 0:
            idx = len(STEP_SEQUENCE) - 1
            decade /= 10
    return STEP_SEQUENCE[idx] * decade


# ============================================================
# OPERATORS
# ============================================================
class OSC_OT_toggle_power(bpy.types.Operator):
    bl_idname = "oscilloscope.toggle_power"
    bl_label = "Toggle Power"

    def execute(self, context):
        state = context.scene.oscilloscope
        state.power_on = not state.power_on  # fires _on_power_update
        return {'FINISHED'}


class OSC_OT_turn_horizontal(bpy.types.Operator):
    bl_idname = "oscilloscope.turn_horizontal"
    bl_label = "Turn Horizontal Knob"
    direction: bpy.props.IntProperty(default=1)  # +1 or -1

    def execute(self, context):
        state = context.scene.oscilloscope
        state.h_scale = _step_value(state.h_scale, self.direction)
        _rotate_knob(OBJ_HORIZONTAL_KNOB, self.direction)
        circuit_solver_on_scale_change(state.h_scale, state.v_scale)
        return {'FINISHED'}


class OSC_OT_turn_vertical(bpy.types.Operator):
    bl_idname = "oscilloscope.turn_vertical"
    bl_label = "Turn Vertical Knob"
    direction: bpy.props.IntProperty(default=1)   # +1 or -1
    channel: bpy.props.IntProperty(default=1)     # 1 or 2

    def execute(self, context):
        state = context.scene.oscilloscope
        state.v_scale = _step_value(state.v_scale, self.direction)
        knob_name = OBJ_CH1_KNOB if self.channel == 1 else OBJ_CH2_KNOB
        _rotate_knob(knob_name, self.direction)
        circuit_solver_on_scale_change(state.h_scale, state.v_scale)
        return {'FINISHED'}


class OSC_OT_refresh_inputs(bpy.types.Operator):
    bl_idname = "oscilloscope.refresh_inputs"
    bl_label = "Pull Gen Out / CH1 from solver"

    def execute(self, context):
        state = context.scene.oscilloscope
        state.gen_out_value = circuit_solver_get_gen_out()
        state.ch1_input_value = circuit_solver_get_ch1_input()
        return {'FINISHED'}


# ============================================================
# UI PANEL (View3D sidebar, press N -> "Oscilloscope" tab)
# ============================================================
class OSC_PT_panel(bpy.types.Panel):
    bl_label = "Oscilloscope Controls"
    bl_idname = "OSC_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Oscilloscope"

    def draw(self, context):
        layout = self.layout
        state = context.scene.oscilloscope

        layout.operator("oscilloscope.toggle_power",
                         text="Power: ON" if state.power_on else "Power: OFF",
                         depress=state.power_on)

        layout.separator()
        layout.label(text=f"Time/Div: {state.h_scale:g} s")
        row = layout.row(align=True)
        row.operator("oscilloscope.turn_horizontal", text="-").direction = -1
        row.operator("oscilloscope.turn_horizontal", text="+").direction = 1

        layout.separator()
        layout.label(text=f"Volts/Div (CH1): {state.v_scale:g} V")
        row = layout.row(align=True)
        op = row.operator("oscilloscope.turn_vertical", text="-")
        op.direction, op.channel = -1, 1
        op = row.operator("oscilloscope.turn_vertical", text="+")
        op.direction, op.channel = 1, 1

        layout.separator()
        layout.prop(state, "gen_out_value")
        layout.prop(state, "ch1_input_value")
        layout.operator("oscilloscope.refresh_inputs")


# ============================================================
# REGISTER
# ============================================================
classes = (
    OscilloscopeState,
    OSC_OT_toggle_power,
    OSC_OT_turn_horizontal,
    OSC_OT_turn_vertical,
    OSC_OT_refresh_inputs,
    OSC_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.oscilloscope = bpy.props.PointerProperty(type=OscilloscopeState)


def unregister():
    del bpy.types.Scene.oscilloscope
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
