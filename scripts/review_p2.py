"""M4 試跑閱讀用：把一個 run 的 05_p2/*.json 整理成 runs/<run_id>/p2_review.md。

  uv run python scripts/review_p2.py <run_id>

每個條目列出 P1 摘要、理解紀錄、傳導管道、兩張表的科目，以及每段引文對條目頁碼（含 check.citation_page_buffer）
text_md 的驗證結果（呼叫 check/citation.py）。引文結果只供參考，不是 S6；本腳本不判斷答案對錯。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from esgfin.check.citation import match_quote  # noqa: E402
from esgfin.schemas.base import read_jsonl, read_model  # noqa: E402
from esgfin.schemas.p1 import P1Items  # noqa: E402
from esgfin.schemas.pages import PageRecord  # noqa: E402

UNDERSTANDING = ("事件是什麼", "影響公司哪一塊", "已發生或預期", "量化線索", "公司因應")
ACCOUNT_COLS = ("排序", "科目", "影響方向", "直接性層級", "證據等級", "發生階段", "傳導說明", "推論前提", "原文依據", "替代科目")


def cell(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, list):
        return "、".join(map(str, v)) if v else "（空）"
    return str(v).replace("|", "\\|").replace("\n", " ")


def citation(quote: str | None, text: str, ratio: float) -> str:
    if quote is None:
        return "—"
    m = match_quote(quote, text, ratio)
    return f"{m.level}（{m.ratio:.2f}）"


def item_block(item, p2: dict | None, failed: dict | None, page_text: str, ratio: float) -> list[str]:
    src = item.source_text
    out = [
        f"### {item.code}",
        "",
        f"- 類型／子類別：{item.item_type}／{item.subcategory or '—'}（{item.subcategory_source or '—'}；原文用語 {item.subcategory_raw or '—'}）",
        f"- 事件描述（AI 摘要）：{item.event_description}",
        f"- 原文內容.事件描述：{src.event_description}",
        f"- 原文內容.顯示財務Impact：{src.financial_impact or 'null'}",
        f"- 原文內容.因應措施備註：{src.response_note or 'null'}",
        f"- 影響時間 {item.time_horizon or 'null'}（{item.time_horizon_raw or '—'}）；發生可能性 {item.likelihood or 'null'}（{item.likelihood_raw or '—'}）；"
        f"衝擊強度 {item.severity or 'null'}（{item.severity_raw or '—'}）；影響範圍 {item.scope or 'null'}（{item.scope_raw or '—'}）",
        f"- 頁碼：{item.pages}",
        "",
    ]
    if failed is not None:
        return out + [f"**推理失敗**：{failed.get('error')}", ""]
    if p2 is None:
        return out + ["（本 run 未執行此條目）", ""]

    u = p2["理解紀錄"]
    out += ["**理解紀錄**", "", "| 題目 | 內容 | 原文引用 | 來源 | 引文驗證 |", "|---|---|---|---|---|"]
    for k in UNDERSTANDING:
        f = u[k]
        out.append(f"| {k} | {cell(f['內容'])} | {cell(f['原文引用'])} | {f['來源']} | {citation(f['原文引用'], page_text, ratio)} |")
    gaps = u["缺口"]
    out += ["", "缺口：" + ("；".join(f"〔{g['類型']}〕{g['說明']}" for g in gaps) if gaps else "（空）"), ""]

    channels = [f"- 〔{c['管道']}〕{c['傳導說明']}" for c in p2["傳導管道"]] or ["（空）"]
    out += ["**傳導管道**", "", *channels, ""]
    out += [
        f"**整體判斷**：{p2['整體判斷']}；無影響理由類型：{p2['無影響理由類型'] or 'null'}",
        "",
        f"**判斷理由**：{p2['判斷理由']}",
        "",
    ]
    for table, rows in p2["潛在受影響會計科目"].items():
        out.append(f"**{table}**")
        out.append("")
        if not rows:
            out += ["（空）", ""]
            continue
        out += ["| " + " | ".join(ACCOUNT_COLS) + " | 原文依據驗證 |", "|" + "---|" * (len(ACCOUNT_COLS) + 1)]
        out += ["| " + " | ".join(cell(r[c]) for c in ACCOUNT_COLS) + f" | {citation(r['原文依據'], page_text, ratio)} |" for r in rows]
        out.append("")
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    run_id = sys.argv[1]
    rdir = ROOT / "runs" / run_id
    run = json.loads((rdir / "run.json").read_text(encoding="utf-8"))
    buffer = run["settings"]["check"]["citation_page_buffer"]
    ratio = run["settings"]["check"]["fuzzy_pass_ratio"]
    selected = run.get("selected_items")
    lines = [
        "# P2 試跑結果",
        "",
        f"run {run_id}；commit {str(run['git']['commit'])[:10]}（未提交修改 {run['git']['dirty']}）；"
        f"p2_reasoning v{run['prompts']['p2_reasoning']['version']}；thinking level {run['settings']['reason']['thinking_level']}",
        f"指定條目：{'、'.join(selected) if selected else '全部'}",
        "",
        f"引文驗證：對條目頁碼前後各 {buffer} 頁的 text_md，模糊通過門檻 {ratio}；只供參考，不是 S6。",
        "",
    ]
    for rid in run["selected_reports"]:
        report_dir = rdir / rid
        items_path = report_dir / "03_p1_items.json"
        if not items_path.exists():
            continue
        pages = {p.pdf_page: p.text_md for p in read_jsonl(report_dir / "01_pages.jsonl", PageRecord)}
        block = []
        for item in read_model(items_path, P1Items).items:
            ok, bad = report_dir / "05_p2" / f"{item.code}.json", report_dir / "05_p2" / f"{item.code}.failed.json"
            if selected and item.code not in selected:
                continue
            want = {p for pg in item.pages for p in range(pg - buffer, pg + buffer + 1)}
            page_text = "\n".join(pages[p] for p in sorted(want) if p in pages)
            p2 = json.loads(ok.read_text(encoding="utf-8")) if ok.exists() else None
            failed = json.loads(bad.read_text(encoding="utf-8")) if bad.exists() else None
            block += item_block(item, p2, failed, page_text, ratio)
        if block:
            lines += [f"## {rid}", "", *block]
    out = rdir / "p2_review.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
