"""M2 人工核對用：把一個 run 的 02_manifest.json 整理成 runs/<run_id>/manifest_review.md。

  uv run python scripts/manifest_review.py              # 最新的 run
  uv run python scripts/manifest_review.py 20261110-165831

只讀 run 資料夾內的 02_manifest.json、02_locate_debug.json、run.json，不呼叫 LLM。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def cell(v) -> str:
    if v is None:
        return "—"
    return str(v).replace("|", "／").replace("\n", " ")


def page_range(r) -> str:
    return "—" if not r else f"{r[0]}–{r[1]}" if r[0] != r[1] else str(r[0])


def report_section(rid: str, run_dir: Path, settings: dict) -> list[str]:
    m = json.loads((run_dir / rid / "02_manifest.json").read_text(encoding="utf-8"))
    d = json.loads((run_dir / rid / "02_locate_debug.json").read_text(encoding="utf-8"))
    loc = settings["locate"]
    out = [f"## {rid}", ""]

    off = m["page_offset"] or {}
    out += [
        f"- 目錄來源：{m['toc_source']}（目錄頁：{d.get('toc_pages', '—')}；書籤：{d['bookmarks']['note']}）",
        f"- 頁碼位移：{off.get('value')}（{off.get('confidence_note', '')}）",
        "",
        "### 章節",
        "",
        "| 代碼 | 層級 | 標題 | 印刷起始頁 | PDF 起訖頁 | 角色 | 警告 |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in m["sections"]:
        printed = s["printed_page_range"][0] if s["printed_page_range"] else None
        indent = "　" * (s["level"] - 1)
        out.append(
            f"| {s['section_id']} | {s['level']} | {indent}{cell(s['title'])} | {cell(printed)} | "
            f"{page_range(s['pdf_page_range'])} | {cell(s['role'])} | {cell('、'.join(s['flags']) or None)} |"
        )

    out += ["", "### 策略表", ""]
    if m["strategy_tables"]:
        out += ["| PDF 頁 | 所屬章節 | 找到方式 |", "|---|---|---|"]
        out += [f"| {', '.join(map(str, t['pdf_pages']))} | {cell(t['section_id'])} | {t['found_by']} |" for t in m["strategy_tables"]]
    else:
        out.append("（未找到）")

    sc = d["strategy_candidates"]
    hits = {int(k): v for k, v in sc["keyword_hits"].items()}
    judged = {int(k): v for k, v in sc["llm_judgement"].items()}
    sent = set(sc["sent_to_llm"])
    eligible = sorted((n for n, h in hits.items() if len(h) >= loc["min_keyword_hits"]), key=lambda n: (-len(hits[n]), n))
    dropped = [n for n in eligible if n not in sent]
    env = sc["environment_pages"]
    out += [
        "",
        "### 候選頁",
        "",
        f"- 搜尋範圍（環境章節含緩衝）：{len(env)} 頁（PDF {page_range([env[0], env[-1]]) if env else '—'}）",
        f"- 排除導覽列後（規則依該 run 的 commit）：命中至少 1 個關鍵字 {len(hits)} 頁；至少 {loc['min_keyword_hits']} 個 {len(eligible)} 頁；"
        f"上限 {loc['max_strategy_candidates']} 頁",
        "",
        "| PDF 頁 | 關鍵字數 | 命中關鍵字 | 送 LLM | LLM 判斷為策略表 |",
        "|---|---|---|---|---|",
    ]
    for n in sorted(eligible):
        j = judged.get(n)
        verdict = "—" if j is None else ("是" if j["is_strategy_table"] else "否")
        out.append(f"| {n} | {len(hits[n])} | {'、'.join(hits[n])} | {'是' if n in sent else '**否（超過上限）**'} | {verdict} |")
    nb = sc.get("neighbor_pages", [])
    if nb:
        out.append("\n策略表前後頁追加判斷：" + "、".join(f"p.{n}（{'是' if judged.get(n, {}).get('is_strategy_table') else '否'}）" for n in nb))
    else:
        out.append("\n策略表前後頁追加判斷：無（前後頁皆已在候選名單中）")
    if sc.get("index_fallback_pages"):
        out.append(f"\n索引退路候選頁：{sc['index_fallback_pages']}")
    out += ["", f"因上限被排除的頁：{'、'.join(f'p.{n}（{len(hits[n])}）' for n in dropped) if dropped else '無'}"]

    out += ["", "### 書末索引交叉驗證", "", f"讀取的索引頁：{d.get('index_pages')}", ""]
    if m["index_cross_check"]:
        out += ["| 索引項目 | 指向 | 在定位結果內 |", "|---|---|---|"]
        out += [f"| {cell(c['index_item'])} | {cell(c['points_to'])} | {cell(c['inside_located_range'])} |" for c in m["index_cross_check"]]
    else:
        out.append("（無）")

    out += ["", "### 警告", ""] + ([f"- {w}" for w in m["warnings"]] or ["（無）"]) + [""]
    return out


def main() -> int:
    runs = ROOT / "runs"
    run_id = sys.argv[1] if len(sys.argv) > 1 else sorted(p.name for p in runs.iterdir() if (p / "run.json").exists())[-1]
    run_dir = runs / run_id
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    lines = [
        f"# M2 manifest 核對（run {run_id}）",
        "",
        f"- git commit：{run['git']['commit']}（未提交修改：{run['git']['dirty']}；strict：{run['strict']}）",
        f"- 模型：{run['model']}；提示版本：{', '.join(k + ' v' + str(v['version']) for k, v in run['prompts'].items())}",
        "",
    ]
    for rid in run["selected_reports"]:
        if (run_dir / rid / "02_manifest.json").exists():
            lines += report_section(rid, run_dir, run["settings"])
        else:
            lines += [f"## {rid}", "", f"（沒有 02_manifest.json：{run['reports'][rid].get('error')}）", ""]
    out = run_dir / "manifest_review.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
