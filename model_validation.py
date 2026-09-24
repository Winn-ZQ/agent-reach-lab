"""纯本地结构与原文检查；无网络、凭据或模型调用。"""

def validate(data, case, stage):
    """检查格式、引用ID与原文匹配；不把这些检查当作语义正确。"""
    issues = []
    sources = {s['source_id']: s for s in case['sources']}
    if not isinstance(data, dict):
        return ['not_object']
    def text(value):
        return isinstance(value, str) and bool(value.strip())
    def refs(values):
        return isinstance(values, list) and all(isinstance(v, str) and v in sources for v in values)
    if not isinstance(data.get('limitations'), list) or not all(text(v) for v in data['limitations']):
        issues.append('limitations_schema')
    if stage == 'analysis':
        answers, hypotheses = data.get('answers'), data.get('hypotheses')
        if not isinstance(answers, list) or not isinstance(hypotheses, list):
            return issues + ['analysis_schema']
        seen = []
        for a in answers:
            if not isinstance(a, dict) or a.get('question_id') not in case['questions'] or a.get('status') not in ('answered', 'unknown') or not text(a.get('text')) or not isinstance(a.get('refs'), list):
                issues.append('answer_schema')
                continue
            seen.append(a['question_id'])
            for r in a['refs']:
                if not isinstance(r, dict) or r.get('source_id') not in sources or not text(r.get('quote')) or not text(r.get('locator')):
                    issues.append('reference_schema')
                    continue
                source = sources[r['source_id']]
                original = source.get('content') or source.get('quote') or ''
                if ''.join(r['quote'].split()) not in ''.join(original.split()):
                    issues.append('quote_not_literal:' + a['question_id'] + ':' + r['source_id'])
        if len(seen) != len(set(seen)):
            issues.append('duplicate_question')
        if set(seen) != set(case['questions']):
            issues.append('omitted_question')
        for h in hypotheses:
            if not isinstance(h, dict) or not all(text(h.get(k)) for k in ('text', 'alternative', 'next_action')) or not refs(h.get('source_ids')):
                issues.append('hypothesis_schema')
    else:
        checks, problems = data.get('checks'), data.get('issues')
        if data.get('verdict') not in ('pass', 'revise') or not isinstance(checks, list) or not checks or not isinstance(problems, list):
            return issues + ['review_schema']
        for c in checks:
            if not isinstance(c, dict) or type(c.get('supported')) is not bool or not text(c.get('claim')) or not text(c.get('reason')) or not refs(c.get('source_ids')):
                issues.append('check_schema')
        for p in problems:
            if not isinstance(p, dict) or p.get('severity') not in ('blocking', 'minor') or p.get('action') not in ('rewrite', 'mark_unknown', 'fetch') or not text(p.get('description')):
                issues.append('issue_schema')
        if data['verdict'] == 'pass' and (any(isinstance(p, dict) and p.get('severity') == 'blocking' for p in problems)
                                         or any(isinstance(c, dict) and c.get('supported') is False for c in checks)):
            issues.append('contradictory_pass')
        if data['verdict'] == 'revise' and not problems:
            issues.append('revise_without_issue')
    return issues

