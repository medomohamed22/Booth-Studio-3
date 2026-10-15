import bpy, math, os, threading, queue, webbrowser
from pathlib import Path
from mathutils import Vector, Matrix
from bpy.props import (EnumProperty, FloatProperty, FloatVectorProperty, StringProperty,
                       IntProperty, CollectionProperty)
from . import library, thumbnails

MODES=[('BOOTH','Booth - Exhibition','Softboxes and ceiling spot grid'),
       ('BOOTH_WARM','Booth - Warm Evening','Warm exhibition mood'),
       ('PRODUCT','Product - Soft Studio','Large key, fill and overhead'),
       ('JEWELRY','Jewelry - Contrast','Small specular sources and strip edges'),
       ('CAR','Car - Strip Studio','Long reflection strips and roof light'),
       ('CAR_DRAMA','Car - Dramatic','Low fill, strong rim and strips')]
_jobs={}
_generation=0

def shutdown():
    global _generation
    _generation+=1
    thumbnails.shutdown()

def kelvin(k):
    t=k/100
    r=255 if t<=66 else 329.698727446*((t-60)**-.1332047592)
    g=99.4708025861*math.log(t)-161.1195681661 if t<=66 else 288.1221695283*((t-60)**-.0755148492)
    b=255 if t>=66 else (0 if t<=19 else 138.5177312231*math.log(t-10)-305.0447927307)
    return tuple(max(0,min(1,c/255)) for c in (r,g,b))

def mapping_nodes(mat):
    nodes=mat.node_tree.nodes; links=mat.node_tree.links
    coord=next((n for n in nodes if n.type=='TEX_COORD'),None) or nodes.new('ShaderNodeTexCoord')
    mapping=next((n for n in nodes if n.type=='MAPPING'),None) or nodes.new('ShaderNodeMapping')
    mapping.name='BS Mapping'; coord.location=(-1050,100); mapping.location=(-850,100)
    return coord,mapping

def configure_mapping(mat,s):
    coord,m=mapping_nodes(mat); links=mat.node_tree.links
    # Object coordinates are in object units: compensate object scale per assignment.
    links.new(coord.outputs[s.coordinate_mode],m.inputs['Vector'])
    m.inputs['Rotation'].default_value=s.texture_rotation
    m.inputs['Location'].default_value=s.texture_offset
    m.inputs['Scale'].default_value=tuple(s.material_scale*v/s.tile_size for v in s.texture_stretch)
    for n in mat.node_tree.nodes:
        if n.type=='TEX_IMAGE':
            n.projection='FLAT' if s.coordinate_mode=='UV' else 'BOX'
            n.projection_blend=.2
            links.new(m.outputs['Vector'],n.inputs['Vector'])
        elif n.type=='BUMP': n.inputs['Strength'].default_value=s.material_bump
        elif n.type=='NORMAL_MAP':
            # Tangent normal maps are only valid with UV projection.
            n.inputs['Strength'].default_value=s.material_bump if s.coordinate_mode=='UV' else 0
    if s.coordinate_mode=='Object':
        units=bpy.context.scene.unit_settings.scale_length
        m.inputs['Scale'].default_value=tuple(v*units for v in m.inputs['Scale'].default_value)
    mat['bs_tile_size']=s.tile_size
    mat['bs_coordinates']=s.coordinate_mode

def import_pbr(folder,s):
    if not os.path.isdir(folder): raise ValueError('Choose an existing PBR folder')
    paths=sorted(str(p) for p in Path(folder).iterdir() if p.suffix.lower() in {'.png','.jpg','.jpeg','.exr','.tif','.tiff','.webp'})
    maps={
        'Base Color':find_map(paths,('basecolor','base_color','albedo','diffuse','color','diff')),
        'Roughness':find_map(paths,('roughness','rough')),
        'Metallic':find_map(paths,('metallic','metalness','metal')),
        'Normal':find_map(paths,('normalgl','normal_opengl','nor_gl','normal','nor')),
        'Height':find_map(paths,('displacement','height','disp')),
        'AO':find_map(paths,('ao','ambientocclusion','ambient_occlusion'))}
    if not maps['Base Color']: raise ValueError('No BaseColor/Albedo/Diffuse map found')
    # DX normals need Y inversion. Reject ambiguous DX-only input rather than shade incorrectly.
    if maps['Normal'] and any(w in Path(maps['Normal']).stem.lower() for w in ('normaldx','nor_dx')):
        raise ValueError('Use OpenGL normal map (NormalGL / nor_gl)')
    mat=bpy.data.materials.new('BS PBR | '+Path(folder).name); mat.use_nodes=True
    mat['bs_owner']=OWNER; mat['bs_source_folder']=folder
    nodes=mat.node_tree.nodes; links=mat.node_tree.links; shader=nodes.get('Principled BSDF')
    normal=None; height=None; color=None; ao=None
    for idx,(socket,path) in enumerate(maps.items()):
        if not path: continue
        n=nodes.new('ShaderNodeTexImage'); n.label=socket; n.name='BS '+socket; n.location=(-600,300-idx*250)
        # Isolate color-space settings from other materials using the same file.
        n.image=bpy.data.images.load(path,check_existing=False)
        n.image.colorspace_settings.name='sRGB' if socket=='Base Color' else 'Non-Color'
        if socket=='Normal':
            normal=nodes.new('ShaderNodeNormalMap'); normal.inputs['Strength'].default_value=s.material_bump
            links.new(n.outputs['Color'],normal.inputs['Color']); links.new(normal.outputs['Normal'],shader.inputs['Normal'])
        elif socket=='Height': height=n
        elif socket=='AO': ao=n
        else:
            links.new(n.outputs['Color'],shader.inputs[socket])
            if socket=='Base Color': color=n
    if ao and color:
        mix=nodes.new('ShaderNodeMixRGB'); mix.blend_type='MULTIPLY'; mix.inputs[0].default_value=.35
        links.new(color.outputs['Color'],mix.inputs[1]); links.new(ao.outputs['Color'],mix.inputs[2]); links.new(mix.outputs[0],shader.inputs['Base Color'])
    if height:
        bump=nodes.new('ShaderNodeBump'); bump.inputs['Strength'].default_value=s.material_bump; bump.inputs['Distance'].default_value=.002
        links.new(height.outputs['Color'],bump.inputs['Height'])
        if normal: links.new(normal.outputs['Normal'],bump.inputs['Normal'])
        links.new(bump.outputs['Normal'],shader.inputs['Normal'])
    configure_mapping(mat,s)
    return mat

def generate(context):
    s=settings(context); scene=context.scene
    center,dims,floor,top=booth_bounds(context,s)
    if s.lighting_mode in {'BOOTH','BOOTH_WARM'}:
        count=generate_booth(context)
    else:
        coll=ensure_collection(scene); x,y,_=center; w,d,h=dims
        z=floor+h*.5; target=Vector((x,y,z)); base=max(.1,w*d)*120*s.intensity/100
        # Offsets relative to center; area dimensions shape reflected highlights.
        rigs={
          'PRODUCT':[((-.7,-.7,1.3),1,(1.1,.9)),((.8,-.3,.7),.4,(.8,.8)),((0,.4,1.5),.8,(1,.7))],
          'JEWELRY':[((-.6,-.6,.8),1,(.25,.25)),((.8,.1,.5),.65,(.1,1.1)),((-.4,.8,.7),1.2,(.1,1.2)),((0,0,1.5),.4,(.6,.6))],
          'CAR':[((-.75,0,1),1.2,(.12,1.5)),((.75,0,1),.9,(.12,1.5)),((0,.1,1.8),1.5,(.75,1.3)),((0,-1,.6),.35,(.6,.35))],
          'CAR_DRAMA':[((-.8,0,.7),1.1,(.08,1.6)),((.8,.3,.8),.3,(.08,1.5)),((0,.8,1.3),1.4,(.7,.2))]}
        expected=set()
        for i,(offset,power,shape) in enumerate(rigs[s.lighting_mode]):
            key='STUDIO_%02d'%i; expected.add(key)
            pos=(x+offset[0]*w,y+offset[1]*d,z+offset[2]*max(h,.3))
            light_upsert(scene,coll,key,'AREA',pos,target,base*power,kelvin(s.temperature),max(.05,w*shape[0]),max(.05,d*shape[1]))
        seen=set()
        for o in list(scene.objects):
            if o.type=='LIGHT' and o.get('bs_owner')==OWNER:
                key=o.get('bs_id')
                if key not in expected or key in seen: bpy.data.objects.remove(o,do_unlink=True)
                else: seen.add(key)
        count=len(expected)
    rotation=Matrix.Rotation(s.rig_rotation,4,'Z')
    tint=kelvin(3200 if s.lighting_mode=='BOOTH_WARM' else s.temperature)
    for o in scene.objects:
        if o.type=='LIGHT' and o.get('bs_owner')==OWNER:
            o.location=center+rotation@(o.location-center)
            o.rotation_euler=(rotation.to_quaternion()@o.rotation_euler.to_quaternion()).to_euler()
            o.data.color=tint
    return count

def setup_hdri(context,path):
    s=settings(context)
    world=bpy.data.worlds.new('BS HDRI | '+Path(path).stem); world.use_nodes=True
    context.scene.world=world
    ns=world.node_tree.nodes; ls=world.node_tree.links; ns.clear()
    out=ns.new('ShaderNodeOutputWorld'); bg=ns.new('ShaderNodeBackground'); bg.name='BS Background'
    tex=ns.new('ShaderNodeTexEnvironment'); tex.image=bpy.data.images.load(path,check_existing=True)
    coord=ns.new('ShaderNodeTexCoord'); mapping=ns.new('ShaderNodeMapping'); mapping.name='BS HDRI Rotation'
    ls.new(coord.outputs['Generated'],mapping.inputs['Vector']); ls.new(mapping.outputs['Vector'],tex.inputs['Vector'])
    ls.new(tex.outputs['Color'],bg.inputs['Color']); ls.new(bg.outputs[0],out.inputs['Surface'])
    world['bs_owner']=OWNER; update_hdri(s,context)
    return world

def update_hdri(s,context):
    w=context.scene.world
    if not w or w.get('bs_owner')!=OWNER: return
    ns=w.node_tree.nodes
    if ns.get('BS Background'): ns['BS Background'].inputs['Strength'].default_value=s.hdri_strength
    if ns.get('BS HDRI Rotation'): ns['BS HDRI Rotation'].inputs['Rotation'].default_value[2]=s.hdri_rotation

class BS_OT_mapping(bpy.types.Operator):
    bl_idname='booth_studio.update_mapping'; bl_label='Update Selected Mapping'; bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        count=0
        for o in context.selected_objects:
            if o.type!='MESH' or not o.active_material or o.active_material.get('bs_owner')!=OWNER: continue
            # Always isolate material and mesh before changing shared datablocks.
            if o.data.users>1: o.data=o.data.copy()
            m=o.active_material.copy(); o.active_material=m; configure_mapping(m,settings(context))
            if settings(context).coordinate_mode=='Object':
                mapping=m.node_tree.nodes['BS Mapping']
                mapping.inputs['Scale'].default_value=tuple(mapping.inputs['Scale'].default_value[i]*abs(o.scale[i]) for i in range(3))
            count+=1
        self.report({'INFO'},'Updated %d selected materials'%count)
        return {'FINISHED'}

class BS_OT_hdri(bpy.types.Operator):
    bl_idname='booth_studio.local_hdri'; bl_label='Load Local HDRI'; bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        path=bpy.path.abspath(settings(context).hdri_path)
        if not os.path.isfile(path) or Path(path).suffix.lower() not in {'.hdr','.exr'}:
            self.report({'ERROR'},'Choose an HDR or EXR file'); return {'CANCELLED'}
        setup_hdri(context,path); return {'FINISHED'}

class BS_OT_quicksearch(bpy.types.Operator):
    bl_idname='booth_studio.quick_search'; bl_label='Find Real PBR Material'
    query:StringProperty(default='wood')
    def execute(self,context):
        s=settings(context); s.asset_type='1'; s.asset_query=self.query
        return bpy.ops.booth_studio.library(action='SEARCH')

class BS_PT_light_edit(bpy.types.Panel):
    bl_label='Fine Tune Individual Lights'; bl_parent_id='BS_PT_main'; bl_space_type='VIEW_3D'; bl_region_type='UI'
    bl_options={'DEFAULT_CLOSED'}
    def draw(self,context):
        s=settings(context); c=self.layout.column(); c.prop(s,'edit_light')
        o=s.edit_light
        if o and o.type=='LIGHT' and o.name in context.scene.objects:
            c.prop(o,'location'); c.prop(o,'rotation_euler')
            c.prop(o.data,'energy'); c.prop(o.data,'color')
            if o.data.type=='AREA': c.prop(o.data,'size'); c.prop(o.data,'size_y')
            elif o.data.type=='SPOT': c.prop(o.data,'spot_size'); c.prop(o.data,'spot_blend')
            c.prop(o,'hide_render')
            c.label(text='Generate resets manual light adjustments')

class BS_Asset(bpy.types.PropertyGroup):
    asset_id:StringProperty(); title:StringProperty(); kind:StringProperty(); provider:StringProperty(default='POLY')
    thumbnail_url:StringProperty()

class BS_OT_asset_pick(bpy.types.Operator):
    bl_idname='booth_studio.pick_asset'; bl_label='Select Asset'; bl_description='Select this preview for download/import'
    index:IntProperty()
    def execute(self,context):
        s=settings(context)
        if 0<=self.index<len(s.assets): s.asset_index=self.index
        return {'FINISHED'}

class BS_OT_asset_page(bpy.types.Operator):
    bl_idname='booth_studio.asset_page'; bl_label='Browse Photos'
    step:IntProperty(default=1)
    def execute(self,context):
        s=settings(context); pages=max(1,math.ceil(len(s.assets)/thumbnails.PAGE_SIZE))
        s.asset_page=max(0,min(pages-1,s.asset_page+self.step))
        thumbnails.start(context.scene)
        return {'FINISHED'}

class BS_UL_assets(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        layout.label(text=item.title,icon={'0':'WORLD','1':'MATERIAL','2':'MESH_CUBE'}.get(item.kind,'ASSET_MANAGER'))

def import_asset(context,path,kind,asset):
    if kind=='0': setup_hdri(context,path)
    elif kind=='1':
        mat=import_pbr(path,settings(context)); mat['bs_provider']='Poly Haven'; mat['bs_license']='CC0'; mat['bs_asset_id']=asset
        apply_to_selection(context,mat)
    else:
        with bpy.data.libraries.load(path,link=False) as (src,dst): dst.objects=list(src.objects)
        coll=bpy.data.collections.new('BS Asset | '+asset); context.scene.collection.children.link(coll)
        for o in dst.objects:
            if o is not None:
                coll.objects.link(o); o['bs_provider']='Poly Haven'; o['bs_license']='CC0'
        # Imported assets use downloaded dependency files, never run library scripts.
        imported_images={n.image for o in dst.objects if o is not None and o.type=='MESH' for m in o.data.materials if m and m.use_nodes for n in m.node_tree.nodes if n.type=='TEX_IMAGE' and n.image}
        for image in imported_images:
            if image.source=='FILE' and not image.packed_file and image.filepath.startswith('//'):
                candidate=Path(path).parent/image.filepath[2:]
                if candidate.is_file(): image.filepath=str(candidate)

class BS_OT_library(bpy.types.Operator):
    bl_idname='booth_studio.library'; bl_label='Library Task'; bl_options={'REGISTER','UNDO'}
    action:EnumProperty(items=[('SEARCH','Search',''),('IMPORT','Download / Import','')])
    def execute(self,context):
        s=settings(context)
        if s.library_busy: self.report({'WARNING'},'A library task is already running'); return {'CANCELLED'}
        root=bpy.path.abspath(s.cache_path); kind=s.asset_type; query=s.asset_query; resolution=s.asset_resolution; provider=s.asset_provider
        if not root: self.report({'ERROR'},'Choose a cache folder'); return {'CANCELLED'}
        if self.action=='IMPORT':
            if not s.assets or s.asset_index<0 or s.asset_index>=len(s.assets): return {'CANCELLED'}
            item=s.assets[s.asset_index]; asset=item.asset_id; kind=item.kind; provider=item.provider
            if kind=='1' and not any(o.type=='MESH' for o in context.selected_objects):
                self.report({'ERROR'},'Select meshes before importing a material'); return {'CANCELLED'}
        else: asset=''
        result=queue.Queue(); scene=context.scene; selected=list(context.selected_objects); action=self.action
        scene_id=scene.as_pointer(); generation=_generation
        s.library_busy=True; s.library_status='Searching...' if action=='SEARCH' else 'Downloading '+asset+'...'
        def worker():
            try:
                if provider=='AMBIENT': value=library.search_ambient(root,query) if action=='SEARCH' else library.fetch_ambient(root,asset,resolution)
                else: value=library.search(root,kind,query) if action=='SEARCH' else library.fetch(root,asset,kind,resolution)
                result.put((True,value))
            except Exception as exc: result.put((False,str(exc)))
        def finish():
            if generation!=_generation: return None
            if result.empty(): return .25
            ok,value=result.get()
            if not any(sc.as_pointer()==scene_id for sc in bpy.data.scenes): return None
            st=scene.booth_studio; st.library_busy=False
            if not ok: st.library_status='Error: '+value[:220]
            elif action=='SEARCH':
                st.assets.clear()
                for aid,meta in value:
                    it=st.assets.add(); it.asset_id=aid; it.title=meta['name']; it.kind=str(meta['type']); it.provider=provider
                    it.thumbnail_url=meta.get('thumbnail_url','')
                st.asset_index=0; st.library_status='%d results - %s / CC0'%(len(value),'ambientCG' if provider=='AMBIENT' else 'Poly Haven')
                st.asset_page=0; thumbnails.start(scene)
            elif bpy.context.scene!=scene or set(bpy.context.selected_objects)!=set(selected):
                st.library_status='Downloaded. Selection/scene changed; import again from cache.'
            else:
                try:
                    if bpy.ops.ed.undo_push.poll(): bpy.ops.ed.undo_push(message='Before Booth Studio asset import')
                    import_asset(bpy.context,value,kind,asset); st.library_status='Imported '+asset+' - Poly Haven / CC0'
                    if provider=='AMBIENT':
                        for o in selected:
                            if o.type=='MESH' and o.active_material: o.active_material['bs_provider']='ambientCG'
                        st.library_status='Imported '+asset+' - ambientCG / CC0'
                    if bpy.ops.ed.undo_push.poll(): bpy.ops.ed.undo_push(message='Booth Studio asset import')
                except Exception as exc: st.library_status='Import error: '+str(exc)[:200]
            for screen in bpy.data.screens:
                for area in screen.areas:
                    if area.type=='VIEW_3D': area.tag_redraw()
            return None
        threading.Thread(target=worker,daemon=True).start()
        bpy.app.timers.register(finish,first_interval=.25)
        return {'FINISHED'}

class BS_OT_source(bpy.types.Operator):
    bl_idname='booth_studio.open_source'; bl_label='Open Free Library'
    source:EnumProperty(items=[('POLY','Poly Haven',''),('AMBIENT','ambientCG',''),('PREVIEW','Selected Asset','')])
    def execute(self,context):
        s=settings(context)
        url='https://ambientcg.com/list' if self.source=='AMBIENT' else 'https://polyhaven.com'
        if self.source=='PREVIEW' and s.assets and 0<=s.asset_index<len(s.assets):
            item=s.assets[s.asset_index]; url=('https://ambientcg.com/a/' if item.provider=='AMBIENT' else 'https://polyhaven.com/a/')+item.asset_id
        webbrowser.open(url); return {'FINISHED'}

class BS_PT_library(bpy.types.Panel):
    bl_label='Free Asset Libraries - CC0'; bl_idname='BS_PT_library'; bl_parent_id='BS_PT_main'
    bl_space_type='VIEW_3D'; bl_region_type='UI'; bl_category='Booth Studio'
    def draw(self,context):
        s=settings(context); c=self.layout.column()
        c.label(text='No account / No API key / CC0')
        c.prop(s,'cache_path'); c.prop(s,'asset_provider')
        if s.asset_provider=='POLY': c.prop(s,'asset_type')
        else: c.label(text='ambientCG PBR Materials')
        c.prop(s,'asset_query')
        row=c.row(align=True)
        for label,query in [('Wood','wood'),('Fabric','fabric'),('Metal','metal')]:
            row.operator('booth_studio.quick_search',text=label).query=query
        row=c.row(); row.enabled=not s.library_busy; row.operator('booth_studio.library',text='Search',icon='VIEWZOOM').action='SEARCH'
        if s.assets:
            pages=math.ceil(len(s.assets)/thumbnails.PAGE_SIZE)
            nav=c.row(align=True)
            prev=nav.row(align=True); prev.enabled=s.asset_page>0
            prev.operator('booth_studio.asset_page',text='',icon='TRIA_LEFT').step=-1
            nav.label(text='%d / %d'%(s.asset_page+1,pages))
            nxt=nav.row(align=True); nxt.enabled=s.asset_page<pages-1
            nxt.operator('booth_studio.asset_page',text='',icon='TRIA_RIGHT').step=1
            grid=c.grid_flow(columns=2,even_columns=True,even_rows=True,align=True)
            begin=s.asset_page*thumbnails.PAGE_SIZE
            for i in range(begin,min(begin+thumbnails.PAGE_SIZE,len(s.assets))):
                item=s.assets[i]; box=grid.box(); photo=thumbnails.icon(item)
                if photo: box.template_icon(icon_value=photo,scale=5.0)
                else:
                    row=box.row(); row.scale_y=4
                    row.label(text='',icon={'0':'WORLD','1':'MATERIAL','2':'MESH_CUBE'}.get(item.kind,'IMAGE_DATA'))
                box.operator('booth_studio.pick_asset',text=item.title,depress=i==s.asset_index).index=i
            c.label(text=s.preview_status)
            if 0<=s.asset_index<len(s.assets): c.label(text='Selected: '+s.assets[s.asset_index].title)
        else: c.label(text='Search to browse asset photos',icon='IMAGE_DATA')
        c.prop(s,'asset_resolution')
        row=c.row(); row.enabled=not s.library_busy; row.operator('booth_studio.library',text='Download / Import',icon='IMPORT').action='IMPORT'
        c.operator('booth_studio.open_source',text='Preview Asset on Website',icon='URL').source='PREVIEW'
        c.label(text=s.library_status[:65])
        c.label(text='Offline: cached files / local PBR folders')
        c.operator('booth_studio.open_source',text='ambientCG - free local PBR downloads',icon='URL').source='AMBIENT'

class BS_PT_hdri(bpy.types.Panel):
    bl_label='HDRI Environment'; bl_parent_id='BS_PT_main'; bl_space_type='VIEW_3D'; bl_region_type='UI'
    def draw(self,context):
        s=settings(context); c=self.layout.column()
        c.prop(s,'hdri_path'); c.operator('booth_studio.local_hdri'); c.prop(s,'hdri_strength'); c.prop(s,'hdri_rotation')
        c.label(text='Load makes a separate World datablock')

def install(ns):
    global settings, find_map, OWNER, booth_bounds, ensure_collection, generate_booth, light_upsert, aim_at, apply_to_selection
    for name in ('settings','find_map','OWNER','booth_bounds','ensure_collection','generate_booth','light_upsert','aim_at'):
        globals()[name]=ns[name]
    def safe_apply(context,mat):
        targets=[o for o in context.selected_objects if o.type=='MESH']
        for o in targets:
            if o.data.users>1: o.data=o.data.copy()
            # Give every object its own real-scale mapping, independent of its transforms.
            m=mat.copy(); m.name=mat.name+' | '+o.name
            if settings(context).coordinate_mode=='Object':
                _,mapping=mapping_nodes(m)
                scale=mapping.inputs['Scale'].default_value
                mapping.inputs['Scale'].default_value=tuple(scale[i]*abs(o.scale[i]) for i in range(3))
            if o.data.materials: o.data.materials[o.active_material_index]=m
            else: o.data.materials.append(m)
        return len(targets)
    apply_to_selection=safe_apply
    ns.update(generate=generate,configure_mapping=configure_mapping,import_pbr=import_pbr,apply_to_selection=safe_apply)
    ann=ns['BS_Settings'].__annotations__
    ann.update({
       'lighting_mode':EnumProperty(name='Lighting Style',items=MODES,default='BOOTH'),
       'rig_rotation':FloatProperty(name='Rig Rotation',subtype='ANGLE',default=0),
       'edit_light':bpy.props.PointerProperty(name='Studio Light',type=bpy.types.Object,poll=lambda self,obj:obj.type=='LIGHT' and obj.get('bs_owner')==OWNER),
       'temperature':FloatProperty(name='Temperature (K)',default=5500,min=1800,max=12000),
       'coordinate_mode':EnumProperty(name='Coordinates',items=[('Object','Object / Box (meters)','UV-free box mapping; tile size assumes Blender meters'),('UV','UV','Best for detailed tangent normal maps; requires UV unwrap'),('Generated','Generated / Box','Fits object bounds')],default='Object'),
       'texture_rotation':FloatVectorProperty(name='Grain Rotation XYZ',subtype='EULER',size=3),
       'texture_stretch':FloatVectorProperty(name='Texture Scale XYZ',size=3,default=(1,1,1),min=.001,max=1000),
       'texture_offset':FloatVectorProperty(name='Texture Offset',size=3),
       'tile_size':FloatProperty(name='Tile Width (m)',default=1,min=.001,max=1000),
       'hdri_path':StringProperty(name='Local HDRI',subtype='FILE_PATH'),
       'hdri_strength':FloatProperty(name='HDRI Strength',default=.5,min=0,max=100,update=update_hdri),
       'hdri_rotation':FloatProperty(name='HDRI Rotation',subtype='ANGLE',update=update_hdri),
       'cache_path':StringProperty(name='Asset Cache',subtype='DIR_PATH',default=str(Path.home()/'Documents/BoothStudioAssets')),
       'asset_type':EnumProperty(name='Asset Type',items=[('1','PBR Materials','Wood, stone, fabric, metals'),('0','HDRIs','Environment lighting'),('2','Models','Free props and furniture')]),
       'asset_provider':EnumProperty(name='Library',items=[('POLY','Poly Haven','PBR, HDRI and models'),('AMBIENT','ambientCG','PBR materials')]),
       'asset_query':StringProperty(name='Search',default='wood'),
       'asset_resolution':EnumProperty(name='Resolution',items=[('1k','1K','Fast'),('2k','2K','Balanced'),('4k','4K','Detailed')],default='2k'),
       'assets':CollectionProperty(type=BS_Asset),'asset_index':IntProperty(default=0),
       'asset_page':IntProperty(default=0,min=0),
       'preview_status':StringProperty(default=''),
       'library_status':StringProperty(default='Ready - Poly Haven CC0'),
       'library_busy':bpy.props.BoolProperty(default=False,options={'SKIP_SAVE'})})
    ns['CLASSES']=(BS_Asset,)+ns['CLASSES']+(BS_OT_mapping,BS_OT_hdri,BS_UL_assets,BS_OT_library,BS_OT_source,BS_OT_quicksearch,BS_OT_asset_pick,BS_OT_asset_page,BS_PT_library,BS_PT_hdri,BS_PT_light_edit)
