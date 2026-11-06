from io import BytesIO
import json
import zipfile

import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.imaging import coverage, png, decode, render
from backend.layout import detect_right
from backend.main import app, sessions


def chat_scene(dark=False):
    bg=32 if dark else 237
    im=np.full((700,400,3),bg,np.uint8)
    rng=np.random.default_rng(123)
    for index,y in enumerate([100,240,380]):
        # Three unrelated avatars, none exists in the repo's sample originals.
        avatar=rng.integers(30,190,(40,40,3),dtype=np.uint8)
        avatar=cv2.GaussianBlur(avatar,(3,3),0)
        avatar[5:15,5+index:17+index]=[40,70,180]
        a=coverage(40,40,5)[...,None]
        im[y:y+40,350:390]=np.rint(avatar*a+bg*(1-a)).astype(np.uint8)
        im[y:y+30,200:330]=[140,220,100]  # outgoing bubble
        im[y+70:y+110,10:50]=avatar       # left-side avatars must stay intact
    # Common outline toolbar icon should not become an avatar candidate.
    cv2.circle(im,(370,660),15,(80,80,80),2)
    return im


def test_unknown_avatars_only_right_column_and_uniform_radius():
    for dark in [False,True]:
        im=chat_scene(dark)
        boxes=detect_right(im)
        assert len(boxes)==3
        assert len({b['radius'] for b in boxes})==1
        for b,y in zip(boxes,[100,240,380]):
            assert abs(b['x']-350)<=1 and abs(b['y']-y)<=1
            assert abs(b['w']-40)<=1
        out=render(im,None,np.full((60,60,3),[80,150,60],np.uint8),boxes)
        assert np.array_equal(out[:,:340],im[:,:340])


def test_source_free_partial_avatar_and_negative():
    im=chat_scene()
    im[560:578,350:390]=im[100:118,350:390]
    boxes=detect_right(im)
    assert len(boxes)==4
    assert boxes[-1].get('clip_h')==18
    assert len({b['radius'] for b in boxes})==1
    assert detect_right(np.full_like(im,237))==[]


def test_upload_only_screenshots_default_avatar_and_export_radius():
    client=TestClient(app)
    im=chat_scene()
    response=client.post('/api/sessions',files={'screenshots':('chat.png',png(im),'image/png')})
    assert response.status_code==200,response.text
    data=response.json();sid=data['id'];item=data['images'][0];iid=item['id']
    try:
        assert data['mode']=='right-layout'
        assert sessions[sid].source is None
        assert len(item['boxes'])==3
        assert client.get(f'/api/sessions/{sid}/assets/source').status_code==404
        assert client.get('/api/default-avatar').status_code==200
        thumb=client.get(f'/api/sessions/{sid}/images/{iid}/thumbnail')
        assert thumb.status_code==200
        # Even contradictory legacy per-box values cannot produce mixed radii.
        edits=item['boxes'];edits[0]['radius']=1;edits[1]['radius']=9
        result=client.post(f'/api/sessions/{sid}/export',json={'edits':{iid:edits},'radii':{iid:6}})
        assert result.status_code==200,result.text
        with zipfile.ZipFile(BytesIO(result.content)) as z:
            audit=json.loads(z.read('audit.json'))
            assert {b['radius'] for b in audit['images'][0]['boxes']}=={6}
            out=decode(z.read('01-chat.png'))
            assert np.array_equal(out[:,:340],im[:,:340])
        bad=client.post(f'/api/sessions/{sid}/images/{iid}/render',json={'boxes':edits,'radius':30})
        assert bad.status_code==422
    finally:client.delete(f'/api/sessions/{sid}')
