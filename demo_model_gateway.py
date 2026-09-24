"""用模拟百炼响应验证适配器全流程；只用临时假凭据，真实API请求为0。"""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from configure_model import MODELS, save_new
from model_gateway import ModelResponses
from research_flow import digest, encoded, parse, prepare_case, run

ROOT = Path(__file__).resolve().parent
SCENARIO = ROOT/'runs/offline-flow-2026-09-23/scenarios/quote-repair.json'


def demo(output):
    output = Path(output)
    if output.exists():
        raise ValueError('output exists')
    scenario = parse(SCENARIO.read_text())
    case = prepare_case(scenario['case'], scenario['run_date'])
    rows = iter(scenario['responses'])
    calls = []
    def fake_transport(config, body):
        payload = parse(body['messages'][1]['content'])
        stage = 'review' if 'review_targets' in payload else 'repair' if 'local_issues' in payload else 'analysis'
        expected = next(rows)
        if expected['stage'] != stage:
            raise ValueError('mock stage mismatch')
        calls.append({'stage': stage, 'model': body['model']})
        content = expected['content']
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': content if isinstance(content, str) else encoded(content)}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}}
    # 所有凭据与批准标记均为模拟测试，绝不使用生产配置。
    with tempfile.TemporaryDirectory(prefix='research-gateway-demo-') as temp:
        private = Path(temp)/'private'
        config = private/'fake-key.json'
        save_new(config, {'api_key': 'sk-fake-offline-gateway-demo-only',
                         'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                         'models': MODELS, 'free_only_user_confirmed': True, 'paid_calls_authorized': False})
        now = datetime.now(timezone.utc)
        grant = {'grant_id':'demo-only', 'approved':True, 'authorization_note':'MOCK ONLY; no user live-call authorization',
                 'free_only':True, 'paid_calls_authorized':False, 'case_sha256':digest(case),
                 'parent_run':'offline-example-only', 'expires_at':(now+timedelta(hours=1)).isoformat(),
                 'max_calls':4, 'roles':{'analysis':MODELS[0], 'repair':MODELS[0], 'review':MODELS[1]},
                 'token_limits':{m:300000 for m in MODELS},
                 'free_quota_observation':{m:{'remaining_tokens':500000,'stop_when_used_up':True,'observed_at':now.isoformat()} for m in MODELS}}
        budget_path=private/'fake-budget.json'; save_new(budget_path,grant)
        source=ModelResponses(budget_path,digest(case),Path(temp)/'mock-ledgers',config,send=fake_transport)
        with source.session():
            state=run(case,source,output)
        summary={'mode':'mock_api','real_api_calls':0,'calls':calls,
                 'mock_usage_notice':'每个模拟响应固定100输入＋50输出Token，不是估算或真实消耗。',
                 'response_provenance':scenario['provenance'],
                 'result':{k:state[k] for k in ('review_status','stop_reason','revision','simulated_calls','known_tokens_by_model')}}
        (output/'adapter-summary.json').write_text(encoded(summary)+'\n')
        return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(encoded(demo(args.output)))
