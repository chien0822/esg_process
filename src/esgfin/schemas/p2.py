"""P2 推理（05_p2/<代碼>.json，SPEC 9.4）。

欄位宣告順序 = 送給 Gemini 的 schema 屬性順序 = 生成順序：推理在前、結論在後。
不要調整欄位順序。
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from esgfin.constants import (
    ALL_ACCOUNT_NAMES,
    BALANCE_SHEET_ACCOUNT_NAMES,
    CHANNELS,
    DIRECTIONS_LLM,
    DIRECTNESS_LEVELS,
    EVIDENCE_GRADES,
    GAP_TYPES,
    INCOME_STATEMENT_ACCOUNT_NAMES,
    NO_IMPACT_REASONS,
    OCCURRENCE_STAGES,
    OVERALL_JUDGEMENTS,
    REASONING_FAILED,
    SOURCE_KINDS,
)

from .base import ZhModel, zh

AccountT = TypeVar("AccountT")


class UnderstandingField(ZhModel):
    content: str = zh("內容")
    quote: str | None = zh("原文引用")
    source: Literal[SOURCE_KINDS] = zh("來源")


class OccurrenceField(ZhModel):
    content: Literal[OCCURRENCE_STAGES] = zh("內容")
    quote: str | None = zh("原文引用")
    source: Literal[SOURCE_KINDS] = zh("來源")


class Gap(ZhModel):
    gap_type: Literal[GAP_TYPES] = zh("類型")
    explanation: str = zh("說明")


class Understanding(ZhModel):
    what: UnderstandingField = zh("事件是什麼")
    where: UnderstandingField = zh("影響公司哪一塊")
    occurrence: OccurrenceField = zh("已發生或預期")
    quantitative: UnderstandingField = zh("量化線索")
    response: UnderstandingField = zh("公司因應")
    gaps: list[Gap] = zh("缺口")


class Channel(ZhModel):
    channel: Literal[CHANNELS] = zh("管道")
    explanation: str = zh("傳導說明")


class AccountImpact(ZhModel, Generic[AccountT]):
    rank: int = zh("排序", ge=1)
    account: AccountT = zh("科目")
    direction: Literal[DIRECTIONS_LLM] = zh("影響方向")
    directness: Literal[DIRECTNESS_LEVELS] = zh("直接性層級")
    evidence_grade: Literal[EVIDENCE_GRADES] = zh("證據等級")
    stage: Literal[OCCURRENCE_STAGES] = zh("發生階段")
    explanation: str = zh("傳導說明")
    basis_quote: str | None = zh("原文依據")
    alternatives: list[Literal[ALL_ACCOUNT_NAMES]] = zh("替代科目")


class BalanceSheetImpact(AccountImpact[Literal[BALANCE_SHEET_ACCOUNT_NAMES]]):
    """科目 enum 限定資產負債表 11 項。"""


class IncomeStatementImpact(AccountImpact[Literal[INCOME_STATEMENT_ACCOUNT_NAMES]]):
    """科目 enum 限定綜合損益表 8 項。"""


class AffectedAccounts(ZhModel):
    balance_sheet: list[BalanceSheetImpact] = zh("資產負債表")
    income_statement: list[IncomeStatementImpact] = zh("綜合損益表")


class P2Result(ZhModel):
    code: str = zh("代碼")
    understanding: Understanding = zh("理解紀錄")
    channels: list[Channel] = zh("傳導管道")
    overall: Literal[OVERALL_JUDGEMENTS] = zh("整體判斷")
    no_impact_reason: Literal[NO_IMPACT_REASONS] | None = zh("無影響理由類型")
    rationale: str = zh("判斷理由")
    accounts: AffectedAccounts = zh("潛在受影響會計科目")


class LLMAttempt(ZhModel):
    attempt: int = zh("attempt")
    raw_text: str | None = zh("raw_text")
    error: str = zh("error")


class P2Failure(ZhModel):
    """05_p2/<代碼>.failed.json：推理失敗紀錄（不是成功快取）。"""

    code: str = zh("代碼")
    status: Literal[REASONING_FAILED] = zh("狀態", default=REASONING_FAILED)
    error: str = zh("error")
    attempts: list[LLMAttempt] = zh("attempts", default_factory=list)
