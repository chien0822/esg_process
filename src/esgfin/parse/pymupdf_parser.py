"""PyMuPDF4LLM 解析器（pymupdf4llm 2.0，裝有 pymupdf-layout 時走版面分析路徑）。

用法依官方文件 Document.to_markdown：page_chunks=True 每頁回傳一個 dict，
頁序在 metadata.page_number（從 1 開始）；page_boxes 的 pos 是該區塊在 text 中的位置。
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

from esgfin.schemas.pages import PageRecord

# 改變下方轉換參數或後處理時加一，使舊的解析快取失效
REVISION = 1

OPTIONS = dict(
    page_chunks=True,
    use_ocr=False,  # ver1 不做 OCR（SPEC 5）
    header=False,  # 頁首頁尾不放入內文（與 Docling 只輸出 body 一致）
    footer=False,
    write_images=False,
    embed_images=False,
    show_progress=False,
)


class PyMuPDF4LLMParser:
    name = "pymupdf4llm"

    @property
    def version(self) -> str:
        v = importlib.metadata.version
        return f"{v('pymupdf4llm')}+pymupdf{v('pymupdf')}+r{REVISION}"

    def parse(self, pdf: Path, pages: list[int] | None = None) -> list[PageRecord]:
        import pymupdf
        import pymupdf4llm

        with pymupdf.open(pdf) as doc:
            zero_based = None if pages is None else [p - 1 for p in pages]
            chunks = pymupdf4llm.to_markdown(doc, pages=zero_based, **OPTIONS)
        version = self.version
        out = []
        for c in chunks:
            text = c["text"]
            tables = [
                text[b["pos"][0] : b["pos"][1]].strip()
                for b in c.get("page_boxes", [])
                if b.get("class") == "table" and b["pos"][1] > b["pos"][0]
            ]
            out.append(
                PageRecord(
                    pdf_page=c["metadata"]["page_number"],
                    text_md=text,
                    tables_md=tables,
                    parser=self.name,
                    parser_version=version,
                )
            )
        return out
