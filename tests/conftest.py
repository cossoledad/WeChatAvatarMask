import pytest

import backend.main as api
from backend.storage import Storage


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'storage',Storage(tmp_path/'data'))
    api.sessions.clear()
    yield
    api.sessions.clear()
