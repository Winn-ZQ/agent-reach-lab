"""固定证据同题试测：默认预检；--run调用。最多10次，另计已完成的2次连接测试。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError

from configure_model import CONFIG, MODELS, PRIVATE, private_directory
from model_smoke import append_event, load_config, transport
from prepare_model_trial import REVIEW, ROOT, encoded, sha
from model_validation import validate
from model_usage import numeric_usage

INPUTS = ROOT / 'runs/model-trial-inputs-v1'
OUTPUT = PRIVATE / 'model-trial-v1'
MAX_CALLS = 10
MAX_OUTPUT = 4096
MAX_INPUT_BYTES = 120000
MODEL_TOKEN_GUARD = 300000


def packets(directory=INPUTS):
    manifest = json.loads((directory / 'manifest.json').read_text())
    result = {}
    for row in manifest['cases']:
        if row['file'] != row['id'] + '.json' or row['id'] not in ('web-facts', 'app-feedback', 'review-trap'):
            raise ValueError('invalid input file')
        text = (directory / row['file']).read_text()
        if sha(text) != row['sha256']:
            raise ValueError('input hash mismatch')
        result[row['id']] = json.loads(text)
    if len(result) != 3:
        raise ValueError('missing cases')
    return result





class Trial:
    def __init__(self, config, directory=OUTPUT, send=None):
        self.config, self.directory = config, directory
        self.send = send
        self.log = directory / 'events.jsonl'

    def history(self):
        if self.log.is_symlink():
            raise ValueError('symlink ledger')
        return [json.loads(s) for s in self.log.read_text().splitlines()] if self.log.exists() else []

    def call(self, model, case_id, stage, messages, reserve_review=False, json_mode=False):
        if model not in MODELS:
            raise ValueError('model outside approved set')
        events = self.history()
        starts = [e for e in events if e['event'] == 'started']
        ends = [e for e in events if e['event'] == 'finished']
        reconciled = {e['job']: e for e in events if e['event'] == 'reconciled'}
        if any(type(e.get('accounted_token_upper_bound')) is not int or e['accounted_token_upper_bound'] <= 0
               or e.get('free_only_switch_observed') is not True for e in reconciled.values()):
            raise ValueError('invalid reconciliation')
        if len(starts) != len(ends) or any(e['status'] != 'ok' and e['job'] not in reconciled for e in ends):
            raise ValueError('prior failed or uncertain usage; stop')
        job = model + '--' + case_id + '--' + stage
        if any(e['job'] == job for e in starts):
            raise ValueError('job already attempted; no automatic replay')
        needed = 2 if reserve_review else 1
        if len(starts) + needed > MAX_CALLS:
            raise ValueError('call budget including review exhausted')
        input_bytes = len(encoded(messages).encode())
        if input_bytes > MAX_INPUT_BYTES:
            raise ValueError('input size guard')
        consumed = sum((e['usage']['total_tokens'] if e.get('usage') else
                        reconciled[e['job']]['accounted_token_upper_bound'])
                       for e in ends if e['model'] == model)
        # UTF-8字节作为保守容量预留，不声称是供应商精确token估算。
        reserve = input_bytes + MAX_OUTPUT + 512
        if reserve_review:
            reserve += input_bytes + MAX_OUTPUT * 5 + 4096
        if consumed + reserve > MODEL_TOKEN_GUARD:
            raise ValueError('token guard including review exhausted')
        body = {'model': model, 'messages': messages, 'enable_thinking': False,
                'max_tokens': MAX_OUTPUT, 'stream': False}
        if json_mode:
            body['response_format'] = {'type': 'json_object'}
        request_text = encoded(body)
        (self.directory / (job + '.request.json')).write_text(request_text)
        append_event(self.log, {'event': 'started', 'job': job, 'model': model,
                               'case': case_id, 'stage': stage, 'input_sha256': sha(request_text),
                               'input_bytes': input_bytes, 'reserved_capacity': reserve,
                               'at': datetime.now(timezone.utc).isoformat()})
        then = time.monotonic()
        outcome = {'event': 'finished', 'job': job, 'model': model, 'case': case_id,
                   'stage': stage, 'status': 'failed', 'usage': None}
        content = None
        try:
            response = (self.send(self.config, body) if self.send else
                        transport(self.config, body, timeout=90, response_limit=524288,
                                  audit=lambda metadata: outcome.update({'http_diagnostic': metadata})))
            outcome['response_received'] = True
            # 数值/结构校验前保存脱敏响应，避免处理异常后丢掉可恢复答案与用量。
            raw_text = encoded(response).replace(self.config['api_key'], '[redacted]')
            (self.directory / (job + '.response.json')).write_text(raw_text)
            if isinstance(response, dict) and isinstance(response.get('usage'), dict):
                # 校验失败时也保存可核对的非负整数量，不保存任意供应商文本。
                outcome['reported_usage_fields'] = {
                    k: response['usage'][k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                    if type(response['usage'].get(k)) is int and response['usage'][k] >= 0}
            outcome['usage'] = numeric_usage(response.get('usage'))
            choice = response['choices'][0]
            content = choice['message'].get('content')
            if not isinstance(content, str) or not content.strip():
                raise ValueError('empty answer')
            # 若供应商异常回显密钥，停止且不落盘自由响应。
            if self.config['api_key'] in content:
                raise ValueError('credential echo')
            outcome['finish_reason'] = choice.get('finish_reason')
            if choice.get('finish_reason') != 'stop':
                raise ValueError('incomplete output')
            outcome['response_model'] = str(response.get('model', 'unknown'))[:160]
            if self.config['api_key'] in outcome['response_model']:
                outcome['response_model'] = '[redacted]'
            outcome['status'] = 'ok'
        except HTTPError as exc:
            outcome['error'] = 'http_error'
            outcome['http_status'] = exc.code
        except Exception as exc:
            outcome['error'] = 'response_or_transport_failure'
            outcome['failure_category'] = ('timeout' if isinstance(exc, TimeoutError) else
                                           'response_validation' if isinstance(exc, (ValueError, KeyError, TypeError, IndexError)) else
                                           'transport_or_other')
        outcome['elapsed_seconds'] = round(time.monotonic() - then, 3)
        if outcome['status'] == 'ok':
            (self.directory / (job + '.answer.txt')).write_text(content)
            try:
                parsed = json.loads(content)
                case = json.loads(messages[1]['content'])
                outcome['validation_issues'] = validate(parsed, case, stage)
                (self.directory / (job + '.result.json')).write_text(encoded(parsed))
            except (ValueError, TypeError, KeyError):
                outcome['validation_issues'] = ['invalid_json_or_schema']
        append_event(self.log, outcome)
        print(encoded({k: outcome[k] for k in ('job', 'status', 'usage', 'elapsed_seconds')}) , flush=True)
        if outcome['status'] != 'ok':
            raise ValueError('request failed; no automatic retry')
        return content


def run(continue_remaining=False):
    config = load_config()
    cases = packets()
    private_directory(OUTPUT)
    lock = OUTPUT / 'run.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        trial = Trial(config)
        if continue_remaining:
            if not any(e['event'] == 'reconciled' for e in trial.history()):
                raise ValueError('no reconciliation available')
            # 已核对历史消耗后，从尚未执行的主场景继续，不重放失败网页题。
            case_ids = ('app-feedback',)
            model_order = tuple(reversed(MODELS))
        elif trial.history():
            raise ValueError('trial already started; inspect before any new run')
        else:
            case_ids = ('web-facts', 'app-feedback')
            model_order = MODELS
        # 每份稿件紧接一次独立复核；不同模型拿到同一证据和提示，不共享答案。
        for case_id in case_ids:
            packet = cases[case_id]
            case = json.loads(packet['messages'][1]['content'])
            for model in model_order:
                draft = trial.call(model, case_id, 'analysis', packet['messages'], reserve_review=True)
                review_case = {**case, 'stage': 'review', 'draft': draft}
                review_messages = [{'role': 'system', 'content': REVIEW},
                                   {'role': 'user', 'content': encoded(review_case)}]
                trial.call(model, case_id, 'review', review_messages)
        for model in MODELS:
            trial.call(model, 'review-trap', 'review', cases['review-trap']['messages'])
    finally:
        os.close(fd)
        lock.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--continue-remaining', action='store_true', help='追加人工核查记录后，仅执行未开始的App与错误题；不重放网页题')
    args = parser.parse_args()
    try:
        if args.run:
            run()
        elif args.continue_remaining:
            run(continue_remaining=True)
        else:
            load_config()
            loaded = packets()
            print('预检通过：3题，最多10次调用，非思考模式，每次最大4096输出Token；尚未调用。')
    except (ValueError, OSError, KeyError, TypeError):
        raise SystemExit('试测停止：请检查配置、输入哈希、预算或私密台账。未打印异常原文或密钥。')
