"""Task-boundary fakes for tests.

The tool loop itself is covered by test_agent_loop and test_tools. These helpers
replace app.agent.run with a small deterministic handler that stages real
prepared files, so task, history and publication tests still exercise
run_project's transaction without calling a model.
"""

import asyncio
import json
from unittest.mock import patch

from app import agent, creation, documents, model, operations


def patch_agent(handler):
    async def run(
        prompt,
        *,
        context,
        conversation=None,
        events=None,
        on_event=None,
        cancelled=None,
    ):
        return await handler(prompt, context)

    return patch("app.agent.run", run)


def _stage(context, item, changes, kind, renamed=False):
    context.prepared.append(item)
    context.artifacts.append(
        dict(
            file_id=item[2]["id"],
            name=item[2]["name"],
            kind=kind,
            changes=changes,
            renamed=renamed,
        )
    )


def stage(context, item, changes, kind, renamed=False):
    """Public wrapper for tests that build their own prepared artifacts."""
    _stage(context, item, changes, kind, renamed)


def _first_block(root, file_id):
    loaded = documents.load_file(root, file_id)
    blocks, _ = operations.inventory(loaded[3], file_id)
    target = next(
        (block for block in blocks if block.get("editable", True) and block["text"].strip()),
        None,
    )
    if target is None:
        raise model.ModelError("没有可修改的正文")
    return loaded[4], target


def answer(text="回答", kind="answer"):
    async def handler(prompt, context):
        return agent.AgentResult(text=text, kind=kind)

    return handler


def edit(file_ids=None, text="已修改"):
    """Replace one paragraph in every requested file, one version per file."""

    async def handler(prompt, context):
        ids = list(file_ids) if file_ids is not None else [
            file["id"] for file in context.files
        ]
        for file_id in ids:
            revision, target = _first_block(context.root, file_id)
            op = operations.Operation(
                op="replace",
                target=target["id"],
                expected_text=target["text"],
                text=target["text"] + "修改",
            )
            item, changes = documents.prepare_batch(
                context.root, file_id, revision, [op]
            )
            _stage(context, item, changes, "edit")
        return agent.AgentResult(text=text, kind="edit")

    return handler


def rename(file_id, name, text="已重命名"):
    async def handler(prompt, context):
        item = documents.prepare_rename(context.root, file_id, name)
        _stage(context, item, [], "edit", renamed=True)
        return agent.AgentResult(text=text, kind="edit")

    return handler


def create(content=None, text="已生成"):
    content = content or creation.NewDocument(
        name="新文档.docx",
        title="新文档",
        sections=[creation.Section(kind="paragraph", text="正文")],
    )

    async def handler(prompt, context):
        item = await asyncio.to_thread(
            creation.prepare, context.root, context.project_id, content
        )
        _stage(context, item, [], "create")
        return agent.AgentResult(text=text, kind="create")

    return handler


def fail(message="测试失败", error=None):
    async def handler(prompt, context):
        raise error or model.ModelError(message)

    return handler


def calls(*replies):
    """Patch app.model.complete with a scripted reply queue for loop tests."""

    queue = list(replies)

    async def complete(messages, tools=None, timeout=45):
        if not queue:
            raise AssertionError("unexpected model call")
        return queue.pop(0)

    return patch("app.model.complete", complete)


def call(name, arguments, identity="call-1"):
    return {
        "id": identity,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def text_reply(text):
    return {"content": text, "tool_calls": [], "finish_reason": "stop"}
