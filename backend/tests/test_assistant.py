"""Assistant acceptance tests: immutable files, memory isolation, structural edits."""
import io
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from docx import Document
from app import database, model, operations
import test_files
from test_documents import example


class AssistantTests(unittest.TestCase):
    setUp = test_files.FilesTest.setUp
    tearDown = test_files.FilesTest.tearDown
    upload = test_files.FilesTest.upload

    def turn(self, file, reply, prompt='修改文档'):
        project = self.client.get(f"/api/files/{file['id']}/history").json()['history_id']
        with patch('app.model.suggest', side_effect=reply):
            response = self.client.post('/api/tasks', json={'project_id':project,'current_file_id':file['id'],'prompt':prompt})
            self.assertEqual(response.status_code,202,response.text)
            for _ in range(200):
                state = self.client.get('/api/tasks/'+response.json()['id']).json()
                if state['status'] not in ('queued','running','cancelling'):
                    return state
                time.sleep(.01)
        self.fail('Timeout')

    def output(self, state):
        self.assertEqual(state['status'],'completed',state)
        data = self.client.get(f"/api/files/{state['result']['file']['id']}/download").content
        return Document(io.BytesIO(data))

    def test_answer_clarify_memory_isolation_migration_deletion(self):
        a,b = self.upload(example()).json(),self.upload(example()).json()
        async def answer(prompt,blocks):
            self.assertTrue(any(x['kind']=='table' and '保留表格' in x['text'] for x in blocks))
            return model.Response(kind='answer',text='讨论实施方案',citations=[model.Citation(block_id=blocks[0]['id'],quote='项目背景')])
        first = self.turn(a,answer,'请用正式语气总结')
        self.assertEqual(first['status'],'completed',first)
        self.assertNotIn('file',first['result'])
        self.assertEqual(first['result']['citations'][0]['file_id'],a['id'])
        async def follow(prompt,blocks):
            self.assertTrue(any('正式语气' in m['content'].get('text','') for m in model.conversation_context.get()['messages']))
            return model.Response(kind='clarify',text='需要补充哪一节？')
        self.assertEqual(self.turn(a,follow)['result']['kind'],'clarify')
        async def isolated(prompt,blocks):
            self.assertEqual(len(model.conversation_context.get()['messages']),1)
            return model.Response(kind='answer',text='独立会话')
        self.assertEqual(self.turn(b,isolated)['status'],'completed')
        self.assertEqual(self.client.get('/api/files').json()['total'],2)
        root=Path(self.temp.name)
        database.initialize(root);database.initialize(root)
        messages=self.client.get(f"/api/projects/{a['id']}/messages").json()['items']
        self.assertEqual(len(messages),4)
        self.assertEqual(len({m['id'] for m in messages}),4)
        self.client.delete('/api/projects/'+a['id'])
        with database.connection(root) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages WHERE project_id=?',(a['id'],)).fetchone()[0],0)
            self.assertIsNone(db.execute('SELECT * FROM conversations WHERE project_id=?',(a['id'],)).fetchone())

    def test_batch_restore_and_stale_version(self):
        file=self.upload(example()).json()
        async def edit(prompt,blocks):
            return model.Response(kind='edit',text='三处修改',operations=[
                operations.Operation(op='insert_paragraph',target=blocks[0]['id'],expected_text=blocks[0]['text'],text='新增标题',style='heading',level=2),
                operations.Operation(op='replace',target=blocks[1]['id'],expected_text=blocks[1]['text'],text='重复文字更新'),
                operations.Operation(op='delete',target=blocks[3]['id'],expected_text=blocks[3]['text'])])
        state=self.turn(file,edit);doc=self.output(state)
        self.assertEqual([p.text for p in doc.paragraphs],['项目背景','新增标题','重复文字更新','实施方案','含域段落'])
        self.assertEqual(doc.paragraphs[1].style.name,'Heading 2')
        self.assertTrue(doc.paragraphs[2].runs[0].bold)
        newer=state['result']['file']
        self.assertEqual(self.client.post('/api/tasks',json={'file_id':file['id'],'prompt':'修改'}).status_code,409)
        response=self.client.post(f"/api/projects/{file['id']}/restore",json={'file_id':file['id'],'expected_current_file_id':newer['id']})
        self.assertEqual(response.status_code,410,response.text)
        database.initialize(Path(self.temp.name))
        self.assertEqual(self.client.get(f"/api/files/{newer['id']}/history").json()['current_file']['id'],newer['id'])
        self.assertEqual(self.client.get('/api/files').json()['total'],2)

    def test_invalid_citation_atomic_batch_noop_overlap(self):
        file=self.upload(example()).json()
        async def citation(prompt,blocks):
            return model.Response(kind='answer',text='回答',citations=[model.Citation(block_id=blocks[0]['id'],quote='编造原文')])
        self.assertEqual(self.turn(file,citation)['status'],'failed')
        for kind in ('foreign','noop','overlap'):
            async def edit(prompt,blocks):
                op=operations.Operation(op='replace',target=blocks[0]['id'],expected_text=blocks[0]['text'],text=blocks[0]['text'] if kind=='noop' else '新标题')
                ops=[op]
                if kind=='foreign':ops.append(operations.Operation(op='delete',target='other:p:0',expected_text=''))
                if kind=='overlap':ops.append(op)
                return model.Response(kind='edit',text='修改',operations=ops)
            self.assertEqual(self.turn(file,edit)['status'],'failed',kind)
        self.assertEqual(self.client.get('/api/files').json()['total'],1)
        self.assertFalse(list((Path(self.temp.name)/'uploads').glob('*.tmp')))

    def test_table_operations_preserve_other_parts(self):
        original=example();file=self.upload(original).json()
        async def insert(prompt,blocks):
            return model.Response(kind='edit',text='新增表格',operations=[operations.Operation(op='insert_table',target=blocks[0]['id'],expected_text=blocks[0]['text'],cells=[['甲','乙'],['丙','丁']])])
        state=self.turn(file,insert);self.output(state);file=state['result']['file']
        for op,index,cells in [('insert_row',1,[['新甲','新乙']]),('insert_column',1,[['X'],['Y'],['Z']]),('delete_row',0,[]),('delete_column',0,[])]:
            async def edit(prompt,blocks):
                t=next(b for b in blocks if b['kind']=='table')
                return model.Response(kind='edit',text='调整表格',operations=[operations.Operation(op=op,target=t['id'],expected_text=t['text'],index=index,cells=cells)])
            state=self.turn(file,edit);doc=self.output(state);file=state['result']['file']
        self.assertEqual([[c.text for c in r.cells] for r in doc.tables[0].rows],[['Y','新乙'],['Z','丁']])
        async def cell(prompt,blocks):
            b=next(b for b in blocks if ':r:0:c:0:p:0' in b['id'])
            return model.Response(kind='edit',text='单元格修改',operations=[operations.Operation(op='replace',target=b['id'],expected_text=b['text'],text='已更新')])
        state=self.turn(file,cell)
        self.assertEqual(self.output(state).tables[0].cell(0,0).text,'已更新')
        data=self.client.get(f"/api/files/{state['result']['file']['id']}/download").content
        with zipfile.ZipFile(io.BytesIO(original)) as old,zipfile.ZipFile(io.BytesIO(data)) as new:
            for name in old.namelist():
                if name!='word/document.xml':self.assertEqual(old.read(name),new.read(name),name)

    def test_complex_tables_readonly(self):
        doc=Document();doc.add_paragraph('开头');table=doc.add_table(rows=2,cols=2)
        table.cell(0,0).text='合并内容';table.cell(0,0).merge(table.cell(0,1))
        table.cell(1,0).add_table(rows=1,cols=1).cell(0,0).text='嵌套内容'
        stream=io.BytesIO();doc.save(stream);file=self.upload(stream.getvalue()).json()
        async def edit(prompt,blocks):
            self.assertTrue(any('嵌套内容' in b['text'] for b in blocks))
            b=next(b for b in blocks if b['kind']=='table');self.assertFalse(b['editable'])
            return model.Response(kind='edit',text='删除',operations=[operations.Operation(op='delete',target=b['id'],expected_text=b['text'])])
        self.assertEqual(self.turn(file,edit)['status'],'failed')
        self.assertEqual(self.client.get('/api/files').json()['total'],1)


class ModelWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_summary_covers_every_chunk(self):
        blocks=[dict(id=f'p:{i}',text='文字'*3000,kind='paragraph') for i in range(8)];seen=[]
        async def fake(system,content,schema):
            if schema is model.Route:return model.Route(kind='answer',scope='all',instruction='总结全文')
            if 'document' in content:
                seen.extend(b['id'] for b in content['document'])
                return model.Response(kind='answer',text='分块要点')
            self.assertIn('partials',content)
            return model.Response(kind='answer',text='完整总结')
        with patch('app.model.call',side_effect=fake):result=await model.suggest('总结全文',blocks)
        self.assertEqual(result.kind,'answer');self.assertEqual(seen,[b['id'] for b in blocks])

    async def test_compaction_retains_recent_turns(self):
        context=dict(summary='',preferences=[],messages=[dict(seq=i,role='user',content={'text':'请求'*1500},file_id='v1') for i in range(12)])
        async def fake(system,content,schema):return model.Memory(summary='已讨论的要求',preferences=['正式语气'])
        with patch('app.model.call',side_effect=fake):result,memory=await model.compact_memory(context)
        self.assertEqual([m['seq'] for m in result['messages']],[8,9,10,11]);self.assertEqual(memory[1],7)
        self.assertEqual(result['preferences'],['正式语气'])

class OperationBoundaryTests(unittest.TestCase):
    def test_empty_document_and_repeated_insertions(self):
        doc=Document();blocks,_=operations.inventory(doc,'v1')
        self.assertEqual(blocks[0]['kind'],'document_end')
        operations.apply(doc,'v1',[operations.Operation(op='insert_paragraph',target=blocks[0]['id'],expected_text='',text=t) for t in ('第一段','第二段')])
        self.assertEqual([p.text for p in doc.paragraphs],['第一段','第二段'])
        blocks,_=operations.inventory(doc,'v2')
        operations.apply(doc,'v2',[operations.Operation(op='insert_paragraph',target=blocks[0]['id'],expected_text=blocks[0]['text'],text=t) for t in ('甲','乙')])
        self.assertEqual([p.text for p in doc.paragraphs],['第一段','甲','乙','第二段'])

    def test_list_inherits_numbering_and_run_format(self):
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        doc=Document();p=doc.add_paragraph('项目一',style='List Number');p.runs[0].bold=True
        num=OxmlElement('w:numPr');nid=OxmlElement('w:numId');nid.set(qn('w:val'),'5');num.append(nid);p._p.get_or_add_pPr().append(num)
        blocks,_=operations.inventory(doc,'v')
        operations.apply(doc,'v',[operations.Operation(op='insert_paragraph',target=blocks[0]['id'],expected_text=blocks[0]['text'],text='项目二')])
        self.assertEqual(doc.paragraphs[1].style.name,'List Number')
        self.assertTrue(doc.paragraphs[1].runs[0].bold)
        self.assertEqual(doc.paragraphs[1]._p.xpath('./w:pPr/w:numPr/w:numId')[0].get(qn('w:val')),'5')

    def test_delete_table_and_preserve_section_boundary(self):
        from docx.oxml import OxmlElement
        doc=Document();p=doc.add_paragraph('不能删除的分节段');p._p.get_or_add_pPr().append(OxmlElement('w:sectPr'))
        doc.add_table(rows=1,cols=1).cell(0,0).text='表格'
        blocks,_=operations.inventory(doc,'v')
        self.assertFalse(blocks[0]['editable'])
        t=next(b for b in blocks if b['kind']=='table')
        operations.apply(doc,'v',[operations.Operation(op='delete',target=t['id'],expected_text=t['text'])])
        self.assertEqual(len(doc.tables),0)
        self.assertTrue(doc.paragraphs[0]._p.xpath('./w:pPr/w:sectPr'))

class BatchTransactionTests(AssistantTests):
    # Inherit the common setup/helpers, but only collect these additional cases.
    test_answer_clarify_memory_isolation_migration_deletion = None
    test_batch_restore_and_stale_version = None
    test_invalid_citation_atomic_batch_noop_overlap = None
    test_table_operations_preserve_other_parts = None
    test_complex_tables_readonly = None

    def test_batch_publication_failure_does_not_advance_pointer(self):
        file=self.upload(example()).json()
        with database.connection(Path(self.temp.name)) as db:
            db.execute("CREATE TRIGGER reject_completion BEFORE UPDATE ON tasks WHEN json_extract(NEW.payload, '$.status') = 'completed' BEGIN SELECT RAISE(ABORT, 'test'); END")
        async def edit(prompt,blocks):
            b=blocks[0]
            return model.Response(kind='edit',text='修改',operations=[operations.Operation(op='replace',target=b['id'],expected_text=b['text'],text='新标题')])
        with self.assertLogs('app.documents',level='ERROR'):
            state=self.turn(file,edit)
        self.assertEqual(state['status'],'failed')
        versions=self.client.get(f"/api/projects/{file['id']}/versions").json()
        self.assertEqual(len(versions['items']),1);self.assertEqual(versions['current_file_id'],file['id'])
        messages=self.client.get(f"/api/projects/{file['id']}/messages").json()['items']
        self.assertEqual(messages[-1]['content']['status'],'failed')
        self.assertEqual(len(list((Path(self.temp.name)/'uploads').iterdir())),1)

    def test_cancel_batch_before_publication(self):
        import threading
        from app import documents
        started,release=threading.Event(),threading.Event()
        original=documents.prepare_batch
        def slow(*args):
            result=original(*args);started.set();release.wait(3);return result
        async def edit(prompt,blocks):
            b=blocks[0]
            return model.Response(kind='edit',text='修改',operations=[operations.Operation(op='replace',target=b['id'],expected_text=b['text'],text='新标题')])
        file=self.upload(example()).json()
        with patch('app.model.suggest',side_effect=edit),patch('app.documents.prepare_batch',side_effect=slow):
            created=self.client.post('/api/tasks',json={'file_id':file['id'],'prompt':'修改'}).json()
            self.assertTrue(started.wait(2))
            self.client.post(f"/api/tasks/{created['id']}/cancel");release.set()
            for _ in range(200):
                state=self.client.get('/api/tasks/'+created['id']).json()
                if state['status']=='cancelled':break
                time.sleep(.01)
            self.assertEqual(state['status'],'cancelled')
        self.assertEqual(self.client.get('/api/files').json()['total'],1)
        self.assertFalse(list((Path(self.temp.name)/'uploads').glob('*.tmp')))

class RoutingBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_read_selects_only_identified_chunks(self):
        blocks=[dict(id=f'p:{i}',text='文字'*3000,kind='paragraph') for i in range(8)]
        groups=model.chunks(blocks);seen=[]
        async def fake(system,content,schema):
            if schema is model.Route:
                return model.Route(kind='answer',scope='local',instruction='解释指定章节',chunk_indices=[2])
            if 'document' in content:
                seen.extend(b['id'] for b in content['document'])
                return model.Response(kind='answer',text='该章节的说明')
            self.assertIn('仅检索',content['coverage'])
            return model.Response(kind='answer',text='局部回答')
        with patch('app.model.call',side_effect=fake):await model.suggest('解释指定章节',blocks)
        self.assertEqual(seen,[b['id'] for b in groups[1]])

    async def test_long_edit_incomplete_review_returns_clarification(self):
        blocks=[dict(id=f'p:{i}',text='文字'*3000,kind='paragraph') for i in range(8)]
        async def fake(system,content,schema):
            if schema is model.Route:return model.Route(kind='edit',scope='all',instruction='修改全部')
            if schema is model.Review:return model.Review(complete=False,text='跨章节目标存在歧义，请明确范围')
            b=content['document'][0]
            return model.Response(kind='edit',text='分块修改',operations=[operations.Operation(op='replace',target=b['id'],expected_text=b['text'],text='新的文字')])
        with patch('app.model.call',side_effect=fake):result=await model.suggest('修改全部',blocks)
        self.assertEqual(result.kind,'clarify');self.assertEqual(result.operations,[])

    def test_paragraph_after_table(self):
        doc=Document();doc.add_table(rows=1,cols=1).cell(0,0).text='表格'
        blocks,_=operations.inventory(doc,'v');b=blocks[0]
        operations.apply(doc,'v',[operations.Operation(op='insert_paragraph',target=b['id'],expected_text=b['text'],text='表格后总结')])
        self.assertEqual(doc.paragraphs[0].text,'表格后总结')
        from docx.oxml.ns import qn
        self.assertEqual([node.tag for node in doc.element.body][:2],[qn('w:tbl'),qn('w:p')])
