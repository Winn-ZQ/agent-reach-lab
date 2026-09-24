"""固定证据研究入口：默认只预检。--execute需已有新预算，不自行创建授权。"""
import argparse
from datetime import datetime
from pathlib import Path

from configure_model import PRIVATE
from flow_backend import BackendStopped
from model_gateway import GRANT, ModelResponses, checked_grant
from research_flow import digest, encoded, parse, prepare_case, run


def task_case(path, run_date):
    raw = parse(Path(path).read_text())
    if raw.get('schema_version') == 'evidence-package/0.1':
        from research_evidence import verify_package
        return verify_package(Path(path).parent, raw)
    if 'case' in raw:
        raw = raw['case']
    elif 'messages' in raw:
        raw = parse(raw['messages'][1]['content'])
    return prepare_case(raw, run_date)


def preflight(case, grant_path=GRANT):
    result = {'api_calls': 0, 'case_sha256': digest(case), 'budget_status': 'not_checked',
              'data_provenance': case['data_provenance'], 'questions': list(case['questions'])}
    try:
        grant = checked_grant(grant_path, result['case_sha256'])
        result.update(budget_status='configured_not_executed', max_calls=grant['max_calls'],
                      roles=grant['roles'], token_limits=grant['token_limits'])
    except BackendStopped as exc:
        result['budget_status'] = exc.code
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, help='原始case、离线场景或历史分析消息包')
    parser.add_argument('--date', default=datetime.now().astimezone().date().isoformat())
    parser.add_argument('--budget', default=str(GRANT))
    parser.add_argument('--output', help='真实执行须使用.local中的新目录')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    try:
        case = task_case(args.case, args.date)
        status = preflight(case, args.budget)
        if not args.execute:
            print(encoded(status))
            return
        if status['budget_status'] != 'configured_not_executed':
            raise BackendStopped(status['budget_status'])
        if not args.output:
            raise BackendStopped('output_required')
        output = Path(args.output).resolve()
        if not output.is_relative_to(PRIVATE.resolve()) or output == PRIVATE.resolve() or output.exists():
            raise BackendStopped('new_private_output_required')
        source = ModelResponses(args.budget, digest(case))
        with source.session():
            result = run(case, source, output, call_limit=source.grant['max_calls'])
        print(encoded({k: result[k] for k in ('execution_status', 'review_status', 'stop_reason',
                                             'api_calls', 'known_tokens_by_model', 'usage_unknown')}))
    except BackendStopped as exc:
        parser.exit(1, '执行已停止：' + exc.code + '。没有自动重试或新增预算。\n')
    except (ValueError, OSError, TypeError, KeyError, AttributeError):
        parser.exit(1, '无法继续：请检查输入、预算或本机运行记录；未输出密钥或异常原文。\n')


if __name__ == '__main__':
    main()
