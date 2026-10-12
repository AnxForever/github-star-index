"""Measure retrieval recall against a labeled task set.

This is a local evaluation harness, not part of the agent retrieval path. It
reads a labels file (default ``data/eval/labels.json``) that maps a natural
development task to the repositories a human judged relevant, runs the same
bounded search the agent uses, and reports recall@k plus empty-result and
zero-feature rates.

The labels file references a personal star index and stays in ignored local
paths. Keep only synthetic or explicitly public labels in shared material.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from search import IndexErrorBase
from service import ROOT, IndexService

DEFAULT_LABELS = ROOT / "data" / "eval" / "labels.json"


def load_labels(path: Path) -> list[dict]:
    if not path.is_file():
        raise IndexErrorBase(f"Labels file not found: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("tasks", [])
    if not isinstance(value, list):
        raise IndexErrorBase("Labels must be a list of {task, relevant} objects")
    labels: list[dict] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        task = entry.get("task")
        relevant = entry.get("relevant")
        if not isinstance(task, str) or not task.strip():
            continue
        if not isinstance(relevant, list):
            relevant = []
        labels.append({
            "task": task.strip(),
            "relevant": [name for name in relevant if isinstance(name, str)],
            "skip": bool(entry.get("skip", False)),
        })
    return labels


def evaluate(service: IndexService, labels: list[dict], limit: int = 5) -> dict:
    rows: list[dict] = []
    scored = 0
    recall_hits = 0
    precision_sum = 0.0
    rr_sum = 0.0
    empty = 0
    zero_feature = 0
    candidates = 0
    for entry in labels:
        task = entry["task"]
        relevant = [name for name in entry["relevant"] if name]
        relevant_set = set(relevant)
        result = service.search(task, limit=limit)
        names = [item["full_name"] for item in result.get("results", [])]
        if not names:
            empty += 1
        found = [name for name in names if name in relevant_set] if relevant else []
        for item in result.get("results", []):
            candidates += 1
            brief = item.get("selection_brief") or {}
            if not brief.get("matched_features"):
                zero_feature += 1
        hit = bool(found)
        if relevant and not entry["skip"]:
            scored += 1
            recall_hits += hit
            # Precision measures how much of the returned page is relevant;
            # recall alone hides a page that buries one hit under noise.
            precision_sum += len(found) / len(names) if names else 0.0
            rank = next((index for index, name in enumerate(names, 1) if name in relevant_set), None)
            rr_sum += 1.0 / rank if rank else 0.0
        rows.append({
            "task": task,
            "relevant": len(relevant),
            "hit": hit,
            "top": names,
            "found": found,
        })
    return {
        "tasks": len(labels),
        "scored_tasks": scored,
        "recall_at_k": round(recall_hits / scored, 3) if scored else None,
        "precision_at_k": round(precision_sum / scored, 3) if scored else None,
        "mrr": round(rr_sum / scored, 3) if scored else None,
        "limit": limit,
        "empty_results": empty,
        "candidates": candidates,
        "zero_feature_candidates": zero_feature,
        "feature_coverage": round(1 - zero_feature / candidates, 3) if candidates else None,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    try:
        labels = load_labels(args.labels)
        service = IndexService(root=args.root)
        report = evaluate(service, labels, limit=args.limit)
    except (IndexErrorBase, ValueError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"tasks={report['tasks']} scored={report['scored_tasks']} limit={report['limit']}")
    print(f"recall@{report['limit']} = {report['recall_at_k']}")
    print(f"precision@{report['limit']} = {report['precision_at_k']}  mrr = {report['mrr']}")
    print(f"empty_results={report['empty_results']} feature_coverage={report['feature_coverage']}")
    if args.verbose:
        for row in report["rows"]:
            mark = "HIT " if row["hit"] else ("n/a " if not row["relevant"] else "MISS")
            print(f"[{mark}] {row['task']}")
            print(f"        top={row['top']}")
            if row["hit"]:
                print(f"        matched label={row['found']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
