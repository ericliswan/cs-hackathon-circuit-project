import bpy
import math
import os
import sys
import numpy as np
from mathutils import Vector 

blend_dir = bpy.path.abspath("//")

if blend_dir not in sys.path:
    sys.path.append(blend_dir)

from Breadboard_Circuit_Parser import *
from breadboard_pins import *

# DW: official docs for the bpy module: https://docs.blender.org/api/current/info_quickstart.html#operators-tools

# CONSTANTS
SNAP_THRESHOLD = 0.5
WAVE_ANIM_SPEED = 0.2

# Inputs that the user sets on the oscilloscope
mag_input, phase_input, frequency = 12, 0, 50

#Calculating omega based on the frequency
FIFTY_HZ = 2*np.pi*frequency


def get_asset_terminals() -> list[object]:
    """
    Returns all the Terminal objects belonging to moveable components 
    (wires, resistors, etc.) in the current scene.
    """
    return [obj for obj in bpy.data.objects if "ATerm" in obj.name]

def get_board_terminals() -> list[object]:
    """
    Returns all the Board Terminal objects in the current scene.
    """
    return [obj for obj in bpy.data.objects if "Board_Term" in obj.name]

def run_circuit_solver(connections: list[list[str]]) -> tuple[bool, list[float] | None]:
    """
    runs the circuit solver
    """
    if not check_complete(connections):
        print("Circuit invalid")
        return (False, None)
    else:
        comp, probe = parse_raw_components(connections)
        print(comp)
        VS1 = VoltageSource("V1", 1, 0, mag_input, phase_input)
        comp.append(VS1)
        v_nodes, i_sources = solve_nodal(comp, omega=FIFTY_HZ)
        if probe is None:
            raise ValueError("No probe was provided")
        params = find_output_voltage(probe, v_nodes, omega=FIFTY_HZ)
        # plot_voltage(*params, VS1.get_value(), FIFTY_HZ, VS1.get_phase())
        print([complex(round(x.real, 4), round(x.imag, 4)) for x in v_nodes]) 
        return (True, params)


class CIRCUIT_OT_snap(bpy.types.Operator):
    """
    A class that snaps nearby terminals together.

    DW: bpy.types.Operator is the base class used to create custom tools in Blender with Python.
    To build a custom tool, we need to create a Python class that inherits from bpy.types.Operator.
    Hence, bpy.types.Operator is passed as an argument since our class inherits from it.
    """
    # bl_idname is the unique internal ID of this Blender Operator class, bl_label is the text shown
    # on the button the tool.
    bl_idname = "circuit.snap_terminals"
    bl_label = "Snap Components"

    def execute(self, context):
        """
        Runs when the operator is called / pressed.

        Parameters:
            context: gives you access to the user's current state and selections.

        DW: For all standard custom Operator classes in Blender, the execute method runs when the operator 
        is called (button is pressed) in the Blender user interface.
        """
        terminals = get_all_terminals()
        snapped_count = 0

        # nested loops to compare every unique pair of Terminals in the scene
        for i in range(len(terminals)):
            for j in range(i + 1, len(terminals)):
                t1 = terminals[i]
                t2 = terminals[j]

                # if t1 and t2 have the same parent, they belong to the same object, we move on
                if t1.parent == t2.parent:
                    continue

                # otherwise, they have different parents, we want to see how far apart they are
                dist = (t1.matrix_world.translation - t2.matrix_world.translation).length

                # if t1 and t2 are close enough, snap them together (physically move t1 to t2)
                if dist < SNAP_THRESHOLD:
                    offset = t2.matrix_world.translation - t1.matrix_world.translation
                    t1.parent.location += offset
                    snapped_count += 1

        # display success message at the bottom of Blender's screen 
        self.report({'INFO'}, f"Snapped {snapped_count} connection(s).")
        return {'FINISHED'}


class CIRCUIT_OT_animate_wave(bpy.types.Operator):
    """
    A class that uses the modal operator to continuously animate the oscilloscope wave.
    """
    bl_idname = "circuit.animate_wave"
    bl_label = "Run Waveform Animation"

    _timer = None
    _time_offset = 0.0

    def modal(self, context, event):
        """
        Called by Blender on every user action or timer tick.

        Parameters:
            context: gives you access to the user's current state and selections.
            event: an event that happens at the time of the timer tick.
        """
        # if user presses ESC or toggles simulation state off, stop timer
        if event.type == 'ESC' or not context.scene.get("circuit_running", False):
            return self.cancel(context)

        # elif the event is a timer tick, draw a wave with +0.2 offset every time
        if event.type == 'TIMER':
            waveform_obj = bpy.data.objects.get("Waveform")
            if waveform_obj and waveform_obj.type == 'CURVE':
                self._time_offset += WAVE_ANIM_SPEED
                self.draw_sine_wave(waveform_obj, self._time_offset)

                # redraw 3D viewports to show live updates
                for area in context.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
        
        return {'PASS_THROUGH'}

    def execute(self, context):
        """
        Runs when "Run Simulation" is clicked.
        """
        asset_terminals = get_asset_terminals()
        board_terminals = get_board_terminals() # replace with all of the holes
        data_to_give = {}

        for t1 in asset_terminals:
            for t2 in board_terminals:
                distance = (t1.matrix_world.translation - t2.matrix_world.translation).length
                if distance < SNAP_THRESHOLD:
                    print(f"Connection made between {t1.name}, {t2.name}")

                    t1_name = t1.name.split('_')[0]
                    t2_name = t2.name.split('_')[-1]

                    # change t1_name if its a wire
                    if "WIRE" in t1_name:
                        name = "WIRE"
                    else:
                        name = t1_name

                    # change t2_name to (1, "e") if its a coordinate
                    if t2_name not in ["VCC", "GND"]:
                        number, letter = t2_name[:-1], t2_name[-1]
                        try:
                            coord = (int(number), letter)
                        except ValueError:
                            print("THIS FAILED")
                    else:
                        coord = t2_name

                    # append to dictionary
                    if t1_name in data_to_give:
                        data_to_give[t1_name].append(coord)
                    else:
                        data_to_give[t1_name] = [name, coord]

        list_to_give = list(data_to_give.values())
        """
        [
            ["WIRE", "VCC", (1, "a")],
            ["RESISTOR1", (1, "e"), (61, "a")],
            ["WIRE", (61, "e"), "GND"]
        ]
        """
        # add the probe
        list_to_give.append(["PROBE", (5,"d"), (65,"b")])

        # run circuit solver!
        checker, params = run_circuit_solver(list_to_give)
        waveform_obj = bpy.data.objects.get("Waveform")

        # if checker succeeds, sets circuit_running to True
        if checker:
            context.scene["circuit_running"] = True

            # register a timer running every 0.03 seconds
            wm = context.window_manager
            self._timer = wm.event_timer_add(0.03, window=context.window)
            wm.modal_handler_add(self)
            self.report({'INFO'}, "Circuit Complete! Oscilloscope active.")
            return {'RUNNING_MODAL'}
        else:
            # if our circuit does not have the right connections, circuit does not run, alert the user.
            context.scene["circuit_running"] = False
            self.report({'WARNING'}, "Circuit Incomplete. Check connections.")
            if waveform_obj and waveform_obj.type == 'CURVE':
                self.clear_wave(waveform_obj)
            return {'CANCELLED'}

    def cancel(self, context):
        """
        Removes the active background timer when the simulation stops.
        """
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)
        context.scene["circuit_running"] = False
        return {'CANCELLED'}

    def draw_sine_wave(self, curve_obj, offset):
        """
        Updates points with a moving phase offset.
        """
        spline = curve_obj.data.splines[0]
        points_count = 20

        if spline.type == 'BEZIER':
            if len(spline.bezier_points) < points_count:
                spline.bezier_points.add(points_count - len(spline.bezier_points))
            
            for i in range(points_count):
                x = (i / points_count) * 2.0 - 1.0
                y = math.sin((i * 0.5) + offset) * 0.3
                spline.bezier_points[i].co = (x, y, 0)
                spline.bezier_points[i].handle_left = (x - 0.05, y, 0)
                spline.bezier_points[i].handle_right = (x + 0.05, y, 0)
        else:
            if len(spline.points) < points_count:
                spline.points.add(points_count - len(spline.points))

            for i in range(points_count):
                x = (i / points_count) * 2.0 - 1.0
                y = math.sin((i * 0.5) + offset) * 0.3
                spline.points[i].co = (x, y, 0, 1)

    def clear_wave(self, curve_obj):
        """
        Flattens the curve.
        """
        spline = curve_obj.data.splines[0]
        if spline.type == 'BEZIER':
            for i, point in enumerate(spline.bezier_points):
                x = (i / len(spline.bezier_points)) * 2.0 - 1.0
                point.co = (x, 0, 0)
                point.handle_left = (x - 0.05, 0, 0)
                point.handle_right = (x + 0.05, 0, 0)
        else:
            for i, point in enumerate(spline.points):
                x = (i / len(spline.points)) * 2.0 - 1.0
                point.co = (x, 0, 0, 1)


class CIRCUIT_OT_stop_wave(bpy.types.Operator):
    """
    Stops the sinusodial animation.
    """
    bl_idname = "circuit.stop_wave"
    bl_label = "Stop Simulation"

    def execute(self, context):
        context.scene["circuit_running"] = False
        waveform_obj = bpy.data.objects.get("Waveform")
        if waveform_obj and waveform_obj.type == 'CURVE':

            # flatten wave shape
            spline = waveform_obj.data.splines[0]
            if spline.type == 'BEZIER':
                for point in spline.bezier_points:
                    point.co[1] = 0
            else:
                for point in spline.points:
                    point.co[1] = 0

        self.report({'INFO'}, "Simulation Stopped.")
        return {'FINISHED'}


class CIRCUIT_PT_panel(bpy.types.Panel):
    """
    Creates a Panel in the 3D Viewport N-Menu. 
    """
    bl_label = "Circuit Simulator POC"
    bl_idname = "CIRCUIT_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Circuit POC'

    def draw(self, context):
        layout = self.layout
        layout.operator("circuit.snap_terminals", icon='SNAP_ON')
        
        is_running = context.scene.get("circuit_running", False)
        if not is_running:
            layout.operator("circuit.animate_wave", text="Run Simulation", icon='PLAY')
        else:
            layout.operator("circuit.stop_wave", text="Stop Simulation", icon='PAUSE')


def register():
    bpy.utils.register_class(CIRCUIT_OT_snap)
    bpy.utils.register_class(CIRCUIT_OT_animate_wave)
    bpy.utils.register_class(CIRCUIT_OT_stop_wave)
    bpy.utils.register_class(CIRCUIT_PT_panel)

def unregister():
    bpy.utils.unregister_class(CIRCUIT_OT_snap)
    bpy.utils.unregister_class(CIRCUIT_OT_animate_wave)
    bpy.utils.unregister_class(CIRCUIT_OT_stop_wave)
    bpy.utils.unregister_class(CIRCUIT_PT_panel)

if __name__ == "__main__":
    register()