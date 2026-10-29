"""Docling 解析器（docling 2.x，CPU）。

用法依官方文件與已安裝版本的 API：DocumentConverter + PdfFormatOption(PdfPipelineOptions)，
DoclingDocument.export_to_markdown(page_no=…, traverse_pictures=True) 逐頁輸出（預設只含 body 內容層，不含頁首頁尾），
表格由 doc.tables 依 prov.page_no 歸頁，TableItem.export_to_markdown(doc=…) 輸出。
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

from esgfin.schemas.pages import PageRecord

# 改變下方轉換參數或後處理時加一，使舊的解析快取失效
REVISION = 2  # r2：traverse_pictures=True
MAX_PAGES_PER_CONVERT = 25  # 分段轉換以限制記憶體用量


def _converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(
        do_ocr=False,  # ver1 不做 OCR（SPEC 5）
        do_table_structure=True,
        table_structure_options=TableStructureOptions(mode=TableFormerMode.ACCURATE, do_cell_matching=True),
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU),
    )
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


class DoclingParser:
    name = "docling"

    @property
    def version(self) -> str:
        v = importlib.metadata.version
        return f"{v('docling')}+core{v('docling-core')}+r{REVISION}"

    def parse(self, pdf: Path, pages: list[int] | None = None) -> list[PageRecord]:
        import pymupdf

        with pymupdf.open(pdf) as d:
            page_count = d.page_count
        wanted = list(range(1, page_count + 1)) if pages is None else sorted(set(pages))
        converter = _converter()
        version = self.version
        out = []
        # page_range 只接受連續範圍；過長的範圍再切段
        for start, end in _ranges(wanted, MAX_PAGES_PER_CONVERT):
            doc = converter.convert(pdf, page_range=(start, end)).document
            tables: dict[int, list[str]] = {}
            for t in doc.tables:
                md = t.export_to_markdown(doc=doc).strip()
                for page_no in sorted({p.page_no for p in t.prov}):
                    tables.setdefault(page_no, []).append(md)
            for n in range(start, end + 1):
                out.append(
                    PageRecord(
                        pdf_page=n,
                        # 圖表內的文字（例如統計圖的數字）也輸出；PyMuPDF4LLM 預設同樣保留
                        text_md=doc.export_to_markdown(page_no=n, traverse_pictures=True),
                        tables_md=tables.get(n, []),
                        parser=self.name,
                        parser_version=version,
                    )
                )
        return out


def _ranges(pages: list[int], max_len: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for p in pages:
        if out and p == out[-1][1] + 1 and p - out[-1][0] < max_len:
            out[-1] = (out[-1][0], p)
        else:
            out.append((p, p))
    return out
