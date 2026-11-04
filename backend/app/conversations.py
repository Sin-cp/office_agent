"""Project-scoped durable conversation, explicit current version and memory."""
import json
import time
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from . import database

router = APIRouter(prefix='/api/projects', tags=['conversations'])


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS conversations (
        project_id TEXT PRIMARY KEY, current_file_id TEXT, summary TEXT NOT NULL DEFAULT '',
        summary_through INTEGER NOT NULL DEFAULT 0, preferences TEXT NOT NULL DEFAULT '[]')''')
    db.execute('''CREATE TABLE IF NOT EXISTS messages (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, project_id TEXT NOT NULL,
        task_id TEXT, role TEXT NOT NULL, content TEXT NOT NULL, file_id TEXT, created_at REAL NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS messages_project ON messages(project_id, seq)')
    for row in db.execute('SELECT id FROM projects').fetchall():
        latest = db.execute('SELECT f.id FROM files f JOIN file_history h ON h.file_id=f.id WHERE h.history_id=? ORDER BY f.created_at DESC,f.id DESC LIMIT 1', (row['id'],)).fetchone()
        db.execute('INSERT OR IGNORE INTO conversations(project_id,current_file_id) VALUES (?,?)', (row['id'], latest['id'] if latest else None))
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='tasks'").fetchone():
        for row in db.execute("SELECT payload FROM tasks ORDER BY json_extract(payload,'$.started_at'),id").fetchall():
            record_task(db, json.loads(row['payload']))


def project_for(db, file_id):
    row = db.execute('SELECT s.project_id AS history_id FROM file_history h JOIN project_sources s ON s.family_id=h.history_id WHERE h.file_id=?', (file_id,)).fetchone()
    return row['history_id'] if row else None


def current(db, root, project_id):
    row = db.execute('SELECT current_file_id FROM conversations WHERE project_id=?', (project_id,)).fetchone()
    if row and row['current_file_id']:
        file = db.execute('SELECT * FROM files WHERE id=?', (row['current_file_id'],)).fetchone()
        if file and (root / 'uploads' / file['storage_key']).is_file():
            return dict(file)
    rows = db.execute('SELECT f.* FROM files f JOIN file_history h ON h.file_id=f.id JOIN project_sources s ON s.family_id=h.history_id WHERE s.project_id=? AND s.deleted=0 ORDER BY f.created_at DESC,f.id DESC', (project_id,)).fetchall()
    return next((dict(f) for f in rows if (root / 'uploads' / f['storage_key']).is_file()), None)


def message(db, identity, project_id, task_id, role, content, file_id, created):
    db.execute('INSERT OR IGNORE INTO messages(id,project_id,task_id,role,content,file_id,created_at) VALUES (?,?,?,?,?,?,?)',
               (identity, project_id, task_id, role, json.dumps(content, ensure_ascii=False), file_id, created))


def record_task(db, state):
    source = (state.get('file') or {}).get('id')
    project_id = state.get('project_id') or project_for(db, source)
    if not project_id or not state.get('prompt'):
        return
    message(db, state['id'] + ':user', project_id, state['id'], 'user', {'text': state['prompt']}, source, state['started_at'])
    if state['status'] in ('completed', 'failed', 'cancelled'):
        result = state.get('result') or {}
        content = dict(status=state['status'], kind=result.get('kind', 'edit'),
                       text=result.get('text') or result.get('explanation') or state.get('error') or '已停止，未应用修改',
                       changes=result.get('changes', []), citations=result.get('citations', []),
                       output_file_id=(result.get('file') or {}).get('id'), outputs=result.get('outputs', []), web_sources=result.get('web_sources', []),
                       web_status=result.get('web_status'), web_notices=result.get('web_notices', []))
        if 'before' in result and not content['changes']:
            content['changes'] = [dict(before=result['before'], after=result['after'], location=f"正文第{result.get('position', '?')}段")]
        message(db, state['id'] + ':assistant', project_id, state['id'], 'assistant', content, source, state.get('finished_at') or time.time())


def context(root, project_id, file_id):
    with database.connection(root) as db:
        memory = db.execute('SELECT * FROM conversations WHERE project_id=?', (project_id,)).fetchone()
        rows = db.execute('SELECT * FROM messages WHERE project_id=? ORDER BY seq', (project_id,)).fetchall()
    through = memory['summary_through'] if memory else 0
    return dict(project_id=project_id, current_file_id=file_id,
                summary=memory['summary'] if memory else '',
                preferences=json.loads(memory['preferences']) if memory else [],
                messages=[dict(seq=r['seq'], role=r['role'], content=json.loads(r['content']), file_id=r['file_id']) for r in rows if r['seq'] > through])


def store_memory(root, project_id, summary, through, preferences):
    with database.connection(root) as db:
        db.execute('UPDATE conversations SET summary=?,summary_through=?,preferences=? WHERE project_id=?',
                   (summary, through, json.dumps(preferences, ensure_ascii=False), project_id))


def ensure_project(db, project_id):
    if not db.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
        raise HTTPException(404, '项目不存在')


@router.get('/{project_id}/messages')
def messages(request: Request, project_id: str, before: int | None = None, limit: int = Query(40, ge=1, le=100)):
    with database.connection(request.app.state.data_dir) as db:
        ensure_project(db, project_id)
        rows = db.execute('SELECT * FROM messages WHERE project_id=? AND seq<? ORDER BY seq DESC LIMIT ?', (project_id, before or 9223372036854775807, limit+1)).fetchall()
        items = [{**dict(r), 'content': json.loads(r['content'])} for r in rows[:limit]]
        return dict(items=list(reversed(items)), next_cursor=items[-1]['seq'] if len(rows)>limit else None)


@router.get('/{project_id}/versions')
def versions(request: Request, project_id: str):
    from .files import FileInfo
    root = request.app.state.data_dir
    with database.connection(root) as db:
        ensure_project(db, project_id)
        selected = current(db, root, project_id)
        rows = db.execute('SELECT f.* FROM files f JOIN file_history h ON h.file_id=f.id JOIN project_sources s ON s.family_id=h.history_id WHERE s.project_id=? AND s.deleted=0 ORDER BY f.created_at DESC,f.id DESC', (project_id,)).fetchall()
        return dict(current_file_id=selected['id'] if selected else None,
                    items=[{**FileInfo(**dict(r)).model_dump(), 'available': (root / 'uploads' / r['storage_key']).is_file()} for r in rows])


class Restore(BaseModel):
    file_id: str
    expected_current_file_id: str


@router.post('/{project_id}/restore', deprecated=True)
def restore(request: Request, project_id: str, change: Restore):
    raise HTTPException(410, '版本恢复功能已停用')
