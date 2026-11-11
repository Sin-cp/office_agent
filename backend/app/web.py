"""Bounded public-web retrieval. Only queries/URLs leave through the Search/Fetch API."""
import asyncio
from datetime import datetime, timedelta, timezone
import ipaddress
import os
import socket
import time
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx2 as httpx

MAX_SEARCHES = 3
MAX_FETCHES = 5
TOOL_TIMEOUT = 20
MAX_PAGE_CHARS = 10000
MAX_EVIDENCE_CHARS = 45000


def clock_context():
    zone = os.getenv('OFFICE_AGENT_TIMEZONE', 'Asia/Shanghai')
    try:
        now = datetime.now(ZoneInfo(zone))
    except (KeyError, ValueError):
        zone = 'Asia/Shanghai'
        now = datetime.now(timezone(timedelta(hours=8), name=zone))
    return dict(current_time=now.isoformat(timespec='seconds'), timezone=zone)


class WebError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def public_url(value):
    """Syntax/IP checks are synchronous; DNS checks are done immediately before retrieval."""
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 33 for c in value) or '\\' in value:
        raise WebError('blocked_url', '仅支持公开的 HTTP/HTTPS 网页链接')
    try:
        url = urlsplit(value)
        host = (url.hostname or '').rstrip('.').lower()
        if url.scheme.lower() not in ('http', 'https') or not host or url.username is not None or url.password is not None:
            raise ValueError()
        if url.port not in (None, 80, 443) or '%' in host:
            raise ValueError()
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.lan', '.home', '.test')):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if '.' not in host:
                raise ValueError()
            host.encode('idna')
        else:
            if not address.is_global or address.is_multicast:
                raise ValueError()
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped and not address.ipv4_mapped.is_global:
                raise ValueError()
        return urlunsplit((url.scheme.lower(), url.netloc, url.path or '/', url.query, ''))
    except (ValueError, UnicodeError):
        raise WebError('blocked_url', '不能读取本机、内网、带凭据或非公开的网页地址') from None


async def validate_public_url(value):
    value = public_url(value)
    parsed = urlsplit(value)
    try:
        async with asyncio.timeout(5):
            records = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except (OSError, TimeoutError):
        raise WebError('dns_failed', '网页域名无法解析，请检查链接或稍后重试') from None
    if not records:
        raise WebError('dns_failed', '网页域名无法解析')
    for row in records:
        address = ipaddress.ip_address(row[4][0])
        if not address.is_global or address.is_multicast:
            raise WebError('blocked_url', '网页地址指向本机或内网，已停止读取')
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped and not address.ipv4_mapped.is_global:
            raise WebError('blocked_url', '网页地址指向本机或内网，已停止读取')
    return value


class WebSession:
    def __init__(self, enabled=True, progress=None):
        self.enabled = enabled
        self.progress = progress
        self.searches = 0
        self.fetches = 0
        self.sources = []
        self.evidence = []
        self.notices = []
        self.metrics = []
        self.status = 'not_needed' if enabled else 'disabled'
        self._read = set()

    def notice(self, exc):
        item = dict(code=exc.code, message=str(exc))
        if item not in self.notices:
            self.notices.append(item)

    async def update(self, label):
        if self.progress:
            await self.progress(label)

    async def request(self, tool, payload):
        key = (os.getenv('DEEPSEEK_WEB_API_KEY') or os.getenv('DEEPSEEK_API_KEY', '')).strip()
        if not key:
            raise WebError('not_configured', '联网服务未配置 API Key，暂时无法查询')
        base = (os.getenv('DEEPSEEK_WEB_BASE_URL') or 'https://api.deepseek.com').rstrip('/')
        metric = dict(tool=tool, status='failed', elapsed_seconds=0)
        self.metrics.append(metric)
        started = time.monotonic()
        try:
            async with asyncio.timeout(TOOL_TIMEOUT):
                async with httpx.AsyncClient(timeout=TOOL_TIMEOUT, follow_redirects=False) as client:
                    response = await client.post(base + '/v1/tools/' + tool,
                        headers={'Authorization': 'Bearer ' + key}, json=payload)
            code = response.status_code
            errors = {
                401: ('authentication', '联网服务鉴权失败，请检查 API Key'),
                402: ('balance', '联网服务余额不足，暂时无法查询'),
                403: ('forbidden', '联网服务拒绝了此请求，网页可能不允许读取或账号无权限'),
                404: ('unavailable', '联网接口或网页内容不可用'),
                408: ('timeout', '联网查询超时，请稍后重试'),
                429: ('rate_limit', '联网服务请求受限，请稍后重试'),
                504: ('timeout', '联网查询超时，请稍后重试'),
            }
            if code != 200:
                reason, message = errors.get(code, ('service_error', f'联网服务暂时不可用（HTTP {code}）'))
                raise WebError(reason, message)
            if len(response.content) > 2_000_000:
                raise WebError('too_large', '网页返回内容过大，未用于回答')
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            metric['status'] = 'completed'
            return data
        except (TimeoutError, httpx.TimeoutException):
            raise WebError('timeout', '联网查询超时，请稍后重试') from None
        except httpx.RequestError:
            raise WebError('network_error', '无法连接联网服务，请检查后端网络') from None
        except (ValueError, TypeError):
            raise WebError('invalid_response', '联网服务返回格式无效，未使用该结果') from None
        finally:
            metric['elapsed_seconds'] = round(time.monotonic() - started, 3)

    def add_evidence(self, title, url, text, date=None, kind='page'):
        if not isinstance(text, str) or not text.strip():
            return
        remaining = MAX_EVIDENCE_CHARS - sum(len(e['content']) for e in self.evidence)
        if remaining <= 0:
            self.notice(WebError('content_limit', '本轮网页内容已达上限，部分内容未读取'))
            return
        truncated = len(text) > min(remaining, MAX_PAGE_CHARS)
        text = text[:min(remaining, MAX_PAGE_CHARS)]
        source = dict(title=str(title or url)[:300], url=url, published_at=date if isinstance(date,str) else None,
                      retrieved_at=clock_context()['current_time'], kind=kind)
        previous = next((s for s in self.sources if s['url'] == url), None)
        if previous:
            if source['published_at'] is None:
                source['published_at'] = previous['published_at']
            previous.update(source)
        else:
            self.sources.append(source)
        self.evidence.append({**source, 'content': text, 'truncated': truncated})

    async def search(self, query):
        if not self.enabled:
            return []
        if self.searches >= MAX_SEARCHES:
            raise WebError('budget', '本轮搜索次数已达上限')
        query = query.strip()
        if not query or len(query) > 128:
            raise WebError('invalid_query', '搜索关键词无效，请缩小查询范围')
        self.searches += 1
        await self.update('正在搜索')
        # No original prompt, conversation, attachment or localization/IP is sent.
        body = await self.request('search', dict(query=query, count=5, need_content=False))
        rows = body.get('results')
        if not isinstance(rows, list):
            raise WebError('invalid_response', '搜索服务返回格式无效')
        urls = []
        for row in rows[:5]:
            if not isinstance(row, dict):
                continue
            try:
                url = await validate_public_url(row.get('url'))
            except WebError:
                continue
            text = row.get('summary') or row.get('snippet')
            self.add_evidence(row.get('title'), url, text, row.get('date'), 'search')
            urls.append(url)
        return urls

    async def fetch(self, url):
        if not self.enabled:
            return
        url = await validate_public_url(url)
        if url in self._read:
            return
        if self.fetches >= MAX_FETCHES:
            raise WebError('budget', '本轮网页读取次数已达上限')
        self.fetches += 1
        self._read.add(url)
        await self.update('正在读取网页')
        body = await self.request('fetch', {'url': url})
        # The provider enforces redirect target security; our HTTP client never follows API redirects.
        # Validate a final URL as well if the provider includes one.
        final_url = await validate_public_url(body['url']) if body.get('url') else url
        if not isinstance(body.get('content'), str) or not body['content'].strip():
            raise WebError('empty_page', '网页没有可读取的正文，未能完成读取')
        self.add_evidence(body.get('title'), final_url, body['content'])

    def result_fields(self):
        return dict(web_sources=self.sources, web_status=self.status, web_notices=self.notices)
