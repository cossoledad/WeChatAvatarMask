"""Render fixed deployment templates and verify release contents (stdlib only)."""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def verify_source(source):
    manifest=source/'deploy'/'source-manifest.json'
    if not manifest.exists():
        print('Source: local checkout (no package manifest).')
        return
    entries=json.loads(manifest.read_text(encoding='utf-8'))['files']
    for relative,expected in entries.items():
        path=(source/relative).resolve()
        if not path.is_relative_to(source.resolve()) or not path.is_file():
            raise ValueError(f'Invalid package member: {relative}')
        actual=hashlib.sha256(path.read_bytes()).hexdigest()
        if actual!=expected:raise ValueError(f'Package checksum mismatch: {relative}')
    print(f'Package checksums verified: {len(entries)} files.')


def render(args):
    address=ipaddress.IPv4Address(args.listen)
    if not 1024<=args.port<=65535:raise ValueError('Use an HTTP port between 1024 and 65535.')
    if address.is_multicast:raise ValueError('Cannot listen on a multicast address.')
    if not (address.is_private or address.is_unspecified):raise ValueError('Use a LAN IPv4 address, loopback, or 0.0.0.0.')
    out=args.output;out.mkdir(parents=True,exist_ok=True)
    # Cache budgets are intentionally lower than total process limits: decoding,
    # compositing, PNG encoding, and downloads also need working memory.
    if args.memory_mb<3000:
        session,total,count,pixels,upload,high,maximum=128,192,2,12_000_000,10,800,1100
    elif args.memory_mb<6000:
        session,total,count,pixels,upload,high,maximum=192,320,3,16_000_000,15,1100,1536
    else:
        session,total,count,pixels,upload,high,maximum=220,440,4,16_000_000,15,1500,2048
    values={
        'AVATAR_MASK_HTTP_LISTEN':str(address),'AVATAR_MASK_HTTP_PORT':args.port,
        'AVATAR_MASK_PRODUCTION':1,
        'AVATAR_MASK_SESSION_TTL':1800,'AVATAR_MASK_MAX_UPLOAD_MB':upload,
        'AVATAR_MASK_MAX_PIXELS':pixels,'AVATAR_MASK_MAX_SESSION_MB':session,
        'AVATAR_MASK_MAX_TOTAL_MB':total,'AVATAR_MASK_MAX_SESSIONS':count,
        'AVATAR_MASK_CV_THREADS':1,
    }
    env='# Generated once; existing settings are preserved during upgrades.\n'
    env+=''.join(f'{key}={value}\n' for key,value in values.items())
    (out/'app.env').write_text(env,encoding='utf-8')
    (out/'resources.conf').write_text(f'[Service]\nMemoryHigh={high}M\nMemoryMax={maximum}M\nTasksMax=128\n',encoding='utf-8')
    print(f'Memory profile: RAM {args.memory_mb} MiB, session {session} MiB, cached total {total} MiB, process max {maximum} MiB.')


def main():
    parser=argparse.ArgumentParser()
    commands=parser.add_subparsers(dest='command',required=True)
    verify=commands.add_parser('verify');verify.add_argument('source',type=Path)
    config=commands.add_parser('render');config.add_argument('--listen',default='0.0.0.0')
    config.add_argument('--port',type=int,default=30127);config.add_argument('--memory-mb',type=int,required=True)
    config.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='verify':verify_source(args.source)
    else:render(args)


if __name__=='__main__':main()
