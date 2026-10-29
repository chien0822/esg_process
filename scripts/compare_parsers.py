"""M1 解析器比較（SPEC 5、13）：Docling 與 PyMuPDF4LLM 並排輸出，讓使用者決定預設值。

  uv run python scripts/compare_parsers.py            # 兩份報告書全文解析（結果存入解析快取，S1 可直接沿用）
  uv run python scripts/compare_parsers.py --pages-only  # 只解析下方挑選的頁（快，但沒有全文耗時）

輸出 runs/parser_compare/<時間戳>/：
  index.html     每個挑選頁：PDF 頁面圖、兩種解析器的 Markdown（渲染與原文）並排
  summary.json   耗時、每頁字元保留率、表格數等數字
不使用 LLM。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymupdf  # noqa: E402
from markdown_it import MarkdownIt  # noqa: E402

from esgfin.config import load_reports  # noqa: E402
from esgfin.parse.base import make_parser  # noqa: E402
from esgfin.parse.cache import ParseCache  # noqa: E402
from esgfin.pipeline.paths import ProjectPaths  # noqa: E402

PARSERS = ("docling", "pymupdf4llm")

# 挑選頁（PDF 頁序）。策略表頁以文字層搜尋「財務影響」等關鍵字找出，印刷頁碼列在說明
SAMPLE_PAGES: dict[str, list[tuple[int, str]]] = {
    "realtek_2024_ESG": [
        (74, "策略表第 1 頁（印刷 p.72）"),
        (75, "策略表第 2 頁（印刷 p.73）"),
        (78, "溫室氣體數據表"),
        (113, "數字密集頁"),
        (162, "GRI 索引"),
    ],
    "Quanta_2024_ESG": [
        (81, "策略表第 1 頁（印刷 p.79）"),
        (82, "策略表第 2 頁（印刷 p.80）"),
        (86, "用電量數據表"),
        (97, "廢棄物數據表"),
        (163, "GRI 索引"),
    ],
}
# 已知必須完整保留的原文（SPEC 9.6）
PROBE_STRINGS: dict[str, list[str]] = {
    "realtek_2024_ESG": ["電費預計增加約1,770萬/年"],
    "Quanta_2024_ESG": [],
}


def normalize(s: str) -> str:
    """與引文驗證相同方向的正規化：全半形統一、去除空白與 Markdown 表格符號。"""
    s = unicodedata.normalize("NFKC", s)
    for ch in "|*#`<>-_":
        s = s.replace(ch, "")
    return "".join(s.split())


def char_recall(raw: str, parsed: str) -> float | None:
    """文字層字元有多少比例出現在解析結果（以字元計數，不看順序）。"""
    a, b = Counter(normalize(raw)), Counter(normalize(parsed.replace("<br>", "")))
    total = sum(a.values())
    if total == 0:
        return None
    return sum(min(n, b[c]) for c, n in a.items()) / total


def parse_report(paths: ProjectPaths, pdf: Path, sha: str, parser_name: str, pages_only: list[int] | None):
    parser = make_parser(parser_name)
    cache = ParseCache(paths.parse_cache_dir)
    if pages_only is None:
        cached = cache.get(sha, parser.name, parser.version)
        if cached is not None:
            return parser.version, cached, None, True
    t0 = time.monotonic()
    pages = parser.parse(pdf, pages_only)
    elapsed = time.monotonic() - t0
    if pages_only is None:
        cache.put(sha, parser.name, parser.version, pages)
    return parser.version, pages, elapsed, False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages-only", action="store_true", help="只解析挑選頁")
    ap.add_argument("--parser", choices=PARSERS, action="append", help="只跑指定解析器（可重複）")
    args = ap.parse_args()
    parsers = args.parser or list(PARSERS)

    paths = ProjectPaths(ROOT)
    reports = {r.report_id: r for r in load_reports(paths.reports_file).reports}
    out_dir = paths.runs_dir / "parser_compare" / datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / "img").mkdir(parents=True)
    md = MarkdownIt("commonmark", {"html": True}).enable("table")

    summary: dict = {"generated_at": datetime.now().isoformat(timespec="seconds"), "reports": {}}
    sections = []
    for rid, samples in SAMPLE_PAGES.items():
        pdf = paths.report_dir / reports[rid].file
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        with pymupdf.open(pdf) as doc:
            page_count = doc.page_count
            raw = {i + 1: doc[i].get_text() for i in range(page_count)}
            for n, _ in samples:
                doc[n - 1].get_pixmap(dpi=110).save(out_dir / "img" / f"{rid}-p{n}.png")

        wanted = [n for n, _ in samples] if args.pages_only else None
        rsum: dict = {"page_count": page_count, "parsers": {}}
        results = {}
        for name in parsers:
            print(f"{rid}：{name} 解析中（{'挑選頁' if wanted else f'全文 {page_count} 頁'}）…", flush=True)
            version, pages, elapsed, hit = parse_report(paths, pdf, sha, name, wanted)
            by_page = {p.pdf_page: p for p in pages}
            results[name] = by_page
            recalls = {n: char_recall(raw[n], p.text_md) for n, p in by_page.items()}
            valid = [r for r in recalls.values() if r is not None]
            all_text = "".join(p.text_md for p in pages)
            rsum["parsers"][name] = {
                "version": version,
                "seconds": None if elapsed is None else round(elapsed, 1),
                "from_cache": hit,
                "pages_parsed": len(pages),
                "seconds_per_page": None if elapsed is None else round(elapsed / len(pages), 2),
                "tables_total": sum(len(p.tables_md) for p in pages),
                "mean_char_recall": round(sum(valid) / len(valid), 4) if valid else None,
                "pages_char_recall_below_0.9": sorted(n for n, r in recalls.items() if r is not None and r < 0.9),
                "sample_pages": {
                    n: {
                        "chars": len(by_page[n].text_md),
                        "tables": len(by_page[n].tables_md),
                        "char_recall": None if recalls[n] is None else round(recalls[n], 4),
                    }
                    for n, _ in samples
                },
                "probes": {s: normalize(s) in normalize(all_text.replace("<br>", "")) for s in PROBE_STRINGS[rid]},
            }
            print(f"  完成：{rsum['parsers'][name]['seconds']} 秒；平均字元保留率 {rsum['parsers'][name]['mean_char_recall']}", flush=True)
        summary["reports"][rid] = rsum

        for n, label in samples:
            cols = []
            for name in parsers:
                p = results[name][n]
                s = rsum["parsers"][name]["sample_pages"][n]
                cols.append(
                    f'<div class="col"><h4>{name}<small>　{s["chars"]} 字元・{s["tables"]} 表・字元保留率 {s["char_recall"]}</small></h4>'
                    f'<div class="render">{md.render(p.text_md)}</div>'
                    f'<details><summary>Markdown 原文</summary><pre>{html.escape(p.text_md)}</pre></details></div>'
                )
            sections.append(
                f'<section><h3>{html.escape(reports[rid].company_full_name)}　PDF 第 {n} 頁　{html.escape(label)}</h3>'
                f'<div class="row"><div class="col img"><img src="img/{rid}-p{n}.png" loading="lazy"></div>{"".join(cols)}</div></section>'
            )

    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "index.html").write_text(PAGE.format(body="\n".join(sections), summary=html.escape(json.dumps(summary, ensure_ascii=False, indent=2))), encoding="utf-8")
    print(f"比較檔：{out_dir / 'index.html'}")
    return 0


PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>解析器比較</title>
<style>
body{{font-family:system-ui,"Noto Sans TC",sans-serif;margin:16px;background:#fff;color:#222}}
.row{{display:flex;gap:12px;align-items:flex-start}}
.col{{flex:1;min-width:0;border:1px solid #ddd;padding:8px;overflow:auto;max-height:90vh}}
.col.img{{flex:1.1}} .col.img img{{width:100%}}
table{{border-collapse:collapse;font-size:12px}} td,th{{border:1px solid #999;padding:2px 4px;vertical-align:top}}
pre{{white-space:pre-wrap;font-size:11px}} h4 small{{font-weight:normal;color:#666}}
section{{margin-bottom:32px}}
</style></head><body><h2>解析器比較（M1）</h2>
<details><summary>summary.json</summary><pre>{summary}</pre></details>
{body}</body></html>"""


if __name__ == "__main__":
    sys.exit(main())
