"""Image inputs for a project.

Images are evidence, not document versions, so they get their own table and
never enter the file/version family. Format is decided by content, not by the
client's filename or declared MIME type.
"""

import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import database

router = APIRouter(prefix="/api", tags=["images"])
logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES_PER_PROJECT = 200

SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)


class AssetInfo(BaseModel):
    id: str
    name: str
    mime: str
    size: int
    created_at: str


class AssetList(BaseModel):
    items: list[AssetInfo]
    total: int


def detect_image(data: bytes):
    """Return (extension, mime) from magic bytes, or None."""
    if len(data) < 12:
        return None
    for signature, extension, mime in SIGNATURES:
        if data.startswith(signature):
            return extension, mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _clean_name(raw: str, extension: str) -> str:
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ord(ch) >= 32).strip()
    if not name:
        name = "图片"
    if len(name) > 120:
        stem = name.rsplit(".", 1)[0][:110]
        name = f"{stem}.{extension}"
    elif "." not in name:
        name = f"{name}.{extension}"
    return name


@router.post("/projects/{project_id}/images", response_model=AssetInfo, status_code=201)
def upload_image(request: Request, project_id: str, file: UploadFile):
    root = request.app.state.data_dir
    identity = str(uuid4())
    temporary = root / "uploads" / f"img-{identity}.tmp"
    committed = False
    size = 0
    try:
        with temporary.open("xb") as output:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    raise HTTPException(413, "图片不能超过 5 MiB")
                output.write(chunk)
        if size == 0:
            raise HTTPException(400, "不能上传空图片")
        with temporary.open("rb") as stream:
            head = stream.read(32)
        detected = detect_image(head)
        if detected is None:
            raise HTTPException(400, "仅支持 PNG、JPEG、GIF、WebP 图片")
        extension, mime = detected
        key = f"{identity}.{extension}"
        record = dict(
            id=identity,
            project_id=project_id,
            name=_clean_name(file.filename or "", extension),
            mime=mime,
            size=size,
            storage_key=key,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with database.connection(root) as db:
            db.execute("BEGIN IMMEDIATE")
            from .conversations import ensure_project

            ensure_project(db, project_id)
            if db.execute(
                "SELECT 1 FROM tasks WHERE json_extract(payload,'$.status') "
                "IN ('queued','running','cancelling')"
            ).fetchone():
                raise HTTPException(409, "任务执行期间不能添加图片")
            count = db.execute(
                "SELECT COUNT(*) FROM assets WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            if count >= MAX_IMAGES_PER_PROJECT:
                raise HTTPException(413, "项目图片数量已达上限")
            db.execute(
                "INSERT INTO assets(id,project_id,name,mime,size,storage_key,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    record["id"],
                    record["project_id"],
                    record["name"],
                    record["mime"],
                    record["size"],
                    record["storage_key"],
                    record["created_at"],
                ),
            )
        temporary.replace(root / "uploads" / key)
        committed = True
        return {key: record[key] for key in ("id", "name", "mime", "size", "created_at")}
    finally:
        if not committed:
            temporary.unlink(missing_ok=True)


@router.get("/projects/{project_id}/images", response_model=AssetList)
def list_images(request: Request, project_id: str):
    root = request.app.state.data_dir
    with database.connection(root) as db:
        from .conversations import ensure_project

        ensure_project(db, project_id)
        rows = db.execute(
            "SELECT * FROM assets WHERE project_id=? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
    items = [
        AssetInfo(
            id=row["id"],
            name=row["name"],
            mime=row["mime"],
            size=row["size"],
            created_at=row["created_at"],
        )
        for row in rows
        if (root / "uploads" / row["storage_key"]).is_file()
    ]
    return AssetList(items=items, total=len(items))


@router.get("/images/{image_id}/download")
def download_image(request: Request, image_id: str):
    root = request.app.state.data_dir
    with database.connection(root) as db:
        row = db.execute("SELECT * FROM assets WHERE id=?", (image_id,)).fetchone()
    if not row:
        raise HTTPException(404, "图片不存在")
    path = root / "uploads" / row["storage_key"]
    if not path.is_file():
        raise HTTPException(404, "图片已丢失")
    return FileResponse(path, media_type=row["mime"], filename=row["name"])


def load_asset(root, image_id: str) -> dict:
    """Read one asset record plus its bytes. Raises HTTPException when missing."""
    with database.connection(root) as db:
        row = db.execute("SELECT * FROM assets WHERE id=?", (image_id,)).fetchone()
    if not row:
        raise HTTPException(404, "图片不存在")
    path = root / "uploads" / row["storage_key"]
    if not path.is_file():
        raise HTTPException(404, "图片已丢失")
    return {**dict(row), "path": path, "data": path.read_bytes()}
