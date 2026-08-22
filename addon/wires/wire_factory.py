import bpy
from mathutils import Vector

# constants
WIRE_BEVEL_DEPTH = 0.05        # wire thickness
WIRE_BEVEL_RESOLUTION = 4      # roundness of the tube cross-section


def create_wire(start_position, end_position, name="wire", sag=1):
    """
    Creates a wire object with a 3 point rounded bevel bezier curve positioned
            between start_position and end_position, with sag already applied.

    start_position and end_position are expected to be the global coords of
            two breadboard nodes the user has clicked (start node clicked
            first, end node clicked second) — this function does not pick
            or initialise its own coords, it only builds the wire between
            whatever two coords it's given.

    Args:
        start_position, end_position: global coords of the two clicked nodes
        name: object name in the scene
        sag: how far the midpoint dips (so wire curves)

    Documentation:
        -   bpy curve 
            https://docs.blender.org/api/current/bpy.types.Curve.html
        -   bpy spline 
            https://docs.blender.org/api/current/bpy.types.Spline.html
        -   mathutils Vector
            https://docs.blender.org/api/current/mathutils.html#mathutils.Vector
        -   update tag
            https://docs.blender.org/api/current/bpy.types.ID.html#bpy.types.ID.update_tag
    """
    curve_data = bpy.data.curves.new(name, type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.bevel_depth = WIRE_BEVEL_DEPTH
    curve_data.bevel_resolution = WIRE_BEVEL_RESOLUTION
    curve_data.fill_mode = "FULL"

    spline = curve_data.splines.new("BEZIER")
    spline.bezier_points.add(2)  # wire now has 3 points: start, mid, end

    # get bezier points from spline
    point_start, point_mid, point_end = spline.bezier_points

    # turn tuples into mathutil vectors so we can perform math operations
    start_vector = Vector(start_position)
    end_vector = Vector(end_position)
    mid_vector = (start_vector + end_vector) / 2
    mid_vector.z += sag

    # vectors -> coords
    point_start.co = start_vector
    point_mid.co = mid_vector
    point_end.co = end_vector

    # reset handle types (apparently forces blender to recalculate positions)
    for point in (point_start, point_mid, point_end):
        point.handle_left_type = "AUTO"
        point.handle_right_type = "AUTO"

    # creates object
    obj = bpy.data.objects.new(name, curve_data)
    # displays object
    bpy.context.collection.objects.link(obj)

    # update object
    obj.data.update_tag()

    return obj