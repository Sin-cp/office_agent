"""Single-process durable task state with cooperative cancellation."""
import asyncio
import json
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from . import database, documents, model, history
from .files import FileInfo

router = APIRouter(prefix='/api/tasks', tags=['tasks'])
ACTIVE = {'queued', 'running', 'cancelling'}
STAGES = ['读取文档', '生成修改内容', '校验修改', '生成文件']


class TaskInput(BaseModel):
    file_id: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=2000)

    @field_validator('prompt')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('请输入修改要求')
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
            db.execute("CREATE INDEX IF NOT EXISTS tasks_file ON tasks(json_extract(payload, '$.file.id'))")
            for row in db.execute('SELECT id, payload FROM tasks').fetchall():
                state = json.loads(row['payload'])
                if state['status'] in ACTIVE:
                    state.update(status='failed', error='服务重启，任务已中断，请重新提交', finished_at=time.time())
                    db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(state, ensure_ascii=False), row['id']))
        # Only our own task temporary files are cleaned after process restart.
        for path in (root / 'uploads').glob('task-*.tmp'):
            path.unlink(missing_ok=True)

    def save(self, state):
        with database.connection(self.root) as db:
            db.execute('INSERT OR REPLACE INTO tasks VALUES (?, ?)', (state['id'], json.dumps(state, ensure_ascii=False)))

    def get(self, task_id):
        with database.connection(self.root) as db:
            row = db.execute('SELECT payload FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, '任务不存在，可能已被清理')
        with database.connection(self.root) as db:
            return history.task_view(self.root, db, json.loads(row['payload']))

    def create(self, change):
        if self.runner and not self.runner.done():
            raise HTTPException(409, '已有任务正在执行，请等待完成或停止后重试')
        with database.connection(self.root) as db:
            db.execute('BEGIN IMMEDIATE')
            record = db.execute('SELECT * FROM files WHERE id=?', (change.file_id,)).fetchone()
            if not record:
                raise HTTPException(404, '文件不存在，请重新选择')
            state = dict(id=str(uuid4()), file=FileInfo(**dict(record)).model_dump(), prompt=change.prompt,
                         status='queued', stage=0, started_at=time.time(), finished_at=None,
                         error=None, result=None, steps=STAGES)
            db.execute('INSERT INTO tasks VALUES (?, ?)', (state['id'], json.dumps(state, ensure_ascii=False)))
        self.active = state
        self.cancel_requested = False
        self.runner = asyncio.create_task(self.run(state))
        return self.get(state['id'])

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
            _, _, _, _, revision, blocks = loaded
            await self.checkpoint(state, 1)
            self.model_call = asyncio.create_task(model.suggest(state['prompt'], blocks))
            try:
                suggestion = await self.model_call
            finally:
                self.model_call = None
            await self.checkpoint(state, 2)
            if suggestion.status == 'unable':
                raise model.ModelError(suggestion.explanation)
            selected = next((b for b in blocks if b['id'] == suggestion.block_id), None)
            if selected is None:
                raise model.ModelError('模型返回的段落不属于当前文件')
            if not selected['editable']:
                raise model.ModelError(selected['reason'])
            change = documents.EditRequest(block_id=selected['id'], revision=revision,
                       expected_text=selected['text'], new_text=suggestion.new_text)
            if change.new_text == selected['text']:
                raise model.ModelError('文字没有变化，无需生成新文件')
            await self.checkpoint(state, 3)
            prepared = await asyncio.to_thread(documents.prepare_edit, self.root, state['file']['id'], change, True)
            await self.checkpoint(state, 3)
            # No await from final cancellation check through publication/state commit.
            # Same event loop serializes cancellation against this short critical section.
            if not database.get(self.root, state['file']['id']):
                raise model.ModelError('源文件已删除，任务终止')
            result = prepared[2]
            state.update(status='completed', finished_at=time.time(), result=dict(file=FileInfo(**result).model_dump(),
                position=selected['position'], summary=f"已替换第 {selected['position']} 段正文文字。",
                explanation=suggestion.explanation, before=selected['text'], after=change.new_text))
            def persist(record):
                # File metadata and completed task become visible together.
                with database.connection(self.root) as db:
                    database.insert_record(db, record)
                    db.execute('UPDATE tasks SET payload=? WHERE id=?', (json.dumps(state, ensure_ascii=False), state['id']))
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
