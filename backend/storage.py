"""Persistent avatar library and image-task cache; never stored in a release."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from time import time

from .imaging import decode, png


class Storage:
    def __init__(self,directory):
        self.directory=Path(directory)
        self.database=self.directory/'cache.sqlite3'
        self.lock=RLock()
        self.ready=False

    @contextmanager
    def connect(self):
        with self.lock:
            self.directory.mkdir(parents=True,exist_ok=True)
            db=sqlite3.connect(self.database,timeout=30)
            db.execute('PRAGMA foreign_keys=ON')
            if not self.ready:
                db.execute('PRAGMA journal_mode=WAL')
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS avatars (
                      id TEXT PRIMARY KEY,name TEXT NOT NULL,image BLOB NOT NULL,
                      thumbnail BLOB NOT NULL,created REAL NOT NULL,used REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS tasks (
                      id TEXT PRIMARY KEY,created REAL NOT NULL,source BLOB,
                      replacement BLOB NOT NULL,metadata TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS images (
                      task TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                      id TEXT NOT NULL,image BLOB NOT NULL,PRIMARY KEY(task,id));
                ''')
                self.ready=True
            try:
                with db:yield db
            finally:db.close()

    def _select(self,db,avatar_id):
        if avatar_id!='builtin' and not db.execute('SELECT 1 FROM avatars WHERE id=?',(avatar_id,)).fetchone():
            raise KeyError(avatar_id)
        db.execute('UPDATE avatars SET used=? WHERE id=?',(time(),avatar_id))
        db.execute("INSERT INTO preferences VALUES ('selected',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(avatar_id,))

    def add_avatar(self,image,thumbnail,name):
        avatar_id=hashlib.sha256(image).hexdigest()
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO avatars VALUES (?,?,?,?,?,?)',(avatar_id,name,image,thumbnail,time(),time()))
        return avatar_id

    def select(self,avatar_id):
        with self.connect() as db:self._select(db,avatar_id)

    def selected(self):
        with self.connect() as db:
            row=db.execute("SELECT value FROM preferences WHERE key='selected'").fetchone()
            return row[0] if row else 'builtin'

    def avatars(self):
        with self.connect() as db:
            return [dict(id=row[0],name=row[1]) for row in db.execute('SELECT id,name FROM avatars ORDER BY used DESC,created DESC,id')]

    def avatar(self,avatar_id,thumbnail=False):
        column='thumbnail' if thumbnail else 'image'
        with self.connect() as db:
            row=db.execute(f'SELECT {column} FROM avatars WHERE id=?',(avatar_id,)).fetchone()
            if not row:raise KeyError(avatar_id)
            return row[0]

    @staticmethod
    def metadata(session):
        return json.dumps(dict(replacement_id=session.replacement_id,images=[{
            key:value for key,value in item.items() if key!='data'
        } for item in session.images]),ensure_ascii=False,separators=(',',':'))

    def put_task(self,sid,session):
        with self.connect() as db:
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?)',(sid,session.created,
                png(session.source) if session.source is not None else None,
                png(session.replacement),self.metadata(session)))
            for item in session.images:
                db.execute('INSERT INTO images VALUES (?,?,?)',(sid,item['id'],png(item['data'])))
            self._select(db,session.replacement_id)

    def load_task(self,sid):
        with self.connect() as db:
            row=db.execute('SELECT created,source,replacement,metadata FROM tasks WHERE id=?',(sid,)).fetchone()
            if not row:raise KeyError(sid)
            created,source,replacement,raw=row;meta=json.loads(raw)
            images=[]
            for item in meta['images']:
                blob=db.execute('SELECT image FROM images WHERE task=? AND id=?',(sid,item['id'])).fetchone()
                if not blob:raise ValueError('Incomplete image cache')
                images.append({**item,'data':decode(blob[0])})
            return dict(created=created,source=decode(source) if source is not None else None,
                        replacement=decode(replacement),images=images,replacement_id=meta.get('replacement_id','builtin'))

    def update_task(self,sid,session,replacement=False,select=False):
        with self.connect() as db:
            if replacement:
                db.execute('UPDATE tasks SET replacement=?,metadata=? WHERE id=?',(png(session.replacement),self.metadata(session),sid))
            else:db.execute('UPDATE tasks SET metadata=? WHERE id=?',(self.metadata(session),sid))
            if select:self._select(db,session.replacement_id)

    def delete_task(self,sid):
        with self.connect() as db:db.execute('DELETE FROM tasks WHERE id=?',(sid,))
