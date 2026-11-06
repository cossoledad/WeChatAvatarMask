import numpy as np
import pytest
from PIL import Image
from io import BytesIO
from backend.imaging import coverage,cover,decode,png,render,estimate_radius
from backend.detection import detect


@pytest.fixture
def scene():
    rng=np.random.default_rng(42)
    source=rng.integers(0,256,(80,80,3),dtype=np.uint8)
    # Structured features survive resizing and distinguish nearby scales.
    import cv2
    source=cv2.GaussianBlur(source,(5,5),0)
    source[10:30,20:40]=[20,30,190];source[35:65,30:50]=[200,40,30]
    im=np.full((420,360,3),237,np.uint8)
    boxes=[dict(x=25,y=30,w=64,h=64,radius=8),dict(x=260,y=190,w=48,h=48,radius=5)]
    for b in boxes:
        x,y,w,h=(b[k] for k in ('x','y','w','h'))
        a=coverage(w,h,b['radius'])[...,None]
        im[y:y+h,x:x+w]=np.rint(cover(source,w,h)*a+237*(1-a)).astype(np.uint8)
    return source,im,boxes


def test_multiscale_and_negative(scene):
    source,im,truth=scene
    found=detect(im,source,min_size=24,max_size=90)
    assert len(found)==2
    for actual,expected in zip(found,truth):
        for key in ('x','y','w','h'):assert abs(actual[key]-expected[key])<=1
        assert abs(actual['radius']-expected['radius'])<=1
    assert detect(np.full_like(im,237),source,min_size=24,max_size=90)==[]


def test_pixels_outside_mask_unchanged_and_no_old_fringe(scene):
    source,im,boxes=scene
    new=np.full((30,30,3),[20,170,100],np.uint8)
    out=render(im,source,new,boxes)
    mask=np.zeros(im.shape[:2],bool)
    for b in boxes:
        x,y,w,h=(b[k] for k in ('x','y','w','h'))
        a=coverage(w,h,b['radius'])
        mask[y:y+h,x:x+w]=a>0
        expected=np.rint(new[0,0]*a[...,None]+237*(1-a[...,None])).astype(np.uint8)
        changed=a>0
        assert np.array_equal(out[y:y+h,x:x+w][changed],expected[changed])
    assert np.array_equal(out[~mask],im[~mask])
    assert np.array_equal(decode(png(out)),out)


def test_disable_and_overlap_clipping(scene):
    source,im,boxes=scene
    assert np.array_equal(render(im,source,source,[dict(boxes[0],enabled=False)]),im)
    b=dict(boxes[0],clip_h=25)
    out=render(im,source,np.zeros((50,50,3),np.uint8),[b])
    assert np.array_equal(out[b['y']+25:],im[b['y']+25:])


def test_partial_recovers_screenshot_and_composer_clips(scene):
    source,im,_=scene
    b=dict(x=25,y=365,w=64,h=64,radius=8)
    # Avatar is hidden by a solid toolbar after 30 rows.
    a=coverage(64,64,8)[:30,...,None]
    im[365:395,25:89]=np.rint(cover(source,64,64)[:30]*a+237*(1-a)).astype(np.uint8)
    im[395:]=250
    found=detect(im,source,min_size=24,max_size=90)
    partial=[b for b in found if b['y']>300]
    assert len(partial)==1
    assert abs(partial[0]['clip_h']-30)<=1


def test_exif_orientation_and_metadata():
    im=Image.new('RGB',(40,20),'red');exif=Image.Exif();exif[274]=6
    buf=BytesIO();im.save(buf,'JPEG',exif=exif)
    decoded=decode(buf.getvalue());assert decoded.shape==(40,20,3)
    exported=Image.open(BytesIO(png(decoded)));assert not exported.getexif()


def test_bad_file():
    with pytest.raises(ValueError):decode(b'not an image')
