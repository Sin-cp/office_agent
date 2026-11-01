import asyncio
import io
import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from docx import Document
from app import database, documents, model
from app.tasks import Manager
import test_files
from test_documents import example


class TaskTests(unittest.TestCase):
    setUp = test_files.FilesTest.setUp
    tearDown = test_files.FilesTest.tearDown
    upload = test_files.FilesTest.upload

    def start(self):
        self.original = example()
        self.file = self.upload(self.original).json()
        return self.client.post('/api/tasks', json={'file_id': self.file['id'], 'prompt': '润色第一段正文'})

    async def suggestion(self, prompt, blocks):
        return model.Suggestion(status='edit', block_id=blocks[1]['id'], new_text='更正式的表达', explanation='调整表达')

    def wait(self, task_id):
        for _ in range(200):
            state = self.client.get('/api/tasks/' + task_id).json()
            if state['status'] not in ('queued', 'running', 'cancelling'):
                return state
            time.sleep(.01)
        self.fail('Task did not terminate')

    def test_success_and_completed_cancel(self):
        with patch('app.model.suggest', side_effect=self.suggestion):
            response = self.start()
            self.assertEqual(response.status_code, 202)
            state = self.wait(response.json()['id'])
        self.assertEqual(state['status'], 'completed', state)
        result = state['result']['file']
        output = self.client.get(f"/api/files/{result['id']}/download").content
        self.assertEqual(Document(io.BytesIO(output)).paragraphs[1].text, '更正式的表达')
        self.assertEqual(self.client.get(f"/api/files/{self.file['id']}/download").content, self.original)
        self.assertEqual(self.client.post(f"/api/tasks/{state['id']}/cancel").json()['status'], 'completed')
        self.assertEqual(result['source_file_id'], self.file['id'])

    def test_cancel_model_and_duplicate(self):
        started = threading.Event()
        async def slow(*args):
            started.set()
            await asyncio.sleep(60)
        with patch('app.model.suggest', side_effect=slow):
            state = self.start().json()
            self.assertTrue(started.wait(2))
            self.assertEqual(self.client.post('/api/tasks', json={'file_id': self.file['id'], 'prompt': '再次修改'}).status_code, 409)
            self.client.post(f"/api/tasks/{state['id']}/cancel")
            self.assertEqual(self.wait(state['id'])['status'], 'cancelled')
        self.assertEqual(self.client.get('/api/files').json()['total'], 1)

    def test_cancel_during_generation(self):
        started, release = threading.Event(), threading.Event()
        original = documents.prepare_edit
        def slow(*args):
            prepared = original(*args)
            started.set()
            release.wait(3)
            return prepared
        with patch('app.model.suggest', side_effect=self.suggestion), patch('app.documents.prepare_edit', side_effect=slow):
            state = self.start().json()
            self.assertTrue(started.wait(2))
            self.client.post(f"/api/tasks/{state['id']}/cancel")
            release.set()
            self.assertEqual(self.wait(state['id'])['status'], 'cancelled')
        self.assertEqual(self.client.get('/api/files').json()['total'], 1)
        self.assertFalse(list((Path(self.temp.name) / 'uploads').glob('*.tmp')))

    def test_invalid_unknown_uneditable_unchanged_and_unable(self):
        for kind in ['unknown', 'uneditable', 'unchanged', 'unable', 'control']:
            async def response(prompt, blocks):
                index = 4 if kind == 'uneditable' else 1
                return model.Suggestion(status='unable' if kind == 'unable' else 'edit',
                    block_id='wrong' if kind == 'unknown' else blocks[index]['id'],
                    new_text=blocks[index]['text'] if kind == 'unchanged' else '\0' if kind == 'control' else '修改', explanation='无法执行')
            with self.subTest(kind=kind), patch('app.model.suggest', side_effect=response):
                state = self.wait(self.start().json()['id'])
                self.assertEqual(state['status'], 'failed', state)
                self.assertIsNone(state['result'])
        self.assertEqual(self.client.get('/api/files').json()['total'], 5)

    def test_validation_and_missing(self):
        for payload in [{}, {'file_id': 'a', 'prompt': '  '}, {'file_id':'a', 'prompt':'x'*2001}]:
            self.assertEqual(self.client.post('/api/tasks', json=payload).status_code, 422)
        self.assertEqual(self.client.post('/api/tasks', json={'file_id':'missing', 'prompt':'改'}).status_code,404)
        self.assertEqual(self.client.get('/api/tasks/missing').status_code,404)

    def test_model_error_ends_without_file(self):
        with patch('app.model.suggest', side_effect=model.ModelError('模型响应超时')):
            state = self.wait(self.start().json()['id'])
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['error'], '模型响应超时')
        self.assertEqual(self.client.get('/api/files').json()['total'], 1)

    def test_publication_transaction_rolls_back_file_and_result(self):
        with database.connection(Path(self.temp.name)) as db:
            db.execute("CREATE TRIGGER reject_completion BEFORE UPDATE ON tasks WHEN json_extract(NEW.payload, '$.status') = 'completed' BEGIN SELECT RAISE(ABORT, 'test'); END")
        with patch('app.model.suggest', side_effect=self.suggestion), self.assertLogs('app.documents', level='ERROR'):
            state = self.wait(self.start().json()['id'])
        self.assertEqual(state['status'], 'failed')
        self.assertIsNone(state['result'])
        self.assertEqual(self.client.get('/api/files').json()['total'], 1)
        self.assertEqual(len(list((Path(self.temp.name) / 'uploads').iterdir())), 1)

    def test_restart_marks_interrupted(self):
        root = Path(self.temp.name)
        state = dict(id='interrupted', status='running', started_at=time.time(), finished_at=None)
        with database.connection(root) as db:
            db.execute('INSERT INTO tasks VALUES (?, ?)', ('interrupted', json.dumps(state)))
        restored = Manager(root).get('interrupted')
        self.assertEqual(restored['status'], 'failed')
        self.assertIn('服务重启', restored['error'])


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_key(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}):
            with self.assertRaisesRegex(model.ModelError, '未配置'):
                await model.suggest('修改', [])

    async def test_transport_and_schema(self):
        import httpx2 as httpx
        for status, content, expected in [
            (401, {}, '鉴权'), (402, {}, '余额'), (429, {}, '受限'), (500, {}, '调用失败'),
            (200, {'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}, '未完整'),
            (200, {'choices':[{'finish_reason':'stop','message':{'content':'not json'}}]}, '格式无效'),
        ]:
            with self.subTest(status=status, expected=expected), patch.dict(os.environ, {'DEEPSEEK_API_KEY':'test-only'}), patch('app.model.httpx.AsyncClient') as client:
                client.return_value.__aenter__.return_value.post = AsyncMock(return_value=httpx.Response(status, json=content))
                with self.assertRaisesRegex(model.ModelError, expected):
                    await model.suggest('修改', [])

    async def test_success_context_limit_and_timeout(self):
        import httpx2 as httpx
        content = {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(dict(status='edit', block_id='p:1', new_text='新', explanation='润色'))}}]}
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY':'test-only'}), patch('app.model.httpx.AsyncClient') as client:
            post = AsyncMock(return_value=httpx.Response(200,json=content))
            client.return_value.__aenter__.return_value.post = post
            self.assertEqual((await model.suggest('修改', [])).new_text, '新')
            with self.assertRaisesRegex(model.ModelError, '40,000'):
                await model.suggest('修改', ['x'*40001])
            self.assertEqual(post.await_count, 1)
            post.side_effect = httpx.ReadTimeout('timeout')
            with self.assertRaisesRegex(model.ModelError, '超时'):
                await model.suggest('修改', [])
