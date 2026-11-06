"""Image IO, rounded-rectangle coverage and local, lossless compositing."""
from io import BytesIO
import warnings

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_PIXELS = 16_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def decode(data: bytes) -> np.ndarray:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as im:
                if im.format not in {'JPEG', 'PNG', 'WEBP'}:
                    raise ValueError('仅支持 JPEG、PNG 和 WebP')
                if im.width * im.height > MAX_PIXELS:
                    raise ValueError('单张图片不能超过 1600 万像素')
                # Flatten transparency on white, apply phone orientation, discard metadata.
                rgba = ImageOps.exif_transpose(im).convert('RGBA')
                flat = Image.new('RGBA', rgba.size, 'white')
                flat.alpha_composite(rgba)
                return np.asarray(flat.convert('RGB')).copy()
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('无法读取图片，文件损坏或尺寸过大') from exc


def png(im: np.ndarray) -> bytes:
    out = BytesIO()
    Image.fromarray(im).save(out, format='PNG')
    return out.getvalue()


def cover(im: np.ndarray, w: int, h: int) -> np.ndarray:
    ih, iw = im.shape[:2]
    scale = max(w / iw, h / ih)
    nw, nh = max(w, round(iw * scale)), max(h, round(ih * scale))
    resized = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    x, y = (nw - w) // 2, (nh - h) // 2
    return resized[y:y+h, x:x+w]


def coverage(w: int, h: int, radius: float, samples: int = 32) -> np.ndarray:
    """Integrate pixel area: exact horizontal span, sampled vertical arc.

    Unlike a 4x4 binary grid, edge opacity is not restricted to 16 steps.
    Keep memory proportional to the output rather than a supersized bitmap.
    """
    radius=min(max(0.,radius),min(w,h)/2)
    if radius==0:return np.ones((h,w),np.float32)
    result=np.zeros((h,w),np.float32)
    xx=np.arange(w,dtype=np.float64)[None,:]
    yy=np.arange(h,dtype=np.float64)[:,None]
    for offset in (np.arange(samples)+.5)/samples:
        y=yy+offset
        distance=np.maximum(np.maximum(radius-y,y-(h-radius)),0)
        inset=radius-np.sqrt(np.maximum(radius*radius-distance*distance,0))
        span=np.minimum(xx+1,w-inset)-np.maximum(xx,inset)
        result+=np.clip(span,0,1).astype(np.float32)
    result/=samples
    result=(result+result[:,::-1])*.5
    return (result+result[::-1,:])*.5


def background(im: np.ndarray, x: int, y: int, w: int, h: int) -> tuple[np.ndarray, float]:
    H, W = im.shape[:2]
    values = []
    # Local outside strips; robust against nearby message bubbles.
    for a, b, c, d in [(x-3,y-3,x+w+3,y),(x-3,y+h,x+w+3,y+h+3),
                       (x-3,y,x,y+h),(x+w,y,x+w+3,y+h)]:
        patch = im[max(0,b):min(H,d), max(0,a):min(W,c)]
        if patch.size:
            values.append(patch.reshape(-1,3))
    if not values:
        return np.array([237.,237.,237.], np.float32), 255.
    values = np.concatenate(values).astype(np.float32)
    bg = np.median(values, axis=0)
    return bg, float(np.median(np.linalg.norm(values-bg, axis=1)))


def edge_background(im: np.ndarray,x:int,y:int,w:int,h:int,visible_h:int,radius:float) -> np.ndarray:
    """Estimate each corner from outside strips, avoiding the old avatar.

    A clipped avatar's bottom samples stay above the composer. Separate corner
    estimates also support gently varying backgrounds instead of one flat RGB.
    """
    H,W=im.shape[:2]
    base,noise=background(im,x,y,w,visible_h)
    reach=max(4,min(24,round(radius)+3))
    colors=[]
    for lower,right in [(False,False),(False,True),(True,False),(True,True)]:
        gx=x+w if right else x
        gy=y+visible_h if lower else y
        cols=(gx-reach,gx+3) if right else (gx-3,gx+reach)
        rows=(max(y,gy-reach),gy) if lower else (gy,gy+reach)
        outside_cols=(gx+2,gx+6) if right else (gx-6,gx-2)
        rects=[(outside_cols[0],rows[0],outside_cols[1],rows[1])]
        if not lower or visible_h==h:
            outside_rows=(gy+2,gy+6) if lower else (gy-6,gy-2)
            rects.append((cols[0],outside_rows[0],cols[1],outside_rows[1]))
        values=[]
        for left,top,right_edge,bottom in rects:
            left,right_edge=max(0,min(W,left)),max(0,min(W,right_edge))
            top,bottom=max(0,min(H,top)),max(0,min(H,bottom))
            if right_edge<=left or bottom<=top:continue
            patch=im[top:bottom,left:right_edge]
            if patch.size:values.append(patch.reshape(-1,3))
        if not values:colors.append(base);continue
        values=np.concatenate(values).astype(np.float32)
        compatible=values[np.linalg.norm(values-base,axis=1)<=max(24.,noise*3)]
        if len(compatible)>=max(3,len(values)//4):values=compatible
        colors.append(np.median(values,axis=0))
    tl,tr,bl,br=colors
    horizontal=np.linspace(0,1,w,dtype=np.float32)[None,:,None]
    vertical=np.linspace(0,1,visible_h,dtype=np.float32)[:,None,None]
    return (tl*(1-horizontal)+tr*horizontal)*(1-vertical)+(bl*(1-horizontal)+br*horizontal)*vertical


def estimate_radius(im: np.ndarray, source: np.ndarray, box: dict) -> tuple[float, float]:
    x,y,w,h = (int(box[k]) for k in ('x','y','w','h'))
    vh=min(h,int(box.get('clip_h') or h),im.shape[0]-y)
    observed = im[y:y+vh,x:x+w].astype(np.float32)
    old = cover(source,w,h)[:vh].astype(np.float32)
    bg, noise = background(im,x,y,w,vh)
    # Calibrate colors on the opaque core (JPEG/resampling changes color slightly).
    core = (slice(vh//4,3*vh//4), slice(w//4,3*w//4))
    offset = np.median(observed[core]-old[core],axis=(0,1))
    old = np.clip(old+offset,0,255)
    yy,xx = np.mgrid[:vh,:w]
    corners = (np.minimum(xx,w-1-xx)<w*.24)&(np.minimum(yy,h-1-yy)<h*.24)
    scores=[]
    for r in np.arange(0, min(w,h)*.3+.25, .5):
        a=coverage(w,h,float(r),samples=4)[:vh]
        expected=old*a[...,None]+bg*(1-a[...,None])
        err=np.mean(np.minimum(np.abs(expected-observed)[corners],60))
        scores.append((float(err),float(r)))
    err,r=min(scores)
    quality=max(0.,1.-err/30.) * max(0.,1.-noise/30.)
    return r,round(quality,3)


def render(im: np.ndarray, source: np.ndarray, replacement: np.ndarray, boxes: list[dict]) -> np.ndarray:
    out = im.copy()
    for b in boxes:
        if not b.get('enabled', True):
            continue
        x,y,w,h = (int(b[k]) for k in ('x','y','w','h'))
        radius = float(b.get('radius', min(w,h)*.1))
        vh=min(h,int(b.get('clip_h') or h),im.shape[0]-y)
        a=coverage(w,h,radius)[:vh]
        new=cover(replacement,w,h)[:vh].astype(np.float32)
        # Never alpha-over the original avatar: even a small original-color
        # contribution becomes a visible fringe when magnified.
        bg=edge_background(im,x,y,w,h,vh,radius)
        mixed=new*a[...,None]+bg*(1-a[...,None])
        patch=out[y:y+vh,x:x+w]
        changed=a>0
        patch[changed]=np.clip(np.rint(mixed[changed]),0,255).astype(np.uint8)
    return out
