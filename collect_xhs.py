"""小红书只读采集入口；复用 Agent-Reach 推荐的 MCP 后端，不调用模型。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from configure_model import private_directory, save_new
from research_acquisition import node_path
from research_evidence import MAX_CASE_BYTES, question_checklist
from research_flow import digest, encoded, prepare_case

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / '.local/xhs-service/mcporter.json'
TOOLS = {'check_login_status', 'search_feeds', 'get_feed_detail', 'get_login_qrcode'}


class XhsError(Exception):
    """固定错误分类；上游异常原文不进入页面。"""


def now():
    return datetime.now(timezone.utc).isoformat()


def call_tool(name, args, *, runner=None, config=CONFIG):
    if name not in TOOLS:
        raise XhsError('operation_not_allowed')
    if not Path(config).is_file():
        raise XhsError('backend_not_configured')
    node = node_path()
    if node is None:
        raise XhsError('node_not_configured')
    env = os.environ.copy()
    env['PATH'] = str(node.parent) + os.pathsep + env.get('PATH', '')
    command = [str(ROOT / '.tools/node_modules/.bin/mcporter'), '--config', str(config),
               'call', 'xiaohongshu.' + name, '--args', json.dumps(args, ensure_ascii=False),
               '--timeout', '120000', '--output', 'json']
    try:
        response = (runner or subprocess.run)(command, capture_output=True, text=True,
                                             timeout=125, env=env, cwd=ROOT, check=False)
    except subprocess.TimeoutExpired:
        raise XhsError('source_timeout') from None
    except OSError:
        raise XhsError('backend_unavailable') from None
    if response.returncode:
        raise XhsError('backend_unavailable')
    try:
        payload = json.loads(response.stdout)
    except (ValueError, TypeError):
        raise XhsError('invalid_tool_response') from None
    if not isinstance(payload, dict) or payload.get('isError'):
        raise XhsError('source_request_failed')
    return payload


def content_text(payload):
    blocks = payload.get('content')
    if not isinstance(blocks, list):
        raise XhsError('invalid_tool_response')
    return '\n'.join(b['text'] for b in blocks if isinstance(b, dict)
                     and b.get('type') == 'text' and isinstance(b.get('text'), str))


def tool_data(payload):
    # mcporter可能直接返回已解析的JSON，也可能保留MCP content包装。
    if 'content' not in payload:
        return payload
    try:
        data = json.loads(content_text(payload))
    except ValueError:
        raise XhsError('invalid_data_response') from None
    if not isinstance(data, dict):
        raise XhsError('invalid_data_response')
    return data


def login_status(client=call_tool):
    payload = client('check_login_status', {})
    text = content_text(payload)
    if '未登录' in text:
        return False
    if '已登录' in text:
        return True
    raise XhsError('login_status_unknown')


def plan(topic):
    if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 80:
        raise ValueError('topic must contain 1–80 characters')
    return {'topic': topic.strip(), 'queries': [topic.strip() + ' 使用体验', topic.strip() + ' 使用问题'],
            'filters': {'sort_by': '综合', 'publish_time': '不限', 'note_type': '不限',
                        'search_scope': '不限', 'location': '不限'},
            'max_notes': 4, 'max_candidates_per_query': 10,
            'selection': '两组查询按返回排名交替选取；同笔记ID只读一次；不按情绪筛除。',
            'author_rule': '保留同作者多篇，但独立作者分开统计；未知作者不合并。',
            'date_rule': '无日期仍可纳入但标未知，不推定为近期；日期为平台返回字段，未独立核实。',
            'scope': '仅笔记标题和正文；图片、视频、评论不纳入分析。',
            'bias': '便利样本；问题查询有负面倾向，两组混合也不代表总体口碑。'}


def candidates(payload, query):
    feeds = tool_data(payload).get('feeds')
    if not isinstance(feeds, list):
        raise XhsError('invalid_search_response')
    result = []
    for rank, feed in enumerate(feeds[:10], 1):
        if not isinstance(feed, dict) or feed.get('modelType') != 'note':
            continue
        note_id, token = feed.get('id'), feed.get('xsecToken')
        if not isinstance(note_id, str) or not re.fullmatch('[a-fA-F0-9]{24}', note_id):
            continue
        if not isinstance(token, str) or not token or len(token) > 2048:
            continue
        result.append({'note_id': note_id, 'access_token': token, 'query': query, 'rank': rank})
    return result


def normalize_note(payload, candidate, sid, fetched_at):
    data = tool_data(payload)
    note = data.get('data', {}).get('note') if isinstance(data.get('data'), dict) else None
    if not isinstance(note, dict) or note.get('noteId') != candidate['note_id']:
        raise XhsError('note_identity_mismatch')
    title, body = note.get('title', ''), note.get('desc', '')
    if not isinstance(title, str) or not isinstance(body, str) or not body.strip():
        raise XhsError('note_text_unavailable')
    content = title + '\n\n' + body
    if len(content.encode()) > 40000:
        raise XhsError('note_too_large')
    user = note.get('user') if isinstance(note.get('user'), dict) else {}
    author = user.get('userId')
    published = None
    timestamp = note.get('time')
    if type(timestamp) is int and timestamp > 0:
        try:
            published = datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    return {'source_id': sid, 'title': title, 'platform': '小红书', 'record_type': 'user_feedback',
            'url': 'https://www.xiaohongshu.com/explore/' + candidate['note_id'],
            'note_id': candidate['note_id'], 'content': content,
            'content_sha256': hashlib.sha256(content.encode()).hexdigest(),
            'author': hashlib.sha256(author.encode()).hexdigest() if isinstance(author, str) and author else None,
            'published_at': published, 'published_at_basis': 'platform_metadata_unverified',
            'fetched_at': fetched_at, 'included': True, 'status': 'fetched_unverified',
            'locator': sid + ' 保存的笔记标题与正文', 'query': candidate['query'], 'search_rank': candidate['rank']}


def collect(topic, output, client=call_tool):
    sampling = plan(topic)
    output = Path(output)
    if output.exists():
        raise ValueError('output exists; preserve the original collection')
    private_directory(output)
    save_new(output / 'sampling.json', sampling)
    if not login_status(client):
        save_new(output / 'status.json', {'status': 'needs_login', 'api_calls': 0})
        raise XhsError('login_required')
    groups = []
    sampling['search_records'] = []
    for i, query in enumerate(sampling['queries'], 1):
        payload = client('search_feeds', {'keyword': query, 'filters': sampling['filters']})
        save_new(output / f'search-{i}.json', payload)
        sampling['search_records'].append({'query': query, 'returned_at': now(),
            'requested_filters': sampling['filters'], 'response_sha256': digest(payload),
            'filter_verification': '已向后端传参；未独立核验平台实际排序和筛选'})
        groups.append(candidates(payload, query))
    selected, seen = [], set()
    for i in range(max(map(len, groups), default=0)):
        for group in groups:
            if i < len(group) and group[i]['note_id'] not in seen:
                selected.append(group[i]); seen.add(group[i]['note_id'])
    selected = selected[:sampling['max_notes']]
    # 公开/模型输入不带访问令牌；原始搜索响应仅保存于本机私有目录。
    sampling['selected'] = [{k: v for k, v in c.items() if k != 'access_token'} for c in selected]
    sampling['candidate_occurrences'] = sum(map(len, groups))
    sampling['failures'] = []
    sources = []
    for i, candidate in enumerate(selected, 1):
        try:
            payload = client('get_feed_detail', {'feed_id': candidate['note_id'],
                             'xsec_token': candidate['access_token'], 'load_all_comments': False})
            save_new(output / f'note-raw-{i}.json', payload)
            source = normalize_note(payload, candidate, f'S{i}', now())
            save_new(output / f'source-{i}.json', source)
            sources.append(source)
        except XhsError as exc:
            sampling['failures'].append({'note_id': candidate['note_id'], 'reason': str(exc)})
            # 身份/结构错误仅排除此条；接口失败可能是登录失效或风控，停止后续读取。
            if str(exc) in ('source_request_failed', 'source_timeout', 'backend_unavailable'):
                break
    save_new(output / 'sampling-result.json', sampling)
    if not sources:
        raise XhsError('no_valid_notes')
    question = f'根据本次小红书笔记样本，整理{topic}的体验反馈、证据局限和待验证的产品改进假设。仅描述本次样本，不推断市场占比。'
    raw = {'kind': 'collected_xhs', 'task': question, 'questions': question_checklist(question),
           'sources': sources, 'sampling': sampling}
    run_date = datetime.now(timezone.utc).date().isoformat()
    case = prepare_case(raw, run_date)
    if len(encoded(case).encode()) > MAX_CASE_BYTES:
        raise XhsError('evidence_too_large')
    package = {'schema_version': 'xhs-evidence/0.1', 'run_date': run_date, 'case': raw,
               'case_sha256': digest(case), 'api_calls': 0, 'review_status': 'not_run'}
    save_new(output / 'analysis-input.json', package)
    save_new(output / 'status.json', {'status': 'collected_unverified', 'notes': len(sources), 'api_calls': 0})
    return package


def verify_package(directory):
    """后续分析前检查保存的正文、哈希和取样记录；不重复采集。"""
    directory = Path(directory)
    def read(name):
        path = directory / name
        if directory.is_symlink() or path.is_symlink():
            raise ValueError('linked evidence')
        return json.loads(path.read_text())
    package = read('analysis-input.json')
    if package.get('schema_version') != 'xhs-evidence/0.1':
        raise ValueError('unknown xhs evidence version')
    raw = package['case']
    if raw.get('kind') != 'collected_xhs' or raw['sampling'] != read('sampling-result.json'):
        raise ValueError('sampling changed')
    for source in raw['sources']:
        sid = source.get('source_id', '')
        if not re.fullmatch(r'S[1-4]', sid) or source != read(f'source-{sid[1:]}.json'):
            raise ValueError('source changed')
    case = prepare_case(raw, package['run_date'])
    if digest(case) != package['case_sha256']:
        raise ValueError('evidence changed')
    return case


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--topic', default='Keep App')
    parser.add_argument('--output')
    args = parser.parse_args()
    try:
        if args.check:
            print(json.dumps({'logged_in': login_status(), 'model_calls': 0}))
        elif args.output:
            result = collect(args.topic, args.output)
            print(json.dumps({'status': 'collected_unverified', 'notes': len(result['case']['sources']), 'model_calls': 0}))
        else:
            print(json.dumps(plan(args.topic), ensure_ascii=False, indent=2))
    except XhsError as exc:
        print(json.dumps({'status': 'stopped', 'reason': str(exc), 'model_calls': 0})); raise SystemExit(2)
