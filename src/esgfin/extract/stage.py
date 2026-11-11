"""S3 方法一擷取（SPEC 7）：03a_tables.json → 03_p1_items.json。

一張重建表一次呼叫，只給重建表的文字（不給 PDF、不給 text_md），另給同義詞表、封閉清單與 TCFD 例句參考檔。
一列一條目：條目數與 column_mapping 欄數由 pydantic 驗證（不符即附錯誤重問）。
代碼、方法、來源列、頁碼由程式依條目順序填入。
"""

from __future__ import annotations

from esgfin.constants import (
    FIELD_UNUSED,
    IMPACT_SCOPES,
    LEVELS,
    METHOD_1,
    P1_MAPPABLE_FIELDS,
    SUBCATEGORIES,
    SUBCATEGORY_SOURCES,
    SUBCATEGORY_SYNONYMS,
    TIME_HORIZONS,
)
from esgfin.llm.base import LLMRequest, TextPart
from esgfin.llm.service import llm_schema
from esgfin.pipeline.context import ReportContext
from esgfin.pipeline.prompts import load_prompt
from esgfin.schemas.base import read_model, write_model
from esgfin.schemas.meta import ReportMeta
from esgfin.schemas.p1 import P1Extraction, P1Item, P1Items, SourceRow, constrained_extraction, make_item_code
from esgfin.schemas.tables import ReconstructedTable, ReconstructedTables
from esgfin.tables.render import reconstructed_text

PROMPT = "s3_extract"
REF_TCFD = "ref_tcfd_examples"


def closed_lists_text() -> str:
    """封閉清單與同義詞表（皆來自 constants.py）。"""
    lines = ["【子類別封閉清單（依類型）】"]
    lines += [f"- {t}：{'、'.join(subs)}" for t, subs in SUBCATEGORIES.items()]
    lines.append(f"【子類別來源】{'、'.join(SUBCATEGORY_SOURCES)}（子類別為 null 時也填 null）")
    lines.append("【子類別同義詞表】原文用語（整串相同才算）→ 子類別")
    lines += [f"- {k} → {v}" for k, v in SUBCATEGORY_SYNONYMS.items()]
    lines.append(f"【影響時間用詞】{'、'.join(TIME_HORIZONS)}（可組合，例如「中至長」）")
    lines.append(f"【發生可能性、衝擊強度等級】{'、'.join(LEVELS)}")
    lines.append(f"【影響範圍封閉清單】{'、'.join(IMPACT_SCOPES)}")
    lines.append(f"【column_mapping 的對應欄位】{'、'.join(P1_MAPPABLE_FIELDS)}（不使用的欄位填「{FIELD_UNUSED}」）")
    return "\n".join(lines)


def _extract_table(ctx: ReportContext, table: ReconstructedTable, ref_text: str) -> P1Extraction:
    info = ctx.prompts[PROMPT]
    parts = (
        TextPart(f"報告書：{ctx.report.company_full_name} {ctx.report.report_year} 年永續報告書"),
        TextPart(closed_lists_text()),
        TextPart("【TCFD 例句參考】\n" + ref_text),
        TextPart(f"【策略表（共 {len(table.rows)} 個資料列、{len(table.header)} 欄）】\n" + reconstructed_text(table)),
    )
    req = LLMRequest(PROMPT, info.version, load_prompt(ctx.paths.root, info), parts, llm_schema(P1Extraction))
    checked = constrained_extraction(len(table.rows), len(table.header))
    res = ctx.llm.structured(req, checked, tag=ctx.llm_tag(pages=",".join(map(str, table.pages))))
    if res.value is None:
        raise RuntimeError(f"S3：LLM 未能產生有效輸出（{res.error}）")
    return res.value


def run_extract(ctx: ReportContext) -> None:
    tables = read_model(ctx.report_dir / "03a_tables.json", ReconstructedTables)
    meta = read_model(ctx.report_dir / "00_meta.json", ReportMeta)
    ref_text = load_prompt(ctx.paths.root, ctx.prompts[REF_TCFD])

    mapping, items = [], []
    for table in tables.tables:
        result = _extract_table(ctx, table, ref_text)
        mapping += result.column_mapping  # ver1 的兩份報告書各只有一張表；多張表時依序接續
        for draft, row in zip(result.items, table.rows):
            items.append(
                P1Item(
                    code=make_item_code(meta.stock_code, meta.report_year, len(items) + 1),
                    method=METHOD_1,
                    source_row=SourceRow(pdf_page=row.pdf_page, row_on_page=row.row_on_page),
                    pages=[row.pdf_page],
                    **draft.model_dump(),
                )
            )
    write_model(ctx.report_dir / "03_p1_items.json", P1Items(report_id=ctx.report_id, column_mapping=mapping, items=items))
