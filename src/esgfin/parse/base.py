"""S1 解析器的可替換介面（SPEC 5）。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from esgfin.schemas.pages import PageRecord, ParserName


class NoTextLayerError(RuntimeError):
    """PDF 沒有文字層（掃描檔）；ver1 不做 OCR。"""


class Parser(Protocol):
    name: ParserName

    @property
    def version(self) -> str:
        """套件版本＋本系統的轉換修訂號；任一改變都使解析快取失效。"""
        ...

    def parse(self, pdf: Path, pages: list[int] | None = None) -> list[PageRecord]:
        """回傳每頁一筆，pdf_page 為 PDF 頁序（從 1 開始）。pages=None 表示全部頁。"""
        ...


def make_parser(name: ParserName) -> Parser:
    # 延遲 import：兩個套件都很重，只載入用到的那個
    if name == "docling":
        from .docling_parser import DoclingParser

        return DoclingParser()
    if name == "pymupdf4llm":
        from .pymupdf_parser import PyMuPDF4LLMParser

        return PyMuPDF4LLMParser()
    raise ValueError(f"未知的解析器：{name}")
