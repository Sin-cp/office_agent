"""Single-process durable task state with cooperative cancellation."""
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from . import database, documents, model, history, conversations, operations, web, edit_workflow, creation
from .files import FileInfo

router = APIRouter(prefix='/api/tasks', tags=['tasks'])
ACTIVE = {'queued', 'running', 'cancelling'}


class TaskInput(BaseModel):
    web_enabled: bool = True
    image_ids: list[str] | None = Field(default=None, max_length=4)
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
        """Single-file payloads are a compatibility shape for project tasks."""
        single = change.file_id or change.current_file_id
        if change.file_ids is None and single:
            with database.connection(self.root) as db:
                record = db.execute('SELECT id FROM files WHERE id=?', (single,)).fetchone()
                if not record:
                    raise HTTPException(404, '文件不存在，请重新选择')
                project_id = conversations.project_for(db, single)
                if change.project_id and change.project_id != project_id:
                    raise HTTPException(409, '文档不属于当前项目')
                current = conversations.current(db, self.root, project_id)
                if not current or current['id'] != single:
                    raise HTTPException(409, '当前版本已变化，请刷新或恢复该版本后再提交')
            change = change.model_copy(
                update={
                    'file_ids': [single],
                    'project_id': project_id,
                    'file_id': None,
                    'current_file_id': None,
                }
            )
        return self.create_project_task(change)

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
            images = []
            for identity in dict.fromkeys(change.image_ids or []):
                row = db.execute(
                    "SELECT * FROM assets WHERE project_id=? AND id=?",
                    (project_id, identity),
                ).fetchone()
                if not row:
                    raise HTTPException(409, '图片不属于此项目或已删除')
                images.append(
                    {key: row[key] for key in ('id', 'name', 'mime', 'size', 'created_at')}
                )
            projects.name_from_prompt(db, project_id, change.prompt)
            state = dict(id=str(uuid4()), project_id=project_id, file=records[0] if records else None, files=records, images=images,
                         prompt=change.prompt, web_enabled=change.web_enabled, status='queued', stage=0, started_at=time.time(), finished_at=None,
                         error=None, result=None, steps=['读取会话', '理解请求', '处理内容', '保存结果'], tools=[])
            db.execute('INSERT INTO tasks VALUES (?,?)', (state['id'], json.dumps(state,ensure_ascii=False)))
            conversations.record_task(db,state)
        self.active = state
        self.cancel_requested = False
        self.runner = asyncio.create_task(self.run_project(state))
        return self.get(state['id'])

    async def run_project(self, state):
        prepared = []
        published = []
        local_sources = {}
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
                # Imported files are not part of the turn's attachment list.
                for index, existing in enumerate(inventories):
                    if existing.get('id') == file['id']:
                        inventories[index] = item
                        break
                else:
                    inventories.append(item)
                return item
            state['metrics'] = []
            model.call_metrics.set(state['metrics'])
            context = conversations.context(self.root, state['project_id'], None)
            await self.checkpoint(state, 1)
            async def web_progress(label):
                state['web_activity'] = label
                await self.checkpoint(state, 1)
            web_session = web.WebSession(state.get('web_enabled', True), web_progress)
            async def edit_progress(label, round_number, stage=1):
                state['activity'] = label
                state['repair_round'] = round_number
                await self.checkpoint(state, stage)
            async def creation_progress(label, attempt):
                state['activity'] = label
                state['creation_attempt'] = attempt
                state['steps'][1] = '生成文档内容'
                state['steps'][2] = '校验文档'
                await self.checkpoint(state, 1)
            async def generate():
                creation.progress_context.set(creation_progress)
                edit_workflow.runtime.set((self.root, edit_progress))
                compacted, memory = await model.compact_memory(context)
                if memory:
                    value, through = memory
                    conversations.store_memory(self.root,state['project_id'],value.summary,through,value.preferences)
                model.conversation_context.set(compacted)
                return await self.run_agent(
                    state, compacted, web_session, load_inventory, prepared, local_sources
                )

            self.model_call = asyncio.create_task(generate())
            try:
                outcome = await self.model_call
            finally:
                self.model_call = None
                state['web_metrics'] = web_session.metrics
                state['web_activity'] = None
                state['activity'] = None
            await self.checkpoint(state, 2)
            result = dict(kind=outcome.kind, text=outcome.text, **web_session.result_fields())
            by_id = {item[2]['id']: item for item in prepared}
            outputs = []
            for artifact in state.get('result_outputs') or []:
                item = by_id.get(artifact.get('file_id'))
                if not item:
                    continue
                outputs.append(
                    dict(
                        file=FileInfo(**item[2]).model_dump(),
                        changes=artifact.get('changes') or [],
                        kind=artifact.get('kind'),
                        renamed=bool(artifact.get('renamed')),
                    )
                )
            if outputs:
                result['outputs'] = outputs
                if len(outputs) == 1:
                    result.update(outputs[0])
            if local_sources and outputs:
                local_edit = self._local_edit(outputs, local_sources)
                if local_edit:
                    result['local_edit'] = local_edit
                    result['text'] = (result.get('text') or '') + (
                        f"\n\n原文件 {local_edit['path']} 未改动。"
                        "如需覆盖，请点击结果下方的「覆盖源文件」按钮。"
                    )
                else:
                    result['text'] = (result.get('text') or '') + (
                        "\n\n原文件在导入后已变化或不存在，未提供覆盖选项。"
                    )
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

    def _local_edit(self, outputs, local_sources):
        """Map a generated version back to a local source imported this turn."""
        from . import local_fs

        for output in outputs:
            source_id = output['file'].get('source_file_id')
            meta = local_sources.get(source_id)
            if not meta:
                continue
            try:
                current = local_fs.file_identity(local_fs.resolve_path(meta['path'], 'file'))
            except local_fs.LocalError:
                return None
            if (
                current['sha256'] == meta['sha256']
                and current['dev'] == meta['dev']
                and current['ino'] == meta['ino']
            ):
                return dict(
                    path=meta['path'],
                    name=meta['name'],
                    source_file_id=source_id,
                    new_file_id=output['file']['id'],
                    source_sha256=meta['sha256'],
                    dev=meta['dev'],
                    ino=meta['ino'],
                )
            return None
        return None

    async def run_agent(
        self, state, conversation, web_session, load_inventory, prepared, local_sources
    ):
        """Run the tool loop; every task goes through this path."""
        from . import agent, tools as tool_module
        from .projects import source_files

        async def list_sources():
            with database.connection(self.root) as db:
                return [dict(item) for item in source_files(self.root, db, state['project_id'])]

        async def load_by_id(file_id):
            # Reading is project-scoped: a file imported in an earlier turn (or
            # listed by list_files) stays readable without re-attaching it.
            def lookup():
                with database.connection(self.root) as db:
                    if conversations.project_for(db, file_id) != state['project_id']:
                        return None
                return database.get(self.root, file_id)

            record = await asyncio.to_thread(lookup)
            if record is None:
                raise tool_module.ToolError('forbidden', '该文件不在当前项目')
            return await load_inventory({'id': record['id'], 'name': record['name']})

        async def load_image(image_id):
            from .images import load_asset

            try:
                return await asyncio.to_thread(load_asset, self.root, image_id)
            except HTTPException as exc:
                raise tool_module.ToolError('missing', str(exc.detail)) from exc

        def register_import(record):
            files = state.setdefault('files', [])
            if not any(item.get('id') == record['id'] for item in files):
                files.append(FileInfo(**record).model_dump())

        context = tool_module.ToolContext(
            files=[dict(id=f['id'], name=f['name']) for f in state.get('files') or []],
            load_inventory=load_by_id,
            list_sources=list_sources,
            web=web_session,
            allowed_files={f['id'] for f in state.get('files') or []},
            user_text=state['prompt'],
            root=self.root,
            project_id=state['project_id'],
            prepared=prepared,
            images=[dict(id=i['id'], name=i['name'], mime=i['mime']) for i in state.get('images') or []],
            allowed_images={i['id'] for i in state.get('images') or []},
            load_image=load_image,
            local_sources=local_sources,
            on_import=register_import,
        )

        async def emit(_event):
            await self.checkpoint(state, 1)

        result = await agent.run(
            state['prompt'],
            context=context,
            conversation=conversation,
            events=state.setdefault('tools', []),
            on_event=emit,
            cancelled=lambda: self.cancel_requested,
        )
        state['tools'] = result.tools
        state['result_outputs'] = context.artifacts
        return result

    def delete(self, task_id):
        """Delete one turn: the prompt, the reply and the task record.

        Generated files stay in the project on purpose; only the conversation
        turn (and the messages the model sees) is removed.
        """
        state = self.get(task_id)
        if state['status'] in ACTIVE:
            raise HTTPException(409, '任务正在执行，请先停止再删除')
        with database.connection(self.root) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM tasks WHERE id=?', (task_id,))
            db.execute('DELETE FROM messages WHERE task_id=?', (task_id,))
        return dict(id=task_id, deleted=True)

    def overwrite_local(self, task_id):
        """Overwrite the original local file after the user confirmed it in the UI."""
        from . import local_fs
        with database.connection(self.root) as db:
            row = db.execute('SELECT payload FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, '任务不存在，可能已被清理')
        state = json.loads(row['payload'])
        result = state.get('result') or {}
        local = result.get('local_edit')
        if state.get('status') != 'completed' or not local:
            raise HTTPException(400, '该轮没有可覆盖的本地文件结果')
        if local.get('overwritten'):
            raise HTTPException(409, '该源文件已覆盖，无需重复操作')
        if not local_fs.write_enabled():
            raise HTTPException(403, '覆盖源文件功能已关闭（OFFICE_AGENT_LOCAL_WRITE=1 开启）')
        with database.connection(self.root) as db:
            if db.execute("SELECT 1 FROM tasks WHERE json_extract(payload,'$.status') IN ('queued','running','cancelling') LIMIT 1").fetchone():
                raise HTTPException(409, '任务执行期间不能覆盖源文件，请等待结束或停止后重试')
        try:
            path = local_fs.resolve_path(local.get('path', ''), 'file')
        except local_fs.LocalError as exc:
            raise HTTPException(exc.status, exc.message) from exc
        record = database.get(self.root, local.get('new_file_id', ''))
        if not record:
            raise HTTPException(400, '生成文件已删除，无法覆盖源文件')
        generated = self.root / 'uploads' / record['storage_key']
        if not generated.is_file():
            raise HTTPException(400, '生成文件已丢失，无法覆盖源文件')
        expected = dict(
            sha256=local.get('source_sha256'),
            dev=local.get('dev'),
            ino=local.get('ino'),
        )
        try:
            backup = local_fs.write_in_place(path, generated.read_bytes(), expected)
        except local_fs.LocalError as exc:
            raise HTTPException(exc.status, exc.message) from exc
        updated = dict(local, overwritten=dict(
            at=datetime.now(timezone.utc).isoformat(), backup=str(backup)))
        try:
            with database.connection(self.root) as db:
                db.execute('BEGIN IMMEDIATE')
                fresh = db.execute('SELECT payload FROM tasks WHERE id=?', (task_id,)).fetchone()
                value = json.loads(fresh['payload']) if fresh else state
                value.setdefault('result', {})['local_edit'] = updated
                value['result']['text'] = (value['result'].get('text') or '') + f"\n\n已覆盖源文件：{path}（备份：{backup}）"
                db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(value, ensure_ascii=False), task_id))
        except Exception:
            try:
                local_fs.restore(backup, path)
            except OSError:
                pass
            raise HTTPException(500, '覆盖完成但记录保存失败，已尝试还原原文件') from None
        return dict(path=str(path), backup=str(backup), text='已覆盖源文件，并保留备份。')

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


@router.post('/{task_id}/overwrite-local')
async def overwrite_local(request: Request, task_id: str):
    return request.app.state.tasks.overwrite_local(task_id)


@router.delete('/{task_id}')
async def delete(request: Request, task_id: str):
    return request.app.state.tasks.delete(task_id)
