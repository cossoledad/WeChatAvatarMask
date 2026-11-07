"""Local-only API. Uploaded images live in bounded, expiring memory sessions."""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import RLock, BoundedSemaphore
from time import monotonic
from uuid import uuid4
import json
import re
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
import numpy as np
import cv2

from .detection import detect
from .imaging import decode, png, render, MAX_PIXELS
from .layout import detect_right, shared_radius
from .settings import integer
from .web import frontend_file, LanOnlyMiddleware

ROOT=Path(__file__).resolve().parents[1]
SOURCE='D579F7B0CC5034C39F4CE4847719A95D.jpg'
REPLACEMENT='F5AC4F279953D1A7B46643AE05353084.jpg'
DEFAULT_AVATAR=ROOT/'backend'/'assets'/'default-avatar.jpg'
TTL=integer('AVATAR_MASK_SESSION_TTL',30*60,60,86400)
MAX_UPLOAD=integer('AVATAR_MASK_MAX_UPLOAD_MB',15,1,50)*1024*1024
MAX_SESSION_BYTES=integer('AVATAR_MASK_MAX_SESSION_MB',220,32,2048)*1024*1024
MAX_TOTAL_BYTES=integer('AVATAR_MASK_MAX_TOTAL_MB',440,32,4096)*1024*1024
MAX_SESSIONS=integer('AVATAR_MASK_MAX_SESSIONS',4,1,16)
if MAX_TOTAL_BYTES<MAX_SESSION_BYTES:raise ValueError('AVATAR_MASK_MAX_TOTAL_MB must be >= AVATAR_MASK_MAX_SESSION_MB')
cv2.setNumThreads(integer('AVATAR_MASK_CV_THREADS',2,1,8))
sessions={}
lock=RLock()
processing=BoundedSemaphore(1)
PRODUCTION=bool(integer('AVATAR_MASK_PRODUCTION',0,0,1))
app=FastAPI(title='Avatar Mask · 本地头像替换',version='0.3.0',
            docs_url=None if PRODUCTION else '/docs',redoc_url=None if PRODUCTION else '/redoc',
            openapi_url=None if PRODUCTION else '/openapi.json')
if PRODUCTION:app.add_middleware(LanOnlyMiddleware)
app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5178','http://localhost:5178'],allow_methods=['GET','POST','DELETE'],allow_headers=['Content-Type'])


class Box(BaseModel):
    x:int=Field(ge=0)
    y:int=Field(ge=0)
    w:int=Field(ge=8,le=1024)
    h:int=Field(ge=8,le=1024)
    radius:float=Field(default=8,ge=0,le=512)
    clip_h:int|None=Field(default=None,ge=1,le=1024)
    enabled:bool=True
    score:float=Field(default=1,ge=-1,le=1)
    edge_quality:float=Field(default=0,ge=0,le=1)
    color_mae:float=0
    method:str='manual'


class Edits(BaseModel):
    boxes:list[Box]=Field(max_length=150)
    radius:float|None=Field(default=None,ge=0,le=512)


class Export(BaseModel):
    edits:dict[str,list[Box]]
    radii:dict[str,float]=Field(default_factory=dict)


@dataclass
class Session:
    source:np.ndarray|None
    replacement:np.ndarray
    images:list
    created:float

    @property
    def size(self):
        return (self.source.nbytes if self.source is not None else 0)+self.replacement.nbytes+sum(i['data'].nbytes for i in self.images)


def purge():
    for key in list(sessions):
        if monotonic()-sessions[key].created>TTL:
            del sessions[key]


def get_session(sid):
    with lock:
        purge()
        if sid not in sessions:
            raise HTTPException(404,'任务已清除或已过期，请重新选择图片')
        return sessions[sid]


def get_image(session,iid):
    for item in session.images:
        if item['id']==iid:return item
    raise HTTPException(404,'图片不存在')


def validated(boxes,im,radius=None):
    H,W=im.shape[:2]
    for b in boxes:
        visible=b.clip_h or b.h
        if b.x+b.w>W or visible>b.h or b.y+visible>H or b.radius>min(b.w,b.h)/2:
            raise HTTPException(422,'头像框超出图片，或圆角超过短边的一半')
    result=[b.model_dump() for b in boxes]
    if result:
        # One radius belongs to the image, including manual and disabled boxes.
        common=shared_radius(result) if radius is None else radius
        if not np.isfinite(common) or common<0 or common>min(min(b.w,b.h)/2 for b in boxes):
            raise HTTPException(422,'整图圆角不能超过最小头像短边的一半')
        for b in result:b['radius']=common
    return result


def read_upload(file):
    try:
        data=file.file.read(MAX_UPLOAD+1)
        if len(data)>MAX_UPLOAD:raise HTTPException(413,f'每个文件不能超过 {MAX_UPLOAD//1024//1024} MB')
        return decode(data)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    finally:
        file.file.close()


def create(source,replacement,images,threshold,min_size,max_size):
    if not images:raise HTTPException(422,'没有可用截图')
    if min_size>max_size:raise HTTPException(422,'最小尺寸不能大于最大尺寸')
    size=(source.nbytes if source is not None else 0)+replacement.nbytes+sum(im.nbytes for _,im in images)
    if size>MAX_SESSION_BYTES:raise HTTPException(413,'这一批图片解码后过大，请减少图片数量')
    if not processing.acquire(blocking=False):raise HTTPException(429,'正在处理另一批图片，请稍后再试')
    try:
        with lock:
            purge()
            if len(sessions)>=MAX_SESSIONS or sum(s.size for s in sessions.values())+size>MAX_TOTAL_BYTES:
                raise HTTPException(429,'本地内存任务已满，请先清除已有任务')
        items=[]
        for name,im in images:
            boxes=detect_right(im) if source is None else detect(im,source,threshold,min_size,max_size)
            radius=shared_radius(boxes)
            for b in boxes:b['radius']=radius
            items.append(dict(id=uuid4().hex,name=name,data=im,boxes=boxes,radius=radius))
        sid=uuid4().hex
        with lock:sessions[sid]=Session(source,replacement,items,monotonic())
        return dict(id=sid,expires_in=TTL,mode='right-layout' if source is None else 'template',images=[dict(id=i['id'],name=i['name'],width=i['data'].shape[1],height=i['data'].shape[0],boxes=i['boxes'],radius=i['radius']) for i in items])
    finally:processing.release()


@app.get('/api/health')
def health():return dict(status='ok',processing='local',engine='right-layout + multiscale NCC',version='0.3.0',limits=dict(max_upload_mb=MAX_UPLOAD//1024//1024,max_pixels=MAX_PIXELS))


@app.get('/api/default-avatar')
def default_avatar():
    return Response(DEFAULT_AVATAR.read_bytes(),media_type='image/jpeg',headers={'Cache-Control':'public, max-age=3600'})


@app.post('/api/sessions')
def upload(screenshots:list[UploadFile]=File(...),source:UploadFile|None=File(None),replacement:UploadFile|None=File(None),threshold:float=Form(.78,ge=.55,le=.99),min_size:int=Form(24,ge=12,le=512),max_size:int=Form(220,ge=12,le=512)):
    if not 1<=len(screenshots)<=30:raise HTTPException(422,'每批支持 1–30 张截图')
    src=read_upload(source) if source else None
    rep=read_upload(replacement) if replacement else decode(DEFAULT_AVATAR.read_bytes())
    images=[]
    total=(src.nbytes if src is not None else 0)+rep.nbytes
    for f in screenshots:
        im=read_upload(f);total+=im.nbytes
        if total>MAX_SESSION_BYTES:raise HTTPException(413,'这一批图片过大，请减少图片数量')
        images.append(((f.filename or 'screenshot').replace('\\','/').split('/')[-1],im))
    return create(src,rep,images,threshold,min_size,max_size)


@app.post('/api/demo')
def demo():
    data=ROOT/'test.data'
    if not data.is_dir():raise HTTPException(404,'本机未找到 test.data 示例图片')
    paths=[p for p in sorted(data.glob('*.jpg')) if p.name not in {SOURCE,REPLACEMENT}]
    images=[];total=0
    for p in paths[:30]:
        im=decode(p.read_bytes());total+=im.nbytes
        if total>MAX_SESSION_BYTES:raise HTTPException(413,'测试集过大，请减少图片数量')
        images.append((p.name,im))
    return create(None,decode(DEFAULT_AVATAR.read_bytes()),images,.78,24,220)


@app.get('/api/sessions/{sid}/assets/{kind}')
def asset(sid:str,kind:str):
    s=get_session(sid)
    if kind not in {'source','replacement'}:raise HTTPException(404,'头像不存在')
    if getattr(s,kind) is None:raise HTTPException(404,'此任务无需原头像')
    return Response(png(getattr(s,kind)),media_type='image/png',headers={'Cache-Control':'no-store'})


@app.post('/api/sessions/{sid}/replacement')
def replace_avatar(sid:str,replacement:UploadFile=File(...)):
    rep=read_upload(replacement)
    with lock:
        s=get_session(sid)
        delta=rep.nbytes-s.replacement.nbytes
        if s.size+delta>MAX_SESSION_BYTES or sum(item.size for item in sessions.values())+delta>MAX_TOTAL_BYTES:
            raise HTTPException(413,'头像过大，请选择较小的图片')
        s.replacement=rep
    return dict(updated=True)


@app.get('/api/sessions/{sid}/images/{iid}')
def original(sid:str,iid:str):
    return Response(png(get_image(get_session(sid),iid)['data']),media_type='image/png',headers={'Cache-Control':'no-store'})


@app.get('/api/sessions/{sid}/images/{iid}/thumbnail')
def thumbnail(sid:str,iid:str):
    s=get_session(sid);item=get_image(s,iid)
    result=render(item['data'],s.source,s.replacement,item['boxes'])
    scale=min(1,400/result.shape[1],700/result.shape[0])
    result=cv2.resize(result,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
    return Response(png(result),media_type='image/png',headers={'Cache-Control':'no-store'})


@app.post('/api/sessions/{sid}/images/{iid}/render')
def preview(sid:str,iid:str,body:Edits):
    s=get_session(sid);im=get_image(s,iid)['data']
    return Response(png(render(im,s.source,s.replacement,validated(body.boxes,im,body.radius))),media_type='image/png',headers={'Cache-Control':'no-store'})


@app.post('/api/sessions/{sid}/export')
def export(sid:str,body:Export):
    s=get_session(sid)
    replacement=s.replacement
    unknown=set(body.edits)-{i['id'] for i in s.images}
    if unknown:raise HTTPException(422,'导出包含未知图片')
    if set(body.radii)-{i['id'] for i in s.images}:raise HTTPException(422,'圆角参数包含未知图片')
    checked={i['id']:validated(body.edits[i['id']],i['data'],body.radii.get(i['id'])) for i in s.images if i['id'] in body.edits}
    if not checked:raise HTTPException(422,'请选择要导出的图片')
    out=BytesIO();audit=[]
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for index,i in enumerate(s.images):
            if i['id'] not in checked:continue
            boxes=checked[i['id']]; result=render(i['data'],s.source,replacement,boxes)
            name=re.sub(r'[^\w.\-]','_',Path(i['name']).stem)[:100]
            z.writestr(f'{index+1:02d}-{name}.png',png(result))
            audit.append(dict(file=i['name'],boxes=boxes,changed_pixels=int(np.count_nonzero(np.any(result!=i['data'],axis=2)))))
        z.writestr('audit.json',json.dumps(dict(engine='right-layout' if s.source is None else 'multiscale-ncc',radius_policy='one radius per image',output='PNG, no source metadata',images=audit),ensure_ascii=False,indent=2))
    return Response(out.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="avatar-mask.zip"','Cache-Control':'no-store'})


@app.delete('/api/sessions/{sid}')
def delete(sid:str):
    with lock:sessions.pop(sid,None)
    return dict(deleted=True)


@app.get('/',include_in_schema=False)
def homepage():return frontend_file('index.html')


@app.get('/{asset}',include_in_schema=False)
def static_asset(asset:str):return frontend_file(asset)
