import asyncio
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database, model
import test_files
from test_documents import example


class HistoryTests(unittest.TestCase):
    setUp = test_files.FilesTest.setUp
    tearDown = test_files.FilesTest.tearDown
    upload = test_files.FilesTest.upload

    def history(self, file, **params):
        response = self.client.get(f"/api/files/{file['id']}/history", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def run_task(self, file, failure=False):
        async def answer(prompt, blocks):
            if failure:
                raise model.ModelError('测试失败')
            return model.Suggestion(status='edit', block_id=blocks[1]['id'], new_text=blocks[1]['text'] + '修改', explanation='调整表达')
        with patch('app.model.suggest', side_effect=answer):
            response = self.client.post('/api/tasks', json={'file_id':file['id'],'prompt':'修改正文'})
            self.assertEqual(response.status_code, 202, response.text)
            task_id = response.json()['id']
            for _ in range(200):
                state = self.client.get(f'/api/tasks/{task_id}').json()
                if state['status'] in ('failed','completed'):
                    return state
                time.sleep(.01)
        self.fail('Task timed out')

    def test_empty_history_and_isolation(self):
        a, b = self.upload(example()).json(), self.upload(example()).json()
        empty = self.history(a)
        self.assertEqual(empty['items'], [])
        self.assertEqual(empty['current_file']['id'], a['id'])
        self.run_task(a, failure=True)
        self.assertEqual(self.history(a)['items'][0]['status'], 'failed')
        self.assertEqual(self.history(b)['total'], 0)
        self.assertEqual(self.client.get('/api/files/missing/history').status_code, 404)

    def test_version_chain_pagination_deletion_and_restart(self):
        original = self.upload(example()).json()
        first = self.run_task(original)
        second = self.run_task(first['result']['file'])
        latest = second['result']['file']
        group = self.history(original)['history_id']
        self.assertEqual(self.history(latest)['history_id'], group)
        self.assertEqual(self.history(original)['current_file']['id'], latest['id'])
        newest = self.history(original, limit=1)
        self.assertEqual(newest['items'][0]['id'], second['id'])
        # Insert a new task between pagination requests: cursor remains stable.
        self.run_task(latest, failure=True)
        older = self.history(original, limit=1, before=newest['next_cursor'])
        self.assertEqual(older['items'][0]['id'], first['id'])
        self.assertIsNone(older['next_cursor'])
        self.assertEqual(self.client.delete(f"/api/files/{first['result']['file']['id']}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/files/{original['id']}").status_code, 200)
        database.initialize(Path(self.temp.name))
        history = self.history(latest)
        self.assertEqual(history['history_id'], group)
        self.assertEqual(history['total'], 3)
        self.assertFalse(history['items'][0]['result']['file']['available'])
        self.assertFalse(self.client.get(f"/api/tasks/{first['id']}").json()['result']['file']['available'])
        self.assertEqual(self.history(original)['current_file']['id'], latest['id'])
        self.assertEqual(self.client.get(f"/api/files/{latest['id']}/history?before=wrong").status_code, 400)

    def test_deleted_latest_falls_back(self):
        original = self.upload(example()).json()
        result = self.run_task(original)['result']['file']
        self.client.delete(f"/api/files/{result['id']}")
        self.assertEqual(self.history(result)['current_file']['id'], original['id'])
        self.client.delete(f"/api/files/{original['id']}")
        history = self.history(result)
        self.assertIsNone(history['current_file'])
        self.assertEqual(history['total'], 1)

    def test_active_task_and_server_side_deletion_lock(self):
        original = self.upload(example()).json()
        unrelated = self.upload(example()).json()
        started = threading.Event()
        async def slow(*args):
            started.set()
            await asyncio.sleep(60)
        with patch('app.model.suggest', side_effect=slow):
            state = self.client.post('/api/tasks',json={'file_id':original['id'],'prompt':'修改'}).json()
            self.assertTrue(started.wait(2))
            self.assertEqual(self.history(original)['active_task']['id'],state['id'])
            for file in [original, unrelated]:
                self.assertEqual(self.client.delete(f"/api/files/{file['id']}").status_code,409)
            self.client.post(f"/api/tasks/{state['id']}/cancel")
            for _ in range(200):
                if self.client.get(f"/api/tasks/{state['id']}").json()['status']=='cancelled':
                    break
                time.sleep(.01)
        self.assertIsNone(self.history(original)['active_task'])
        self.assertEqual(self.history(original)['items'][0]['status'],'cancelled')
        self.assertEqual(self.client.delete(f"/api/files/{original['id']}").status_code,200)

    def test_migrate_legacy_tasks_recovers_deleted_links(self):
        original = self.upload(example()).json()
        first = self.run_task(original)
        second = self.run_task(first['result']['file'])
        self.client.delete(f"/api/files/{first['result']['file']['id']}")
        root = Path(self.temp.name)
        with database.connection(root) as db:
            db.execute('DROP TABLE file_history')
        database.initialize(root)
        group = self.history(original)['history_id']
        self.assertEqual(self.history(second['result']['file'])['history_id'],group)
        self.assertEqual(self.history(second['result']['file'])['total'],2)
        database.initialize(root)
        self.assertEqual(self.history(original)['history_id'],group)

    def test_missing_parent_stays_independent(self):
        original = self.upload(example()).json()
        root = Path(self.temp.name)
        with database.connection(root) as db:
            db.execute('UPDATE files SET source_file_id=? WHERE id=?',('unknown-parent',original['id']))
            db.execute('DROP TABLE file_history')
        database.initialize(root)
        self.assertEqual(self.history(original)['history_id'],original['id'])
