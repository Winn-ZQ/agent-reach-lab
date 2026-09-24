"""网页与导出共享结构化结果；不发请求，不将回放标成真实研究。"""
import csv
import html
import io
from urllib.parse import urlparse

REASONS = {
    'review_passed': '模拟检查与复核通过', 'invalid_review': '复核结果不符合要求',
    'budget_exhausted': '剩余名额不足以完成修正和复核', 'revision_limit': '已达到一次修正上限',
    'needs_sources': '需要补充来源', 'no_change': '修正稿没有变化',
    'review_invalid_response': '复核返回的内容无法解析', 'invalid_response': '分析返回的内容无法解析',
    'interrupted': '服务中断，未自动继续', 'worker_failed': '运行失败，已保留本机记录',
}


def review_label(task, state):
    if task.get('status') == 'interrupted':
        return '中断 · 未通过复核'
    if task.get('status') == 'running':
        return '处理中 · 尚未完成'
    return '模拟复核通过' if state.get('review_status') == 'passed' else '未通过复核的草稿'


def csv_safe(value):
    text = str(value or '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r')) else text


def rows(detail):
    task, state, case = detail['task'], detail['state'], detail['case']
    draft = state.get('final_draft') or {}
    common = {'task_id':task['id'], 'mode':'offline_simulation', 'review_status':review_label(task,state),
              'stop_reason':REASONS.get(state.get('stop_reason'),state.get('stop_reason') or '尚未完成'),
              'disclosure':'离线回放；来源为虚构案例，不是实时搜索或新的模型判断。'}
    result = []
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
                       'text':s.get('title',''),'source_ids':s['source_id'],'quotes':s.get('quote') or '',
                       'author':s.get('author') or '', 'published_at':s.get('published_at') or '',
                       'exclusion_reason':s.get('exclusion_reason') or ''})
    return result


def export_csv(detail):
    fields = ['task_id','mode','review_status','stop_reason','disclosure','record_type','id','status',
              'text','source_ids','quotes','alternative','next_action','author','published_at','exclusion_reason']
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
           '> 离线回放：资料为虚构案例，没有进行实时搜索或新的模型调用。','',
           '**状态：'+review_label(task,state)+'**','',
           '停止原因：'+safe(REASONS.get(state.get('stop_reason'),state.get('stop_reason') or '尚未完成')),'',
           '响应来源：'+safe(state.get('response_provenance','预设回放')),'',
           '## 样本范围','']
    facts=case['sampling_facts']
    lines += [f"纳入 {facts['included_notes']} 条笔记，来自 {facts['known_unique_authors']} 名已知作者；作者未知笔记 {facts['unknown_author_notes']} 条。",'',
              '同作者分组：'+safe('；'.join('、'.join(g) for g in facts['same_author_groups']) or '无'),'',
              '笔记数不等于用户数；样本不能推断总体发生率。','']
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
