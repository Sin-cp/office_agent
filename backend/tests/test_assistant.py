"""Assistant acceptance: immutable originals, memory isolation and structural edits."""
import asyncio
import io
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from docx import Document

from app import agent, database, documents, model, operations
import loop_support
import test_files
from test_documents import example


class AssistantTests(unittest.TestCase):
    setUp = test_files.FilesTest.setUp
    tearDown = test_files.FilesTest.tearDown

    def project(self):
        response = self.client.post('/api/projects')
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def upload(self, project, name='文稿.docx', content=None):
        response = self.client.post(
            '/api/files',
            params={'project_id': project},
            files={'file': (name, example() if content is None else content)},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def wait(self, identity):
        for _ in range(300):
            state = self.client.get('/api/tasks/' + identity).json()
            if state['status'] not in ('queued', 'running', 'cancelling'):
                return state
            time.sleep(.01)
        self.fail('Timeout')

    def turn(self, project, file_ids, handler, prompt='修改文档'):
        with loop_support.patch_agent(handler):
            response = self.client.post(
                '/api/tasks',
                json={'project_id': project, 'file_ids': file_ids, 'prompt': prompt},
            )
            self.assertEqual(response.status_code, 202, response.text)
            return self.wait(response.json()['id'])

    def edit_handler(self, file_id, ops_fn, text='已修改'):
        async def handler(prompt, context):
            inventory = await context.load_inventory(file_id)
            ops = ops_fn(inventory['blocks'])
            item, changes = await asyncio.to_thread(
                documents.prepare_batch,
                context.root,
                file_id,
                inventory['revision'],
                ops,
            )
            loop_support.stage(context, item, changes, 'edit')
            return agent.AgentResult(text=text, kind='edit')

        return handler

    def output(self, state):
        self.assertEqual(state['status'], 'completed', state)
        data = self.client.get(f"/api/files/{state['result']['file']['id']}/download").content
        return Document(io.BytesIO(data))

    def test_answer_memory_isolation_and_project_deletion(self):
        first = self.project()
        second = self.project()
        a = self.upload(first)
        b = self.upload(second)
        first_turn = self.turn(
            first, [a['id']], loop_support.answer('讨论实施方案'), '请用正式语气总结'
        )
        self.assertEqual(first_turn['status'], 'completed', first_turn)
        self.assertNotIn('file', first_turn['result'])

        async def follow(prompt, context):
            self.assertTrue(
                any(
                    '讨论实施方案' in str(message.get('content'))
                    for message in model.conversation_context.get()['messages']
                )
            )
            return agent.AgentResult(text='需要补充哪一节？')

        second_turn = self.turn(first, [a['id']], follow)
        self.assertEqual(second_turn['result']['kind'], 'answer')
        self.assertIn('补充', second_turn['result']['text'])

        async def isolated(prompt, context):
            self.assertEqual(len(model.conversation_context.get()['messages']), 1)
            return agent.AgentResult(text='独立会话')

        self.assertEqual(self.turn(second, [b['id']], isolated)['status'], 'completed')
        self.assertEqual(self.client.get('/api/files').json()['total'], 2)
        root = Path(self.temp.name)
        database.initialize(root)
        messages = self.client.get(f'/api/projects/{first}/messages').json()['items']
        self.assertEqual(len(messages), 4)
        self.assertEqual(len({item['id'] for item in messages}), 4)
        self.assertEqual(self.client.delete('/api/projects/' + first).status_code, 200)
        with database.connection(root) as db:
            self.assertEqual(
                db.execute(
                    'SELECT count(*) FROM messages WHERE project_id=?', (first,)
                ).fetchone()[0],
                0,
            )
            self.assertIsNone(
                db.execute(
                    'SELECT * FROM conversations WHERE project_id=?', (first,)
                ).fetchone()
            )

    def test_structural_edit_keeps_original_and_stale_version(self):
        project = self.project()
        original = self.upload(project)

        def ops(blocks):
            return [
                operations.Operation(
                    op='insert_paragraph',
                    target=blocks[0]['id'],
                    expected_text=blocks[0]['text'],
                    text='新增标题',
                    style='heading',
                    level=2,
                ),
                operations.Operation(
                    op='replace',
                    target=blocks[1]['id'],
                    expected_text=blocks[1]['text'],
                    text='重复文字更新',
                ),
                operations.Operation(
                    op='delete', target=blocks[3]['id'], expected_text=blocks[3]['text']
                ),
            ]

        state = self.turn(project, [original['id']], self.edit_handler(original['id'], ops), '三处修改')
        doc = self.output(state)
        self.assertEqual(
            [p.text for p in doc.paragraphs],
            ['项目背景', '新增标题', '重复文字更新', '实施方案', '含域段落'],
        )
        self.assertEqual(doc.paragraphs[1].style.name, 'Heading 2')
        self.assertTrue(doc.paragraphs[2].runs[0].bold)
        newer = state['result']['file']
        self.assertEqual(
            self.client.post('/api/tasks', json={'file_id': original['id'], 'prompt': '修改'}).status_code,
            409,
        )
        response = self.client.post(
            f"/api/projects/{project}/restore",
            json={'file_id': original['id'], 'expected_current_file_id': newer['id']},
        )
        self.assertEqual(response.status_code, 410, response.text)
        database.initialize(Path(self.temp.name))
        current = self.client.get(f'/api/projects/{project}/files').json()['items'][0]['current_file']
        self.assertEqual(current['id'], newer['id'])
        self.assertEqual(self.client.get('/api/files').json()['total'], 2)

    def test_invalid_operations_never_publish(self):
        project = self.project()
        file = self.upload(project)
        for kind in ('foreign', 'noop', 'overlap'):

            def ops(blocks, kind=kind):
                op = operations.Operation(
                    op='replace',
                    target=blocks[0]['id'],
                    expected_text=blocks[0]['text'],
                    text=blocks[0]['text'] if kind == 'noop' else '新标题',
                )
                result = [op]
                if kind == 'foreign':
                    result.append(
                        operations.Operation(op='delete', target='other:p:0', expected_text='')
                    )
                if kind == 'overlap':
                    result.append(op)
                return result

            state = self.turn(project, [file['id']], self.edit_handler(file['id'], ops))
            self.assertEqual(state['status'], 'failed', (kind, state))
            self.assertNotIn('file', state['result'] or {})
            self.assertEqual(self.client.get('/api/files').json()['total'], 1)
        self.assertFalse(list((Path(self.temp.name) / 'uploads').glob('*.tmp')))

    def test_table_operations_preserve_other_parts(self):
        project = self.project()
        original = example()
        file = self.upload(project, content=original)

        def insert(blocks):
            return [
                operations.Operation(
                    op='insert_table',
                    target=blocks[0]['id'],
                    expected_text=blocks[0]['text'],
                    cells=[['甲', '乙'], ['丙', '丁']],
                )
            ]

        state = self.turn(project, [file['id']], self.edit_handler(file['id'], insert), '新增表格')
        self.output(state)
        file = state['result']['file']
        for op, index, cells in [
            ('insert_row', 1, [['新甲', '新乙']]),
            ('insert_column', 1, [['X'], ['Y'], ['Z']]),
            ('delete_row', 0, []),
            ('delete_column', 0, []),
        ]:

            def change(blocks, op=op, index=index, cells=cells):
                table = next(block for block in blocks if block['kind'] == 'table')
                return [
                    operations.Operation(
                        op=op,
                        target=table['id'],
                        expected_text=table['text'],
                        index=index,
                        cells=cells,
                    )
                ]

            state = self.turn(project, [file['id']], self.edit_handler(file['id'], change), '调整表格')
            doc = self.output(state)
            file = state['result']['file']

        self.assertEqual(
            [[cell.text for cell in row.cells] for row in doc.tables[0].rows],
            [['Y', '新乙'], ['Z', '丁']],
        )

        def cell(blocks):
            block = next(block for block in blocks if ':r:0:c:0:p:0' in block['id'])
            return [
                operations.Operation(
                    op='replace',
                    target=block['id'],
                    expected_text=block['text'],
                    text='已更新',
                )
            ]

        state = self.turn(project, [file['id']], self.edit_handler(file['id'], cell), '单元格修改')
        self.assertEqual(self.output(state).tables[0].cell(0, 0).text, '已更新')
        data = self.client.get(f"/api/files/{state['result']['file']['id']}/download").content
        with zipfile.ZipFile(io.BytesIO(original)) as old, zipfile.ZipFile(io.BytesIO(data)) as new:
            for name in old.namelist():
                if name != 'word/document.xml':
                    self.assertEqual(old.read(name), new.read(name), name)

    def test_complex_tables_readonly(self):
        doc = Document()
        doc.add_paragraph('开头')
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = '合并内容'
        table.cell(0, 0).merge(table.cell(0, 1))
        table.cell(1, 0).add_table(rows=1, cols=1).cell(0, 0).text = '嵌套内容'
        stream = io.BytesIO()
        doc.save(stream)
        project = self.project()
        file = self.upload(project, '复杂.docx', stream.getvalue())

        def remove(blocks):
            self.assertTrue(any('嵌套内容' in block['text'] for block in blocks))
            target = next(block for block in blocks if block['kind'] == 'table')
            self.assertFalse(target['editable'])
            return [
                operations.Operation(
                    op='delete', target=target['id'], expected_text=target['text']
                )
            ]

        state = self.turn(project, [file['id']], self.edit_handler(file['id'], remove), '删除表格')
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(self.client.get('/api/files').json()['total'], 1)


class LongDocumentTests(unittest.IsolatedAsyncioTestCase):
    async def test_answer_documents_covers_every_chunk(self):
        blocks = [
            dict(id=f'p:{i}', text='文字' * 3000, kind='paragraph', file_id='v1',
                 file_name='长文.docx', location=f'第{i + 1}段')
            for i in range(8)
        ]
        inventories = [
            dict(id='v1', name='长文.docx', revision='r1', blocks=blocks, coverage=[])
        ]
        seen = []

        async def fake(system, content, schema=None):
            if 'document' in content:
                seen.extend(block['id'] for block in content['document'])
                return '分块要点'
            self.assertIn('partials', content)
            return '完整总结'

        with patch('app.model.call', side_effect=fake):
            text = await model.answer_documents('总结全文', inventories)
        self.assertEqual(text, '完整总结')
        self.assertEqual(seen, [block['id'] for block in blocks])

    async def test_compaction_retains_recent_turns(self):
        context = dict(
            summary='',
            preferences=[],
            messages=[
                dict(seq=i, role='user', content={'text': '请求' * 1500}, file_id='v1')
                for i in range(12)
            ],
        )

        async def fake(system, content, schema):
            return model.Memory(summary='已讨论的要求', preferences=['正式语气'])

        with patch('app.model.call', side_effect=fake):
            result, memory = await model.compact_memory(context)
        self.assertEqual([message['seq'] for message in result['messages']], [6, 7, 8, 9, 10, 11])
        self.assertEqual(memory[1], 5)
        self.assertEqual(result['preferences'], ['正式语气'])


class OperationBoundaryTests(unittest.TestCase):
    def test_empty_document_and_repeated_insertions(self):
        doc = Document()
        blocks, _ = operations.inventory(doc, 'v1')
        self.assertEqual(blocks[0]['kind'], 'document_end')
        operations.apply(
            doc,
            'v1',
            [
                operations.Operation(
                    op='insert_paragraph', target=blocks[0]['id'], expected_text='', text=text
                )
                for text in ('第一段', '第二段')
            ],
        )
        self.assertEqual([p.text for p in doc.paragraphs], ['第一段', '第二段'])
        blocks, _ = operations.inventory(doc, 'v2')
        operations.apply(
            doc,
            'v2',
            [
                operations.Operation(
                    op='insert_paragraph',
                    target=blocks[0]['id'],
                    expected_text=blocks[0]['text'],
                    text=text,
                )
                for text in ('甲', '乙')
            ],
        )
        self.assertEqual([p.text for p in doc.paragraphs], ['第一段', '甲', '乙', '第二段'])

    def test_list_inherits_numbering_and_run_format(self):
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        doc = Document()
        paragraph = doc.add_paragraph('项目一', style='List Number')
        paragraph.runs[0].bold = True
        num = OxmlElement('w:numPr')
        nid = OxmlElement('w:numId')
        nid.set(qn('w:val'), '5')
        num.append(nid)
        paragraph._p.get_or_add_pPr().append(num)
        blocks, _ = operations.inventory(doc, 'v')
        operations.apply(
            doc,
            'v',
            [
                operations.Operation(
                    op='insert_paragraph',
                    target=blocks[0]['id'],
                    expected_text=blocks[0]['text'],
                    text='项目二',
                )
            ],
        )
        self.assertEqual(doc.paragraphs[1].style.name, 'List Number')
        self.assertTrue(doc.paragraphs[1].runs[0].bold)
        self.assertEqual(
            doc.paragraphs[1]._p.xpath('./w:pPr/w:numPr/w:numId')[0].get(qn('w:val')), '5'
        )

    def test_delete_table_and_preserve_section_boundary(self):
        from docx.oxml import OxmlElement

        doc = Document()
        paragraph = doc.add_paragraph('不能删除的分节段')
        paragraph._p.get_or_add_pPr().append(OxmlElement('w:sectPr'))
        doc.add_table(rows=1, cols=1).cell(0, 0).text = '表格'
        blocks, _ = operations.inventory(doc, 'v')
        self.assertFalse(blocks[0]['editable'])
        table = next(block for block in blocks if block['kind'] == 'table')
        operations.apply(
            doc,
            'v',
            [
                operations.Operation(
                    op='delete', target=table['id'], expected_text=table['text']
                )
            ],
        )
        self.assertEqual(len(doc.tables), 0)
        self.assertTrue(doc.paragraphs[0]._p.xpath('./w:pPr/w:sectPr'))


if __name__ == '__main__':
    unittest.main()
