"""Stable version-family membership survives deletion of individual files."""
import json
import time

from fastapi import HTTPException

from . import database


def migrate(db):
    db.execute('CREATE TABLE IF NOT EXISTS file_history (file_id TEXT PRIMARY KEY, history_id TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS file_history_group ON file_history(history_id)')
    known = {row['file_id']: row['history_id'] for row in db.execute('SELECT * FROM file_history')}
    records = {row['id']: dict(row) for row in db.execute('SELECT * FROM files')}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
        for row in db.execute('SELECT payload FROM tasks'):
            task = json.loads(row['payload'])
            source = task.get('file')
            result = (task.get('result') or {}).get('file')
            if source:
                records.setdefault(source['id'], source)
            if result:
                records.setdefault(result['id'], {**result, 'source_file_id': source['id'] if source else None})
    # Iterative ancestry walk handles old databases without recursion limits.
    for file_id in sorted(records):
        if file_id in known:
            continue
        path, seen, current = [], set(), file_id
        while current in records and current not in known and current not in seen:
            path.append(current)
            seen.add(current)
            parent = records[current].get('source_file_id')
            if not parent or parent not in records and parent not in known:
                break
            current = parent
        group = known.get(current) or (min(seen) if current in seen and current != path[-1] else path[-1])
        for item in path:
            known[item] = group
            db.execute('INSERT OR IGNORE INTO file_history VALUES (?, ?)', (item, group))


def register(db, record):
    parent = db.execute('SELECT history_id FROM file_history WHERE file_id=?', (record.get('source_file_id'),)).fetchone()
    db.execute('INSERT OR IGNORE INTO file_history VALUES (?, ?)', (record['id'], parent['history_id'] if parent else record['id']))


def task_view(root, db, state):
    state['elapsed_seconds'] = round(max(0, (state['finished_at'] or time.time()) - state['started_at']), 1)
    for file in [state.get('file'), (state.get('result') or {}).get('file'), *state.get('files', []), *[o['file'] for o in (state.get('result') or {}).get('outputs', [])]]:
        if file:
            row = db.execute('SELECT storage_key FROM files WHERE id=?', (file['id'],)).fetchone()
            file['available'] = bool(row and (root / 'uploads' / row['storage_key']).is_file())
    return state


def read(root, file_id, limit, before):
    from .files import FileInfo
    with database.connection(root) as db:
        group = db.execute('SELECT history_id FROM file_history WHERE file_id=?', (file_id,)).fetchone()
        if not group:
            raise HTTPException(404, '文件或历史记录不存在')
        group_id = group['history_id']
        base = "FROM tasks t JOIN file_history h ON h.file_id=json_extract(t.payload, '$.file.id') WHERE h.history_id=?"
        total = db.execute('SELECT COUNT(*) ' + base, (group_id,)).fetchone()[0]
        params = [group_id]
        query = base
        if before:
            cursor = db.execute('SELECT t.id, t.payload ' + base + ' AND t.id=?', (group_id, before)).fetchone()
            if not cursor:
                raise HTTPException(400, '历史分页位置无效，请重新加载')
            started = json.loads(cursor['payload'])['started_at']
            query += " AND (json_extract(t.payload, '$.started_at'), t.id) < (?, ?)"
            params.extend([started, before])
        rows = db.execute('SELECT t.id,t.payload ' + query + " ORDER BY json_extract(t.payload, '$.started_at') DESC,t.id DESC LIMIT ?", (*params, limit + 1)).fetchall()
        page = rows[:limit]
        items = [task_view(root, db, json.loads(row['payload'])) for row in reversed(page)]
        active = db.execute('SELECT t.payload ' + base + " AND json_extract(t.payload, '$.status') IN ('queued','running','cancelling') LIMIT 1", (group_id,)).fetchone()
        from .conversations import current as current_version
        pointed = current_version(db, root, group_id)
        current = FileInfo(**pointed).model_dump() if pointed else None
        active_task = task_view(root, db, json.loads(active['payload'])) if active else None
        if active_task and active_task['file']['available']:
            current = active_task['file']
        return dict(history_id=group_id, items=items, total=total,
                    next_cursor=page[-1]['id'] if len(rows) > limit else None,
                    current_file=current, active_task=active_task)


def read_project(root, project_id, limit, before):
    from .conversations import ensure_project
    from .projects import source_files
    with database.connection(root) as db:
        ensure_project(db, project_id)
        base = "FROM tasks WHERE json_extract(payload,'$.project_id')=? OR json_extract(payload,'$.file.id') IN (SELECT file_id FROM file_history WHERE history_id IN (SELECT family_id FROM project_sources WHERE project_id=?))"
        # Wrap the OR before appending pagination/status predicates.
        base = base.replace('WHERE ', 'WHERE (', 1) + ')'
        total = db.execute('SELECT COUNT(*) ' + base, (project_id, project_id)).fetchone()[0]
        params = [project_id, project_id]
        query = base
        if before:
            cursor = db.execute('SELECT payload ' + base + ' AND id=?', (*params, before)).fetchone()
            if not cursor:
                raise HTTPException(400, '历史分页位置无效')
            started = json.loads(cursor[0])['started_at']
            query += " AND (json_extract(payload,'$.started_at'),id) < (?,?)"
            params.extend([started, before])
        rows = db.execute('SELECT payload ' + query + " ORDER BY json_extract(payload,'$.started_at') DESC,id DESC LIMIT ?", (*params, limit+1)).fetchall()
        page = [json.loads(r[0]) for r in rows[:limit]]
        active_row = db.execute('SELECT payload ' + base + " AND json_extract(payload,'$.status') IN ('queued','running','cancelling') LIMIT 1", (project_id,project_id)).fetchone()
        active = json.loads(active_row[0]) if active_row else None
        sources = source_files(root, db, project_id)
        return dict(history_id=project_id, items=[task_view(root,db,s) for s in reversed(page)], total=total,
                    next_cursor=page[-1]['id'] if len(rows)>limit else None,
                    current_file=None, sources=sources, active_task=task_view(root,db,active) if active else None)
