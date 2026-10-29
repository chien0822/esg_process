"""07_final.json（SPEC 11.1）與 07_metrics.json（SPEC 11.2）。"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from esgfin.constants import (
    DIRECTION_NONE,
    IMPACT_NO,
    NO_IMPACT_REASONS,
    OVERALL_JUDGEMENTS,
    REASONING_STATUSES,
)

from .base import StrictModel, ZhModel, zh
from .meta import ReportMeta
from .p1 import P1Item
from .p2 import BalanceSheetImpact, IncomeStatementImpact


class TopAccounts(ZhModel):
    """各表排序前三；空的表為 None，輸出時以 exclude_none 省略。"""

    balance_sheet: list[BalanceSheetImpact] | None = zh("資產負債表", default=None)
    income_statement: list[IncomeStatementImpact] | None = zh("綜合損益表", default=None)


class FinalItem(P1Item):
    reasoning_status: Literal[REASONING_STATUSES] = zh("推理狀態")
    overall: Literal[OVERALL_JUDGEMENTS] | None = zh("整體判斷", default=None)
    # 有影響：TopAccounts；無影響：字串「無影響」；推理失敗、檢查未通過：None
    top_accounts: TopAccounts | Literal[IMPACT_NO] | None = zh("潛在受影響會計科目", default=None)
    # 只在無影響時為「不影響」，其餘為 None
    direction: Literal[DIRECTION_NONE] | None = zh("影響方向", default=None)
    no_impact_reason: Literal[NO_IMPACT_REASONS] | None = zh("無影響理由類型", default=None)


class FinalReport(StrictModel):
    report_id: str
    run_id: str
    generated_at: str
    meta: ReportMeta
    items: list[FinalItem]


class Ratio(ZhModel):
    """保留分子分母，總計時以合併計數計算（分子總和 / 分母總和），不取平均。"""

    numerator: int = zh("分子", default=0, ge=0)
    denominator: int = zh("分母", default=0, ge=0)

    value: float | None = zh("比例", default=None)  # 一律由分子分母重算，輸入值忽略

    @model_validator(mode="after")
    def _recompute(self) -> "Ratio":
        self.value = None if self.denominator == 0 else self.numerator / self.denominator
        return self

    @classmethod
    def pooled(cls, ratios: list["Ratio"]) -> "Ratio":
        return cls(numerator=sum(r.numerator for r in ratios), denominator=sum(r.denominator for r in ratios))

    @classmethod
    def pooled_by_key(cls, groups: list[dict[str, "Ratio"]]) -> dict[str, "Ratio"]:
        """逐鍵合併（例如各規則的問題比例）；某報告書缺該鍵時不計入。"""
        keys = list(dict.fromkeys(k for g in groups for k in g))
        return {k: cls.pooled([g[k] for g in groups if k in g]) for k in keys}


class CitationStats(ZhModel):
    exact: int = zh("完全通過", default=0)
    fuzzy: int = zh("模糊通過", default=0)
    failed: int = zh("未通過", default=0)

    @classmethod
    def pooled(cls, stats: list["CitationStats"]) -> "CitationStats":
        return cls(
            exact=sum(s.exact for s in stats),
            fuzzy=sum(s.fuzzy for s in stats),
            failed=sum(s.failed for s in stats),
        )


class StageUsage(ZhModel):
    duration_s: float = zh("耗時秒", default=0.0)
    input_tokens: int = zh("輸入token", default=0)
    output_tokens: int = zh("輸出token", default=0)
    thought_tokens: int = zh("思考token", default=0)


class Metrics(ZhModel):
    scope: str = zh("範圍")  # report_id 或「全部」
    item_count: int = zh("條目數", default=0)
    no_impact_by_reason: dict[str, int] = zh("無影響條目數", default_factory=dict)
    reasoning_failed: int = zh("推理失敗數", default=0)
    # 分子 = 檢查未通過（S4 或 S6 有嚴重問題）的條目數；分母 = 有 P2 結果的條目數（不含推理失敗）
    inconsistent: Ratio = zh("不一致比例", default_factory=Ratio)
    # 各檢查規則：分子 = 有該問題的條目數（同條目多次只計一次）
    # 分母：S4 規則 = P1 條目數；S6 規則 = 有 P2 結果的條目數
    issues_p1: dict[str, Ratio] = zh("問題比例_S4", default_factory=dict)
    issues_p2: dict[str, Ratio] = zh("問題比例_S6", default_factory=dict)
    gap_type_counts: dict[str, int] = zh("缺口類型分布", default_factory=dict)
    certainty_all: Ratio = zh("確定比例_全部排序科目", default_factory=Ratio)
    certainty_top: Ratio = zh("確定比例_前三顯示科目", default_factory=Ratio)
    citation_p1: CitationStats = zh("引文驗證_P1", default_factory=CitationStats)
    citation_p2: CitationStats = zh("引文驗證_P2", default_factory=CitationStats)
    json_first_pass: Ratio = zh("JSON首次通過率", default_factory=Ratio)
    retries: int = zh("重試次數", default=0)
    stages: dict[str, StageUsage] = zh("階段耗時與token", default_factory=dict)
