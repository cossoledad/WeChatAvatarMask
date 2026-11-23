import numpy as np
from fastapi.testclient import TestClient

import backend.main as api
from backend.imaging import png,decode
from backend.storage import Storage


def test_avatar_library_default_task_and_edits_survive_restart():
    client=TestClient(api.app)
    colors=[np.full((32,32,3),value,np.uint8) for value in (40,170)]
    ids=[]
    for index,image in enumerate(colors):
        result=client.post('/api/avatars',files={'replacement':(f'{index}.png',png(image),'image/png')})
        assert result.status_code==200,result.text
        ids.append(result.json()['id'])
    # Duplicate uploads are retained once, not duplicated in the chooser.
    duplicate=client.post('/api/avatars',files={'replacement':('again.png',png(colors[1]),'image/png')})
    assert duplicate.json()['id']==ids[1]
    assert len(client.get('/api/avatars').json()['items'])==3
    assert client.post(f'/api/avatars/{ids[0]}/select',json={}).status_code==200
    image=np.full((100,140,3),237,np.uint8)
    task=client.post('/api/sessions',files={'screenshots':('chat.png',png(image),'image/png')}).json()
    sid=task['id'];iid=task['images'][0]['id']
    assert task['replacement_id']==ids[0]
    boxes=[dict(x=90,y=20,w=30,h=30,radius=3)]
    endpoint=f'/api/sessions/{sid}/images/{iid}/render'
    assert client.post(endpoint,json={'boxes':boxes,'radius':4.3,'revision':2}).status_code==200
    # An older in-flight preview must not overwrite the latest radius.
    assert client.post(endpoint,json={'boxes':boxes,'radius':2,'revision':1}).status_code==200
    api.sessions.clear()
    api.storage=Storage(api.storage.directory)
    restored=client.get(f'/api/sessions/{sid}').json()['images'][0]
    assert restored['radius']==4.3 and restored['revision']==2
    assert client.get('/api/avatars').json()['selected']==ids[0]
    assert np.array_equal(decode(client.get('/api/default-avatar').content),colors[0])
    assert client.post(f'/api/avatars/{ids[1]}/select',json={'session_id':sid}).status_code==200
    api.sessions.clear()
    assert client.get(f'/api/sessions/{sid}').json()['replacement_id']==ids[1]
    out=decode(client.post(endpoint,json={'boxes':restored['boxes'],'radius':4.3,'revision':2}).content)
    assert np.array_equal(out[35,105],colors[1][0,0])
    assert client.delete(f'/api/sessions/{sid}').status_code==200
    api.sessions.clear()
    assert client.get(f'/api/sessions/{sid}').status_code==404
    assert len(client.get('/api/avatars').json()['items'])==3
    assert client.get('/api/avatars').json()['selected']==ids[1]
