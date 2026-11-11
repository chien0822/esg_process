"""01_pages.jsonl（SPEC 5；table_cells 見 SPEC 17.11 第 3 點）。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import StrictModel

ParserName = Literal["docling", "pymupdf4llm"]


class TableCell(StrictModel):
    """Docling 的一個儲存格，照原值保存、不修正。列、欄為 0 起算，end 不含。"""

    start_row: int = Field(ge=0)
    end_row: int = Field(ge=0)
    start_col: int = Field(ge=0)
    end_col: int = Field(ge=0)
    text: str
    column_header: bool = False
    row_header: bool = False


class PageTable(StrictModel):
    """該頁一張表的格子（S3a 檢查用）。跨頁表依 prov.page_no 歸頁，與 tables_md 一致。"""

    num_rows: int = Field(ge=0)
    num_cols: int = Field(ge=0)
    cells: list[TableCell] = Field(default_factory=list)


class PageRecord(StrictModel):
    pdf_page: int = Field(ge=1)  # PDF 頁序，從 1 開始；不是印刷頁碼
    text_md: str
    tables_md: list[str] = Field(default_factory=list)
    table_cells: list[PageTable] = Field(default_factory=list)  # PyMuPDF4LLM 為空清單
    parser: ParserName
    parser_version: str
