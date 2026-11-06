"""Known-avatar matching; no face recognition, network service or learned weights."""
import cv2
import numpy as np
from .imaging import cover, estimate_radius


def visible_height(im: np.ndarray, source: np.ndarray, b: dict) -> int:
    x,y,w,h=(b[k] for k in ('x','y','w','h'))
    vh=min(h,im.shape[0]-y)
    p=max(2,w//5)
    observed=im[y:y+vh,x+p:x+w-p].astype(float)
    expected=cover(source,w,h)[:vh,p:w-p].astype(float)
    errors=np.abs(observed-expected).mean((1,2))
    baseline=float(np.median(errors[:max(1,int(vh*.45))]))
    cutoff=max(35.,baseline*3.5)
    for row in range(max(8,round(h*.35)),vh-2):
        if np.all(errors[row:row+3]>cutoff):
            return row
    return vh


def overlap(a: dict, b: dict) -> float:
    inter=max(0,min(a['x']+a['w'],b['x']+b['w'])-max(a['x'],b['x']))*max(0,min(a['y']+a['h'],b['y']+b['h'])-max(a['y'],b['y']))
    return inter/max(1,min(a['w']*a['h'],b['w']*b['h']))


def detect(im: np.ndarray, source: np.ndarray, threshold: float=.78, min_size: int=24, max_size: int=220) -> list[dict]:
    H,W=im.shape[:2]
    max_size=min(max_size,H,W)
    if min_size>max_size:
        return []
    factor=min(1.,640/W)
    small=cv2.resize(im,None,fx=factor,fy=factor,interpolation=cv2.INTER_AREA)
    gray=cv2.cvtColor(small,cv2.COLOR_RGB2GRAY)
    candidates=[]
    sizes=np.unique(np.rint(np.geomspace(min_size,max_size,max(2,int(np.log(max_size/min_size)/np.log(1.10))+1))).astype(int))
    for size in sizes:
        s=max(12,round(size*factor)); pad=max(2,round(s*.12))
        t=cv2.cvtColor(cover(source,s,s),cv2.COLOR_RGB2GRAY)[pad:s-pad,pad:s-pad]
        if t.std()<5:
            continue
        result=cv2.matchTemplate(gray,t,cv2.TM_CCOEFF_NORMED)
        for _ in range(100):
            _,score,_,(px,py)=cv2.minMaxLoc(result)
            if score < max(.55,threshold-.15):
                break
            x,y=round((px-pad)/factor),round((py-pad)/factor)
            if x>=0 and y>=0 and x+size<=W and y+size<=H:
                candidates.append(dict(x=x,y=y,w=int(size),h=int(size),score=float(score)))
            r=max(4,s//2)
            result[max(0,py-r):py+r+1,max(0,px-r):px+r+1]=-1
    candidates.sort(key=lambda b:-b['score'])
    seeds=[]
    for b in candidates:
        if all(overlap(b,k)<.35 for k in seeds):
            seeds.append(b)
    full=cv2.cvtColor(im,cv2.COLOR_RGB2GRAY)
    refined=[]
    for b in seeds[:100]:
        best=None
        for s in range(max(min_size,round(b['w']*.88)),min(max_size,round(b['w']*1.12))+1):
            pad=max(2,round(s*.12))
            t=cv2.cvtColor(cover(source,s,s),cv2.COLOR_RGB2GRAY)[pad:s-pad,pad:s-pad]
            delta=max(5,round(s*.15))
            left=max(0,b['x']-delta+pad); top=max(0,b['y']-delta+pad)
            right=min(W,b['x']+delta+s-pad); bottom=min(H,b['y']+delta+s-pad)
            region=full[top:bottom,left:right]
            if region.shape[0]<t.shape[0] or region.shape[1]<t.shape[1]:
                continue
            result=cv2.matchTemplate(region,t,cv2.TM_CCOEFF_NORMED)
            _,score,_,(px,py)=cv2.minMaxLoc(result)
            x,y=left+px-pad,top+py-pad
            if x<0 or y<0 or x+s>W or y+s>H:
                continue
            if best is None or score>best['score']:
                best=dict(x=x,y=y,w=s,h=s,score=float(score))
        if best and best['score']>=threshold:
            # Reject similarly shaped but differently colored content.
            x,y,s=best['x'],best['y'],best['w']; p=max(2,round(s*.12))
            observed=im[y+p:y+s-p,x+p:x+s-p].astype(float)
            expected=cover(source,s,s)[p:s-p,p:s-p].astype(float)
            mae=float(np.mean(np.abs(observed-expected)))
            if mae>48:
                continue
            best['score']=round(best['score'],4)
            best['color_mae']=round(mae,2)
            best['enabled']=True
            best['method']='multiscale-ncc'
            refined.append(best)
    kept=[]
    for b in sorted(refined,key=lambda b:-b['score']):
        if all(overlap(b,k)<.35 for k in kept):
            kept.append(b)
    # Repeated avatars teach us scales present in this screenshot. Match their
    # upper portion to recover copies clipped by the screenshot or composer.
    reliable=[b for b in kept if b['score']>=.95]
    scales=[]
    for b in sorted(reliable,key=lambda b:b['w']):
        if not scales or abs(b['w']-scales[-1])>4:scales.append(b['w'])
    partials=[]
    for s0 in scales:
        for s in range(max(min_size,s0-1),min(max_size,s0+1)+1):
            p=max(2,round(s*.12)); end=round(s*.43)
            t=cv2.cvtColor(cover(source,s,s),cv2.COLOR_RGB2GRAY)[p:end,p:s-p]
            if t.std()<5:continue
            result=cv2.matchTemplate(full,t,cv2.TM_CCOEFF_NORMED)
            for _ in range(100):
                _,score,_,(px,py)=cv2.minMaxLoc(result)
                if score<max(.9,threshold):break
                x,y=px-p,py-p
                result[max(0,py-s//2):py+s//2+1,max(0,px-s//2):px+s//2+1]=-1
                if x<0 or y<0 or x+s>W:continue
                candidate=dict(x=x,y=y,w=s,h=s,score=round(float(score),4),enabled=True,method='partial-ncc')
                if any(overlap(candidate,b)>.35 for b in reliable):continue
                observed=im[y+p:y+end,x+p:x+s-p].astype(float)
                expected=cover(source,s,s)[p:end,p:s-p].astype(float)
                mae=float(np.abs(observed-expected).mean())
                if mae>30:continue
                candidate['color_mae']=round(mae,2)
                partials.append(candidate)
    for b in sorted(partials,key=lambda b:-b['score']):
        if any(overlap(b,k)>.35 and k['score']>=.95 for k in kept):continue
        kept=[k for k in kept if overlap(b,k)<=.35]
        kept.append(b)
    for b in kept:
        vh=visible_height(im,source,b)
        if vh<b['h']:b['clip_h']=vh
        b['radius'],b['edge_quality']=estimate_radius(im,source,b)
    return sorted(kept,key=lambda b:(b['y'],b['x']))
