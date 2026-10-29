"""04_p1_check.json 與 06_check_report.json（SPEC 8、10）。只標記，不修改。"""

from __future__ import annotations

from typing import Literal

from esgfin.constants import CITATION_LEVELS

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


class P1Check(ZhModel):
    report_id: str = zh("report_id")
    items: list[NullFields] = zh("items")
    citations: list[CitationCheck] = zh("引文驗證")


class CheckIssue(ZhModel):
    code: str = zh("代碼")
    rule: str = zh("規則")
    message: str = zh("說明")


class CheckReport(ZhModel):
    report_id: str = zh("report_id")
    issues: list[CheckIssue] = zh("問題")
    citations: list[CitationCheck] = zh("引文驗證")
