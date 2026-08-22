import bpy

# bpy RESOURCES
# bpy.types.Curve, bpy.types.BezierSplinePoint, bpy.types.Collection, and the Add-on Tutorial

def create_wire(name="wire", start_position=(0, 0, 0), end_position=(0.1, 0, 0)):
    """
    Creates a wire object with a 3 point rounded bevel bezier curve positioned
            between start_position and end_position.

    Documentation:
        -   bpy curve 
            https://docs.blender.org/api/current/bpy.types.Curve.html
        -   bpy spline 
            https://docs.blender.org/api/current/bpy.types.Spline.html
    """
    curve_data = bpy.data.curves.new(name, type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.bevel_depth = 0.1
    curve_data.bevel_resolution = 6
    curve_data.fill_mode = "FULL"

    spline = curve_data.splines.new("BEZIER")
    spline.bezier_points.add(2) # wire now has 3points: start, mid, end

    point_start, point_mid, point_end = spline.bezier_points

    mid_position = (
    (start_position[0] + end_position[0]) / 2,
    (start_position[1] + end_position[1]) / 2,
    (start_position[2] + end_position[2]) / 2,
    )
    # .co means coordinate
    point_start.co = start_position
    point_mid.co = mid_position
    point_end.co = end_position

    # set handle types
    for point in (point_start, point_mid, point_end):
        point.handle_left_type = "AUTO"
        point.handle_right_type = "AUTO"

    # creates object
    obj = bpy.data.objects.new(name, curve_data)
    # displays object
    bpy.context.collection.objects.link(obj)
    return obj




def update_wire_endpoints(wire_obj, start_position, end_position, sag=12):
    """
    Reshapes/moves an already existing wire object to run between start_position
            and endposition with a little downward sag at the midpoint for added
            realism (if its fully straight it doesnt look right)
    
    Args:
        -   wire_obj: Object returned by create_wire()
        -   start_position & end_position: new start and endpoints
        -   sag: how far the midpoint curves
    
    Documentation:
        -   bpy spline 
            https://docs.blender.org/api/current/bpy.types.Spline.html
        -   mathutils Vector
            https://docs.blender.org/api/current/mathutils.html#mathutils.Vector
        -   update tag
            https://docs.blender.org/api/current/bpy.types.ID.html#bpy.types.ID.update_tag

    """
    # get spline from wire object and bezier points from spline
    spline = wire_obj.data.splines[0]
    position_start, position_mid, position_end = spline.bezier_points

    # turn tuples into mathutil vectors so we can perform math operations
    start_vector = Vector(start_position)
    end_vector = Vector(end_position)
    mid_vector = (start_vector + end_vector) / 2
    mid_vector.z -= sag

    # vectors -> coords
    point_start.co = start_vector
    point_mid.co = mid_vector
    point_end.co = end_vector

    # reset handle types (apparently forces blender to recalculate positions)
    for point in (point_start, point_mid, point_end):
        point.handle_left_type = "AUTO"
        point.handle_right_type = "AUTO"

    # update object
    wire_obj.data.update_tag()
