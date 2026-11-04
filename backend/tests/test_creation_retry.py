import asyncio
from pathlib import Path
import unittest
from unittest.mock import patch
from app import model, creation
import test_project_chat
import test_creation
import test_model_requests


class RetryTests(unittest.TestCase):
    setUp = test_project_chat.ProjectChatTests.setUp
    tearDown = test_project_chat.ProjectChatTests.tearDown
    wait = test_project_chat.ProjectChatTests.wait

    def run_turn(self, replies):
        with patch('app.model.call',side_effect=replies) as call:
            response=self.client.post('/api/tasks',json={'prompt':'生成招聘报告文档','web_enabled':False})
            state=self.wait(response.json()['id'])
            return state,call

    def test_invalid_structure_is_repaired_and_saved_once(self):
        error=model.StructuredOutputError([{'path':'sections.1.rows','code':'list_type'}], 'PRIVATE BAD OUTPUT')
        state,call=self.run_turn([model.ProjectRoute(kind='create'),error,test_creation.CONTENT])
        self.assertEqual(state['status'],'completed',state)
        self.assertEqual(state['creation_attempt'],2)
        self.assertEqual(state['steps'][1],'生成文档内容')
        self.assertEqual(len(list((Path(self.temp.name)/'uploads').iterdir())),1)
        correction=call.call_args_list[-1].args[1]['format_correction']
        self.assertEqual(correction['previous_output'],'PRIVATE BAD OUTPUT')
        self.assertNotIn('PRIVATE BAD OUTPUT',str(state))
        self.assertIsNone(state['activity'])

    def test_invalid_output_stops_after_two_corrections(self):
        error=model.StructuredOutputError([{'path':'sections.0','code':'value_error'}])
        state,call=self.run_turn([model.ProjectRoute(kind='create'),error,error,error])
        self.assertEqual(call.call_count,4)
        self.assertEqual(state['status'],'failed')
        self.assertIn('sections.0',state['error'])
        self.assertEqual(state['creation_attempt'],3)
        self.assertEqual(list((Path(self.temp.name)/'uploads').iterdir()),[])

    def test_network_retries_once_but_bad_http_requests_do_not(self):
        state,call=self.run_turn([model.ProjectRoute(kind='create'),model.TransportError('传输异常'),test_creation.CONTENT])
        self.assertEqual(state['status'],'completed',state)
        state,call=self.run_turn([model.ProjectRoute(kind='create'),model.TransportError('传输异常'),model.TransportError('传输异常')])
        self.assertEqual(state['status'],'failed')
        self.assertEqual(call.call_count,3)
        state,call=self.run_turn([model.ProjectRoute(kind='create'),model.ModelError('HTTP 400')])
        self.assertEqual(call.call_count,2)
        self.assertEqual(state['status'],'failed')


class ParseTests(unittest.IsolatedAsyncioTestCase):
    invoke = test_model_requests.RequestTests.invoke

    async def test_validation_diagnostics_hide_content_and_unknown_keys(self):
        reply=test_model_requests.Reply({'choices':[{'finish_reason':'stop','message':{'content':'{"name":"标题","title":"标题","sections":[{"kind":"paragraph","text":42,"PRIVATE_SECRET":"value"}]}'}}]})
        metrics=[];token=model.call_metrics.set(metrics)
        try:
            with self.assertRaises(model.StructuredOutputError) as error:
                await self.invoke(creation.NewDocument,reply)
            self.assertTrue(any(e['path']=='sections.0.text' for e in error.exception.issues))
            self.assertNotIn('PRIVATE_SECRET',str(metrics))
            self.assertEqual(metrics[0]['error_category'],'schema_validation')
        finally: model.call_metrics.reset(token)

    async def test_complete_json_fence_is_safe_to_unwrap(self):
        reply=test_model_requests.Reply({'choices':[{'finish_reason':'stop','message':{'content':'```json\n'+test_creation.CONTENT.model_dump_json()+'\n```'}}]})
        result,_=await self.invoke(creation.NewDocument,reply)
        self.assertEqual(result.title,test_creation.CONTENT.title)

    async def test_truncation_requests_regeneration_not_partial_parsing(self):
        reply=test_model_requests.Reply({'choices':[{'finish_reason':'length','message':{'content':'{"title":'}}]})
        with self.assertRaises(model.StructuredOutputError) as error:
            await self.invoke(creation.NewDocument,reply)
        self.assertEqual(error.exception.issues[0]['code'],'output_truncated')

    async def test_cancel_while_correcting_does_not_retry_again(self):
        async def progress(label, attempt):
            if '纠正' in label: raise asyncio.CancelledError()
        token=creation.progress_context.set(progress)
        try:
            with patch('app.model.call',side_effect=model.StructuredOutputError([{'path':'$','code':'json_invalid'}])) as call:
                with self.assertRaises(asyncio.CancelledError):
                    await creation.generate('生成文档',[])
                self.assertEqual(call.call_count,1)
        finally: creation.progress_context.reset(token)
