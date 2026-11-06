import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const root = new URL('./', import.meta.url);
const files = {'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css'};
const types = {html:'text/html; charset=utf-8',js:'text/javascript; charset=utf-8',css:'text/css; charset=utf-8'};
const host=process.argv.includes('--lan')?'0.0.0.0':'127.0.0.1';
http.createServer(async(req,res)=>{
  let pathname;
  try{pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);}catch{res.writeHead(400);res.end('Invalid path');return;}
  if(pathname.startsWith('/api/')){
    if(pathname.replace(/\/+$/,'')==='/api/demo'&&!['127.0.0.1','::1','::ffff:127.0.0.1'].includes(req.socket.remoteAddress)){res.writeHead(403);res.end('Local examples are only available on this computer');return;}
    const upstream=http.request({hostname:'127.0.0.1',port:8000,path:req.url,method:req.method,headers:{...req.headers,host:'127.0.0.1:8000'}},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res);});
    upstream.on('error',()=>{if(!res.headersSent)res.writeHead(502,{'Content-Type':'application/json'});res.end(JSON.stringify({detail:'图片处理服务未连接，请启动后端。'}));});
    req.on('aborted',()=>upstream.destroy());req.pipe(upstream);return;
  }
  const file=files[pathname];
  if(!file){res.writeHead(404);res.end('Not found');return;}
  try {const data=await readFile(fileURLToPath(new URL(file,root)));res.writeHead(200,{'Content-Type':types[file.split('.').pop()],'Cache-Control':'no-store'});res.end(data);}
  catch{res.writeHead(500);res.end('Unable to load frontend');}
}).listen(5178,host,()=>console.log(`Avatar Mask: http://${host}:5178`));
