"""One durable project per upload, with recoverable whole-project deletion."""
import json
import logging
import os
import shutil
import sqlite3
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request

from . import database

router = APIRouter(prefix='/api/projects', tags=['projects'])
logger = logging.getLogger(__name__)


def register(db, record):
    if record.get('source_file_id'):
        return
    db.execute('INSERT OR IGNORE INTO projects (id,source_file_id,name,size,created_at) VALUES (?,?,?,?,?)',
               (record['id'], record['id'], record['name'], record['size'], record['created_at']))


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY, source_file_id TEXT, name TEXT NOT NULL,
        size INTEGER, created_at TEXT
    )''')
    records = {row['id']: dict(row) for row in db.execute('SELECT * FROM files')}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
        for row in db.execute('SELECT payload FROM tasks'):
            task = json.loads(row['payload'])
            for snapshot in (task.get('file'), (task.get('result') or {}).get('file')):
                if snapshot:
                    records.setdefault(snapshot['id'], snapshot)
    for row in db.execute('SELECT DISTINCT history_id FROM file_history').fetchall():
        group = row['history_id']
        if db.execute('SELECT 1 FROM projects WHERE id=?', (group,)).fetchone():
            continue
        identity, visited = group, set()
        while identity in records and identity not in visited:
            visited.add(identity)
            parent = records[identity].get('source_file_id')
            if not parent:
                break
            identity = parent
        source = records.get(identity)
        if source and source.get('source_file_id'):
            source = None  # Untraceable ancestry: never label a generated version as the source.
        db.execute('INSERT INTO projects VALUES (?,?,?,?,?)', (
            group, identity, source['name'] if source else '源文件不可用（历史项目）',
            source.get('size') if source else None, source.get('created_at') if source else None))


def list_projects(root, limit, offset):
    with database.connection(root) as db:
        rows = db.execute('''SELECT p.*, f.storage_key FROM projects p
            LEFT JOIN files f ON f.id=p.source_file_id
            ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?''', (limit, offset)).fetchall()
        total = db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
    items = []
    for row in rows:
        item = dict(row)
        key = item.pop('storage_key')
        item['source_available'] = bool(key and (root / 'uploads' / key).is_file())
        items.append(item)
    return dict(items=items, total=total)


def recover_directory(root, directory):
    manifest = directory / 'manifest.json'
    if not manifest.exists():
        # A manifest is made durable before the first move, so there is nothing to restore.
        shutil.rmtree(directory)
        return
    journal = json.loads(manifest.read_text())
    with database.connection(root) as db:
        exists = db.execute('SELECT 1 FROM projects WHERE id=?', (journal['project_id'],)).fetchone()
    if exists:
        for key in journal['storage_keys']:
            if not isinstance(key, str) or '/' in key or '\\' in key or not key.endswith('.docx'):
                raise ValueError('Invalid deletion journal')
            staged, target = directory / key, root / 'uploads' / key
            if staged.exists():
                if target.exists():
                    raise OSError('Cannot restore staged file over an existing file')
                staged.replace(target)
    shutil.rmtree(directory)


def recover_deletions(root):
    trash = root / '.project-trash'
    if trash.exists():
        for directory in trash.iterdir():
            if directory.is_dir():
                recover_directory(root, directory)


def delete_project(root, project_id):
    directory = None
    try:
        with database.connection(root) as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM tasks WHERE json_extract(payload, '$.status') IN ('queued','running','cancelling') LIMIT 1").fetchone():
                raise database.ActiveTaskError('任务执行期间不能删除项目，请等待结束或停止后重试')
            if not db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
                return False
            rows = db.execute('''SELECT f.storage_key FROM files f JOIN file_history h
                ON h.file_id=f.id WHERE h.history_id=?''', (project_id,)).fetchall()
            keys = [row['storage_key'] for row in rows]
            directory = root / '.project-trash' / str(uuid4())
            directory.mkdir(parents=True)
            journal = dict(project_id=project_id, storage_keys=keys)
            with (directory / 'manifest.tmp').open('x') as stream:
                json.dump(journal, stream)
                stream.flush()
                os.fsync(stream.fileno())
            (directory / 'manifest.tmp').replace(directory / 'manifest.json')
            for key in keys:
                source = root / 'uploads' / key
                if source.exists():
                    source.replace(directory / key)
            db.execute("DELETE FROM tasks WHERE json_extract(payload, '$.file.id') IN (SELECT file_id FROM file_history WHERE history_id=?)", (project_id,))
            db.execute('DELETE FROM files WHERE id IN (SELECT file_id FROM file_history WHERE history_id=?)', (project_id,))
            db.execute('DELETE FROM file_history WHERE history_id=?', (project_id,))
            db.execute('DELETE FROM projects WHERE id=?', (project_id,))
    except Exception:
        if directory is not None:
            recover_directory(root, directory)
        raise
    # Committed deletion is successful even if physical cleanup needs a restart.
    try:
        recover_directory(root, directory)
    except OSError:
        logger.warning('Project deletion committed; staged files will be cleaned at startup')
    return True


@router.get('')
def listing(request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    return list_projects(request.app.state.data_dir, limit, offset)


@router.delete('/{project_id}')
def delete(request: Request, project_id: str):
    try:
        deleted = delete_project(request.app.state.data_dir, project_id)
    except database.ActiveTaskError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        logger.exception('Project deletion failed')
        raise HTTPException(500, '删除项目失败，请稍后重试') from exc
    if not deleted:
        raise HTTPException(404, '项目不存在或已删除')
    return dict(id=project_id, deleted=True)
