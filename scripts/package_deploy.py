"""Package the current workspace for ARM Ubuntu without private test pictures.

Uses an explicit runtime/deployment allowlist, not git history or directory-wide
archiving. No Windows virtualenv, cached tasks, image outputs, or dev tests ship.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]
PREFIX='wechat-avatar-mask'


def source_files():
    files=list((ROOT/'backend').glob('*.py'))
    files+=[ROOT/'backend/assets/default-avatar.jpg']
    files+=[ROOT/'frontend'/name for name in ('index.html','style.css','app.js','image-viewer.js')]
    for pattern in ('*.sh','*.py','*.in','*.service','requirements-arm64.txt'):
        files+=list((ROOT/'deploy').glob(pattern))
    files+=[ROOT/'README.md',ROOT/'docs/DEPLOYMENT_ARM_UBUNTU.md']
    result={}
    for file in files:
        if not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError(f'Invalid deployment source: {file}')
        relative=file.relative_to(ROOT).as_posix()
        content=file.read_bytes()
        if file.suffix in {'.sh','.py','.in','.service','.txt','.html','.js','.css','.md'}:
            # CRLF from Windows checkouts must not break Bash/systemd on Ubuntu.
            content=content.decode('utf-8-sig').replace('\r\n','\n').encode('utf-8')
        result[relative]=content
    return dict(sorted(result.items()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/wechat-avatar-mask-arm64.tar.gz')
    args=parser.parse_args()
    files=source_files()
    manifest={'format':1,'target':'Ubuntu 24.04 arm64 / Python 3.12',
              'files':{name:hashlib.sha256(content).hexdigest() for name,content in files.items()}}
    files['deploy/source-manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    output=args.output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(output,'w:gz',format=tarfile.PAX_FORMAT) as archive:
        for relative,content in sorted(files.items()):
            info=tarfile.TarInfo(f'{PREFIX}/{relative}');info.size=len(content)
            info.mode=0o755 if relative.endswith('.sh') else 0o644
            info.mtime=0;info.uid=info.gid=0;info.uname=info.gname='root'
            archive.addfile(info,io.BytesIO(content))
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    checksum=output.with_name(output.name+'.sha256')
    checksum.write_text(f'{digest}  {output.name}\n',encoding='ascii')
    print(f'Archive: {output}\nSHA256: {digest}\nFiles: {len(files)}')


if __name__=='__main__':main()
