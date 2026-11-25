"""LAN API with a persistent avatar library and bounded memory cache."""
from dataclasses import dataclass
import os
from pathlib import Path
from threading import RLock, BoundedSemaphore
from time import time
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
import numpy as np
import cv2

from .detection import detect
from .imaging import decode, png, render, MAX_PIXELS
from .layout import detect_right, shared_radius
from .settings import integer
from .storage import Storage
from .web import frontend_file, LanOnlyMiddleware

ROOT=Path(__file__).resolve().parents[1]
SOURCE='D579F7B0CC5034C39F4CE4847719A95D.jpg'
REPLACEMENT='F5AC4F279953D1A7B46643AE05353084.jpg'
DEFAULT_AVATAR=ROOT/'backend'/'assets'/'default-avatar.jpg'
TTL=integer('AVATAR_MASK_SESSION_TTL',30*60,60,86400)
storage=Storage(os.environ.get('AVATAR_MASK_DATA_DIR',str(ROOT/'data')))
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
app=FastAPI(title='Avatar Mask',version='0.4.1',
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
    clip_top:int=Field(default=0,ge=0,le=1023)
    enabled:bool=True
    score:float=Field(default=1,ge=-1,le=1)
    edge_quality:float=Field(default=0,ge=0,le=1)
    color_mae:float=0
    method:str='manual'


class Edits(BaseModel):
    boxes:list[Box]=Field(max_length=150)
    radius:float|None=Field(default=None,ge=0,le=512)
    revision:int=Field(default=0,ge=0)


class Selection(BaseModel):
    session_id:str|None=None


@dataclass
class Session:
    source:np.ndarray|None
    replacement:np.ndarray
    images:list
    created:float
    replacement_id:str='builtin'
    accessed:float=0

    @property
    def size(self):
        return (self.source.nbytes if self.source is not None else 0)+self.replacement.nbytes+sum(i['data'].nbytes for i in self.images)


def purge():
    for key in list(sessions):
        if time()-sessions[key].accessed>TTL:
            del sessions[key]


def cache(sid,session):
    purge()
    while sessions and (len(sessions)>=MAX_SESSIONS or sum(s.size for s in sessions.values())+session.size>MAX_TOTAL_BYTES):
        del sessions[min(sessions,key=lambda key:sessions[key].accessed)]
    session.accessed=time()
    sessions[sid]=session


def get_session(sid):
    with lock:
        purge()
        if sid not in sessions:
            try:restored=Session(**storage.load_task(sid))
            except KeyError as exc:raise HTTPException(404,'任务已清除，请重新选择图片') from exc
            cache(sid,restored)
        sessions[sid].accessed=time()
        return sessions[sid]


def public_session(sid,s):
    return dict(id=sid,replacement_id=s.replacement_id,mode='right-layout' if s.source is None else 'template',images=[
        dict(id=i['id'],name=i['name'],width=i['data'].shape[1],height=i['data'].shape[0],boxes=i['boxes'],radius=i['radius'],revision=i.get('revision',0)) for i in s.images])


def avatar_image(avatar_id):
    try:return decode(DEFAULT_AVATAR.read_bytes() if avatar_id=='builtin' else storage.avatar(avatar_id))
    except KeyError as exc:raise HTTPException(404,'头像不存在') from exc


def remember_avatar(image,name):
    scale=min(1,128/image.shape[1],128/image.shape[0])
    thumbnail=cv2.resize(image,(max(1,round(image.shape[1]*scale)),max(1,round(image.shape[0]*scale))),interpolation=cv2.INTER_AREA)
    return storage.add_avatar(png(image),png(thumbnail),Path(name.replace('\\','/')).name[:150])


def select_avatar(avatar_id,sid=None):
    rep=avatar_image(avatar_id)
    with lock:
        if sid:
            s=get_session(sid)
            delta=rep.nbytes-s.replacement.nbytes
            if s.size+delta>MAX_SESSION_BYTES or sum(item.size for item in sessions.values())+delta>MAX_TOTAL_BYTES:
                raise HTTPException(413,'头像过大，请选择较小的图片')
            old,old_id=s.replacement,s.replacement_id
            s.replacement,s.replacement_id=rep,avatar_id
            try:storage.update_task(sid,s,replacement=True,select=True)
            except Exception:
                s.replacement,s.replacement_id=old,old_id
                raise
        else:storage.select(avatar_id)


def get_image(session,iid):
    for item in session.images:
        if item['id']==iid:return item
    raise HTTPException(404,'图片不存在')


def validated(boxes,im,radius=None):
    H,W=im.shape[:2]
    for b in boxes:
        visible=b.clip_h if b.clip_h is not None else b.h-b.clip_top
        if b.x+b.w>W or visible<1 or b.clip_top+visible>b.h or b.y+visible>H or b.radius>min(b.w,b.h)/2:
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


def create(source,replacement,images,threshold,min_size,max_size,replacement_id='builtin'):
    if not images:raise HTTPException(422,'没有可用截图')
    if min_size>max_size:raise HTTPException(422,'最小尺寸不能大于最大尺寸')
    size=(source.nbytes if source is not None else 0)+replacement.nbytes+sum(im.nbytes for _,im in images)
    if size>MAX_SESSION_BYTES:raise HTTPException(413,'这一批图片解码后过大，请减少图片数量')
    if not processing.acquire(blocking=False):raise HTTPException(429,'正在处理另一批图片，请稍后再试')
    try:
        items=[]
        for name,im in images:
            boxes=detect_right(im) if source is None else detect(im,source,threshold,min_size,max_size)
            radius=shared_radius(boxes)
            for b in boxes:b['radius']=radius
            items.append(dict(id=uuid4().hex,name=name,data=im,boxes=boxes,radius=radius,revision=0))
        sid=uuid4().hex
        s=Session(source,replacement,items,time(),replacement_id)
        with lock:
            storage.put_task(sid,s)
            cache(sid,s)
        return public_session(sid,s)
    finally:processing.release()


@app.get('/api/health')
def health():return dict(status='ok',processing='local',engine='right-layout + multiscale NCC',version='0.4.1',limits=dict(max_upload_mb=MAX_UPLOAD//1024//1024,max_pixels=MAX_PIXELS))


@app.get('/api/default-avatar')
def default_avatar():
    return avatar_file(storage.selected())


@app.get('/api/avatars')
def avatars():
    return dict(selected=storage.selected(),items=[dict(id='builtin',name='默认头像'),*storage.avatars()])


@app.get('/api/avatars/{avatar_id}/image')
def avatar_file(avatar_id:str,thumbnail:bool=False):
    if avatar_id=='builtin':return Response(DEFAULT_AVATAR.read_bytes(),media_type='image/jpeg',headers={'Cache-Control':'no-store'})
    try:data=storage.avatar(avatar_id,thumbnail)
    except KeyError as exc:raise HTTPException(404,'头像不存在') from exc
    return Response(data,media_type='image/png',headers={'Cache-Control':'no-store'})


@app.post('/api/avatars')
def add_avatar(replacement:UploadFile=File(...),session_id:str|None=Form(None)):
    image=read_upload(replacement)
    avatar_id=remember_avatar(image,replacement.filename or '头像')
    select_avatar(avatar_id,session_id)
    return dict(id=avatar_id)


@app.post('/api/avatars/{avatar_id}/select')
def choose_avatar(avatar_id:str,body:Selection):
    select_avatar(avatar_id,body.session_id)
    return dict(id=avatar_id)


@app.post('/api/sessions')
def upload(screenshots:list[UploadFile]=File(...),source:UploadFile|None=File(None),replacement:UploadFile|None=File(None),avatar_id:str|None=Form(None),threshold:float=Form(.78,ge=.55,le=.99),min_size:int=Form(24,ge=12,le=512),max_size:int=Form(220,ge=12,le=512)):
    if not 1<=len(screenshots)<=30:raise HTTPException(422,'每批支持 1–30 张截图')
    src=read_upload(source) if source else None
    if replacement:
        rep=read_upload(replacement)
        avatar_id=remember_avatar(rep,replacement.filename or '头像')
    else:
        avatar_id=avatar_id or storage.selected()
        rep=avatar_image(avatar_id)
    images=[]
    total=(src.nbytes if src is not None else 0)+rep.nbytes
    for f in screenshots:
        im=read_upload(f);total+=im.nbytes
        if total>MAX_SESSION_BYTES:raise HTTPException(413,'这一批图片过大，请减少图片数量')
        images.append(((f.filename or 'screenshot').replace('\\','/').split('/')[-1],im))
    return create(src,rep,images,threshold,min_size,max_size,avatar_id)


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


@app.get('/api/sessions/{sid}')
def task(sid:str):return public_session(sid,get_session(sid))


@app.get('/api/sessions/{sid}/assets/{kind}')
def asset(sid:str,kind:str):
    s=get_session(sid)
    if kind not in {'source','replacement'}:raise HTTPException(404,'头像不存在')
    if getattr(s,kind) is None:raise HTTPException(404,'此任务无需原头像')
    return Response(png(getattr(s,kind)),media_type='image/png',headers={'Cache-Control':'no-store'})


@app.post('/api/sessions/{sid}/replacement')
def replace_avatar(sid:str,replacement:UploadFile=File(...)):
    rep=read_upload(replacement)
    avatar_id=remember_avatar(rep,replacement.filename or '头像')
    select_avatar(avatar_id,sid)
    return dict(updated=True,id=avatar_id)


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
    with lock:
        s=get_session(sid);item=get_image(s,iid);im=item['data']
        boxes=validated(body.boxes,im,body.radius)
        if body.revision>=item.get('revision',0):
            before=item.copy()
            item.update(boxes=boxes,radius=boxes[0]['radius'] if boxes else (body.radius or 0),revision=body.revision)
            try:storage.update_task(sid,s)
            except Exception:
                item.clear();item.update(before)
                raise
        source,replacement=s.source,s.replacement
    return Response(png(render(im,source,replacement,boxes)),media_type='image/png',headers={'Cache-Control':'no-store'})


@app.delete('/api/sessions/{sid}')
def delete(sid:str):
    with lock:
        storage.delete_task(sid)
        sessions.pop(sid,None)
    return dict(deleted=True)


@app.get('/',include_in_schema=False)
def homepage():return frontend_file('index.html')


@app.get('/{asset}',include_in_schema=False)
def static_asset(asset:str):return frontend_file(asset)
