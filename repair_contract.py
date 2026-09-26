"""逐问题修正交接。只保证版本、覆盖及真实改动，不声称能判断语义正确。"""
from copy import deepcopy

PATCH_INSTRUCTION = '''本次只输出修正补丁JSON，不输出整份报告：
{"changes":[{"target_id":"A:Q1","replacement":{"question_id":"Q1","status":"answered","text":"完整的新回答","refs":[{"source_id":"S1","quote":"连续原文","locator":"原来源位置"}]}}],
"resolutions":[{"issue_id":"I1","resolution":"changed或disputed","reason":"如何消除该问题，或为何原意见不成立","refs":[{"source_id":"S1","quote":"连续原文"}]}]}。
repair_plan中每个issue_id恰好回应一次。changed要求修改对应目标的正文含义，不能只换引文或只改其他目标；同一目标只给一个replacement，处理它的全部问题。
disputed用于原意见有误，须提供原文依据；不能为了满足审稿人而造事实。changed回执refs若为空，程序只允许沿用对应replacement中通过逐字验证的refs；不会从其他目标猜测依据。
可以同时修正其他已发现的错误目标。A目标replacement是完整answer；H目标是完整hypothesis。可选limitations为修正后的限制列表。
未列在changes中的目标由程序原样保留，不要复制无关全文。保留所有目标ID，不增加或删除回答/假设。
每个answered替换回答的refs必须非空；逐字复制case.sources中content/quote内的连续原文，并逐字复制source.locator。不得只给正文却把refs全部留空。
修正后继续成立的旧引文可以复用；新的事实也必须有原文依据。不得用元数据或自己改写的文字作quote。
先解决错误关系、标题和列表归类，再输出补丁。修改后仍需独立复核，自己声称解决不代表通过。'''

RECHECK_INSTRUCTION = '''revision_context含repair_plan和程序生成的changes及模型resolutions。
除了检查整份新稿，还必须输出repair_checks数组，每个issue_id恰好一次：
{"issue_id":"I1","resolved":true,"reason":"对照旧稿、实际修改和原文后的判断","refs":[{"source_id":"S1","quote":"连续原文"}]}。
逐项确认原问题已消除，或原意见确实不成立；不能因文字变了或修正者自称已解决就判resolved=true。
必须给原文依据。任何resolved=false都必须verdict=revise，并在issues中关联原目标。'''

def target_map(draft):
    return {**{'A:'+a['question_id']:a for a in draft['answers']},
            **{'H:'+str(i):h for i,h in enumerate(draft['hypotheses'],1)}}

def build_plan(draft, review):
    targets=target_map(draft);result=[]
    for i,issue in enumerate(review['issues'],1):
        tid=issue.get('target_id')
        if tid not in targets:raise ValueError('repair_target_missing')
        result.append({'issue_id':'I'+str(i),'target_id':tid,'problem':deepcopy(issue),
                       'before':deepcopy(targets[tid]),
                       'review_evidence':[deepcopy(c) for c in review['checks'] if c['target_id']==tid],
                       'acceptance':'对照原文解决此问题的含义和分类；如果意见有误，举原文说明。'})
    return result

def grounded(refs, case):
    if not isinstance(refs,list) or not refs:return False
    sources={s['source_id']:s for s in case['sources']}
    for ref in refs:
        if not isinstance(ref,dict):return False
        source=sources.get(ref.get('source_id'));quote=ref.get('quote')
        if not source or source.get('included') is False or not isinstance(quote,str) or not quote.strip():return False
        if ''.join(quote.split()) not in ''.join((source.get('content') or source.get('quote') or '').split()):return False
    return True

def unique_rows(rows, key, expected):
    if not isinstance(rows,list) or any(not isinstance(r,dict) or not isinstance(r.get(key),str) for r in rows):
        raise ValueError('repair_contract_schema')
    ids=[r[key] for r in rows]
    if len(ids)!=len(set(ids)) or set(ids)!=set(expected):raise ValueError('repair_issue_coverage')
    return {r[key]:r for r in rows}

def apply_patch(draft, patch, plan, case):
    if not isinstance(patch,dict) or set(patch)-{'changes','resolutions','limitations'}:
        raise ValueError('repair_patch_schema')
    old=target_map(draft);changes=patch.get('changes')
    if not isinstance(changes,list) or any(not isinstance(c,dict) or not isinstance(c.get('target_id'),str) for c in changes):
        raise ValueError('repair_patch_schema')
    ids=[c['target_id'] for c in changes]
    if len(ids)!=len(set(ids)) or not set(ids)<=set(old):raise ValueError('repair_patch_targets')
    new=deepcopy(old)
    for change in changes:
        tid=change['target_id'];replacement=change.get('replacement')
        if not isinstance(replacement,dict) or tid.startswith('A:') and replacement.get('question_id')!=tid[2:]:
            raise ValueError('repair_patch_identity')
        if not isinstance(replacement.get('text'),str) or not replacement['text'].strip():raise ValueError('repair_patch_text')
        new[tid]=deepcopy(replacement)
    receipts=unique_rows(patch.get('resolutions'),'issue_id',[p['issue_id'] for p in plan])
    for problem in plan:
        receipt=receipts[problem['issue_id']];tid=problem['target_id']
        refs=receipt.get('refs')
        if receipt.get('resolution')=='changed' and refs==[]:
            refs=new[tid].get('refs')
        if receipt.get('resolution') not in ('changed','disputed') or not isinstance(receipt.get('reason'),str) or not receipt['reason'].strip() or not grounded(refs,case):
            raise ValueError('repair_resolution_evidence')
        if receipt['resolution']=='changed' and ''.join(new[tid]['text'].split())==''.join(old[tid]['text'].split()):
            raise ValueError('repair_target_unchanged')
    result=deepcopy(draft)
    result['answers']=[new['A:'+a['question_id']] for a in draft['answers']]
    result['hypotheses']=[new['H:'+str(i)] for i in range(1,len(draft['hypotheses'])+1)]
    if 'limitations' in patch:result['limitations']=deepcopy(patch['limitations'])
    delta=[{'target_id':tid,'before':deepcopy(old[tid]),'after':deepcopy(new[tid])} for tid in ids]
    return result,delta

def resolution_evidence(patch, plan, draft):
    """标明依据来自模型回执还是已校验的替换回答，不改写原模型补丁。"""
    targets=target_map(draft);by_id={p['issue_id']:p['target_id'] for p in plan}
    receipts=deepcopy(patch['resolutions'])
    for receipt in receipts:
        fallback=receipt['resolution']=='changed' and receipt['refs']==[]
        if fallback:receipt['refs']=deepcopy(targets[by_id[receipt['issue_id']]]['refs'])
        receipt['evidence_origin']='validated_replacement_refs' if fallback else 'model_resolution_refs'
    return receipts

def check_repair_review(review, plan, case):
    try:
        checks=unique_rows(review.get('repair_checks'),'issue_id',[p['issue_id'] for p in plan])
        for problem in plan:
            c=checks[problem['issue_id']]
            if type(c.get('resolved')) is not bool or not isinstance(c.get('reason'),str) or not c['reason'].strip() or not grounded(c.get('refs'),case):
                return ['repair_recheck_evidence']
            if not c['resolved'] and (review['verdict']=='pass' or not any(i.get('target_id')==problem['target_id'] for i in review['issues'])):
                return ['repair_recheck_unresolved']
        return []
    except (ValueError,TypeError,KeyError,AttributeError):return ['repair_recheck_coverage']
