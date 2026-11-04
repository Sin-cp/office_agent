import asyncio
import io
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from docx import Document

from app import model, operations, documents, edit_workflow as flow
import test_project_chat


class WorkflowTests(unittest.TestCase):
    setUp = test_project_chat.ProjectChatTests.setUp
    tearDown = test_project_chat.ProjectChatTests.tearDown
    create = test_project_chat.ProjectChatTests.create
    wait = test_project_chat.ProjectChatTests.wait

    def upload(self, project, name='攻略.docx'):
        doc=Document()
        doc.add_heading('西安三日游',0)
        doc.add_paragraph('第一天乘地铁到钟楼，停车后步行到回民街。')
        doc.add_paragraph('第二天乘公交到兵马俑。')
        table=doc.add_table(rows=1,cols=2)
        table.cell(0,0).text='第三天交通';table.cell(0,1).text='优先地铁'
        data=io.BytesIO();doc.save(data)
        response=self.client.post('/api/files',params={'project_id':project},files={'file':(name,data.getvalue())})
        self.assertEqual(response.status_code,201)
        return response.json(), data.getvalue()

    def start(self, project, files):
        response=self.client.post('/api/tasks',json={'project_id':project,'file_ids':[f['id'] for f in files],
            'prompt':'把整份攻略改为自驾，补充停车建议，保留停车后步行。','web_enabled':False})
        self.assertEqual(response.status_code,202,response.text)
        return response.json()['id']

    def fake(self, ids, mode='repair'):
        self.drafts=0;self.audits=0
        async def call(system,content,schema=None):
            metrics=model.call_metrics.get()
            if metrics is not None:
                self.assertLess(len(metrics),20)
                metrics.append({'status':'completed'})
            if schema is model.ProjectRoute:
                return model.ProjectRoute(kind='edit',file_ids=ids)
            if schema is flow.Plan:
                self.assertIn('不能作为失败原因',system)
                return flow.Plan(requirements=[flow.Requirement(id='drive',text='自驾与停车',evidence='改为自驾',groups=[d['group'] for d in content['directory']])],
                                 question='请问跨城自驾从哪座城市出发？' if mode=='question' else '')
            if schema is flow.Draft:
                self.drafts+=1
                ops=[]
                for block in content['original']:
                    if block['kind']=='table': continue
                    text=block['text']
                    new=text.replace('乘地铁','驾车').replace('乘公交','驾车').replace('优先地铁','自驾，停车位置以现场公告为准')
                    if new!=text:
                        ops.append(operations.Operation(op='replace',target=block['id'],expected_text=text,text=new))
                if mode in ('repair','exhaust') and (self.drafts==1 or mode=='exhaust'):
                    return flow.Draft(operations=ops[:1])
                if mode=='conflict' and self.drafts==1:
                    return flow.Draft(operations=[ops[0],ops[0]])
                return flow.Draft(operations=ops)
            if schema is flow.Audit:
                self.audits+=1
                self.assertIn('after',content)
                after='\n'.join(b['text'] for b in content['after'])
                self.assertIn('停车后步行',after)
                if mode=='cancel':
                    self.started.set(); await asyncio.Event().wait()
                if '乘公交' in after or '优先地铁' in after:
                    return flow.Audit(status='repair',issues=[flow.Issue(requirement_id='drive',groups=[0],detail='第二天及交通表仍未改为自驾')])
                return flow.Audit(status='pass',summary='已调整三日自驾安排并补充停车说明。')
            raise AssertionError(f'Unexpected schema {schema}')
        return call

    def test_repairs_actual_document_and_keeps_original(self):
        project=self.create();file,original=self.upload(project)
        with patch('app.model.call',side_effect=self.fake([file['id']])):
            state=self.wait(self.start(project,[file]))
        self.assertEqual(state['status'],'completed',state)
        self.assertEqual(state['repair_round'],1)
        self.assertIsNone(state['activity'])
        self.assertEqual(self.drafts,2);self.assertEqual(self.audits,2)
        self.assertEqual(self.client.get(f"/api/files/{file['id']}/download").content,original)
        result=state['result']['file']
        content=self.client.get(f"/api/files/{result['id']}/content").json()
        self.assertIn('驾车',str(content));self.assertNotIn('乘公交',str(content))
        self.assertEqual(len(list((Path(self.temp.name)/'uploads').iterdir())),2)
        history=self.client.get(f'/api/projects/{project}/history').json()['items']
        self.assertEqual(history[-1]['repair_round'],1)

    def test_two_failed_repairs_publish_nothing(self):
        project=self.create();file,original=self.upload(project)
        with patch('app.model.call',side_effect=self.fake([file['id']], 'exhaust')):
            state=self.wait(self.start(project,[file]))
        self.assertEqual(state['status'],'failed',state)
        self.assertEqual(state['repair_round'],2)
        self.assertIn('自动补改两轮',state['error'])
        self.assertNotIn('分块',state['error'])
        self.assertEqual(self.drafts,3)
        self.assertEqual(len(list((Path(self.temp.name)/'uploads').iterdir())),1)
        self.assertEqual(self.client.get(f"/api/files/{file['id']}/download").content,original)

    def test_operation_conflict_is_repaired_not_asked_of_user(self):
        project=self.create();file,_=self.upload(project)
        with patch('app.model.call',side_effect=self.fake([file['id']], 'conflict')):
            state=self.wait(self.start(project,[file]))
        self.assertEqual(state['status'],'completed',state)
        self.assertEqual(state['repair_round'],1)
        self.assertEqual(self.drafts,2)

    def test_genuine_missing_information_asks_without_edits(self):
        project=self.create();file,_=self.upload(project)
        with patch('app.model.call',side_effect=self.fake([file['id']], 'question')):
            state=self.wait(self.start(project,[file]))
        self.assertEqual(state['result']['kind'],'clarify')
        self.assertEqual(self.drafts,0)
        self.assertEqual(len(list((Path(self.temp.name)/'uploads').iterdir())),1)

    def test_cancel_during_actual_review_cleans_temporary_files(self):
        project=self.create();file,_=self.upload(project);self.started=threading.Event()
        with patch('app.model.call',side_effect=self.fake([file['id']], 'cancel')):
            identity=self.start(project,[file]);self.assertTrue(self.started.wait(3))
            state=self.client.get('/api/tasks/'+identity).json()
            self.assertEqual(state['activity'],'正在检查修改完整性')
            self.client.post('/api/tasks/'+identity+'/cancel')
            state=self.wait(identity)
        self.assertEqual(state['status'],'cancelled',state)
        self.assertEqual(len(list((Path(self.temp.name)/'uploads').iterdir())),1)
        self.assertIsNone(state['activity'])

    def test_multi_file_outputs_are_published_together(self):
        project=self.create();a,_=self.upload(project);b,_=self.upload(project,'另一个.docx')
        with patch('app.model.call',side_effect=self.fake([a['id'],b['id']], 'pass')):
            state=self.wait(self.start(project,[a,b]))
        self.assertEqual(state['status'],'completed',state)
        self.assertEqual(len(state['result']['outputs']),2)


    def test_only_affected_group_is_regenerated(self):
        project=self.create();a,_=self.upload(project);b,_=self.upload(project,'另一个.docx')
        def groups(blocks):
            return [[x for x in blocks if x['file_id']==f] for f in dict.fromkeys(x['file_id'] for x in blocks)]
        with patch('app.model.call',side_effect=self.fake([a['id'],b['id']])),patch('app.model.chunks',side_effect=groups):
            state=self.wait(self.start(project,[a,b]))
        self.assertEqual(state['status'],'completed',state)
        self.assertEqual(self.drafts,3)  # Two initial groups, only the first is repaired.
        self.assertEqual(state['repair_round'],1)
        self.assertEqual(len(state['result']['outputs']),2)

    def test_foreign_targets_never_modify_another_file(self):
        project=self.create();a,_=self.upload(project);b,original=self.upload(project,'未选中.docx')
        delegate=self.fake([a['id']], 'pass')
        async def call(system,content,schema=None):
            if schema is flow.Draft:
                return flow.Draft(operations=[operations.Operation(op='replace',target=b['id']+':p:0',expected_text='西安三日游',text='错误修改')])
            return await delegate(system,content,schema)
        with patch('app.model.call',side_effect=call):
            state=self.wait(self.start(project,[a]))
        self.assertEqual(state['status'],'failed',state)
        self.assertEqual(state['repair_round'],2)
        self.assertEqual(self.client.get(f"/api/files/{b['id']}/download").content,original)
        self.assertEqual(len(list((Path(self.temp.name)/'uploads').iterdir())),2)

    def test_local_request_can_leave_other_sections_unchanged(self):
        project=self.create();file,_=self.upload(project)
        delegate=self.fake([file['id']], 'pass')
        async def call(system,content,schema=None):
            if schema is flow.Draft:
                block=content['original'][0]
                return flow.Draft(operations=[operations.Operation(op='replace',target=block['id'],expected_text=block['text'],text='西安自驾攻略')])
            if schema is flow.Audit:
                self.assertTrue(any('乘公交' in b['text'] for b in content['after']))
                return flow.Audit(status='pass',summary='已修改标题。')
            return await delegate(system,content,schema)
        with patch('app.model.call',side_effect=call):
            response=self.client.post('/api/tasks',json={'project_id':project,'file_ids':[file['id']], 'prompt':'仅将标题改为自驾，其他内容不变','web_enabled':False})
            state=self.wait(response.json()['id'])
        self.assertEqual(state['status'],'completed',state)
        self.assertEqual(state['repair_round'],0)

    def test_budget_failure_is_not_a_clarification(self):
        project=self.create();file,_=self.upload(project)
        delegate=self.fake([file['id']], 'pass')
        async def call(system,content,schema=None):
            result=await delegate(system,content,schema)
            if schema is flow.Plan:
                model.call_metrics.get().extend([{}]*16)
            return result
        with patch('app.model.call',side_effect=call):
            state=self.wait(self.start(project,[file]))
        self.assertEqual(state['status'],'failed')
        self.assertIn('预算不足',state['error'])
        self.assertEqual(self.drafts,0)
        self.assertEqual(len(list((Path(self.temp.name)/'uploads').iterdir())),1)

    def test_auditor_cannot_add_an_unrequested_requirement(self):
        project=self.create();file,_=self.upload(project)
        delegate=self.fake([file['id']], 'pass')
        async def call(system,content,schema=None):
            if schema is flow.Audit:
                return flow.Audit(status='repair',issues=[flow.Issue(requirement_id='precise_mileage',groups=[0],detail='缺少精确里程')])
            return await delegate(system,content,schema)
        with patch('app.model.call',side_effect=call):
            state=self.wait(self.start(project,[file]))
        self.assertEqual(state['status'],'failed')
        self.assertNotIn('精确里程',state['error'])
        self.assertEqual(self.drafts,1)
        self.assertEqual(len(list((Path(self.temp.name)/'uploads').iterdir())),1)

    def test_legacy_file_task_uses_the_same_repair_flow(self):
        project=self.create();file,_=self.upload(project)
        with patch('app.model.call',side_effect=self.fake([file['id']])):
            response=self.client.post('/api/tasks',json={'file_id':file['id'],'prompt':'改为自驾并补充停车建议','web_enabled':False})
            self.assertEqual(response.status_code,202,response.text)
            state=self.wait(response.json()['id'])
        self.assertEqual(state['status'],'completed',state)
        self.assertEqual(state['repair_round'],1)
        self.assertIsNone(state['activity'])

    def test_large_actual_result_is_reviewed_in_sections_then_together(self):
        project=self.create()
        doc=Document();doc.add_paragraph('乘地铁到钟楼，停车后步行。')
        for i in range(6): doc.add_paragraph(str(i)+'背景资料。'*1000)
        data=io.BytesIO();doc.save(data)
        file=self.client.post('/api/files',params={'project_id':project},files={'file':('长文.docx',data.getvalue())}).json()
        delegate=self.fake([file['id']], 'pass')
        observations=[]
        async def call(system,content,schema=None):
            if schema is flow.Observation:
                observations.append(content['actual'])
                return flow.Observation(findings='本部分内容已核对，相关交通改为驾车，保留停车后步行。')
            if schema is flow.Audit:
                self.assertGreater(len(content['actual_observations']),1)
                self.assertNotIn('after',content)
                return flow.Audit(status='pass',summary='已完成修改。')
            return await delegate(system,content,schema)
        with patch('app.model.call',side_effect=call):
            state=self.wait(self.start(project,[file]))
        self.assertEqual(state['status'],'completed',state)
        self.assertGreater(len(observations),1)
        self.assertTrue(any('驾车' in b['text'] for g in observations for b in g))

    def test_budget_reserves_review(self):
        token=model.call_metrics.set([{}]*18)
        try:
            with self.assertRaisesRegex(model.ModelError,'预算不足'):
                flow.budget(3)
            flow.budget(2)
        finally: model.call_metrics.reset(token)
