"""S3a 表格重建階段（SPEC 7.6）：策略表 → 03a_tables.json、03a_table_check.json。

一張策略表（可能跨頁）一次 LLM 呼叫；程式以 Docling 格子檢查，未通過就附檢查錯誤重問，
次數用 llm.validation_retries。仍未通過時該報告書停在 S3a（不帶著錯誤的表格繼續跑）。
有 overrides/<report_id>/03a_tables.json 時不呼叫 LLM，但仍跑同樣的檢查。
"""

from __future__ import annotations

import json

from esgfin.constants import REPORT_STATUS_FAILED
from esgfin.llm.base import DocumentPart, LLMRequest, TextPart
from esgfin.llm.service import llm_schema
from esgfin.pdfpages import pdf_bytes, raw_page_text
from esgfin.pipeline.context import ReportContext
from esgfin.pipeline.errors import ReportStop
from esgfin.pipeline.prompts import load_prompt
from esgfin.schemas.base import read_jsonl, read_model, write_model
from esgfin.schemas.manifest import Manifest
from esgfin.schemas.pages import PageRecord
from esgfin.schemas.tables import (
    AttemptCheck,
    ReconstructedTable,
    ReconstructedTables,
    TableCheckReport,
    TableCheckResult,
    TableDraft,
)

from .checks import CheckOutcome, check_table, problems_text
from .render import page_block

PROMPT = "s3a_table"
TABLES_FILE = "03a_tables.json"
CHECK_FILE = "03a_table_check.json"
FAILED_FILE = "03a_tables.failed.json"  # 未通過檢查的最後一版，供人工參考（不是階段輸出）

RETRY_TEMPLATE = (
    "你上一次輸出的表格沒有通過程式檢查。上一次的輸出：\n{previous}\n\n檢查錯誤：\n{errors}\n\n"
    "請對照 PDF 頁面與 Docling 格子表重新輸出完整的表格（同一個 schema），不要只輸出修正的部分。"
)


def _result(table: ReconstructedTable, outcome: CheckOutcome, attempts: list[AttemptCheck]) -> TableCheckResult:
    return TableCheckResult(
        pages=table.pages, passed=outcome.passed, attempts=attempts, page_checks=outcome.page_checks,
        fuzzy_cells=outcome.fuzzy, boundary_rows=outcome.boundary,
    )


def _reconstruct(ctx: ReportContext, pages_wanted: list[int], pages: dict[int, PageRecord], raw: dict[int, str]) -> tuple[ReconstructedTable, TableCheckResult]:
    mode = ctx.settings.extract.input_mode
    ratio = ctx.settings.check.fuzzy_pass_ratio
    info = ctx.prompts[PROMPT]
    intro = (
        f"報告書：{ctx.report.company_full_name} {ctx.report.report_year} 年永續報告書\n"
        f"策略表所在的 PDF 頁：{pages_wanted}（輸出的 pdf_page 只能是這些頁）\n"
        f"輸入模式：{'文字＋PDF 頁面' if mode == 'text+pdf' else '只有文字（沒有 PDF 頁面）'}"
    )
    parts: list = [TextPart(intro), TextPart("\n\n".join(page_block(pages[n]) for n in pages_wanted))]
    if mode == "text+pdf":
        parts.append(DocumentPart(pdf_bytes(ctx.pdf_path, pages_wanted), label=f"{ctx.report_id} p{pages_wanted}"))
    base = LLMRequest(PROMPT, info.version, load_prompt(ctx.paths.root, info), tuple(parts), llm_schema(TableDraft))

    attempts: list[AttemptCheck] = []
    request = base
    retries = ctx.settings.llm.validation_retries
    for attempt in range(retries + 1):
        res = ctx.llm.structured(request, TableDraft, tag=ctx.llm_tag(pages=",".join(map(str, pages_wanted)), attempt=str(attempt)))
        if res.value is None:
            raise RuntimeError(f"S3a：LLM 未能產生符合 schema 的表格（{res.error}）")
        d = res.value
        table = ReconstructedTable(pages=pages_wanted, header=d.header, rows=d.rows, merge_notes=d.merge_notes)
        outcome = check_table(table, pages, raw, ratio)
        attempts.append(AttemptCheck(attempt=attempt, passed=outcome.passed, problems=outcome.problems))
        if outcome.passed or attempt == retries:
            break
        previous = json.dumps(d.model_dump(mode="json"), ensure_ascii=False)
        request = LLMRequest(
            base.prompt_name, base.prompt_version, base.system_instruction,
            base.parts + (TextPart(RETRY_TEMPLATE.format(previous=previous, errors=problems_text(outcome.problems))),),
            base.response_schema,
        )
    if ctx.stage_record is not None:
        ctx.stage_record.check_retries = (ctx.stage_record.check_retries or 0) + len(attempts) - 1
    return table, _result(table, outcome, attempts)


def run_tables(ctx: ReportContext) -> None:
    manifest = read_model(ctx.report_dir / "02_manifest.json", Manifest)
    pages = {p.pdf_page: p for p in read_jsonl(ctx.report_dir / "01_pages.jsonl", PageRecord)}
    ratio = ctx.settings.check.fuzzy_pass_ratio
    wanted = sorted({n for t in manifest.strategy_tables for n in t.pdf_pages})
    raw = raw_page_text(ctx.pdf_path, wanted)

    override = ctx.paths.override_dir(ctx.report_id) / TABLES_FILE
    if override.exists():
        tables = read_model(override, ReconstructedTables)
        results = []
        for t in tables.tables:
            outcome = check_table(t, pages, raw, ratio)
            results.append(_result(t, outcome, [AttemptCheck(attempt=0, passed=outcome.passed, problems=outcome.problems)]))
        mode = "override"
    else:
        mode = ctx.settings.extract.input_mode
        built = [_reconstruct(ctx, t.pdf_pages, pages, raw) for t in manifest.strategy_tables]
        tables = ReconstructedTables(report_id=ctx.report_id, input_mode=mode, tables=[t for t, _ in built])
        results = [r for _, r in built]

    report = TableCheckReport(report_id=ctx.report_id, input_mode=mode, passed=all(r.passed for r in results), tables=results)
    write_model(ctx.report_dir / CHECK_FILE, report)
    if not report.passed:
        (ctx.report_dir / TABLES_FILE).unlink(missing_ok=True)
        write_model(ctx.report_dir / FAILED_FILE, tables)
        failed = [r for r in results if not r.passed]
        detail = problems_text(failed[0].attempts[-1].problems).splitlines()
        raise ReportStop(
            REPORT_STATUS_FAILED,
            f"S3a 表格重建未通過程式檢查（{len(failed)} 張表；詳見 {CHECK_FILE}，最後一版表格在 {FAILED_FILE}）。"
            f"第一個問題：{detail[0] if detail else '—'}。"
            f"可把人工整理的表格放在 overrides/{ctx.report_id}/{TABLES_FILE} 後用 --from-stage tables 重跑",
        )
    (ctx.report_dir / FAILED_FILE).unlink(missing_ok=True)
    write_model(ctx.report_dir / TABLES_FILE, tables)
