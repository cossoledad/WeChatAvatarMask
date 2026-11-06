"""Source-free detection of the right avatar column in WeChat-style screenshots.

Uses UI geometry, background separation and repeated column alignment. It does
not compare to a bundled source portrait or infer the identity of a face.
"""
import cv2
import numpy as np

from .imaging import background, coverage


def _runs(mask):
    edges=np.diff(np.r_[False,mask,False].astype(np.int8))
    return list(zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)))


def _background(im):
    pixels=im[::5,::5].reshape(-1,3)
    quantized=pixels//8
    colors,counts=np.unique(quantized,axis=0,return_counts=True)
    winner=colors[counts.argmax()]
    return np.median(pixels[np.all(quantized==winner,axis=1)],axis=0)


def _edge(im, axis, guess, lo, hi, limit=4):
    """Locate the strongest straight boundary, using its middle portion."""
    H,W=im.shape[:2]
    values=[]
    for pos in range(max(1,guess-limit),min((W if axis==0 else H)-1,guess+limit)+1):
        if axis==0:
            a=im[max(0,lo):min(H,hi),pos-1].astype(float)
            b=im[max(0,lo):min(H,hi),pos].astype(float)
        else:
            a=im[pos-1,max(0,lo):min(W,hi)].astype(float)
            b=im[pos,max(0,lo):min(W,hi)].astype(float)
        if len(a):values.append((float(np.median(np.linalg.norm(a-b,axis=1))),pos))
    return max(values)[1] if values else guess


def geometric_radius(im,box):
    x,y,w,h=[box[k] for k in ('x','y','w','h')]
    vh=min(box.get('clip_h') or h,im.shape[0]-y)
    bg,noise=background(im,x,y,w,vh)
    observed=im[y:y+vh,x:x+w].astype(float)
    distance=np.linalg.norm(observed-bg,axis=2)
    yy,xx=np.mgrid[:vh,:w]
    corners=(np.minimum(xx,w-1-xx)<w*.25)&(np.minimum(yy,h-1-yy)<h*.25)
    opaque=distance>max(22.,noise*3)
    k=max(2,round(min(w,h)*.25))
    for rows,cols in [(slice(0,k),slice(0,k)),(slice(0,k),slice(w-k,w)),
                      (slice(max(0,h-k),vh),slice(0,k)),(slice(max(0,h-k),vh),slice(w-k,w))]:
        local=distance[rows,cols]
        if local.size:
            threshold=max(22.,noise*3,float(np.percentile(local,80))*.25)
            opaque[rows,cols]=local>threshold
    choices=[]
    for r in np.arange(0,min(w,h)*.25+.25,.5):
        predicted=coverage(w,h,float(r))[:vh]>.5
        error=float(np.mean(predicted[corners]!=opaque[corners]))
        choices.append((error,float(r)))
    err,r=min(choices)
    return r,round(max(0,1-err*3)*max(0,1-noise/30),3)


def shared_radius(boxes):
    if not boxes:return 0.
    complete=[b for b in boxes if not b.get('clip_h') and b.get('edge_quality',0)>=.4]
    pool=complete or boxes
    r=float(np.median([b['radius'] for b in pool]))
    return min(round(r*2)/2,min(min(b['w'],b['h'])/2 for b in boxes))


def detect_right(im):
    H,W=im.shape[:2]
    bg=_background(im)
    # Reject textured wallpaper rather than treating arbitrary right-side photos
    # as known UI. Such images can be corrected manually in the review sheet.
    fg=(np.linalg.norm(im.astype(np.float32)-bg,axis=2)>26).astype(np.uint8)
    fg[:,:int(W*.78)]=0
    fg[:,int(W*.992):]=0
    closed=cv2.morphologyEx(fg,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    _,_,stats,_=cv2.connectedComponentsWithStats(closed)
    seeds=[]
    for x,y,w,h,area in stats[1:]:
        if not (.045*W<=w<=.145*W and .75<=h/w<=1.2 and .008*W<=W-x-w<=.08*W):continue
        fill=area/(w*h)
        if fill<.74:continue
        patch=im[y:y+h,x:x+w].astype(float)
        if patch.std()<10:continue
        seeds.append(dict(x=int(x),y=int(y),w=int(w),h=int(h),score=min(.98,float(fill))))
    if not seeds:return []
    # Select the most consistently repeated rightmost square column. Different
    # avatar content is allowed; matching identity is deliberately unnecessary.
    def support(seed):
        return sum(abs(b['x']-seed['x'])<W*.012 and abs(b['w']-seed['w'])<seed['w']*.15 for b in seeds)
    anchor=max(seeds,key=lambda b:(support(b),b['x']+b['w']))
    family=[b for b in seeds if abs(b['x']-anchor['x'])<W*.016 and abs(b['w']-anchor['w'])<anchor['w']*.18]
    rough_size=round(float(np.median([b['w'] for b in family])))
    rough_x=round(float(np.median([b['x'] for b in family])))
    # Row occupancy recovers partial avatars even when JPEG artifacts connect
    # them to a bubble, toolbar, or neighboring image outside the avatar column.
    pad=max(3,round(rough_size*.2))
    occupied=closed[:,rough_x+pad:rough_x+rough_size-pad].mean(axis=1)>.67
    # Close only 1–2 px gaps, preserving the larger gap between messages.
    occupied=cv2.morphologyEx(occupied.astype(np.uint8)[:,None],cv2.MORPH_CLOSE,np.ones((3,1),np.uint8)).ravel()>0
    candidates=[]
    for start,end in _runs(occupied):
        length=end-start
        if length<rough_size*.32 or length>rough_size*1.24:continue
        x=_edge(im,0,rough_x,start+max(2,length//4),start+max(3,3*length//4))
        right=_edge(im,0,rough_x+rough_size,start+max(2,length//4),start+max(3,3*length//4))
        size=right-x
        if abs(size-rough_size)>max(5,rough_size*.1):continue
        y=0 if start==0 else _edge(im,1,int(start),x+pad,right-pad)
        if length>rough_size*.8 and end<H:
            bottom=_edge(im,1,int(end),x+pad,right-pad)
            if abs((bottom-y)-size)<=max(4,size*.06):
                size=round((size+bottom-y)/2)
        vh=min(size,H-y)
        if end-start<size*.85:
            vh=min(vh,int(end)-y)
            # Preserve the horizontal composer boundary; occupancy may include a
            # few mixed rows, so take the strongest transition near its end.
            if end<H:vh=min(vh,_edge(im,1,int(end),x+pad,min(W,x+size-pad),limit=3)-y)
        if size<12 or vh<8 or x<0 or y<0 or x+size>W:continue
        # A pure outline UI icon has very low filled area inside its square.
        fill=float(fg[y:y+vh,x:x+size].mean())
        if fill<.65:continue
        b=dict(x=int(x),y=int(y),w=int(size),h=int(size),score=round(min(.98,fill),3),enabled=True,method='right-layout')
        if vh<size:b['clip_h']=int(vh)
        b['radius'],b['edge_quality']=geometric_radius(im,b)
        candidates.append(b)
    radius=shared_radius(candidates)
    for b in candidates:b['radius']=radius
    return candidates
