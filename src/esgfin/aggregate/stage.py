"""S7 彙整與指標（SPEC 11、17.5、17.6、17.8、17.12）：→ 07_final.json、07_metrics.json；全部報告書 → runs/<run_id>/metrics.json。

推理狀態：沒有有效 P2 檔＝推理失敗（優先）；S4 或 S6 有嚴重問題＝檢查未通過；其餘＝成功。
所有比例保留分子分母；總計以合併計數計算（分子總和 ÷ 分母總和），不取平均。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from esgfin.constants import (
    DIRECTION_NONE,
    DIRECTIONS_CERTAIN,
    IMPACT_NO,
    IMPACT_YES,
    P1_CHECK_RULES,
    P2_CHECK_RULES,
    REASONING_CHECK_FAILED,
    REASONING_FAILED,
    REASONING_OK,
    TOP_N_PER_STATEMENT,
)
from esgfin.llm.service import LLMCallRecord
from esgfin.pipeline.context import ReportContext
from esgfin.pipeline.paths import ProjectPaths
from esgfin.pipeline.record import RunRecord, StageRecord
from esgfin.pipeline.stages import REPORT_STAGES, p2_item_path, p2_item_valid
from esgfin.schemas.base import read_jsonl, read_model, write_model
from esgfin.schemas.check import CheckReport, CitationCheck, P1Check
from esgfin.schemas.final import (
    CitationStats,
    FinalItem,
    FinalReport,
    Metrics,
    Ratio,
    StageUsage,
    TopAccounts,
)
from esgfin.schemas.meta import ReportMeta
from esgfin.schemas.p1 import P1Item, P1Items
from esgfin.schemas.p2 import P2Result

ALL_SCOPE = "全部"


# ---------------------------------------------------------------- 07_final.json


def top_accounts(p2: P2Result) -> TopAccounts:
    """兩表各取排序前三（列表順序＝排序；不連續時 S6 已標嚴重，條目不會顯示）。空的表為 None。"""
    bs, inc = p2.accounts.balance_sheet[:TOP_N_PER_STATEMENT], p2.accounts.income_statement[:TOP_N_PER_STATEMENT]
    return TopAccounts(balance_sheet=bs or None, income_statement=inc or None)


def final_item(item: P1Item, p2: P2Result | None, severe: set[str]) -> FinalItem:
    base = item.model_dump()
    if p2 is None:
        return FinalItem(**base, reasoning_status=REASONING_FAILED)
    common = dict(overall=p2.overall, no_impact_reason=p2.no_impact_reason)
    if item.code in severe:
        return FinalItem(**base, reasoning_status=REASONING_CHECK_FAILED, **common)
    if p2.overall == IMPACT_NO:
        return FinalItem(**base, reasoning_status=REASONING_OK, top_accounts=IMPACT_NO, direction=DIRECTION_NONE, **common)
    return FinalItem(**base, reasoning_status=REASONING_OK, top_accounts=top_accounts(p2), **common)


# ---------------------------------------------------------------- 指標


def citation_stats(cits: list[CitationCheck]) -> CitationStats:
    c = Counter(x.level for x in cits)
    return CitationStats(exact=c["完全通過"], fuzzy=c["模糊通過"], failed=c["未通過"])


def issue_ratios(issues, rules: dict[str, str], denominator: int, codes: set[str]) -> dict[str, Ratio]:
    """每條規則：分子＝有該問題的條目數（同條目多次只計一次）；只計 codes 內的條目。列出分級表的全部規則（含 0）。"""
    hit: dict[str, set[str]] = {r: set() for r in rules}
    for i in issues:
        if i.code in codes:
            hit[i.rule].add(i.code)
    return {r: Ratio(numerator=len(hit[r]), denominator=denominator) for r in rules}


def account_metrics(results: list[P2Result]) -> dict[str, Ratio]:
    """確定比例（全部、前三）與「證據等級 C 且原文依據 null」比例；只計「有影響」條目（不論是否檢查未通過）。"""
    all_rows, top_rows = [], []
    for p2 in results:
        if p2.overall != IMPACT_YES:
            continue
        for rows in (p2.accounts.balance_sheet, p2.accounts.income_statement):
            all_rows += rows
            top_rows += rows[:TOP_N_PER_STATEMENT]

    def certain(rows) -> Ratio:
        return Ratio(numerator=sum(r.direction in DIRECTIONS_CERTAIN for r in rows), denominator=len(rows))

    c_null = sum(r.evidence_grade == "C" and r.basis_quote is None for r in all_rows)
    return {
        "all": certain(all_rows),
        "top": certain(top_rows),
        "c_null": Ratio(numerator=c_null, denominator=len(all_rows)),
    }


def _load_run(paths: ProjectPaths, run_id: str) -> RunRecord | None:
    try:
        return read_model(paths.run_dir(run_id) / "run.json", RunRecord)
    except (OSError, ValidationError, ValueError):
        return None


def stage_origin(paths: ProjectPaths, run_id: str, report_id: str, stage: str) -> tuple[str, StageRecord] | None:
    """階段實際執行的 run：沿著 --from-stage 的來源 run 往回找，直到該階段不是「copied」。"""
    seen = set()
    while run_id and run_id not in seen:
        seen.add(run_id)
        run = _load_run(paths, run_id)
        if run is None or report_id not in run.reports:
            return None
        srec = run.reports[report_id].stages.get(stage)
        if srec is None:
            return None
        if srec.status != "copied":
            return run_id, srec
        run_id = run.source_run_id
    return None


def usage_metrics(paths: ProjectPaths, run_id: str, report_id: str) -> tuple[dict[str, StageUsage], Ratio, int, int]:
    """S0–S6 的耗時與 token（取實際執行該階段的 run），以及 JSON 首次通過率、驗證重試、連線重試（llm_calls.jsonl）。"""
    stages: dict[str, StageUsage] = {}
    requests = first_ok = retries = transient = 0
    for spec in REPORT_STAGES:
        if spec.key == "aggregate":
            continue  # S7 自己尚未結束
        found = stage_origin(paths, run_id, report_id, spec.key)
        if found is None:
            continue
        origin, srec = found
        stages[f"{spec.code} {spec.label}"] = StageUsage(
            duration_s=srec.duration_s, input_tokens=srec.input_tokens, output_tokens=srec.output_tokens, thought_tokens=srec.thought_tokens,
        )
        if not spec.uses_llm:
            continue
        log = paths.run_report_dir(origin, report_id) / "llm_calls.jsonl"
        recs = [r for r in read_jsonl(log, LLMCallRecord) if r.tag.get("stage") == spec.key] if log.exists() else []
        firsts = [r for r in recs if r.validation_attempt == 0]
        requests += len(firsts)
        first_ok += sum(r.ok for r in firsts)
        retries += sum(r.validation_attempt > 0 for r in recs)
        transient += sum(r.transient_retries for r in recs)
    return stages, Ratio(numerator=first_ok, denominator=requests), retries, transient


def report_metrics(ctx: ReportContext, items: list[P1Item], results: dict[str, P2Result], p1check: P1Check, check: CheckReport) -> Metrics:
    p2_codes = set(results)
    all_codes = {it.code for it in items}
    severe = (p1check.severe_codes() | check.severe_codes()) & p2_codes
    no_impact = Counter(p2.no_impact_reason or "未填理由類型" for p2 in results.values() if p2.overall == IMPACT_NO)
    gaps = Counter(g.gap_type for p2 in results.values() for g in p2.understanding.gaps)
    acc = account_metrics(list(results.values()))
    stages, first_pass, retries, transient = usage_metrics(ctx.paths, ctx.run_id, ctx.report_id)
    return Metrics(
        scope=ctx.report_id,
        item_count=len(items),
        no_impact_by_reason=dict(no_impact),
        reasoning_failed=len(all_codes - p2_codes),
        inconsistent=Ratio(numerator=len(severe), denominator=len(p2_codes)),
        issues_p1=issue_ratios(p1check.issues, P1_CHECK_RULES, len(items), all_codes),
        issues_p2=issue_ratios(check.issues, P2_CHECK_RULES, len(p2_codes), p2_codes),
        gap_type_counts=dict(gaps),
        certainty_all=acc["all"],
        certainty_top=acc["top"],
        grade_c_null_quote=acc["c_null"],
        citation_p1=citation_stats(p1check.citations),
        citation_p2=citation_stats(check.citations),
        json_first_pass=first_pass,
        retries=retries,
        transient_retries=transient,
        stages=stages,
    )


def pooled_metrics(metrics: list[Metrics]) -> Metrics:
    """全部報告書的總計：計數相加、比例以合併計數重算（SPEC 17.6 第 2 點）。"""
    def add_counts(dicts: list[dict[str, int]]) -> dict[str, int]:
        c: Counter = Counter()
        for d in dicts:
            c.update(d)
        return dict(c)

    stage_keys = list(dict.fromkeys(k for m in metrics for k in m.stages))
    stages = {
        k: StageUsage(
            duration_s=round(sum(m.stages[k].duration_s for m in metrics if k in m.stages), 3),
            input_tokens=sum(m.stages[k].input_tokens for m in metrics if k in m.stages),
            output_tokens=sum(m.stages[k].output_tokens for m in metrics if k in m.stages),
            thought_tokens=sum(m.stages[k].thought_tokens for m in metrics if k in m.stages),
        )
        for k in stage_keys
    }
    return Metrics(
        scope=ALL_SCOPE,
        item_count=sum(m.item_count for m in metrics),
        no_impact_by_reason=add_counts([m.no_impact_by_reason for m in metrics]),
        reasoning_failed=sum(m.reasoning_failed for m in metrics),
        inconsistent=Ratio.pooled([m.inconsistent for m in metrics]),
        issues_p1=Ratio.pooled_by_key([m.issues_p1 for m in metrics]),
        issues_p2=Ratio.pooled_by_key([m.issues_p2 for m in metrics]),
        gap_type_counts=add_counts([m.gap_type_counts for m in metrics]),
        certainty_all=Ratio.pooled([m.certainty_all for m in metrics]),
        certainty_top=Ratio.pooled([m.certainty_top for m in metrics]),
        grade_c_null_quote=Ratio.pooled([m.grade_c_null_quote for m in metrics]),
        citation_p1=CitationStats.pooled([m.citation_p1 for m in metrics]),
        citation_p2=CitationStats.pooled([m.citation_p2 for m in metrics]),
        json_first_pass=Ratio.pooled([m.json_first_pass for m in metrics]),
        retries=sum(m.retries for m in metrics),
        transient_retries=sum(m.transient_retries for m in metrics),
        stages=stages,
    )


def write_run_metrics(paths: ProjectPaths, run_id: str, report_ids: list[str]) -> Path | None:
    """runs/<run_id>/metrics.json：本 run 內有 07_metrics.json 的報告書合併計算。沒有任何一份時不寫。"""
    found = []
    for rid in report_ids:
        f = paths.run_report_dir(run_id, rid) / "07_metrics.json"
        if f.exists():
            found.append(read_model(f, Metrics))
    if not found:
        return None
    out = paths.run_dir(run_id) / "metrics.json"
    write_model(out, pooled_metrics(found))
    return out


# ---------------------------------------------------------------- 階段


def run_aggregate(ctx: ReportContext) -> None:
    rdir = ctx.report_dir
    meta = read_model(rdir / "00_meta.json", ReportMeta)
    items = read_model(rdir / "03_p1_items.json", P1Items).items
    p1check = read_model(rdir / "04_p1_check.json", P1Check)
    check = read_model(rdir / "06_check_report.json", CheckReport)
    results = {
        it.code: read_model(p2_item_path(rdir, it.code), P2Result)
        for it in items
        if p2_item_valid(p2_item_path(rdir, it.code))
    }
    severe = p1check.severe_codes() | check.severe_codes()
    final = FinalReport(
        report_id=ctx.report_id,
        run_id=ctx.run_id,
        generated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        meta=meta,
        items=[final_item(it, results.get(it.code), severe) for it in items],
    )
    write_model(rdir / "07_final.json", final)
    write_model(rdir / "07_metrics.json", report_metrics(ctx, items, results, p1check, check))

