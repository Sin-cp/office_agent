"""Durable conversations with zero or more independent source-file families."""
import json
import logging
import os
import shutil
import sqlite3
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timezone

from . import database

router = APIRouter(prefix='/api/projects', tags=['projects'])
logger = logging.getLogger(__name__)


def title(prompt):
    text = prompt.strip()
    return text[:20] + ('…' if len(text) > 20 else '')


def create(db, identity=None):
    identity = identity or str(uuid4())
    db.execute("INSERT INTO projects(id,name,created_at,title_initialized) VALUES (?,?,?,0)",
               (identity, '新项目', datetime.now(timezone.utc).isoformat()))
    db.execute('INSERT INTO conversations(project_id) VALUES (?)', (identity,))
    return identity


def name_from_prompt(db, project_id, prompt):
    db.execute('UPDATE projects SET name=?,title_initialized=1 WHERE id=? AND title_initialized=0',
               (title(prompt), project_id))


def register(db, record):
    parent = record.get('source_file_id')
    if parent:
        db.execute('UPDATE project_sources SET current_file_id=? WHERE current_file_id=?', (record['id'], parent))
        return
    project_id = record.get('project_id')
    if not project_id:
        project_id = create(db, record['id'])
        db.execute('UPDATE projects SET created_at=? WHERE id=?', (record['created_at'],project_id))
    db.execute('INSERT INTO project_sources(project_id,source_file_id,current_file_id,family_id,origin) VALUES (?,?,?,?,?)',
               (project_id, record['id'], record['id'], record['id'], record.get('origin', 'uploaded')))
    db.execute('UPDATE projects SET source_file_id=COALESCE(source_file_id,?) WHERE id=?', (record['id'], project_id))


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
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='project_sources'").fetchone() and db.execute('SELECT 1 FROM project_sources s JOIN projects p ON p.id=s.project_id WHERE s.family_id=?', (group,)).fetchone():
            continue
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
        db.execute('INSERT INTO projects(id,source_file_id,name,size,created_at) VALUES (?,?,?,?,?)', (
            group, identity, source['name'] if source else '源文件不可用（历史项目）',
            source.get('size') if source else None, source.get('created_at') if source else None))

    columns = {r[1] for r in db.execute('PRAGMA table_info(projects)')}
    if 'title_initialized' not in columns:
        db.execute('ALTER TABLE projects ADD COLUMN title_initialized INTEGER NOT NULL DEFAULT 0')
    db.execute('CREATE TABLE IF NOT EXISTS project_sources(project_id TEXT NOT NULL, source_file_id TEXT PRIMARY KEY, current_file_id TEXT, family_id TEXT NOT NULL)')
    if 'deleted' not in {r[1] for r in db.execute('PRAGMA table_info(project_sources)')}:
        db.execute('ALTER TABLE project_sources ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
    if 'origin' not in {r[1] for r in db.execute('PRAGMA table_info(project_sources)')}:
        db.execute("ALTER TABLE project_sources ADD COLUMN origin TEXT NOT NULL DEFAULT 'uploaded'")
    db.execute('CREATE INDEX IF NOT EXISTS project_sources_project ON project_sources(project_id)')
    for row in db.execute('SELECT * FROM projects').fetchall():
        if row['source_file_id']:
            current = None
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='conversations'").fetchone():
                current = db.execute('SELECT current_file_id FROM conversations WHERE project_id=?', (row['id'],)).fetchone()
            latest = db.execute('SELECT f.id FROM files f JOIN file_history h ON f.id=h.file_id WHERE h.history_id=? ORDER BY f.created_at DESC,f.id DESC LIMIT 1', (row['source_file_id'],)).fetchone()
            db.execute('INSERT OR IGNORE INTO project_sources(project_id,source_file_id,current_file_id,family_id) VALUES (?,?,?,?)',
                       (row['id'], row['source_file_id'], (current[0] if current and current[0] else latest[0] if latest else None), row['id']))
        if not row['title_initialized'] and db.execute("SELECT 1 FROM sqlite_master WHERE name='tasks'").fetchone():
            first = db.execute("SELECT payload FROM tasks WHERE json_extract(payload,'$.project_id')=? OR json_extract(payload,'$.file.id') IN (SELECT file_id FROM file_history WHERE history_id IN (SELECT family_id FROM project_sources WHERE project_id=?)) ORDER BY json_extract(payload,'$.started_at'),id LIMIT 1", (row['id'],row['id'])).fetchone()
            if first:
                name_from_prompt(db, row['id'], json.loads(first[0])['prompt'])


def source_files(root, db, project_id):
    rows = db.execute('SELECT s.*,f.name,f.size,f.created_at,f.storage_key FROM project_sources s LEFT JOIN files f ON f.id=s.source_file_id WHERE s.project_id=? AND s.deleted=0 ORDER BY f.created_at,s.source_file_id', (project_id,)).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        key = item.pop('storage_key')
        item.update(id=item['source_file_id'], name=item['name'] or '源文件不可用', available=bool(key and (root/'uploads'/key).is_file()))
        current = db.execute('SELECT * FROM files WHERE id=?', (item['current_file_id'],)).fetchone()
        item['current_file'] = None
        if current:
            from .files import FileInfo
            item['current_file'] = {**FileInfo(**dict(current)).model_dump(), 'available': (root/'uploads'/current['storage_key']).is_file()}
        items.append(item)
    return items


def list_projects(root, limit, offset):
    with database.connection(root) as db:
        rows = db.execute('SELECT * FROM projects ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
        items = []
        for row in rows:
            sources = source_files(root, db, row['id'])
            items.append({**dict(row), 'file_count': len(sources), 'source_available': any(f['available'] for f in sources)})
        return dict(items=items, total=db.execute('SELECT COUNT(*) FROM projects').fetchone()[0])


@router.post('', status_code=201)
def new_project(request: Request):
    with database.connection(request.app.state.data_dir) as db:
        identity = create(db)
    return dict(id=identity, name='新项目')


class Rename(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator('name')
    @classmethod
    def nonempty(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError('请输入有效的项目名称')
        return value.strip()


@router.patch('/{project_id}')
def rename(request: Request, project_id: str, change: Rename):
    with database.connection(request.app.state.data_dir) as db:
        from .conversations import ensure_project
        ensure_project(db, project_id)
        db.execute('UPDATE projects SET name=?,title_initialized=1 WHERE id=?', (change.name, project_id))
    return dict(id=project_id, name=change.name)


@router.get('/{project_id}/files')
def sources(request: Request, project_id: str):
    with database.connection(request.app.state.data_dir) as db:
        from .conversations import ensure_project
        ensure_project(db, project_id)
        return dict(items=source_files(request.app.state.data_dir, db, project_id))


@router.get('/{project_id}/history')
def project_history(request: Request, project_id: str, limit: int = Query(20, ge=1, le=100), before: str | None = None):
    from .history import read_project
    return read_project(request.app.state.data_dir, project_id, limit, before)


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
                ON h.file_id=f.id WHERE h.history_id IN (SELECT family_id FROM project_sources WHERE project_id=?)''', (project_id,)).fetchall()
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
            db.execute("DELETE FROM tasks WHERE json_extract(payload, '$.project_id')=? OR json_extract(payload, '$.file.id') IN (SELECT file_id FROM file_history WHERE history_id IN (SELECT family_id FROM project_sources WHERE project_id=?))", (project_id,project_id))
            db.execute('DELETE FROM files WHERE id IN (SELECT file_id FROM file_history WHERE history_id IN (SELECT family_id FROM project_sources WHERE project_id=?))', (project_id,))
            db.execute('DELETE FROM file_history WHERE history_id IN (SELECT family_id FROM project_sources WHERE project_id=?)', (project_id,))
            db.execute('DELETE FROM messages WHERE project_id=?', (project_id,))
            db.execute('DELETE FROM conversations WHERE project_id=?', (project_id,))
            db.execute('DELETE FROM project_sources WHERE project_id=?', (project_id,))
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


@router.delete('/{project_id}/files/{file_id}')
def delete_source(request: Request, project_id: str, file_id: str):
    try:
        deleted = database.delete_file(request.app.state.data_dir, file_id, project_id=project_id)
    except database.ActiveTaskError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        logger.exception('Source deletion failed')
        raise HTTPException(500, '删除文件失败，请稍后重试') from exc
    if not deleted:
        raise HTTPException(404, '源文件不存在或已删除')
    return dict(id=file_id, deleted=True)
