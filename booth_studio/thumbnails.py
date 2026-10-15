"""Bounded preview cache, cancellable requests and next-page disk prefetch."""
import bpy, bpy.utils.previews
import queue, threading, hashlib
from collections import OrderedDict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit,parse_qsl,urlencode,urlunsplit
from . import library,tasksnet
PAGE_SIZE=6
_previews=None
_tokens={}
_epoch=0
_locks={}
_guard=threading.Lock()
_pool=None
_lru=OrderedDict()
_pending={}
def page_size(s): return getattr(s,'browser_columns',3)*2
def key(item): return item.provider+':'+item.asset_id
def icon(item):
    k=key(item)
    if _previews and k in _previews:
        _lru[k]=None; _lru.move_to_end(k)
        return _previews[k].icon_id
    return 0
def cached_photo(root,provider,aid,url):
    if not url: raise ValueError('Thumbnail unavailable')
    parts=urlsplit(url)
    if parts.hostname=='cdn.polyhaven.com':
        params=dict(parse_qsl(parts.query)); params.update(width='256',height='256',format='png')
        url=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(params),''))
    path=Path(root)/'thumbnails'/(hashlib.sha256((provider+':'+aid).encode()).hexdigest()+'.png')
    with _guard: lock=_locks.setdefault(str(path),threading.Lock())
    with lock: return library.download({'url':url},path)
def redraw():
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type=='VIEW_3D': area.tag_redraw()
def prune(limit,protected=()):
    while len(_lru)>limit:
        candidate=next((k for k in _lru if k not in protected),None)
        if candidate is None: break
        _lru.pop(candidate,None)
        if _previews and candidate in _previews: del _previews[candidate]
def start(scene):
    global _previews,_pool
    if _previews is None: _previews=bpy.utils.previews.new()
    if _pool is None: _pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='BSPhoto')
    s=scene.booth_studio; sid=scene.as_pointer(); count=page_size(s); begin=s.asset_page*count
    token=_tokens.get(sid,0)+1; _tokens[sid]=token; epoch=_epoch
    for f in _pending.pop(sid,[]): f.cancel()
    # Snapshot plain values; workers never dereference bpy data.
    visible=[(key(a),a.provider,a.asset_id,a.thumbnail_url) for a in list(s.assets)[begin:begin+count]]
    ahead=[(key(a),a.provider,a.asset_id,a.thumbnail_url) for a in list(s.assets)[begin+count:begin+count*2]]
    wanted={k for k,_,_,_ in visible}
    rows=[(*r,True) for r in visible if r[0] not in _previews]+[(*r,False) for r in ahead]
    out=queue.Queue(); root=bpy.path.abspath(s.cache_path); s.preview_status='Loading visible photos...'
    def worker(row):
        k,p,aid,url,show=row
        tasksnet.local.cancel=lambda:epoch!=_epoch or _tokens.get(sid)!=token
        try:
            tasksnet.check(); path=cached_photo(root,p,aid,url)
            if show: out.put((k,path))
        except Exception:
            if show: out.put((k,''))
        finally: tasksnet.local.cancel=None
    futures=[_pool.submit(worker,r) for r in rows]; _pending[sid]=futures
    def finish():
        if epoch!=_epoch or _tokens.get(sid)!=token: return None
        if not any(sc.as_pointer()==sid for sc in bpy.data.scenes): return None
        changed=False
        while not out.empty():
            k,path=out.get()
            if path:
                try:
                    if k not in _previews: _previews.load(k,path,'IMAGE')
                    _lru[k]=None; _lru.move_to_end(k); changed=True
                except Exception: Path(path).unlink(missing_ok=True)
        prune(getattr(s,'preview_limit',64),wanted)
        loaded=sum(k in _previews for k in wanted)
        s.preview_status='Photos %d / %d | next page prefetched'%(loaded,len(wanted))
        if changed: redraw()
        if all(f.done() for f in futures):
            _pending.pop(sid,None)
            if loaded<len(wanted): s.preview_status+=' | Search to retry'
            redraw(); return None
        return .15
    bpy.app.timers.register(finish,first_interval=.15)
def shutdown():
    global _epoch,_previews,_pool
    _epoch+=1; _tokens.clear()
    for futures in _pending.values():
        for f in futures: f.cancel()
    _pending.clear()
    if _pool: _pool.shutdown(wait=False,cancel_futures=True); _pool=None
    if _previews is not None: bpy.utils.previews.remove(_previews); _previews=None
    _lru.clear()
