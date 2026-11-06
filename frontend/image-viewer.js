const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));

// All positions are local CSS pixels. Scaling around a focal point preserves
// the image pixel beneath the fingers/cursor, even while the centroid moves.
export class Viewport {
  constructor(imageWidth,imageHeight,width,height){
    this.imageWidth=imageWidth;this.imageHeight=imageHeight;
    this.width=width;this.height=height;this.padding=12;
    this.fit();
  }
  get minimum(){return Math.min(1,Math.max(1,this.width-2*this.padding)/this.imageWidth,Math.max(1,this.height-2*this.padding)/this.imageHeight);}
  get maximum(){return Math.max(2,this.minimum*12);}
  fit(){this.scale=this.minimum;this.x=(this.width-this.imageWidth*this.scale)/2;this.y=(this.height-this.imageHeight*this.scale)/2;}
  constrain(){
    const w=this.imageWidth*this.scale,h=this.imageHeight*this.scale,p=this.padding;
    this.x=w<=this.width-2*p?(this.width-w)/2:clamp(this.x,this.width-w-p,p);
    this.y=h<=this.height-2*p?(this.height-h)/2:clamp(this.y,this.height-h-p,p);
  }
  transformAt(from,to,factor){
    const ix=(from.x-this.x)/this.scale,iy=(from.y-this.y)/this.scale;
    this.scale=clamp(this.scale*factor,this.minimum,this.maximum);
    this.x=to.x-ix*this.scale;this.y=to.y-iy*this.scale;
    this.constrain();
  }
  zoomAt(point,factor){this.transformAt(point,point,factor);}
  pan(dx,dy){this.x+=dx;this.y+=dy;this.constrain();}
  resize(width,height){
    if(width===this.width&&height===this.height)return;
    const ix=(this.width/2-this.x)/this.scale,iy=(this.height/2-this.y)/this.scale;
    const relative=this.scale/this.minimum;
    this.width=width;this.height=height;
    this.scale=clamp(this.minimum*relative,this.minimum,this.maximum);
    this.x=width/2-ix*this.scale;this.y=height/2-iy*this.scale;this.constrain();
  }
}

export function gesture(points){
  const [a,b]=points;
  if(!a)return null;
  return b?{x:(a.x+b.x)/2,y:(a.y+b.y)/2,distance:Math.hypot(b.x-a.x,b.y-a.y)}:{...a,distance:0};
}

export class ImageViewer {
  constructor(element,stage){
    this.element=element;this.stage=stage;this.points=new Map();this.model=null;
    element.addEventListener('wheel',event=>{
      if(!this.model)return;
      event.preventDefault();
      const unit=event.deltaMode===1?16:event.deltaMode===2?this.model.height:1;
      this.model.zoomAt(this.local(event),Math.exp(-clamp(event.deltaY*unit,-240,240)*.0025));this.paint();
    },{passive:false});
    element.addEventListener('pointerdown',event=>{
      if(!this.model||(event.pointerType==='mouse'&&event.button!==0))return;
      event.preventDefault();this.points.set(event.pointerId,this.local(event));
      element.setPointerCapture(event.pointerId);element.classList.add('dragging');
    });
    element.addEventListener('pointermove',event=>{
      if(!this.model||!this.points.has(event.pointerId))return;
      const before=gesture([...this.points.values()]);
      this.points.set(event.pointerId,this.local(event));
      const after=gesture([...this.points.values()]);
      this.model.transformAt(before,after,before.distance>0&&after.distance>0?after.distance/before.distance:1);this.paint();
    });
    const end=event=>{this.points.delete(event.pointerId);if(element.hasPointerCapture(event.pointerId))element.releasePointerCapture(event.pointerId);if(!this.points.size)element.classList.remove('dragging');};
    for(const name of ['pointerup','pointercancel','lostpointercapture'])element.addEventListener(name,end);
    element.addEventListener('keydown',event=>{
      if(!this.model)return;
      const center={x:this.model.width/2,y:this.model.height/2};
      if(['+','='].includes(event.key))this.model.zoomAt(center,1.3);
      else if(event.key==='-')this.model.zoomAt(center,1/1.3);
      else if(event.key==='0')this.model.fit();
      else if(event.key==='ArrowLeft')this.model.pan(40,0);
      else if(event.key==='ArrowRight')this.model.pan(-40,0);
      else if(event.key==='ArrowUp')this.model.pan(0,40);
      else if(event.key==='ArrowDown')this.model.pan(0,-40);
      else return;
      event.preventDefault();this.paint();
    });
    this.observer=new ResizeObserver(()=>{
      if(!this.model||!element.clientWidth||!element.clientHeight)return;
      this.model.resize(element.clientWidth,element.clientHeight);this.paint();
    });
    this.observer.observe(element);
  }
  local(event){const rect=this.element.getBoundingClientRect();return{x:event.clientX-rect.left-this.element.clientLeft,y:event.clientY-rect.top-this.element.clientTop};}
  open(width,height){
    this.close();this.model=new Viewport(width,height,this.element.clientWidth,this.element.clientHeight);
    this.stage.style.width=`${width}px`;this.stage.style.height=`${height}px`;this.paint();
  }
  close(){
    for(const id of this.points.keys())if(this.element.hasPointerCapture(id))this.element.releasePointerCapture(id);
    this.points.clear();this.model=null;this.element.classList.remove('dragging');
  }
  paint(){if(!this.model)return;const{x,y,scale}=this.model;this.stage.style.transform=`translate3d(${x}px, ${y}px, 0) scale(${scale})`;}
}
