"""Serve the separated static frontend directly for the single-service ARM setup."""
import ipaddress
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

FRONTEND=Path(__file__).resolve().parents[1]/'frontend'
PUBLIC_FILES={
    'index.html':'text/html',
    'style.css':'text/css',
    'app.js':'text/javascript',
    'image-viewer.js':'text/javascript',
}
LAN_NETWORKS=tuple(ipaddress.ip_network(value) for value in (
    '127.0.0.0/8','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','169.254.0.0/16',
))


def frontend_file(name):
    if name not in PUBLIC_FILES:raise HTTPException(404,'Not found')
    return FileResponse(FRONTEND/name,media_type=PUBLIC_FILES[name],headers={
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
    })


class LanOnlyMiddleware:
    """Use the real socket address; production Uvicorn ignores proxy headers."""
    def __init__(self,app):self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        try:
            address=ipaddress.ip_address((scope.get('client') or ('',0))[0])
            if isinstance(address,ipaddress.IPv6Address):address=address.ipv4_mapped or address
            allowed=address==ipaddress.ip_address('::1') or any(address in network for network in LAN_NETWORKS)
        except ValueError:allowed=False
        status=403 if not allowed else 404 if scope['path'].rstrip('/')=='/api/demo' else None
        if status is None:return await self.app(scope,receive,send)
        body=b'{"detail":"Not available"}'
        await send({'type':'http.response.start','status':status,'headers':[
            (b'content-type',b'application/json'),(b'content-length',str(len(body)).encode()),
        ]})
        await send({'type':'http.response.body','body':body})
