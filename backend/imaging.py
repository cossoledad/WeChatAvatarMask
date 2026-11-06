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


def coverage(w: int, h: int, radius: float, samples: int = 4) -> np.ndarray:
    """Area coverage, with box edges aligned to pixel edges (no feathered interiors)."""
    radius = min(max(0., radius), min(w, h) / 2)
    yy, xx = np.mgrid[:h*samples, :w*samples].astype(np.float32)
    xx = (xx + .5) / samples
    yy = (yy + .5) / samples
    dx = np.maximum(np.maximum(radius - xx, xx - (w - radius)), 0)
    dy = np.maximum(np.maximum(radius - yy, yy - (h - radius)), 0)
    return ((dx*dx + dy*dy) <= radius*radius).reshape(h, samples, w, samples).mean((1, 3)).astype(np.float32)


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
        a=coverage(w,h,float(r))[:vh]
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
        old_patch=im[y:y+vh,x:x+w].astype(np.float32)
        bg,noise=background(im,x,y,w,vh)
        # At antialias pixels, composite against estimated background rather than
        # the old avatar: alpha-over-old would leave the original face as a fringe.
        if noise <= 8:
            mixed=new*a[...,None]+bg*(1-a[...,None])
        else:
            mixed=new*a[...,None]+old_patch*(1-a[...,None])
        patch=out[y:y+vh,x:x+w]
        changed=a>0
        patch[changed]=np.clip(np.rint(mixed[changed]),0,255).astype(np.uint8)
    return out
