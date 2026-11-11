"""Read and replace one explicitly selected paragraph; never execute AI code."""
import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from . import database, document_service as service, naming
from .files import FileInfo
from .storage import MAX_BYTES, validate_docx

router = APIRouter(prefix='/api/files', tags=['documents'])
logger = logging.getLogger(__name__)


class EditRequest(BaseModel):
    block_id: str
    revision: str
    expected_text: str
    new_text: str = Field(max_length=20000)

    @field_validator('new_text')
    @classmethod
    def valid_xml_text(cls, value):
        if any(not (c in '\t\n\r' or 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in value):
            raise ValueError('文字包含不支持的控制字符')
        return value.replace('\r\n', '\n').replace('\r', '\n')


def load_file(root, file_id):
    record = database.get(root, file_id)
    if not record:
        raise HTTPException(404, '文件不存在')
    path = root / 'uploads' / record['storage_key']
    if not path.is_file():
        raise HTTPException(404, '文件已丢失或不可用')
    try:
        validate_docx(path)
        data, document, revision = service.read_document(path)
        items = service.blocks(document, file_id)
    except Exception as exc:
        logger.warning('Document could not be read: %s', type(exc).__name__)
        raise HTTPException(422, '暂时无法解析此文档，请使用标准 DOCX 文档') from exc
    return root, record, data, document, revision, items


def load(request, file_id):
    return load_file(request.app.state.data_dir, file_id)


def family_names(root, file_id):
    """Published names of this file's version family, for numbering new versions."""
    with database.connection(root) as db:
        row = db.execute('SELECT history_id FROM file_history WHERE file_id=?', (file_id,)).fetchone()
        if not row:
            return []
        rows = db.execute('SELECT f.name FROM files f JOIN file_history h ON h.file_id=f.id WHERE h.history_id=?',
                          (row['history_id'],)).fetchall()
    return [item['name'] for item in rows]


def result_name(root, record, name=None):
    """An explicit rename wins; otherwise the next numbered ``_修改版N`` name."""
    if name:
        return naming.safe_name(name)
    return naming.versioned_name(record['name'], family_names(root, record['id']))


@router.get('/{file_id}/content')
def content(request: Request, file_id: str):
    _, record, _, document, revision, items = load(request, file_id)
    from . import operations
    ordered, _ = operations.inventory(document, file_id)
    return dict(file=FileInfo(**record), revision=revision, blocks=items,
                ordered_blocks=ordered, coverage=operations.coverage(document), table_count=len(document.tables))


@router.post('/{file_id}/edits', response_model=FileInfo, status_code=201)
def edit(request: Request, file_id: str, change: EditRequest):
    return apply_edit(request.app.state.data_dir, file_id, change)


def apply_edit(root, file_id, change):
    prepared = prepare_edit(root, file_id, change)
    return publish_edit(root, prepared)


def prepare_edit(root, file_id, change, name=None):
    root, record, data, document, revision, items = load_file(root, file_id)
    if change.revision != revision:
        raise HTTPException(409, '文档已变化，请重新读取后修改')
    selected = next((item for item in items if item['id'] == change.block_id), None)
    if selected is None:
        raise HTTPException(404, '所选位置不属于此文件或不存在')
    if selected['text'] != change.expected_text:
        raise HTTPException(409, '原文不匹配，请重新读取后修改')
    if not selected['editable']:
        raise HTTPException(422, selected['reason'])
    if change.new_text == selected['text']:
        raise HTTPException(400, '文字没有变化，无需生成新文件')
    new_id = str(uuid4())
    temporary = root / 'uploads' / f'{new_id}.tmp'
    destination = root / 'uploads' / f'{new_id}.docx'
    committed = False
    try:
        paragraph = document.paragraphs[selected['position'] - 1]
        service.write_replacement(data, document, paragraph, change.new_text, temporary)
        validate_docx(temporary)
        if temporary.stat().st_size > MAX_BYTES:
            raise HTTPException(413, '生成文件超过 10 MiB 限制')
        result = dict(id=new_id, name=result_name(root, record, name),
                      size=temporary.stat().st_size, storage_key=destination.name,
                      created_at=datetime.now(timezone.utc).isoformat(), source_file_id=file_id)
        committed = True
        return temporary, destination, result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception('Document generation failed')
        raise HTTPException(500, '生成文件失败，请稍后重试') from exc
    finally:
        if not committed:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)


def publish_edit(root, prepared, persist=None):
    temporary, destination, result = prepared
    try:
        temporary.replace(destination)
        if persist is None:
            database.insert(root, result)
        else:
            persist(result)
        return result
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        logger.exception('Document publication failed')
        raise HTTPException(500, '生成文件失败，请稍后重试') from exc


def prepare_batch(root, file_id, revision, operations, name=None):
    from . import operations as engine
    root, record, data, document, actual, _ = load_file(root, file_id)
    if actual != revision:
        raise HTTPException(409, '文档已变化，请重新读取后修改')
    identity = str(uuid4())
    temporary = root / 'uploads' / f'task-{identity}.tmp'
    destination = root / 'uploads' / f'{identity}.docx'
    try:
        changes = engine.apply(document, file_id, operations)
        engine.write(data, document, temporary)
        if temporary.read_bytes() == data:
            raise HTTPException(400, '文字没有变化，无需生成新文件')
        validate_docx(temporary)
        # Parse generated OOXML before publishing, not just ZIP structure.
        service.read_document(temporary)
        if temporary.stat().st_size > MAX_BYTES:
            raise HTTPException(413, '生成文件超过 10 MiB 限制')
        record = dict(id=identity, name=result_name(root, record, name),
                      size=temporary.stat().st_size, storage_key=destination.name,
                      created_at=datetime.now(timezone.utc).isoformat(), source_file_id=file_id)
        return (temporary, destination, record), changes
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            raise HTTPException(422, str(exc)) from exc
        raise


def prepare_image(root, file_id, after_block_id, image_path, width_cm=12.0, caption=''):
    """Deterministic image insertion: the caller picks the position, not the model's judgement.

    python-docx must rewrite the whole package here (an image adds a media part),
    so this path saves the document instead of patching word/document.xml.
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm
    from . import operations as engine

    root, record, data, document, revision, _ = load_file(root, file_id)
    identity = str(uuid4())
    temporary = root / 'uploads' / f'task-{identity}.tmp'
    destination = root / 'uploads' / f'{identity}.docx'
    try:
        anchor = None
        if after_block_id:
            _, refs = engine.inventory(document, file_id)
            entry = refs.get(after_block_id)
            if entry is None:
                raise HTTPException(400, '插图位置不存在或已变化')
            block, target = entry
            if block['kind'] != 'document_end':
                anchor = target
        paragraph = document.add_paragraph()
        run = paragraph.add_run()
        run.add_picture(str(image_path), width=Cm(width_cm))
        caption_paragraph = None
        if caption.strip():
            caption_paragraph = document.add_paragraph(caption.strip())
            caption_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if anchor is not None:
            anchor._element.addnext(paragraph._p)
            if caption_paragraph is not None:
                paragraph._p.addnext(caption_paragraph._p)
        document.save(temporary)
        validate_docx(temporary)
        service.read_document(temporary)
        if temporary.stat().st_size > MAX_BYTES:
            raise HTTPException(413, '生成文件超过 10 MiB 限制')
        record = dict(id=identity, name=result_name(root, record),
                      size=temporary.stat().st_size, storage_key=destination.name,
                      created_at=datetime.now(timezone.utc).isoformat(), source_file_id=file_id)
        return temporary, destination, record
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def prepare_rename(root, file_id, name):
    """A rename-only version: identical bytes, new metadata, original untouched."""
    root, record, data, _, _, _ = load_file(root, file_id)
    identity = str(uuid4())
    temporary = root / 'uploads' / f'task-{identity}.tmp'
    destination = root / 'uploads' / f'{identity}.docx'
    new_name = naming.safe_name(name)
    if new_name == record['name']:
        raise HTTPException(400, '文件名称没有变化，无需生成新版本')
    try:
        temporary.write_bytes(data)
        validate_docx(temporary)
        if temporary.stat().st_size > MAX_BYTES:
            raise HTTPException(413, '生成文件超过 10 MiB 限制')
        result = dict(id=identity, name=new_name, size=temporary.stat().st_size,
                      storage_key=destination.name,
                      created_at=datetime.now(timezone.utc).isoformat(), source_file_id=file_id)
        return temporary, destination, result
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            raise HTTPException(422, str(exc)) from exc
        raise
