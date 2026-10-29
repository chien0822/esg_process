"""02_manifest.json（SPEC 6.2）。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from esgfin.constants import SECTION_ROLES, STRATEGY_TABLE_FOUND_BY, TOC_SOURCES

from .base import StrictModel


class PageOffset(StrictModel):
    value: int | None
    method: str
    confidence_note: str = ""


class Section(StrictModel):
    section_id: str
    title: str
    level: int = Field(ge=1)
    printed_page_range: tuple[int, int] | None
    pdf_page_range: tuple[int, int] | None
    pdf_page_range_buffered: tuple[int, int] | None
    role: Literal[SECTION_ROLES] | None
    evidence: str  # 目錄原文行（逐字）
    flags: list[str] = Field(default_factory=list)


class StrategyTable(StrictModel):
    pdf_pages: list[int] = Field(min_length=1)
    section_id: str | None
    evidence: str
    found_by: Literal[STRATEGY_TABLE_FOUND_BY]


class IndexCrossCheck(StrictModel):
    index_item: str
    points_to: str
    inside_located_range: bool | None


class Manifest(StrictModel):
    report_id: str
    toc_source: Literal[TOC_SOURCES] | None
    page_offset: PageOffset | None
    sections: list[Section] = Field(default_factory=list)
    strategy_tables: list[StrategyTable] = Field(default_factory=list)
    index_cross_check: list[IndexCrossCheck] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
