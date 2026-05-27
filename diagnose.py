#!/usr/bin/env python3
"""PaddlePaddle 社区开发者统计 — 全量诊断工具

使用 repository.pullRequests(states: MERGED) 逐仓库查询，
绕过 Search API 的 1000 条上限，建立可靠的 ground truth。

用法:
    python3 diagnose.py                          # 全量诊断
    python3 diagnose.py --before 2026-04-02      # 仅统计该日期前合并的 PR
    python3 diagnose.py --repo Paddle --repo docs # 仅诊断指定仓库

依赖: pip install requests pyyaml
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import requests

# ─── 路径 ────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── 全部 15 个仓库 ──────────────────────────────────────────

ALL_REPOS = [
    "Paddle", "docs", "PaddleScience", "PaddleMIX", "Paddle2ONNX",
    "PaddleOCR", "PaddleSpeech", "PaddleNLP", "FastDeploy",
    "GraphNet", "PaddleCustomDevice", "PaddleFormers", "PaddleX",
    "PaddleSOT", "PaConvert",
]

ORG = "PaddlePaddle"


# ─── 代理配置 ────────────────────────────────────────────────

def _get_proxies() -> dict:
    """从环境变量或 git config 获取代理配置"""
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(var, "")
        if val:
            return {"https": val, "http": val}
    # 从 git config 读取
    import subprocess
    try:
        proxy = subprocess.check_output(
            ["git", "config", "--global", "https.proxy"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        if proxy:
            return {"https": proxy, "http": proxy}
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return {}


# ─── GitHub API ─────────────────────────────────────────────

def _get_token() -> str:
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        val = os.environ.get(var, "")
        if val:
            return val
    token_file = os.path.expanduser("~/.gh_tokenrc")
    if os.path.exists(token_file):
        with open(token_file) as f:
            for line in f:
                if "github_oauth" in line and "=" in line:
                    return line.split("=", 1)[1].strip()
    print("错误: 未找到 GitHub token")
    sys.exit(1)


def _graphql(token: str, query: str, retries: int = 3) -> dict:
    """带重试和限流处理的 GraphQL 请求"""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    proxies = _get_proxies()
    for attempt in range(retries):
        resp = requests.post(
            "https://api.github.com/graphql",
            json={"query": query},
            headers=headers,
            timeout=30,
            proxies=proxies,
        )
        if resp.status_code == 200:
            data = resp.json()
            if "errors" in data:
                # 检查是否是限流
                for err in data["errors"]:
                    if "rate limit" in err.get("message", "").lower():
                        reset = int(resp.headers.get("X-RateLimit-Reset", 0))
                        wait = max(reset - int(time.time()), 10)
                        print(f"  API 限流，等待 {wait}s ...")
                        time.sleep(wait)
                        continue
                print(f"GraphQL 错误: {data['errors']}")
                sys.exit(1)
            return data
        elif resp.status_code == 403:
            reset = int(resp.headers.get("X-RateLimit-Reset", 0))
            wait = max(reset - int(time.time()), 10)
            print(f"  API 限流 (403)，等待 {wait}s ...")
            time.sleep(wait)
        else:
            print(f"  HTTP {resp.status_code}，重试 {attempt+1}/{retries} ...")
            time.sleep(5)
    print(f"API 请求失败，已重试 {retries} 次")
    sys.exit(1)


def fetch_all_merged_prs(token: str, repo: str, before: Optional[str] = None, since: Optional[str] = None) -> List[dict]:
    """获取单个仓库的全量已合并 PR（使用 pullRequests 端点，无 1000 条限制）

    返回: [{"url": ..., "author": ..., "mergedAt": ...}, ...]
    """
    prs = []
    cursor = None
    page = 0

    while True:
        after_clause = f', after: "{cursor}"' if cursor else ""
        query = f'''{{
          repository(owner: "{ORG}", name: "{repo}") {{
            pullRequests(states: MERGED, first: 100, orderBy: {{field: CREATED_AT, direction: ASC}}{after_clause}) {{
              totalCount
              nodes {{
                url
                mergedAt
                author {{ login }}
              }}
              pageInfo {{ hasNextPage endCursor }}
            }}
          }}
        }}'''

        data = _graphql(token, query)
        repo_data = data.get("data", {}).get("repository")
        if not repo_data:
            print(f"  警告: 仓库 {repo} 不存在或无权限访问")
            return prs

        pr_data = repo_data["pullRequests"]
        if page == 0:
            print(f"  {repo}: 共 {pr_data['totalCount']} 个已合并 PR，正在分页获取 ...", end="", flush=True)

        for node in pr_data["nodes"]:
            if not node:
                continue
            merged_at = node.get("mergedAt", "")
            # 如果指定了 before，跳过该日期之后合并的 PR
            if before and merged_at and merged_at[:10] > before:
                continue
            # 如果指定了 since，跳过该日期之前合并的 PR
            if since and merged_at and merged_at[:10] < since:
                continue
            author = node.get("author")
            author_login = author["login"] if author else "(ghost)"
            prs.append({
                "url": node["url"],
                "author": author_login,
                "mergedAt": merged_at,
            })

        page += 1
        if page % 10 == 0:
            print(".", end="", flush=True)

        if not pr_data["pageInfo"]["hasNextPage"]:
            break
        cursor = pr_data["pageInfo"]["endCursor"]

    print(f" 获取到 {len(prs)} 条")
    return prs


# ─── 贡献者分析 ──────────────────────────────────────────────

def load_contributors(csv_path: str) -> Dict[str, str]:
    if not os.path.exists(csv_path):
        return {}
    with open(csv_path, newline="") as f:
        return {row["github_id"]: row["category"] for row in csv.DictReader(f)}


def analyze(
    all_prs: Dict[str, List[dict]],
    known: Dict[str, str],
    before: Optional[str],
    since: Optional[str] = None,
) -> dict:
    """分析全量 PR 数据，产出诊断报告"""

    # 汇总所有唯一作者
    all_authors: Dict[str, Set[str]] = defaultdict(set)  # author -> set of repos
    ghost_prs: List[dict] = []
    total_prs = 0

    for repo, prs in all_prs.items():
        for pr in prs:
            total_prs += 1
            if pr["author"] == "(ghost)":
                ghost_prs.append({"url": pr["url"], "repo": repo, "mergedAt": pr["mergedAt"]})
            else:
                all_authors[pr["author"]].add(repo)

    # 按分类统计
    category_counts: Dict[str, int] = defaultdict(int)
    community_authors: List[str] = []
    unknown_authors: List[str] = []

    for author in all_authors:
        cat = known.get(author, "未知")
        category_counts[cat] += 1
        if cat == "社区":
            community_authors.append(author)
        elif cat == "未知":
            unknown_authors.append(author)

    # 在 contributors.csv 中但 API 查不到的作者
    api_authors = set(all_authors.keys())
    csv_only = {gid for gid in known if gid not in api_authors and gid != "(ghost)"}

    return {
        "total_prs": total_prs,
        "total_unique_authors": len(all_authors),
        "ghost_prs_count": len(ghost_prs),
        "ghost_prs": ghost_prs[:20],  # 前 20 条用于展示
        "category_counts": dict(category_counts),
        "community_count": len(community_authors),
        "community_authors": sorted(community_authors),
        "unknown_count": len(unknown_authors),
        "unknown_authors": sorted(unknown_authors)[:50],  # 前 50 条
        "csv_only_count": len(csv_only),
        "csv_only": sorted(csv_only)[:30],  # 前 30 条
        "per_repo_stats": {
            repo: {
                "total_prs": len(prs),
                "unique_authors": len({p["author"] for p in prs if p["author"] != "(ghost)"}),
                "community_prs": sum(1 for p in prs if known.get(p["author"]) == "社区"),
            }
            for repo, prs in all_prs.items()
        },
        "before_date": before,
        "since_date": since,
    }


def print_report(report: dict):
    """打印诊断报告"""
    before = report["before_date"]
    since = report.get("since_date")
    parts = []
    if since:
        parts.append(f"自 {since}")
    if before:
        parts.append(f"截止 {before}")
    date_label = f"（{'，'.join(parts)}）" if parts else "（全量）"
    print(f"\n{'='*60}")
    print(f"  飞桨社区开发者统计 — 诊断报告 {date_label}")
    print(f"{'='*60}")

    print(f"\n总 PR 数: {report['total_prs']}")
    print(f"唯一作者数（不含 ghost）: {report['total_unique_authors']}")
    print(f"Ghost PR 数: {report['ghost_prs_count']}")

    print(f"\n--- 按分类统计 ---")
    for cat, count in sorted(report["category_counts"].items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")

    print(f"\n★ 社区开发者（个人贡献者）: {report['community_count']} 人")

    print(f"\n--- 各仓库统计 ---")
    for repo, stats in sorted(report["per_repo_stats"].items(), key=lambda x: -x[1]["total_prs"]):
        total = stats["total_prs"]
        community = stats["community_prs"]
        ratio = community / total * 100 if total > 0 else 0
        print(f"  {repo}: {total} PR, {stats['unique_authors']} 作者, 社区占比 {ratio:.1f}%")

    if report["ghost_prs_count"] > 0:
        print(f"\n--- Ghost 账户（已删号，共 {report['ghost_prs_count']} 条 PR）---")
        for gpr in report["ghost_prs"]:
            print(f"  {gpr['url']}  (merged {gpr['mergedAt'][:10]})")
        if report["ghost_prs_count"] > 20:
            print(f"  ... 还有 {report['ghost_prs_count'] - 20} 条")

    if report["unknown_count"] > 0:
        print(f"\n--- 未分类作者（不在 contributors.csv 中，共 {report['unknown_count']} 人）---")
        for a in report["unknown_authors"]:
            print(f"  {a}")
        if report["unknown_count"] > 50:
            print(f"  ... 还有 {report['unknown_count'] - 50} 人")

    if report["csv_only_count"] > 0:
        print(f"\n--- CSV 中有但 API 查不到的作者（疑似改名/删号，共 {report['csv_only_count']} 人）---")
        for a in report["csv_only"]:
            print(f"  {a}")
        if report["csv_only_count"] > 30:
            print(f"  ... 还有 {report['csv_only_count'] - 30} 人")

    print(f"\n{'='*60}")
    print(f"  关键指标: 社区开发者 = {report['community_count']} 人")
    if before:
        print(f"  对比基准: 661 人（2026-04-02）")
        diff = report['community_count'] - 661
        print(f"  差异: {diff:+d} 人")
    print(f"{'='*60}")


def save_report(report: dict, output_path: str):
    """保存诊断报告为 JSON"""
    with open(output_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n详细报告已保存到: {output_path}")


# ─── 主入口 ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PaddlePaddle 社区开发者全量诊断")
    parser.add_argument("--before", help="仅统计该日期前合并的 PR（YYYY-MM-DD）")
    parser.add_argument("--since", help="仅统计该日期起合并的 PR（YYYY-MM-DD）")
    parser.add_argument("--repo", action="append", help="仅诊断指定仓库（可多次使用）")
    parser.add_argument("--contributors", default=os.path.join(SCRIPT_DIR, "contributors.csv"),
                        help="贡献者列表路径")
    parser.add_argument("--output", default=os.path.join(SCRIPT_DIR, "diagnose_report.json"),
                        help="诊断报告输出路径")
    parser.add_argument("--cache", default=os.path.join(SCRIPT_DIR, "diagnose_cache.json"),
                        help="API 缓存路径（避免重复请求）")
    args = parser.parse_args()

    repos = args.repo if args.repo else ALL_REPOS
    token = _get_token()

    # 尝试加载缓存
    cached_prs: Dict[str, List[dict]] = {}
    if os.path.exists(args.cache):
        with open(args.cache) as f:
            cached_prs = json.load(f)
        print(f"加载缓存: {len(cached_prs)} 个仓库")

    print(f"诊断范围: {len(repos)} 个仓库")
    if args.since:
        print(f"起始日期: {args.since}")
    if args.before:
        print(f"截止日期: {args.before}")
    print()

    all_prs: Dict[str, List[dict]] = {}
    for repo in repos:
        if repo in cached_prs:
            # 使用缓存，在内存中按日期过滤
            cached = cached_prs[repo]
            filtered = cached
            if args.since or args.before:
                filtered = [
                    p for p in cached
                    if (not args.since or p.get("mergedAt", "")[:10] >= args.since)
                    and (not args.before or p.get("mergedAt", "")[:10] <= args.before)
                ]
            print(f"  {repo}: 使用缓存 ({len(cached)} 条, 过滤后 {len(filtered)} 条)")
            all_prs[repo] = filtered
        else:
            prs = fetch_all_merged_prs(token, repo, before=args.before, since=args.since)
            all_prs[repo] = prs

    # 保存缓存（仅全量模式）
    # 保存缓存（始终保存，用于后续分析）
    with open(args.cache, "w") as f:
        json.dump(all_prs, f, ensure_ascii=False)
    print(f"\n缓存已保存到: {args.cache}")

    # 分析
    known = load_contributors(args.contributors)
    report = analyze(all_prs, known, before=args.before, since=args.since)
    # 保存完整的未分类作者列表（不截断）
    all_authors_set = set()
    for prs in all_prs.values():
        for pr in prs:
            if pr["author"] != "(ghost)":
                all_authors_set.add(pr["author"])
    full_unknown = sorted(a for a in all_authors_set if a not in known)
    report["unknown_authors_full"] = full_unknown
    print_report(report)
    save_report(report, args.output)


if __name__ == "__main__":
    main()
