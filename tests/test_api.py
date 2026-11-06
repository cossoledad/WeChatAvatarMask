from io import BytesIO
import json
import zipfile
import numpy as np
from fastapi.testclient import TestClient
from backend.main import app,sessions
from backend.imaging import png,decode

client=TestClient(app)


def test_upload_manual_render_export_clear():
    im=np.full((150,200,3),237,np.uint8)
    src=np.random.default_rng(3).integers(0,256,(64,64,3),dtype=np.uint8)
    rep=np.full((80,80,3),[20,170,100],np.uint8)
    response=client.post('/api/sessions',files=[('source',('source.png',png(src),'image/png')),('replacement',('new.png',png(rep),'image/png')),('screenshots',('chat.png',png(im),'image/png'))])
    assert response.status_code==200,response.text
    data=response.json();sid=data['id'];iid=data['images'][0]['id']
    box=dict(x=20,y=30,w=40,h=40,radius=5)
    bad=client.post(f'/api/sessions/{sid}/images/{iid}/render',json={'boxes':[dict(box,x=190)]})
    assert bad.status_code==422
    r=client.post(f'/api/sessions/{sid}/images/{iid}/render',json={'boxes':[box]})
    assert r.status_code==200;out=decode(r.content);assert np.array_equal(out[40,30],rep[0,0])
    export=client.post(f'/api/sessions/{sid}/export',json={'edits':{iid:[box]}})
    assert export.status_code==200
    with zipfile.ZipFile(BytesIO(export.content)) as z:
        assert sorted(z.namelist())==['01-chat.png','audit.json']
        assert np.array_equal(decode(z.read('01-chat.png')),out)
        assert json.loads(z.read('audit.json'))['images'][0]['changed_pixels']>0
    assert client.delete(f'/api/sessions/{sid}').status_code==200
    assert client.get(f'/api/sessions/{sid}/images/{iid}').status_code==404


def test_invalid_upload_and_parameters():
    data=png(np.zeros((40,40,3),np.uint8))
    response=client.post('/api/sessions',files={'source':('bad.jpg',b'nope'),'replacement':('rep.png',data),'screenshots':('s.png',data)})
    assert response.status_code==422
    assert client.post('/api/sessions',files={'source':('s.png',data),'replacement':('r.png',data),'screenshots':('c.png',data)},data={'min_size':100,'max_size':30}).status_code==422


def test_cors_only_local_frontend():
    headers={'Origin':'http://127.0.0.1:5178','Access-Control-Request-Method':'POST'}
    assert client.options('/api/demo',headers=headers).headers['access-control-allow-origin']==headers['Origin']
    headers['Origin']='https://example.com'
    assert 'access-control-allow-origin' not in client.options('/api/demo',headers=headers).headers


def test_expiration():
    from backend.main import Session,TTL
    from time import monotonic
    sessions['expired']=Session(np.zeros((1,1,3),np.uint8),np.zeros((1,1,3),np.uint8),[],monotonic()-TTL-1)
    assert client.get('/api/sessions/expired/assets/source').status_code==404
