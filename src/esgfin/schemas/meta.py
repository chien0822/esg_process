"""報告書 metadata（config/reports.yaml 與 00_meta.json，SPEC 4.1）。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import model_validator

from esgfin.constants import INDUSTRY_SECTION_CODES, INDUSTRY_SECTIONS

from .base import StrictModel


class Industry(StrictModel):
    section_code: Literal[INDUSTRY_SECTION_CODES]
    section_name: str
    version: str  # 行業統計分類修訂版次【待確認 15-5】
    detail: str | None = None

    @model_validator(mode="after")
    def _name_matches_code(self) -> "Industry":
        expected = INDUSTRY_SECTIONS[self.section_code]
        if self.section_name != expected:
            raise ValueError(f"行業大類 {self.section_code} 的名稱應為「{expected}」，設定檔寫的是「{self.section_name}」")
        return self


class ReportConfig(StrictModel):
    file: str
    company_full_name: str
    stock_code: str
    report_year: int
    industry: Industry

    @property
    def report_id(self) -> str:
        return Path(self.file).stem


class ReportsConfig(StrictModel):
    reports: list[ReportConfig]

    @model_validator(mode="after")
    def _unique_files(self) -> "ReportsConfig":
        ids = [r.report_id for r in self.reports]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"reports.yaml 有重複的報告書：{sorted(dup)}")
        return self


class ReportMeta(StrictModel):
    """00_meta.json"""

    report_id: str
    file: str
    company_full_name: str
    stock_code: str
    report_year: int
    industry: Industry
    pdf_sha256: str
    pdf_size_bytes: int
