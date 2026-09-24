"""把多份采集记录合为一份带来源编号的证据包，不调用模型。"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from loop import digest, read_json, save


def bundle(paths):
    sources = []
    for i, path in enumerate(paths, 1):
        source = read_json(path)
        content = source.get('content')
        url = source.get('url')
        if (source.get('error') or not isinstance(content, str) or not content.strip()
                or source.get('content_sha256') != digest(content)
                or not isinstance(url, str) or urlsplit(url).scheme not in ('http', 'https')
                or not urlsplit(url).hostname):
            raise ValueError(f'来源获取失败、缺少网址或哈希不符：{path}')
        sources.append({**source, 'source_id': f'S{i}'})
    if not sources:
        raise ValueError('至少需要一份证据')
    # 将 URL、日期与正文一起纳入 content 哈希，供循环控制器绑定版本。
    content = json.dumps(sources, ensure_ascii=False, indent=2)
    return {'source': 'evidence_bundle', 'created_at': datetime.now(timezone.utc).isoformat(),
            'status': 'fetched_unverified', 'error': None,
            'content': content, 'content_sha256': digest(content)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources', nargs='+')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if Path(args.output).exists():
        parser.error('输出已存在，请使用新路径保留旧证据')
    try:
        result = bundle(args.sources)
        save(args.output, result)
        print(f'已保存 {len(args.sources)} 个来源到 {args.output}')
    except (ValueError, OSError) as exc:
        parser.exit(1, f'失败：{exc}\n')
