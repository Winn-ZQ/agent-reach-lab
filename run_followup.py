"""继续一个已停在 revise 的真实任务：只执行一次修正和一次再复核。"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from flow_backend import BackendStopped, MAX_INPUT_BYTES
from model_gateway import GRANT, ModelResponses
from research_flow import (ANALYZE, REVIEW, MAX_RESPONSE_BYTES, digest, encoded,
                           parse, targets, validate_draft, validate_review)
from run_research import task_case


def write_json(path, value):
    path = Path(path)
    path.write_text(encoded(value) + '\n', encoding='utf-8')


def report(case, draft, review, state):
    lines = [
        '# 研究流程跟进结果', '',
        '本目录继续同一证据包；没有重新搜索或抓取网页。', '',
        f"状态：{'模型复核通过' if state['review_status'] == 'passed' else '未通过复核'}；停止原因：{state['stop_reason']}。", '',
        f"真实 API 调用：{state['api_calls']}；Qwen 修正 1 次，DeepSeek 再复核 1 次。", '',
        '## 回答', ''
    ]
    for answer in draft.get('answers', []):
        lines += [f"### {answer.get('question_id')} · {answer.get('status')}", '', answer.get('text', ''), '']
        for ref in answer.get('refs', []):
            lines.append(f"- [{ref.get('source_id')}] {ref.get('quote')}（{ref.get('locator')}）")
        lines.append('')
    lines += ['## 复核', '', f"结论：{review.get('verdict')}", '']
    for issue in review.get('issues', []):
        lines.append(f"- {issue.get('severity')}：{issue.get('description')}（{issue.get('action')}）")
    lines += ['', '## 限制', '']
    lines += [f'- {item}' for item in draft.get('limitations', [])]
    lines += ['', '证据包哈希：' + digest(case), '']
    return '\n'.join(lines)


def run_followup(case, previous_dir, responses, output):
    previous_dir = Path(previous_dir)
    output = Path(output)
    if output.exists():
        raise BackendStopped('new_private_output_required')
    draft = parse((previous_dir / 'draft-1.json').read_text(encoding='utf-8'))
    previous_record = parse((previous_dir / 'review-1.json').read_text(encoding='utf-8'))
    previous_review = previous_record['result']
    previous_hash = digest(draft)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    state = {
        'schema_version': 'research-followup/0.1', 'mode': responses.mode,
        'response_provenance': responses.provenance, 'binding_scope': responses.binding_scope,
        'execution_status': 'running', 'review_status': 'not_run', 'stop_reason': None,
        'api_calls': 0, 'simulated_calls': 0, 'revision': 1, 'format_repairs': 0,
        'previous_draft_sha256': previous_hash, 'evidence_sha256': digest(case),
        'versions': [], 'reviews': [], 'events': []
    }

    def event(action, **fields):
        state.update(responses.snapshot())
        state['events'].append({'action': action, 'at': datetime.now(timezone.utc).isoformat(), **fields})
        write_json(output / 'state.json', state)

    def request(stage, payload, system, reserve_review=False):
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': encoded(payload)}]
        if len(encoded(messages).encode()) > MAX_INPUT_BYTES:
            raise BackendStopped('input_size_limit')
        number = state['api_calls'] + 1
        write_json(output / f'request-{number}.json', {'stage': stage, 'messages': messages,
                                                      'evidence_sha256': state['evidence_sha256']})
        event('request_prepared', stage=stage, request_number=number)
        text = responses.respond(stage, messages, reserve_review=reserve_review)
        if len(text.encode()) > MAX_RESPONSE_BYTES:
            raise BackendStopped('output_size_limit')
        (output / f'response-{number}.txt').write_text(text, encoding='utf-8')
        event('response_received', stage=stage, request_number=number)
        try:
            return parse(text)
        except (ValueError, TypeError, KeyError):
            raise BackendStopped('invalid_response') from None

    write_json(output / 'evidence.json', case)
    event('initialized', previous_run=str(previous_dir))
    repair_payload = {'case': case, 'draft': draft, 'local_issues': [], 'review': previous_review}
    repaired = request('repair', repair_payload,
                       ANALYZE + '\n按review中的问题只修正草稿；不得增加外部来源。', reserve_review=True)
    repair_issues = validate_draft(repaired, case)
    if repair_issues:
        state['review_status'] = 'blocked_by_validation'
        state['stop_reason'] = 'repair_' + ','.join(repair_issues)
        state['final_draft'] = repaired
        state['versions'].append({'revision': 2, 'draft_sha256': digest(repaired),
                                  'validation_issues': repair_issues})
        event('finished', reason=state['stop_reason'])
        write_json(output / 'draft-2.json', repaired)
        write_json(output / 'result.json', state)
        return state
    write_json(output / 'draft-2.json', repaired)
    state['final_draft'] = repaired
    state['versions'].append({'revision': 2, 'draft_sha256': digest(repaired), 'validation_issues': []})
    event('draft_checked', revision=2, issues=[])
    review_payload = {'case': case, 'draft': repaired, 'review_targets': targets(repaired)}
    review = request('review', review_payload, REVIEW)
    review_issues = validate_review(review, case, repaired)
    record = {'revision': 2, 'draft_sha256': digest(repaired), 'evidence_sha256': digest(case),
              'validation_issues': review_issues, 'result': review}
    write_json(output / 'review-2.json', record)
    state['reviews'].append(record)
    if review_issues:
        state['review_status'], state['stop_reason'] = 'invalid', 'invalid_review'
    elif review['verdict'] == 'pass':
        state['review_status'], state['stop_reason'] = 'passed', 'review_passed'
        state['execution_status'] = 'completed'
    else:
        state['review_status'], state['stop_reason'] = 'revise', 'followup_revision_limit'
    state['execution_status'] = 'completed' if state['review_status'] == 'passed' else 'stopped'
    event('finished', reason=state['stop_reason'])
    write_json(output / 'result.json', state)
    (output / 'report.md').write_text(report(case, repaired, review, state) + '\n', encoding='utf-8')
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True)
    parser.add_argument('--previous-run', required=True)
    parser.add_argument('--budget', default=str(GRANT))
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        case = task_case(args.case, datetime.now().astimezone().date().isoformat())
        responses = ModelResponses(args.budget, digest(case))
        with responses.session():
            result = run_followup(case, args.previous_run, responses, args.output)
        print(encoded({k: result.get(k) for k in ('execution_status', 'review_status', 'stop_reason',
                                                   'api_calls', 'known_tokens_by_model', 'usage_unknown')}))
    except BackendStopped as exc:
        parser.exit(1, '执行已停止：' + exc.code + '。没有自动重试或新增预算。\n')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        parser.exit(1, '无法继续：请检查案例、上一轮记录或预算；未输出密钥或异常原文。\n')


if __name__ == '__main__':
    main()
