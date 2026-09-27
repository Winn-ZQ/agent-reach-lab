"""百炼模型适配与持久预算。没有新批准预算时，在读取凭据及联网前拒绝。"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import socket
import ssl
from http.client import RemoteDisconnected, IncompleteRead
import time
from urllib.error import HTTPError, URLError

from configure_model import CONFIG, MODELS, PRIVATE, private_directory
from flow_backend import (BackendStopped, ResponseBackend, MAX_INPUT_BYTES,
                          MAX_OUTPUT_TOKENS, TOKEN_OVERHEAD, REVIEW_CAPACITY)
from model_smoke import append_event, load_config, transport
from model_usage import numeric_usage
from model_profiles import SUPPORTED_MODELS, role_options, output_capacity, request_options
from evidence_segments import recover_case

GRANT = PRIVATE / 'research-budget.json'
LEDGERS = PRIVATE / 'research-budgets'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def checked_grant(path, case_hash, now=None):
    """预算必须事先存在；不创建、批准或自动扩充预算。"""
    path = Path(path)
    if not path.exists():
        raise BackendStopped('new_budget_required')
    if path.is_symlink() or path.parent.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise BackendStopped('budget_file_permissions')
    try:
        data = json.loads(path.read_text())
        if (data.get('approved') is not True or data.get('free_only') is not True
                or data.get('paid_calls_authorized') is not False):
            raise ValueError()
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', data['grant_id']):
            raise ValueError()
        if data['case_sha256'] != case_hash or not re.fullmatch(r'[a-f0-9]{64}', case_hash):
            raise ValueError()
        if not isinstance(data['authorization_note'], str) or not data['authorization_note'].strip():
            raise ValueError()
        # 关联旧任务只记录关系，不赋予重置旧台账的能力。
        if not isinstance(data['parent_run'], str) or not data['parent_run'].strip():
            raise ValueError()
        expires = datetime.fromisoformat(data['expires_at'].replace('Z', '+00:00'))
        if expires.tzinfo is None or expires <= (now or datetime.now(timezone.utc)):
            raise BackendStopped('budget_expired')
        if type(data['max_calls']) is not int or not 2 <= data['max_calls'] <= 5:
            raise ValueError()
        if data.get('transport_mode','json') not in ('json','sse'):
            raise ValueError()
        input_limit = data.get('max_input_bytes', MAX_INPUT_BYTES)
        if type(input_limit) is not int or not 8192 <= input_limit <= MAX_INPUT_BYTES:
            raise ValueError()
        if set(data['roles']) != {'analysis', 'review', 'repair'}:
            raise ValueError()
        if any(m not in SUPPORTED_MODELS for m in data['roles'].values()):
            raise ValueError()
        for stage in data['roles']:
            role_options(data, stage)
        models = set(data['roles'].values())
        if set(data['token_limits']) != models or set(data['free_quota_observation']) != models:
            raise ValueError()
        for model in models:
            limit = data['token_limits'][model]
            observation = data['free_quota_observation'][model]
            if (type(limit) is not int or limit <= 0 or type(observation['remaining_tokens']) is not int
                    or observation['remaining_tokens'] < limit or observation['stop_when_used_up'] is not True):
                raise ValueError()
            observed = datetime.fromisoformat(observation['observed_at'].replace('Z', '+00:00'))
            current = now or datetime.now(timezone.utc)
            if observed.tzinfo is None or observed > current or (current-observed).total_seconds() > 86400:
                raise BackendStopped('quota_observation_stale')
        return data
    except BackendStopped:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise BackendStopped('invalid_budget') from None


def safe_failure_kind(exc):
    # 只输出固定分类，不序列化异常消息、URL、头部或任意类名。
    cause = exc.reason if isinstance(exc, URLError) else exc
    for cls, label in ((ssl.SSLCertVerificationError, 'tls_certificate'),
                       (ssl.SSLEOFError, 'tls_eof'),
                       (socket.gaierror, 'dns'), (TimeoutError, 'timeout'),
                       (RemoteDisconnected, 'remote_closed'), (IncompleteRead, 'incomplete_read'),
                       (ConnectionResetError, 'connection_reset'),
                       (ConnectionRefusedError, 'connection_refused'), (ssl.SSLError, 'tls')):
        if isinstance(cause, cls): return label
    return 'network' if isinstance(exc, (OSError, URLError)) else 'response_or_usage'


class ModelResponses(ResponseBackend):
    """必须在session上下文内使用；mock传输与真实请求分开标记和存放。"""
    def __init__(self, grant_path, case_hash, ledger_root=LEDGERS, config_path=CONFIG, send=None):
        self.grant_path, self.case_hash = Path(grant_path), case_hash
        self.ledger_root, self.config_path = Path(ledger_root), Path(config_path)
        self.send = send
        self.mode = 'mock_api' if send is not None else 'live_api'
        self.provenance = ('API适配器模拟传输；不会发出真实请求。' if send is not None else
                           '通过用户配置的百炼北京API真实调用；资料属性另行标注。')
        self.binding_scope = 'current_request'
        self.active = False
        self.grant = None
        self.directory = None

    def events(self):
        path = self.directory / 'events.jsonl'
        if path.is_symlink():
            raise BackendStopped('invalid_ledger')
        try:
            return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        except (ValueError, OSError):
            raise BackendStopped('invalid_ledger') from None

    def check_scope(self, case_hash):
        if not self.active or case_hash != self.case_hash:
            raise BackendStopped('task_scope_mismatch')

    def snapshot(self):
        events = self.events() if self.directory else []
        starts = [e for e in events if e.get('event') == 'started']
        ends = [e for e in events if e.get('event') == 'finished']
        usage = {m: sum(e['usage']['total_tokens'] for e in ends if e.get('model') == m and e.get('usage'))
                 for m in dict.fromkeys([*MODELS, *(self.grant['roles'].values() if self.grant else [])])}
        return {'api_calls': len(starts) if self.mode == 'live_api' else 0,
                'simulated_calls': len(starts) if self.mode == 'mock_api' else 0,
                'known_tokens_by_model': usage,
                'usage_unknown': len(starts) != len(ends) or any(e.get('usage') is None for e in ends),
                'paid_calls_authorized': False, 'billing_verified': False,
                'grant_id': self.grant['grant_id'] if self.grant else None,
                'last_failure': ({'kind':ends[-1].get('failure_kind', ends[-1].get('error','unknown')),
                                  'phase':ends[-1].get('http_diagnostic',{}).get('phase'),
                                  'http_status':ends[-1].get('http_status',ends[-1].get('http_diagnostic',{}).get('http_status'))}
                                 if ends and ends[-1].get('status') != 'ok' else None)}

    @contextmanager
    def session(self):
        if self.active:
            raise BackendStopped('session_already_active')
        self.grant = checked_grant(self.grant_path, self.case_hash)
        private_directory(self.ledger_root)
        # 测试不能消耗或复用生产预算账本。
        self.directory = self.ledger_root / (self.mode + '-' + self.grant['grant_id'])
        private_directory(self.directory)
        lock = self.directory / 'run.lock'
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        locked = False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError:
                raise BackendStopped('task_busy') from None
            events = self.events()
            grant_hash = sha(self.grant)
            if events and events[0].get('grant_sha256') != grant_hash:
                raise BackendStopped('budget_changed')
            if any(e.get('event') == 'started' for e in events):
                # 不提供自动恢复；改输出目录不能复用已消耗授权。
                raise BackendStopped('budget_already_used')
            if not events:
                append_event(self.directory/'events.jsonl', {'event': 'initialized', 'grant_sha256': grant_hash,
                             'case_sha256': self.case_hash, 'mode': self.mode, 'parent_run': self.grant['parent_run']})
            self.active = True
            yield self
        finally:
            self.active = False
            if locked:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def respond(self, stage, messages, reserve_review=False):
        if not self.active:
            raise BackendStopped('session_required')
        current = checked_grant(self.grant_path, self.case_hash)
        if sha(current) != sha(self.grant):
            raise BackendStopped('budget_changed')
        if stage not in self.grant['roles']:
            raise BackendStopped('unknown_stage')
        try:
            if len(messages) != 2 or [m['role'] for m in messages] != ['system', 'user']:
                raise ValueError()
            payload = json.loads(messages[1]['content'])
            task = payload.get('case', payload)
            if 'reference_contract' in task:
                task = recover_case(task)
            task_hash = hashlib.sha256(json.dumps(task, ensure_ascii=False, sort_keys=True, indent=2).encode()).hexdigest()
            if task_hash != self.case_hash:
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise BackendStopped('task_scope_mismatch') from None
        # 不能由调用方忘传参数而取消分析/修正的复核预留。
        reserve_review = stage in ('analysis', 'repair') or reserve_review
        events = self.events()
        starts = [e for e in events if e.get('event') == 'started']
        ends = [e for e in events if e.get('event') == 'finished']
        if (len(starts) != len(ends) or any(e.get('status') != 'ok' for e in ends)
                or [e['attempt'] for e in starts] != [e['attempt'] for e in ends]):
            raise BackendStopped('prior_failure_or_unknown_usage')
        if len(starts) + (2 if reserve_review else 1) > self.grant['max_calls']:
            raise BackendStopped('call_budget_exhausted')
        model = self.grant['roles'][stage]
        input_bytes = len(encoded(messages).encode())
        input_limit = self.grant.get('max_input_bytes', MAX_INPUT_BYTES)
        if input_bytes > input_limit:
            raise BackendStopped('input_size_limit')
        options = role_options(self.grant, stage)
        capacity = input_bytes + output_capacity(self.grant, stage) + TOKEN_OVERHEAD
        reserves = {model: capacity}
        if reserve_review:
            reviewer = self.grant['roles']['review']
            reserves[reviewer] = reserves.get(reviewer, 0) + input_limit + output_capacity(self.grant, 'review') + TOKEN_OVERHEAD
        used = self.snapshot()['known_tokens_by_model']
        if any(used[m] + value > self.grant['token_limits'][m] for m, value in reserves.items()):
            raise BackendStopped('token_budget_exhausted')
        # 预算检查之后才读取凭据；继承HTTPS域名白名单及免费保护检查。
        try:
            config = load_config(self.config_path)
        except (ValueError, OSError, TypeError, KeyError):
            raise BackendStopped('model_configuration_invalid') from None
        if model not in config['models']:
            raise BackendStopped('model_not_configured')
        body = {'model': model, 'messages': messages, **request_options(self.grant, stage),
                'stream': self.grant.get('transport_mode','json') == 'sse'}
        if body['stream']:
            body['stream_options']={'include_usage':True}
        # JSON模式由版本化配置明确选择，所有配置仍执行相同的本地严格校验。
        if config['api_key'] in encoded(body):
            raise BackendStopped('credential_in_request')
        attempt = len(starts) + 1
        request_text = encoded(body)
        (self.directory/f'request-{attempt}.json').write_text(request_text)
        append_event(self.directory/'events.jsonl', {'event': 'started', 'attempt': attempt, 'model': model,
                     'stage': stage, 'request_sha256': sha(body), 'reserved_capacity': reserves,
                     'role_options': options, 'model_profile': self.grant.get('model_profile', 'legacy'),
                     'at': datetime.now(timezone.utc).isoformat()})
        outcome = {'event': 'finished', 'attempt': attempt, 'stage': stage, 'model': model,
                   'status': 'failed', 'usage': None, 'billing': 'free_expected_not_verified'}
        started_at = time.monotonic()
        content = None
        try:
            response = (self.send(config, body) if self.send else
                        transport(config, body, timeout=options['timeout_seconds'], response_limit=1048576,
                                  audit=lambda value: outcome.update(http_diagnostic=value)))
            # 在校验前保留脱敏响应，避免异常时丢失供应商返回的计量。
            text = encoded(response).replace(config['api_key'], '[redacted]')
            (self.directory/f'response-{attempt}.json').write_text(text)
            outcome['usage'] = numeric_usage(response.get('usage'))
            if (outcome['usage']['prompt_tokens'] > input_bytes + TOKEN_OVERHEAD
                    or outcome['usage']['completion_tokens'] > output_capacity(self.grant, stage)):
                raise BackendStopped('capacity_estimate_exceeded')
            choice = response['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise BackendStopped('incomplete_response')
            content = choice['message'].get('content')
            if not isinstance(content, str) or not content.strip():
                raise BackendStopped('empty_response')
            if config['api_key'] in content:
                raise BackendStopped('credential_echo')
            outcome['status'] = 'ok'
        except BackendStopped as exc:
            outcome['error'] = exc.code
        except HTTPError as exc:
            outcome.update(error='http_error', http_status=exc.code)
        except Exception as exc:
            # 不能把供应商异常原文写入日志或前端。
            outcome['error'] = 'transport_or_usage_error'
            outcome['failure_kind'] = safe_failure_kind(exc)
        outcome['elapsed_seconds'] = round(time.monotonic() - started_at, 3)
        append_event(self.directory/'events.jsonl', outcome)
        if outcome['status'] != 'ok':
            raise BackendStopped(outcome['error'])
        return content
