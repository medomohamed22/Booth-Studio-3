"""Worker-only search, metadata and cancellable streaming transfers."""
import json, threading, time, hashlib, os, urllib.parse
from pathlib import Path
from . import library

local=threading.local()
class Cancelled(Exception): pass
AR={'خشب':'wood','خشبية':'wood','بلوط':'oak','جوز':'walnut','معدن':'metal','قماش':'fabric','سجاد':'carpet','رخام':'marble','خرسانة':'concrete','حجر':'stone','جلد':'leather','كرسي':'chair','كراسي':'chair','طاولة':'table','عربية':'car','سيارة':'car','استوديو':'studio','داخلي':'indoor','خارجي':'outdoor'}
def normalize(query): return ' '.join(AR.get(w,w) for w in query.strip().lower().split())
def digest(path,algorithm='md5'):
    h=hashlib.new(algorithm)
    with open(path,'rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''): h.update(c)
    return h.hexdigest()
def check():
    cancel=getattr(local,'cancel',None)
    if cancel and cancel(): raise Cancelled('Cancelled')
def progress(done,total,name):
    check()
    callback=getattr(local,'progress',None)
    if callback: callback(done,total,name)
def download(record,target):
    target=Path(target); target.parent.mkdir(parents=True,exist_ok=True); check()
    if target.is_file() and (not record.get('md5') or digest(target)==record['md5']):
        progress(target.stat().st_size,target.stat().st_size,target.name); return str(target)
    tmp=target.with_suffix(target.suffix+'.part')
    try:
        with library.request(record['url']) as src,tmp.open('wb') as dst:
            total=int(src.headers.get('Content-Length',record.get('size',0)) or 0); done=0
            progress(0,total,target.name)
            while True:
                check(); chunk=src.read(256*1024)
                if not chunk: break
                done+=len(chunk)
                if done>1024**3: raise ValueError('Asset exceeds 1 GB limit')
                dst.write(chunk); progress(done,total,target.name)
        check()
        if record.get('md5') and digest(tmp)!=record['md5']: raise ValueError('Checksum mismatch')
        os.replace(tmp,target)
    finally:
        if tmp.exists(): tmp.unlink()
    return str(target)
library.download=download

def row(provider,aid,meta):
    return {'provider':provider,'id':aid,'name':meta.get('name',aid),'type':str(meta.get('type',1)),
            'thumbnail_url':meta.get('thumbnail_url',''),'meta':meta}
def search(root,provider,kind,query,topic,environment,sort):
    query=normalize(query)
    terms=' '.join(v for v in (query,topic if topic!='ALL' else '',environment if environment!='ALL' and kind=='0' else '') if v)
    found=[]; errors=[]
    if provider in {'POLY','ALL'}:
        try:
            for aid,m in library.search(root,kind,terms): found.append(row('POLY',aid,m))
        except Exception as exc: errors.append('Poly Haven: '+str(exc))
    check()
    if provider in {'AMBIENT','ALL'} and kind=='1':
        try:
            for aid,m in library.search_ambient(root,terms): found.append(row('AMBIENT',aid,m))
        except Exception as exc: errors.append('ambientCG: '+str(exc))
    if not found and errors: raise ValueError('; '.join(errors))
    found.sort(key=(lambda r:-r['meta'].get('download_count',0)) if sort=='POPULAR' else (lambda r:r['name'].casefold()))
    return {'rows':found[:200],'warning':'; '.join(errors)}
def details(provider,aid,kind='1'):
    if provider=='POLY':
        data=library.get_json(library.API+'/files/'+aid)
        resolutions={}
        allowed={'hdri'} if kind=='0' else {'blend'} if kind=='2' else {'Diffuse','Rough','nor_gl','Metal','Displacement','AO','diff','rough','metal','disp','ao'}
        for channel,branch in data.items():
            if channel not in allowed: continue
            if channel not in {'hdri','blend','Diffuse','Rough','nor_gl','Metal','Displacement','AO','diff','rough','metal','disp','ao'}: continue
            for res,formats in branch.items():
                if not res.endswith('k'): continue
                fmt=next((f for f in (('hdr','exr') if channel=='hdri' else ('blend',) if channel=='blend' else ('jpg','png','exr') if channel in {'Diffuse','diff'} else ('png','jpg','exr')) if f in formats),None)
                if not fmt: continue
                rec=formats[fmt]; size=rec.get('size',0)+sum(x.get('size',0) for x in rec.get('include',{}).values())
                resolutions[res]=resolutions.get(res,0)+size
        return {'resolutions':resolutions}
    data=library.get_json('https://ambientcg.com/api/v2/full_json?'+urllib.parse.urlencode({'id':aid,'include':'downloadData'}))
    a=data['foundAssets'][0]; resolutions={}
    for d in a['downloadFolders']['default']['downloadFiletypeCategories']['zip']['downloads']:
        if d['attribute'].endswith('-JPG'): resolutions[d['attribute'].split('-')[0].lower()]=d['size']
    return {'resolutions':resolutions,'dimensions_cm':[a.get('dimensionX'),a.get('dimensionY')]}
