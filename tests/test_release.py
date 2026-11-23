import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from deploy.update import unpack,fetch,SecureRedirect
from urllib.request import Request

ROOT=Path(__file__).resolve().parents[1]


def test_release_package_verification_and_corruption(tmp_path):
    output=tmp_path/'release.tar.gz'
    subprocess.run([sys.executable,str(ROOT/'scripts/package_deploy.py'),'--output',str(output),'--version','v0.4.0'],check=True,capture_output=True)
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    assert output.with_name(output.name+'.sha256').read_text().split()[0]==digest
    source=unpack(output,tmp_path/'good')
    assert json.loads((source/'deploy/release.json').read_text())['version']=='v0.4.0'
    assert (source/'backend/storage.py').is_file() and (source/'deploy/update.py').is_file()
    assert not (source/'data').exists() and not (source/'test.data').exists()
    assert b'\r\n' not in (source/'deploy/install.sh').read_bytes()
    # A modified file with an unchanged manifest must be rejected.
    corrupted=tmp_path/'corrupted.tar.gz'
    with tarfile.open(output) as original,tarfile.open(corrupted,'w:gz') as changed:
        for member in original:
            content=original.extractfile(member).read()
            if member.name.endswith('/frontend/app.js'):content+=b'\nmodified\n';member.size=len(content)
            changed.addfile(member,io.BytesIO(content))
    with pytest.raises(ValueError,match='checksum mismatch'):unpack(corrupted,tmp_path/'bad')


def test_release_rejects_path_escape_and_protects_token(tmp_path):
    archive=tmp_path/'unsafe.tar.gz'
    with tarfile.open(archive,'w:gz') as package:
        member=tarfile.TarInfo('wechat-avatar-mask/../../escape');member.size=1
        package.addfile(member,io.BytesIO(b'x'))
    with pytest.raises(ValueError,match='Unsafe'):unpack(archive,tmp_path/'extract')
    assert not (tmp_path/'escape').exists()
    request=Request('https://api.github.com/repos/owner/repo/releases/assets/1',headers={'Authorization':'Bearer secret'})
    redirect=SecureRedirect().redirect_request(request,None,302,'',{},'https://release-assets.githubusercontent.com/file')
    assert not redirect.has_header('Authorization')
    with pytest.raises(ValueError,match='insecure'):SecureRedirect().redirect_request(request,None,302,'',{},'http://example.com/file')
    with pytest.raises(ValueError,match='Invalid GitHub'):fetch('https://example.com/file',tmp_path/'file',100,'secret')
