"""S2 定位的 LLM 輸出 schema（SPEC 6.1 第 2、5、6、7 步）。

欄位宣告順序 = 生成順序：依據與原文在前、結論在後。
"""

from __future__ import annotations

from typing import Literal

from esgfin.constants import SECTION_ROLES, TOC_UNCERTAIN_FIELDS

from .base import ZhModel, zh


# 第 2 步：目錄解析
class TocEntryDraft(ZhModel):
    evidence: str = zh("目錄原文")  # 該目錄項在目錄頁上的文字（逐字）
    title: str = zh("標題")  # 逐字，不含頁碼
    level: int = zh("層級", ge=1)  # 1 = 最上層（章）
    printed_page: int | None = zh("印刷頁碼")  # 看不出來就填 null，不得推測
    uncertain: list[Literal[TOC_UNCERTAIN_FIELDS]] = zh("不確定欄位")


class TocParse(ZhModel):
    entries: list[TocEntryDraft] = zh("目錄項")


# 第 5 步：角色分類
class RoleAssignment(ZhModel):
    section_id: str = zh("章節代碼")
    reason: str = zh("理由")
    role: Literal[SECTION_ROLES] = zh("角色")


class RoleResult(ZhModel):
    roles: list[RoleAssignment] = zh("分類")


# 第 6 步：策略表判斷（每個候選頁一筆）
class StrategyPageJudgement(ZhModel):
    pdf_page: int = zh("PDF頁序")
    evidence: str | None = zh("原文摘錄")  # 逐字，例如表頭或第一列
    reason: str = zh("理由")
    is_strategy_table: bool = zh("是否為策略表")


class StrategyResult(ZhModel):
    pages: list[StrategyPageJudgement] = zh("判斷")


# 第 7 步：書末索引
class IndexDisclosure(ZhModel):
    evidence: str = zh("索引原文")  # 逐字
    index_item: str = zh("索引項目")  # 例如「GRI 201-2」「TCFD 策略 b」
    points_to: str = zh("指向")  # 索引寫的頁碼或章節（逐字）
    printed_pages: list[int] = zh("印刷頁碼")  # 從「指向」讀出的頁碼；只寫章節時為空


class IndexResult(ZhModel):
    items: list[IndexDisclosure] = zh("氣候相關揭露")
