"""S4 程式完整度檢查（SPEC 8、10.2、17.8）：03_p1_items.json → 04_p1_check.json。只標記，不修改。

- 空值紀錄（不分級）
- 引文驗證：原文內容對該條目頁碼（含緩衝）的 text_md
- 規則：子類別與類型不符；引文不在對應列；子類別原文用語與對應列不符；子類別來源與同義詞表矛盾
"""

from __future__ import annotations

from esgfin.constants import (
    CITATION_EXACT,
    CITATION_FAIL,
    FIELD_SUBCATEGORY,
    P1_CHECK_RULES,
    RULE_CITATION_FAIL,
    RULE_CITATION_FUZZY,
    RULE_QUOTE_NOT_IN_ROW,
    RULE_SUBCATEGORY_MISMATCH,
    RULE_SUBCATEGORY_RAW_MISMATCH,
    RULE_SUBCATEGORY_SYNONYM_CONFLICT,
    SUBCATEGORIES,
    SUBCATEGORY_SOURCE_ORIGINAL,
    SUBCATEGORY_SYNONYMS,
)
from esgfin.pipeline.context import ReportContext
from esgfin.schemas.base import read_jsonl, read_model, write_model
from esgfin.schemas.check import CheckIssue, CitationCheck, NullFields, P1Check
from esgfin.schemas.p1 import P1Item, P1Items
from esgfin.schemas.pages import PageRecord
from esgfin.schemas.tables import ReconstructedTables, TableRow
from esgfin.textnorm import normalize

from .citation import match_quote

SOURCE_FIELDS = (("event_description", "事件描述"), ("financial_impact", "顯示財務Impact"), ("response_note", "因應措施備註"))
_SYNONYMS = {normalize(k): v for k, v in SUBCATEGORY_SYNONYMS.items()}


def _issue(item: P1Item, rule: str, message: str, field: str | None = None) -> CheckIssue:
    return CheckIssue(code=item.code, rule=rule, severity=P1_CHECK_RULES[rule], field=field, message=message)


def null_fields(item: P1Item) -> list[str]:
    data = item.model_dump(by_alias=True)
    out = [k for k, v in data.items() if v is None]
    out += [f"原文內容.{k}" for k, v in data["原文內容"].items() if v is None]
    return out


def synonym_of(raw: str | None) -> str | None:
    return _SYNONYMS.get(normalize(raw)) if raw else None


def check_rules(item: P1Item, row: TableRow | None, page_rows: list[TableRow], sub_col: int | None) -> list[CheckIssue]:
    """sub_col：子類別欄在重建表的欄序（0 起算）；原表沒有子類別欄時為 None。"""
    issues = []
    if item.subcategory is not None and item.subcategory not in SUBCATEGORIES[item.item_type]:
        issues.append(_issue(item, RULE_SUBCATEGORY_MISMATCH, f"子類別「{item.subcategory}」不屬於類型「{item.item_type}」", "子類別"))

    if row is None:
        issues.append(_issue(item, RULE_QUOTE_NOT_IN_ROW, f"重建表沒有來源列（PDF 第 {item.source_row.pdf_page} 頁第 {item.source_row.row_on_page} 列）", "來源列"))
        return issues

    row_text = normalize("".join(c for c in row.cells if c))
    for attr, label in SOURCE_FIELDS:
        quote = getattr(item.source_text, attr)
        if not quote or normalize(quote) in row_text:
            continue
        others = [r.row_on_page for r in page_rows if r is not row and normalize(quote) in normalize("".join(c for c in r.cells if c))]
        where = f"出現在同頁第 {'、'.join(map(str, others))} 列（錯位）" if others else "也不在同頁其他列"
        issues.append(_issue(item, RULE_QUOTE_NOT_IN_ROW, f"原文內容.{label} 不在來源列（第 {row.row_on_page} 列）的格子中；{where}", f"原文內容.{label}"))

    expected_raw = row.cells[sub_col] if sub_col is not None and sub_col < len(row.cells) else None
    raw = item.subcategory_raw
    if sub_col is None:
        if raw is not None:
            issues.append(_issue(item, RULE_SUBCATEGORY_RAW_MISMATCH, f"原表沒有子類別欄，子類別原文用語應為 null，實際「{raw}」", "子類別原文用語"))
    elif normalize(raw or "") != normalize(expected_raw or ""):
        issues.append(_issue(item, RULE_SUBCATEGORY_RAW_MISMATCH, f"子類別原文用語「{raw}」與來源列子類別欄「{expected_raw}」不符", "子類別原文用語"))

    target = synonym_of(raw)
    if item.subcategory_source == SUBCATEGORY_SOURCE_ORIGINAL and target is not None and item.subcategory != target:
        issues.append(
            _issue(item, RULE_SUBCATEGORY_SYNONYM_CONFLICT, f"子類別原文用語「{raw}」在同義詞表中對應「{target}」，但子類別為「{item.subcategory}」", "子類別")
        )
    return issues


def run_p1check(ctx: ReportContext) -> None:
    items_file = read_model(ctx.report_dir / "03_p1_items.json", P1Items)
    tables = read_model(ctx.report_dir / "03a_tables.json", ReconstructedTables)
    pages = {p.pdf_page: p for p in read_jsonl(ctx.report_dir / "01_pages.jsonl", PageRecord)}
    buffer = ctx.settings.check.citation_page_buffer
    ratio = ctx.settings.check.fuzzy_pass_ratio

    rows = {(r.pdf_page, r.row_on_page): r for t in tables.tables for r in t.rows}
    sub_cols = [m.column - 1 for m in items_file.column_mapping if m.mapped_field == FIELD_SUBCATEGORY]
    sub_col = sub_cols[0] if sub_cols else None

    nulls, issues, citations = [], [], []
    for item in items_file.items:
        nulls.append(NullFields(code=item.code, null_fields=null_fields(item)))
        searched = sorted({n for p in item.pages for n in range(p - buffer, p + buffer + 1) if n in pages})
        text = "\n".join(pages[n].text_md for n in searched)
        for attr, label in SOURCE_FIELDS:
            quote = getattr(item.source_text, attr)
            if not quote:
                continue
            m = match_quote(quote, text, ratio)
            citations.append(CitationCheck(code=item.code, field=f"原文內容.{label}", quote=quote, pages_searched=searched, level=m.level, ratio=m.ratio))
            if m.level != CITATION_EXACT:
                rule = RULE_CITATION_FAIL if m.level == CITATION_FAIL else RULE_CITATION_FUZZY
                issues.append(_issue(item, rule, f"原文內容.{label} 引文{m.level}（比例 {m.ratio:.2f}）", f"原文內容.{label}"))
        row = rows.get((item.source_row.pdf_page, item.source_row.row_on_page))
        page_rows = [r for (p, _), r in rows.items() if p == item.source_row.pdf_page]
        issues += check_rules(item, row, page_rows, sub_col)

    write_model(ctx.report_dir / "04_p1_check.json", P1Check(report_id=ctx.report_id, items=nulls, issues=issues, citations=citations))
