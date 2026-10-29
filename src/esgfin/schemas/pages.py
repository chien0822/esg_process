"""01_pages.jsonl（SPEC 5）。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import StrictModel

ParserName = Literal["docling", "pymupdf4llm"]


class PageRecord(StrictModel):
    pdf_page: int = Field(ge=1)  # PDF 頁序，從 1 開始；不是印刷頁碼
    text_md: str
    tables_md: list[str] = Field(default_factory=list)
    parser: ParserName
    parser_version: str
