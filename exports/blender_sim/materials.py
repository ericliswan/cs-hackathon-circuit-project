"""
materials.py — PBR material factory with caching (Blender 4.1 Principled BSDF).

All colours are linear RGB(A) tuples.  `get_material(name, ...)` returns the existing
material of that name if it already exists (so rebuilding the scene never duplicates
materials), otherwise creates it.
"""
import bpy

# Named colours used across the scene (linear RGBA).
PALETTE = {
    "board_white":   (0.93, 0.90, 0.76, 1.0),   # ivory, like the reference board
    "board_edge":    (0.85, 0.82, 0.68, 1.0),
    "hole_dark":     (0.03, 0.03, 0.03, 1.0),
    "hole_metal":    (0.55, 0.55, 0.58, 1.0),
    "rail_red":      (0.80, 0.05, 0.03, 1.0),
    "rail_blue":     (0.02, 0.12, 0.75, 1.0),
    "label_dark":    (0.05, 0.05, 0.06, 1.0),
    "carrier_green": (0.02, 0.16, 0.08, 1.0),
    "bench_wood":    (0.36, 0.24, 0.13, 1.0),
    "panel_grey":    (0.16, 0.17, 0.19, 1.0),
    "button_grey":   (0.42, 0.44, 0.47, 1.0),
    "post_red":      (0.80, 0.04, 0.03, 1.0),
    "post_black":    (0.02, 0.02, 0.02, 1.0),
    "lead_silver":   (0.70, 0.70, 0.72, 1.0),
    "resistor_tan":  (0.82, 0.62, 0.36, 1.0),
    "band_black":    (0.01, 0.01, 0.01, 1.0),
    "band_brown":    (0.30, 0.12, 0.03, 1.0),
    "band_red":      (0.75, 0.04, 0.02, 1.0),
    "band_orange":   (0.85, 0.30, 0.02, 1.0),
    "band_yellow":   (0.90, 0.75, 0.05, 1.0),
    "band_green":    (0.03, 0.40, 0.08, 1.0),
    "band_blue":     (0.03, 0.08, 0.60, 1.0),
    "band_violet":   (0.35, 0.05, 0.50, 1.0),
    "band_grey":     (0.40, 0.40, 0.40, 1.0),
    "band_white":    (0.90, 0.90, 0.90, 1.0),
    "band_gold":     (0.75, 0.55, 0.12, 1.0),
    "cap_ceramic":   (0.85, 0.50, 0.18, 1.0),
    "cap_blue":      (0.02, 0.06, 0.35, 1.0),
    "cap_black":     (0.03, 0.03, 0.035, 1.0),
    "cap_stripe":    (0.85, 0.85, 0.80, 1.0),
    "copper":        (0.72, 0.38, 0.18, 1.0),
    "ferrite":       (0.12, 0.12, 0.13, 1.0),
    "wire_red":      (0.80, 0.05, 0.03, 1.0),
    "wire_yellow":   (0.90, 0.75, 0.05, 1.0),
    "wire_green":    (0.04, 0.50, 0.10, 1.0),
    "wire_blue":     (0.03, 0.15, 0.80, 1.0),
    "wire_black":    (0.02, 0.02, 0.02, 1.0),
    "wire_white":    (0.90, 0.90, 0.88, 1.0),
    "probe_yellow":  (0.95, 0.80, 0.05, 1.0),
    "probe_black":   (0.03, 0.03, 0.03, 1.0),
    "marker_cyan":   (0.10, 0.90, 1.00, 1.0),
    "marker_orange": (1.00, 0.55, 0.05, 1.0),
    "accent_resistor":  (0.82, 0.62, 0.36, 1.0),
    "accent_capacitor": (0.15, 0.45, 0.90, 1.0),
    "accent_inductor":  (0.72, 0.38, 0.18, 1.0),
    "accent_wire":      (0.10, 0.70, 0.25, 1.0),
    "accent_probe":     (0.95, 0.80, 0.05, 1.0),
    "accent_delete":    (0.85, 0.10, 0.08, 1.0),
}


def _rgba(color):
    c = tuple(color)
    return c if len(c) == 4 else (c[0], c[1], c[2], 1.0)


def get_material(name, base_color=(0.8, 0.8, 0.8, 1.0), metallic=0.0, roughness=0.5,
                 emission_color=None, emission_strength=0.0, alpha=1.0, *, update=False):
    """Return (and cache) a Principled BSDF material called `name`.

    If a material of that name exists it is returned unchanged unless update=True.
    """
    mat = bpy.data.materials.get(name)
    if mat is not None and not update:
        return mat
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        out = nodes.get("Material Output") or nodes.new("ShaderNodeOutputMaterial")
        mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    base = _rgba(base_color)
    bsdf.inputs["Base Color"].default_value = base
    bsdf.inputs["Metallic"].default_value = float(metallic)
    bsdf.inputs["Roughness"].default_value = float(roughness)
    bsdf.inputs["Alpha"].default_value = float(alpha)
    if emission_color is not None or emission_strength:
        bsdf.inputs["Emission Color"].default_value = _rgba(emission_color or base)
        bsdf.inputs["Emission Strength"].default_value = float(emission_strength)
    else:
        bsdf.inputs["Emission Strength"].default_value = 0.0
    # Viewport (solid mode) colour so the board still reads in Solid shading
    mat.diffuse_color = base
    mat.metallic = float(metallic)
    mat.roughness = float(roughness)
    if alpha < 1.0:
        mat.blend_method = "BLEND"
    return mat


def palette_material(key, **kw):
    """Material from a PALETTE key, e.g. palette_material('rail_red', roughness=0.6)."""
    return get_material(f"SIM_{key}", base_color=PALETTE[key], **kw)
