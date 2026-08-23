"""
bpy_utils.py — small geometry / object helpers shared by scene_builder and
component_meshes.  Everything is data-level (bmesh / bpy.data) — no bpy.ops — so it
works headless and from inside a modal operator.  Units: mm.
"""
import math

import bpy
import bmesh
from mathutils import Matrix, Vector


# ----------------------------------------------------------------------------
# Collections / objects / properties
# ----------------------------------------------------------------------------
def get_collection(name, parent=None):
    """Return the collection `name`, creating and linking it (to `parent` or the scene) if needed."""
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    parent = parent or bpy.context.scene.collection
    if col.name not in parent.children:
        try:
            parent.children.link(col)
        except RuntimeError:
            pass
    return col


def mesh_from_bmesh(name, bm, materials=()):
    """Create a Mesh datablock from a bmesh (frees the bmesh) and append material slots."""
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in materials:
        me.materials.append(m)
    me.update()
    return me


def new_object(name, data, collection, location=(0.0, 0.0, 0.0), parent=None, rotation=(0.0, 0.0, 0.0)):
    """Create an object for `data` (Mesh/Curve/None), link it to `collection`."""
    ob = bpy.data.objects.new(name, data)
    collection.objects.link(ob)
    ob.location = location
    ob.rotation_euler = rotation
    if parent is not None:
        ob.parent = parent
    return ob


def set_props(ob, **props):
    for k, v in props.items():
        ob[k] = v
    return ob


def remove_object_tree(ob):
    """Remove an object, its children, and orphaned mesh/curve data."""
    if ob is None:
        return
    for child in list(ob.children):
        remove_object_tree(child)
    data = ob.data
    try:
        bpy.data.objects.remove(ob, do_unlink=True)
    except ReferenceError:
        return
    if data is not None and data.users == 0:
        if isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        elif isinstance(data, bpy.types.Curve):
            bpy.data.curves.remove(data)


def text_object(name, body, collection, size=3.0, location=(0, 0, 0), rotation=(0, 0, 0),
                material=None, align_x="CENTER", align_y="CENTER", extrude=0.0, parent=None):
    """A FONT-curve text object (no ops)."""
    cu = bpy.data.curves.new(name, type="FONT")
    cu.body = body
    cu.size = size
    cu.align_x = align_x
    cu.align_y = align_y
    cu.extrude = extrude
    if material is not None:
        cu.materials.append(material)
    return new_object(name, cu, collection, location=location, rotation=rotation, parent=parent)


# ----------------------------------------------------------------------------
# Math helpers
# ----------------------------------------------------------------------------
def V(p):
    return p if isinstance(p, Vector) else Vector(p)


def align_z_matrix(p0, p1):
    """Matrix that maps local +Z onto the segment p0->p1, translated to the segment midpoint."""
    p0, p1 = V(p0), V(p1)
    d = p1 - p0
    length = d.length
    if length < 1e-9:
        return Matrix.Translation(p0)
    rot = d.normalized().to_track_quat("Z", "Y").to_matrix().to_4x4()
    return Matrix.Translation((p0 + p1) * 0.5) @ rot


def bezier(p0, p1, p2, p3, n=24):
    """Cubic Bézier sampled at n+1 points (inclusive)."""
    p0, p1, p2, p3 = V(p0), V(p1), V(p2), V(p3)
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1.0 - t
        pts.append(u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3)
    return pts


def arc_points(a, b, height, n=24, lift=(0, 0, 1)):
    """Smooth arc from a to b rising `height` above the chord (cubic Bézier)."""
    a, b, up = V(a), V(b), V(lift)
    c1 = a + up * height * 1.35
    c2 = b + up * height * 1.35
    return bezier(a, c1, c2, b, n)


def helix_points(center, axis_p0, axis_p1, radius, turns, samples_per_turn=16):
    """Points of a helix around the segment axis_p0->axis_p1."""
    p0, p1 = V(axis_p0), V(axis_p1)
    axis = p1 - p0
    length = axis.length
    if length < 1e-9:
        return [p0]
    z = axis.normalized()
    x = z.cross(Vector((0, 0, 1)))
    if x.length < 1e-6:
        x = z.cross(Vector((0, 1, 0)))
    x.normalize()
    y = z.cross(x)
    n = max(2, int(turns * samples_per_turn))
    pts = []
    for i in range(n + 1):
        t = i / n
        ang = 2 * math.pi * turns * t
        pts.append(p0 + z * (length * t) + x * (radius * math.cos(ang)) + y * (radius * math.sin(ang)))
    return pts


# ----------------------------------------------------------------------------
# bmesh primitives (all accept an existing bmesh and tag faces with material_index)
# ----------------------------------------------------------------------------
def _tag(bm, ret, material_index, smooth=True):
    faces = {f for v in ret["verts"] for f in v.link_faces}
    for f in faces:
        f.material_index = material_index
        f.smooth = smooth
    return faces


def bm_cylinder(bm, p0, p1, radius, segments=14, material_index=0, radius2=None, cap=True, smooth=True):
    """Cylinder (or cone if radius2 given) spanning p0->p1."""
    p0, p1 = V(p0), V(p1)
    depth = (p1 - p0).length
    ret = bmesh.ops.create_cone(bm, cap_ends=cap, cap_tris=False, segments=segments,
                                radius1=radius, radius2=radius if radius2 is None else radius2,
                                depth=max(depth, 1e-6), matrix=align_z_matrix(p0, p1), calc_uvs=False)
    return _tag(bm, ret, material_index, smooth)


def bm_box(bm, center, size, material_index=0, matrix=None):
    """Axis-aligned box of `size` (sx, sy, sz) centred at `center` (optionally transformed by matrix)."""
    cx, cy, cz = V(center)
    sx, sy, sz = size
    m = Matrix.Translation((cx, cy, cz)) @ Matrix.Diagonal((sx, sy, sz, 1.0))
    if matrix is not None:
        m = matrix @ m
    ret = bmesh.ops.create_cube(bm, size=1.0, matrix=m, calc_uvs=False)
    return _tag(bm, ret, material_index, smooth=False)


def bm_sphere(bm, center, radius, material_index=0, u=12, v=8):
    ret = bmesh.ops.create_uvsphere(bm, u_segments=u, v_segments=v, radius=radius,
                                    matrix=Matrix.Translation(V(center)), calc_uvs=False)
    return _tag(bm, ret, material_index)


def bm_tube(bm, points, radius, segments=8, material_index=0, cap=True, smooth=True):
    """Sweep a circle of `radius` along a polyline `points` (parallel-transport frames)."""
    pts = [V(p) for p in points]
    pts = [p for i, p in enumerate(pts) if i == 0 or (p - pts[i - 1]).length > 1e-6]
    if len(pts) < 2:
        return set()
    # tangents
    tangents = []
    for i in range(len(pts)):
        if i == 0:
            t = pts[1] - pts[0]
        elif i == len(pts) - 1:
            t = pts[-1] - pts[-2]
        else:
            t = pts[i + 1] - pts[i - 1]
        tangents.append(t.normalized())
    # initial normal
    t0 = tangents[0]
    n = t0.cross(Vector((0, 0, 1)))
    if n.length < 1e-6:
        n = t0.cross(Vector((1, 0, 0)))
    n.normalize()
    rings = []
    for i, p in enumerate(pts):
        t = tangents[i]
        # parallel transport n
        n = (n - t * n.dot(t))
        if n.length < 1e-6:
            n = t.cross(Vector((0, 0, 1)))
            if n.length < 1e-6:
                n = t.cross(Vector((1, 0, 0)))
        n.normalize()
        b = t.cross(n)
        ring = []
        for k in range(segments):
            ang = 2 * math.pi * k / segments
            ring.append(bm.verts.new(p + n * (radius * math.cos(ang)) + b * (radius * math.sin(ang))))
        rings.append(ring)
    faces = []
    for i in range(len(rings) - 1):
        r0, r1 = rings[i], rings[i + 1]
        for k in range(segments):
            k2 = (k + 1) % segments
            faces.append(bm.faces.new((r0[k], r0[k2], r1[k2], r1[k])))
    if cap:
        faces.append(bm.faces.new(tuple(reversed(rings[0]))))
        faces.append(bm.faces.new(tuple(rings[-1])))
    for f in faces:
        f.material_index = material_index
        f.smooth = smooth
    bm.normal_update()
    return set(faces)


def bm_torus(bm, center, major, minor, material_index=0, major_segments=24, minor_segments=8, matrix=None):
    """Torus in the XY plane centred at `center` (optionally transformed by matrix)."""
    c = V(center)
    verts = []
    for i in range(major_segments):
        a = 2 * math.pi * i / major_segments
        ring = []
        for j in range(minor_segments):
            b = 2 * math.pi * j / minor_segments
            p = Vector(((major + minor * math.cos(b)) * math.cos(a),
                        (major + minor * math.cos(b)) * math.sin(a),
                        minor * math.sin(b)))
            if matrix is not None:
                p = matrix @ p
            ring.append(bm.verts.new(c + p))
        verts.append(ring)
    faces = []
    for i in range(major_segments):
        r0, r1 = verts[i], verts[(i + 1) % major_segments]
        for j in range(minor_segments):
            j2 = (j + 1) % minor_segments
            faces.append(bm.faces.new((r0[j], r1[j], r1[j2], r0[j2])))
    for f in faces:
        f.material_index = material_index
        f.smooth = True
    bm.normal_update()
    return set(faces)


def finish_bmesh(bm):
    """Recalculate normals consistently for the whole bmesh."""
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.normal_update()
    return bm
