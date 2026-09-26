"""网页与导出共享结构化结果；不发请求，不将回放标成真实研究。"""
import csv
import html
import io
from urllib.parse import urlparse

REASONS = {
    'repair_target_unchanged':'修正模型声称已处理问题，但对应正文未改变',
    'repair_target_missing':'旧复核缺少具体目标，需要重新核查',
    'repair_issue_coverage':'修正未逐条回应复核问题',
    'repair_resolution_evidence':'修正说明缺少有效原文依据',
    'repair_patch_schema':'修正补丁格式无效',
    'repair_contract_schema':'逐项修正记录格式无效',
    'repair_patch_targets':'修正目标重复或不存在',
    'repair_patch_identity':'修正擅自改变了问题编号',
    'repair_patch_text':'修正正文缺失',
    'transport_or_usage_error': '模型请求未返回可用响应或用量',
    'review_transport_or_usage_error': '最终复核请求未返回可用响应，修正稿尚未通过复核',
    'format_revision_limit': '草稿校验仍未通过，已达到本地修正上限',
    'review_passed': '检查与复核通过', 'invalid_review': '复核结果不符合要求',
    'budget_exhausted': '剩余名额不足以完成修正和复核', 'revision_limit': '已达到一次修正上限',
    'needs_sources': '需要补充来源', 'no_change': '修正稿没有变化',
    'review_invalid_response': '复核返回的内容无法解析', 'invalid_response': '分析返回的内容无法解析',
    'interrupted': '服务中断，未自动继续', 'worker_failed': '运行失败，已保留本机记录',
}


def disclosure(detail):
    if detail['task'].get('mode')=='live_api':
        return '真实网页研究任务；是否已调用模型以调用记录为准。'+detail['case'].get('data_provenance','尚未取得有效证据。')
    return '离线回放：资料为虚构案例，没有进行实时搜索或新的模型调用。'


def review_label(task, state):
    if task.get('status') == 'interrupted':
        return '中断 · 未通过复核'
    if task.get('status') in ('cancelled','cancelling'):
        return '已取消 · 未完成复核'
    if task.get('status') == 'running':
        return '处理中 · 尚未完成'
    if not state.get('final_draft'):
        return '尚无有效报告 · 尚未完成复核'
    return ('模型复核通过（非事实正确保证）' if task.get('mode')=='live_api' else '模拟复核通过') if state.get('review_status') == 'passed' else '未通过复核的草稿'


def csv_safe(value):
    text = str(value or '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r')) else text


def rows(detail):
    task, state, case = detail['task'], detail['state'], detail['case']
    draft = state.get('final_draft') or {}
    common = {'task_id':task['id'], 'mode':task.get('mode','offline_simulation'), 'review_status':review_label(task,state),
              'stop_reason':REASONS.get(state.get('stop_reason'),state.get('stop_reason') or '尚未完成'),
              'disclosure':disclosure(detail),'question':case['task'],
              'missing_sources':' | '.join(task.get('skipped_sources',[])+task.get('missing_sources',[]))}
    result = [{**common, 'record_type':'task','id':task['id'],'status':task['status'],
               'text':case['task']}]
    for a in draft.get('answers', []):
        refs = a.get('refs', [])
        result.append({**common,'record_type':'answer','id':a['question_id'],'status':a['status'],
                       'text':a['text'],'source_ids':' | '.join(r['source_id'] for r in refs),
                       'quotes':' | '.join(r.get('quote') or '' for r in refs)})
    for i,h in enumerate(draft.get('hypotheses', []),1):
        result.append({**common,'record_type':'hypothesis','id':f'H{i}','status':'待验证',
                       'text':h['text'],'source_ids':' | '.join(h['source_ids']),
                       'alternative':h['alternative'],'next_action':h['next_action']})
    for s in case['sources']:
        result.append({**common,'record_type':'source','id':s['source_id'],
                       'status':'纳入' if s.get('included') is True else '排除' if s.get('included') is False else '参考来源',
                       'url':s.get('url',''),'fetched_at':s.get('fetched_at',''),'text':s.get('title',''),'source_ids':s['source_id'],'quotes':s.get('quote') or '',
                       'author':s.get('author') or '', 'published_at':s.get('published_at') or '',
                       'exclusion_reason':s.get('exclusion_reason') or ''})
    return result


def export_csv(detail):
    fields = ['task_id','mode','review_status','stop_reason','disclosure','record_type','id','status',
              'text','source_ids','quotes','alternative','next_action','author','published_at','exclusion_reason','url','fetched_at','question','missing_sources']
    buffer=io.StringIO(newline='')
    writer=csv.DictWriter(buffer,fieldnames=fields)
    writer.writeheader()
    for row in rows(detail):
        writer.writerow({k:csv_safe(row.get(k,'')) for k in fields})
    return '\ufeff'+buffer.getvalue()


def export_markdown(detail):
    task,state,case=detail['task'],detail['state'],detail['case']
    safe=lambda value: html.escape(str(value or ''),quote=False)
    draft=state.get('final_draft') or {}
    lines=['# '+safe(case['task']),'',
           '> '+safe(disclosure(detail)),'',
           '**状态：'+review_label(task,state)+'**','',
           '停止原因：'+safe(REASONS.get(state.get('stop_reason'),state.get('stop_reason') or '尚未完成')),'',
           '响应来源：'+safe(state.get('response_provenance','预设回放')),'',
           '## 样本范围','']
    facts=case.get('sampling_facts',dict(included_notes=0,known_unique_authors=0,unknown_author_notes=0,same_author_groups=[]))
    lines += [f"纳入 {facts['included_notes']} 条笔记，来自 {facts['known_unique_authors']} 名已知作者；作者未知笔记 {facts['unknown_author_notes']} 条。",'',
              '同作者分组：'+safe('；'.join('、'.join(g) for g in facts['same_author_groups']) or '无'),'',
              '笔记数不等于用户数；样本不能推断总体发生率。','']
    if task.get('mode')=='live_api':
        start=lines.index('## 样本范围')
        lines=lines[:start]+['## 研究范围','', '公开网页来源：'+str(len(case['sources']))+'；地区：'+safe(task['input']['region'] or '不限')+'；时间：'+safe(task['input']['period'] or '不限'),'', 'API调用：'+str(state.get('api_calls',0))+'；已知用量：'+safe(state.get('known_tokens_by_model',{})), '', '未取得来源：'+safe('、'.join(task.get('skipped_sources',[])+task.get('missing_sources',[])) or '无额外平台缺失记录'), '']
    for answer in draft.get('answers',[]):
        lines += ['## '+safe(answer['question_id'])+' · '+('明确未知' if answer['status']=='unknown' else '回答'),'',safe(answer['text']),'']
        for ref in answer.get('refs',[]):
            lines += ['- ['+safe(ref['source_id'])+'] '+safe(ref.get('quote'))+'（'+safe(ref.get('locator'))+'）']
        lines.append('')
    if draft.get('hypotheses'):
        lines += ['## 待验证的改进假设','']
        for i,h in enumerate(draft['hypotheses'],1):
            lines += [f'### H{i}', '',safe(h['text']),'','替代解释：'+safe(h['alternative']),'',
                      '下一步：'+safe(h['next_action']),'','依据：'+safe('、'.join(h['source_ids'])),'']
    lines += ['## 限制','']
    lines += ['- '+safe(t) for t in draft.get('limitations',[])]
    lines += ['', '## 来源清单', '']
    for s in case['sources']:
        line='- '+safe(s['source_id'])+' · '+safe(s.get('title',''))
        if s.get('included') is False:
            line+='（排除：'+safe(s.get('exclusion_reason'))+'）'
        if s.get('url') and urlparse(s['url']).scheme in ('https','http'):
            line+=' '+safe(s['url'])
        lines.append(line)
    return '\n'.join(lines)+'\n'
