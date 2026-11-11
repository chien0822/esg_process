"""M3 評估（SPEC 17.11 第 7、15 點）：比對 S3a 重建表、S3 條目與人工標註 annotations/strategy_tables.csv，S3 欄位對應與 annotations/column_mapping.csv。

  uv run python scripts/eval_tables.py <run_id> [<run_id> ...]

每個 run 依 run.json 的 extract.input_mode 標示模式（text / text+pdf）。輸出 runs/<第一個 run_id>/eval_tables.md 並印出路徑。
比對一律用 SPEC 10.2 的正規化（textnorm.normalize）後相等。標註已經使用者逐列核對（2026-11-12）。
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from esgfin.textnorm import normalize  # noqa: E402

ANNOTATIONS = ROOT / "annotations" / "strategy_tables.csv"
MAPPING = ROOT / "annotations" / "column_mapping.csv"  # 預期 column_mapping（SPEC 7.5）
LIMITATION = "限制：兩種輸入模式各只執行一次，樣本共 21 列（瑞昱 11、廣達 10），無法判斷輸入模式的差異與結果的穩定性。"
# 欄序（1 起算）：類型、子類別、事件識別。優先用 S3 的 column_mapping 找類型與子類別欄；沒有 S3 結果時用這裡
COLUMNS = {
    "realtek_2024_ESG": {"類型": 1, "子類別": 2, "事件識別": 3},  # 氣候影響事件
    "Quanta_2024_ESG": {"類型": 1, "子類別": 2, "事件識別": 3},  # 風險項目
}


def read_csv(path: Path) -> list[dict]:
    text = "".join(line for line in path.read_text(encoding="utf-8").splitlines(keepends=True) if not line.startswith("#"))
    return list(csv.DictReader(io.StringIO(text)))


def load_annotations() -> dict[tuple[str, int, int], dict]:
    return {(r["report_id"], int(r["pdf_page"]), int(r["row_on_page"])): r for r in read_csv(ANNOTATIONS)}


def load_mapping() -> dict[str, dict[int, dict]]:
    out: dict[str, dict[int, dict]] = {}
    for r in read_csv(MAPPING):
        out.setdefault(r["report_id"], {})[int(r["欄序"])] = r
    return out


def mapping_score(items: dict, expected: dict[int, dict]) -> tuple[int, int, list[str]]:
    """回傳（正確欄數、預期欄數、不同的列）。缺少或多出的欄序都算錯。"""
    got = {m["欄序"]: m["對應欄位"] for m in items["column_mapping"]}
    wrong = []
    for k in sorted(set(got) | set(expected)):
        exp = expected[k]["對應欄位"] if k in expected else None
        if got.get(k) != exp:
            name = expected[k]["原表欄位"] if k in expected else "?"
            wrong.append(f"| {k} | {name} | {got.get(k) or '（缺）'} | {exp or '（多出）'} |")
    return len(expected) - sum(1 for k in expected if got.get(k) != expected[k]["對應欄位"]), len(expected), wrong


def same(a, b) -> bool:
    return normalize(a or "") == normalize(b or "")


def pct(n: int, d: int) -> str:
    return f"{n}/{d}（{n / d:.0%}）" if d else "—"


def report_block(run_id: str, rid: str, rdir: Path, ann: dict, mapping: dict, summary: list) -> list[str]:
    out = [f"### {rid}", ""]
    tables_path = rdir / "03a_tables.json"
    if not tables_path.exists():
        out += [f"（沒有 03a_tables.json：S3a 未通過或未執行；{'有' if (rdir / '03a_tables.failed.json').exists() else '沒有'} 03a_tables.failed.json）", ""]
        return out
    tables = json.loads(tables_path.read_text(encoding="utf-8"))
    check = json.loads((rdir / "03a_table_check.json").read_text(encoding="utf-8"))
    items_path = rdir / "03_p1_items.json"
    items = json.loads(items_path.read_text(encoding="utf-8")) if items_path.exists() else None

    cols = dict(COLUMNS[rid])
    if items:
        for m in items["column_mapping"]:
            if m["對應欄位"] in ("類型", "子類別"):
                cols[m["對應欄位"]] = m["欄序"]
    boundary = {(b["PDF頁"], b["頁內列"]) for t in check["表格"] for b in t["邊界待確認"] if b["欄序"] in (cols["類型"], cols["子類別"])}

    rows = {(r["pdf_page"], r["row_on_page"]): r["cells"] for t in tables["tables"] for r in t["rows"]}
    keys = sorted(k for k in ann if k[0] == rid)
    missing = [k for k in keys if (k[1], k[2]) not in rows]
    extra = sorted(k for k in rows if (rid, *k) not in ann)

    wrong_a, ident_diff = [], []
    ok_type = ok_sub = ok_both_boundary = n_boundary = 0
    for k in keys:
        cells = rows.get((k[1], k[2]))
        if cells is None:
            continue
        a = ann[k]
        t_ok = same(cells[cols["類型"] - 1], a["類型原文"])
        s_ok = same(cells[cols["子類別"] - 1], a["子類別原文"])
        ok_type += t_ok
        ok_sub += s_ok
        if (k[1], k[2]) in boundary:
            n_boundary += 1
            ok_both_boundary += t_ok and s_ok
        if not (t_ok and s_ok):
            wrong_a.append(f"| p.{k[1]} 第 {k[2]} 列 | {a['事件識別']} | {cells[cols['類型'] - 1]}／{cells[cols['子類別'] - 1]} | {a['類型原文']}／{a['子類別原文']} |")
        if not same(cells[cols["事件識別"] - 1], a["事件識別"]):
            ident_diff.append(f"| p.{k[1]} 第 {k[2]} 列 | {cells[cols['事件識別'] - 1]} | {a['事件識別']} |")

    n = len(keys) - len(missing)
    out += [
        f"- 欄序：類型 {cols['類型']}、子類別 {cols['子類別']}（{'依 S3 column_mapping' if items else '依腳本預設'}）",
        f"- 重建表資料列 {len(rows)}；標註 {len(keys)} 列；標註有但重建表沒有：{[f'p.{p} 第 {r} 列' for _, p, r in missing] or '無'}；"
        f"重建表有但標註沒有：{[f'p.{p} 第 {r} 列' for p, r in extra] or '無'}",
        f"- S3a 類型原文正確：{pct(ok_type, n)}；子類別原文正確：{pct(ok_sub, n)}",
        f"- 邊界待確認列（類型或子類別欄）：{n_boundary} 列，兩欄皆正確 {pct(ok_both_boundary, n_boundary)}",
        "",
    ]
    if wrong_a:
        out += ["S3a 不正確的列：", "", "| 列 | 事件識別 | 重建表（類型／子類別） | 標註 |", "|---|---|---|---|", *wrong_a, ""]
    if ident_diff:
        out += ["事件識別文字與重建表不同（不計分）：", "", "| 列 | 重建表 | 標註 |", "|---|---|---|", *ident_diff, ""]

    if items:
        by_row = {(i["來源列"]["pdf_page"], i["來源列"]["row_on_page"]): i for i in items["items"]}
        ok = {"類型": 0, "子類別": 0, "子類別來源": 0}
        wrong_s = []
        for k in keys:
            it = by_row.get((k[1], k[2]))
            if it is None:
                continue
            a = ann[k]
            diffs = [f for f in ok if (it[f] or "") != (a[f] or "")]
            for f in ok:
                ok[f] += f not in diffs
            if diffs:
                wrong_s.append(f"| p.{k[1]} 第 {k[2]} 列 | {a['事件識別']} | " + "；".join(f"{f}：{it[f]}（標註 {a[f] or '空'}）" for f in diffs) + " |")
        out += [f"- S3 {'、'.join(f'{f}正確 {pct(v, n)}' for f, v in ok.items())}", ""]
        if rid in mapping:
            m_ok, m_n, m_wrong = mapping_score(items, mapping[rid])
            summary.append((run_id, rid, pct(m_ok, m_n)))
            out += [f"- S3 欄位對應正確 {pct(m_ok, m_n)}", ""]
            if m_wrong:
                out += ["欄位對應與預期不同：", "", "| 欄序 | 原表欄位 | S3 | 預期 |", "|---|---|---|---|", *m_wrong, ""]
        if wrong_s:
            out += ["S3 與標註不同的列：", "", "| 列 | 事件識別 | 差異 |", "|---|---|---|", *wrong_s, ""]
    else:
        out += ["（沒有 03_p1_items.json，未評估 S3）", ""]
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ann = load_annotations()
    mapping = load_mapping()
    body: list[str] = []
    summary: list[tuple[str, str, str]] = []
    modes: dict[str, str] = {}
    for run_id in sys.argv[1:]:
        rdir = ROOT / "runs" / run_id
        run = json.loads((rdir / "run.json").read_text(encoding="utf-8"))
        mode = modes[run_id] = run["settings"]["extract"]["input_mode"]
        s3 = run["settings"]["prompts"].get("s3_extract")
        body += [f"## run {run_id}（S3a 模式：{mode}；s3_extract v{s3}；commit {str(run['git']['commit'])[:10]}，未提交修改 {run['git']['dirty']}）", ""]
        for rid in run["selected_reports"]:
            body += report_block(run_id, rid, rdir / rid, ann, mapping, summary)
    lines = ["# M3 表格評估", "", f"標註檔：{ANNOTATIONS.relative_to(ROOT)}、{MAPPING.relative_to(ROOT)}（使用者已逐列核對；欄位對應依 SPEC 7.5）", "", LIMITATION, ""]
    if summary:
        lines += ["欄位對應正確率（依模式分列）：", "", "| run | 模式 | 報告書 | 欄位對應正確 |", "|---|---|---|---|"]
        lines += [f"| {r} | {modes[r]} | {rid} | {v} |" for r, rid, v in summary] + [""]
    lines += body
    out = ROOT / "runs" / sys.argv[1] / "eval_tables.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
