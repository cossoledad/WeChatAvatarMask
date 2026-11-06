import test from 'node:test';
import assert from 'node:assert/strict';
import {Viewport,gesture,ImageViewer} from './image-viewer.js';

const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-7,`${a} differs from ${b}`);
const pixel=(view,point)=>({x:(point.x-view.x)/view.scale,y:(point.y-view.y)/view.scale});

test('wheel-style zoom keeps the image point under the cursor stationary',()=>{
  const view=new Viewport(1200,1800,360,560);
  view.zoomAt({x:180,y:280},3);
  const cursor={x:210,y:350},before=pixel(view,cursor);
  view.zoomAt(cursor,1.4);
  const after=pixel(view,cursor);
  near(before.x,after.x);near(before.y,after.y);
});

test('two fingers zoom and translate around their moving midpoint',()=>{
  const view=new Viewport(1200,1800,360,560);
  view.zoomAt({x:180,y:280},3);
  const start=gesture([{x:110,y:250},{x:250,y:310}]);
  const end=gesture([{x:80,y:240},{x:300,y:340}]);
  const before=pixel(view,start);
  view.transformAt(start,end,end.distance/start.distance);
  const after=pixel(view,end);
  near(before.x,after.x);near(before.y,after.y);
});

test('repeated zoom-out returns to the fitted image without escaping its bounds',()=>{
  const view=new Viewport(1200,1800,360,560);
  const fit={scale:view.scale,x:view.x,y:view.y};
  view.zoomAt({x:250,y:420},1000);view.pan(100000,-100000);
  assert.equal(view.scale,view.maximum);
  assert.ok(view.x<=view.padding);
  assert.ok(view.y>=view.height-view.imageHeight*view.scale-view.padding);
  for(let i=0;i<20;i++)view.zoomAt({x:320,y:480},.3);
  near(view.scale,fit.scale);near(view.x,fit.x);near(view.y,fit.y);
});

test('orientation changes preserve relative zoom and do not crop a fitted image',()=>{
  const view=new Viewport(1200,1800,360,560);
  view.zoomAt({x:180,y:280},3);
  const relative=view.scale/view.minimum,centerPixel=pixel(view,{x:180,y:280});
  view.resize(700,400);
  near(view.scale/view.minimum,relative);
  const nextCenter=pixel(view,{x:350,y:200});
  near(nextCenter.x,centerPixel.x);near(nextCenter.y,centerPixel.y);
  view.fit();view.resize(360,560);
  assert.ok(view.imageWidth*view.scale<=view.width);
  assert.ok(view.imageHeight*view.scale<=view.height);
});

class Surface extends EventTarget {
  clientWidth=360;clientHeight=560;clientLeft=0;clientTop=0;captured=new Set();
  classList={add(){},remove(){}};
  getBoundingClientRect(){return{left:0,top:0};}
  setPointerCapture(id){this.captured.add(id);}
  hasPointerCapture(id){return this.captured.has(id);}
  releasePointerCapture(id){this.captured.delete(id);}
}
function send(surface,type,properties){const event=new Event(type,{cancelable:true});Object.assign(event,properties);surface.dispatchEvent(event);return event;}
globalThis.ResizeObserver=class{observe(){}};

test('actual wheel handler consumes scroll only inside an open viewer',()=>{
  const element=new Surface(),stage={style:{}},viewer=new ImageViewer(element,stage);
  const untouched=send(element,'wheel',{deltaY:-100,deltaMode:0,clientX:180,clientY:280});
  assert.equal(untouched.defaultPrevented,false);
  viewer.open(1200,1800);const fit=viewer.model.scale;
  const wheel=send(element,'wheel',{deltaY:-100,deltaMode:0,clientX:180,clientY:280});
  assert.equal(wheel.defaultPrevented,true);assert.ok(viewer.model.scale>fit);
  assert.match(stage.style.transform,/translate3d/);
});

test('pinch can become a one-finger drag without jumping, then releases capture',()=>{
  const element=new Surface(),viewer=new ImageViewer(element,{style:{}});viewer.open(1200,1800);
  const pointer=(id,x,y)=>({pointerId:id,pointerType:'touch',button:0,clientX:x,clientY:y});
  send(element,'pointerdown',pointer(1,130,280));send(element,'pointerdown',pointer(2,230,280));
  send(element,'pointermove',pointer(1,50,280));send(element,'pointermove',pointer(2,310,280));
  assert.ok(viewer.model.scale>viewer.model.minimum*2);
  send(element,'pointerup',pointer(2,310,280));
  const before=viewer.model.x;
  send(element,'pointermove',pointer(1,60,280));near(viewer.model.x-before,10);
  send(element,'pointercancel',pointer(1,60,280));assert.equal(viewer.points.size,0);
  send(element,'pointerdown',pointer(3,180,280));viewer.close();
  assert.equal(viewer.model,null);assert.equal(element.captured.size,0);
});
