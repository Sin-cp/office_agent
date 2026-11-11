"""Project conversations and multi-document transactional publication."""
import asyncio
import io
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from docx import Document
from app import agent, conversations, database, documents, model, operations
from app.tasks import Manager
import loop_support
import test_files
from test_documents import example


class ProjectChatTests(unittest.TestCase):
    setUp = test_files.FilesTest.setUp
    tearDown = test_files.FilesTest.tearDown

    def create(self):
        response = self.client.post('/api/projects')
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def upload(self, project):
        response = self.client.post('/api/files', params={'project_id': project}, files={'file': ('same.docx', example())})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def wait(self, identity):
        for _ in range(300):
            state = self.client.get('/api/tasks/'+identity).json()
            if state['status'] not in ('queued', 'running', 'cancelling'):
                return state
            time.sleep(.01)
        self.fail('Task timed out')

    def turn(self, project=None, files=None, reply=None, prompt='你好'):
        with loop_support.patch_agent(reply or loop_support.answer('正常聊天回答')):
            response = self.client.post('/api/tasks', json={'project_id':project,'file_ids':files or [],'prompt':prompt})
            self.assertEqual(response.status_code, 202, response.text)
            return self.wait(response.json()['id'])

    def test_plain_chat_title_rename_restart_paging_and_delete(self):
        prompt = '  ' + '标题内容'*10 + '  '
        first = self.turn(prompt=prompt)
        self.assertEqual(first['status'], 'completed', first)
        self.assertIsNone(first['file'])
        project = first['project_id']
        listing = self.client.get('/api/projects').json()['items'][0]
        self.assertEqual(listing['name'], prompt.strip()[:20]+'…')
        self.assertEqual(listing['file_count'],0)
        self.assertEqual(self.client.patch('/api/projects/'+project,json={'name':'自定义名字'}).status_code,200)
        self.assertEqual(self.client.patch('/api/projects/'+project,json={'name':'  '}).status_code,422)
        async def follow(prompt, context):
            self.assertEqual(context.files,[])
            self.assertTrue(any(m['content'].get('text')=='正常聊天回答' for m in model.conversation_context.get()['messages']))
            return agent.AgentResult(text='记得之前的聊天')
        second = self.turn(project,reply=follow)
        self.assertEqual(second['status'],'completed',second)
        root=Path(self.temp.name)
        database.initialize(root); database.initialize(root)
        listing=self.client.get('/api/projects').json()['items'][0]
        self.assertEqual(listing['name'],'自定义名字')
        page=self.client.get(f'/api/projects/{project}/history?limit=1').json()
        self.assertEqual(page['items'][0]['id'],second['id'])
        older=self.client.get(f"/api/projects/{project}/history?before={page['next_cursor']}").json()
        self.assertEqual(older['items'][0]['id'],first['id'])
        self.assertEqual(self.client.delete('/api/projects/'+project).status_code,200)
        self.assertEqual(self.client.get('/api/tasks/'+first['id']).status_code,404)

    def test_append_same_named_files_multi_edit_and_restart(self):
        first=self.turn()
        project=first['project_id']
        a,b=self.upload(project),self.upload(project)
        state=self.turn(project,[a['id'],b['id']],loop_support.edit([a['id'],b['id']]),'修改全部文件的标题')
        self.assertEqual(state['status'],'completed',state)
        outputs=state['result']['outputs']
        self.assertEqual(len(outputs),2)
        for output in outputs:
            data=self.client.get('/api/files/'+output['file']['id']+'/download').content
            self.assertEqual(Document(io.BytesIO(data)).paragraphs[0].text,'项目背景修改')
        for source in [a,b]:
            original=self.client.get('/api/files/'+source['id']+'/download').content
            self.assertEqual(Document(io.BytesIO(original)).paragraphs[0].text,'项目背景')
        database.initialize(Path(self.temp.name)); database.initialize(Path(self.temp.name))
        self.assertEqual(self.client.get('/api/projects').json()['total'],1)
        sources=self.client.get(f'/api/projects/{project}/files').json()['items']
        self.assertEqual(len(sources),2)
        self.assertEqual({s['current_file']['id'] for s in sources},{o['file']['id'] for o in outputs})
        history=self.client.get(f'/api/projects/{project}/history').json()
        self.assertEqual(len(history['items']),2)
        self.assertEqual(len(history['items'][-1]['result']['outputs']),2)
        self.assertEqual(self.client.post('/api/tasks',json={'project_id':project,'file_ids':[a['id']],'prompt':'修改'}).status_code,409)
        # Clearing attachments is represented by an explicit empty list, never defaulting to all project files.
        async def chat(prompt, context):
            self.assertEqual(context.files,[])
            return agent.AgentResult(text='不读取附件')
        self.assertEqual(self.turn(project,[],chat)['status'],'completed')
        self.assertEqual(self.client.delete('/api/projects/'+project).status_code,200)
        self.assertEqual(list((Path(self.temp.name)/'uploads').iterdir()),[])
        database.initialize(Path(self.temp.name))
        self.assertEqual(self.client.get('/api/projects').json()['total'],0)

    def assert_unchanged(self, project, files):
        sources=self.client.get(f'/api/projects/{project}/files').json()['items']
        self.assertEqual({s['current_file']['id'] for s in sources},{f['id'] for f in files})
        self.assertEqual(self.client.get('/api/files').json()['total'],len(files))
        self.assertEqual(len(list((Path(self.temp.name)/'uploads').iterdir())),len(files))

    def test_invalid_second_document_rolls_back_prepared_first(self):
        project=self.create(); files=[self.upload(project),self.upload(project)]
        async def invalid(prompt, context):
            await loop_support.edit([files[0]['id']])(prompt, context)
            raise model.ModelError('修改目标不属于本轮选中文件')
        state=self.turn(project,[f['id'] for f in files],invalid)
        self.assertEqual(state['status'],'failed',state)
        self.assert_unchanged(project,files)

    def test_database_failure_and_second_move_failure_are_atomic(self):
        project=self.create(); files=[self.upload(project),self.upload(project)]
        root=Path(self.temp.name)
        with database.connection(root) as db:
            db.execute("CREATE TRIGGER reject_completion BEFORE UPDATE ON tasks WHEN json_extract(NEW.payload,'$.status')='completed' BEGIN SELECT RAISE(ABORT,'test'); END")
        state=self.turn(project,[f['id'] for f in files],loop_support.edit([f['id'] for f in files]))
        self.assertEqual(state['status'],'failed',state); self.assert_unchanged(project,files)
        with database.connection(root) as db: db.execute('DROP TRIGGER reject_completion')
        replace=Path.replace; moved=0
        def fail_second(path,target):
            nonlocal moved
            if path.suffix == '.tmp':
                moved+=1
                if moved==2: raise OSError('Cannot publish second output')
            return replace(path,target)
        with patch.object(Path,'replace',fail_second):
            state=self.turn(project,[f['id'] for f in files],loop_support.edit([f['id'] for f in files]))
        self.assertEqual(state['status'],'failed',state); self.assert_unchanged(project,files)
        self.assertFalse(list(root.glob('publication-*')))

    def test_cancel_during_generation_does_not_publish_any_file(self):
        project=self.create(); files=[self.upload(project),self.upload(project)]
        started=threading.Event()
        async def slow(prompt, context):
            started.set()
            await asyncio.sleep(60)
        with loop_support.patch_agent(slow):
            created=self.client.post('/api/tasks',json={'project_id':project,'file_ids':[f['id'] for f in files],'prompt':'修改全部'}).json()
            self.assertTrue(started.wait(2))
            self.client.post('/api/tasks/'+created['id']+'/cancel')
            state=self.wait(created['id'])
        self.assertEqual(state['status'],'cancelled',state); self.assert_unchanged(project,files)

    def test_foreign_file_and_missing_project_rejected(self):
        a,b=self.create(),self.create(); file=self.upload(b)
        self.assertEqual(self.client.post('/api/tasks',json={'project_id':a,'file_ids':[file['id']],'prompt':'修改'}).status_code,409)
        self.assertEqual(self.client.post('/api/files?project_id=missing',files={'file':('x.docx',example())}).status_code,404)

    def test_restart_publication_cleanup_preserves_committed_files(self):
        project=self.create(); file=self.upload(project); root=Path(self.temp.name)
        orphan='interrupted.docx'; (root/'uploads'/orphan).write_bytes(b'partial output')
        (root/'publication-interrupted.json').write_text(json.dumps([orphan,file['id']+'.docx']))
        Manager(root)
        self.assertFalse((root/'uploads'/orphan).exists())
        self.assertEqual(self.client.get('/api/files/'+file['id']+'/download').status_code,200)

    def test_general_question_in_file_project_answers_once(self):
        project=self.create(); file=self.upload(project)
        with loop_support.calls(loop_support.text_reply('一般知识回答')):
            response=self.client.post('/api/tasks',json={'project_id':project,'file_ids':[file['id']],
                'prompt':'我有五万块钱，帮我规划一下，我可以买哪些结婚用的三金','web_enabled':False})
            self.assertEqual(response.status_code,202,response.text)
            state=self.wait(response.json()['id'])
        self.assertEqual(state['status'],'completed',state)
        self.assertEqual(state['result']['kind'],'answer')
        self.assertEqual(state['result']['text'],'一般知识回答')

    def test_context_includes_project_snapshot_and_trims_changes(self):
        project=self.create(); file=self.upload(project); root=Path(self.temp.name)
        content=dict(text='已修改两份内容',kind='edit',status='completed',
            changes=[dict(op='replace',block_id=f'b{i}',location=f'正文第{i+1}段',before='旧'*300,after='新'*300) for i in range(10)],
            outputs=[dict(file=dict(id='x',name='same_修改版1.docx'),changes=[dict(op='replace')])],
            citations=[dict(file_id=file['id'],block_id='b',location='正文第1段',quote='引'*200) for _ in range(6)],
            web_sources=[dict(title=f'来源{i}',url='https://example.com') for i in range(6)])
        with database.connection(root) as db:
            db.execute("INSERT INTO messages(id,project_id,task_id,role,content,file_id,created_at) VALUES (?,?,?,?,?,?,?)",
                       ('m1',project,'t1','assistant',json.dumps(content,ensure_ascii=False),file['id'],1.0))
        built=conversations.context(root,project,file['id'])
        self.assertEqual(built['project']['name'],'新项目')
        self.assertEqual(built['project']['sources'][0]['name'],'same.docx')
        self.assertEqual(built['project']['sources'][0]['current']['name'],'same.docx')
        self.assertEqual(built['current_file']['name'],'same.docx')
        message=built['messages'][-1]['content']
        self.assertEqual(message['change_count'],10)
        self.assertEqual(len(message['changes']),3)
        self.assertLessEqual(max(len(item['before']) for item in message['changes']),160)
        self.assertEqual(message['outputs'][0]['file'],'same_修改版1.docx')
        self.assertEqual(len(message['citations']),3)
        self.assertEqual(len(message['web_sources']),3)
        with database.connection(root) as db:
            stored=json.loads(db.execute('SELECT content FROM messages WHERE project_id=?',(project,)).fetchone()['content'])
        self.assertEqual(len(stored['changes']),10)


class ProjectModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_call_does_not_request_json(self):
        import os
        class Response:
            status_code=200
            def json(self):
                return {'choices':[{'finish_reason':'stop','message':{'content':'你好，正常回答。'}}]}
        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def post(self,url,headers,json):
                self_test.assertNotIn('response_format',json)
                self_test.assertNotIn('输出 schema',json['messages'][0]['content'])
                return Response()
        self_test=self
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-only'}),patch('app.model.httpx.AsyncClient',return_value=Client()):
            self.assertEqual(await model.call('正常聊天',{'instruction':'你好'}),'你好，正常回答。')
