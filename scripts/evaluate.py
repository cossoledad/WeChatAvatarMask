"""Run the local dataset, write PNGs, detection overlays and an audit JSON."""
import json
from pathlib import Path
import sys
import time
import zipfile
import argparse

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps
from backend.imaging import decode,png,render,coverage
from backend.detection import detect
from backend.layout import detect_right, shared_radius

ROOT=Path(__file__).resolve().parents[1]
SOURCE='D579F7B0CC5034C39F4CE4847719A95D.jpg'
REPLACEMENT='F5AC4F279953D1A7B46643AE05353084.jpg'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--template',action='store_true');args=parser.parse_args()
    mode='template' if args.template else 'right'
    data=ROOT/'test.data'; out=ROOT/'artifacts'/('evaluation-'+mode); out.mkdir(parents=True,exist_ok=True)
    source=decode((data/SOURCE).read_bytes()) if args.template else None
    replacement=decode((ROOT/'backend/assets/default-avatar.jpg').read_bytes())
    report=[]; thumbs=[]; details=[]
    for p in sorted(data.glob('*.jpg')):
        if p.name in {SOURCE,REPLACEMENT}: continue
        start=time.perf_counter(); im=decode(p.read_bytes()); boxes=detect(im,source) if args.template else detect_right(im)
        radius=shared_radius(boxes)
        for b in boxes:b['radius']=radius
        result=render(im,source,replacement,boxes)
        (out/(p.stem+'.png')).write_bytes(png(result))
        mask=np.zeros(im.shape[:2],bool)
        overlay=Image.fromarray(im); draw=ImageDraw.Draw(overlay)
        for i,b in enumerate(boxes):
            x,y,w,h=[b[k] for k in ('x','y','w','h')]
            vh=b.get('clip_h',h)
            mask[y:y+vh,x:x+w] |= coverage(w,h,b['radius'])[:vh]>0
            draw.rectangle((x-2,y-2,x+w+1,y+vh+1),outline='#00a781',width=3)
            draw.text((x-85,y),f"{i+1}: {b['score']:.3f}",fill='#00745c')
            before=Image.fromarray(im).crop((x-5,y-5,x+w+5,y+h+5)).resize((240,240))
            after=Image.fromarray(result).crop((x-5,y-5,x+w+5,y+h+5)).resize((240,240))
            details.append((p.stem[:8],b,before,after))
        overlay.save(out/(p.stem+'-detections.png'))
        outside=int(np.count_nonzero(np.any(im!=result,axis=2)&~mask))
        entry=dict(file=p.name,count=len(boxes),radius=radius,uniform_radius=len({b['radius'] for b in boxes})<=1,seconds=round(time.perf_counter()-start,3),outside_mask_changed_pixels=outside,boxes=boxes)
        report.append(entry); print(p.stem[:10],len(boxes),entry['seconds'],flush=True)
        thumbs.append((p.stem[:10],overlay))
    sheet=Image.new('RGB',(1200,360*((len(thumbs)+5)//6)),'#e8eded');d=ImageDraw.Draw(sheet)
    for i,(name,im) in enumerate(thumbs):
        t=ImageOps.contain(im,(194,330));x=i%6*200;y=i//6*360
        sheet.paste(t,(x,y));d.text((x+3,y+334),name,fill='black')
    sheet.save(out/'detection-sheet.png')
    # First appearance of every screenshot, for visual edge audit.
    selected=[];seen=set()
    for item in details:
        if item[0] not in seen: selected.append(item);seen.add(item[0])
    sheet=Image.new('RGB',(1000,280*((len(selected)+1)//2)),'#edf0f2');d=ImageDraw.Draw(sheet)
    for i,(name,b,before,after) in enumerate(selected):
        x=i%2*500;y=i//2*280;sheet.paste(before,(x,y));sheet.paste(after,(x+245,y))
        d.text((x,y+244),f"{name}  {b['w']}px  r={b['radius']}  edge={b['edge_quality']}",fill='black')
    sheet.save(out/'edge-audit.png')
    summary=dict(mode=mode,images=len(report),detections=sum(r['count'] for r in report),all_images_uniform_radius=all(r['uniform_radius'] for r in report),total_seconds=round(sum(r['seconds'] for r in report),2),outside_mask_changed_pixels=sum(r['outside_mask_changed_pixels'] for r in report),results=report)
    (out/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    with zipfile.ZipFile(ROOT/'artifacts'/('avatar-mask-'+mode+'-results.zip'),'w',zipfile.ZIP_DEFLATED) as z:
        for entry in report:
            p=out/(Path(entry['file']).stem+'.png')
            z.write(p,p.name)
        z.write(out/'report.json','report.json')
    print(json.dumps({k:v for k,v in summary.items() if k!='results'}),flush=True)

if __name__=='__main__': main()
