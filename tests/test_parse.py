"""M1：S1 解析階段（文字層檢查、解析快取、run.json 記錄）。不載入 Docling。"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pymupdf

from esgfin.constants import REPORT_STATUS_FAILED
from esgfin.parse.docling_parser import _ranges
from esgfin.parse.stage import run_parse
from esgfin.pipeline.runner import RunOptions
from esgfin.schemas.base import read_jsonl
from esgfin.schemas.pages import PageRecord

from fakes import fake_registry

REALTEK = "realtek_2024_ESG"


def write_pdf(path: Path, texts: list[str]) -> None:
    doc = pymupdf.open()
    for t in texts:
        page = doc.new_page()
        if t:
            page.insert_text((72, 72), t)
    doc.save(path)


class CountingParser:
    name = "pymupdf4llm"
    version = "test-1"

    def __init__(self):
        self.calls = 0

    def parse(self, pdf, pages=None):
        self.calls += 1
        with pymupdf.open(pdf) as doc:
            return [
                PageRecord(pdf_page=i + 1, text_md=p.get_text(), parser=self.name, parser_version=self.version)
                for i, p in enumerate(doc)
            ]


def setup(project: Path, texts: list[str]):
    for f in ("realtek_2024_ESG.pdf", "Quanta_2024_ESG.pdf"):
        write_pdf(project / "report" / f, texts)
    parser = CountingParser()
    registry = fake_registry(parse=partial(run_parse, parser_factory=lambda name: parser))
    return parser, registry


def test_parse_writes_pages_and_uses_cache_in_next_run(project, make_runner):
    parser, registry = setup(project, ["Climate risk and opportunity page one", "Second page with table text"])
    opts = RunOptions(reports=["realtek_2024_ESG.pdf"], build_site=False)

    first = make_runner(project, registry=registry).run(opts).record
    stage = first.reports[REALTEK].stages["parse"]
    assert stage.status == "completed" and stage.parse_cache_hit is False
    pages = read_jsonl(project / "runs" / first.run_id / REALTEK / "01_pages.jsonl", PageRecord)
    assert [p.pdf_page for p in pages] == [1, 2]
    assert "Climate risk" in pages[0].text_md

    second = make_runner(project, registry=registry).run(opts).record
    assert second.reports[REALTEK].stages["parse"].parse_cache_hit is True
    assert parser.calls == 1  # 第二個 run 沒有重新解析


def test_scanned_pdf_stops_report(project, make_runner):
    _, registry = setup(project, ["", "", "only one page has a text layer here"])
    rec = make_runner(project, registry=registry).run(RunOptions(reports=["realtek_2024_ESG.pdf"], build_site=False)).record
    r = rec.reports[REALTEK]
    assert r.status == REPORT_STATUS_FAILED
    assert "沒有文字層" in r.error and "OCR" in r.error
    assert "locate" not in r.stages


def test_docling_page_ranges_are_contiguous_and_bounded():
    assert _ranges([1, 2, 3, 7, 8, 10], 25) == [(1, 3), (7, 8), (10, 10)]
    assert _ranges(list(range(1, 61)), 25) == [(1, 25), (26, 50), (51, 60)]
