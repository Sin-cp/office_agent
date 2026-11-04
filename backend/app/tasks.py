"""Single-process durable task state with cooperative cancellation."""
import asyncio
import json
import os
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from . import database, documents, model, history, conversations, operations, web, edit_workflow, creation
from .files import FileInfo

router = APIRouter(prefix='/api/tasks', tags=['tasks'])
ACTIVE = {'queued', 'running', 'cancelling'}
STAGES = ['读取文档与会话', '理解请求', '校验结果', '保存结果']


class TaskInput(BaseModel):
    web_enabled: bool = True
    file_ids: list[str] | None = Field(default=None, max_length=20)
    file_id: str | None = Field(default=None, min_length=1, max_length=100)
    project_id: str | None = Field(default=None, min_length=1, max_length=100)
    current_file_id: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode='after')
    def identity(self):
        if self.file_ids is not None and (self.file_id or self.current_file_id):
            raise ValueError('不能混用单文件和多文件参数')
        if self.file_id and self.current_file_id and self.file_id != self.current_file_id:
            raise ValueError('文档版本参数不一致')
        return self
    prompt: str = Field(min_length=1, max_length=2000)

    @field_validator('prompt')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('请输入文档处理要求')
        return value.strip()


class Manager:
    def __init__(self, root):
        self.root = root
        self.active = None
        self.runner = None
        self.model_call = None
        self.cancel_requested = False
        with database.connection(root) as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute("CREATE INDEX IF NOT EXISTS tasks_project ON tasks(json_extract(payload, '$.project_id'))")
            db.execute("CREATE INDEX IF NOT EXISTS tasks_file ON tasks(json_extract(payload, '$.file.id'))")
            for row in db.execute('SELECT id, payload FROM tasks').fetchall():
                state = json.loads(row['payload'])
                if state['status'] in ACTIVE:
                    state.update(status='failed', error='服务重启，任务已中断，请重新提交', finished_at=time.time())
                    db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(state, ensure_ascii=False), row['id']))
            conversations.migrate(db)
        for temporary in root.glob('publication-*.tmp'):
            temporary.unlink(missing_ok=True)
        for journal in root.glob('publication-*.json'):
            keys = json.loads(journal.read_text())
            with database.connection(root) as db:
                for key in keys:
                    if '/' in key or '\\' in key or not key.endswith('.docx'):
                        raise ValueError('Invalid publication journal')
                    if not db.execute('SELECT 1 FROM files WHERE storage_key=?', (key,)).fetchone():
                        (root/'uploads'/key).unlink(missing_ok=True)
            journal.unlink()
        # Only our own task temporary files are cleaned after process restart.
        for path in (root / 'uploads').glob('task-*.tmp'):
            path.unlink(missing_ok=True)

    def save(self, state):
        with database.connection(self.root) as db:
            db.execute('INSERT OR REPLACE INTO tasks VALUES (?, ?)', (state['id'], json.dumps(state, ensure_ascii=False)))
            conversations.record_task(db, state)

    def get(self, task_id):
        with database.connection(self.root) as db:
            row = db.execute('SELECT payload FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, '任务不存在，可能已被清理')
        with database.connection(self.root) as db:
            return history.task_view(self.root, db, json.loads(row['payload']))

    def create(self, change):
        if change.file_ids is not None or not (change.file_id or change.current_file_id):
            return self.create_project_task(change)
        if self.runner and not self.runner.done():
            raise HTTPException(409, '已有任务正在执行，请等待完成或停止后重试')
        with database.connection(self.root) as db:
            db.execute('BEGIN IMMEDIATE')
            file_id = change.current_file_id or change.file_id
            record = db.execute('SELECT * FROM files WHERE id=?', (file_id,)).fetchone()
            if not record:
                raise HTTPException(404, '文件不存在，请重新选择')
            project_id = conversations.project_for(db, file_id)
            if change.project_id and change.project_id != project_id:
                raise HTTPException(409, '文档不属于当前项目')
            current = conversations.current(db, self.root, project_id)
            if not current or current['id'] != file_id:
                raise HTTPException(409, '当前版本已变化，请刷新或恢复该版本后再提交')
            from .projects import name_from_prompt
            name_from_prompt(db, project_id, change.prompt)
            state = dict(id=str(uuid4()), project_id=project_id, file=FileInfo(**dict(record)).model_dump(), prompt=change.prompt,
                         web_enabled=change.web_enabled, status='queued', stage=0, started_at=time.time(), finished_at=None,
                         error=None, result=None, steps=STAGES)
            db.execute('INSERT INTO tasks VALUES (?, ?)', (state['id'], json.dumps(state, ensure_ascii=False)))
            conversations.record_task(db, state)
        self.active = state
        self.cancel_requested = False
        self.runner = asyncio.create_task(self.run(state))
        return self.get(state['id'])

    def create_project_task(self, change):
        from . import projects
        if self.runner and not self.runner.done():
            raise HTTPException(409, '已有任务正在执行，请等待完成或停止后重试')
        with database.connection(self.root) as db:
            db.execute('BEGIN IMMEDIATE')
            project_id = change.project_id
            if project_id:
                conversations.ensure_project(db, project_id)
            else:
                if change.file_ids:
                    raise HTTPException(400, '附件必须属于指定项目')
                project_id = projects.create(db)
            records = []
            for identity in dict.fromkeys(change.file_ids or []):
                row = db.execute('SELECT f.* FROM files f JOIN project_sources s ON s.current_file_id=f.id WHERE s.project_id=? AND f.id=?', (project_id,identity)).fetchone()
                if not row:
                    raise HTTPException(409, '附件不属于此项目或已更新，请重新选择')
                if not (self.root/'uploads'/row['storage_key']).is_file():
                    raise HTTPException(404, '附件文件不可用')
                records.append(FileInfo(**dict(row)).model_dump())
            projects.name_from_prompt(db, project_id, change.prompt)
            state = dict(id=str(uuid4()), project_id=project_id, file=records[0] if records else None, files=records,
                         prompt=change.prompt, web_enabled=change.web_enabled, status='queued', stage=0, started_at=time.time(), finished_at=None,
                         error=None, result=None, steps=['读取会话', '理解请求', '处理内容', '保存结果'])
            db.execute('INSERT INTO tasks VALUES (?,?)', (state['id'], json.dumps(state,ensure_ascii=False)))
            conversations.record_task(db,state)
        self.active = state
        self.cancel_requested = False
        self.runner = asyncio.create_task(self.run_project(state))
        return self.get(state['id'])

    async def run_project(self, state):
        prepared = []
        published = []
        committed = False
        journal = self.root / f"publication-{state['id']}.json"
        try:
            await self.checkpoint(state, 0)
            inventories = [dict(id=f['id'], name=f['name']) for f in state['files']]
            async def load_inventory(file):
                await self.checkpoint(state, 1)
                loaded = await asyncio.to_thread(documents.load_file, self.root, file['id'])
                blocks, _ = operations.inventory(loaded[3], file['id'])
                item = dict(id=file['id'], name=file['name'], revision=loaded[4], blocks=blocks, coverage=operations.coverage(loaded[3]))
                inventories[inventories.index(file)] = item
                return item
            state['metrics'] = []
            model.call_metrics.set(state['metrics'])
            context = conversations.context(self.root, state['project_id'], None)
            await self.checkpoint(state, 1)
            async def web_progress(label):
                state['web_activity'] = label
                await self.checkpoint(state, 1)
            web_session = web.WebSession(state.get('web_enabled', True), web_progress)
            async def edit_progress(label, round_number):
                state['activity'] = label
                state['repair_round'] = round_number
                await self.checkpoint(state, 1)
            async def creation_progress(label, attempt):
                state['activity'] = label
                state['creation_attempt'] = attempt
                state['steps'][1] = '生成文档内容'
                state['steps'][2] = '校验文档'
                await self.checkpoint(state, 1)
            async def generate():
                creation.progress_context.set(creation_progress)
                edit_workflow.runtime.set((self.root, edit_progress))
                web.session_context.set(web_session)
                compacted, memory = await model.compact_memory(context)
                if memory:
                    value, through = memory
                    conversations.store_memory(self.root,state['project_id'],value.summary,through,value.preferences)
                model.conversation_context.set(compacted)
                return await model.respond_project(state['prompt'], inventories, load_inventory)
            self.model_call = asyncio.create_task(generate())
            try:
                suggestion = await self.model_call
            finally:
                self.model_call = None
                state['web_metrics'] = web_session.metrics
                state['web_activity'] = None
                state['activity'] = None
            await self.checkpoint(state, 2)
            result = dict(kind=suggestion.kind, text=suggestion.text, **web_session.result_fields())
            if suggestion.kind == 'create':
                from .creation import prepare
                # Bounded local rendering runs synchronously so cancellation cannot leak a worker's temporary file.
                item = prepare(self.root, state['project_id'], suggestion.document)
                prepared.append(item)
                output = dict(file=FileInfo(**item[2]).model_dump(), changes=[])
                result.update(outputs=[output], **output)
            if suggestion.kind == 'edit':
                owners = {b['id']: d['id'] for d in inventories for b in d.get('blocks', [])}
                grouped = {}
                for op in suggestion.operations:
                    owner = owners.get(op.target)
                    if not owner:
                        raise model.ModelError('修改目标不属于本轮选中文件')
                    grouped.setdefault(owner, []).append(op)
                outputs = []
                for d in inventories:
                    if d['id'] not in grouped:
                        continue
                    await self.checkpoint(state, 2)
                    item, changes = await asyncio.to_thread(documents.prepare_batch, self.root, d['id'], d['revision'], grouped[d['id']])
                    prepared.append(item)
                    outputs.append(dict(file=FileInfo(**item[2]).model_dump(), changes=changes))
                result['outputs'] = outputs
                if len(outputs) == 1:
                    result.update(outputs[0])
            await self.checkpoint(state, 3)
            state.update(status='completed', finished_at=time.time(), result=result)
            if prepared:
                with journal.with_suffix('.tmp').open('x') as stream:
                    json.dump([item[1].name for item in prepared], stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                journal.with_suffix('.tmp').replace(journal)
            # Files are moved before the single DB commit; uncommitted outputs are removed on failure.
            # A crash is recovered at startup by removing unreferenced generated files.
            with database.connection(self.root) as db:
                db.execute('BEGIN IMMEDIATE')
                for file in state['files']:
                    if not db.execute('SELECT 1 FROM project_sources WHERE project_id=? AND current_file_id=?', (state['project_id'], file['id'])).fetchone():
                        raise model.ModelError('文件已更新，本轮结果未发布')
                for temporary, destination, record in prepared:
                    temporary.replace(destination)
                    published.append(destination)
                    database.insert_record(db, record)
                db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(state,ensure_ascii=False),state['id']))
                conversations.record_task(db,state)
            committed = True
        except asyncio.CancelledError:
            state.update(status='cancelled', error=None, result=None)
        except (model.ModelError, HTTPException) as exc:
            state.update(status='failed', result=None, error=exc.detail if isinstance(exc,HTTPException) else str(exc))
        except Exception:
            state.update(status='failed', result=None, error='处理失败，未发布文件结果，请重试')
        finally:
            for temporary, _, _ in prepared:
                temporary.unlink(missing_ok=True)
            if not committed:
                for destination in published:
                    destination.unlink(missing_ok=True)
            journal.unlink(missing_ok=True)
            journal.with_suffix('.tmp').unlink(missing_ok=True)
            if state['status'] != 'completed':
                state['finished_at'] = time.time()
                self.save(state)

    def cancel(self, task_id):
        state = self.get(task_id)
        if state['status'] in ACTIVE and self.active and self.active['id'] == task_id:
            self.cancel_requested = True
            self.active['status'] = 'cancelling'
            self.save(self.active)
            if self.model_call:
                self.model_call.cancel()
        return self.get(task_id)

    async def checkpoint(self, state, stage):
        await asyncio.sleep(0)
        if self.cancel_requested:
            raise asyncio.CancelledError()
        state.update(status='running', stage=stage)
        self.save(state)

    async def run(self, state):
        prepared = None
        try:
            await self.checkpoint(state, 0)
            loaded = await asyncio.to_thread(documents.load_file, self.root, state['file']['id'])
            _, _, _, document, revision, legacy_blocks = loaded
            blocks, _ = operations.inventory(document, state['file']['id'])
            state['coverage'] = operations.coverage(document)
            state['metrics'] = []
            model.call_metrics.set(state['metrics'])
            context = conversations.context(self.root, state['project_id'], state['file']['id'])
            context['coverage'] = state['coverage']
            await self.checkpoint(state, 1)
            async def web_progress(label):
                state['web_activity'] = label
                await self.checkpoint(state, 1)
            web_session = web.WebSession(state.get('web_enabled', True), web_progress)
            async def edit_progress(label, round_number):
                state['activity'] = label
                state['repair_round'] = round_number
                await self.checkpoint(state, 1)
            async def generate():
                edit_workflow.runtime.set((self.root, edit_progress))
                edit_workflow.legacy_inventory.set(dict(id=state['file']['id'], name=state['file']['name'],
                                                       revision=revision, blocks=blocks, coverage=state['coverage']))
                web.session_context.set(web_session)
                compacted, memory = await model.compact_memory(context)
                if memory:
                    value, through = memory
                    conversations.store_memory(self.root, state['project_id'], value.summary, through, value.preferences)
                model.conversation_context.set(compacted)
                return await model.suggest(state['prompt'], blocks)
            self.model_call = asyncio.create_task(generate())
            try:
                suggestion = await self.model_call
            finally:
                self.model_call = None
                state['web_metrics'] = web_session.metrics
                state['web_activity'] = None
                state['activity'] = None
            await self.checkpoint(state, 2)
            legacy = isinstance(suggestion, model.Suggestion)
            if legacy and suggestion.status == 'unable':
                raise model.ModelError(suggestion.explanation)
            if not legacy:
                if (suggestion.kind == 'edit') != bool(suggestion.operations):
                    raise model.ModelError('模型结果类型与修改操作不一致')
                references = {block['id']: block for block in blocks}
                citations = []
                for citation in suggestion.citations:
                    block = references.get(citation.block_id)
                    if not block or citation.quote not in block['text']:
                        raise model.ModelError('模型引用无法在当前版本原文中核对')
                    citations.append(dict(file_id=state['file']['id'], block_id=block['id'], location=block['location'], quote=citation.quote))
                if suggestion.kind != 'edit':
                    state.update(status='completed', stage=3, finished_at=time.time(),
                                 result=dict(kind=suggestion.kind, text=suggestion.text, citations=citations, **web_session.result_fields()))
                    state['steps'] = ['读取文档与会话', '分析内容', '核对引用', '完成回答']
                    self.save(state)
                    return
            state['steps'] = ['读取文档与会话', '规划修改', '校验修改', '生成文件']
            await self.checkpoint(state, 3)
            if legacy:
                selected = next((b for b in legacy_blocks if b['id'] == suggestion.block_id), None)
                if selected is None:
                    raise model.ModelError('模型返回的段落不属于当前文件')
                change = documents.EditRequest(block_id=selected['id'], revision=revision,
                    expected_text=selected['text'], new_text=suggestion.new_text)
                if change.new_text == selected['text']:
                    raise model.ModelError('文字没有变化，无需生成新文件')
                prepared = await asyncio.to_thread(documents.prepare_edit, self.root, state['file']['id'], change, True)
                result = dict(kind='edit', text=suggestion.explanation, position=selected['position'],
                    summary=f"已替换第 {selected['position']} 段正文文字。", explanation=suggestion.explanation,
                    before=selected['text'], after=change.new_text)
            else:
                prepared, changes = await asyncio.to_thread(documents.prepare_batch, self.root, state['file']['id'], revision, suggestion.operations)
                result = dict(kind='edit', text=suggestion.text, changes=changes, citations=citations)
            await self.checkpoint(state, 3)
            result.update(web_session.result_fields())
            result['file'] = FileInfo(**prepared[2]).model_dump()
            state.update(status='completed', finished_at=time.time(), result=result)
            def persist(record):
                with database.connection(self.root) as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = conversations.current(db, self.root, state['project_id'])
                    if not current or current['id'] != state['file']['id']:
                        raise model.ModelError('当前版本已变化，修改未发布')
                    database.insert_record(db, record)
                    db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(state, ensure_ascii=False), state['id']))
                    conversations.record_task(db, state)
            documents.publish_edit(self.root, prepared, persist)

        except asyncio.CancelledError:
            state.update(status='cancelled', error=None)
        except (model.ModelError, HTTPException) as exc:
            state.update(status='failed', result=None, error=exc.detail if isinstance(exc, HTTPException) else str(exc))
        except Exception:
            state.update(status='failed', result=None, error='处理失败，模型内容无效或文件无法保存，请检查后重试')
        finally:
            if prepared:
                prepared[0].unlink(missing_ok=True)
            if state['status'] != 'completed':
                state['finished_at'] = time.time()
                self.save(state)

    async def close(self):
        if self.runner and not self.runner.done():
            self.cancel(self.active['id'])
            await self.runner


@router.post('', status_code=202)
async def create(request: Request, change: TaskInput):
    return request.app.state.tasks.create(change)


@router.get('/{task_id}')
async def get(request: Request, task_id: str):
    return request.app.state.tasks.get(task_id)


@router.post('/{task_id}/cancel')
async def cancel(request: Request, task_id: str):
    return request.app.state.tasks.cancel(task_id)
