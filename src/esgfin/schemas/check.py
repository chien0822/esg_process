"""04_p1_check.json 與 06_check_report.json（SPEC 8、10）。只標記，不修改。"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import model_validator

from esgfin.constants import (
    ALL_CHECK_RULES,
    CITATION_LEVELS,
    P1_CHECK_RULES,
    P2_CHECK_RULES,
    SEVERITIES,
    SEVERITY_SEVERE,
)

from .base import ZhModel, zh


class CitationCheck(ZhModel):
    code: str = zh("代碼")
    field: str = zh("欄位")  # 例如「原文內容.事件描述」「理解紀錄.量化線索」
    quote: str = zh("引文")
    pages_searched: list[int] = zh("比對頁碼")  # 含緩衝
    level: Literal[CITATION_LEVELS] = zh("等級")
    ratio: float = zh("比例", ge=0, le=1)


class NullFields(ZhModel):
    code: str = zh("代碼")
    null_fields: list[str] = zh("空值欄位")


class CheckIssue(ZhModel):
    code: str = zh("代碼")
    rule: Literal[ALL_CHECK_RULES] = zh("規則")
    severity: Literal[SEVERITIES] = zh("嚴重度")  # 由 constants 的分級表決定，不可自填
    field: str | None = zh("欄位", default=None)
    message: str = zh("說明")


class _IssueReport(ZhModel):
    RULES: ClassVar[dict[str, str]]

    @model_validator(mode="after")
    def _check_rules(self):
        for issue in self.issues:
            expected = self.RULES.get(issue.rule)
            if expected is None:
                raise ValueError(f"規則「{issue.rule}」不屬於本階段的檢查")
            if issue.severity != expected:
                raise ValueError(f"規則「{issue.rule}」的嚴重度應為「{expected}」，檔案為「{issue.severity}」")
        return self

    def severe_codes(self) -> set[str]:
        return {i.code for i in self.issues if i.severity == SEVERITY_SEVERE}


class P1Check(_IssueReport):
    RULES: ClassVar[dict[str, str]] = P1_CHECK_RULES

    report_id: str = zh("report_id")
    items: list[NullFields] = zh("items")  # 空值只記錄，不分級
    issues: list[CheckIssue] = zh("問題", default_factory=list)
    citations: list[CitationCheck] = zh("引文驗證")


class CheckReport(_IssueReport):
    """S6 只檢查 P2，不重複 S4 的 P1 檢查。"""

    RULES: ClassVar[dict[str, str]] = P2_CHECK_RULES

    report_id: str = zh("report_id")
    issues: list[CheckIssue] = zh("問題")
    citations: list[CitationCheck] = zh("引文驗證")
