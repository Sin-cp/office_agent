"""Read and replace one explicitly selected paragraph; never execute AI code."""
import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from . import database, document_service as service
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


def load(request, file_id):
    root = request.app.state.data_dir
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


@router.get('/{file_id}/content')
def content(request: Request, file_id: str):
    _, record, _, document, revision, items = load(request, file_id)
    return dict(file=FileInfo(**record), revision=revision, blocks=items,
                table_count=len(document.tables))


@router.post('/{file_id}/edits', response_model=FileInfo, status_code=201)
def edit(request: Request, file_id: str, change: EditRequest):
    root, record, data, document, revision, items = load(request, file_id)
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
        result = dict(id=new_id, name=record['name'][:-5][:220] + '_修改版.docx',
                      size=temporary.stat().st_size, storage_key=destination.name,
                      created_at=datetime.now(timezone.utc).isoformat(), source_file_id=file_id)
        temporary.replace(destination)
        database.insert(root, result)
        committed = True
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception('Document generation failed')
        raise HTTPException(500, '生成文件失败，请稍后重试') from exc
    finally:
        temporary.unlink(missing_ok=True)
        if not committed:
            destination.unlink(missing_ok=True)
