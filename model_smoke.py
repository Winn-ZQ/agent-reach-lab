"""两款模型的最小连通性测试；默认只检查，--run 才调用。每款最多一次，不重试。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import time
import hashlib
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from configure_model import CONFIG, MODELS, PRIVATE, private_directory, validate_key

PROMPT = '这是接口连通性测试。请只回复：连接成功'
LEDGER = PRIVATE / 'model-smoke.jsonl'


def load_config(path=CONFIG):
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('配置不能是符号链接')
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('凭据权限过宽')
    data = json.loads(path.read_text(encoding='utf-8'))
    validate_key(data.get('api_key', ''))
    endpoint = data.get('base_url', '')
    if not re.fullmatch(r'https://(?:dashscope\.aliyuncs\.com|[A-Za-z0-9][A-Za-z0-9-]{0,127}\.cn-beijing\.maas\.aliyuncs\.com)/compatible-mode/v1', endpoint):
        raise ValueError('仅允许已核实的百炼北京HTTPS接口')
    if data.get('free_only_user_confirmed') is not True or data.get('paid_calls_authorized') is not False:
        raise ValueError('未确认免费额度保护')
    if data.get('models') != MODELS:
        raise ValueError('模型范围不匹配')
    return data


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 凭据绝不转发到重定向目标。


def transport(config, body, timeout=30, response_limit=131072, audit=None):
    request = Request(config['base_url'] + '/chat/completions',
                      data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                      headers={'Authorization': 'Bearer ' + config['api_key'],
                               'Content-Type': 'application/json'}, method='POST')
    with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        raw = response.read(response_limit + 1)
        if audit:
            audit({'http_status': response.status, 'response_bytes': len(raw),
                   'body_sha256': hashlib.sha256(raw).hexdigest(),
                   'content_type_json': 'json' in response.headers.get('Content-Type', ''),
                   'body_kind': ('json' if raw.lstrip().startswith((b'{', b'[')) else
                                 'sse' if raw.lstrip().startswith((b'data:', b':')) else
                                 'gzip' if raw.startswith(b'\x1f\x8b') else 'other')})
        if len(raw) > response_limit:
            raise ValueError('响应超过限制')
        return json.loads(raw)


def append_event(path, event):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def run(config_path=CONFIG, ledger=LEDGER, send=transport):
    config = load_config(config_path)
    private_directory(ledger.parent)
    if ledger.is_symlink():
        raise ValueError('拒绝符号链接台账')
    lock = ledger.with_suffix('.lock')
    lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        prior = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
        # 未结束、缺失用量或任何失败记录都阻止自动继续；需先核查实际消耗。
        started = [e for e in prior if e.get('event') == 'started']
        done = [e for e in prior if e.get('event') == 'finished']
        if len(started) != len(done) or any(e.get('status') != 'ok' for e in done):
            raise ValueError('已有失败或未确认用量，停止调用，先检查台账')
        attempted = {e['model'] for e in started}
        results = []
        for model in MODELS:
            if model in attempted:
                continue
            body = {'model': model, 'messages': [{'role': 'user', 'content': PROMPT}],
                    'max_tokens': 64, 'enable_thinking': False, 'stream': False}
            append_event(ledger, {'event': 'started', 'model': model,
                                 'at': datetime.now(timezone.utc).isoformat(), 'request': body})
            then = time.monotonic()
            result = {'event': 'finished', 'model': model, 'usage': None,
                      'status': 'failed', 'billing': 'free_quota_expected_not_bill_verified'}
            try:
                response = send(config, body)
                choice = response['choices'][0]
                usage = response.get('usage')
                if isinstance(usage, dict):
                    # 只保留明确的计量字段，避免将未知响应文本或凭据写入日志。
                    result['usage'] = {k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                                       if type(usage.get(k)) is int and usage[k] >= 0}
                content = choice['message'].get('content')
                if (not isinstance(content, str) or not content.strip()
                        or choice.get('finish_reason') != 'stop'):
                    result['error'] = 'empty_or_incomplete_response'
                elif result['usage'] is None or len(result['usage']) != 3:
                    result['error'] = 'usage_unknown'
                else:
                    result['status'] = 'ok'
                    result['expected_reply_matched'] = content.strip() == '连接成功'
                    # 本轮只需证明收到回答，不保存模型自由文本或思考过程。
                    result['answer_characters'] = len(content)
            except HTTPError as exc:
                result['error'] = 'http_error'
                result['http_status'] = exc.code
            except Exception:
                result['error'] = 'transport_or_response_error_usage_unknown'
            result['elapsed_seconds'] = round(time.monotonic() - then, 3)
            append_event(ledger, result)
            results.append(result)
            if result['status'] != 'ok':
                break
        return results
    finally:
        os.close(lock_fd)
        lock.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='实际调用两款模型各一次，消耗免费额度')
    args = parser.parse_args()
    try:
        if args.run:
            results = run()
            print(json.dumps(results, ensure_ascii=False, indent=2))
            if any(r['status'] != 'ok' for r in results):
                raise SystemExit(1)
        else:
            load_config()
            print('配置检查通过；没有调用模型。使用 --run 才开始最多2次最小调用。')
    except (ValueError, OSError, TypeError, KeyError):
        raise SystemExit('未执行或已停止：请检查本机配置、文件权限及已有调用台账。密钥与异常原文不输出。')
