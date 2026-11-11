"""方法一條目（03_p1_items.json，SPEC 7.3）。

P1ItemDraft / P1Extraction 是 LLM 的 structured output；
代碼、方法、來源列、頁碼由程式產生，不由 LLM 產生（SPEC 7.1、7.3），所以只出現在 P1Item / P1Items。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from esgfin.constants import (
    ALL_SUBCATEGORIES,
    IMPACT_SCOPES,
    ITEM_TYPES,
    LEVELS,
    METHODS,
    P1_MAPPABLE_FIELDS,
    SUBCATEGORY_SOURCES,
)

from .base import ZhModel, zh


class ColumnMapping(ZhModel):
    column: int = zh("欄序", ge=1)  # 對應 03a_tables.json 的 header（從 1 起算）；區分同名欄
    original_column: str = zh("原表欄位")
    mapped_field: Literal[P1_MAPPABLE_FIELDS] = zh("對應欄位")


class P1SourceText(ZhModel):
    """原文內容：逐字引用，保留原語言。"""

    event_description: str = zh("事件描述")
    financial_impact: str | None = zh("顯示財務Impact")
    response_note: str | None = zh("因應措施備註")


class P1ItemDraft(ZhModel):
    item_type: Literal[ITEM_TYPES] = zh("類型")
    subcategory: Literal[ALL_SUBCATEGORIES] | None = zh("子類別")
    subcategory_source: Literal[SUBCATEGORY_SOURCES] | None = zh("子類別來源")
    subcategory_raw: str | None = zh("子類別原文用語")  # 原表子類別欄的文字，逐字；沒有子類別欄時為 null
    event_description: str = zh("事件描述")
    # 不限定內容（SPEC 7.3 v2）：提示引導以短／中／長及其組合填寫，例如「中至長」；檢查模組不驗證
    time_horizon: str | None = zh("影響時間")
    time_horizon_raw: str | None = zh("影響時間原文用語")
    likelihood: Literal[LEVELS] | None = zh("發生可能性")
    likelihood_raw: str | None = zh("發生可能性原文用語")
    severity: Literal[LEVELS] | None = zh("衝擊強度")
    severity_raw: str | None = zh("衝擊強度原文用語")
    scope: Literal[IMPACT_SCOPES] | None = zh("影響範圍")
    scope_raw: str | None = zh("影響範圍原文用語")
    financial_impact: str | None = zh("顯示財務Impact")
    response_note: str | None = zh("因應措施備註")
    source_text: P1SourceText = zh("原文內容")

    @model_validator(mode="after")
    def _subcategory_pair(self):
        if (self.subcategory is None) != (self.subcategory_source is None):
            raise ValueError("「子類別」與「子類別來源」必須同時為 null 或同時有值")
        return self


class P1Extraction(ZhModel):
    """S3 LLM 回應。items 依表格由上而下排列，一列一條目。"""

    column_mapping: list[ColumnMapping] = zh("column_mapping")
    items: list[P1ItemDraft] = zh("items")


def constrained_extraction(n_rows: int, n_cols: int) -> type[P1Extraction]:
    """驗證用：條目數＝重建表資料列數；column_mapping 每欄恰好一筆（欄序 1…n_cols）。

    只用於 pydantic 驗證（不合即附錯誤重問）；送給模型的 schema 仍是 P1Extraction。
    """

    class P1ExtractionChecked(P1Extraction):
        @model_validator(mode="after")
        def _shape(self):
            errors = []
            if len(self.items) != n_rows:
                errors.append(f"items 必須恰好 {n_rows} 筆（重建表的資料列數，一列一條目），實際 {len(self.items)} 筆")
            cols = sorted(m.column for m in self.column_mapping)
            if cols != list(range(1, n_cols + 1)):
                errors.append(f"column_mapping 必須每一欄恰好一筆（欄序 1–{n_cols}），實際欄序 {cols}")
            if errors:
                raise ValueError("；".join(errors))
            return self

    return P1ExtractionChecked


class SourceRow(ZhModel):
    pdf_page: int = Field(ge=1, alias="pdf_page")
    row_on_page: int = Field(ge=1, alias="row_on_page")


class _P1Id(ZhModel):
    code: str = zh("代碼")
    method: Literal[METHODS] = zh("方法")
    source_row: SourceRow = zh("來源列")  # 重建表的資料列，由程式依條目順序填入


class P1Item(P1ItemDraft, _P1Id):
    """欄位順序：代碼、方法、來源列在前，其餘同 P1ItemDraft，最後為頁碼。"""

    pages: list[int] = zh("頁碼", min_length=1)  # PDF 頁序：來源列所在的頁


class P1Items(ZhModel):
    report_id: str = zh("report_id")
    column_mapping: list[ColumnMapping] = zh("column_mapping")
    items: list[P1Item] = zh("items")


def make_item_code(stock_code: str, report_year: int, seq: int) -> str:
    """<股票代號>-<年度>-M1-<三位序號>，例如 2379-2024-M1-001。"""
    return f"{stock_code}-{report_year}-M1-{seq:03d}"
