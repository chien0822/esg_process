"""S6 檢查模組（SPEC 10、17.5、17.8、17.12）：05_p2/*.json → 06_check_report.json。只標記，不修改 LLM 輸出。

S6 只檢查 P2，不重複 S4；引文驗證與 S4 共用 check/citation.match_quote。
推理失敗（只有 .failed.json）或沒有有效 P2 檔的條目不檢查，由 S7 標為推理失敗。
"""

from __future__ import annotations

from esgfin.constants import (
    ACCOUNTS_BY_STATEMENT,
    ALL_ACCOUNT_NAMES,
    BALANCE_SHEET,
    CITATION_EXACT,
    CITATION_FAIL,
    DIRECTION_NONE,
    EVIDENCE_GRADES_REQUIRING_QUOTE,
    EXCLUDED_ITEMS,
    IMPACT_NO,
    INCOME_STATEMENT,
    NO_IMPACT_REASONS,
    P2_CHECK_RULES,
    RULE_ACCOUNT_DUPLICATE,
    RULE_ACCOUNT_EXCLUDED,
    RULE_ACCOUNT_NOT_IN_LIST,
    RULE_ACCOUNT_WRONG_STATEMENT,
    RULE_ALTERNATIVE_EQUALS_MAIN,
    RULE_CITATION_FAIL,
    RULE_CITATION_FUZZY,
    RULE_DIRECTION_NONE,
    RULE_EXPLANATION_EMPTY,
    RULE_INSUFFICIENT_WITHOUT_GAP,
    RULE_JUDGEMENT_REASON_MISMATCH,
    RULE_JUDGEMENT_TABLE_MISMATCH,
    RULE_PREMISE_MISSING,
    RULE_QUOTE_MISSING_FOR_AB,
    RULE_RANK_NOT_CONTINUOUS,
    STATEMENTS,
)
from esgfin.pipeline.context import ReportContext
from esgfin.pipeline.stages import p2_item_path, p2_item_valid
from esgfin.schemas.base import read_jsonl, read_model, write_model
from esgfin.schemas.check import CheckIssue, CheckReport, CitationCheck
from esgfin.schemas.p1 import P1Item, P1Items
from esgfin.schemas.p2 import P2Result
from esgfin.schemas.pages import PageRecord

from .citation import match_quote

INSUFFICIENT = NO_IMPACT_REASONS[1]  # 「描述不足以建立傳導路徑」
UNDERSTANDING_FIELDS = (("what", "事件是什麼"), ("where", "影響公司哪一塊"), ("occurrence", "已發生或預期"),
                        ("quantitative", "量化線索"), ("response", "公司因應"))


def _issue(code: str, rule: str, message: str, field: str | None = None) -> CheckIssue:
    return CheckIssue(code=code, rule=rule, severity=P2_CHECK_RULES[rule], field=field, message=message)


def _blank(s: str | None) -> bool:
    return s is None or not s.strip()


def statement_rows(p2: P2Result) -> dict[str, list]:
    return {BALANCE_SHEET: p2.accounts.balance_sheet, INCOME_STATEMENT: p2.accounts.income_statement}


def check_rules(p2: P2Result) -> list[CheckIssue]:
    """10.1 的結構與跨欄位規則（不含引文驗證）。"""
    code = p2.code
    out: list[CheckIssue] = []
    tables = statement_rows(p2)
    all_empty = not any(tables.values())

    # 整體判斷 ⇔ 表格；整體判斷 ⇔ 無影響理由類型
    if (p2.overall == IMPACT_NO) != all_empty:
        detail = "整體判斷為無影響但仍列出科目" if p2.overall == IMPACT_NO else "整體判斷為有影響但兩張表皆為空"
        out.append(_issue(code, RULE_JUDGEMENT_TABLE_MISMATCH, detail, "整體判斷"))
    if (p2.overall == IMPACT_NO) != (p2.no_impact_reason is not None):
        detail = ("整體判斷為無影響但無影響理由類型為 null" if p2.overall == IMPACT_NO
                  else f"整體判斷為有影響但無影響理由類型為「{p2.no_impact_reason}」")
        out.append(_issue(code, RULE_JUDGEMENT_REASON_MISMATCH, detail, "無影響理由類型"))
    if p2.no_impact_reason == INSUFFICIENT and not p2.understanding.gaps:
        out.append(_issue(code, RULE_INSUFFICIENT_WITHOUT_GAP, f"無影響理由類型為「{INSUFFICIENT}」但缺口列表為空", "理解紀錄.缺口"))

    for i, ch in enumerate(p2.channels, 1):
        if _blank(ch.explanation):
            out.append(_issue(code, RULE_EXPLANATION_EMPTY, f"傳導管道第 {i} 條的傳導說明空白", f"傳導管道[{i}].傳導說明"))

    for table in STATEMENTS:
        rows = tables[table]
        seen: set[str] = set()
        ranks = [r.rank for r in rows]
        if ranks != list(range(1, len(rows) + 1)):
            out.append(_issue(code, RULE_RANK_NOT_CONTINUOUS, f"{table}的排序為 {ranks}，應為 1 起連續且與列表順序一致", f"{table}.排序"))
        for i, r in enumerate(rows, 1):
            acc = r.account
            where = f"{table}[{i}]"
            if acc in EXCLUDED_ITEMS:
                out.append(_issue(code, RULE_ACCOUNT_EXCLUDED, f"科目「{acc}」是排除項目", f"{where}.科目"))
            elif acc not in ALL_ACCOUNT_NAMES:
                out.append(_issue(code, RULE_ACCOUNT_NOT_IN_LIST, f"科目「{acc}」不在 19 項會計科目清單", f"{where}.科目"))
            elif acc not in ACCOUNTS_BY_STATEMENT[table]:
                out.append(_issue(code, RULE_ACCOUNT_WRONG_STATEMENT, f"科目「{acc}」不屬於{table}", f"{where}.科目"))
            if acc in seen:
                out.append(_issue(code, RULE_ACCOUNT_DUPLICATE, f"{table}內科目「{acc}」重複", f"{where}.科目"))
            seen.add(acc)
            if r.direction == DIRECTION_NONE:
                out.append(_issue(code, RULE_DIRECTION_NONE, f"科目「{acc}」的影響方向為「{DIRECTION_NONE}」", f"{where}.影響方向"))
            if _blank(r.explanation):
                out.append(_issue(code, RULE_EXPLANATION_EMPTY, f"科目「{acc}」的傳導說明空白", f"{where}.傳導說明"))
            if r.evidence_grade in EVIDENCE_GRADES_REQUIRING_QUOTE and _blank(r.basis_quote):
                out.append(_issue(code, RULE_QUOTE_MISSING_FOR_AB, f"科目「{acc}」證據等級 {r.evidence_grade} 但原文依據為空", f"{where}.原文依據"))
            if _blank(r.basis_quote) and _blank(r.inference_premise):
                out.append(_issue(code, RULE_PREMISE_MISSING, f"科目「{acc}」原文依據為空且推論前提為空", f"{where}.推論前提"))
            if acc in r.alternatives:
                out.append(_issue(code, RULE_ALTERNATIVE_EQUALS_MAIN, f"科目「{acc}」的替代科目包含主科目本身", f"{where}.替代科目"))
    return out


def quotes_of(p2: P2Result) -> list[tuple[str, str]]:
    """所有非 null 的引文：(欄位, 引文)。null 不驗證（10.1）。"""
    out = []
    for attr, label in UNDERSTANDING_FIELDS:
        q = getattr(p2.understanding, attr).quote
        if q is not None:
            out.append((f"理解紀錄.{label}.原文引用", q))
    for table, rows in statement_rows(p2).items():
        for i, r in enumerate(rows, 1):
            if r.basis_quote is not None:
                out.append((f"{table}[{i}]（{r.account}）.原文依據", r.basis_quote))
    return out


def check_citations(p2: P2Result, item: P1Item, pages: dict[int, str], buffer: int, ratio: float) -> tuple[list[CitationCheck], list[CheckIssue]]:
    searched = sorted({n for p in item.pages for n in range(p - buffer, p + buffer + 1) if n in pages})
    text = "\n".join(pages[n] for n in searched)
    cits, issues = [], []
    for field, quote in quotes_of(p2):
        m = match_quote(quote, text, ratio)
        cits.append(CitationCheck(code=p2.code, field=field, quote=quote, pages_searched=searched, level=m.level, ratio=m.ratio))
        if m.level != CITATION_EXACT:
            rule = RULE_CITATION_FAIL if m.level == CITATION_FAIL else RULE_CITATION_FUZZY
            issues.append(_issue(p2.code, rule, f"{field} 引文{m.level}（比例 {m.ratio:.2f}）", field))
    return cits, issues


def run_p2check(ctx: ReportContext) -> None:
    items = read_model(ctx.report_dir / "03_p1_items.json", P1Items).items
    pages = {p.pdf_page: p.text_md for p in read_jsonl(ctx.report_dir / "01_pages.jsonl", PageRecord)}
    buffer = ctx.settings.check.citation_page_buffer
    ratio = ctx.settings.check.fuzzy_pass_ratio
    issues: list[CheckIssue] = []
    citations: list[CitationCheck] = []
    for item in items:
        path = p2_item_path(ctx.report_dir, item.code)
        if not p2_item_valid(path):
            continue
        p2 = read_model(path, P2Result)
        issues += check_rules(p2)
        cits, cit_issues = check_citations(p2, item, pages, buffer, ratio)
        citations += cits
        issues += cit_issues
    write_model(ctx.report_dir / "06_check_report.json", CheckReport(report_id=ctx.report_id, issues=issues, citations=citations))
