import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.imaging import coverage, cover, png, decode, render, visible_rows
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


def clipped_scene():
    im=chat_scene()
    im[:60]=250  # The chat header hides the first 22 rows of a 40px avatar.
    im[60:78,350:390]=im[122:140,350:390]
    im[560:578,350:390]=im[100:118,350:390]  # Composer: opposite crop direction.
    return im


@pytest.mark.parametrize('crop_header',[False,True])
def test_top_and_bottom_crops_keep_the_correct_avatar_rows(crop_header):
    im=clipped_scene()
    if crop_header:im=im[60:].copy()
    boxes=detect_right(im)
    assert len(boxes)==5
    top,bottom=boxes[0],boxes[-1]
    assert (top['h'],top['clip_h'],top['clip_top'])==(40,18,22)
    assert bottom['clip_h']==18 and not bottom.get('clip_top')
    assert len({b['radius'] for b in boxes})==1
    replacement=np.full((40,40,3),[220,30,30],np.uint8)
    replacement[20:]=[30,60,220]
    out=render(im,None,replacement,boxes)
    mask=np.zeros(im.shape[:2],bool)
    for b in boxes:
        x,y,w,h=(b[k] for k in ('x','y','w','h'));rows=visible_rows(b,im.shape[0]);vh=rows.stop-rows.start
        a=coverage(w,h,b['radius'])[rows]
        opaque=a==1
        assert np.array_equal(out[y:y+vh,x:x+w][opaque],cover(replacement,w,h)[rows][opaque])
        mask[y:y+vh,x:x+w]|=a>0
    assert np.array_equal(out[~mask],im[~mask])
    assert np.array_equal(out[top['y'],top['x']],replacement[22,0])  # No false rounded corner along the cut.
    assert np.array_equal(out[top['y']+17,top['x']],im[top['y']+17,top['x']])  # Real bottom corner remains round.


def test_top_crop_survives_api_render_and_cache_restore():
    client=TestClient(app)
    im=clipped_scene()
    replacement=np.full((40,40,3),[220,30,30],np.uint8);replacement[20:]=[30,60,220]
    response=client.post('/api/sessions',files=[('screenshots',('chat.png',png(im),'image/png')),('replacement',('avatar.png',png(replacement),'image/png'))])
    assert response.status_code==200,response.text
    task=response.json();sid=task['id'];item=task['images'][0];endpoint=f"/api/sessions/{sid}/images/{item['id']}/render"
    assert item['boxes'][0]['clip_top']==22
    edits=dict(boxes=item['boxes'],radius=item['radius'])
    output=client.post(endpoint,json=edits)
    assert output.status_code==200,output.text
    assert np.array_equal(decode(output.content)[60,370],replacement[22,20])
    sessions.clear()
    restored=client.get(f'/api/sessions/{sid}').json()['images'][0]
    assert restored['boxes'][0]['clip_top']==22
    assert client.post(endpoint,json=dict(boxes=restored['boxes'],radius=restored['radius'])).content==output.content
    bad=[dict(b) for b in restored['boxes']];bad[0]['clip_top']=30
    assert client.post(endpoint,json=dict(boxes=bad)).status_code==422


def test_upload_only_screenshots_default_avatar_and_render_radius():
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
        result=client.post(f'/api/sessions/{sid}/images/{iid}/render',json={'boxes':edits,'radius':6})
        assert result.status_code==200,result.text
        assert {b['radius'] for b in client.get(f'/api/sessions/{sid}').json()['images'][0]['boxes']}=={6}
        out=decode(result.content)
        assert np.array_equal(out[:,:340],im[:,:340])
        bad=client.post(f'/api/sessions/{sid}/images/{iid}/render',json={'boxes':edits,'radius':30})
        assert bad.status_code==422
    finally:client.delete(f'/api/sessions/{sid}')
