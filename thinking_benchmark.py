"""有界角色配置对照；默认预检，--run才使用本机免费预算执行一次。"""
import argparse
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path

from configure_model import PRIVATE, save_new
from flow_backend import BackendStopped
from flow_backend import MAX_INPUT_BYTES
from model_gateway import ModelResponses
from research_budget import WebBudget
from research_flow import digest, encoded, parse, prepare_case, review_instructions, targets, validate_review
from review_benchmark import FIXTURE, packet
import evidence_segments
import compact_review

ARMS = ('qwen-review-thinking-v1', 'deepseek-review-thinking-v1', 'max-review-thinking-v1')


def examples():
    mixed = json.loads(FIXTURE.read_text())
    mixed_packet = packet(mixed, 'paragraph')
    mixed_expected = {'A:'+g['id']:all(c['expected_supported'] for c in g['claims']) for g in mixed['groups']}
    # 新的正例仅验证误报；与mixed一样全部是虚构资料，不冒充真实平台。
    sources = [dict(source_id='S1', locator='虚构导出规则',
                    quote='资料盒Y免费版可导出Markdown，仅供个人非商业使用。团队商业使用需购买团队版。'),
               dict(source_id='S2', locator='虚构容量规则',
                    quote='免费版每月可导出20份文件；失败的导出不扣次数。当月未用完的次数不结转至下月。')]
    case = prepare_case(dict(kind='synthetic_positive_control', task='核对资料盒Y免费导出的格式、适用范围、次数与计数规则。',
                            questions={'Q1':'格式和使用范围是什么？','Q2':'每月次数是多少？失败与结转如何处理？'},
                            sources=sources), '2026-09-26')
    draft = dict(answers=[dict(question_id='Q1',status='answered',
                        text='免费版支持Markdown导出，仅供个人非商业使用；团队商业使用需购买团队版。',
                        refs=[dict(source_id='S1',quote=sources[0]['quote'],locator=sources[0]['locator'])]),
                         dict(question_id='Q2',status='answered',
                        text='免费版每月可导出20份，失败的导出不扣次数，未用次数不结转到下月。',
                        refs=[dict(source_id='S2',quote=sources[1]['quote'],locator=sources[1]['locator'])])],
                 hypotheses=[],limitations=['虚构资料，只能据所给规则核对。'])
    positive = dict(case=case,draft=draft,review_targets=targets(draft))
    return [('mixed', mixed_packet, mixed_expected),('positive',positive,{'A:Q1':True,'A:Q2':True})]


def score(payload, response, expected):
    errors = validate_review(response,payload['case'],payload['draft'],require_grounding=True,include_limitations=payload.get('reference_mode')==compact_review.MODE)
    result = dict(validation_errors=errors,
                  verdict=response.get('verdict') if isinstance(response, dict) else None,
                  valid=not errors)
    if errors or expected is None:
        result['semantic_score']=None
        return result
    actual={c['target_id']:c['supported'] for c in response['checks']}
    result['semantic_score']=dict(correct=sum(actual[k]==v for k,v in expected.items()),total=len(expected),
        false_accepts=[k for k,v in expected.items() if not v and actual[k]],
        false_rejections=[k for k,v in expected.items() if v and not actual[k]])
    return result


def messages(payload, reference_mode='literal'):
    # expected仅保存在评分端，模型可见内容按白名单投影。
    visible={k:deepcopy(payload[k]) for k in ('case','draft','review_targets')}
    system = review_instructions(visible['draft'])
    if reference_mode == compact_review.MODE:
        visible = compact_review.review_payload(visible,evidence_segments.catalog(payload['case']))
        system = compact_review.REVIEW
    elif reference_mode == 'segments-v1':
        visible['case'] = evidence_segments.present_case(payload['case'], evidence_segments.catalog(payload['case']))
        system += evidence_segments.instructions('review')
    elif reference_mode != 'literal':
        raise ValueError('invalid reference mode')
    return [dict(role='system',content=system),
            dict(role='user',content=encoded(visible))]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--id',default='thinking-review-20260926-v1')
    parser.add_argument('--real-request',type=Path,help='先前保存在本机的复核请求；读取case及draft，使用当前固定提示词')
    parser.add_argument('--arm',action='append',choices=(*ARMS,'qwen-review-thinking-json-v2','max-review-thinking-json-v2',
                                                      'deepseek-review-thinking-budget-v2'))
    parser.add_argument('--case',action='append',choices=('mixed','positive','real-vscode'))
    parser.add_argument('--reference-mode',choices=('literal','segments-v1',compact_review.MODE),default='literal')
    args=parser.parse_args()
    if not args.id.replace('-','').isalnum() or len(args.id)>40:raise ValueError('invalid id')
    cases=examples()
    if args.real_request:
        saved=parse(args.real_request.read_text())
        payload=parse(saved['messages'][1]['content'])
        payload={k:payload[k] for k in ('case','draft','review_targets')}
        cases.append(('real-vscode',payload,None))
    if args.case:
        cases=[row for row in cases if row[0] in args.case]
        if len(cases)!=len(set(args.case)):raise ValueError('missing case')
    arms=tuple(args.arm or ARMS)
    if len(arms)!=len(set(arms)):raise ValueError('duplicate arm')
    manifest=dict(cases=[dict(name=n,input_sha256=digest(messages(p,args.reference_mode)),gold_sha256=digest(g)) for n,p,g in cases],
                  reference_mode=args.reference_mode,
                  arms=list(arms),max_requests=len(cases)*len(arms),transport='sse',
                  rule='一次每格；独立上下文；同题同提示词；不自动重试。语义标签与理由分别评审。')
    if not args.run:
        print(encoded(manifest));return
    root=PRIVATE/args.id
    root.mkdir(mode=0o700,exist_ok=False)
    save_new(root/'manifest.json',manifest)
    # 单一执行锁，与网页共用日额度、历史未知用量及模型额度观察。
    fd=os.open(PRIVATE/'live-research/worker.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    rows=[];budget=WebBudget(PRIVATE/'web-budget')
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for index,(label,payload,gold) in enumerate(cases):
            # 轮换顺序，防止某模型总是排在前面；不是随机重复试验。
            order=arms[index:]+arms[:index]
            for arm in order:
                identity=args.id+'-'+str(index)+'-'+str(arms.index(arm))
                directory=root/(label+'-'+arm);directory.mkdir(mode=0o700)
                request_messages=messages(payload,args.reference_mode)
                save_new(directory/'request.json',dict(messages=request_messages))
                backend=None
                row=dict(case=label,arm=arm,request_sha256=digest(request_messages),reference_mode=args.reference_mode)
                try:
                    input_bytes = len(encoded(request_messages).encode())
                    input_cap = min(MAX_INPUT_BYTES, max(8192, ((input_bytes+8191)//8192)*8192))
                    row.update(input_bytes=input_bytes, max_input_bytes=input_cap)
                    grant=budget.issue(identity,digest(payload['case']),args.id,requested_calls=2,
                                       transport_mode='sse',model_profile=arm,max_input_bytes=input_cap)
                    backend=ModelResponses(grant,digest(payload['case']))
                    with backend.session():
                        text=backend.respond('review',request_messages)
                    save_new(directory/'response.json',dict(content=text))
                    response=parse(text)
                    if args.reference_mode in ('segments-v1',compact_review.MODE):
                        segments=evidence_segments.catalog(payload['case'])
                        save_new(directory/'evidence-segments.json',segments)
                        try:
                            if args.reference_mode == compact_review.MODE:
                                response,bindings=compact_review.normalize_review(response,payload['case'],payload['draft'],segments)
                            else:
                                response,bindings=evidence_segments.bind_response(response,'review',payload['case'],segments)
                        except ValueError as exc:
                            row['reference_error']=str(exc)
                            raise
                        save_new(directory/'bound-response.json',response)
                        save_new(directory/'reference-bindings.json',dict(bindings=bindings,
                            catalog_sha256=evidence_segments.fingerprint(segments),bound_sha256=digest(response)))
                    scoring_payload={**payload,'reference_mode':args.reference_mode}
                    row.update(score(scoring_payload,response,gold))
                except BackendStopped as exc:
                    row.update(stopped=exc.code)
                except (ValueError,TypeError,KeyError):
                    row.update(stopped='invalid_response')
                finally:
                    if backend is not None and backend.grant is not None:
                        row['usage']=backend.snapshot()
                        budget.settle(identity,backend.snapshot())
                        ends=[e for e in backend.events() if e.get('event')=='finished']
                        if ends:row.update(elapsed_seconds=ends[-1].get('elapsed_seconds'),token_usage=ends[-1].get('usage'))
                save_new(directory/'result.json',row);rows.append(row)
                print(encoded(row),flush=True)
                if row.get('stopped') in ('free_quota_insufficient','daily_call_limit','quota_observation_stale'):
                    save_new(root/'summary.json',rows);return
        save_new(root/'summary.json',rows)
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,TypeError,KeyError):
        raise SystemExit('未执行或未完成；检查本机输入和已有目录，不输出异常原文。')
