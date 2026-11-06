const $=id=>document.getElementById(id);
let session=null,busy=false,current=0,selected=-1,editing=false,adding=false,zoomed=false,generation=0,timer;
let previewUrl=null;
const cardUrls=new Map();
const active=()=>session?.images[current];
const jsonPost=data=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
function notice(message,error=false){$('notice').textContent=message;$('notice').className=error?'error':'';$('notice').hidden=!message;}
async function request(path,options={}){
  let response;try{response=await fetch(path,options);}catch{throw new Error('本机服务暂未连接，请稍后重试。');}
  if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(typeof data.detail==='string'?data.detail:`处理失败（${response.status}），请重试。`);}
  return response;
}
function setBusy(value,processing=false){busy=value;for(const id of ['screenshots','demo','reset','saveAll','saveCurrent'])$(id).disabled=value;$('processing').hidden=!processing;$('dropzone').hidden=processing;}
function releaseUrls(){if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=null;for(const url of cardUrls.values())URL.revokeObjectURL(url);cardUrls.clear();generation++;clearTimeout(timer);}
async function clear(){
  if(session)await request(`/api/sessions/${session.id}`,{method:'DELETE'});
  session=null;releaseUrls();$('cards').replaceChildren();$('results').hidden=true;$('savebar').hidden=true;
  document.body.classList.remove('has-results');$('screenshots').value='';$('uploadTitle').textContent='选择聊天截图';
}
async function run(files,demo=false){
  if(busy)return;
  if(!demo){if(!files.length)return;if(files.length>30){notice('每次最多选择 30 张截图。',true);return;}if(files.some(f=>f.size>15*1024*1024)){notice('单张截图不能超过 15 MB。',true);return;}}
  setBusy(true,true);notice('');
  try{
    await clear();let body;
    if(!demo){body=new FormData();files.forEach(f=>body.append('screenshots',f));}
    session=await(await request(demo?'/api/demo':'/api/sessions',{method:'POST',...(body?{body}:{})})).json();
    if(!session.images.length)throw new Error('没有可以处理的截图。');
    for(const im of session.images){im.radius??=im.boxes[0]?.radius??0;im.boxes.forEach(b=>b.radius=im.radius);}
    document.body.classList.add('has-results');$('results').hidden=false;$('savebar').hidden=false;$('uploadTitle').textContent='选择另一批截图';
    updateSummary();drawCards();
    const missed=session.images.filter(im=>!im.boxes.length).length;
    if(missed)notice(`${missed} 张截图暂未识别到右侧头像，轻点图片可手动补选。`,true);
  }catch(e){notice(e.message,true);}finally{setBusy(false);$('screenshots').value='';}
}
$('screenshots').onchange=e=>run([...e.target.files]);
for(const name of ['dragenter','dragover'])$('dropzone').addEventListener(name,e=>{e.preventDefault();if(!busy)$('dropzone').classList.add('drag');});
for(const name of ['dragleave','drop'])$('dropzone').addEventListener(name,e=>{e.preventDefault();$('dropzone').classList.remove('drag');});
$('dropzone').addEventListener('drop',e=>{if(!busy)run([...e.dataTransfer.files]);});
$('demo').hidden=!['localhost','127.0.0.1','[::1]'].includes(location.hostname);
$('demo').onclick=()=>run([],true);
$('reset').onclick=async()=>{if(busy)return;setBusy(true);try{await clear();notice('');}catch(e){notice(e.message,true);}finally{setBusy(false);}};
function updateSummary(){if(!session)return;const total=session.images.reduce((n,im)=>n+im.boxes.filter(b=>b.enabled).length,0);$('summary').textContent=`已处理 ${session.images.length} 张截图`;$('saveSummary').textContent=`共替换 ${total} 处头像`;$('saveAll').textContent=session.images.length===1?'保存图片 ↓':`打包下载 ${session.images.length} 张 ↓`;}
function drawCards(){
  $('cards').replaceChildren(...session.images.map((im,i)=>{
    const button=document.createElement('button');button.className=`image-card${im.boxes.some(b=>b.enabled)?'':' zero'}`;button.setAttribute('aria-label',`检查第 ${i+1} 张截图`);
    const frame=document.createElement('div');frame.className='card-preview';const img=new Image();img.loading='lazy';img.alt=`第 ${i+1} 张替换结果`;img.src=cardUrls.get(im.id)||`/api/sessions/${session.id}/images/${im.id}/thumbnail`;frame.append(img);
    const meta=document.createElement('div');meta.className='card-meta';const title=document.createElement('span');title.textContent=`截图 ${String(i+1).padStart(2,'0')}`;const count=document.createElement('small');count.textContent=im.boxes.filter(b=>b.enabled).length?`${im.boxes.filter(b=>b.enabled).length} 处已替换`:'需要检查';meta.append(title,count);button.append(frame,meta);button.onclick=()=>openDetail(i);return button;
  }));
}
function openDetail(index){if(busy)return;current=index;selected=active().boxes.length?0:-1;editing=false;adding=false;zoomed=false;$('zoom').textContent='放大检查';$('detailTitle').textContent=`截图 ${index+1}`;$('detailSubtitle').textContent=`${active().width} × ${active().height} · 只替换右侧头像`;$('reviewDialog').showModal();drawDetail();resizeStage();queueRender();}
function closeDetail(){if(active()?.dirty)refreshCard();generation++;clearTimeout(timer);$('reviewDialog').close();if(previewUrl){URL.revokeObjectURL(previewUrl);previewUrl=null;}$('preview').removeAttribute('src');}
$('closeDetail').onclick=closeDetail;
$('reviewDialog').addEventListener('cancel',e=>{e.preventDefault();closeDetail();});
$('reviewDialog').addEventListener('click',e=>{if(e.target===$('reviewDialog'))closeDetail();});
function resizeStage(){const im=active();if(!im)return;const pad=innerWidth<=600?24:30;const scale=zoomed?1:Math.min(1,($('previewScroll').clientWidth-pad)/im.width,($('previewScroll').clientHeight-pad)/im.height);$('stage').style.width=`${im.width*scale}px`;$('stage').style.height=`${im.height*scale}px`;}
window.addEventListener('resize',resizeStage);
$('zoom').onclick=()=>{zoomed=!zoomed;$('zoom').textContent=zoomed?'适应窗口':'放大检查';resizeStage();if(zoomed){const b=active().boxes[selected]||active().boxes[0];$('previewScroll').scrollLeft=b?Math.max(0,b.x-50):active().width;$('previewScroll').scrollTop=b?Math.max(0,b.y-60):0;}else $('previewScroll').scrollTo(0,0);};
function drawDetail(){
  const im=active();if(!im)return;
  $('resultView').classList.toggle('selected',!editing);$('editView').classList.toggle('selected',editing);$('boxLayer').hidden=!editing;$('adjustments').hidden=!editing;
  $('stage').classList.toggle('adding',adding);$('addBox').textContent=adding?'完成补选':'＋ 补选头像';$('addHint').hidden=!adding;
  $('radius').max=im.boxes.length?Math.min(...im.boxes.map(b=>Math.min(b.w,b.h)/2)):40;$('radius').value=im.radius;$('radiusValue').textContent=`${im.radius} px`;
  $('boxLayer').replaceChildren(...im.boxes.map((b,i)=>{
    const box=document.createElement('button');box.className=`box${b.enabled?'':' off'}${selected===i?' selected':''}`;
    for(const [key,value]of Object.entries({left:100*b.x/im.width,top:100*b.y/im.height,width:100*b.w/im.width,height:100*(b.clip_h||b.h)/im.height}))box.style[key]=`${value}%`;
    box.setAttribute('aria-label',`${b.enabled?'取消':'恢复'}替换头像 ${i+1}`);const label=document.createElement('span');label.textContent=i+1;box.append(label);
    box.onclick=e=>{e.stopPropagation();if(adding)return;selected=i;b.enabled=!b.enabled;changed();refreshCard();};return box;
  }));
  $('boxList').replaceChildren(...im.boxes.map((b,i)=>{const el=document.createElement('button');el.className=`box-choice${b.enabled?'':' off'}${i===selected?' selected':''}`;el.textContent=`${b.enabled?'✓':'−'} 头像 ${i+1}`;el.onclick=()=>{selected=i;drawDetail();};return el;}));
  if(!im.boxes.length)$('boxList').textContent='未识别到头像，点击“补选头像”添加。';
  const b=im.boxes[selected];$('fineTune').hidden=!b;
  if(b)for(const[key,id]of Object.entries({x:'editX',y:'editY',w:'editW',h:'editH',clip_h:'editClipH'}))$(id).value=key==='clip_h'?(b.clip_h||b.h):b[key];
}
function changed(){active().dirty=true;updateSummary();drawDetail();if(!editing)queueRender();}
$('resultView').onclick=()=>{editing=false;adding=false;drawDetail();queueRender();};
$('editView').onclick=()=>{editing=true;generation++;clearTimeout(timer);$('previewLoading').hidden=true;$('preview').src=`/api/sessions/${session.id}/images/${active().id}`;drawDetail();};
$('radius').oninput=()=>{const im=active();im.dirty=true;im.radius=Number($('radius').value);im.boxes.forEach(b=>b.radius=im.radius);$('radiusValue').textContent=`${im.radius} px`;if(!editing)queueRender();};
$('radius').onchange=()=>{if(editing)refreshCard();};
function queueRender(){const token=++generation;clearTimeout(timer);$('previewLoading').hidden=false;timer=setTimeout(async()=>{const im=active(),sid=session?.id;if(!im||!sid)return;try{const response=await request(`/api/sessions/${sid}/images/${im.id}/render`,jsonPost({boxes:im.boxes,radius:im.radius}));const blob=await response.blob();if(token!==generation||editing||!$('reviewDialog').open)return;const url=URL.createObjectURL(blob);if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=url;$('preview').src=url;updateCard(im,blob);}catch(e){if(token===generation)notice(e.message,true);}finally{if(token===generation)$('previewLoading').hidden=true;}},150);}
function updateCard(im,blob){im.dirty=false;const old=cardUrls.get(im.id);const url=URL.createObjectURL(blob);cardUrls.set(im.id,url);if(old)URL.revokeObjectURL(old);const index=session.images.indexOf(im);const card=$('cards').children[index];if(card){card.querySelector('img').src=url;card.querySelector('small').textContent=im.boxes.some(b=>b.enabled)?`${im.boxes.filter(b=>b.enabled).length} 处已替换`:'需要检查';card.classList.toggle('zero',!im.boxes.some(b=>b.enabled));}}
async function refreshCard(){const im=active(),sid=session?.id;if(!im||!sid)return;const snapshot=JSON.stringify({boxes:im.boxes,radius:im.radius});try{const response=await request(`/api/sessions/${sid}/images/${im.id}/render`,{method:'POST',headers:{'Content-Type':'application/json'},body:snapshot});const blob=await response.blob();if(session?.id===sid&&snapshot===JSON.stringify({boxes:im.boxes,radius:im.radius}))updateCard(im,blob);}catch(e){notice(e.message,true);}}
$('addBox').onclick=()=>{adding=!adding;drawDetail();};
$('stage').onclick=e=>{if(!editing||!adding)return;const im=active();if(im.boxes.length>=150){notice('每张截图最多支持 150 个头像框。',true);return;}const rect=$('stage').getBoundingClientRect();const size=Math.max(8,Math.min(1024,im.width,im.height,Math.round(im.boxes[0]?.w||im.width*.1)));const x=Math.max(0,Math.min(im.width-size,Math.round((e.clientX-rect.left)/rect.width*im.width-size/2)));const y=Math.max(0,Math.min(im.height-size,Math.round((e.clientY-rect.top)/rect.height*im.height-size/2)));im.radius=Math.min(im.radius,size/2);im.boxes.forEach(b=>b.radius=im.radius);im.boxes.push({x,y,w:size,h:size,radius:im.radius,enabled:true,score:1,edge_quality:0,method:'manual'});selected=im.boxes.length-1;changed();refreshCard();};
for(const[key,id]of Object.entries({x:'editX',y:'editY',w:'editW',h:'editH',clip_h:'editClipH'}))$(id).onchange=()=>{
  const im=active(),b=im?.boxes[selected];if(!b)return;const value=Number($(id).value);const next={...b,[key]:value};const visible=next.clip_h||next.h;
  if(!Number.isInteger(value)||value<0||next.w<8||next.h<8||next.w>1024||next.h>1024||next.x+next.w>im.width||next.y+visible>im.height||visible<1||visible>next.h){notice('头像框须在图片内，可见高度不能超过完整高度。',true);drawDetail();return;}
  Object.assign(b,next);b.method='manual';im.radius=Math.min(im.radius,...im.boxes.map(b=>Math.min(b.w,b.h)/2));im.boxes.forEach(b=>b.radius=im.radius);changed();refreshCard();
};
$('removeBox').onclick=()=>{const im=active();im.boxes.splice(selected,1);selected=Math.min(selected,im.boxes.length-1);changed();refreshCard();};
function download(blob,name){const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
async function saveImage(im,index){const response=await request(`/api/sessions/${session.id}/images/${im.id}/render`,jsonPost({boxes:im.boxes,radius:im.radius}));download(await response.blob(),`avatar-mask-${index+1}.png`);}
$('saveCurrent').onclick=async()=>{if(busy)return;setBusy(true);try{await saveImage(active(),current);}catch(e){notice(e.message,true);}finally{setBusy(false);}};
$('saveAll').onclick=async()=>{if(busy||!session)return;setBusy(true);$('saveAll').textContent='正在准备…';try{if(session.images.length===1)await saveImage(session.images[0],0);else{const edits=Object.fromEntries(session.images.map(im=>[im.id,im.boxes]));const radii=Object.fromEntries(session.images.map(im=>[im.id,im.radius]));const response=await request(`/api/sessions/${session.id}/export`,jsonPost({edits,radii}));download(await response.blob(),'avatar-mask.zip');}notice('结果已准备好，请在浏览器下载中查看。');}catch(e){notice(e.message,true);}finally{setBusy(false);updateSummary();}};
$('preview').ondragstart=e=>e.preventDefault();
request('/api/health').then(()=>{$('health').textContent='本地处理';$('health').classList.add('ready');}).catch(()=>{$('health').textContent='服务未连接';});
