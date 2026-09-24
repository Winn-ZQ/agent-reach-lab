"""可审计的助手驱动循环。模型由宿主助手调用，本程序只管理状态与上限。"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def record(state, action):
    state['events'].append({'at': datetime.now(timezone.utc).isoformat(),
                            'action': action, 'revision': state['revision']})


def initialize(directory, draft, evidence, max_revisions=2):
    directory = Path(directory)
    if directory.exists():
        raise ValueError('运行目录已存在，请使用新目录，避免覆盖历史')
    if not 0 <= max_revisions <= 5:
        raise ValueError('修正上限必须为 0–5')
    content = Path(draft).read_text(encoding='utf-8')
    source = read_json(evidence)
    if source.get('error') or not source.get('content', '').strip():
        raise ValueError('证据获取失败或为空')
    if source.get('content_sha256') != digest(source['content']):
        raise ValueError('证据哈希不匹配')
    directory.mkdir(parents=True)
    (directory / 'draft-0.md').write_text(content, encoding='utf-8')
    save(directory / 'evidence-0.json', source)
    state = {'status': 'awaiting_review', 'revision': 0,
             'max_revisions': max_revisions, 'draft_sha256': digest(content),
             'evidence_sha256': digest(source['content']), 'events': []}
    record(state, 'initialized')
    save(directory / 'state.json', state)
    return state


def accept_review(directory, review_path):
    directory = Path(directory)
    state = read_json(directory / 'state.json')
    if state['status'] != 'awaiting_review':
        raise ValueError('当前不接受复核')
    current_draft = (directory / f"draft-{state['revision']}.md").read_text(encoding='utf-8')
    current_evidence = read_json(directory / f"evidence-{state['revision']}.json")
    if (digest(current_draft) != state['draft_sha256']
            or digest(current_evidence['content']) != state['evidence_sha256']):
        raise ValueError('存档被修改，请创建新的运行')
    review = read_json(review_path)
    for field in ('draft_sha256', 'evidence_sha256'):
        if review.get(field) != state[field]:
            raise ValueError('复核不属于当前稿件或证据：' + field)
    if review.get('verdict') not in ('pass', 'revise'):
        raise ValueError('复核结论必须为 pass 或 revise')
    checks = review.get('checks')
    issues = review.get('issues')
    if not isinstance(checks, list) or not checks or not isinstance(issues, list):
        raise ValueError('复核必须包含非空 checks 和 issues 列表')
    for check in checks:
        if (not isinstance(check, dict) or type(check.get('supported')) is not bool
                or not check.get('claim') or not check.get('evidence_location')):
            raise ValueError('每项检查须含结论、布尔判断及证据位置')
    for issue in issues:
        if (not isinstance(issue, dict) or not issue.get('description')
                or issue.get('action') not in ('rewrite', 'fetch', 'mark_unknown')):
            raise ValueError('问题须含描述与合法修正动作')
    if review['verdict'] == 'pass' and (issues or not all(c['supported'] for c in checks)):
        raise ValueError('存在问题或不受支持的结论，不能通过')
    if review['verdict'] == 'revise' and not issues:
        raise ValueError('要求修正必须提供具体问题')
    revision = state['revision']
    save(directory / f'review-{revision}.json', review)
    if review['verdict'] == 'pass':
        state['status'] = 'passed'
    elif revision >= state['max_revisions']:
        state['status'] = 'needs_human'
    else:
        state['status'] = 'needs_revision'
    record(state, 'review_' + review['verdict'])
    save(directory / 'state.json', state)
    return state


def revise(directory, draft, action, evidence=None):
    directory = Path(directory)
    state = read_json(directory / 'state.json')
    if state['status'] != 'needs_revision':
        raise ValueError('当前不可修正，可能已通过或达到上限')
    if not action.strip():
        raise ValueError('必须记录采取的修正动作')
    content = Path(draft).read_text(encoding='utf-8')
    source = read_json(evidence or directory / f"evidence-{state['revision']}.json")
    if (source.get('error') or not source.get('content', '').strip()
            or source.get('content_sha256') != digest(source['content'])):
        raise ValueError('新证据为空、失败或哈希不匹配')
    if digest(content) == state['draft_sha256'] and digest(source['content']) == state['evidence_sha256']:
        raise ValueError('稿件和证据均未变化，不消耗修正次数')
    state['revision'] += 1
    n = state['revision']
    (directory / f'draft-{n}.md').write_text(content, encoding='utf-8')
    save(directory / f'evidence-{n}.json', source)
    state.update(status='awaiting_review', draft_sha256=digest(content),
                 evidence_sha256=digest(source['content']))
    record(state, action)
    save(directory / 'state.json', state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    init.add_argument('directory')
    init.add_argument('--draft', required=True)
    init.add_argument('--evidence', required=True)
    init.add_argument('--max-revisions', type=int, default=2)
    review = sub.add_parser('review')
    review.add_argument('directory')
    review.add_argument('--file', required=True)
    revision = sub.add_parser('revise')
    revision.add_argument('directory')
    revision.add_argument('--draft', required=True)
    revision.add_argument('--action', required=True)
    revision.add_argument('--evidence')
    status = sub.add_parser('status')
    status.add_argument('directory')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            state = initialize(args.directory, args.draft, args.evidence, args.max_revisions)
        elif args.command == 'review':
            state = accept_review(args.directory, args.file)
        elif args.command == 'revise':
            state = revise(args.directory, args.draft, args.action, args.evidence)
        else:
            state = read_json(Path(args.directory) / 'state.json')
        print(json.dumps(state, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f'失败：{exc}\n')


if __name__ == '__main__':
    main()
