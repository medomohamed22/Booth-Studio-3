"""Booth Studio 3: library workspace, managed jobs, materials and lighting tools."""
import bpy, json, math, time, threading, queue, uuid, hashlib
from pathlib import Path
from mathutils import Vector
from bpy.props import *
from . import pro,library,thumbnails,tasksnet

_epoch=0
_jobs={}
_search_tokens={}
_live_tokens={}
_enum_cache={}
_mute=False
_db_cache={}
_picker_items={}
def redraw(): thumbnails.redraw()
def S(context): return context.scene.booth_studio
def root_for(s): return Path(bpy.path.abspath(s.cache_path))
def db(s):
    path=root_for(s)/'studio_library.json'
    k=str(path)
    if k not in _db_cache:
        try: value=json.loads(path.read_text(encoding='utf-8'))
        except (FileNotFoundError,ValueError): value={}
        for field,default in [('favorites',[]),('groups',{}),('items',{}),('recent',[]),('profiles',{})]:
            value.setdefault(field,default)
        _db_cache[k]=value
    return _db_cache[k]
def save_db(s):
    p=root_for(s)/'studio_library.json'; p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(db(s),ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(p)
def item_key(a): return a.provider+':'+a.asset_id
def selected(s):
    return s.assets[s.asset_index] if s.assets and 0<=s.asset_index<len(s.assets) else None
def encode_item(a):
    try: meta=json.loads(a.metadata)
    except Exception: meta={}
    return {'provider':a.provider,'id':a.asset_id,'name':a.title,'type':a.kind,'thumbnail_url':a.thumbnail_url,'meta':meta}
def keep_item(s,a):
    data=db(s); data['items'][item_key(a)]=encode_item(a); return data
def history(s,a):
    data=keep_item(s,a); k=item_key(a); data['recent']=[k]+[x for x in data['recent'] if x!=k][:99]; save_db(s)
def populate(scene,rows,warning=''):
    s=scene.booth_studio; s.assets.clear()
    for r in rows:
        a=s.assets.add(); a.provider=r['provider']; a.asset_id=r['id']; a.title=r['name']; a.kind=r['type']
        a.thumbnail_url=r['thumbnail_url']; a.metadata=json.dumps(r.get('meta',{}))
    s.asset_index=0; s.asset_page=0
    _picker_items.pop(scene.as_pointer(),None)
    s.library_status='%d results%s'%(len(rows),' | '+warning if warning else '')
    if s.assets:
        s.asset_picker='0'; thumbnails.start(scene)
    redraw()
def is_downloaded(s,a):
    folder=root_for(s)/('ambientCG' if a.provider=='AMBIENT' else '')/a.asset_id
    return any(folder.glob('*/provenance.json'))
def local_rows(s):
    data=db(s)
    if s.library_view=='FAVORITES': keys=data['favorites']
    elif s.library_view=='RECENT': keys=data['recent']
    elif s.library_view=='GROUP': keys=data['groups'].get(s.group_name,[])
    else: keys=list(data['items'])
    rows=downloaded_rows(s,data) if s.library_view=='DOWNLOADED' else [data['items'][k] for k in keys if k in data['items']]
    terms=tasksnet.normalize(s.asset_query).split()
    rows=[r for r in rows if r['type']==s.asset_type and (s.asset_provider=='ALL' or r['provider']==s.asset_provider) and all(t in (r['name']+' '+r['id']+' '+json.dumps(r['meta'])).lower() for t in terms)]
    if s.library_view=='DOWNLOADED':
        rows=[r for r in rows if any((root_for(s)/('ambientCG' if r['provider']=='AMBIENT' else '')/r['id']).glob('*/provenance.json'))]
    return rows
def picker_items(s,context):
    count=thumbnails.page_size(s)
    pages=max(1,math.ceil(len(s.assets)/count))
    begin=min(s.asset_page,pages-1)*count
    visible=list(enumerate(s.assets))[begin:begin+count]
    sid=s.as_pointer()
    fingerprint=(begin,count,tuple((a.provider,a.asset_id,thumbnails.icon(a)) for _,a in visible))
    old=_picker_items.get(sid)
    if old and old[0]==fingerprint: return old[1]
    items=[(str(i),a.title,a.asset_id,thumbnails.icon(a) or 'TIME',i) for i,a in visible]
    if not items: items=[('NONE','Search for assets','',0,0)]
    for e in items:
        for value in e[:3]: _enum_cache[value]=value
    items=[(_enum_cache[a],_enum_cache[b],_enum_cache[c],d,e) for a,b,c,d,e in items]
    _picker_items[sid]=(fingerprint,items)
    return items
def picker_update(s,context):
    try:
        i=int(s.asset_picker)
        if 0<=i<len(s.assets): s.asset_index=i
    except ValueError: pass

def enqueue(context,action,asset=None,description=None):
    s=S(context); scene=context.scene
    a=asset or selected(s)
    if description is None:
        if not a: raise ValueError('Select an asset first')
        description={'provider':a.provider,'id':a.asset_id,'kind':a.kind,'resolution':s.asset_resolution,'row':encode_item(a)}
    if action in {'IMPORT','SWAP'} and description['kind']=='1' and not any(o.type=='MESH' for o in context.selected_objects):
        raise ValueError('Select mesh objects first')
    job_id=uuid.uuid4().hex
    entry=s.downloads.add(); entry.job_id=job_id; entry.title=description['row']['name']; entry.status='QUEUED'; entry.description=json.dumps(description); entry.action=action
    _jobs[job_id]={'scene':scene,'sid':scene.as_pointer(),'desc':description,'action':action,
                  'selected':tuple(o.as_pointer() for o in context.selected_objects),
                  'cancel':threading.Event(),'queue':queue.Queue(),'epoch':_epoch,'entry':entry}
    if not any(j.get('running') and j['sid']==scene.as_pointer() for j in _jobs.values()): launch(job_id)
    redraw(); return job_id
def launch(job_id):
    j=_jobs[job_id]; j['running']=True; e=j['entry']; e.status='RUNNING'; j['started']=time.monotonic()
    d=j['desc']; root=str(root_for(j['scene'].booth_studio)); out=j['queue']
    def progress(done,total,name): out.put(('PROGRESS',(done,total,name,time.monotonic())))
    def worker():
        tasksnet.local.cancel=j['cancel'].is_set; tasksnet.local.progress=progress
        try:
            tasksnet.check()
            value=tasksnet.details(d['provider'],d['id'],d['kind']) if j['action']=='INFO' else (library.fetch_ambient(root,d['id'],d['resolution']) if d['provider']=='AMBIENT' else library.fetch(root,d['id'],d['kind'],d['resolution']))
            tasksnet.check(); out.put(('DONE',value))
        except tasksnet.Cancelled: out.put(('CANCELLED','Cancelled'))
        except Exception as exc: out.put(('ERROR',str(exc)))
        finally: tasksnet.local.progress=None; tasksnet.local.cancel=None
    threading.Thread(target=worker,daemon=True).start()
    def poll():
        if j['epoch']!=_epoch: j['cancel'].set(); return None
        if not any(sc.as_pointer()==j['sid'] for sc in bpy.data.scenes): j['cancel'].set(); _jobs.pop(job_id,None); return None
        terminal=None
        while not out.empty():
            kind,value=out.get()
            if kind=='PROGRESS':
                done,total,name,now=value; e.bytes_done=str(done); e.bytes_total=str(total); e.current_file=name
                e.progress=done/total if total else 0; e.speed=done/max(.05,now-j['started'])
            else: terminal=(kind,value)
        if terminal:
            kind,value=terminal; e.status=kind; e.message=str(value)[:240] if kind!='DONE' else ''
            if kind=='DONE':
                if j['action']=='INFO':
                    for a in j['scene'].booth_studio.assets:
                        if a.provider==d['provider'] and a.asset_id==d['id']: a.details=json.dumps(value)
                    e.status='COMPLETE'
                else:
                    e.path=value
                    same=bpy.context.scene==j['scene'] and tuple(sorted(o.as_pointer() for o in bpy.context.selected_objects))==tuple(sorted(j['selected']))
                    if same:
                        try: apply_job(bpy.context,e); e.status='COMPLETE'
                        except Exception as exc: e.status='READY'; e.message='Downloaded; apply error: '+str(exc)[:170]
                    else: e.status='READY'; e.message='Downloaded; choose Apply to use current selection'
            j['running']=False; _jobs.pop(job_id,None)
            next_id=next((k for k,v in _jobs.items() if v['sid']==j['sid'] and v['entry'].status=='QUEUED'),None)
            if next_id: launch(next_id)
            redraw(); return None
        redraw(); return .15
    bpy.app.timers.register(poll,first_interval=.15)

def swap_maps(context,path,d):
    paths=sorted(str(p) for p in Path(path).iterdir() if p.suffix.lower() in {'.png','.jpg','.exr','.jpeg','.tif','.tiff','.webp'})
    aliases={'Base Color':('basecolor','base_color','color','diffuse','diff'),'Roughness':('roughness','rough'),'Metallic':('metallic','metalness','metal'),'Normal':('normalgl','nor_gl','normal_opengl'),'Height':('displacement','disp','height'),'AO':('ao','ambientocclusion')}
    done=set(); count=0
    for obj in context.selected_objects:
        m=obj.active_material if obj.type=='MESH' else None
        if not m or m.get('bs_asset_id')!=d['id'] or m.get('bs_provider')!=('ambientCG' if d['provider']=='AMBIENT' else 'Poly Haven'): continue
        if obj.data.users>1: obj.data=obj.data.copy()
        if m.users>1: m=m.copy(); obj.active_material=m
        if m.as_pointer() in done: continue
        done.add(m.as_pointer())
        for node in m.node_tree.nodes:
            if node.type!='TEX_IMAGE' or node.label not in aliases: continue
            p=pro.find_map(paths,aliases[node.label])
            if p:
                image=bpy.data.images.load(p,check_existing=False); image.colorspace_settings.name='sRGB' if node.label=='Base Color' else 'Non-Color'; node.image=image
        m['bs_resolution']=d['resolution']; m['bs_source_folder']=path; count+=1
    if not count: raise ValueError('Select materials from the same asset to replace their maps')
    return count
def apply_job(context,e):
    d=json.loads(e.description); s=S(context)
    if bpy.ops.ed.undo_push.poll(): bpy.ops.ed.undo_push(message='Before Booth Studio import')
    if e.action=='SWAP': swap_maps(context,e.path,d)
    else:
        pro.import_asset(context,e.path,d['kind'],d['id'])
        if d['kind']=='1':
            for obj in context.selected_objects:
                m=obj.active_material if obj.type=='MESH' else None
                if m and m.get('bs_owner')==pro.OWNER:
                    m['bs_provider']='ambientCG' if d['provider']=='AMBIENT' else 'Poly Haven'; m['bs_asset_id']=d['id']; m['bs_resolution']=d['resolution']
                    m['bs_mapping']=json.dumps(mapping_values(s))
    data=db(s); k=d['provider']+':'+d['id']; data['items'][k]=d['row']; data['recent']=[k]+[x for x in data['recent'] if x!=k][:99]; save_db(s)
    s.library_status='Imported '+d['row']['name']; e.status='COMPLETE'
    if bpy.ops.ed.undo_push.poll(): bpy.ops.ed.undo_push(message='Booth Studio asset import')

class BS_Download(bpy.types.PropertyGroup):
    job_id:StringProperty(); title:StringProperty(); status:StringProperty(); description:StringProperty()
    action:StringProperty(); path:StringProperty(); message:StringProperty(); current_file:StringProperty()
    progress:FloatProperty(min=0,max=1); bytes_done:StringProperty(default='0'); bytes_total:StringProperty(default='0'); speed:FloatProperty()

class BS_OT_library3(bpy.types.Operator):
    bl_idname='booth_studio.library'; bl_label='Asset Library'
    action:EnumProperty(items=[('SEARCH','Search',''),('IMPORT','Download / Import',''),('INFO','Inspect Download',''),('SWAP','Change Selected Material Resolution','')])
    def execute(self,context):
        s=S(context)
        try:
            if self.action!='SEARCH':
                if self.action=='SWAP':
                    m=context.object.active_material if context.object and context.object.type=='MESH' else None
                    if not m or not m.get('bs_asset_id'): raise ValueError('Select a library material first')
                    provider='AMBIENT' if m.get('bs_provider')=='ambientCG' else 'POLY'; aid=m['bs_asset_id']
                    r=db(s)['items'].get(provider+':'+aid,{'provider':provider,'id':aid,'name':m.name,'type':'1','thumbnail_url':'','meta':{}})
                    enqueue(context,'SWAP',description={'provider':provider,'id':aid,'kind':'1','resolution':s.asset_resolution,'row':r})
                else: enqueue(context,self.action)
                return {'FINISHED'}
            sid=context.scene.as_pointer(); token=_search_tokens.get(sid,0)+1; _search_tokens[sid]=token
            if s.library_view!='ALL':
                populate(context.scene,local_rows(s)); s.library_busy=False; return {'FINISHED'}
            scene=context.scene; epoch=_epoch; out=queue.Queue()
            args=(str(root_for(s)),s.asset_provider,s.asset_type,s.asset_query,s.asset_topic,s.hdri_environment,s.asset_sort)
            s.library_busy=True; s.library_status='Searching libraries...'
            def worker():
                tasksnet.local.cancel=lambda:epoch!=_epoch or _search_tokens.get(sid)!=token
                try: out.put((True,tasksnet.search(*args)))
                except Exception as exc: out.put((False,str(exc)))
                finally: tasksnet.local.cancel=None
            def poll():
                if epoch!=_epoch or _search_tokens.get(sid)!=token: return None
                if not any(sc.as_pointer()==sid for sc in bpy.data.scenes): return None
                if out.empty(): return .15
                ok,value=out.get(); scene.booth_studio.library_busy=False
                if ok: populate(scene,value['rows'],value['warning'])
                else: scene.booth_studio.library_status='Search error: '+value[:200]
                redraw(); return None
            threading.Thread(target=worker,daemon=True).start(); bpy.app.timers.register(poll,first_interval=.15)
            return {'FINISHED'}
        except Exception as exc: self.report({'ERROR'},str(exc)); return {'CANCELLED'}

class BS_OT_job(bpy.types.Operator):
    bl_idname='booth_studio.download_control'; bl_label='Download Control'; bl_options={'REGISTER'}
    job_id:StringProperty()
    action:EnumProperty(items=[('CANCEL','Cancel',''),('RETRY','Retry',''),('APPLY','Apply Download',''),('CLEAR','Clear Finished','')])
    def execute(self,context):
        s=S(context); e=next((x for x in s.downloads if x.job_id==self.job_id),None)
        try:
            if self.action=='CLEAR':
                for i in reversed(range(len(s.downloads))):
                    if s.downloads[i].status not in {'QUEUED','RUNNING','CANCELLING'}: s.downloads.remove(i)
            elif e:
                j=_jobs.get(e.job_id)
                if self.action=='CANCEL' and j:
                    j['cancel'].set()
                    if e.status=='QUEUED': e.status='CANCELLED'; _jobs.pop(e.job_id,None)
                    else: e.status='CANCELLING'
                elif self.action=='RETRY': enqueue(context,e.action,description=json.loads(e.description))
                elif self.action=='APPLY': apply_job(context,e)
            redraw(); return {'FINISHED'}
        except Exception as exc: self.report({'ERROR'},str(exc)); return {'CANCELLED'}

class BS_OT_organize(bpy.types.Operator):
    bl_idname='booth_studio.organize'; bl_label='Organize Asset'
    action:EnumProperty(items=[('FAVORITE','Favorite',''),('GROUP','Add to Group',''),('UNGROUP','Remove from Group','')])
    def execute(self,context):
        s=S(context); a=selected(s)
        if not a: return {'CANCELLED'}
        data=keep_item(s,a); k=item_key(a)
        if self.action=='FAVORITE':
            if k in data['favorites']: data['favorites'].remove(k)
            else: data['favorites'].append(k)
        elif self.action=='GROUP':
            g=s.group_name.strip()
            if not g: self.report({'WARNING'},'Enter a collection name'); return {'CANCELLED'}
            group=data['groups'].setdefault(g,[])
            if k not in group: group.append(k)
        elif self.action=='UNGROUP':
            group=data['groups'].get(s.group_name,[])
            if k in group: group.remove(k)
        save_db(s); redraw(); return {'FINISHED'}

def auto_search(s,context):
    global _mute
    if _mute or not context or not hasattr(context.scene,'booth_studio'): return
    if s.asset_provider=='AMBIENT' and s.asset_type!='1':
        _mute=True
        try: s.asset_type='1'
        finally: _mute=False
    scene=context.scene; sid=scene.as_pointer(); token=_live_tokens.get(('search',sid),0)+1; _live_tokens[('search',sid)]=token; epoch=_epoch
    def run():
        if epoch!=_epoch or _live_tokens.get(('search',sid))!=token: return None
        if bpy.context.scene==scene:
            bpy.ops.booth_studio.library(action='SEARCH')
        return None
    bpy.app.timers.register(run,first_interval=.35)


MAPPING_FIELDS=('coordinate_mode','texture_rotation','texture_stretch','texture_offset','tile_size','material_scale','material_bump')
def mapping_values(s):
    return {k:list(getattr(s,k)) if k in {'texture_rotation','texture_stretch','texture_offset'} else getattr(s,k) for k in MAPPING_FIELDS}
def update_mapping(context):
    s=S(context); count=0
    for obj in context.selected_objects:
        m=obj.active_material if obj.type=='MESH' else None
        if not m or m.get('bs_owner')!=pro.OWNER: continue
        if obj.data.users>1: obj.data=obj.data.copy()
        if m.users>1: m=m.copy(); obj.active_material=m
        pro.configure_mapping(m,s)
        if s.coordinate_mode=='Object':
            n=m.node_tree.nodes['BS Mapping']
            n.inputs['Scale'].default_value=tuple(n.inputs['Scale'].default_value[i]*abs(obj.scale[i]) for i in range(3))
        m['bs_mapping']=json.dumps(mapping_values(s)); count+=1
    return count
def live_mapping(s,context):
    if _mute or not s.live_material or not context: return
    scene=context.scene; sid=scene.as_pointer(); selection=tuple(o.as_pointer() for o in context.selected_objects)
    token=_live_tokens.get(('mapping',sid),0)+1; _live_tokens[('mapping',sid)]=token; epoch=_epoch
    def run():
        if epoch!=_epoch or _live_tokens.get(('mapping',sid))!=token: return None
        if bpy.context.scene==scene and tuple(o.as_pointer() for o in bpy.context.selected_objects)==selection:
            if bpy.ops.ed.undo_push.poll(): bpy.ops.ed.undo_push(message='Before live material mapping')
            update_mapping(bpy.context)
            if bpy.ops.ed.undo_push.poll(): bpy.ops.ed.undo_push(message='Live material mapping')
        return None
    bpy.app.timers.register(run,first_interval=.25)
def tile_cm_update(s,context):
    global _mute
    if _mute: return
    _mute=True
    try: s.tile_size=s.tile_cm/100
    finally: _mute=False
    live_mapping(s,context)
class BS_OT_mapping3(bpy.types.Operator):
    bl_idname='booth_studio.update_mapping'; bl_label='Apply Mapping to Selected'; bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        count=update_mapping(context); self.report({'INFO'},'Updated %d materials'%count)
        return {'FINISHED'} if count else {'CANCELLED'}
class BS_OT_grain(bpy.types.Operator):
    bl_idname='booth_studio.grain_angle'; bl_label='Set Grain Direction'; bl_options={'REGISTER','UNDO'}
    angle:IntProperty()
    def execute(self,context):
        s=S(context); value=list(s.texture_rotation); value['XYZ'.index(s.grain_axis)]=math.radians(self.angle); s.texture_rotation=value
        update_mapping(context); return {'FINISHED'}

def light_record(o):
    d=o.data
    return {'id':str(o.get('bs_id',o.name)),'name':o.name,'type':d.type,'location':list(o.location),'rotation':list(o.rotation_euler),
            'energy':d.energy,'color':list(d.color),'size':getattr(d,'size',1),'size_y':getattr(d,'size_y',1),
            'spot_size':getattr(d,'spot_size',.7),'spot_blend':getattr(d,'spot_blend',.4),
            'hide_render':o.hide_render,'hide_viewport':o.hide_viewport,'pinned':bool(o.get('bs_pinned',False))}
def managed(scene): return [o for o in scene.objects if o.type=='LIGHT' and o.get('bs_owner')==pro.OWNER]
def capture(context):
    s=S(context); world=context.scene.world; hdri=''
    if world and world.use_nodes:
        hdri=next((bpy.path.abspath(n.image.filepath) for n in world.node_tree.nodes if n.type=='TEX_ENVIRONMENT' and n.image),'')
    return {'version':1,'lights':[light_record(o) for o in managed(context.scene)],
            'hdri':hdri,'world':world.name if world else '', 'strength':s.hdri_strength,'rotation':s.hdri_rotation,
            'exposure':context.scene.view_settings.exposure,'style':s.lighting_mode}
def restore(context,data,respect_pins=True):
    validate_profile(data)
    coll=pro.ensure_collection(context.scene)
    pins={o.get('bs_id'):o for o in managed(context.scene) if o.get('bs_pinned')} if respect_pins else {}
    for o in list(managed(context.scene)):
        if o not in pins.values(): bpy.data.objects.remove(o,do_unlink=True)
    for r in data['lights']:
        if r['id'] in pins: continue
        if r['type'] not in {'AREA','SPOT','POINT','SUN'}: continue
        d=bpy.data.lights.new(r['name'],r['type']); o=bpy.data.objects.new(r['name'],d); coll.objects.link(o)
        o['bs_owner']=pro.OWNER; o['bs_id']=r['id']; o['bs_pinned']=r.get('pinned',False)
        o.location=r['location']; o.rotation_euler=r['rotation']; d.energy=max(0,r['energy']); d.color=r['color']
        if d.type=='AREA': d.shape='RECTANGLE'; d.size=max(.01,r['size']); d.size_y=max(.01,r['size_y'])
        if d.type=='SPOT': d.spot_size=r['spot_size']; d.spot_blend=r['spot_blend']
        o.hide_render=r.get('hide_render',False); o.hide_viewport=r.get('hide_viewport',False)
    s=S(context)
    if data.get('hdri') and Path(data['hdri']).is_file(): pro.setup_hdri(context,data['hdri'])
    elif data.get('world') in bpy.data.worlds: context.scene.world=bpy.data.worlds[data['world']]
    s.hdri_strength=data.get('strength',.5); s.hdri_rotation=data.get('rotation',0)
    context.scene.view_settings.exposure=data.get('exposure',0)
    if data.get('style'): s.lighting_mode=data['style']
    redraw()
def profile_items(s,context):
    names=sorted(db(s)['profiles'])
    key=('profiles',str(root_for(s)),tuple(names))
    if key not in _enum_cache: _enum_cache[key]=[(n,n,n) for n in names] or [('NONE','No saved profiles','')]
    return _enum_cache[key]
class BS_OT_profile(bpy.types.Operator):
    bl_idname='booth_studio.profile'; bl_label='Lighting Profile'; bl_options={'REGISTER','UNDO'}
    action:EnumProperty(items=[(k,k.replace('_',' '),'') for k in ('SAVE','LOAD','A_SAVE','A_LOAD','B_SAVE','B_LOAD','EXPORT','IMPORT')])
    def execute(self,context):
        s=S(context)
        try:
            if self.action=='SAVE':
                name=s.profile_name.strip()
                if not name: raise ValueError('Enter a profile name')
                db(s)['profiles'][name]=capture(context); save_db(s); s.profile_choice=name
            elif self.action=='LOAD':
                data=db(s)['profiles'].get(s.profile_choice)
                if not data: raise ValueError('Select a saved profile')
                restore(context,data)
            elif self.action.endswith('_SAVE'):
                setattr(s,'snapshot_'+self.action[0].lower(),json.dumps(capture(context)))
            elif self.action.endswith('_LOAD'):
                raw=getattr(s,'snapshot_'+self.action[0].lower())
                if not raw: raise ValueError('Save this comparison slot first')
                restore(context,json.loads(raw),respect_pins=False)
            elif self.action=='EXPORT':
                path=Path(bpy.path.abspath(s.profile_file))
                if path.suffix.lower()!='.json': raise ValueError('Choose a .json profile filename')
                path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(capture(context),indent=2))
            elif self.action=='IMPORT':
                path=Path(bpy.path.abspath(s.profile_file))
                if path.stat().st_size>1024*1024: raise ValueError('Profile file too large')
                data=json.loads(path.read_text()); restore(context,data)
            self.report({'INFO'},'Lighting profile updated'); return {'FINISHED'}
        except Exception as exc: self.report({'ERROR'},str(exc)); return {'CANCELLED'}

class BS_OT_light_control(bpy.types.Operator):
    bl_idname='booth_studio.light_control'; bl_label='Control Studio Light'; bl_options={'REGISTER','UNDO'}
    action:EnumProperty(items=[('TOGGLE','On / Off',''),('SOLO','Solo / Restore',''),('PIN','Pin / Unpin',''),('LINK','Light Selected Objects Only',''),('UNLINK','Light All Objects','')])
    def execute(self,context):
        s=S(context); o=s.edit_light
        if not o or o not in managed(context.scene): self.report({'WARNING'},'Choose a studio light'); return {'CANCELLED'}
        if self.action=='TOGGLE': o.hide_render=not o.hide_render; o.hide_viewport=o.hide_render
        elif self.action=='PIN': o['bs_pinned']=not o.get('bs_pinned',False)
        elif self.action=='SOLO':
            if s.solo_backup:
                for name,vis in json.loads(s.solo_backup).items():
                    light=context.scene.objects.get(name)
                    if light: light.hide_render=vis[0]; light.hide_viewport=vis[1]
                s.solo_backup=''
            else:
                s.solo_backup=json.dumps({x.name:[x.hide_render,x.hide_viewport] for x in managed(context.scene)})
                for x in managed(context.scene): x.hide_render=x!=o; x.hide_viewport=x!=o
        elif self.action=='LINK':
            targets=[x for x in context.selected_objects if x.type=='MESH']
            if not targets: self.report({'WARNING'},'Select receiving meshes first'); return {'CANCELLED'}
            coll=bpy.data.collections.new('BS Receivers | '+o.name); coll['bs_owner']=pro.OWNER
            for x in targets: coll.objects.link(x)
            o.light_linking.receiver_collection=coll
        elif self.action=='UNLINK': o.light_linking.receiver_collection=None
        redraw(); return {'FINISHED'}

def preserve_generate(original):
    def generate(context):
        pins=[o for o in managed(context.scene) if o.get('bs_pinned')]
        for o in pins: o['bs_owner']='booth_studio_pin_hold'
        try:
            original(context)
            ids={o.get('bs_id') for o in pins}
            for o in list(managed(context.scene)):
                if o.get('bs_id') in ids: bpy.data.objects.remove(o,do_unlink=True)
        finally:
            for o in pins: o['bs_owner']=pro.OWNER
        return len(managed(context.scene))
    return generate

def canvas_project(location,center,span,rect):
    x,y,w,h=rect
    return (x+w*.5+(location[0]-center[0])*w/span, y+h*.5+(location[1]-center[1])*h/span)
def canvas_unproject(point,center,span,rect):
    x,y,w,h=rect
    return (center[0]+(point[0]-x-w*.5)*span/w, center[1]+(point[1]-y-h*.5)*span/h)
def canvas_move(light,point,center,span,rect,target):
    x,y=canvas_unproject(point,center,span,rect)
    light.location.x=x; light.location.y=y; pro.aim_at(light,target)
class BS_OT_light_canvas(bpy.types.Operator):
    bl_idname='booth_studio.light_canvas'; bl_label='Interactive Light Map'; bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context): return context.area is not None and context.area.type=='VIEW_3D' and bool(managed(context.scene))
    def invoke(self,context,event):
        self.lights=managed(context.scene); self.original=[(o,list(o.location),list(o.rotation_euler)) for o in self.lights]
        self.target,self.dims,_,_=pro.booth_bounds(context,S(context)); self.target.z+=self.dims.z*.5
        self.center=self.target.copy(); self.span=max(2,self.dims.x*3,self.dims.y*3,max((o.location-self.center).length for o in self.lights)*2.5)
        self.rect=(40,100,440,340); self.drag=None; self.area=context.area
        self.handle=bpy.types.SpaceView3D.draw_handler_add(self.draw,(context,),'WINDOW','POST_PIXEL')
        context.window_manager.modal_handler_add(self); redraw(); return {'RUNNING_MODAL'}
    def draw(self,context):
        import gpu,blf
        from gpu_extras.batch import batch_for_shader
        shader=gpu.shader.from_builtin('UNIFORM_COLOR'); x,y,w,h=self.rect
        gpu.state.blend_set('ALPHA')
        def shape(mode,coords,color):
            shader.bind(); shader.uniform_float('color',color); batch_for_shader(shader,mode,{'pos':coords}).draw(shader)
        shape('TRI_FAN',[(x,y),(x+w,y),(x+w,y+h),(x,y+h)],(.025,.035,.05,.94))
        for i in range(5):
            xx=x+w*i/4; yy=y+h*i/4
            shape('LINES',[(xx,y),(xx,y+h),(x,yy),(x+w,yy)],(.2,.25,.3,.65))
        cx,cy=canvas_project(self.target,self.center,self.span,self.rect)
        shape('LINE_STRIP',[(cx-18,cy-18),(cx+18,cy-18),(cx+18,cy+18),(cx-18,cy+18),(cx-18,cy-18)],(.3,.8,1,1))
        for i,o in enumerate(self.lights):
            px,py=canvas_project(o.location,self.center,self.span,self.rect)
            coords=[(px+9*math.cos(a*math.tau/24),py+9*math.sin(a*math.tau/24)) for a in range(25)]
            shape('TRI_FAN',[(px,py)]+coords,(1,.65,.15,1) if o==S(context).edit_light else (.4,.6,.9,1))
            blf.position(0,px+12,py-3,0); blf.size(0,12); blf.color(0,1,1,1,1); blf.draw(0,str(i+1))
        blf.position(0,x+10,y+h+12,0); blf.size(0,15); blf.color(0,1,1,1,1); blf.draw(0,'Drag lights | Wheel: height | Enter: apply | Esc: restore')
        gpu.state.blend_set('NONE')
    def close(self):
        bpy.types.SpaceView3D.draw_handler_remove(self.handle,'WINDOW'); self.area.tag_redraw()
    def modal(self,context,event):
        if event.type in {'ESC','RIGHTMOUSE'}:
            for o,loc,rot in self.original:
                if o.name in context.scene.objects: o.location=loc; o.rotation_euler=rot
            self.close(); return {'CANCELLED'}
        if event.type in {'RET','NUMPAD_ENTER'}: self.close(); return {'FINISHED'}
        p=(event.mouse_region_x,event.mouse_region_y)
        if event.type=='LEFTMOUSE':
            if event.value=='PRESS':
                candidates=[((Vector(canvas_project(o.location,self.center,self.span,self.rect))-Vector(p)).length,o) for o in self.lights]
                distance,o=min(candidates,key=lambda t:t[0])
                if distance<20: self.drag=o; S(context).edit_light=o
            else: self.drag=None
        if event.type=='MOUSEMOVE' and self.drag: canvas_move(self.drag,p,self.center,self.span,self.rect,self.target)
        if event.type in {'WHEELUPMOUSE','WHEELDOWNMOUSE'} and S(context).edit_light:
            S(context).edit_light.location.z+=.15 if event.type=='WHEELUPMOUSE' else -.15
        self.area.tag_redraw(); return {'RUNNING_MODAL'}

class BS_OT_cache(bpy.types.Operator):
    bl_idname='booth_studio.cache'; bl_label='Asset Cache'
    action:EnumProperty(items=[('SCAN','Calculate Cache Size',''),('CLEAN','Remove Cached Thumbnails','')])
    def invoke(self,context,event):
        if self.action=='CLEAN': return context.window_manager.invoke_confirm(self,event)
        return self.execute(context)
    def execute(self,context):
        s=S(context); root=root_for(s).resolve()
        if self.action=='CLEAN':
            folder=(root/'thumbnails').resolve()
            if folder.parent!=root: return {'CANCELLED'}
            thumbnails.shutdown()
            for p in folder.glob('*.png'): p.unlink(missing_ok=True)
            s.cache_info='Thumbnail cache cleared; assets and favorites kept'
        else:
            size=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
            photos=sum(p.stat().st_size for p in (root/'thumbnails').glob('*.png'))
            s.cache_info='Total %.1f MB | Photos %.1f MB'%(size/1024**2,photos/1024**2)
        redraw(); return {'FINISHED'}

def draw_jobs(layout,s):
    entries=[e for e in s.downloads if e.action!='INFO' or e.status!='COMPLETE']
    if not entries: return
    box=layout.box(); row=box.row(); row.label(text='Downloads',icon='IMPORT')
    row.operator('booth_studio.download_control',text='',icon='TRASH').action='CLEAR'
    finished=sum(e.status=='COMPLETE' for e in entries)
    if finished: box.label(text='%d completed downloads'%finished)
    priority={'RUNNING':0,'CANCELLING':1,'READY':2,'ERROR':3,'CANCELLED':4,'QUEUED':5}
    active=sorted((e for e in entries if e.status!='COMPLETE'),key=lambda e:priority.get(e.status,6))
    if len(active)>4: box.label(text='%d additional tasks in queue'%(len(active)-4))
    for e in active[:4]:
        b=box.box(); r=b.row(); r.label(text=e.title[:45]); r.label(text=e.status)
        if e.status in {'RUNNING','CANCELLING'}:
            if hasattr(b,'progress'): b.progress(factor=e.progress,type='BAR',text='%d%% | current file'%round(e.progress*100))
            b.label(text=e.current_file[:60]); b.label(text='%.1f / %.1f MB | %.1f MB/s'%(int(e.bytes_done)/1024**2,int(e.bytes_total)/1024**2,e.speed/1024**2))
        if e.message: b.label(text=e.message[:80])
        if e.status in {'QUEUED','RUNNING'}:
            op=b.operator('booth_studio.download_control',text='Cancel',icon='CANCEL'); op.job_id=e.job_id; op.action='CANCEL'
        elif e.status in {'ERROR','CANCELLED'}:
            op=b.operator('booth_studio.download_control',text='Retry',icon='FILE_REFRESH'); op.job_id=e.job_id; op.action='RETRY'
        elif e.status=='READY':
            op=b.operator('booth_studio.download_control',text='Apply to Current Selection',icon='CHECKMARK'); op.job_id=e.job_id; op.action='APPLY'

def details_ui(layout,s,zoom=True):
    a=selected(s)
    if not a: return
    b=layout.box(); b.label(text=a.title,icon='ASSET_MANAGER')
    if zoom: b.operator('booth_studio.asset_details',text='Enlarge Preview',icon='VIEWZOOM')
    try: meta=json.loads(a.metadata)
    except Exception: meta={}
    b.label(text=('Poly Haven' if a.provider=='POLY' else 'ambientCG')+' | CC0')
    if meta.get('dimensions'): b.label(text='Source size (mm): '+str(meta['dimensions']))
    if meta.get('polycount'): b.label(text='Polygons: '+str(meta['polycount']))
    if meta.get('max_resolution'): b.label(text='Max pixels: '+str(meta['max_resolution']))
    if a.details:
        d=json.loads(a.details)
        for resolution,size in d.get('resolutions',{}).items():
            if resolution in {'1k','2k','4k'}: b.label(text=resolution.upper()+': %.1f MB'%(size/1024**2))
        if d.get('dimensions_cm'): b.label(text='Tile (cm): '+str(d['dimensions_cm']))
    b.operator('booth_studio.library',text='Inspect Resolutions / Download Size',icon='INFO').action='INFO'
    r=b.row(align=True)
    starred=item_key(a) in db(s)['favorites']
    r.operator('booth_studio.organize',text='Unfavorite' if starred else 'Favorite',icon='SOLO_ON' if starred else 'SOLO_OFF').action='FAVORITE'
    r.operator('booth_studio.open_source',text='Source',icon='URL').source='PREVIEW'
    r=b.row(align=True); r.prop(s,'group_name',text='Collection')
    r.operator('booth_studio.organize',text='',icon='ADD').action='GROUP'
    if s.library_view=='GROUP': r.operator('booth_studio.organize',text='',icon='REMOVE').action='UNGROUP'
    r=b.row(align=True); r.prop(s,'asset_resolution',text='')
    r.operator('booth_studio.library',text='Download / Import',icon='IMPORT').action='IMPORT'

def draw_browser(layout,context,wide=False):
    s=S(context)
    row=layout.row(align=True); row.prop(s,'asset_provider',text=''); row.prop(s,'asset_type',text='')
    row=layout.row(align=True); row.prop(s,'asset_query',text='',icon='VIEWZOOM')
    row.operator('booth_studio.library',text='Search',icon='VIEWZOOM').action='SEARCH'
    if wide or s.advanced:
        row=layout.row(align=True); row.prop(s,'asset_topic',text='Category')
        if s.asset_type=='0': row.prop(s,'hdri_environment',text='Environment')
        row.prop(s,'asset_sort',text='Sort')
        row=layout.row(align=True); row.prop(s,'library_view',text='View')
        if s.library_view=='GROUP': row.prop(s,'collection_choice',text='Collection')
        row=layout.row(align=True); row.prop(s,'browser_columns',text='Columns'); row.prop(s,'thumbnail_scale',text='Image Size')
    if s.library_busy: layout.label(text='Searching...',icon='TIME')
    if s.assets:
        count=thumbnails.page_size(s); pages=max(1,math.ceil(len(s.assets)/count)); s_page=min(s.asset_page,pages-1)
        nav=layout.row(align=True)
        r=nav.row(); r.enabled=s_page>0; r.operator('booth_studio.asset_page',text='',icon='TRIA_LEFT').step=-1
        nav.label(text='Page %d / %d | %d assets'%(s_page+1,pages,len(s.assets)))
        r=nav.row(); r.enabled=s_page<pages-1; r.operator('booth_studio.asset_page',text='',icon='TRIA_RIGHT').step=1
        # Native Blender image picker: click the image to choose another asset directly.
        layout.template_icon_view(s,'asset_picker',show_labels=True,scale=3 if not wide else 4,scale_popup=s.thumbnail_scale)
        if wide:
            grid=layout.grid_flow(row_major=True,columns=s.browser_columns,even_columns=True,even_rows=True,align=True)
            begin=s_page*count
            for i in range(begin,min(begin+count,len(s.assets))):
                a=s.assets[i]; b=grid.box(); photo=thumbnails.icon(a)
                if photo: b.template_icon(icon_value=photo,scale=s.thumbnail_scale)
                else: r=b.row(); r.scale_y=3; r.label(text='Loading',icon='TIME')
                b.operator('booth_studio.pick_asset',text=a.title,depress=i==s.asset_index).index=i
        layout.label(text=s.preview_status)
        details_ui(layout,s)
    else: layout.label(text='Search: wood / chair / studio, or Arabic keywords',icon='IMAGE_DATA')
    layout.label(text=s.library_status[:100])
    draw_jobs(layout,s)
    if s.advanced:
        b=layout.box(); b.prop(s,'cache_path'); b.prop(s,'preview_limit')
        r=b.row(); r.operator('booth_studio.cache',text='Cache Size',icon='FILE_FOLDER').action='SCAN'
        r.operator('booth_studio.cache',text='Clear Photos',icon='TRASH').action='CLEAN'
        b.label(text=s.cache_info)

class BS_OT_browser(bpy.types.Operator):
    bl_idname='booth_studio.browser'; bl_label='Booth Studio Asset Browser'
    def invoke(self,context,event):
        return context.window_manager.invoke_props_dialog(self,width=S(context).browser_width)
    def draw(self,context): draw_browser(self.layout,context,True)
    def execute(self,context): return {'FINISHED'}

def draw_materials(layout,context):
    s=S(context)
    b=layout.box(); b.label(text='Selected Material Mapping',icon='MATERIAL')
    b.prop(s,'live_material')
    r=b.row(align=True); r.prop(s,'grain_axis',text='Axis')
    for angle in (0,90,180): r.operator('booth_studio.grain_angle',text=str(angle)+'°').angle=angle
    b.prop(s,'tile_cm'); b.prop(s,'material_scale'); b.prop(s,'material_bump')
    if s.advanced:
        b.prop(s,'coordinate_mode'); b.prop(s,'texture_rotation'); b.prop(s,'texture_stretch'); b.prop(s,'texture_offset')
        b.label(text='UV: NormalGL | Box: Height Bump')
    b.operator('booth_studio.update_mapping',icon='CHECKMARK')
    r=b.row(align=True); r.prop(s,'asset_resolution',text='')
    r.operator('booth_studio.library',text='Replace Map Resolution',icon='FILE_REFRESH').action='SWAP'
    b=layout.box(); b.label(text='Offline Procedural Materials'); b.prop(s,'material',text='')
    b.operator('booth_studio.apply_material',icon='MATERIAL')
    if s.advanced:
        b.prop(s,'pbr_folder'); b.operator('booth_studio.import_pbr',icon='IMPORT')
    layout.operator('booth_studio.browser',text='Browse Real PBR Materials',icon='ASSET_MANAGER')
    draw_jobs(layout,s)

def draw_lighting(layout,context):
    s=S(context)
    layout.prop(s,'lighting_mode'); layout.prop(s,'intensity'); layout.prop(s,'rig_rotation'); layout.prop(s,'temperature')
    layout.prop(s,'use_selection')
    if not s.use_selection:
        row=layout.row(); row.prop(s,'width'); row.prop(s,'depth'); row.prop(s,'height')
        if s.advanced: layout.prop(s,'center')
    if s.advanced and s.lighting_mode.startswith('BOOTH'):
        for field in ('spot_count','spot_power','spot_angle','mount_height','target_height'): layout.prop(s,field)
    layout.operator('booth_studio.generate',text='Generate / Update - Keep Pinned Lights',icon='LIGHT_AREA')
    layout.operator('booth_studio.light_canvas',text='Drag Lights on Visual Map',icon='ORIENTATION_GLOBAL')
    b=layout.box(); b.prop(s,'edit_light')
    o=s.edit_light
    if o and o in managed(context.scene):
        r=b.row(align=True)
        for action,label,icon in [('TOGGLE','On / Off','LIGHT'),('SOLO','Solo','HIDE_OFF'),('PIN','Unpin' if o.get('bs_pinned') else 'Pin','PINNED')]:
            r.operator('booth_studio.light_control',text=label,icon=icon).action=action
        b.prop(o.data,'energy'); b.prop(o.data,'color')
        if s.advanced:
            b.prop(o,'location'); b.prop(o,'rotation_euler')
            if o.data.type=='AREA': b.prop(o.data,'size'); b.prop(o.data,'size_y')
            b.operator('booth_studio.light_control',text='Affect Selected Meshes Only',icon='LINKED').action='LINK'
            b.operator('booth_studio.light_control',text='Affect All Objects',icon='UNLINKED').action='UNLINK'
    b=layout.box(); b.label(text='Lighting Profiles',icon='PRESET')
    r=b.row(align=True); r.prop(s,'profile_name',text=''); r.operator('booth_studio.profile',text='Save').action='SAVE'
    r=b.row(align=True); r.prop(s,'profile_choice',text=''); r.operator('booth_studio.profile',text='Load').action='LOAD'
    r=b.row(align=True)
    for action,label in [('A_SAVE','Save A'),('A_LOAD','View A'),('B_SAVE','Save B'),('B_LOAD','View B')]:
        r.operator('booth_studio.profile',text=label).action=action
    if s.advanced:
        b.prop(s,'profile_file')
        r=b.row(); r.operator('booth_studio.profile',text='Export JSON').action='EXPORT'; r.operator('booth_studio.profile',text='Import JSON').action='IMPORT'
        layout.operator('booth_studio.clear',text='Remove Studio Lights',icon='TRASH')
    b=layout.box(); b.label(text='Environment'); b.prop(s,'hdri_strength'); b.prop(s,'hdri_rotation')
    if s.advanced: b.prop(s,'hdri_path'); b.operator('booth_studio.local_hdri')
    layout.prop(context.scene.view_settings,'exposure')

class BS_PT_main3(bpy.types.Panel):
    bl_label='Booth Studio 3'; bl_idname='BS_PT_main'; bl_space_type='VIEW_3D'; bl_region_type='UI'; bl_category='Booth Studio'
    def draw(self,context):
        s=S(context); c=self.layout
        for pair in (('LIBRARY','MATERIAL'),('LIGHT','RENDER')):
            row=c.row(align=True)
            for tab in pair: row.prop_enum(s,'studio_tab',tab)
        c.prop(s,'advanced',text='Advanced Controls')
        if s.studio_tab=='LIBRARY':
            c.operator('booth_studio.browser',text='Open Large Asset Browser',icon='ASSET_MANAGER')
            if s.advanced: c.prop(s,'browser_width')
            draw_browser(c,context)
        elif s.studio_tab=='MATERIAL': draw_materials(c,context)
        elif s.studio_tab=='LIGHT': draw_lighting(c,context)
        else:
            c.prop(s,'render_preset'); c.operator('booth_studio.render_preset',icon='RENDER_STILL')
            c.prop(context.scene.render,'resolution_x'); c.prop(context.scene.render,'resolution_y')
            c.prop(context.scene.view_settings,'exposure')

def photo_settings(s,context):
    if _mute or not context: return
    s.asset_index=0
    set_asset_page(context,0)
def refresh_picker(context,index):
    s=S(context)
    if 0<=index<len(s.assets):
        count=thumbnails.page_size(s)
        if index//count!=s.asset_page:
            s.asset_page=index//count
            thumbnails.start(context.scene)
        s.asset_index=index
        s.asset_picker=str(index)

def install(ns):
    global original_generate
    ann=ns['BS_Settings'].__annotations__
    ann.update({
        'studio_tab':EnumProperty(name='Workspace',items=[('LIBRARY','Library','', 'ASSET_MANAGER',0),('MATERIAL','Material','','MATERIAL',1),('LIGHT','Lighting','','LIGHT_AREA',2),('RENDER','Render','','RENDER_STILL',3)],default='LIBRARY'),
        'advanced':BoolProperty(name='Advanced',default=False),
        'asset_provider':EnumProperty(name='Library',items=[('ALL','All Libraries',''),('POLY','Poly Haven',''),('AMBIENT','ambientCG','Materials only')],default='ALL',update=auto_search),
        'asset_type':EnumProperty(name='Type',items=[('1','Materials',''),('2','Models',''),('0','HDRIs','')],default='1',update=auto_search),
        'asset_topic':EnumProperty(name='Category',items=[('ALL','All','')]+[(k,k.title(),'') for k in ('wood','metal','fabric','stone','concrete','leather')],update=auto_search),
        'hdri_environment':EnumProperty(name='Environment',items=[('ALL','All',''),('indoor','Indoor',''),('outdoor','Outdoor','')],update=auto_search),
        'asset_sort':EnumProperty(name='Sort',items=[('NAME','Name',''),('POPULAR','Popularity','')],update=auto_search),
        'library_view':EnumProperty(name='View',items=[('ALL','All Assets',''),('FAVORITES','Favorites',''),('RECENT','Recently Used',''),('GROUP','My Collection',''),('DOWNLOADED','Downloaded','')],update=auto_search),
        'group_name':StringProperty(name='Collection',default='Booths'),
        'collection_choice':EnumProperty(name='Collection',items=collection_items,update=collection_update),
        'asset_picker':EnumProperty(name='Click Preview to Select',items=picker_items,update=picker_update),
        'browser_width':IntProperty(name='Browser Width',default=1100,min=640,max=1600),
        'browser_columns':IntProperty(name='Columns',default=4,min=2,max=6,update=photo_settings),
        'thumbnail_scale':FloatProperty(name='Image Size',default=5,min=2,max=8),
        'preview_limit':IntProperty(name='Images in Memory',default=64,min=16,max=256),
        'cache_info':StringProperty(default=''),
        'downloads':CollectionProperty(type=BS_Download),
        'live_material':BoolProperty(name='Live Material Preview',default=False,update=live_mapping),
        'grain_axis':EnumProperty(name='Grain Axis',items=[(a,a,'') for a in 'XYZ'],default='Z'),
        'tile_cm':FloatProperty(name='Tile Width (cm)',default=100,min=.1,max=100000,update=tile_cm_update),
        'texture_rotation':FloatVectorProperty(name='Rotation XYZ',subtype='EULER',size=3,update=live_mapping),
        'texture_stretch':FloatVectorProperty(name='Scale XYZ',size=3,default=(1,1,1),min=.001,max=1000,update=live_mapping),
        'texture_offset':FloatVectorProperty(name='Offset',size=3,update=live_mapping),
        'material_scale':FloatProperty(name='Texture Scale',default=1,min=.001,max=1000,update=live_mapping),
        'material_bump':FloatProperty(name='Surface Detail',default=.2,min=0,max=1,update=live_mapping),
        'profile_name':StringProperty(name='Profile Name',default='My Studio'),
        'profile_choice':EnumProperty(name='Saved Profile',items=profile_items),
        'profile_file':StringProperty(name='Profile JSON',subtype='FILE_PATH'),
        'snapshot_a':StringProperty(),'snapshot_b':StringProperty(),'solo_backup':StringProperty()
    })
    pro.BS_Asset.__annotations__.update(metadata=StringProperty(default='{}'),details=StringProperty())
    def pick(self,context):
        refresh_picker(context,self.index); return {'FINISHED'}
    pro.BS_OT_asset_pick.execute=pick
    def page(self,context):
        s=S(context); pages=max(1,math.ceil(len(s.assets)/thumbnails.page_size(s)))
        set_asset_page(context,s.asset_page+self.step); return {'FINISHED'}
    pro.BS_OT_asset_page.execute=page
    original_generate=pro.generate
    ns['generate']=preserve_generate(pro.generate); pro.generate=ns['generate']
    remove={'BS_OT_library','BS_OT_mapping'}
    # Old panels are replaced with a single tabbed workspace; underlying operators remain.
    ns['CLASSES']=tuple(c for c in ns['CLASSES'] if not issubclass(c,bpy.types.Panel) and c.__name__ not in remove)
    ns['CLASSES']=(BS_Download,)+ns['CLASSES']+(BS_OT_library3,BS_OT_mapping3,BS_OT_job,BS_OT_organize,BS_OT_browser,BS_OT_grain,BS_OT_profile,BS_OT_light_control,BS_OT_light_canvas,BS_OT_cache,BS_OT_asset_details,BS_PT_main3)

def shutdown():
    global _epoch
    _epoch+=1
    for j in _jobs.values(): j['cancel'].set()
    _jobs.clear(); _search_tokens.clear(); _live_tokens.clear(); _picker_items.clear()
    if _load_handler in bpy.app.handlers.load_post: bpy.app.handlers.load_post.remove(_load_handler)

def validate_profile(data):
    if not isinstance(data,dict) or not isinstance(data.get('lights'),list) or len(data['lights'])>128:
        raise ValueError('Invalid light profile')
    ids=set()
    def numbers(value,n,lo=-100000,hi=100000):
        if not isinstance(value,(list,tuple)) or len(value)!=n: raise ValueError('Invalid profile vector')
        if not all(isinstance(v,(float,int)) and math.isfinite(v) and lo<=v<=hi for v in value): raise ValueError('Invalid profile number')
    for r in data['lights']:
        if not isinstance(r,dict) or not isinstance(r.get('id'),str) or r['id'] in ids: raise ValueError('Duplicate or invalid light ID')
        ids.add(r['id'])
        if not isinstance(r.get('name'),str) or r.get('type') not in {'AREA','POINT','SUN','SPOT'}: raise ValueError('Invalid light type')
        numbers(r.get('location'),3); numbers(r.get('rotation'),3); numbers(r.get('color'),3,0,1)
        for name in ('energy','size','size_y','spot_size','spot_blend'):
            v=r.get(name)
            if not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1000000: raise ValueError('Invalid light parameter')
    if data.get('style') not in {m[0] for m in pro.MODES}: raise ValueError('Unknown lighting style')
    for name in ('hdri','world'):
        if not isinstance(data.get(name,''),str): raise ValueError('Invalid environment path')
    for name in ('strength','rotation','exposure'):
        v=data.get(name,0)
        if not isinstance(v,(int,float)) or not math.isfinite(v): raise ValueError('Invalid environment parameter')
def ready():
    global _mute
    if not hasattr(bpy.data,'scenes'):
        epoch=_epoch
        def delayed():
            if epoch==_epoch: ready()
            return None
        bpy.app.timers.register(delayed,first_interval=.1)
        return
    _mute=True
    try:
        for scene in bpy.data.scenes:
            if not hasattr(scene,'booth_studio'): continue
            s=scene.booth_studio
            s.tile_cm=s.tile_size*100; s.library_busy=False
            for e in s.downloads:
                if e.status in {'QUEUED','RUNNING','CANCELLING'}:
                    e.status='ERROR'; e.message='Interrupted by file load or restart; press Retry'
    finally: _mute=False
    if _load_handler not in bpy.app.handlers.load_post: bpy.app.handlers.load_post.append(_load_handler)
@bpy.app.handlers.persistent
def _load_handler(_):
    shutdown(); thumbnails.shutdown(); ready()

class BS_OT_asset_details(bpy.types.Operator):
    bl_idname='booth_studio.asset_details'; bl_label='Asset Preview and Details'
    def invoke(self,context,event):
        a=selected(S(context))
        if not a: return {'CANCELLED'}
        if not a.details: enqueue(context,'INFO')
        return context.window_manager.invoke_props_dialog(self,width=600)
    def draw(self,context):
        s=S(context); a=selected(s)
        if a and thumbnails.icon(a): self.layout.template_icon(icon_value=thumbnails.icon(a),scale=18)
        details_ui(self.layout,s,False)
    def execute(self,context): return {'FINISHED'}

def collection_items(s,context):
    names=sorted(db(s)['groups']); key=('collections',str(root_for(s)),tuple(names))
    if key not in _enum_cache: _enum_cache[key]=[(n,n,n) for n in names] or [('NONE','No collections','')]
    return _enum_cache[key]
def collection_update(s,context):
    if _mute or s.collection_choice=='NONE': return
    s.group_name=s.collection_choice
    if s.library_view=='GROUP': auto_search(s,context)

def downloaded_rows(s,data):
    root=root_for(s); items=dict(data['items'])
    try: catalog=json.loads((root/'polyhaven_catalog.json').read_text(encoding='utf-8'))
    except (FileNotFoundError,ValueError): catalog={}
    manifests=list(root.glob('*/*/provenance.json'))+list(root.glob('ambientCG/*/*/provenance.json'))
    for path in manifests:
        try:
            m=json.loads(path.read_text(encoding='utf-8')); aid=m['asset']; provider='AMBIENT' if m['provider']=='ambientCG' else 'POLY'; k=provider+':'+aid
            if k in items: continue
            meta=catalog.get(aid,{}) if provider=='POLY' else {}
            url=meta.get('thumbnail_url','')
            if not url and provider=='POLY': url='https://cdn.polyhaven.com/asset_img/thumbs/'+aid+'.png?width=256&height=256'
            if not url and provider=='AMBIENT': url='https://acg-media.struffelproductions.com/file/ambientCG-Web/media/thumbnail/256-PNG/'+aid+'.png'
            items[k]={'provider':provider,'id':aid,'name':meta.get('name',aid.replace('_',' ').title()),'type':str(m.get('kind',1)),'thumbnail_url':url,'meta':meta}
        except (KeyError,ValueError,OSError): continue
    return list(items.values())

def set_asset_page(context,page):
    s=S(context);count=thumbnails.page_size(s)
    pages=max(1,math.ceil(len(s.assets)/count))
    s.asset_page=max(0,min(pages-1,page))
    if s.assets:
        begin=s.asset_page*count
        if not begin<=s.asset_index<min(begin+count,len(s.assets)): s.asset_index=begin
        s.asset_picker=str(s.asset_index)
    thumbnails.start(context.scene)
