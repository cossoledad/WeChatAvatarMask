import {ImageViewer} from './image-viewer.js';

const $=id=>document.getElementById(id);
let session=null,busy=false,current=0,generation=0,timer,renderController=null,previewUrl=null;
let selectedAvatar='builtin',replacementRevision=0,avatarLibrary=[];
const cardUrls=new Map();
const limits={max_upload_mb:15,max_pixels:16_000_000};
const active=()=>session?.images[current];
const viewer=new ImageViewer($('imageViewport'),$('stage'));
const jsonPost=data=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});

function notice(message){$('notice').textContent=message;$('notice').hidden=!message;}
function detailNotice(message){$('detailNotice').textContent=message;$('detailNotice').hidden=!message;}
async function request(path,options={}){
  let response;
  try{response=await fetch(path,options);}catch(error){if(error.name==='AbortError')throw error;throw new Error('连接失败，请重试。');}
  if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(typeof data.detail==='string'?data.detail:'处理失败，请重试。');}
  return response;
}
function setBusy(value,processing=false){busy=value;for(const id of ['screenshots','replacement','chooseAvatar','reset','saveAll','saveCurrent'])$(id).disabled=value;for(const button of $('avatarGrid').querySelectorAll('button'))button.disabled=value;$('radius').disabled=value||!active()?.boxes.length;$('processing').hidden=!processing;$('dropzone').hidden=processing;}
function storeTask(id){try{if(id)sessionStorage.setItem('avatar-mask-task',id);else sessionStorage.removeItem('avatar-mask-task');}catch{}}
function showResults(){document.body.classList.add('has-results');$('results').hidden=false;$('savebar').hidden=false;updateSummary();drawCards();}
function cancelRender(){generation++;clearTimeout(timer);renderController?.abort();renderController=null;$('previewLoading').hidden=true;}
function releaseUrls(){cancelRender();if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=null;for(const url of cardUrls.values())URL.revokeObjectURL(url);cardUrls.clear();}
async function clear(){
  if(session)await request(`/api/sessions/${session.id}`,{method:'DELETE'});
  session=null;storeTask(null);releaseUrls();$('cards').replaceChildren();$('results').hidden=true;$('savebar').hidden=true;
  document.body.classList.remove('has-results');$('screenshots').value='';$('uploadTitle').textContent='选择截图';
}
async function run(files){
  if(busy||!files.length)return;
  if(files.length>30){notice('最多选择 30 张。');return;}
  if(files.some(f=>f.size>limits.max_upload_mb*1024*1024)){notice(`单张图片不能超过 ${limits.max_upload_mb} MB。`);return;}
  setBusy(true,true);notice('');
  try{
    await clear();const body=new FormData();files.forEach(file=>body.append('screenshots',file));body.append('avatar_id',selectedAvatar);
    session=await(await request('/api/sessions',{method:'POST',body})).json();
    if(!session.images.length)throw new Error('没有可处理的截图。');
    storeTask(session.id);showResults();
  }catch(error){notice(error.message);}finally{setBusy(false);$('screenshots').value='';}
}
$('screenshots').onchange=event=>run([...event.target.files]);
function avatarUrl(id,thumbnail=false){return `/api/avatars/${encodeURIComponent(id)}/image${thumbnail?'?thumbnail=true':''}`;}
function avatarNotice(message){$('avatarNotice').textContent=message;$('avatarNotice').hidden=!message;}
async function loadAvatars(){
  const data=await(await request('/api/avatars')).json();avatarLibrary=data.items;selectedAvatar=data.selected;
  $('targetAvatar').src=avatarUrl(selectedAvatar,true);drawAvatars();
}
function drawAvatars(){
  const upload=document.createElement('button');upload.className='avatar-choice add-avatar';upload.textContent='+';upload.setAttribute('aria-label','上传头像');upload.onclick=()=>{if(!busy)$('replacement').click();};
  $('avatarGrid').replaceChildren(upload,...avatarLibrary.map(item=>{
    const button=document.createElement('button');button.className='avatar-choice';button.setAttribute('aria-label',item.name);button.setAttribute('aria-pressed',String(item.id===selectedAvatar));
    const img=new Image();img.src=avatarUrl(item.id,true);img.alt='';img.loading='lazy';button.append(img);
    button.onclick=async()=>{
      if(busy)return;setBusy(true);avatarNotice('');
      try{await request(`/api/avatars/${item.id}/select`,jsonPost({session_id:session?.id}));await avatarChanged();}
      catch(error){avatarNotice(error.message);}finally{setBusy(false);}
    };return button;
  }));
}
async function avatarChanged(){
  replacementRevision++;await loadAvatars();
  if(session){session.replacement_id=selectedAvatar;releaseUrls();for(const im of session.images)im.dirty=true;drawCards();if($('reviewDialog').open)queueRender();}
  $('avatarDialog').close();
}
$('chooseAvatar').onclick=()=>{if(!busy){avatarNotice('');$('avatarDialog').showModal();}};
$('closeAvatars').onclick=()=>$('avatarDialog').close();
$('avatarDialog').addEventListener('click',event=>{if(event.target===$('avatarDialog'))$('avatarDialog').close();});
$('replacement').onchange=async event=>{
  const file=event.target.files[0];if(!file||busy)return;
  if(file.size>limits.max_upload_mb*1024*1024){avatarNotice(`头像不能超过 ${limits.max_upload_mb} MB。`);event.target.value='';return;}
  setBusy(true);avatarNotice('');cancelRender();
  try{
    if(file.type&&!['image/jpeg','image/png','image/webp'].includes(file.type))throw new Error('请选择 JPEG、PNG 或 WebP 头像。');
    // Validate before replacing the chosen file, so a broken image cannot
    // destroy the working avatar selection or the current batch.
    const candidateUrl=URL.createObjectURL(file),image=new Image();image.src=candidateUrl;
    try{await image.decode();}catch{throw new Error('无法读取头像。');}finally{URL.revokeObjectURL(candidateUrl);}
    if(image.naturalWidth*image.naturalHeight>limits.max_pixels)throw new Error(`头像不能超过 ${limits.max_pixels/10_000} 万像素。`);
    const body=new FormData();body.append('replacement',file);if(session)body.append('session_id',session.id);
    await request('/api/avatars',{method:'POST',body});await avatarChanged();
  }catch(error){avatarNotice(error.message);}
  finally{setBusy(false);event.target.value='';}
};
for(const name of ['dragenter','dragover'])$('dropzone').addEventListener(name,event=>{event.preventDefault();if(!busy)$('dropzone').classList.add('drag');});
for(const name of ['dragleave','drop'])$('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.remove('drag');});
$('dropzone').addEventListener('drop',event=>{if(!busy)run([...event.dataTransfer.files]);});
$('reset').onclick=async()=>{if(busy)return;setBusy(true);try{await clear();notice('');}catch(error){notice(error.message);}finally{setBusy(false);}};

function updateSummary(){if(!session)return;$('summary').textContent=`${session.images.length} 张`;$('saveAll').textContent=session.images.length===1?'保存':'保存全部';}
function drawCards(){
  $('cards').replaceChildren(...session.images.map((im,index)=>{
    const button=document.createElement('button');button.className='image-card';button.setAttribute('aria-label',`查看第 ${index+1} 张结果`);
    const img=new Image();img.loading='lazy';img.alt=`第 ${index+1} 张处理结果`;img.src=cardUrls.get(im.id)||`/api/sessions/${session.id}/images/${im.id}/thumbnail?v=${replacementRevision}`;button.append(img);
    if(!im.boxes.some(box=>box.enabled)){const badge=document.createElement('span');badge.className='badge';badge.textContent='未识别';button.append(badge);}
    button.onclick=()=>openDetail(index);return button;
  }));
}
function openDetail(index){
  if(busy)return;current=index;const im=active();detailNotice('');$('detailTitle').textContent=`${index+1} / ${session.images.length}`;
  $('radius').max=im.boxes.length?Math.min(...im.boxes.map(box=>Math.min(box.w,box.h)/2)):40;
  $('radius').value=im.radius;$('radius').disabled=!im.boxes.length;$('radiusValue').textContent=`${im.radius} px`;
  $('preview').src=cardUrls.get(im.id)||`/api/sessions/${session.id}/images/${im.id}/thumbnail?v=${replacementRevision}`;
  $('reviewDialog').showModal();viewer.open(im.width,im.height);
  if(!cardUrls.has(im.id)||im.dirty)queueRender();
}
function closeDetail(){if(active()?.dirty)refreshCard(active());cancelRender();viewer.close();$('reviewDialog').close();if(previewUrl){URL.revokeObjectURL(previewUrl);previewUrl=null;}$('preview').removeAttribute('src');}
$('closeDetail').onclick=closeDetail;
$('reviewDialog').addEventListener('cancel',event=>{event.preventDefault();closeDetail();});
$('reviewDialog').addEventListener('click',event=>{if(event.target===$('reviewDialog'))closeDetail();});
$('radius').oninput=()=>{
  const im=active();im.radius=Number($('radius').value);im.boxes.forEach(box=>box.radius=im.radius);im.dirty=true;im.revision++;
  $('radiusValue').textContent=`${im.radius} px`;queueRender();
};
function updateCard(im,blob){
  im.dirty=false;const old=cardUrls.get(im.id),url=URL.createObjectURL(blob);cardUrls.set(im.id,url);
  const card=$('cards').children[session.images.indexOf(im)];if(card)card.querySelector('img').src=url;
  if(old)URL.revokeObjectURL(old);
}
function queueRender(){
  cancelRender();const token=generation,im=active(),sid=session?.id,revision=im?.revision;
  if(!im||!sid)return;
  const options=jsonPost({boxes:im.boxes,radius:im.radius,revision:im.revision});$('previewLoading').hidden=false;
  timer=setTimeout(async()=>{
    const controller=new AbortController();renderController=controller;
    try{
      const response=await request(`/api/sessions/${sid}/images/${im.id}/render`,{...options,signal:controller.signal});const blob=await response.blob();
      if(token!==generation||session?.id!==sid||revision!==im.revision||!$('reviewDialog').open)return;
      const url=URL.createObjectURL(blob),old=previewUrl;previewUrl=url;$('preview').src=url;if(old)URL.revokeObjectURL(old);
      updateCard(im,blob);detailNotice('');
    }catch(error){if(error.name!=='AbortError'&&token===generation)detailNotice(error.message);}
    finally{if(token===generation){$('previewLoading').hidden=true;renderController=null;}}
  },150);
}
async function refreshCard(im){
  const sid=session?.id,revision=im.revision;if(!sid)return;
  try{const response=await request(`/api/sessions/${sid}/images/${im.id}/render`,jsonPost({boxes:im.boxes,radius:im.radius,revision:im.revision}));const blob=await response.blob();if(session?.id===sid&&revision===im.revision)updateCard(im,blob);}
  catch(error){if(session?.id===sid)notice(error.message);}
}
function download(blob,name){const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
async function saveImage(im,index,directory=null){
  const response=await request(`/api/sessions/${session.id}/images/${im.id}/render`,jsonPost({boxes:im.boxes,radius:im.radius,revision:im.revision}));
  const blob=await response.blob(),name=`avatar-mask-${String(index+1).padStart(2,'0')}.png`;
  if(directory){const file=await directory.getFileHandle(name,{create:true}),writer=await file.createWritable();try{await writer.write(blob);await writer.close();}catch(error){await writer.abort().catch(()=>{});throw error;}}
  else download(blob,name);
}
$('saveCurrent').onclick=async()=>{
  if(busy)return;setBusy(true);$('saveCurrent').textContent='…';
  try{await saveImage(active(),current);detailNotice('');}catch(error){detailNotice(error.message);}finally{setBusy(false);$('saveCurrent').textContent='保存';}
};
$('saveAll').onclick=async()=>{
  if(busy||!session)return;setBusy(true);$('saveAll').textContent='…';notice('');
  try{
    const directory=session.images.length>1&&window.showDirectoryPicker?await window.showDirectoryPicker({mode:'readwrite'}):null;
    for(let index=0;index<session.images.length;index++){await saveImage(session.images[index],index,directory);$('saveAll').textContent=`${index+1} / ${session.images.length}`;}
  }catch(error){if(error.name!=='AbortError')notice(error.message);}finally{setBusy(false);updateSummary();}
};
setBusy(true);
(async()=>{
  try{
    const health=await(await request('/api/health')).json();if(health.limits)Object.assign(limits,health.limits);
    await loadAvatars();let sid;try{sid=sessionStorage.getItem('avatar-mask-task');}catch{}
    if(sid){try{session=await(await request(`/api/sessions/${sid}`)).json();selectedAvatar=session.replacement_id;$('targetAvatar').src=avatarUrl(selectedAvatar,true);drawAvatars();showResults();}catch(error){storeTask(null);throw error;}}
  }catch(error){notice(error.message);}finally{setBusy(false);}
})();
