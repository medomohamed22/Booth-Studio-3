"""Network worker: no bpy calls. Poly Haven CC0, no API key."""
import json, os, time, hashlib, urllib.request, urllib.parse, zipfile
from pathlib import Path

UA = 'BoothStudio/2.0 (Blender; Poly Haven asset integration)'
API = 'https://api.polyhaven.com'

def request(url):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'api.polyhaven.com','dl.polyhaven.org','cdn.polyhaven.com','polyhaven.com','ambientcg.com','acg-media.struffelproductions.com','f003.backblazeb2.com'}:
        raise ValueError('Untrusted asset URL')
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':UA}), timeout=15)

def get_json(url):
    with request(url) as r: return json.load(r)

def search(root, kind, query):
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    p=root/'polyhaven_catalog.json'
    if p.exists() and time.time()-p.stat().st_mtime < 86400:
        data=json.loads(p.read_text(encoding='utf-8'))
    else:
        try:
            data=get_json(API+'/assets'); p.write_text(json.dumps(data),encoding='utf-8')
        except Exception:
            if not p.exists(): raise
            data=json.loads(p.read_text(encoding='utf-8'))
    words=query.lower().split()
    return [(k,v) for k,v in data.items() if v['type']==int(kind) and all(w in (k+' '+json.dumps(v)).lower() for w in words)][:100]

def download(record, target):
    target=Path(target); target.parent.mkdir(parents=True,exist_ok=True)
    def valid():
        return target.exists() and (not record.get('md5') or hashlib.md5(target.read_bytes()).hexdigest()==record['md5'])
    if valid(): return str(target)
    tmp=target.with_suffix(target.suffix+'.part')
    try:
        with request(record['url']) as src, tmp.open('wb') as dst:
            total=0
            while True:
                chunk=src.read(1024*1024)
                if not chunk: break
                total+=len(chunk)
                if total>1024*1024*1024: raise ValueError('Asset exceeds 1 GB limit')
                dst.write(chunk)
        if record.get('md5') and hashlib.md5(tmp.read_bytes()).hexdigest()!=record['md5']:
            raise ValueError('Download checksum mismatch')
        os.replace(tmp,target)
    finally:
        if tmp.exists(): tmp.unlink()
    return str(target)

def choose(branch, resolution, formats):
    available=branch.get(resolution)
    if available is None: raise ValueError('Resolution unavailable; choose another resolution')
    for fmt in formats:
        if fmt in available: return available[fmt],fmt
    raise ValueError('Supported file format unavailable')

def fetch(root, asset, kind, resolution):
    if not asset or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in asset):
        raise ValueError('Invalid Poly Haven asset ID')
    folder=Path(root)/asset/resolution; folder.mkdir(parents=True,exist_ok=True)
    old=folder/'provenance.json'
    if old.exists():
        cached=json.loads(old.read_text(encoding='utf-8'))
        if cached.get('kind')==kind and cached.get('result') and all(Path(f['path']).is_file() and (not f.get('md5') or hashlib.md5(Path(f['path']).read_bytes()).hexdigest()==f['md5']) for f in cached['files']):
            return cached['result']
    data=get_json(API+'/files/'+asset)
    manifest={'provider':'Poly Haven','license':'CC0','url':'https://polyhaven.com/a/'+asset,'asset':asset,'files':[]}
    def save(rec, name):
        path=download(rec, folder/name); manifest['files'].append({'path':path,'md5':rec.get('md5')}); return path
    if kind=='0':
        rec,fmt=choose(data['hdri'],resolution,('hdr','exr'))
        result=save(rec,asset+'.'+fmt)
    elif kind=='1':
        for channel,aliases in [('diff',('Diffuse','diff')),('rough',('Rough','rough')),('nor_gl',('nor_gl',)),('metal',('Metal','metal')),('disp',('Displacement','disp')),('ao',('AO','ao'))]:
            branch=next((data[k] for k in aliases if k in data),None)
            if branch is None: continue
            rec,fmt=choose(branch,resolution,('jpg','png','exr') if channel=='diff' else ('png','jpg','exr'))
            save(rec,asset+'_'+channel+'.'+fmt)
        if not any('_diff.' in f['path'] for f in manifest['files']): raise ValueError('No diffuse map available')
        result=str(folder)
    else:
        rec,fmt=choose(data['blend'],resolution,('blend',))
        result=save(rec,asset+'.blend')
        for name,dep in rec.get('include',{}).items():
            rel=Path(name)
            if rel.is_absolute() or '..' in rel.parts: raise ValueError('Unsafe dependency path')
            save(dep,rel)
    manifest.update(kind=kind,result=result)
    (folder/'provenance.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return result

def search_ambient(root,query):
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    p=root/('ambient_'+hashlib.sha256(query.encode()).hexdigest()[:16]+'.json')
    if p.exists() and time.time()-p.stat().st_mtime<86400:
        data=json.loads(p.read_text(encoding='utf-8'))
    else:
        url='https://ambientcg.com/api/v2/full_json?'+urllib.parse.urlencode({'q':query,'type':'Material','limit':100,'include':'downloadData'})
        try:
            data=get_json(url); p.write_text(json.dumps(data),encoding='utf-8')
        except Exception:
            if not p.exists(): raise
            data=json.loads(p.read_text(encoding='utf-8'))
    return [(a['assetId'],{'name':a.get('displayName',a['assetId']),'type':1,'thumbnail_url':a.get('previewImage',{}).get('256-PNG',''),'download_count':a.get('downloadCount',0),'dimensions_cm':[a.get('dimensionX'),a.get('dimensionY')]}) for a in data['foundAssets']]

def fetch_ambient(root,asset,resolution):
    if not asset.isalnum(): raise ValueError('Invalid ambientCG ID')
    folder=Path(root)/'ambientCG'/asset/resolution
    manifest_path=folder/'provenance.json'
    if manifest_path.is_file():
        manifest=json.loads(manifest_path.read_text())
        if all(Path(f['path']).is_file() and hashlib.sha256(Path(f['path']).read_bytes()).hexdigest()==f['sha256'] for f in manifest['files']): return str(folder)
    data=get_json('https://ambientcg.com/api/v2/full_json?'+urllib.parse.urlencode({'id':asset,'include':'downloadData'}))
    rows=data['foundAssets']
    if not rows: raise ValueError('Asset unavailable')
    downloads=rows[0]['downloadFolders']['default']['downloadFiletypeCategories']['zip']['downloads']
    option=next((d for d in downloads if d['attribute']==resolution.upper()+'-JPG'),None)
    if not option: raise ValueError('Resolution unavailable')
    archive=download({'url':option['downloadLink']},folder/option['fileName'])
    files=[]
    with zipfile.ZipFile(archive) as z:
        if sum(i.file_size for i in z.infolist())>1024**3: raise ValueError('Archive too large')
        for info in z.infolist():
            rel=Path(info.filename)
            if rel.is_absolute() or '..' in rel.parts: raise ValueError('Unsafe archive path')
            if rel.suffix.lower() not in {'.png','.jpg','.jpeg','.exr'}: continue
            # Flatten map files, ignoring licenses/preview docs and executable content.
            target=folder/rel.name; target.write_bytes(z.read(info))
            files.append({'path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    if not files: raise ValueError('Archive contains no supported maps')
    manifest_path.write_text(json.dumps({'provider':'ambientCG','license':'CC0','asset':asset,'url':'https://ambientcg.com/a/'+asset,'files':files},indent=2))
    return str(folder)
