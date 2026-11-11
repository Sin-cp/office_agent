"""Deleting one conversation turn removes the prompt and the reply, not files."""
import asyncio
import threading
import time
import unittest

import loop_support
import test_files


class TaskDeleteTests(unittest.TestCase):
    setUp = test_files.FilesTest.setUp
    tearDown = test_files.FilesTest.tearDown

    def project(self):
        response = self.client.post('/api/projects')
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def turn(self, project=None, prompt='你好'):
        with loop_support.patch_agent(loop_support.answer('正常聊天回答')):
            response = self.client.post('/api/tasks', json={'project_id': project, 'file_ids': [], 'prompt': prompt})
            self.assertEqual(response.status_code, 202, response.text)
            return self.wait(response.json()['id'])

    def wait(self, identity):
        for _ in range(300):
            state = self.client.get('/api/tasks/' + identity).json()
            if state['status'] not in ('queued', 'running', 'cancelling'):
                return state
            time.sleep(.01)
        self.fail('Task timed out')

    def messages(self, project):
        return self.client.get(f'/api/projects/{project}/messages').json()['items']

    def test_delete_removes_one_turn_and_keeps_the_rest(self):
        project = self.project()
        kept = self.turn(project, '留下的一轮')
        removed = self.turn(project, '要删掉的一轮')
        self.assertEqual(len(self.messages(project)), 4)

        response = self.client.delete(f"/api/tasks/{removed['id']}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'id': removed['id'], 'deleted': True})

        self.assertEqual(self.client.get(f"/api/tasks/{removed['id']}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/tasks/{kept['id']}").status_code, 200)

        history = self.client.get(f'/api/projects/{project}/history').json()
        self.assertEqual([item['id'] for item in history['items']], [kept['id']])

        remaining = self.messages(project)
        self.assertEqual(len(remaining), 2)
        self.assertTrue(all(item['task_id'] == kept['id'] for item in remaining))
        self.assertEqual(remaining[0]['content']['text'], '留下的一轮')

        self.assertEqual(self.client.delete(f"/api/tasks/{removed['id']}").status_code, 404)
        self.assertEqual(self.client.delete('/api/tasks/missing').status_code, 404)

    def test_delete_keeps_files_created_by_the_turn(self):
        project = self.project()
        uploaded = self.client.post('/api/files', params={'project_id': project}, files={'file': ('原文.docx', test_files.docx())})
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        source = uploaded.json()

        with loop_support.patch_agent(loop_support.edit([source['id']])):
            response = self.client.post('/api/tasks', json={'project_id': project, 'file_ids': [source['id']], 'prompt': '改一下正文'})
            self.assertEqual(response.status_code, 202, response.text)
            state = self.wait(response.json()['id'])
        self.assertEqual(state['status'], 'completed', state)
        output = state['result']['file']

        self.assertEqual(self.client.delete(f"/api/tasks/{state['id']}").status_code, 200)
        files = self.client.get(f'/api/projects/{project}/files').json()['items']
        self.assertEqual([item['current_file']['id'] for item in files], [output['id']])
        self.assertEqual(self.client.get(f"/api/files/{output['id']}/download").status_code, 200)
        self.assertEqual(self.messages(project), [])

    def test_delete_refuses_a_running_task(self):
        started = threading.Event()

        async def slow(prompt, context):
            started.set()
            await asyncio.sleep(60)

        with loop_support.patch_agent(slow):
            response = self.client.post('/api/tasks', json={'project_id': None, 'file_ids': [], 'prompt': '慢慢想'})
            self.assertEqual(response.status_code, 202, response.text)
            state = response.json()
            self.assertTrue(started.wait(2))
            refused = self.client.delete(f"/api/tasks/{state['id']}")
            self.assertEqual(refused.status_code, 409)
            self.assertIn('正在执行', refused.json()['detail'])
            self.assertEqual(self.client.get(f"/api/tasks/{state['id']}").status_code, 200)
            self.client.post(f"/api/tasks/{state['id']}/cancel")
            self.assertEqual(self.wait(state['id'])['status'], 'cancelled')
        # A stopped turn can be deleted afterwards.
        self.assertEqual(self.client.delete(f"/api/tasks/{state['id']}").status_code, 200)


if __name__ == '__main__':
    unittest.main()
