"""Produce human-readable Markdown cards and a coverage manifest."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEVEL_LABEL = {"source": "已读代表源码", "docs": "已读功能文档", "readme": "README 分析", "metadata": "仅元数据"}
KIND_LABEL = {"app": "应用", "library": "组件与库", "framework": "框架", "cli": "命令行工具", "skill": "AI 技能", "collection": "资料与资源集", "tutorial": "教程", "research": "研究", "infrastructure": "基础设施", "other": "其他"}


def bullet(items):
    return "\n".join(f"- {item}" for item in items) or "- 尚未确认。"


def render_card(repo, card, manifest):
    name = repo["full_name"]
    sha = card["source_sha"]
    stale = sha != manifest.get("source_sha")
    header = f"# {name}\n\n{card['summary_zh']}\n\n"
    header += f"[GitHub]({repo['url']}) · {KIND_LABEL.get(card['kind'],card['kind'])} · {LEVEL_LABEL.get(card['evidence_level'])} · 运行未验证\n\n"
    header += f"分析 commit：`{sha}`；分析日期：{card['analyzed_at']}。\n\n"
    if stale:
        header += "**此卡片早于当前采集的版本，需要复核后推荐。**\n\n"
    out = header + "## 适合用来做什么\n\n" + bullet(card["use_cases"]) + "\n\n## 功能与依据\n\n"
    evidence = {e["id"]: e for e in card["evidence"]}
    for f in card["features"]:
        links = []
        for ident in f["evidence"]:
            e = evidence[ident]
            links.append(f"[{e['path']}:{e['start_line']}]({e['url']})")
        out += f"### {f['name']}\n\n{f['description']}\n\n依据等级：{LEVEL_LABEL.get(f['evidence_level'])}；{'、'.join(links)}。\n\n"
    for title, key in (("入口与接口", "interfaces"), ("使用前提", "requirements"), ("限制与推荐边界", "limitations"), ("帮助选择的区别", "comparisons"), ("未验证部分", "unverified")):
        out += f"## {title}\n\n{bullet(card.get(key,[]))}\n\n"
    out += "## 已检查的证据\n\n"
    for e in evidence.values():
        out += f"- [{e['path']}:{e['start_line']}-{e['end_line']}]({e['url']})：{e['claim']}\n"
    out += "\n标签：" + "、".join(card["tags"]) + "\n"
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    catalog = json.loads((root / "data" / "catalog.json").read_text(encoding="utf-8"))
    dest = root / "cards"
    dest.mkdir(exist_ok=True)
    groups = defaultdict(list)
    levels = Counter()
    statuses = []
    for repo in catalog["repositories"]:
        name = repo["full_name"]
        slug = name.replace("/", "__")
        card_path = root / "data" / "cards" / f"{slug}.json"
        manifest_path = root / "data" / "sources" / slug / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        if card_path.exists():
            card = json.loads(card_path.read_text(encoding="utf-8"))
            stale = card.get("source_sha") != manifest.get("source_sha")
            level = card["evidence_level"]
            (dest / f"{slug}.md").write_text(render_card(repo, card, manifest), encoding="utf-8")
            groups[card["kind"]].append((repo, card, stale))
        else:
            level, stale = "metadata", False
            groups["pending"].append((repo, None, False))
        levels[level] += 1
        statuses.append({"full_name": name, "card_exists": card_path.exists(), "source_sha": manifest.get("source_sha"), "evidence_level": level, "stale": stale, "captured_files": len(manifest.get("files", [])), "capture_errors": manifest.get("errors", [])})
    status = {"generated_at": datetime.now(timezone.utc).isoformat(), "catalog_count": catalog["count"], "catalog_complete": catalog["complete"], "levels": dict(levels), "stale_cards": sum(s["stale"] for s in statuses), "repositories": statuses}
    (root / "data" / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# GitHub Star 功能索引", "", f"账号：{catalog['user']}；当前收藏：{catalog['count']}；同步时间：{catalog['synced_at']}。", "", "每个功能的依据等级分别标记。源码分析采用代表性流程抽样；所有第三方仓库的运行效果均未验证。", "", "## 分析覆盖", ""]
    lines += [f"- {LEVEL_LABEL[level]}：{count}" for level, count in levels.items()]
    lines += ["", "## AI 检索", "", '`python search.py search "你想做的事情" --json --limit 8`', "", "打开命中的卡片，结合功能证据、平台限制和未验证部分选择候选。", ""]
    for kind, entries in sorted(groups.items()):
        lines += [f"## {KIND_LABEL.get(kind, '待深入分析')}（{len(entries)}）", ""]
        for repo, card, stale in sorted(entries, key=lambda x: x[0]["full_name"].lower()):
            slug = repo["full_name"].replace("/", "__")
            if card:
                marker = " · 版本待复核" if stale else ""
                lines.append(f"- [{repo['full_name']}](cards/{slug}.md)：{card['summary_zh']}（{LEVEL_LABEL[card['evidence_level']]}{marker}）")
            else:
                lines.append(f"- [{repo['full_name']}]({repo['url']})：{repo.get('description') or '暂无描述'}")
        lines.append("")
    (root / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: v for k, v in status.items() if k != "repositories"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
