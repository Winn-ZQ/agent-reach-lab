"""从已通过本地引用修正的草稿开始复核，必要时一次修正并再复核。"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from flow_backend import BackendStopped, MAX_INPUT_BYTES
from model_gateway import GRANT, ModelResponses
from research_flow import (ANALYZE, REVIEW, MAX_RESPONSE_BYTES, digest, encoded,
                           parse, targets, validate_draft, validate_review)
from run_research import task_case


def write_json(path, value):
    Path(path).write_text(encoded(value) + '\n', encoding='utf-8')


def write_report(output, draft, review, state):
    lines = ['# 研究流程续行结果', '',
             '本目录沿用同一证据包；没有重新搜索网页。', '',
             f"状态：{'模型复核通过' if state['review_status'] == 'passed' else '未通过复核'}；停止原因：{state['stop_reason']}。", '',
             f"真实 API 调用：{state['api_calls']}。", '', '## 回答', '']
    for answer in draft.get('answers', []):
        lines += [f"### {answer.get('question_id')} · {answer.get('status')}", '', answer.get('text', ''), '']
        lines += [f"- [{ref.get('source_id')}] {ref.get('quote')}（{ref.get('locator')}）"
                  for ref in answer.get('refs', [])] + ['']
    if review:
        lines += ['## 复核', '', '结论：' + str(review.get('verdict')), '']
        lines += [f"- {i.get('severity')}：{i.get('description')}（{i.get('action')}）"
                  for i in review.get('issues', [])]
    (Path(output) / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def run_resume(case, draft, responses, output, call_limit=3):
    output = Path(output)
    if output.exists():
        raise BackendStopped('new_private_output_required')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    state = {'schema_version': 'research-resume/0.1', 'mode': responses.mode,
             'response_provenance': responses.provenance, 'binding_scope': responses.binding_scope,
             'execution_status': 'running', 'review_status': 'not_run', 'stop_reason': None,
             'api_calls': 0, 'simulated_calls': 0, 'call_limit': call_limit,
             'evidence_sha256': digest(case), 'draft_sha256': digest(draft), 'events': [], 'reviews': []}

    def event(action, **fields):
        state.update(responses.snapshot())
        state['events'].append({'action': action, 'at': datetime.now(timezone.utc).isoformat(), **fields})
        write_json(output / 'state.json', state)

    def request(stage, payload, system, reserve_review=False):
        if state['api_calls'] + (2 if reserve_review else 1) > call_limit:
            raise BackendStopped('call_budget_exhausted')
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
    write_json(output / 'draft-1.json', draft)
    event('initialized')
    issues = validate_draft(draft, case)
    if issues:
        state['review_status'], state['stop_reason'] = 'blocked_by_validation', 'draft_' + ','.join(issues)
        state['final_draft'] = draft
        state['execution_status'] = 'stopped'
        event('finished', reason=state['stop_reason'])
        write_json(output / 'result.json', state)
        return state

    review = request('review', {'case': case, 'draft': draft, 'review_targets': targets(draft)}, REVIEW)
    review_issues = validate_review(review, case, draft)
    record = {'revision': 1, 'draft_sha256': digest(draft), 'evidence_sha256': digest(case),
              'validation_issues': review_issues, 'result': review}
    write_json(output / 'review-1.json', record)
    state['reviews'].append(record)
    if review_issues:
        state['review_status'], state['stop_reason'] = 'invalid', 'invalid_review'
    elif review['verdict'] == 'pass':
        state['review_status'], state['stop_reason'] = 'passed', 'review_passed'
    elif any(i.get('action') == 'fetch' for i in review.get('issues', [])):
        state['review_status'], state['stop_reason'] = 'revise', 'needs_sources'
    else:
        repaired = request('repair', {'case': case, 'draft': draft, 'local_issues': [], 'review': review},
                           ANALYZE + '\n按review中的问题只修正草稿；不得增加外部来源。', reserve_review=True)
        repair_issues = validate_draft(repaired, case)
        write_json(output / 'draft-2.json', repaired)
        state['final_draft'] = repaired
        if repair_issues:
            state['review_status'], state['stop_reason'] = 'blocked_by_validation', 'repair_' + ','.join(repair_issues)
        else:
            review2 = request('review', {'case': case, 'draft': repaired, 'review_targets': targets(repaired)}, REVIEW)
            review2_issues = validate_review(review2, case, repaired)
            record2 = {'revision': 2, 'draft_sha256': digest(repaired), 'evidence_sha256': digest(case),
                       'validation_issues': review2_issues, 'result': review2}
            write_json(output / 'review-2.json', record2)
            state['reviews'].append(record2)
            if review2_issues:
                state['review_status'], state['stop_reason'] = 'invalid', 'invalid_review'
            elif review2['verdict'] == 'pass':
                state['review_status'], state['stop_reason'] = 'passed', 'review_passed'
            else:
                state['review_status'], state['stop_reason'] = 'revise', 'revision_limit'
    state['final_draft'] = state.get('final_draft', draft)
    state['execution_status'] = 'completed' if state['review_status'] == 'passed' else 'stopped'
    event('finished', reason=state['stop_reason'])
    write_json(output / 'result.json', state)
    write_report(output, state['final_draft'], state['reviews'][-1]['result'] if state['reviews'] else None, state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True)
    parser.add_argument('--draft', required=True)
    parser.add_argument('--budget', default=str(GRANT))
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        case = task_case(args.case, datetime.now().astimezone().date().isoformat())
        draft = parse(Path(args.draft).read_text(encoding='utf-8'))
        responses = ModelResponses(args.budget, digest(case))
        with responses.session():
            result = run_resume(case, draft, responses, args.output)
        print(encoded({k: result.get(k) for k in ('execution_status', 'review_status', 'stop_reason',
                                                   'api_calls', 'known_tokens_by_model', 'usage_unknown')}))
    except BackendStopped as exc:
        parser.exit(1, '执行已停止：' + exc.code + '。没有自动重试或新增预算。\n')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        parser.exit(1, '无法继续：请检查案例、草稿或预算；未输出密钥或异常原文。\n')


if __name__ == '__main__':
    main()
