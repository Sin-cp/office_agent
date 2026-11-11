"""No-network tests for web retrieval, isolation, cancellation and persistence."""
import asyncio
import json
import os
from pathlib import Path
import socket
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from app import database, model, web
import loop_support
import test_files
from test_documents import example

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
URL = 'https://example.com/article'
PAGE = {'title':'公开资料', 'content':'这是一段公开网页正文。'}
SEARCH = {'results':[{'title':'公开资料', 'url':URL, 'summary':'今日公开信息', 'date':'2026-11-03T00:00:00Z'}]}


class URLTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_credentials_and_unsupported_urls_are_blocked(self):
        blocked = ['http://localhost/', 'http://127.0.0.1/', 'http://10.0.0.1/', 'http://192.168.1.2/',
                   'http://169.254.169.254/latest/meta-data', 'http://[::1]/', 'http://[::ffff:127.0.0.1]/',
                   'file:///etc/passwd', 'ftp://example.com/a', 'https://user:pass@example.com/',
                   'https://example.com:8000/', 'https://a.local/', 'https://example.com\\@localhost/',
                   'https://example.com/\nsecret']
        with patch('app.web.socket.getaddrinfo', return_value=PUBLIC) as dns:
            for url in blocked:
                with self.subTest(url=url), self.assertRaises(web.WebError):
                    await web.validate_public_url(url)
            dns.assert_not_called()

    async def test_dns_alias_and_mixed_public_private_records_are_blocked(self):
        private = [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        for records in [private, PUBLIC+private]:
            with patch('app.web.socket.getaddrinfo',return_value=records),self.assertRaises(web.WebError):
                await web.validate_public_url('https://public-looking.example.com/')
        with patch('app.web.socket.getaddrinfo',return_value=PUBLIC):
            self.assertEqual(await web.validate_public_url(URL+'#section'),URL)

    async def test_redirect_final_url_checked(self):
        session=web.WebSession()
        with patch('app.web.socket.getaddrinfo',return_value=PUBLIC),patch.object(session,'request',return_value={**PAGE,'url':'http://127.0.0.1/'}):
            with self.assertRaises(web.WebError): await session.fetch(URL)
        self.assertEqual(session.sources,[])


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_session_never_requests(self):
        session=web.WebSession(False)
        with patch.object(session,'request') as request:
            self.assertEqual(await session.search('你好'),[])
            request.assert_not_called()
        self.assertEqual(session.sources,[])

    async def test_request_sends_only_keyword_and_enforces_limits(self):
        session=web.WebSession()
        request=AsyncMock(return_value=SEARCH)
        with patch.object(session,'request',request),patch('app.web.socket.getaddrinfo',return_value=PUBLIC):
            for _ in range(3): await session.search('北京 2026-11-03 天气')
            with self.assertRaises(web.WebError): await session.search('第四次')
        self.assertEqual(request.call_count,3)
        for args in request.call_args_list:
            self.assertEqual(args.args[1],{'query':'北京 2026-11-03 天气','count':5,'need_content':False})
        request=AsyncMock(return_value=PAGE)
        with patch.object(session,'request',request),patch('app.web.socket.getaddrinfo',return_value=PUBLIC):
            for i in range(5): await session.fetch(URL+str(i))
            with self.assertRaises(web.WebError): await session.fetch(URL+'six')
            await session.fetch(URL+'0')
        self.assertEqual(request.call_count,5)

    async def test_missing_config_timeout_and_empty_page(self):
        session=web.WebSession()
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'','DEEPSEEK_WEB_API_KEY':''}),self.assertRaises(web.WebError) as error:
            await session.request('search',{'query':'天气'})
        self.assertEqual(error.exception.code,'not_configured')
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-only'}),patch('app.web.httpx.AsyncClient',side_effect=TimeoutError),self.assertRaises(web.WebError) as error:
            await session.request('search',{'query':'天气'})
        self.assertEqual(error.exception.code,'timeout')
        with patch.object(session,'request',return_value={'url':URL,'content':' '}),patch('app.web.socket.getaddrinfo',return_value=PUBLIC),self.assertRaises(web.WebError) as error:
            await session.fetch(URL)
        self.assertEqual(error.exception.code,'empty_page')

    async def test_clock_injected_and_api_redirects_disabled(self):
        payloads=[]
        class Reply:
            status_code=200
            content=b'{}'
            def json(self): return {'results':[]}
        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def post(self,url,headers,json): payloads.append((url,json)); return Reply()
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-only'}),patch('app.web.httpx.AsyncClient',return_value=Client()) as client:
            await web.WebSession().request('search',{'query':'test'})
        client.assert_called_once_with(timeout=20,follow_redirects=False)
        self.assertTrue(payloads[0][0].endswith('/v1/tools/search'))
        with patch.dict(os.environ,{'OFFICE_AGENT_TIMEZONE':'invalid-zone'}):
            self.assertEqual(web.clock_context()['timezone'],'Asia/Shanghai')
            self.assertTrue(web.clock_context()['current_time'].endswith('+08:00'))


class WebTaskTests(unittest.TestCase):
    setUp=test_files.FilesTest.setUp
    tearDown=test_files.FilesTest.tearDown

    def wait(self,identity):
        for _ in range(300):
            state=self.client.get('/api/tasks/'+identity).json()
            if state['status'] not in ('queued','running','cancelling'): return state
            time.sleep(.01)
        self.fail('Task timeout')

    def test_sources_persist_after_restart_and_web_off_sends_no_queries(self):
        async def request(self,tool,payload): return SEARCH if tool=='search' else PAGE
        replies=[{'content':'','tool_calls':[loop_support.call('search_web',{'query':'北京天气'})],'finish_reason':'tool_calls'},
                 loop_support.text_reply('今天的天气资料如下。')]
        with loop_support.calls(*replies),patch('app.web.WebSession.request',request),patch('app.web.socket.getaddrinfo',return_value=PUBLIC):
            created=self.client.post('/api/tasks',json={'prompt':'北京天气'}).json()
            state=self.wait(created['id'])
        self.assertEqual(state['status'],'completed',state)
        self.assertTrue(state['web_enabled'])
        self.assertEqual(state['result']['web_sources'][0]['url'],URL)
        database.initialize(Path(self.temp.name))
        project=state['project_id']
        history=self.client.get(f'/api/projects/{project}/history').json()
        self.assertEqual(history['items'][0]['result']['web_sources'],state['result']['web_sources'])
        messages=self.client.get(f'/api/projects/{project}/messages').json()['items']
        self.assertEqual(messages[-1]['content']['web_sources'],state['result']['web_sources'])
        with loop_support.calls(loop_support.text_reply('联网已关闭')),patch('app.web.WebSession.request') as request:
            created=self.client.post('/api/tasks',json={'project_id':project,'prompt':'再查一下','web_enabled':False}).json()
            state=self.wait(created['id'])
            request.assert_not_called()
        self.assertEqual(state['result']['web_status'],'disabled')
        self.assertEqual(state['result']['web_sources'],[])

    def test_cancel_stops_search_and_persists_cancelled_state(self):
        started=threading.Event()
        async def slow(self,tool,payload):
            started.set()
            await asyncio.Event().wait()
        replies=[{'content':'','tool_calls':[loop_support.call('search_web',{'query':'天气'})],'finish_reason':'tool_calls'}]
        with loop_support.calls(*replies),patch('app.web.WebSession.request',slow),patch('app.web.socket.getaddrinfo',return_value=PUBLIC):
            created=self.client.post('/api/tasks',json={'prompt':'查天气'}).json()
            self.assertTrue(started.wait(2))
            running=self.client.get('/api/tasks/'+created['id']).json()
            self.assertEqual(running['web_activity'],'正在搜索')
            self.client.post('/api/tasks/'+created['id']+'/cancel')
            state=self.wait(created['id'])
        self.assertEqual(state['status'],'cancelled',state)
        self.assertIsNone(state['result'])
        self.assertIsNone(state['web_activity'])

    def test_document_failure_does_not_publish_changes(self):
        project=self.client.post('/api/projects').json()['id']
        file=self.client.post('/api/files?project_id='+project,files={'file':('test.docx',example())}).json()
        replies=[{'content':'','tool_calls':[loop_support.call('search_web',{'query':'最新资料'})],'finish_reason':'tool_calls'},
                 loop_support.text_reply('联网查询超时，未修改文档。')]
        with loop_support.calls(*replies),patch('app.web.socket.getaddrinfo',return_value=PUBLIC),patch('app.web.WebSession.request',side_effect=web.WebError('timeout','联网查询超时')):
            created=self.client.post('/api/tasks',json={'project_id':project,'file_ids':[file['id']],'prompt':'联网核实后修改文档'}).json()
            state=self.wait(created['id'])
        self.assertEqual(state['status'],'completed',state)
        self.assertNotIn('file',state['result'] or {})
        self.assertIn('联网查询超时',' '.join(str(e.get('summary') or '') for e in state['tools']))
        self.assertEqual(self.client.get('/api/files').json()['total'],1)
