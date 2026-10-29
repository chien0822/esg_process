"""S1 解析快取（cache/parse/）。

鍵 = PDF 檔案 SHA-256 + 解析器名稱 + 解析器版本。新 run 遇到同一份 PDF、
同一個解析器版本時直接讀快取，不必重新解析。內容與 01_pages.jsonl 相同，讀取時同樣做 schema 驗證。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from esgfin.schemas.base import read_jsonl, write_jsonl
from esgfin.schemas.pages import PageRecord


class ParseCache:
    def __init__(self, root: Path):
        self.root = root

    def path(self, pdf_sha256: str, parser: str, parser_version: str) -> Path:
        safe_version = parser_version.replace("/", "_")
        return self.root / pdf_sha256[:2] / pdf_sha256 / f"{parser}-{safe_version}.jsonl"

    def get(self, pdf_sha256: str, parser: str, parser_version: str) -> list[PageRecord] | None:
        p = self.path(pdf_sha256, parser, parser_version)
        if not p.exists():
            return None
        try:
            pages = read_jsonl(p, PageRecord)
        except (ValidationError, ValueError, OSError):
            return None
        if not pages or any(pg.parser != parser or pg.parser_version != parser_version for pg in pages):
            return None
        return pages

    def put(self, pdf_sha256: str, parser: str, parser_version: str, pages: list[PageRecord]) -> None:
        write_jsonl(self.path(pdf_sha256, parser, parser_version), pages)
