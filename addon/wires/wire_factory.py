import bpy

# bpy RESOURCES
# bpy.types.Curve, bpy.types.BezierSplinePoint, bpy.types.Collection, and the Add-on Tutorial

def create_wire(name="wire", start_position=(0, 0, 0), end_position=(0.1, 0, 0)):
    """
    Creates a wire object with a 3 point rounded bevel bezier curve positioned
            between start_position and end_position.

        -   bpy curve documentation
            https://docs.blender.org/api/current/bpy.types.Curve.html
        -   bpy spline documentation
            https://docs.blender.org/api/current/bpy.types.Spline.html
    """
    curve_data = bpy.data.curves.new(name, type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.bevel_depth = 0.01
    curve_data.bevel_resolution = 4
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

    for point in (point_start, point_mid, point_end):
        point.handle_left_type = "AUTO"
        point.handle_right_type = "AUTO"

    # creates object
    obj = bpy.data.objects.new(name, curve_data)
    # displays object
    bpy.context.collection.objects.link(obj)
    return obj




