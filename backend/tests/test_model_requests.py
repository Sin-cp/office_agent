"""Regression coverage for the provider's JSON-mode precondition."""
import json
import unittest
from unittest.mock import patch
from app import model

class Reply:
    status_code = 200
    def __init__(self, body): self.body = body
    def json(self): return self.body

class RequestTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, schema, response):
        captured = []
        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, headers, json):
                captured.append(json)
                return response
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'test-secret'}), patch('app.model.httpx.AsyncClient', Client):
            result = await model.call('核对内容，仅保留任务意图。', {'instruction': '核对内容'}, schema)
        return result, captured[0]

    async def test_every_structured_call_includes_explicit_json_instruction(self):
        cases = [
            (model.Review, dict(complete=True,text='已完成')),
            (model.Memory, dict(summary='摘要',preferences=[])),
            (model.Route, dict(kind='answer',scope='all',instruction='总结')),
            (model.ProjectRoute, dict(kind='edit',file_ids=['one'])),
            (model.Response, dict(kind='answer',text='回答')),
            (model.NewDocument, dict(name='文档',title='文档',sections=[dict(kind='paragraph',text='正文')])),
        ]
        for schema, data in cases:
            with self.subTest(schema=schema.__name__):
                reply=Reply({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(data)}}]})
                _, payload=await self.invoke(schema,reply)
                self.assertEqual(payload['response_format'],{'type':'json_object'})
                self.assertIn('json',payload['messages'][0]['content'].lower())
                self.assertIn('不要使用 Markdown',payload['messages'][0]['content'])

    async def test_plain_chat_keeps_unstructured_output(self):
        reply=Reply({'choices':[{'finish_reason':'stop','message':{'content':'你好'}}]})
        result,payload=await self.invoke(None,reply)
        self.assertEqual(result,'你好')
        self.assertNotIn('response_format',payload)
        self.assertNotIn('JSON Schema',payload['messages'][0]['content'])

    async def test_bad_requests_are_classified_without_leaking_upstream_text(self):
        for detail,category in [
            ("Prompt must contain the word 'json' to use response_format",'json_prompt_requirement'),
            ('maximum context length exceeded','context_limit'),
            ('test-secret PRIVATE DOCUMENT invalid argument','invalid_request'),
        ]:
            reply=Reply({'error':{'message':detail}});reply.status_code=400
            metrics=[];token=model.call_metrics.set(metrics)
            try:
                with self.assertRaises(model.ModelError) as error:
                    await self.invoke(model.Review,reply)
                self.assertIn('HTTP 400',str(error.exception))
                self.assertNotIn('test-secret',str(error.exception))
                self.assertNotIn('PRIVATE DOCUMENT',str(error.exception))
                self.assertEqual(metrics[0]['error_category'],category)
                self.assertEqual(metrics[0]['response_schema'],'Review')
                self.assertEqual(metrics[0]['http_status'],400)
            finally:
                model.call_metrics.reset(token)
