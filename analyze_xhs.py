"""复用已采集小红书证据运行既有研究流程；默认预检，--execute才调用模型。"""
import argparse
from pathlib import Path
import uuid

from collect_xhs import verify_package
from configure_model import PRIVATE, private_directory, save_new
from evidence_segments import catalog, present_case
from flow_backend import BackendStopped
from model_gateway import ModelResponses
from research_budget import WebBudget
from research_exports import export_csv, export_markdown
from research_flow import digest, encoded, run


def capacity(case):
    return min(180000, max(65536, len(encoded(present_case(case,catalog(case))).encode())*3+16384))


def execute(source, output, *, budget=None, backend_factory=ModelResponses):
    case = verify_package(source)
    output = Path(output).resolve()
    if not output.is_relative_to(PRIVATE.resolve()) or output == PRIVATE.resolve() or output.exists():
        raise ValueError('new private output required')
    budget = budget or WebBudget(PRIVATE/'web-budget')
    cap = capacity(case)
    status = budget.status('qwen-review-thinking-json-v2', cap)
    if not status['ready']: raise BackendStopped(status['reason'])
    private_directory(output)
    tid = uuid.uuid4().hex
    task = {'id':tid,'mode':'live_api','status':'running','question':case['task'],
            'input':{'region':'','period':'','sources':['xhs'],'required_sources':['xhs']},
            'evidence_sha256':digest(case), 'execution_policy':'preview-v1',
            'source_directory':str(Path(source).resolve())}
    save_new(output/'task-input.json',task)
    grant = budget.issue(tid,digest(case),'saved-xhs-evidence-'+digest(case)[:16],requested_calls=5,
                         transport_mode='sse',model_profile='qwen-review-thinking-json-v2',max_input_bytes=cap)
    backend = backend_factory(grant,digest(case))
    try:
        with backend.session():
            state = run(case,backend,output/'flow',call_limit=5,reference_mode='compact-v2')
    finally:
        budget.settle(tid,backend.snapshot())
    task.update(status=state['execution_status'],reason=state['stop_reason'])
    detail={'task':task,'case':case,'state':state}
    save_new(output/'detail.json',detail)
    for name,text in [('report.md',export_markdown(detail)),('materials.csv',export_csv(detail))]:
        path=output/name;path.write_text(text);path.chmod(0o600)
    return detail


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--output')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    try:
        if args.execute:
            if not args.output:parser.error('--execute requires --output')
            d=execute(args.source,args.output)
            print(encoded({k:d['state'][k] for k in ['execution_status','review_status','stop_reason','api_calls','known_tokens_by_model','usage_unknown']}))
        else:
            case=verify_package(args.source)
            print(encoded({'notes':len(case['sources']),'model_calls':0,'case_sha256':digest(case),
                           'budget':WebBudget(PRIVATE/'web-budget').status('qwen-review-thinking-json-v2',capacity(case))}))
    except BackendStopped as exc:
        print('已停止：'+exc.code);raise SystemExit(2)
    except (ValueError,OSError,KeyError):
        print('输入或输出未通过校验；原始资料保留。');raise SystemExit(2)
