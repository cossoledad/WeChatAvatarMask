"""Download a GitHub release, verify it, and activate an isolated installation."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

ASSET='wechat-avatar-mask-arm64.tar.gz'
PREFIX='wechat-avatar-mask'
ROOT=Path(__file__).resolve().parents[1]
CACHE=Path('/var/cache/wechat-avatar-mask/releases')
MAX_ARCHIVE=512*1024*1024
MAX_UNPACKED=1024*1024*1024
TAG_PATTERN=r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?'


class SecureRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if urlsplit(newurl).scheme!='https':raise ValueError('Refusing an insecure download redirect')
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if redirected and urlsplit(req.full_url).netloc!=urlsplit(newurl).netloc:
            redirected.remove_header('Authorization')
        return redirected


def fetch(url,destination,limit,token='',binary=False):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.netloc!='api.github.com' or not parsed.path.startswith('/repos/'):
        raise ValueError('Invalid GitHub API URL')
    headers={'User-Agent':'WeChatAvatarMask-Updater','Accept':'application/octet-stream' if binary else 'application/vnd.github+json',
             'X-GitHub-Api-Version':'2026-03-10'}
    if token:headers['Authorization']=f'Bearer {token}'
    opener=build_opener(SecureRedirect())
    with opener.open(Request(url,headers=headers),timeout=60) as response,destination.open('wb') as output:
        total=0
        while chunk:=response.read(1024*1024):
            total+=len(chunk)
            if total>limit:raise ValueError('Release download exceeds the size limit')
            output.write(chunk)


def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as file:
        while chunk:=file.read(1024*1024):digest.update(chunk)
    return digest.hexdigest()


def unpack(archive,destination):
    """Accept only regular, bounded files beneath the expected archive root."""
    total=0;seen=set()
    with tarfile.open(archive,'r:gz') as package:
        for member in package:
            parts=PurePosixPath(member.name).parts
            if not parts or parts[0]!=PREFIX or any(p in {'..','.'} or '\\' in p or ':' in p for p in parts) or member.name.startswith('/'):
                raise ValueError('Unsafe release archive path')
            if member.isdir():continue
            if not member.isfile() or len(parts)<2 or member.name in seen:
                raise ValueError('Unsafe or duplicate release archive member')
            seen.add(member.name);total+=member.size
            if member.size<0 or total>MAX_UNPACKED or len(seen)>1000:raise ValueError('Release archive exceeds the extraction limit')
            target=destination.joinpath(*parts)
            target.parent.mkdir(parents=True,exist_ok=True)
            with package.extractfile(member) as source,target.open('xb') as output:shutil.copyfileobj(source,output)
            target.chmod(0o755 if target.suffix=='.sh' else 0o644)
    source=destination/PREFIX
    manifest=source/'deploy/source-manifest.json'
    entries=json.loads(manifest.read_text(encoding='utf-8'))['files']
    actual={path.relative_to(source).as_posix() for path in source.rglob('*') if path.is_file()}
    if actual!=set(entries)|{'deploy/source-manifest.json'}:raise ValueError('Release manifest does not match archive contents')
    for name,expected in entries.items():
        path=(source/name).resolve()
        if not path.is_relative_to(source.resolve()) or not path.is_file() or sha256(path)!=expected:
            raise ValueError(f'Release checksum mismatch: {name}')
    return source


def main(argv=None):
    metadata=ROOT/'deploy/release.json'
    installed=json.loads(metadata.read_text()) if metadata.exists() else {}
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tag',nargs='?',help='Version tag; defaults to latest stable release')
    parser.add_argument('--check',action='store_true',help='Only show the available version')
    parser.add_argument('--repo',default=installed.get('repository','cossoledad/WeChatAvatarMask'))
    args=parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',args.repo):parser.error('Invalid repository')
    if args.tag and not re.fullmatch(TAG_PATTERN,args.tag):parser.error('Use a tag such as v1.2.3')
    if not args.check and (os.name!='posix' or os.geteuid()!=0):parser.error('Run updates with sudo')
    token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN','')
    endpoint=f'https://api.github.com/repos/{args.repo}/releases/'
    endpoint+=f'tags/{quote(args.tag,safe="")}' if args.tag else 'latest'
    with tempfile.TemporaryDirectory(prefix='avatar-release-') as temporary:
        release_path=Path(temporary)/'release.json'
        fetch(endpoint,release_path,2*1024*1024,token)
        release=json.loads(release_path.read_text())
    tag=release['tag_name']
    if not re.fullmatch(TAG_PATTERN,tag) or release.get('draft'):raise ValueError('Invalid release version')
    current=installed.get('version','local')
    print(f'Installed: {current} | Available: {tag}',flush=True)
    if args.check:return
    if tag==current:
        print('Already up to date.');return
    assets={item['name']:item for item in release['assets']}
    if not {ASSET,ASSET+'.sha256'}<=assets.keys():raise ValueError('This release has no ARM64 package/checksum')
    CACHE.mkdir(parents=True,exist_ok=True,mode=0o700)
    import fcntl
    with (CACHE.parent/'update.lock').open('a') as update_lock:
        try:fcntl.flock(update_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise ValueError('Another update is running') from exc
        with tempfile.TemporaryDirectory(prefix='.update-',dir=CACHE) as temporary:
            staging=Path(temporary)
            checksum=staging/(ASSET+'.sha256')
            fetch(assets[ASSET+'.sha256']['url'],checksum,4096,token,True)
            match=re.fullmatch(r'([0-9a-f]{64})\s+\*?'+re.escape(ASSET)+r'\s*',checksum.read_text(encoding='ascii'))
            if not match:raise ValueError('Invalid release checksum file')
            expected=match[1]
            cached=CACHE/f'{tag}-{ASSET}'
            if not cached.is_file() or sha256(cached)!=expected:
                download=staging/ASSET
                print(f'Downloading {tag}…',flush=True)
                fetch(assets[ASSET]['url'],download,MAX_ARCHIVE,token,True)
                if sha256(download)!=expected:raise ValueError('Release archive checksum mismatch')
                download.replace(cached)
            source=unpack(cached,staging/'source')
            bundled=json.loads((source/'deploy/release.json').read_text())
            if bundled.get('version')!=tag or bundled.get('repository')!=args.repo:
                raise ValueError('Release metadata does not match the requested version/repository')
            print('Checksums verified. Installing…',flush=True)
            subprocess.run(['bash',str(source/'deploy/install.sh'),'--skip-apt'],check=True)
            print(f'Updated to {tag}. Avatars and cache were preserved.',flush=True)


if __name__=='__main__':
    try:main()
    except HTTPError as exc:raise SystemExit(f'GitHub download failed (HTTP {exc.code}); check the release/repository or GH_TOKEN.')
    except (ValueError,KeyError,OSError,URLError,tarfile.TarError,subprocess.CalledProcessError) as exc:raise SystemExit(f'Update failed: {exc}')
