"""公开网页获取适配层：Exa 搜索候选，Agent-Reach 读取页面。

本模块不调用模型、不登录站点，也不把用户输入拼进 shell 命令。搜索和读取是
两个独立步骤，失败会保留可审计的状态，供后续分析/复核决定是否采信。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from collect_page import collect


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "mcporter.json"


def node_path():
    """优先显式配置与 PATH；兼容本机 Codex 运行时，不依赖具体用户名。"""
    explicit = os.environ.get("AGENT_REACH_NODE")
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which("node")
    if found:
        return Path(found)
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    return bundled if bundled.is_file() and os.access(bundled, os.X_OK) else None


class SearchError(RuntimeError):
    """搜索没有产出可供读取的候选。"""

    def __init__(self, kind: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_url(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 4096:
        return False
    parsed = urlsplit(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.username


def _validate_text(name: str, value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是文本")
    value = value.strip()
    if not value or len(value) > maximum:
        raise ValueError(f"{name} 长度不符合要求")
    return value


def _parse_text_results(text: str, max_results: int) -> list[dict[str, Any]]:
    blocks = re.split(r"(?m)(?=^Title:\s*)", text.strip())
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in blocks:
        block = block.strip()
        if not block or not re.search(r"(?m)^URL:\s*", block):
            continue
        def field(label: str) -> str | None:
            match = re.search(rf"(?m)^{re.escape(label)}:\s*(.*)$", block)
            return match.group(1).strip() if match else None

        url = field("URL")
        if not _valid_url(url) or url in seen:
            continue
        seen.add(url)
        highlights = ""
        match = re.search(r"(?ms)^Highlights:\s*\n?(.*)$", block)
        if match:
            highlights = match.group(1).strip()
        results.append({
            "rank": len(results) + 1,
            "title": field("Title") or "",
            "url": url,
            "published": field("Published"),
            "author": field("Author"),
            "highlights": highlights,
        })
        if len(results) >= max_results:
            break
    return results


def parse_search_response(payload: Any, max_results: int = 3) -> list[dict[str, Any]]:
    """解析 mcporter JSON 输出，返回去重且仅含 HTTP(S) URL 的候选。"""
    if not isinstance(max_results, int) or not 1 <= max_results <= 5:
        raise ValueError("max_results 须为 1–5")
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise SearchError("invalid_response", "搜索响应结构无效")
    texts = [item.get("text", "") for item in payload["content"]
             if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)]
    results = _parse_text_results("\n\n".join(texts), max_results)
    if not results:
        raise SearchError("no_results", "搜索没有返回可读取的公开网页")
    return results


def _command(root: Path = ROOT) -> tuple[list[str], Path]:
    mcporter = Path(os.environ.get("AGENT_REACH_MCPORTER", str(root / ".tools/node_modules/.bin/mcporter")))
    config = Path(os.environ.get("AGENT_REACH_MCPORTER_CONFIG", str(root / "config/mcporter.json")))
    if not mcporter.exists():
        raise SearchError("not_configured", "mcporter 未安装或路径不存在")
    return [str(mcporter)], config


def search_public_web(
    query: str,
    objective: str,
    *,
    max_results: int = 3,
    timeout_ms: int = 45000,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    """通过固定参数调用 Exa；runner 仅用于离线测试。"""
    query = _validate_text("query", query, 600)
    objective = _validate_text("objective", objective, 2000)
    if not 1 <= max_results <= 5 or not 1000 <= timeout_ms <= 60000:
        raise ValueError("搜索数量或超时不符合范围")
    command, config = _command(root)
    args = command + ["--config", str(config), "call", "exa.web_search_exa",
                      "--args", json.dumps({"query": query, "objective": objective,
                                             "numResults": max_results}, ensure_ascii=False),
                      "--timeout", str(timeout_ms), "--output", "json"]
    if runner is None:
        if node_path() is None:
            raise SearchError("not_configured", "Node 未安装；请配置 PATH 或 AGENT_REACH_NODE")
        runner = subprocess.run
    env = os.environ.copy()
    node = node_path()
    node_parent = str(node.parent) if node else None
    if node_parent and node_parent not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = node_parent + os.pathsep + env.get("PATH", "")
    try:
        completed = runner(args, capture_output=True, text=True, timeout=timeout_ms / 1000,
                           cwd=str(root), env=env, shell=False, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SearchError("timeout", "搜索超时，未读取任何网页", retryable=True) from exc
    if completed.returncode != 0:
        # 不把 stderr（可能包含配置路径或令牌）返回给页面或报告。
        raise SearchError("search_failed", "搜索命令失败，请检查 Exa/mcporter 配置")
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SearchError("invalid_response", "搜索返回不是有效 JSON") from exc
    results = parse_search_response(payload, max_results)
    return {"status": "searched", "backend": "Exa via mcporter", "query": query,
            "objective": objective, "searched_at": _now(), "results": results}


def collect_candidates(
    search: dict[str, Any],
    output_dir: Path,
    *,
    max_retries: int = 1,
    collector: Callable[..., dict[str, Any]] = collect,
) -> dict[str, Any]:
    """按搜索排序读取候选，保存每条记录；不因单条失败而伪造成功。"""
    if not isinstance(search, dict) or search.get("status") != "searched":
        raise ValueError("只能读取已完成的搜索结果")
    results = search.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("搜索结果为空")
    if not 0 <= max_retries <= 2:
        raise ValueError("重试次数须为 0–2")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    for index, candidate in enumerate(results, start=1):
        if not isinstance(candidate, dict) or not _valid_url(candidate.get("url")):
            continue
        record = collector(candidate["url"], max_retries=max_retries)
        record["search_rank"] = index
        record["search_title"] = candidate.get("title", "")
        record["search_highlights"] = candidate.get("highlights", "")
        path = output_dir / f"source-{index}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        records.append({key: record.get(key) for key in
                        ("url", "status", "error", "fetched_at", "content_sha256", "search_rank")})
    if not records:
        raise ValueError("没有可读取的候选 URL")
    manifest = {"status": "collected", "search": {key: search.get(key) for key in
                ("backend", "query", "objective", "searched_at")}, "records": records,
                "created_at": _now(), "max_retries": max_retries}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                encoding="utf-8")
    return manifest


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--objective", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-results", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--max-retries", type=int, choices=range(3), default=1)
    args = parser.parse_args()
    search = search_public_web(args.query, args.objective, max_results=args.max_results)
    manifest = collect_candidates(search, Path(args.output), max_retries=args.max_retries)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
