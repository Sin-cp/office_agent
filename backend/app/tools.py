"""Deterministic tool implementations for the agent loop.

The model only proposes a call; every effect is authorised and executed here.
Tools read no process globals, so they can be exercised with a fake context.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

from . import model
from .web import WebError

logger = logging.getLogger(__name__)

MAX_READ_CHARS = 40000
MAX_READ_GROUPS = 2
READ_LIMIT = 4
ANSWER_LIMIT = 1
SEARCH_LIMIT = 3
FETCH_LIMIT = 5
CREATE_LIMIT = 1
EDIT_LIMIT = 2
IMAGE_LIMIT = 4
LOCAL_IMPORT_LIMIT = 20


class ToolError(Exception):
    """A rejected call. The message is safe to show to the user."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ToolResult:
    ok: bool
    summary: str
    content: str


@dataclass
class ToolContext:
    files: list[dict]
    load_inventory: Callable[[str], Awaitable[dict]]
    list_sources: Callable[[], Awaitable[list[dict]]]
    web: Any | None
    allowed_files: set[str]
    user_text: str
    search_urls: set[str] = field(default_factory=set)
    calls: dict[str, int] = field(default_factory=dict)
    root: Path | None = None
    project_id: str = ""
    prepared: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    edited: set[str] = field(default_factory=set)
    images: list = field(default_factory=list)
    allowed_images: set[str] = field(default_factory=set)
    load_image: Callable[[str], Awaitable[dict]] | None = None
    analysis: dict[str, str] = field(default_factory=dict)
    # Local files imported this turn; keeps the source identity out of task JSON.
    local_sources: dict[str, dict] = field(default_factory=dict)
    local_edit_used: bool = False
    on_import: Callable[[dict], None] | None = None

    def spend(self, name: str, limit: int) -> None:
        used = self.calls.get(name, 0) + 1
        if used > limit:
            raise ToolError("budget", f"本轮 {name} 已达上限（{limit} 次）")
        self.calls[name] = used


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出当前项目的文档、当前版本与是否可用。用于确认能处理哪些文件。",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": "读取本轮选中文档的正文。按块返回，可能被截断并附带分块目录；可用 chunk 跳到指定分块。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "附件或 list_files 中的文件 id"},
                    "chunk": {
                        "type": "integer",
                        "description": "从第几个分块开始（1 起，见返回的 directory.index）；默认 1",
                    },
                },
                "required": ["file_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "answer_documents",
            "description": (
                "阅读一份或多份项目文档并回答问题或总结全文。整篇总结、跨文件问答优先用本工具；"
                "返回可直接采用的回答。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "要回答的问题或总结要求，尽量逐字复述用户问题",
                    },
                    "file_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要阅读的文件 id，最多 10 份；缺省用本轮附件",
                    },
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "联网检索实时信息，返回标题、链接与摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索词，1–128 字"}
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": "读取网页正文。只允许用户消息里的链接或本轮 search_web 返回的链接。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_image",
            "description": (
                "分析本轮上传或粘贴的图片，返回文字描述、识别出的文字与表格结构。"
                "需要把图片内容写进文档时，先用本工具转成文字。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "image_id": {"type": "string", "description": "本轮图片的 id"},
                    "question": {
                        "type": "string",
                        "description": "关注点，例如「提取表格」「读出金额」",
                    },
                },
                "required": ["image_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": (
                "统计用户给出的本机目录：文件数、子目录数、按扩展名分布。"
                "只读文件名与数量，不读取文件内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "用户消息里逐字出现的路径"},
                    "recursive": {
                        "type": "boolean",
                        "description": "仅在用户明确要求包含子目录时为 true",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "import_local_docx",
            "description": (
                "把用户给出的本机 .docx 复制进当前项目，之后可用 read_document 读取与总结。"
                "path 可以是单个 .docx 或目录（目录只导入顶层，最多 20 个）。"
                "不会修改或删除源文件。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "用户消息里逐字出现的 .docx 文件或目录路径",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_document",
            "description": (
                "新建一份 Word 文档。用于「整理成文件 / 生成文档 / 输出一份材料」这类要求。"
                "内容必须来自本轮已读取的文档或检索结果，不要编造。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "要生成什么文档、包含哪些内容与数据来源",
                    },
                    "reference_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "需要参考的本轮附件 id，只能来自 user 消息的 files 数组；"
                            "图片用 image 区块引用，不要放进这里；本轮没有附件时省略"
                        ),
                    },
                },
                "required": ["instruction"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_document",
            "description": (
                "修改本轮选中的已有文档并生成新版本，原文件不变。"
                "只在用户明确要求改写、增删文档内容或重命名时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "本轮附件的文件 id"},
                    "instruction": {
                        "type": "string",
                        "description": "要改什么，尽量逐字复述用户要求",
                    },
                },
                "required": ["file_id", "instruction"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attach_image",
            "description": (
                "把本轮图片原样插入已有文档，生成新版本。只决定位置与尺寸，"
                "不判断图片内容；用户明确要求把图片放进文档时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "image_id": {"type": "string", "description": "本轮图片的 id"},
                    "file_id": {"type": "string", "description": "目标文档 id"},
                    "after_block_id": {
                        "type": "string",
                        "description": "插到该段落之后；缺省插入文档末尾",
                    },
                    "width_cm": {"type": "number", "description": "3–18，默认 12"},
                    "caption": {"type": "string", "description": "可选图注"},
                },
                "required": ["image_id", "file_id"],
                "additionalProperties": False,
            },
        },
    },
]


def _host(url: str) -> str:
    return url.split("//")[-1].split("/")[0] if "//" in url else url


def _chunk_start(value: Any, total: int) -> int:
    try:
        start = int(value or 1)
    except (TypeError, ValueError):
        start = 1
    if start < 1 or start > total:
        raise ToolError("chunk_out_of_range", f"分块编号需在 1–{total} 之间")
    return start


def describe(name: str, args: dict, context: ToolContext) -> str:
    """Human-readable one-liner for the UI. Never includes document text."""
    if name == "list_files":
        return "查看项目文件"
    if name == "read_document":
        file_id = str(args.get("file_id") or "")
        row = next((f for f in context.files if f.get("id") == file_id), None)
        return f"读取《{row['name']}》" if row else "读取文档"
    if name == "answer_documents":
        return "阅读文档并回答"
    if name == "search_web":
        query = str(args.get("query") or "").strip()
        return f"检索网页「{query[:40]}」" if query else "检索网页"
    if name == "fetch_page":
        return f"读取 {_host(str(args.get('url') or ''))}".strip()
    if name == "analyze_image":
        image_id = str(args.get("image_id") or "")
        row = next((i for i in context.images if i.get("id") == image_id), None)
        return f"分析图片《{row['name']}》" if row else "分析图片"
    if name == "create_document":
        return "生成文档"
    if name == "edit_document":
        file_id = str(args.get("file_id") or "")
        row = next((f for f in context.files if f.get("id") == file_id), None)
        return f"修改《{row['name']}》" if row else "修改文档"
    if name == "list_directory":
        return "统计本机目录"
    if name == "import_local_docx":
        return "导入本地文档"
    if name == "attach_image":
        file_id = str(args.get("file_id") or "")
        row = next((f for f in context.files if f.get("id") == file_id), None)
        return f"插入图片到《{row['name']}》" if row else "插入图片"
    return name


async def _list_files(args, context: ToolContext) -> ToolResult:
    rows = await context.list_sources()
    files = []
    for row in rows:
        current = row.get("current_file")
        current = current if isinstance(current, dict) else None
        # Users mean the latest version; keep the source id for reference.
        files.append(
            {
                "id": (current or {}).get("id") or row.get("id") or row.get("source_file_id"),
                "name": (current or {}).get("name") or row.get("name"),
                "source_id": row.get("id") or row.get("source_file_id"),
                "available": row.get("available", True),
            }
        )
    payload = {"files": files}
    if not files:
        payload["note"] = "当前项目还没有文档"
    return ToolResult(
        True,
        f"项目内 {len(files)} 份文档",
        json.dumps(payload, ensure_ascii=False),
    )


async def _read_document(args, context: ToolContext) -> ToolResult:
    file_id = str(args.get("file_id") or "")
    if not file_id:
        raise ToolError("invalid_file", "缺少文件 id")
    context.spend("read_document", READ_LIMIT)
    inventory = await context.load_inventory(file_id)
    blocks = inventory.get("blocks") or []
    name = inventory.get("name") or "文档"
    if not blocks:
        return ToolResult(
            True,
            f"《{name}》没有可读正文",
            json.dumps({"file_id": file_id, "blocks": [], "truncated": False}, ensure_ascii=False),
        )
    try:
        groups = model.chunks(blocks)
    except model.ModelError as exc:
        raise ToolError("too_large", str(exc)) from exc
    directory = [
        {
            "index": i + 1,
            "start": group[0].get("location") or group[0].get("id"),
            "end": group[-1].get("location") or group[-1].get("id"),
            "excerpt": (group[0].get("text") or "")[:80],
        }
        for i, group in enumerate(groups)
    ]
    start = _chunk_start(args.get("chunk"), len(groups))
    selected = groups[start - 1 : start - 1 + MAX_READ_GROUPS]
    end = start - 1 + len(selected)
    payload = {
        "file_id": file_id,
        "name": name,
        "coverage": inventory.get("coverage", []),
        "directory": directory,
        "chunk_start": start,
        "chunk_end": end,
        "total_chunks": len(groups),
        "blocks": [block for group in selected for block in group],
        "truncated": end < len(groups),
    }
    if payload["truncated"]:
        payload["note"] = (
            f"只包含第 {start}–{end} 个分块（共 {len(groups)} 个），"
            "其余见 directory，可用 chunk 继续读取"
        )
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > MAX_READ_CHARS:
        payload["blocks"] = []
        payload["truncated"] = True
        payload["note"] = "正文过大，只返回分块目录"
        text = json.dumps(payload, ensure_ascii=False)
    summary = f"读取《{name}》（第 {start}–{end} 块，共 {len(groups)} 块" + (
        "，已截断）" if payload["truncated"] else "）"
    )
    return ToolResult(True, summary, text)


async def _answer_documents(args, context: ToolContext) -> ToolResult:
    question = str(args.get("question") or "").strip()
    if not question:
        raise ToolError("invalid_question", "缺少要回答的问题")
    if len(question) > 4000:
        raise ToolError("invalid_question", "问题过长，请精简到 4000 字以内")
    raw_ids = args.get("file_ids")
    if raw_ids is None:
        raw_ids = [item.get("id") for item in context.files]
    if not isinstance(raw_ids, list):
        raise ToolError("invalid_file", "file_ids 必须是数组")
    file_ids: list[str] = []
    for value in raw_ids:
        text = str(value or "").strip()
        if text and text not in file_ids:
            file_ids.append(text)
    if not file_ids:
        raise ToolError("no_files", "请指定要阅读的文档，或先用 list_files 查看项目文件")
    if len(file_ids) > 10:
        raise ToolError("too_many", "一次最多阅读 10 份文档")
    context.spend("answer_documents", ANSWER_LIMIT)
    inventories = [await context.load_inventory(file_id) for file_id in file_ids]
    text = await model.answer_documents(question, inventories)
    return ToolResult(
        True,
        f"阅读 {len(inventories)} 份文档",
        json.dumps(
            {
                "answer": text,
                "files": [item.get("name") for item in inventories],
            },
            ensure_ascii=False,
        ),
    )


def _session(context: ToolContext):
    session = context.web
    if session is None or not getattr(session, "enabled", False):
        raise ToolError("web_disabled", "本轮未开启联网")
    return session


async def _search_web(args, context: ToolContext) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query or len(query) > 128:
        raise ToolError("invalid_query", "检索词需为 1–128 字")
    context.spend("search_web", SEARCH_LIMIT)
    session = _session(context)
    before = len(session.evidence)
    try:
        urls = await session.search(query)
    except WebError as exc:
        raise ToolError(exc.code, str(exc)) from exc
    context.search_urls.update(urls)
    rows = [
        {
            "title": item.get("title"),
            "url": item.get("url"),
            "summary": (item.get("content") or "")[:800],
            "published_at": item.get("published_at"),
        }
        for item in session.evidence[before:]
    ]
    payload = {"query": query, "results": rows}
    if not rows:
        payload["note"] = "没有检索到公开结果"
    return ToolResult(
        True,
        f"检索到 {len(rows)} 条结果",
        json.dumps(payload, ensure_ascii=False),
    )


async def _fetch_page(args, context: ToolContext) -> ToolResult:
    url = str(args.get("url") or "").strip()
    if not url:
        raise ToolError("invalid_url", "缺少链接")
    if url not in context.user_text and url not in context.search_urls:
        raise ToolError("blocked_url", "只能读取用户给出的链接或本轮检索结果")
    context.spend("fetch_page", FETCH_LIMIT)
    session = _session(context)
    before = len(session.evidence)
    try:
        await session.fetch(url)
    except WebError as exc:
        raise ToolError(exc.code, str(exc)) from exc
    new = session.evidence[before:]
    if not new:
        return ToolResult(
            True,
            "网页没有可读正文",
            json.dumps({"url": url, "content": "", "truncated": False}, ensure_ascii=False),
        )
    item = new[-1]
    return ToolResult(
        True,
        f"读取 {_host(url)}",
        json.dumps(
            {
                "url": item.get("url"),
                "title": item.get("title"),
                "content": item.get("content"),
                "truncated": item.get("truncated", False),
            },
            ensure_ascii=False,
        ),
    )


async def _analyze_image(args, context: ToolContext) -> ToolResult:
    image_id = str(args.get("image_id") or "")
    if image_id not in context.allowed_images:
        raise ToolError("forbidden", "该图片不在本轮范围")
    if context.load_image is None:
        raise ToolError("unavailable", "当前环境不支持读取图片")
    context.spend("analyze_image", IMAGE_LIMIT)
    question = str(args.get("question") or "").strip() or (
        "描述这张图片的内容；如果包含文字或表格，请逐字提取并保留结构；"
        "看不清或不确定的部分明确说明。"
    )
    key = image_id + "\n" + question
    if key in context.analysis:
        text = context.analysis[key]
    else:
        asset = await context.load_image(image_id)
        text = await model.vision(
            question, asset["data"], asset.get("mime") or "image/png"
        )
        context.analysis[key] = text
    row = next((i for i in context.images if i.get("id") == image_id), None)
    name = (row or {}).get("name") or "图片"
    return ToolResult(
        True,
        f"分析图片《{name}》",
        json.dumps(
            {
                "image_id": image_id,
                "text": text,
                "note": "以上是图片内容的文字转写，可直接用于生成文档",
            },
            ensure_ascii=False,
        ),
    )


async def _list_directory(args, context: ToolContext) -> ToolResult:
    from . import local_fs

    path = str(args.get("path") or "").strip()
    if not path:
        raise ToolError("invalid_path", "缺少目录路径")
    if path not in context.user_text:
        raise ToolError("forbidden", "只能统计用户消息里给出的路径")
    try:
        resolved = local_fs.resolve_path(path, "dir")
        data = await asyncio.to_thread(
            local_fs.list_directory, resolved, bool(args.get("recursive"))
        )
    except local_fs.LocalError as exc:
        raise ToolError("local_error", exc.message) from exc
    return ToolResult(
        True,
        f"统计目录 {resolved}",
        json.dumps(data, ensure_ascii=False),
    )


async def _import_local_docx(args, context: ToolContext) -> ToolResult:
    from . import local_fs

    path = str(args.get("path") or "").strip()
    if not path:
        raise ToolError("invalid_path", "缺少文件路径")
    if path not in context.user_text:
        raise ToolError("forbidden", "只能导入用户消息里给出的路径")
    if context.root is None or not context.project_id:
        raise ToolError("unavailable", "当前环境不支持导入本地文件")
    context.spend("import_local_docx", LOCAL_IMPORT_LIMIT)
    try:
        resolved = local_fs.resolve_path(path)
        candidates, total = await asyncio.to_thread(local_fs.collect_docx, resolved)
    except local_fs.LocalError as exc:
        raise ToolError("local_error", exc.message) from exc
    if not candidates:
        return ToolResult(
            True,
            "没有可导入的 .docx",
            json.dumps(
                {
                    "files": [],
                    "total": total,
                    "skipped": [],
                    "note": "该路径下没有可导入的 .docx 文件",
                },
                ensure_ascii=False,
            ),
        )
    imported: list[dict] = []
    skipped: list[dict] = []
    for source in candidates:
        if context.calls.get("local_import_files", 0) >= LOCAL_IMPORT_LIMIT:
            skipped.append(
                {"name": source.name, "reason": "本轮导入文件数已达上限（20 个）"}
            )
            continue
        try:
            identity = await asyncio.to_thread(local_fs.file_identity, source)
            record = await asyncio.to_thread(
                local_fs.import_docx, context.root, context.project_id, source
            )
            stored_sha = await asyncio.to_thread(
                local_fs.sha256, context.root / "uploads" / record["storage_key"]
            )
        except local_fs.LocalError as exc:
            skipped.append({"name": source.name, "reason": exc.message})
            continue
        if stored_sha != identity["sha256"]:
            # The source changed while copying; keep the project copy but no overwrite.
            skipped.append(
                {
                    "name": source.name,
                    "reason": "导入期间源文件发生变化，仅保留项目副本",
                }
            )
        else:
            context.local_sources[record["id"]] = {
                "path": str(source),
                "name": record["name"],
                **identity,
            }
        context.allowed_files.add(record["id"])
        if not any(item.get("id") == record["id"] for item in context.files):
            context.files.append({"id": record["id"], "name": record["name"]})
        if context.on_import is not None:
            context.on_import(record)
        context.calls["local_import_files"] = (
            context.calls.get("local_import_files", 0) + 1
        )
        imported.append({"file_id": record["id"], "name": record["name"]})
    note = ""
    if total > len(candidates):
        note = f"共有 {total} 个 .docx，本次最多导入 {len(candidates)} 个"
    payload = {
        "files": imported,
        "total": total,
        "skipped": skipped,
        "note": note or "已复制进项目，源文件未改动；可继续用 read_document 阅读",
    }
    return ToolResult(
        True,
        f"导入 {len(imported)} 份文档",
        json.dumps(payload, ensure_ascii=False),
    )


async def _attach_image(args, context: ToolContext) -> ToolResult:
    from . import documents

    image_id = str(args.get("image_id") or "")
    file_id = str(args.get("file_id") or "")
    if image_id not in context.allowed_images:
        raise ToolError("forbidden", "该图片不在本轮范围")
    if file_id not in context.allowed_files:
        raise ToolError("forbidden", "该文件不在本轮选中范围")
    if context.root is None:
        raise ToolError("unavailable", "当前环境不支持修改文件")
    if file_id in context.local_sources and context.local_edit_used:
        raise ToolError("local_budget", "本轮只能生成一个本地修改版，请分开提交")
    context.spend("attach_image", 5)
    try:
        width = float(args.get("width_cm") or 12)
    except (TypeError, ValueError):
        width = 12.0
    width = max(3.0, min(18.0, width))
    caption = str(args.get("caption") or "").strip()[:200]
    after = str(args.get("after_block_id") or "").strip()
    asset = await context.load_image(image_id)
    if asset.get("mime") == "image/webp":
        raise ToolError("unsupported", "文档暂不支持 WebP 图片，请改用 PNG 或 JPEG")
    try:
        item = documents.prepare_image(
            context.root, file_id, after, asset["path"], width, caption
        )
    except HTTPException as exc:
        raise ToolError("attach_failed", str(exc.detail)) from exc
    except ValueError as exc:
        raise ToolError("attach_failed", str(exc)) from exc
    context.prepared.append(item)
    if file_id in context.local_sources:
        context.local_edit_used = True
    record = item[2]
    context.artifacts.append(
        {
            "file_id": record["id"],
            "name": record["name"],
            "kind": "edit",
            "changes": [],
            "renamed": False,
        }
    )
    return ToolResult(
        True,
        f"已把图片插入《{record['name']}》",
        json.dumps(
            {
                "file_id": record["id"],
                "name": record["name"],
                "note": "图片已插入并会生成新版本，原文件不变；不要在回答里重复全文",
            },
            ensure_ascii=False,
        ),
    )


async def _create_document(args, context: ToolContext) -> ToolResult:
    from . import creation

    instruction = str(args.get("instruction") or "").strip()
    if not instruction:
        raise ToolError("invalid_instruction", "缺少生成要求")
    if context.root is None or not context.project_id:
        raise ToolError("unavailable", "当前环境不支持生成文件")
    # A rejected call must not burn the one allowed document, otherwise a single
    # bad argument blocks the whole turn.
    attempts = context.calls.get("create_attempts", 0) + 1
    if attempts > 3:
        raise ToolError("budget", "生成文档的尝试次数过多，请缩小范围后重试")
    context.calls["create_attempts"] = attempts
    if context.calls.get("create_document", 0) >= CREATE_LIMIT:
        raise ToolError("budget", "本轮已生成过一份文档，请把要求合并成一次")
    references = []
    for file_id in args.get("reference_ids") or []:
        if file_id not in context.allowed_files:
            raise ToolError(
                "forbidden",
                "reference_ids 只能使用本轮附件；本轮没有附件时请不要传该字段",
            )
        references.append(await context.load_inventory(str(file_id)))
    if len(json.dumps(references, ensure_ascii=False)) > 60000:
        raise ToolError("too_large", "参考附件超出新建文档预算，请缩小范围")
    content = await creation.generate(instruction, references, images=context.images)
    image_paths = {}
    for section in content.sections:
        if section.kind != "image":
            continue
        if section.asset_id not in context.allowed_images:
            raise ToolError("forbidden", "文档引用了不在本轮的图片")
        if section.asset_id not in image_paths:
            asset = await context.load_image(section.asset_id)
            if asset.get("mime") == "image/webp":
                raise ToolError("unsupported", "文档暂不支持 WebP 图片，请改用 PNG 或 JPEG")
            image_paths[section.asset_id] = asset["path"]
    try:
        temporary, destination, record = creation.prepare(
            context.root, context.project_id, content, image_paths
        )
    except Exception as exc:
        raise ToolError("create_failed", f"文档渲染失败：{exc}") from exc
    context.calls["create_document"] = context.calls.get("create_document", 0) + 1
    context.prepared.append((temporary, destination, record))
    context.artifacts.append(
        {
            "file_id": record["id"],
            "name": record["name"],
            "kind": "create",
            "changes": [],
            "renamed": False,
        }
    )
    return ToolResult(
        True,
        f"已生成《{record['name']}》",
        json.dumps(
            {
                "file_id": record["id"],
                "name": record["name"],
                "note": "文件已生成，本轮结束时保存；不要重复粘贴全文",
            },
            ensure_ascii=False,
        ),
    )


async def _edit_document(args, context: ToolContext) -> ToolResult:
    from . import documents, edit_workflow

    file_id = str(args.get("file_id") or "")
    instruction = str(args.get("instruction") or "").strip()
    if file_id not in context.allowed_files:
        raise ToolError("forbidden", "该文件不在本轮选中范围")
    if not instruction:
        raise ToolError("invalid_instruction", "缺少修改要求")
    if context.root is None:
        raise ToolError("unavailable", "当前环境不支持修改文件")
    if file_id in context.edited:
        raise ToolError("duplicate", "本轮已修改过该文件，请把要求合并成一次")
    if file_id in context.local_sources and context.local_edit_used:
        raise ToolError("local_budget", "本轮只能生成一个本地修改版，请分开提交")
    attempt_key = "edit_attempts:" + file_id
    attempts = context.calls.get(attempt_key, 0) + 1
    if attempts > 2:
        raise ToolError("budget", "该文件本轮修改尝试次数过多，请缩小范围后重试")
    context.calls[attempt_key] = attempts
    if context.calls.get("edit_document", 0) >= EDIT_LIMIT:
        raise ToolError("budget", "本轮修改次数已达上限，请把要求合并成一次")
    inventory = await context.load_inventory(file_id)
    metrics = model.call_metrics.get()
    token = edit_workflow.budget_base.set(len(metrics or []))
    try:
        response = await edit_workflow.run(instruction, [inventory])
    finally:
        edit_workflow.budget_base.reset(token)
    if response.kind == "clarify":
        return ToolResult(
            False,
            response.text,
            json.dumps({"clarify": response.text}, ensure_ascii=False),
        )
    if response.kind != "edit":
        return ToolResult(
            True,
            response.text,
            json.dumps({"note": response.text}, ensure_ascii=False),
        )
    name = next(
        (rename.name for rename in response.renames if rename.file_id == file_id), None
    )
    try:
        if response.operations:
            item, changes = documents.prepare_batch(
                context.root, file_id, inventory["revision"], response.operations, name
            )
        else:
            item, changes = documents.prepare_rename(context.root, file_id, name), []
    except HTTPException as exc:
        raise ToolError("edit_failed", str(exc.detail)) from exc
    except ValueError as exc:
        raise ToolError("edit_failed", str(exc)) from exc
    context.calls["edit_document"] = context.calls.get("edit_document", 0) + 1
    context.edited.add(file_id)
    context.prepared.append(item)
    if file_id in context.local_sources:
        context.local_edit_used = True
    record = item[2]
    context.artifacts.append(
        {
            "file_id": record["id"],
            "name": record["name"],
            "kind": "edit",
            "changes": changes,
            "renamed": bool(response.renames),
        }
    )
    return ToolResult(
        True,
        f"修改 {len(changes)} 处，审核通过",
        json.dumps(
            {
                "file_id": record["id"],
                "name": record["name"],
                "note": "已生成修改版，原文件未改动；不要在回答里重复全文",
            },
            ensure_ascii=False,
        ),
    )


HANDLERS = {
    "list_files": _list_files,
    "read_document": _read_document,
    "answer_documents": _answer_documents,
    "search_web": _search_web,
    "fetch_page": _fetch_page,
    "analyze_image": _analyze_image,
    "create_document": _create_document,
    "edit_document": _edit_document,
    "attach_image": _attach_image,
    "list_directory": _list_directory,
    "import_local_docx": _import_local_docx,
}


def safe_args(arguments: Any) -> dict:
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str) or not arguments.strip():
        return {}
    try:
        value = json.loads(arguments)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


async def dispatch(name: str, arguments: Any, context: ToolContext) -> ToolResult:
    """Run one proposed call. Never raises: failures become a ToolResult."""
    args = safe_args(arguments)
    handler = HANDLERS.get(name)
    if handler is None:
        return ToolResult(
            False, f"未知工具 {name or '（空）'}", json.dumps({"error": "unknown_tool"}, ensure_ascii=False)
        )
    try:
        return await handler(args, context)
    except ToolError as exc:
        return ToolResult(
            False,
            exc.message,
            json.dumps({"error": exc.code, "message": exc.message}, ensure_ascii=False),
        )
    except model.ModelError as exc:
        return ToolResult(
            False,
            str(exc),
            json.dumps({"error": "model_error", "message": str(exc)}, ensure_ascii=False),
        )
    except Exception as exc:  # a tool must never break the loop
        logger.warning("tool %s failed: %s", name, type(exc).__name__)
        return ToolResult(
            False, "工具执行失败", json.dumps({"error": "internal_error"}, ensure_ascii=False)
        )
