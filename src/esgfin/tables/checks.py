"""S3a 程式檢查（SPEC 7.6 第 5 點）：以 Docling 格子檢查 LLM 重建的表格。

每頁分別檢查：
1. 文字存在：非空白格子須出現在該頁文字（text_md，必要時加 PyMuPDF 原始文字層）
2. 欄數與列數：欄數＝Docling 欄數；每頁資料列數＝Docling 資料列數（表頭列以文字比對判斷，不依 column_header 旗標）
3. 逐欄對照 Docling 放值位置：每欄把重建值切成連續相同的段，每段須有 Docling 放值（或為跨頁延續），
   段內 Docling 的值須等於該段的值（可為多個片段依序相接），Docling 的格子不得跨兩段
4. 表頭不當資料列
另記錄邊界待確認列（不算錯誤）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from esgfin.check.citation import coverage_ratio, same_text
from esgfin.constants import CITATION_FAIL, CITATION_FUZZY
from esgfin.schemas.pages import PageRecord, PageTable, TableCell
from esgfin.schemas.tables import (
    CHECK_COLUMN,
    CHECK_HEADER_AS_ROW,
    CHECK_SHAPE,
    CHECK_TEXT,
    BoundaryRow,
    CheckProblem,
    FuzzyCell,
    PageCheck,
    ReconstructedTable,
    TableRow,
)
from esgfin.textnorm import normalize


@dataclass
class Placement:
    """Docling 在某欄放的一個值：涵蓋的資料列（1 起算）與文字。"""

    rows: list[int]
    text: str

    @property
    def first(self) -> int:
        return self.rows[0]


@dataclass
class DoclingPage:
    table_index: int
    table: PageTable
    header_rows: list[int]
    data_rows: list[int]  # Docling 列號（0 起算），依序對應資料列 1、2、3…
    empty_rows: list[int]

    def placements(self, col: int) -> list[Placement]:
        pos = {r: k for k, r in enumerate(self.data_rows, start=1)}
        out = []
        for c in self.table.cells:
            if not (c.start_col <= col < c.end_col) or not normalize(c.text):
                continue
            rows = sorted(pos[r] for r in range(c.start_row, c.end_row) if r in pos)
            if rows:
                out.append(Placement(rows, c.text))
        return sorted(out, key=lambda p: p.first)


@dataclass
class CheckOutcome:
    problems: list[CheckProblem] = field(default_factory=list)
    page_checks: list[PageCheck] = field(default_factory=list)
    fuzzy: list[FuzzyCell] = field(default_factory=list)
    boundary: list[BoundaryRow] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems


# ---------------------------------------------------------------- 表頭與資料列


def _is_match(a: str, b: str, ratio: float) -> bool:
    return same_text(a, b, ratio).level != CITATION_FAIL


def header_rows(table: PageTable, header: list[str], ratio: float) -> list[int]:
    """某列中有文字的格子（以起始列計），過半數等於 header 對應欄的值 → 表頭列。"""
    out = []
    for r in range(table.num_rows):
        cells = [c for c in table.cells if c.start_row == r and normalize(c.text)]
        if not cells:
            continue
        matched = sum(
            1 for c in cells if any(col < len(header) and _is_match(c.text, header[col], ratio) for col in range(c.start_col, c.end_col))
        )
        if matched * 2 > len(cells):
            out.append(r)
    return out


def _covering(table: PageTable, r: int) -> list[TableCell]:
    return [c for c in table.cells if c.start_row <= r < c.end_row and normalize(c.text)]


def docling_page(page: PageRecord, header: list[str], ratio: float) -> tuple[DoclingPage | None, str | None]:
    """該頁與重建表對應的 Docling 表格：欄數相同、且找得到表頭列；多張符合時取資料列最多者。"""
    found: list[DoclingPage] = []
    for i, t in enumerate(page.table_cells):
        if t.num_cols != len(header):
            continue
        hdr = header_rows(t, header, ratio)
        if not hdr:
            continue
        rest = [r for r in range(t.num_rows) if r not in hdr]
        empty = [r for r in rest if not _covering(t, r)]
        found.append(DoclingPage(i, t, hdr, [r for r in rest if r not in empty], empty))
    if not found:
        shapes = "、".join(f"{t.num_cols} 欄" for t in page.table_cells) or "沒有表格"
        return None, f"Docling 在 PDF 第 {page.pdf_page} 頁找不到 {len(header)} 欄且表頭相符的表格（該頁：{shapes}）"
    return max(found, key=lambda d: len(d.data_rows)), None


# ---------------------------------------------------------------- 逐欄對照


def _join_matches(key: str, texts: list[str]) -> bool:
    """texts 依序相接後能切成一或多段，每段都等於 key（處理「極端性」＋「氣候」與每列重印同值）。"""
    acc = ""
    for t in texts:
        acc += normalize(t)
        if acc == key:
            acc = ""
        elif not key.startswith(acc):
            return False
    return acc == ""


def _segments(values: list[str | None]) -> list[tuple[str | None, str | None, int, int]]:
    """連續相同（正規化後）的段：(key, 原值, 起列, 迄列)，列為 1 起算。"""
    out: list[tuple[str | None, str | None, int, int]] = []
    for k, v in enumerate(values, start=1):
        key = normalize(v) if v is not None else None
        key = key or None
        if out and out[-1][0] == key:
            out[-1] = (key, out[-1][1], out[-1][2], k)
        else:
            out.append((key, v, k, k))
    return out


def check_column(
    page_no: int, col: int, header: str, values: list[str | None], placements: list[Placement], prev: str | None, ratio: float, out: CheckOutcome
) -> None:
    segs = _segments(values)
    seg_of = {k: i for i, (_, _, a, b) in enumerate(segs) for k in range(a, b + 1)}
    prev_key = normalize(prev) if prev else None

    def problem(msg: str, row: int | None = None) -> None:
        out.problems.append(CheckProblem(check=CHECK_COLUMN, pdf_page=page_no, row_on_page=row, column=col + 1, message=f"第 {col + 1} 欄「{header}」：{msg}"))

    for p in placements:
        if len({seg_of[k] for k in p.rows}) > 1:
            problem(f"Docling 的格子「{p.text}」涵蓋第 {p.rows[0]}–{p.rows[-1]} 列，但重建表在這些列之間換了值", p.first)

    for i, (key, value, a, b) in enumerate(segs):
        inside = [p for p in placements if seg_of[p.first] == i]
        if key is None:
            if inside:
                problem(f"重建表第 {a}–{b} 列為空白，但 Docling 在第 {inside[0].first} 列有「{inside[0].text}」", a)
            continue
        if not inside:
            if a == 1 and prev_key is not None and key == prev_key:
                continue  # 跨頁延續前一頁最後一列的值
            problem(f"重建表第 {a}–{b} 列為「{value}」，但 Docling 在這些列沒有放值（也不是前一頁最後一列的值）", a)
            continue
        texts = [p.text for p in inside]
        if _join_matches(key, texts):
            continue
        whole = same_text(value, "".join(texts), ratio)
        each = [same_text(value, t, ratio) for t in texts]
        if whole.level != CITATION_FAIL:
            out.fuzzy.append(FuzzyCell(pdf_page=page_no, row_on_page=a, column=col + 1, check=CHECK_COLUMN, level=CITATION_FUZZY, ratio=whole.ratio))
        elif all(m.level != CITATION_FAIL for m in each):
            out.fuzzy.append(FuzzyCell(pdf_page=page_no, row_on_page=a, column=col + 1, check=CHECK_COLUMN, level=CITATION_FUZZY, ratio=min(m.ratio for m in each)))
        else:
            shown = "／".join(f"第 {p.first} 列「{p.text}」" for p in inside)
            problem(f"重建表第 {a}–{b} 列為「{value}」，與 Docling 在這些列的值不符（{shown}）", a)

    # 邊界待確認：沒有 Docling 放值的列，上方（或前一頁延續）與下方最近的值不同
    covered = {k for p in placements for k in p.rows}
    for k in range(1, len(values) + 1):
        if k in covered:
            continue
        above = next((p.text for p in reversed(placements) if p.rows[-1] < k), prev)
        below = next((p.text for p in placements if p.first > k), None)
        if above is not None and below is not None and normalize(above) != normalize(below):
            out.boundary.append(BoundaryRow(pdf_page=page_no, row_on_page=k, column=col + 1, header=header, candidates=[above, below]))


# ---------------------------------------------------------------- 主函式


def _page_rows(table: ReconstructedTable, page_no: int) -> list[TableRow]:
    return [r for r in table.rows if r.pdf_page == page_no]


def check_table(table: ReconstructedTable, pages: dict[int, PageRecord], raw_text: dict[int, str], ratio: float) -> CheckOutcome:
    out = CheckOutcome()
    header = table.header
    hnorm = [normalize(h) for h in header]

    stray = sorted({r.pdf_page for r in table.rows} - set(table.pages))
    if stray:
        out.problems.append(CheckProblem(check=CHECK_SHAPE, pdf_page=stray[0], message=f"重建表有列標在策略表以外的頁：{stray}"))

    prev_last: TableRow | None = None
    for page_no in table.pages:
        rows = _page_rows(table, page_no)
        page = pages.get(page_no)
        if page is None:
            out.problems.append(CheckProblem(check=CHECK_SHAPE, pdf_page=page_no, message=f"01_pages.jsonl 沒有 PDF 第 {page_no} 頁"))
            continue
        if [r.row_on_page for r in rows] != list(range(1, len(rows) + 1)):
            out.problems.append(
                CheckProblem(check=CHECK_SHAPE, pdf_page=page_no, message=f"row_on_page 須從 1 起連續遞增，實際為 {[r.row_on_page for r in rows]}")
            )

        # 4. 表頭不當資料列
        for r in rows:
            cells = [(normalize(v), h) for v, h in zip(r.cells, hnorm) if v is not None and normalize(v)]
            if cells and sum(1 for v, h in cells if v == h) * 2 > len(cells):
                out.problems.append(CheckProblem(check=CHECK_HEADER_AS_ROW, pdf_page=page_no, row_on_page=r.row_on_page, message="這一列與表頭相同，表頭不是資料列"))

        dp, why = docling_page(page, header, ratio)
        if dp is None:
            out.problems.append(CheckProblem(check=CHECK_SHAPE, pdf_page=page_no, message=why or ""))
            out.page_checks.append(PageCheck(pdf_page=page_no, docling_table_index=None, docling_header_rows=[], docling_data_rows=[], llm_rows=len(rows)))
        else:
            out.page_checks.append(
                PageCheck(pdf_page=page_no, docling_table_index=dp.table_index, docling_header_rows=dp.header_rows,
                          docling_data_rows=dp.data_rows, docling_empty_rows=dp.empty_rows, llm_rows=len(rows))
            )

        # 1. 文字存在
        page_text = normalize(page.text_md) + "\n" + normalize(raw_text.get(page_no, ""))
        prev_text = ""
        if prev_last is not None and prev_last.pdf_page in pages:
            prev_text = normalize(pages[prev_last.pdf_page].text_md) + normalize(raw_text.get(prev_last.pdf_page, ""))
        for r in rows:
            for c, v in enumerate(r.cells):
                key = normalize(v) if v is not None else ""
                if not key or key in page_text:
                    continue
                if dp is not None and _in_adjacent_join(key, dp.placements(c)):
                    continue
                continuing = prev_last is not None and c < len(prev_last.cells) and prev_last.cells[c] is not None and normalize(prev_last.cells[c]) == key
                if continuing and key in prev_text:
                    continue
                cov = coverage_ratio(key, page_text)
                if cov >= ratio:
                    out.fuzzy.append(FuzzyCell(pdf_page=page_no, row_on_page=r.row_on_page, column=c + 1, check=CHECK_TEXT, level=CITATION_FUZZY, ratio=round(cov, 4)))
                else:
                    out.problems.append(
                        CheckProblem(check=CHECK_TEXT, pdf_page=page_no, row_on_page=r.row_on_page, column=c + 1,
                                     message=f"第 {r.row_on_page} 列第 {c + 1} 欄「{v}」不在該頁文字中（覆蓋比例 {cov:.2f}）")
                    )

        # 2. 列數；3. 逐欄對照
        if dp is not None:
            if len(dp.data_rows) != len(rows):
                out.problems.append(
                    CheckProblem(check=CHECK_SHAPE, pdf_page=page_no,
                                 message=f"重建表在 PDF 第 {page_no} 頁有 {len(rows)} 個資料列，Docling 有 {len(dp.data_rows)} 個（Docling 表頭列：{dp.header_rows}）")
                )
            else:
                for c in range(len(header)):
                    prev = prev_last.cells[c] if prev_last is not None else None
                    check_column(page_no, c, header[c], [r.cells[c] for r in rows], dp.placements(c), prev, ratio, out)
        if rows:
            prev_last = rows[-1]
    return out


def _in_adjacent_join(key: str, placements: list[Placement]) -> bool:
    """key 等於同欄相鄰幾個 Docling 格子依序相接（例如「極端性」＋「氣候」）。"""
    texts = [normalize(p.text) for p in placements]
    for i in range(len(texts)):
        acc = ""
        for t in texts[i:]:
            acc += t
            if acc == key:
                return True
            if not key.startswith(acc):
                break
    return False


def problems_text(problems: list[CheckProblem]) -> str:
    return "\n".join(f"- [{p.check}] {p.message}" for p in problems)


__all__ = ["CheckOutcome", "check_table", "docling_page", "header_rows", "problems_text"]
