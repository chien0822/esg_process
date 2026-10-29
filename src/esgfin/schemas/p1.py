"""方法一條目（03_p1_items.json，SPEC 7.3）。

P1ItemDraft / P1Extraction 是 LLM 的 structured output；
代碼與方法由程式產生，不由 LLM 產生（SPEC 7.1），所以只出現在 P1Item / P1Items。
"""

from __future__ import annotations

from typing import Literal


from esgfin.constants import (
    ALL_SUBCATEGORIES,
    IMPACT_SCOPES,
    ITEM_TYPES,
    LEVELS,
    METHODS,
    SUBCATEGORY_SOURCES,
    TIME_HORIZONS,
)

from .base import ZhModel, zh


class ColumnMapping(ZhModel):
    original_column: str = zh("原表欄位")
    mapped_field: str = zh("對應欄位")


class P1SourceText(ZhModel):
    """原文內容：逐字引用，保留原語言。"""

    event_description: str = zh("事件描述")
    financial_impact: str | None = zh("顯示財務Impact")
    response_note: str | None = zh("因應措施備註")


class P1ItemDraft(ZhModel):
    item_type: Literal[ITEM_TYPES] = zh("類型")
    subcategory: Literal[ALL_SUBCATEGORIES] = zh("子類別")
    subcategory_source: Literal[SUBCATEGORY_SOURCES] = zh("子類別來源")
    event_description: str = zh("事件描述")
    time_horizon: Literal[TIME_HORIZONS] | None = zh("影響時間")
    time_horizon_raw: str | None = zh("影響時間原文用語")
    likelihood: Literal[LEVELS] | None = zh("發生可能性")
    likelihood_raw: str | None = zh("發生可能性原文用語")
    severity: Literal[LEVELS] | None = zh("衝擊強度")
    severity_raw: str | None = zh("衝擊強度原文用語")
    scope: Literal[IMPACT_SCOPES] | None = zh("影響範圍")
    financial_impact: str | None = zh("顯示財務Impact")
    response_note: str | None = zh("因應措施備註")
    source_text: P1SourceText = zh("原文內容")
    pages: list[int] = zh("頁碼", min_length=1)  # PDF 頁序


class P1Extraction(ZhModel):
    """S3 LLM 回應。items 依表格由上而下排列。"""

    column_mapping: list[ColumnMapping] = zh("column_mapping")
    items: list[P1ItemDraft] = zh("items")


class _P1Id(ZhModel):
    code: str = zh("代碼")
    method: Literal[METHODS] = zh("方法")


class P1Item(P1ItemDraft, _P1Id):
    """欄位順序：代碼、方法在前，其餘同 P1ItemDraft。"""


class P1Items(ZhModel):
    report_id: str = zh("report_id")
    column_mapping: list[ColumnMapping] = zh("column_mapping")
    items: list[P1Item] = zh("items")


def make_item_code(stock_code: str, report_year: int, seq: int) -> str:
    """<股票代號>-<年度>-M1-<三位序號>，例如 2379-2024-M1-001。"""
    return f"{stock_code}-{report_year}-M1-{seq:03d}"
