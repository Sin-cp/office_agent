import asyncio
import io
from pathlib import Path
import unittest
from unittest.mock import patch, AsyncMock
from docx import Document
from app import model, database, creation
import test_project_chat

CONTENT = creation.NewDocument(name='新计划', title='计划书', sections=[
    creation.Section(kind='heading', text='第一天'), creation.Section(kind='paragraph', text='行程内容'),
    creation.Section(kind='bullet', text='注意保暖'), creation.Section(kind='number', text='提前准备'),
    creation.Section(kind='table', rows=[['时间','地点'],['上午','博物馆']])])


class CreationTests(unittest.TestCase):
    setUp = test_project_chat.ProjectChatTests.setUp
    tearDown = test_project_chat.ProjectChatTests.tearDown
    create = test_project_chat.ProjectChatTests.create
    upload = test_project_chat.ProjectChatTests.upload
    wait = test_project_chat.ProjectChatTests.wait
    turn = test_project_chat.ProjectChatTests.turn
    edit_all = test_project_chat.ProjectChatTests.edit_all

    async def generated(self, prompt, inventories, load_inventory=None):
        return model.Response(kind='create', text='文档已生成', document=CONTENT)

    def test_create_persists_independently_and_can_edit_delete(self):
        project = self.create()
        original = self.upload(project)
        for ids in ([], [original['id']]):
            result = self.turn(project, ids, self.generated, '新建文档')
            self.assertEqual(result['status'], 'completed', result)
        output = result['result']['file']
        self.assertIsNone(output['source_file_id'])
        sources = self.client.get(f'/api/projects/{project}/files').json()['items']
        self.assertEqual(len(sources), 3)
        self.assertEqual(sum(s['origin']=='generated' for s in sources), 2)
        downloaded = self.client.get(f"/api/files/{output['id']}/download")
        doc = Document(io.BytesIO(downloaded.content))
        self.assertEqual(doc.paragraphs[0].text, CONTENT.title)
        self.assertEqual(doc.tables[0].cell(1,1).text,'博物馆')
        self.assertEqual(self.client.get(f"/api/files/{output['id']}/content").status_code,200)
        edited = self.turn(project,[output['id']],self.edit_all,'修改标题')
        self.assertEqual(edited['status'],'completed',edited)
        version = edited['result']['file']
        database.initialize(Path(self.temp.name))
        self.assertEqual(self.client.get('/api/projects').json()['total'],1)
        sources = self.client.get(f'/api/projects/{project}/files').json()['items']
        generated = next(s for s in sources if s['id']==output['id'])
        self.assertEqual(generated['origin'],'generated')
        self.assertEqual(generated['current_file']['id'],version['id'])
        self.assertEqual(self.client.delete(f"/api/projects/{project}/files/{output['id']}").status_code,200)
        history = self.client.get(f'/api/projects/{project}/history').json()['items']
        self.assertFalse(next(t for t in history if t['id']==result['id'])['result']['file']['available'])
        self.assertEqual(self.client.get(f"/api/files/{version['id']}/download").status_code,200)
        self.assertEqual(self.client.get(f"/api/files/{original['id']}/download").status_code,200)

    def test_failed_validation_and_cancel_leave_no_files(self):
        project = self.create()
        with patch('app.storage.validate_docx', side_effect=ValueError('invalid')):
            state = self.turn(project, [], self.generated)
        self.assertEqual(state['status'],'failed')
        self.assertEqual(list((Path(self.temp.name)/'uploads').iterdir()),[])
        async def cancelled(*args):
            raise asyncio.CancelledError()
        state=self.turn(project,[],cancelled)
        self.assertEqual(state['status'],'cancelled')
        self.assertEqual(self.client.get(f'/api/projects/{project}/files').json()['items'],[])

    def test_cancel_after_render_before_publish_cleans_file(self):
        from app.tasks import Manager
        original = Manager.checkpoint
        async def checkpoint(manager, state, stage):
            if stage == 3:
                raise asyncio.CancelledError()
            await original(manager, state, stage)
        project = self.create()
        with patch.object(Manager, 'checkpoint', checkpoint):
            state = self.turn(project, [], self.generated)
        self.assertEqual(state['status'], 'cancelled')
        self.assertEqual(list((Path(self.temp.name)/'uploads').iterdir()), [])
        self.assertEqual(self.client.get(f'/api/projects/{project}/files').json()['items'], [])

    def test_publication_failure_cleans_generated_bytes(self):
        project = self.create()
        with patch('app.database.insert_record', side_effect=ValueError('failure')):
            state = self.turn(project, [], self.generated)
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(list((Path(self.temp.name)/'uploads').iterdir()), [])
        self.assertEqual(self.client.get(f'/api/projects/{project}/files').json()['items'], [])


class CreationRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_loads_only_explicit_references(self):
        inventory=[dict(id='a',name='附件.docx')]
        for ids in ([],['a']):
            loader=AsyncMock(return_value=dict(id='a',name='附件.docx',blocks=[{'text':'参考内容'}]))
            with patch('app.model.call',side_effect=[model.ProjectRoute(kind='create',file_ids=ids),CONTENT]),patch('app.model.enrich_web',return_value=None):
                result=await model.respond_project('新建文档',inventory,loader)
            self.assertEqual(result.kind,'create')
            self.assertEqual(loader.await_count,len(ids))

    async def test_no_attachment_creation_and_clarification(self):
        with patch('app.model.call',side_effect=[model.ProjectRoute(kind='create'),CONTENT]),patch('app.model.enrich_web',return_value=None):
            self.assertEqual((await model.respond_project('整理刚才内容成Word',[])).kind,'create')
        with patch('app.model.call',return_value=model.ProjectRoute(kind='clarify',question='需要哪一年？')):
            self.assertEqual((await model.respond_project('元旦攻略',[])).kind,'clarify')

    def test_invalid_structure_rejected(self):
        with self.assertRaises(ValueError):
            creation.Section(kind='table',rows=[['a'],['b','c']])
        with self.assertRaises(ValueError):
            model.Response(kind='create',text='缺少内容')
