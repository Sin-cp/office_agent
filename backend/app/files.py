"""File HTTP routes; validation, storage and metadata remain separate."""

import logging
import sqlite3
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import database
from .storage import MAX_BYTES, validate_docx

router = APIRouter(prefix="/api/files", tags=["files"])
logger = logging.getLogger(__name__)


class FileInfo(BaseModel):
    source_file_id: str | None = None
    id: str
    name: str
    size: int
    created_at: str


class FileList(BaseModel):
    items: list[FileInfo]
    total: int


@router.post("", response_model=FileInfo, status_code=201)
def upload_file(request: Request, file: UploadFile, project_id: str | None = None):
    root = request.app.state.data_dir
    name = (file.filename or "").replace("\\", "/").split("/")[-1]
    file_id = str(uuid4())
    key = f"{file_id}.docx"
    temporary = root / "uploads" / f"{file_id}.tmp"
    destination = root / "uploads" / key
    committed = False
    try:
        if (
            not name.lower().endswith(".docx")
            or any(ord(c) < 32 for c in name)
            or len(name) > 255
        ):
            raise HTTPException(400, "仅支持文件名合法的 .docx 文件")
        size = 0
        with temporary.open("xb") as output:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "文件不能超过 10 MiB")
                output.write(chunk)
        if size == 0:
            raise HTTPException(400, "不能上传空文件")
        try:
            validate_docx(temporary)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        record = dict(
            id=file_id,
            name=name,
            size=size,
            storage_key=key,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        temporary.replace(destination)
        if project_id:
            with database.connection(root) as db:
                db.execute('BEGIN IMMEDIATE')
                from .conversations import ensure_project
                ensure_project(db, project_id)
                if db.execute("SELECT 1 FROM tasks WHERE json_extract(payload,'$.status') IN ('queued','running','cancelling')").fetchone():
                    raise HTTPException(409, '任务执行期间不能添加文件')
                database.insert_record(db, {**record, 'project_id': project_id})
        else:
            database.insert(root, record)
        committed = True
        return record
    except (OSError, sqlite3.Error) as exc:
        logger.exception("File persistence failed")
        raise HTTPException(500, "文件保存失败，请稍后重试") from exc
    finally:
        file.file.close()
        temporary.unlink(missing_ok=True)
        if not committed:
            destination.unlink(missing_ok=True)


@router.get("", response_model=FileList)
def list_files(
    request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
):
    return database.list_files(request.app.state.data_dir, limit, offset)


@router.get("/{file_id}/download")
def download_file(request: Request, file_id: str):
    try:
        UUID(file_id)
    except ValueError:
        raise HTTPException(404, "文件不存在")
    root = request.app.state.data_dir
    record = database.get(root, file_id)
    if not record:
        raise HTTPException(404, "文件不存在")
    path = root / "uploads" / record["storage_key"]
    if not path.is_file():
        raise HTTPException(404, "文件已丢失或不可用")
    return FileResponse(
        path,
        filename=record["name"],
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.delete("/{file_id}")
def delete_file(request: Request, file_id: str):
    try:
        UUID(file_id)
    except ValueError:
        raise HTTPException(404, "文件不存在")
    try:
        deleted = database.delete_file(request.app.state.data_dir, file_id)
    except database.ActiveTaskError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        logger.exception("File deletion failed")
        raise HTTPException(500, "删除失败，请稍后重试") from exc
    if not deleted:
        raise HTTPException(404, "文件不存在或已删除")
    return {"id": file_id, "deleted": True}


@router.get("/{file_id}/history")
def history(request: Request, file_id: str, limit: int = Query(20, ge=1, le=100), before: str | None = Query(None, max_length=100)):
    from .history import read
    return read(request.app.state.data_dir, file_id, limit, before)
