#!/usr/bin/env python3
"""基于 diagnose_cache.json 计算每周社区开发者人数变化

以 2026-04-02 为基准（801 人），统计后续每周四的累计社区开发者数及增量。

用法:
    python3 weekly_delta.py
    python3 weekly_delta.py --baseline-date 2026-04-02 --baseline-count 801
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Set, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_cache(cache_path: str) -> Dict[str, List[dict]]:
    with open(cache_path) as f:
        return json.load(f)


def load_contributors(csv_path: str) -> Dict[str, str]:
    if not os.path.exists(csv_path):
        return {}
    with open(csv_path, newline="") as f:
        return {row["github_id"]: row["category"] for row in csv.DictReader(f)}


def get_thursdays(start_date: str, end_date: str) -> List[str]:
    """返回 start_date 之后、end_date 之前（含）的所有周四日期"""
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()

    # 找到 start 之后的第一个周四
    d = start
    while d.weekday() != 3:  # 3 = Thursday
        d += timedelta(days=1)

    thursdays = []
    while d <= end:
        thursdays.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=7)
    return thursdays


def compute_cumulative_community(
    cache: Dict[str, List[dict]],
    known: Dict[str, str],
    cutoff: str,
) -> Tuple[int, Set[str]]:
    """计算截止某日的累计社区开发者集合"""
    community = set()
    for repo, prs in cache.items():
        for pr in prs:
            merged = pr.get("mergedAt", "")
            if not merged:
                continue
            if merged[:10] > cutoff:
                continue
            author = pr.get("author", "")
            if author == "(ghost)":
                continue
            if known.get(author) == "社区":
                community.add(author)
    return len(community), community


def main():
    parser = argparse.ArgumentParser(description="每周社区开发者人数变化")
    parser.add_argument("--baseline-date", default="2026-04-02", help="基准日期")
    parser.add_argument("--baseline-count", type=int, default=801, help="基准社区开发者数")
    parser.add_argument("--cache", default=os.path.join(SCRIPT_DIR, "diagnose_cache.json"))
    parser.add_argument("--contributors", default=os.path.join(SCRIPT_DIR, "contributors.csv"))
    args = parser.parse_args()

    cache = load_cache(args.cache)
    known = load_contributors(args.contributors)

    # 确定时间范围
    today = date.today().strftime("%Y-%m-%d")

    # 先验证基准
    baseline_count, baseline_set = compute_cumulative_community(cache, known, args.baseline_date)
    print(f"基准日期: {args.baseline_date}")
    print(f"  计算社区开发者数: {baseline_count}")
    print(f"  声明基准: {args.baseline_count}")
    if baseline_count != args.baseline_count:
        print(f"  ⚠️  差异: {baseline_count - args.baseline_count:+d}")
    print()

    # 获取基准日期之后到今天的所有周四
    thursdays = get_thursdays(
        (datetime.strptime(args.baseline_date, "%Y-%m-%d").date() + timedelta(days=1)).strftime("%Y-%m-%d"),
        today,
    )

    # 如果今天不是周四且不在列表中，追加今天
    if today not in thursdays:
        thursdays.append(today)

    # 计算每个截止日的累计数
    print(f"{'日期':<14} {'累计社区开发者':>10} {'环比变化':>8} {'新增开发者'}")
    print("-" * 70)

    prev_set = baseline_set
    prev_count = baseline_count
    print(f"{args.baseline_date:<14} {baseline_count:>10} {'(基准)':>8}")

    for cutoff in thursdays:
        count, current_set = compute_cumulative_community(cache, known, cutoff)
        delta = count - prev_count
        new_authors = sorted(current_set - prev_set)
        new_str = ", ".join(new_authors[:10])
        if len(new_authors) > 10:
            new_str += f" ... (+{len(new_authors)-10})"
        label = "(本周至今)" if cutoff == today and date.today().weekday() != 3 else ""
        print(f"{cutoff:<14} {count:>10} {delta:>+8} {new_str} {label}")
        prev_set = current_set
        prev_count = count

    # 输出总结
    final_count, _ = compute_cumulative_community(cache, known, today)
    total_delta = final_count - args.baseline_count
    print("-" * 70)
    print(f"总计: {args.baseline_date} → {today}，社区开发者 {args.baseline_count} → {final_count} ({total_delta:+d})")


if __name__ == "__main__":
    main()
