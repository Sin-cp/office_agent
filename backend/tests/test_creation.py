import asyncio
import io
from pathlib import Path
import unittest
from unittest.mock import patch, AsyncMock
from docx import Document
from app import model, database, creation
import loop_support
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

    def test_create_persists_independently_and_can_edit_delete(self):
        project = self.create()
        original = self.upload(project)
        for ids in ([], [original['id']]):
            result = self.turn(project, ids, loop_support.create(CONTENT, '文档已生成'), '新建文档')
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
        edited = self.turn(project,[output['id']],loop_support.edit([output['id']]),'修改标题')
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
            state = self.turn(project, [], loop_support.create(CONTENT, '文档已生成'))
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
            state = self.turn(project, [], loop_support.create(CONTENT, '文档已生成'))
        self.assertEqual(state['status'], 'cancelled')
        self.assertEqual(list((Path(self.temp.name)/'uploads').iterdir()), [])
        self.assertEqual(self.client.get(f'/api/projects/{project}/files').json()['items'], [])

    def test_invalid_structure_rejected(self):
        with self.assertRaises(ValueError):
            creation.Section(kind='table',rows=[['a'],['b','c']])
        with self.assertRaises(ValueError):
            model.Response(kind='create',text='缺少内容')

    def test_publication_failure_cleans_generated_bytes(self):
        project = self.create()
        with patch('app.database.insert_record', side_effect=ValueError('failure')):
            state = self.turn(project, [], loop_support.create(CONTENT, '文档已生成'))
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(list((Path(self.temp.name)/'uploads').iterdir()), [])
        self.assertEqual(self.client.get(f'/api/projects/{project}/files').json()['items'], [])


class ImageSectionTests(unittest.TestCase):
    """P4: an image block renders a real picture, never model-supplied bytes."""

    def setUp(self):
        import base64
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "uploads").mkdir()
        self.png = self.root / "uploads" / "pic.png"
        self.png.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
            )
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_image_section_renders_a_picture_and_caption(self):
        content = creation.NewDocument(
            name="图文",
            title="图文",
            sections=[
                creation.Section(kind="paragraph", text="前文"),
                creation.Section(kind="image", asset_id="img1", width_cm=8, caption="图一"),
            ],
        )
        temporary, destination, record = creation.prepare(
            self.root, "p1", content, {"img1": self.png}
        )
        try:
            document = Document(str(temporary))
            self.assertEqual(len(document.inline_shapes), 1)
            self.assertIn("图一", [p.text for p in document.paragraphs])
            self.assertEqual(record["project_id"], "p1")
        finally:
            temporary.unlink(missing_ok=True)

    def test_missing_asset_fails_before_writing(self):
        content = creation.NewDocument(
            name="图文",
            title="图文",
            sections=[creation.Section(kind="image", asset_id="ghost")],
        )
        with self.assertRaises(ValueError):
            creation.prepare(self.root, "p1", content, {})

    def test_image_block_rejects_text(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            creation.Section(kind="image", asset_id="img1", text="不能有文字")
