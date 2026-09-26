"""固定虚构证据复核对照；默认预检，--run仅调用同一复核模型两次。"""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path

from configure_model import PRIVATE, save_new
from flow_backend import BackendStopped
from model_gateway import ModelResponses
from research_budget import WebBudget
from research_flow import REVIEW, digest, encoded, parse, prepare_case, validate_review

FIXTURE = Path(__file__).parent/'tests/fixtures/review-semantics.json'
CONDITIONS = ('paragraph', 'atomic')
ATOMIC_RULE = '''每个待检目标是一条独立结论，context保留其完整段落。
分别判断每条结论是否得到证据支持，不以同段其他正确内容代替该条的证据。
只检查review_targets中的目标，保持同一输出格式；结论关系本身也需要依据。'''


def materialize(fixture):
    """白名单构造模型可见输入，不发送答案键、分类标签或评分理由。"""
    questions = {g['id']: '核对以下表述是否受给定资料支持。' for g in fixture['groups']}
    case = prepare_case({'task': '依据给定资料核对离线笔记X的功能、条件与未知；不使用外部知识。',
                         'kind': 'synthetic_review_benchmark', 'questions': questions,
                         'sources': deepcopy(fixture['sources'])}, '2026-09-26')
    sources = {s['source_id']:s for s in case['sources']}
    draft = {'answers': [{'question_id':g['id'], 'status':'unknown' if g['id']=='Q7' else 'answered',
                          'text':' '.join(c['text'] for c in g['claims']),
                          'refs':[{'source_id':sid,'quote':sources[sid]['quote'],'locator':sources[sid]['locator']}
                                  for sid in g['source_ids']]}
                         for g in fixture['groups']], 'hypotheses':[], 'limitations':['待核查草稿，不自证正确。']}
    return case, draft


def packet(fixture, condition):
    if condition not in CONDITIONS:
        raise ValueError('unknown condition')
    case, draft = materialize(fixture)
    targets = {}
    for group, answer in zip(fixture['groups'], draft['answers']):
        if condition == 'paragraph':
            targets['A:'+group['id']] = answer
        else:
            for claim in group['claims']:
                targets['A:'+claim['id']] = {'claim':claim['text'], 'context':answer['text']}
    return {'case':case, 'draft':draft, 'review_targets':targets}


def score(fixture, condition, response):
    payload = packet(fixture, condition)
    expected = {}
    for group in fixture['groups']:
        if condition == 'paragraph':
            expected['A:'+group['id']] = all(c['expected_supported'] for c in group['claims'])
        else:
            expected.update({'A:'+c['id']:c['expected_supported'] for c in group['claims']})
    # 校验器只需目标ID来校验复核覆盖；不把答案键或此对象发给模型。
    target_draft = {'answers':[{'question_id':k[2:]} for k in expected], 'hypotheses':[]}
    issues = validate_review(response, payload['case'], target_draft, require_grounding=True)
    if not issues and Counter(c['target_id'] for c in response['checks']) != Counter(expected.keys()):
        issues.append('duplicate_or_missing_target')
    if issues:
        return {'valid':False, 'validation_issues':issues, 'target_count':len(expected), 'scores':None}
    actual = {c['target_id']:c['supported'] for c in response['checks']}
    def compare(wanted, got):
        return {'correct':sum(got[k] == v for k,v in wanted.items()), 'total':len(wanted),
                'false_accepts':[k for k,v in wanted.items() if not v and got[k]],
                'false_rejections':[k for k,v in wanted.items() if v and not got[k]]}
    group_expected = {'A:'+g['id']:all(c['expected_supported'] for c in g['claims']) for g in fixture['groups']}
    group_actual = actual if condition == 'paragraph' else {
        'A:'+g['id']:all(actual['A:'+c['id']] for c in g['claims']) for g in fixture['groups']}
    return {'valid':True, 'validation_issues':[], 'target_count':len(expected),
            'scores':compare(expected,actual), 'group_scores':compare(group_expected,group_actual),
            'note':'只是固定答案键的标签匹配；还须人工核对理由。粒度不同的目标总数不能直接当整体准确率比较。'}


def evaluate(fixture, backend, directory, conditions=CONDITIONS):
    """两次独立上下文，不把前一条件的回答或评分放入下一请求。"""
    results = []
    for condition in conditions:
        payload = packet(fixture, condition)
        system = REVIEW + ('\n'+ATOMIC_RULE if condition == 'atomic' else '')
        messages = [{'role':'system','content':system}, {'role':'user','content':encoded(payload)}]
        save_new(directory/(condition+'-request.json'), {'messages':messages})
        try:
            text = backend.respond('review', messages)
        except BackendStopped as exc:
            results.append({'condition':condition, 'status':'transport_stopped', 'reason':exc.code})
            break  # 传输或用量失败不重试，不派发下一个条件。
        save_new(directory/(condition+'-response.json'), {'content':text})
        try:
            response = parse(text)
            result = score(fixture, condition, response)
        except (ValueError, TypeError, KeyError):
            result = {'valid':False, 'validation_issues':['invalid_json_or_schema'], 'scores':None}
        results.append({'condition':condition, 'status':'scored', **result})
        save_new(directory/(condition+'-score.json'), results[-1])
    return results


def comparison_ready(results):
    return ([r.get('condition') for r in results] == list(CONDITIONS)
            and all(r.get('status') == 'scored' and r.get('valid') is True for r in results))


def inherit_paragraph(fixture, parent):
    """只补缺失的第二组；重新校验旧响应及两组旧输入，不能借恢复更换题目。"""
    manifest=json.loads((parent/'manifest.json').read_text())
    if (manifest.get('parent_run') or manifest['fixture_sha256']!=digest(fixture)
            or manifest['case_sha256']!=digest(materialize(fixture)[0])):
        raise ValueError('parent fixture mismatch')
    old=json.loads((parent/'summary.json').read_text())['results']
    if ([r.get('condition') for r in old]!=list(CONDITIONS)
            or old[0].get('valid') is not True or old[1].get('status')!='transport_stopped'):
        raise ValueError('parent not recoverable')
    for condition in CONDITIONS:
        saved=json.loads((parent/(condition+'-request.json')).read_text())
        system=REVIEW+('\n'+ATOMIC_RULE if condition=='atomic' else '')
        if saved!={'messages':[{'role':'system','content':system},
                             {'role':'user','content':encoded(packet(fixture,condition))}]}:
            raise ValueError('parent request mismatch')
    response=parse(json.loads((parent/'paragraph-response.json').read_text())['content'])
    rescored=score(fixture,'paragraph',response)
    if not rescored['valid']:raise ValueError('parent response invalid')
    return {'condition':'paragraph','status':'scored',**rescored,'inherited_from':parent.name}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--id',default='review-semantics-20260926-v1')
    parser.add_argument('--resume',help='只恢复此原始对照缺失的atomic组，保留原始记录')
    parser.add_argument('--transport',choices=('json','sse'),default='json')
    args=parser.parse_args()
    fixture=json.loads(FIXTURE.read_text())
    case,_=materialize(fixture)
    for name in (args.id,args.resume):
        if name is not None and (not name.replace('-','').isalnum() or len(name)>48):
            raise ValueError('invalid run id')
    inherited=[inherit_paragraph(fixture,PRIVATE/args.resume)] if args.resume else []
    conditions=('atomic',) if args.resume else CONDITIONS
    if not args.run:
        print(encoded({'calls_if_run':len(conditions),'transport_mode':args.transport,'fixture_sha256':digest(fixture),'case_sha256':digest(case),
                       'targets':{c:len(packet(fixture,c)['review_targets']) for c in CONDITIONS}}))
        return
    if not args.id.replace('-','').isalnum() or len(args.id)>48:
        raise ValueError('invalid run id')
    # 与网页模型任务共用独占锁及预算账本；不会绕过日预算、免费保护或未知用量。
    lock=PRIVATE/'live-research/worker.lock'
    fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    backend=None; grant=None
    budget=WebBudget(PRIVATE/'web-budget')
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.resume and (PRIVATE/args.resume/'recovery.json').exists():
            raise ValueError('parent recovery already allocated')
        directory=PRIVATE/args.id
        directory.mkdir(mode=0o700,exist_ok=False)
        save_new(directory/'manifest.json',{'fixture_sha256':digest(fixture),'case_sha256':digest(case),
                    'conditions':list(conditions),'max_calls':len(conditions),'parent_run':args.resume,
                    'transport_mode':args.transport,'at':datetime.now(timezone.utc).isoformat()})
        grant=budget.issue(args.id,digest(case),args.resume or 'fixed-synthetic-review-benchmark',
                           requested_calls=2,transport_mode=args.transport)
        if args.resume:save_new(PRIVATE/args.resume/'recovery.json',{'child':args.id})
        backend=ModelResponses(grant,digest(case))
        with backend.session():
            results=inherited+evaluate(fixture,backend,directory,conditions)
        summary={'results':results,'usage':backend.snapshot(),'comparison_ready':comparison_ready(results)}
        save_new(directory/'summary.json',summary)
        print(encoded(summary))
        return 0 if summary['comparison_ready'] else 2
    finally:
        if backend is not None and backend.grant is not None:
            budget.settle(args.id,backend.snapshot())
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


if __name__=='__main__':
    try: raise SystemExit(main() or 0)
    except BackendStopped as exc: raise SystemExit('验证未继续：'+exc.code)
    except (OSError,ValueError,TypeError,KeyError): raise SystemExit('验证未继续；检查本地输入、预算或已有目录。不输出凭据或异常原文。')
