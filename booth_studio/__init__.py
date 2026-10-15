bl_info = {
    "name": "Booth Studio - Smart Lighting & Materials",
    "author": "Booth Studio",
    "version": (3, 0, 1),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Booth Studio",
    "description": "Repeatable softbox and booth spotlight rigs, materials, PBR import and render presets",
    "category": "Lighting",
}

import bpy
import math
import os
import re
from mathutils import Vector
from bpy.props import (PointerProperty, FloatProperty, IntProperty, EnumProperty,
                       BoolProperty, FloatVectorProperty, StringProperty)

PREFIX = "BS_"
OWNER = "booth_studio_v1"
LIGHT_COLL = "BS | Lighting Rig"


def settings(context):
    return context.scene.booth_studio


def ensure_collection(scene):
    coll = bpy.data.collections.get(LIGHT_COLL)
    if coll is None:
        coll = bpy.data.collections.new(LIGHT_COLL)
    if coll not in scene.collection.children[:]:
        scene.collection.children.link(coll)
    return coll


def selected_bounds(context):
    points = []
    for obj in context.selected_objects:
        if obj.type not in {'MESH', 'CURVE', 'FONT', 'SURFACE'} or obj.get('bs_owner') == OWNER:
            continue
        for corner in obj.bound_box:
            points.append(obj.matrix_world @ Vector(corner))
    if not points:
        return None
    low = Vector(tuple(min(v[i] for v in points) for i in range(3)))
    high = Vector(tuple(max(v[i] for v in points) for i in range(3)))
    return low, high


def booth_bounds(context, s):
    bounds = selected_bounds(context) if s.use_selection else None
    if bounds:
        lo, hi = bounds
        center = (lo + hi) * 0.5
        # Small detail selections may be flat; fallback to configured dimensions.
        dims = Vector((max(hi.x-lo.x, .25), max(hi.y-lo.y, .25), max(hi.z-lo.z, .25)))
        return center, dims, lo.z, hi.z
    center = Vector(tuple(s.center))
    dims = Vector((s.width, s.depth, s.height))
    return center, dims, center.z, center.z + s.height


def aim_at(obj, target):
    direction = Vector(target) - obj.location
    if direction.length_squared > 1e-8:
        obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()


def managed_lights(scene):
    return {str(o.get('bs_id')): o for o in scene.objects
            if o.type == 'LIGHT' and o.get('bs_owner') == OWNER and o.get('bs_id')}


def light_upsert(scene, coll, key, light_type, location, target, power, rgb,
                 size=None, size_y=None, cone=None, blend=None):
    existing = managed_lights(scene).get(key)
    if existing is not None and existing.data.type != light_type:
        bpy.data.objects.remove(existing, do_unlink=True)
        existing = None
    if existing is None:
        data = bpy.data.lights.new(PREFIX + key, light_type)
        existing = bpy.data.objects.new(PREFIX + key, data)
        coll.objects.link(existing)
    elif coll not in existing.users_collection:
        coll.objects.link(existing)
    # A duplicated Blender light can share its datablock; isolate before editing.
    if existing.data.users > 1:
        existing.data = existing.data.copy()
    existing['bs_owner'] = OWNER
    existing['bs_id'] = key
    existing.name = PREFIX + key
    existing.data.name = PREFIX + key
    existing.location = location
    aim_at(existing, target)
    existing.data.energy = max(.0, power)
    existing.data.color = rgb
    if light_type == 'AREA':
        existing.data.shape = 'RECTANGLE'
        existing.data.size = max(.05, size or 1.0)
        existing.data.size_y = max(.05, size_y or 1.0)
    elif light_type == 'SPOT':
        existing.data.spot_size = cone if cone is not None else math.radians(40)
        existing.data.spot_blend = blend if blend is not None else .4
    return existing


def spotlight_positions(count, width, depth):
    # Aspect-ratio-aware grid, row-major, with no duplicates and symmetric margins.
    cols = min(count, max(1, math.ceil(math.sqrt(count * width / max(depth, .01)))))
    rows = math.ceil(count / cols)
    out = []
    for row in range(rows):
        slots = min(cols, count - row * cols)
        y = (row + .5) / rows - .5
        for col in range(slots):
            x = (col + .5) / slots - .5
            # Offset incomplete rows by centering positions, never stack spotlights.
            out.append((x * width * .85, y * depth * .85))
    return out


def generate_booth(context):
    s = settings(context)
    scene = context.scene
    coll = ensure_collection(scene)
    center, dims, floor, top = booth_bounds(context, s)
    x, y, z = center
    width, depth = dims.x, dims.y
    # The stage/front is -Y. Softboxes are deliberately outside booth bounds.
    level = s.intensity / 100.0
    target = Vector((x, y, floor + min(max(dims.z * .5, 1.0), 2.0)))
    area_base = 420.0 * max(.6, width / 4.0)
    softboxes = [
        ('SOFT_KEY', (-.65*width, -.85*depth, top+1.3), 1.0, (1., .95, .89), (max(1.0, width*.55), 1.8)),
        ('SOFT_FILL', (.67*width, -.55*depth, top+.8), .60, (.9, .95, 1.), (max(1., width*.45), 1.7)),
        ('SOFT_RIM', (0, .72*depth, top+1.4), .82, (1., 1., 1.), (max(1., width*.55), 1.3)),
    ]
    expected = set()
    for key, offset, factor, color, shape in softboxes:
        expected.add(key)
        pos = Vector((x+offset[0], y+offset[1], offset[2]))
        light_upsert(scene, coll, key, 'AREA', pos, target, area_base*factor*level, color, *shape)
    # Prefer above the top of selected geometry and a fixed minimum clearance.
    spot_height = max(top + .8, floor + s.mount_height)
    spot_power = s.spot_power * level
    for i, (dx, dy) in enumerate(spotlight_positions(s.spot_count, width, depth), 1):
        key = 'SPOT_%02d' % i
        expected.add(key)
        pos = Vector((x+dx, y+dy, spot_height))
        spot_target = Vector((x+dx*.72, y+dy*.72, floor + s.target_height))
        light_upsert(scene, coll, key, 'SPOT', pos, spot_target, spot_power,
                     (1., .96, .89), cone=math.radians(s.spot_angle), blend=.45)
    # Remove stale IDs and duplicate managed IDs (including copies made by the user).
    seen = set()
    for obj in list(scene.objects):
        if obj.type != 'LIGHT' or obj.get('bs_owner') != OWNER:
            continue
        key = obj.get('bs_id')
        if key not in expected or key in seen:
            bpy.data.objects.remove(obj, do_unlink=True)
        else:
            seen.add(key)
    return len(expected)


class BS_OT_generate(bpy.types.Operator):
    bl_idname = 'booth_studio.generate'
    bl_label = 'Generate / Update Lighting'
    bl_description = 'Create or update the managed lighting rig without adding duplicates'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        try:
            count = generate(context)
        except Exception as exc:
            self.report({'ERROR'}, 'Lighting error: ' + str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'Updated {count} lights; no duplicate managed IDs')
        return {'FINISHED'}


class BS_OT_clear(bpy.types.Operator):
    bl_idname = 'booth_studio.clear'
    bl_label = 'Remove Booth Studio Lights'
    bl_description = 'Remove only lights generated by Booth Studio'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        objs = [o for o in context.scene.objects if o.type == 'LIGHT' and o.get('bs_owner') == OWNER]
        for obj in objs:
            bpy.data.objects.remove(obj, do_unlink=True)
        self.report({'INFO'}, f'Removed {len(objs)} Booth Studio lights')
        return {'FINISHED'}


MATERIALS = [
    ('WHITE_MDF', 'White MDF', 'Matte painted white board'),
    ('BLACK_MDF', 'Black MDF', 'Matte black exhibition board'),
    ('OAK', 'Natural Oak', 'Warm oak veneer'),
    ('WALNUT', 'Walnut', 'Dark walnut veneer'),
    ('ALUMINUM', 'Brushed Aluminum', 'Satin metallic frame'),
    ('BLACK_METAL', 'Black Metal', 'Powder-coated frame'),
    ('ACRYLIC', 'Glossy Acrylic', 'Reflective white acrylic'),
    ('FABRIC', 'Fabric Beige', 'Fabric surface'),
    ('CARPET', 'Expo Carpet', 'Charcoal trade-show carpet'),
    ('LED', 'LED Emission', 'White lightbox or glowing sign'),
]


def _bs_node(nodes, node_type, label, x, y):
    node = nodes.new(node_type)
    node.label = label
    node.location = (x, y)
    return node


def _bs_ramp(nodes, links, source, colors, x, y, label):
    ramp = _bs_node(nodes, 'ShaderNodeValToRGB', label, x, y)
    elements = ramp.color_ramp.elements
    elements.remove(elements[1])
    for index, (position, rgba) in enumerate(colors):
        element = elements[0] if index == 0 else elements.new(position)
        element.position = position
        element.color = rgba
    links.new(source, ramp.inputs['Fac'])
    return ramp


def create_material(kind, force_update=False, texture_scale=1.0, bump_strength=0.2):
    """Create/rebuild a reusable, layered procedural material; never touch unmanaged names."""
    label = dict((key, value) for key, value, _ in MATERIALS)[kind]
    base = 'BS | ' + label
    name = base
    mat = bpy.data.materials.get(name)
    if mat is not None and mat.get('bs_owner') != OWNER:
        name = base + ' (Managed)'
        mat = bpy.data.materials.get(name)
    if mat is not None and mat.get('bs_owner') == OWNER and not force_update and mat.get('bs_material_version') == 2:
        return mat
    if mat is None:
        mat = bpy.data.materials.new(name)
    elif mat.get('bs_owner') != OWNER:
        raise RuntimeError('Refusing to alter a material not owned by Booth Studio')
    mat['bs_owner'] = OWNER
    mat['bs_material_version'] = 2
    mat['bs_kind'] = kind
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    out = _bs_node(nodes, 'ShaderNodeOutputMaterial', 'Material Output', 960, 160)
    bsdf = _bs_node(nodes, 'ShaderNodeBsdfPrincipled', 'Surface Finish', 680, 160)
    links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    tex = _bs_node(nodes, 'ShaderNodeTexCoord', 'Coordinates - Generated / UV', -1150, 150)
    mapping = _bs_node(nodes, 'ShaderNodeMapping', 'Real-world direction / Scale', -930, 150)
    mapping.inputs['Scale'].default_value = (texture_scale,)*3
    # Generated coords are robust for booth meshes without manually prepared UVs.
    links.new(tex.outputs['Generated'], mapping.inputs['Vector'])
    noise = _bs_node(nodes, 'ShaderNodeTexNoise', 'Fine Surface Variation', -680, -220)
    noise.inputs['Scale'].default_value = 115 if kind in {'FABRIC','CARPET'} else 35
    noise.inputs['Detail'].default_value = 3.0
    noise.inputs['Roughness'].default_value = .65
    links.new(mapping.outputs['Vector'], noise.inputs['Vector'])
    bump = _bs_node(nodes, 'ShaderNodeBump', 'Micro Surface Bump', 450, -220)
    bump.inputs['Strength'].default_value = bump_strength
    bump.inputs['Distance'].default_value = .004 if kind in {'OAK','WALNUT','ALUMINUM'} else .0015
    links.new(noise.outputs['Fac'], bump.inputs['Height'])
    links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
    colors = {
        'WHITE_MDF': [(0.18,(.68,.67,.65,1)),(.80,(.9,.89,.86,1))],
        'BLACK_MDF': [(0.15,(.016,.019,.023,1)),(.85,(.052,.055,.060,1))],
        'OAK': [(0.12,(.18,.075,.025,1)),(.38,(.39,.20,.065,1)),(.66,(.67,.40,.16,1)),(.9,(.79,.57,.32,1))],
        'WALNUT': [(0.1,(.045,.018,.009,1)),(.40,(.13,.055,.024,1)),(.70,(.28,.13,.061,1)),(.92,(.37,.20,.10,1))],
        'ALUMINUM': [(0.1,(.39,.43,.47,1)),(.9,(.72,.76,.79,1))],
        'BLACK_METAL': [(0.1,(.025,.03,.035,1)),(.9,(.075,.08,.085,1))],
        'ACRYLIC': [(0.1,(.77,.81,.86,1)),(.9,(.96,.97,.98,1))],
        'FABRIC': [(0.15,(.25,.18,.115,1)),(.85,(.58,.43,.31,1))],
        'CARPET': [(0.10,(.055,.065,.085,1)),(.9,(.15,.17,.20,1))],
        'LED': [(0.0,(1.,.94,.82,1)),(1.,(1.,1.,1.,1))],
    }
    if kind in {'OAK','WALNUT','ALUMINUM'}:
        # Stretch the wave-space on the grain axis to create elongated natural streaks.
        stretch = _bs_node(nodes, 'ShaderNodeVectorMath', 'Elongated Grain Direction', -705, 230)
        stretch.operation = 'MULTIPLY'
        stretch.inputs[1].default_value = (1.8, 30., 4.) if kind in {'OAK','WALNUT'} else (2., 65., 2.)
        links.new(mapping.outputs['Vector'], stretch.inputs[0])
        wave = _bs_node(nodes, 'ShaderNodeTexWave', 'Wood Fibers / Metal Brushing', -465, 245)
        wave.wave_type = 'BANDS'
        wave.bands_direction = 'X'
        wave.inputs['Scale'].default_value = 3.2 if kind in {'OAK','WALNUT'} else 5.0
        wave.inputs['Distortion'].default_value = 4.6 if kind in {'OAK','WALNUT'} else .25
        wave.inputs['Detail'].default_value = 3
        links.new(stretch.outputs['Vector'], wave.inputs['Vector'])
        grain = _bs_node(nodes, 'ShaderNodeMixRGB', 'Grain + Micro Noise', -195, 150)
        grain.blend_type = 'MULTIPLY'
        grain.inputs[0].default_value = .22 if kind in {'OAK','WALNUT'} else .10
        links.new(wave.outputs['Color'], grain.inputs[1])
        links.new(noise.outputs['Fac'], grain.inputs[2])
        grain_source = grain.outputs[0]
        links.new(wave.outputs['Fac'], bump.inputs['Height'])
    else:
        grain_source = noise.outputs['Fac']
    ramp = _bs_ramp(nodes, links, grain_source, colors[kind], 90, 220, 'Surface Color Variation')
    links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
    rough = _bs_ramp(nodes, links, noise.outputs['Fac'],
                     [(.13,(.33,.33,.33,1)),(.88,(.68,.68,.68,1))],
                     65, -90, 'Variable Roughness')
    links.new(rough.outputs['Color'], bsdf.inputs['Roughness'])
    if kind in {'WHITE_MDF','BLACK_MDF'}:
        bump.inputs['Strength'].default_value *= .30
        bump.inputs['Distance'].default_value = .00035
    elif kind in {'OAK','WALNUT'}:
        bump.inputs['Strength'].default_value *= .6
    elif kind in {'FABRIC','CARPET'}:
        vor = _bs_node(nodes, 'ShaderNodeTexVoronoi', 'Micro Fiber Cells', -460, -530)
        vor.inputs['Scale'].default_value = 160 if kind == 'FABRIC' else 230
        links.new(mapping.outputs['Vector'], vor.inputs['Vector'])
        bump.inputs['Distance'].default_value = .0015 if kind == 'FABRIC' else .0025
        links.new(vor.outputs['Distance'], bump.inputs['Height'])
    elif kind == 'ACRYLIC':
        bump.inputs['Strength'].default_value *= .08
        bump.inputs['Distance'].default_value = .0001
        bsdf.inputs['Coat Weight'].default_value = .45
        bsdf.inputs['Coat Roughness'].default_value = .10
    elif kind == 'ALUMINUM':
        bsdf.inputs['Metallic'].default_value = 1.
        if 'Anisotropic IOR Level' in bsdf.inputs:
            bsdf.inputs['Anisotropic IOR Level'].default_value = .5
        elif 'Anisotropic' in bsdf.inputs:
            bsdf.inputs['Anisotropic'].default_value = .5
        bump.inputs['Strength'].default_value *= .3
    elif kind == 'BLACK_METAL':
        bsdf.inputs['Metallic'].default_value = .8
        bump.inputs['Strength'].default_value *= .2
    elif kind == 'LED':
        bsdf.inputs['Emission Color'].default_value = (1.,.94,.82,1.)
        bsdf.inputs['Emission Strength'].default_value = 3.
        bump.inputs['Strength'].default_value = 0.0
    return mat


def apply_to_selection(context, mat):
    targets = [o for o in context.selected_objects if o.type == 'MESH']
    for obj in targets:
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)
    return len(targets)


class BS_OT_material(bpy.types.Operator):
    bl_idname = 'booth_studio.apply_material'
    bl_label = 'Apply Material'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        if not any(o.type == 'MESH' for o in context.selected_objects):
            self.report({'WARNING'}, 'Select one or more mesh objects first')
            return {'CANCELLED'}
        s = settings(context)
        mat = create_material(s.material, False, s.material_scale, s.material_bump).copy()
        mat.name += ' | Selection'
        configure_mapping(mat, s)
        count = apply_to_selection(context, mat)
        self.report({'INFO'}, f'Applied {mat.name} to {count} objects')
        return {'FINISHED'}


def find_map(files, names):
    for filepath in files:
        stem = os.path.splitext(os.path.basename(filepath))[0].lower()
        # Token-aware match where possible, with common texture naming conventions.
        for word in names:
            if re.search(r'(^|[_\-. ])' + re.escape(word) + r'($|[_\-. 0-9])', stem):
                return filepath
    return None


class BS_OT_import_pbr(bpy.types.Operator):
    bl_idname = 'booth_studio.import_pbr'
    bl_label = 'Import Local PBR Folder'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        try:
            mat = import_pbr(bpy.path.abspath(settings(context).pbr_folder), settings(context))
            apply_to_selection(context, mat)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class BS_OT_render(bpy.types.Operator):
    bl_idname = 'booth_studio.render_preset'
    bl_label = 'Apply Render Preset'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        scene = context.scene
        preset = settings(context).render_preset
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = {'PREVIEW': 48, 'DRAFT': 160, 'FINAL': 384}[preset]
        scene.cycles.use_denoising = True
        scene.render.resolution_percentage = {'PREVIEW': 50, 'DRAFT': 100, 'FINAL': 100}[preset]
        try:
            scene.view_settings.view_transform = 'AgX'
        except (TypeError, ValueError):
            pass
        self.report({'INFO'}, f'Applied {preset} Cycles preset')
        return {'FINISHED'}


class BS_Settings(bpy.types.PropertyGroup):
    use_selection: BoolProperty(name='Use Selected Booth Bounds', default=True,
         description='Calculate the booth extents from selected geometry, or use manual dimensions')
    width: FloatProperty(name='Width', default=6., min=.25, max=100., unit='LENGTH')
    depth: FloatProperty(name='Depth', default=4., min=.25, max=100., unit='LENGTH')
    height: FloatProperty(name='Booth Height', default=3., min=.25, max=100., unit='LENGTH')
    center: FloatVectorProperty(name='Origin / Floor Center', size=3, default=(0.,0.,0.), subtype='TRANSLATION')
    intensity: FloatProperty(name='Intensity', default=105., min=0., max=300., subtype='PERCENTAGE', description='Overall light power multiplier')
    spot_count: IntProperty(name='Spotlights', default=8, min=1, max=64)
    spot_power: FloatProperty(name='Spot Power (W)', default=220., min=0., max=100000.)
    spot_angle: FloatProperty(name='Spot Beam Angle', default=42., min=5., max=170.)
    mount_height: FloatProperty(name='Minimum Mount Height', default=3.8, min=.5, max=100., unit='LENGTH')
    target_height: FloatProperty(name='Target Height', default=1.2, min=0., max=30., unit='LENGTH')
    material: EnumProperty(name='Material', items=MATERIALS, default='WHITE_MDF')
    material_scale: FloatProperty(name='Texture Scale', default=1.0, min=0.01, max=100.0, description='Scale of generated procedural textures')
    material_bump: FloatProperty(name='Bump Strength', default=.2, min=0, max=1, subtype='FACTOR')
    material_rebuild: BoolProperty(name='Rebuild Existing', default=True, description='Update the existing Booth Studio material nodes with current settings')
    pbr_folder: StringProperty(name='PBR Texture Folder', subtype='DIR_PATH')
    render_preset: EnumProperty(name='Quality', items=[
        ('PREVIEW', 'Preview', 'Fast preview'), ('DRAFT', 'Client Draft', 'Balanced'),
        ('FINAL', 'Final', 'High quality')], default='DRAFT')


class BS_PT_main(bpy.types.Panel):
    bl_label = 'Booth Studio'
    bl_idname = 'BS_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Booth Studio'
    def draw(self, context):
        layout = self.layout
        layout.label(text='Lighting + Materials + Render', icon='LIGHT_AREA')
        layout.label(text='Select booth geometry or enter dimensions.')


class BS_PT_lighting(bpy.types.Panel):
    bl_label = 'Smart Lighting'
    bl_parent_id = 'BS_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {'DEFAULT_CLOSED'}
    def draw(self, context):
        s = settings(context)
        col = self.layout.column(align=True)
        col.prop(s, 'lighting_mode')
        col.prop(s, 'rig_rotation')
        col.prop(s, 'temperature')
        col.label(text='Generate updates only managed lights')
        col.prop(s, 'use_selection')
        if not s.use_selection:
            col.prop(s, 'width'); col.prop(s, 'depth'); col.prop(s, 'height'); col.prop(s, 'center')
        col.separator()
        col.prop(s, 'intensity', slider=True)
        col.prop(s, 'spot_count')
        col.prop(s, 'spot_power')
        col.prop(s, 'spot_angle')
        col.prop(s, 'mount_height')
        col.prop(s, 'target_height')
        col.separator()
        col.operator('booth_studio.generate', icon='LIGHT_SPOT')
        col.operator('booth_studio.clear', icon='TRASH')


class BS_PT_materials(bpy.types.Panel):
    bl_label = 'Materials Library'
    bl_parent_id = 'BS_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    def draw(self, context):
        s = settings(context)
        col = self.layout.column(align=True)
        col.label(text='Select meshes, then apply:')
        col.prop(s, 'material', text='')
        col.prop(s, 'material_scale')
        col.prop(s, 'material_bump', slider=True)
        col.prop(s, 'coordinate_mode')
        col.prop(s, 'texture_rotation')
        col.prop(s, 'texture_stretch')
        col.prop(s, 'texture_offset')
        col.prop(s, 'tile_size')
        col.operator('booth_studio.update_mapping')
        col.operator('booth_studio.apply_material', icon='MATERIAL')
        col.label(text='Real wood: search Wood in Free Asset Libraries')
        col.label(text='UV: NormalGL / Box: height-based bump')
        col.separator()
        col.label(text='Local PBR Textures')
        col.prop(s, 'pbr_folder', text='Folder')
        col.operator('booth_studio.import_pbr', icon='IMPORT')
        col.label(text='Maps: Color / Rough / Metal / Normal')


class BS_PT_render(bpy.types.Panel):
    bl_label = 'Render Presets'
    bl_parent_id = 'BS_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {'DEFAULT_CLOSED'}
    def draw(self, context):
        s = settings(context)
        col = self.layout.column(align=True)
        col.prop(s, 'render_preset', text='Quality')
        col.operator('booth_studio.render_preset', icon='RENDER_STILL')
        col.label(text='Cycles / Denoising / AgX')


CLASSES = (BS_Settings, BS_OT_generate, BS_OT_clear, BS_OT_material,
           BS_OT_import_pbr, BS_OT_render, BS_PT_main, BS_PT_lighting,
           BS_PT_materials, BS_PT_render)


from .pro import install
install(globals())
from . import studio
studio.install(globals())


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.booth_studio = PointerProperty(type=BS_Settings)
    studio.ready()


def unregister():
    studio.shutdown()
    from .pro import shutdown
    shutdown()
    del bpy.types.Scene.booth_studio
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == '__main__':
    register()
