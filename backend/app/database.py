"""SQLite metadata repository. File bytes live outside the database."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connection(root: Path):
    db = sqlite3.connect(root / "office_agent.db", timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


class ActiveTaskError(Exception):
    pass


def initialize(root: Path):
    (root / "uploads").mkdir(parents=True, exist_ok=True)
    with connection(root) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, size INTEGER NOT NULL,
            storage_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
        )""")

        columns = {row[1] for row in db.execute("PRAGMA table_info(files)")}
        if "source_file_id" not in columns:
            db.execute("ALTER TABLE files ADD COLUMN source_file_id TEXT")
        from .history import migrate
        migrate(db)
        from .projects import migrate as migrate_projects
        migrate_projects(db)
        from .conversations import migrate as migrate_conversations
        migrate_conversations(db)


def insert(root: Path, record: dict):
    with connection(root) as db:
        insert_record(db, record)


def insert_record(db, record):
    from .history import register
    if record.get('source_file_id') and not db.execute('SELECT 1 FROM files WHERE id=?', (record['source_file_id'],)).fetchone():
        raise sqlite3.IntegrityError('Source file no longer exists')
    db.execute(
        "INSERT INTO files (id, name, size, storage_key, created_at, source_file_id) VALUES (:id, :name, :size, :storage_key, :created_at, :source_file_id)",
        {"source_file_id": None, **record},
    )
    register(db, record)
    from .projects import register as register_project
    register_project(db, record)
    from .conversations import project_for
    project_id = project_for(db, record['id'])
    db.execute('INSERT OR IGNORE INTO conversations(project_id,current_file_id) VALUES (?,?)', (project_id, record['id']))
    db.execute('UPDATE conversations SET current_file_id=? WHERE project_id=?', (record['id'], project_id))


def list_files(root: Path, limit: int, offset: int):
    with connection(root) as db:
        items = db.execute(
            "SELECT * FROM files ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        total = db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        return {"items": [dict(item) for item in items], "total": total}


def get(root: Path, file_id: str):
    with connection(root) as db:
        row = db.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return dict(row) if row else None


def delete_file(root: Path, file_id: str, project_id: str | None = None):
    """Delete metadata and bytes; restore bytes if the transaction fails."""
    original = None
    path = None
    removed = False
    try:
        with connection(root) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM tasks WHERE json_extract(payload, '$.status') IN ('queued','running','cancelling') LIMIT 1").fetchone():
                raise ActiveTaskError('任务执行期间不能删除文件，请等待结束或停止后重试')
            if project_id is not None:
                source = db.execute('SELECT * FROM project_sources WHERE project_id=? AND source_file_id=? AND deleted=0', (project_id, file_id)).fetchone()
                if source is None:
                    return False
                db.execute('UPDATE project_sources SET deleted=1,current_file_id=NULL WHERE source_file_id=?', (file_id,))
                db.execute('UPDATE conversations SET current_file_id=NULL WHERE project_id=? AND current_file_id=?', (project_id, source['current_file_id']))
            row = db.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            if row is None:
                return project_id is not None
            path = root / "uploads" / row["storage_key"]
            if path.exists():
                original = path.read_bytes()
            db.execute("DELETE FROM files WHERE id = ?", (file_id,))
            if original is not None:
                path.unlink()
                removed = True
        return True
    except Exception:
        if removed and path is not None and original is not None:
            path.write_bytes(original)
        raise
