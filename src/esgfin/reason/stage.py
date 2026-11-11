"""S5 P2 推理（SPEC 9）：每個 P1 條目一次呼叫 → 05_p2/<代碼>.json。

送給 LLM：提示檔（固定規則）＋ 會計科目清單與排除項目（由 constants.py 產生）＋ metadata ＋ 公司脈絡卡（ver1 未提供）
＋ 條目全文（P1 item 的 JSON，含原文內容與頁碼）。只給條目本身，不給表格或頁面全文（SPEC 9.1）。
structured output 用 P2Draft（不含代碼）；代碼由程式填入。thinking level 用 reason.thinking_level（SPEC 17.12）。
驗證失敗附錯誤重問由 LLMService 處理（llm.validation_retries）；仍失敗回傳 P2Failure，流程繼續。
"""

from __future__ import annotations

import dataclasses
import json

from esgfin.constants import BALANCE_SHEET, BALANCE_SHEET_ACCOUNTS, EXCLUDED_ITEMS, INCOME_STATEMENT, INCOME_STATEMENT_ACCOUNTS
from esgfin.llm.base import LLMRequest, TextPart
from esgfin.llm.service import llm_schema
from esgfin.pipeline.context import ReportContext
from esgfin.pipeline.prompts import load_prompt
from esgfin.schemas.base import read_model
from esgfin.schemas.meta import ReportMeta
from esgfin.schemas.p1 import P1Item
from esgfin.schemas.p2 import P2Draft, P2Failure, P2Result

PROMPT = "p2_reasoning"
CONTEXT_CARD_NONE = "未提供"


def _account_lines(accounts: dict[str, str]) -> list[str]:
    return [f"{i}. {name}" + (f"（{desc}）" if desc else "") for i, (name, desc) in enumerate(accounts.items(), 1)]


def accounts_text() -> str:
    """會計科目清單（含括號說明）與排除項目，皆來自 constants.py（SPEC 9.2）。"""
    lines = [f"【會計科目清單：{BALANCE_SHEET}（{len(BALANCE_SHEET_ACCOUNTS)} 項）】", *_account_lines(BALANCE_SHEET_ACCOUNTS)]
    lines += [f"【會計科目清單：{INCOME_STATEMENT}（{len(INCOME_STATEMENT_ACCOUNTS)} 項）】", *_account_lines(INCOME_STATEMENT_ACCOUNTS)]
    lines.append("科目欄位只能填上列「括號前」的名稱；括號內是說明。")
    lines.append("【排除項目（不得選）】" + "；".join(EXCLUDED_ITEMS))
    return "\n".join(lines)


def meta_text(meta: ReportMeta) -> str:
    ind = meta.industry
    return (
        f"【報告書】公司全名：{meta.company_full_name}；報告書年度：{meta.report_year}；"
        f"行業：{ind.section_code} {ind.section_name}" + (f"（{ind.detail}）" if ind.detail else "")
    )


def item_text(item: P1Item) -> str:
    return "【條目全文】\n" + json.dumps(item.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=1)


def build_request(ctx: ReportContext, item: P1Item, meta: ReportMeta) -> LLMRequest:
    info = ctx.prompts[PROMPT]
    parts = (
        TextPart(accounts_text()),
        TextPart(meta_text(meta)),
        TextPart(f"【公司脈絡卡】{CONTEXT_CARD_NONE}"),
        TextPart(item_text(item)),
    )
    return LLMRequest(PROMPT, info.version, load_prompt(ctx.paths.root, info), parts, llm_schema(P2Draft))


def reason_item(ctx: ReportContext, item: P1Item) -> P2Result | P2Failure:
    meta = read_model(ctx.report_dir / "00_meta.json", ReportMeta)
    req = build_request(ctx, item, meta)
    params = dataclasses.replace(ctx.llm.params, thinking_level=ctx.settings.reason.thinking_level)
    res = ctx.llm.structured(req, P2Draft, tag=ctx.llm_tag(item=item.code), params=params)
    if res.value is None:
        return P2Failure(code=item.code, error=res.error or "", attempts=res.attempts)
    return P2Result(code=item.code, **res.value.model_dump())
