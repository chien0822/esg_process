"""從 PDF 取出指定頁，組成給 LLM 的子 PDF（S2 目錄解析、S3a 表格重建共用）。"""

from __future__ import annotations

from pathlib import Path


def pdf_bytes(pdf: Path, pages: list[int]) -> bytes:
    import pymupdf

    with pymupdf.open(pdf) as src, pymupdf.open() as out:
        for n in pages:
            out.insert_pdf(src, from_page=n - 1, to_page=n - 1)
        # no_new_id：不寫入新的檔案 ID，同樣的頁每次產生相同位元組，LLM 快取才會命中
        return out.tobytes(no_new_id=True)


def raw_page_text(pdf: Path, pages: list[int]) -> dict[int, str]:
    """PyMuPDF 原始文字層（含頁首頁尾）。"""
    import pymupdf

    with pymupdf.open(pdf) as doc:
        return {n: doc[n - 1].get_text() for n in pages if 1 <= n <= doc.page_count}
