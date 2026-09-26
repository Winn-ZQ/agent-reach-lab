"""通过 Agent-Reach/Jina 获取证据，有限重试并保留失败记录。"""
import argparse
import hashlib
import json
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

from agent_reach.channels.web import WebChannel


class AcquisitionError(Exception):
    def __init__(self, kind, message, retryable=False):
        super().__init__(message)
        self.kind, self.retryable = kind, retryable


def http_failure(code):
    if code in (401, 403):
        return AcquisitionError('access_required', '访问被拒绝；可能需登录或授权，请在本机检查。')
    if code in (404, 410):
        return AcquisitionError('not_found', '页面不存在或已移除，请核对链接。')
    if code == 429:
        return AcquisitionError('rate_limited', '服务限流，本次停止，请稍后再试。')
    return AcquisitionError('service_error', f'服务返回 HTTP {code}。', code in (408, 500, 502, 503, 504))


def validate_content(content):
    if not content.strip():
        raise AcquisitionError('empty', '返回正文为空。')
    # Jina 可用 HTTP 200 包装目标站点错误；只匹配包装头，避免正文提及错误被误判。
    header = content.split('Markdown Content:', 1)[0]
    match = re.search(r'^Warning: Target URL returned error (\d{3})\b', header, re.M | re.I)
    if match:
        raise http_failure(int(match.group(1)))
    title = re.search(r'^Title:\s*(.*)$', header, re.M)
    if title and re.fullmatch(r'(?:404(?:\s*[-:|]\s*|\s+))?(?:page not found|not found)|页面不存在|页面未找到', title[1].strip(), re.I):
        raise AcquisitionError('not_found', '读取到页面不存在提示，未取得目标资料。')
    # 某些站点沿用正常标题、HTTP 200，却只返回缺页外壳。
    # 仅拒绝明确缺页双标记且无其他正文的模板，正常文章讨论404不受影响。
    body = content.partition('Markdown Content:')[2]
    if (title and re.fullmatch(r'client challenge', title[1].strip(), re.I)
            and 'A required part of this site couldn’t load.' in body):
        raise AcquisitionError('access_required', '读取到浏览器验证提示，未取得目标正文。')
    if 'Markdown Content:' in content and not body.strip():
        raise AcquisitionError('empty', '读取器只返回页面标题，正文为空。')
    if (re.search(r'^#\s+Page not found\.?\s*$',body,re.M|re.I)
            and re.search(r'This page may be private\. You may be able to view it by',body,re.I)):
        raise AcquisitionError('not_found', '页面返回缺页提示及登录说明，未取得目标正文。')
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    text_lines = [line for line in lines if not re.fullmatch(r'(?:!?\[.*\]\([^)]+\))+', line)]
    normalized = [line.casefold().strip('# *') for line in text_lines]
    if (normalized[:2] == ['not found', 'this page does not exist']
            and all(line in ('interactive graph', 'on this page') for line in normalized[2:])):
        raise AcquisitionError('not_found', '页面只返回不存在提示，未取得目标资料。')
    if title and re.fullmatch(r'log\s*in|sign\s*in|登录|用户登录|just a moment\.\.\.', title[1].strip(), re.I):
        raise AcquisitionError('access_required', '读取到登录或验证页面，需要在本机处理。')
    if re.search(r'^Warning:.*(?:requiring captcha|requires authentication)', header, re.M | re.I):
        raise AcquisitionError('access_required', '页面要求验证或登录，需要在本机处理。')


def classify(exc):
    if isinstance(exc, AcquisitionError):
        return exc
    if isinstance(exc, HTTPError):
        return http_failure(exc.code)
    if isinstance(exc, (TimeoutError, socket.timeout)) or (isinstance(exc, URLError) and isinstance(exc.reason, (TimeoutError, socket.timeout))):
        return AcquisitionError('timeout', '请求超时。', True)
    if isinstance(exc, URLError):
        return AcquisitionError('network_error', '网络连接失败，请检查网络或执行环境权限。')
    if isinstance(exc, RuntimeError) and '反爬验证页' in str(exc):
        return AcquisitionError('access_required', '目标返回验证页面，需要在本机处理。')
    if isinstance(exc, ValueError):
        return AcquisitionError('invalid_input_or_response', '链接或响应不符合读取要求。')
    return AcquisitionError('unexpected_error', f'获取发生未预期错误（{type(exc).__name__}），停止并检查。')


def collect(url, max_retries=1, reader=None, sleeper=time.sleep):
    if not 0 <= max_retries <= 2:
        raise ValueError('重试次数须为 0–2')
    reader = reader or WebChannel().read
    record = {'url': url, 'source': 'web', 'backend': 'Agent-Reach WebChannel / Jina Reader',
              'fetched_at': None, 'published_at': None, 'status': 'error', 'content': '',
              'error': None, 'attempts': [], 'max_retries': max_retries}
    for n in range(max_retries + 1):
        at = datetime.now(timezone.utc).isoformat()
        try:
            content = reader(url)
            validate_content(content)
            record['attempts'].append({'attempt': n + 1, 'at': at, 'status': 'fetched'})
            record.update(status='fetched_unverified', fetched_at=at, content=content, error=None,
                          content_sha256=hashlib.sha256(content.encode()).hexdigest())
            return record
        except Exception as exc:
            failure = classify(exc)
            retry = failure.retryable and n < max_retries
            record['attempts'].append({'attempt': n + 1, 'at': at, 'status': 'error',
                                       'kind': failure.kind, 'retry_scheduled': retry})
            if retry:
                sleeper(1)
                continue
            record.update(status='needs_user' if failure.kind == 'access_required' else 'error',
                          error={'kind': failure.kind, 'message': str(failure),
                                 'attempts_exhausted': failure.retryable},
                          conclusion='未取得该来源的有效正文，不能据此生成事实结论。')
            return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--output', required=True)
    parser.add_argument('--max-retries', type=int, choices=range(3), default=1)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        parser.error('输出已存在，请使用新路径，保留历史记录')
    record = collect(args.url, args.max_retries)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in record.items() if k != 'content'}, ensure_ascii=False))
    return 1 if record['error'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
