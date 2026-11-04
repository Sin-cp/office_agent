"""Project conversations and multi-document transactional publication."""
import io
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from docx import Document
from app import database, documents, model, operations
from app.tasks import Manager
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
        async def answer(prompt, inventories, load_inventory=None):
            return model.Response(kind='answer', text='正常聊天回答')
        with patch('app.model.respond_project', side_effect=reply or answer):
            response = self.client.post('/api/tasks', json={'project_id':project,'file_ids':files or [],'prompt':prompt})
            self.assertEqual(response.status_code, 202, response.text)
            return self.wait(response.json()['id'])

    async def edit_all(self, prompt, inventories, load_inventory=None):
        if load_inventory:
            inventories = [await load_inventory(d) for d in list(inventories)]
        return model.Response(kind='edit', text='全部修改完成', operations=[
            operations.Operation(op='replace', target=d['blocks'][0]['id'], expected_text=d['blocks'][0]['text'], text='新的标题')
            for d in inventories])

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
        async def follow(prompt, inventories, load_inventory=None):
            self.assertEqual(inventories,[])
            self.assertTrue(any(m['content'].get('text')=='正常聊天回答' for m in model.conversation_context.get()['messages']))
            return model.Response(kind='answer',text='记得之前的聊天')
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
        state=self.turn(project,[a['id'],b['id']],self.edit_all,'修改全部文件的标题')
        self.assertEqual(state['status'],'completed',state)
        outputs=state['result']['outputs']
        self.assertEqual(len(outputs),2)
        for output in outputs:
            data=self.client.get('/api/files/'+output['file']['id']+'/download').content
            self.assertEqual(Document(io.BytesIO(data)).paragraphs[0].text,'新的标题')
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
        async def chat(prompt, inventories, load_inventory=None):
            self.assertEqual(inventories,[])
            return model.Response(kind='answer',text='不读取附件')
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
        async def invalid(prompt, inventories, load_inventory=None):
            if load_inventory:
                inventories = [await load_inventory(d) for d in list(inventories)]
            result=await self.edit_all(prompt,inventories)
            result.operations[1].expected_text='错误的原文'
            return result
        state=self.turn(project,[f['id'] for f in files],invalid)
        self.assertEqual(state['status'],'failed',state)
        self.assert_unchanged(project,files)

    def test_database_failure_and_second_move_failure_are_atomic(self):
        project=self.create(); files=[self.upload(project),self.upload(project)]
        root=Path(self.temp.name)
        with database.connection(root) as db:
            db.execute("CREATE TRIGGER reject_completion BEFORE UPDATE ON tasks WHEN json_extract(NEW.payload,'$.status')='completed' BEGIN SELECT RAISE(ABORT,'test'); END")
        state=self.turn(project,[f['id'] for f in files],self.edit_all)
        self.assertEqual(state['status'],'failed',state); self.assert_unchanged(project,files)
        with database.connection(root) as db: db.execute('DROP TRIGGER reject_completion')
        replace=Path.replace; moved=0
        def fail_second(path,target):
            nonlocal moved
            if path.name.startswith('task-'):
                moved+=1
                if moved==2: raise OSError('Cannot publish second output')
            return replace(path,target)
        with patch.object(Path,'replace',fail_second):
            state=self.turn(project,[f['id'] for f in files],self.edit_all)
        self.assertEqual(state['status'],'failed',state); self.assert_unchanged(project,files)
        self.assertFalse(list(root.glob('publication-*')))

    def test_cancel_during_preparation_does_not_publish_any_file(self):
        project=self.create(); files=[self.upload(project),self.upload(project)]
        started,release=threading.Event(),threading.Event()
        original=documents.prepare_batch
        def slow(*args):
            prepared=original(*args); started.set(); release.wait(3); return prepared
        with patch('app.model.respond_project',side_effect=self.edit_all),patch('app.documents.prepare_batch',side_effect=slow):
            created=self.client.post('/api/tasks',json={'project_id':project,'file_ids':[f['id'] for f in files],'prompt':'修改全部'}).json()
            self.assertTrue(started.wait(2))
            self.client.post('/api/tasks/'+created['id']+'/cancel'); release.set()
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


class ProjectModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_uses_plain_response_and_actual_model(self):
        async def call(system,content,schema=None):
            if schema is model.ProjectRoute: return model.ProjectRoute(kind='chat')
            self.assertIsNone(schema)
            self.assertIn('configured-model',system)
            return '自由格式回答'
        with patch.dict('os.environ',{'DEEPSEEK_MODEL':'configured-model'}),patch('app.model.call',side_effect=call):
            result=await model.respond_project('你是什么模型',[])
        self.assertEqual(result.text,'自由格式回答')

    async def test_multi_file_answer_and_ambiguous_edit(self):
        inventories=[dict(id=i,name=i+'.docx',blocks=[dict(id=i+':p:0',text=i,kind='paragraph')],coverage=[]) for i in ('a','b')]
        async def answer(system,content,schema=None):
            if schema is model.ProjectRoute: return model.ProjectRoute(kind='answer',file_ids=['a','b'])
            self.assertIsNone(schema)
            self.assertEqual({b['file_id'] for b in content['documents']},{'a','b'})
            return '综合两份文件'
        with patch('app.model.call',side_effect=answer):
            self.assertEqual((await model.respond_project('比较',inventories)).text,'综合两份文件')
        with patch('app.model.call',return_value=model.ProjectRoute(kind='clarify',question='修改哪一份？')):
            result=await model.respond_project('改一下标题',inventories)
        self.assertEqual(result.kind,'clarify'); self.assertEqual(result.operations,[])

    async def test_edit_route_passes_only_named_files_to_workflow(self):
        inventories=[dict(id=i,name=i+'.docx',blocks=[dict(id=i+':p:0',text=i,kind='paragraph')],coverage=[]) for i in ('a','b')]
        with patch('app.model.call',return_value=model.ProjectRoute(kind='edit',file_ids=['a'])),patch('app.edit_workflow.run',return_value=model.Response(kind='clarify',text='具体问题')) as workflow:
            await model.respond_project('只修改a',inventories)
        self.assertEqual([d['id'] for d in workflow.call_args.args[1]],['a'])

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
