"""S1 解析階段：文字層檢查 → 解析快取 → 01_pages.jsonl（SPEC 5、17.7）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from esgfin.constants import REPORT_STATUS_FAILED
from esgfin.pipeline.context import ReportContext
from esgfin.pipeline.errors import ReportStop
from esgfin.schemas.base import read_model, write_jsonl
from esgfin.schemas.meta import ReportMeta

from .base import Parser, make_parser
from .cache import ParseCache

MIN_CHARS_PER_TEXT_PAGE = 20  # 去除空白後少於此字數的頁視為沒有文字


def text_layer_pages(pdf: Path) -> tuple[int, list[int]]:
    """回傳（總頁數, 沒有文字層的 PDF 頁序）。只用 PyMuPDF 讀文字層，不做 OCR。"""
    import pymupdf

    empty = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, start=1):
            if len("".join(page.get_text().split())) < MIN_CHARS_PER_TEXT_PAGE:
                empty.append(i)
        return doc.page_count, empty


def run_parse(ctx: ReportContext, parser_factory: Callable[[str], Parser] = make_parser) -> None:
    settings = ctx.settings.parse
    total, empty = text_layer_pages(ctx.pdf_path)
    if total == 0 or (total - len(empty)) / total < settings.min_text_page_ratio:
        raise ReportStop(
            REPORT_STATUS_FAILED,
            f"S1 解析：{total} 頁中有 {len(empty)} 頁沒有文字層，疑似掃描檔；ver1 不做 OCR，請改用有文字層的 PDF",
        )
    if empty:
        ctx.console.print(f"  [dim]S1：{len(empty)} 頁沒有文字層（多為封面或全頁圖片）：{empty}[/dim]")

    meta = read_model(ctx.report_dir / "00_meta.json", ReportMeta)
    parser = parser_factory(settings.parser)
    cache = ParseCache(ctx.paths.parse_cache_dir)
    pages = cache.get(meta.pdf_sha256, parser.name, parser.version)
    hit = pages is not None
    if pages is None:
        ctx.console.print(f"  S1：以 {parser.name} {parser.version} 解析 {total} 頁（無快取，可能需要數分鐘）…")
        pages = parser.parse(ctx.pdf_path)
        if len(pages) != total:
            raise RuntimeError(f"{parser.name} 回傳 {len(pages)} 頁，PDF 共 {total} 頁")
        cache.put(meta.pdf_sha256, parser.name, parser.version, pages)
    else:
        ctx.console.print(f"  S1：使用解析快取（{parser.name} {parser.version}）")
    write_jsonl(ctx.report_dir / "01_pages.jsonl", pages)
    if ctx.stage_record is not None:
        ctx.stage_record.parse_cache_hit = hit
