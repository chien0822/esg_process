"""目錄取得、規則檢查與頁碼範圍（S2 第 1、3、4、8 步）。全部是程式，不用 LLM。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from esgfin.constants import (
    FLAG_CORRECTED_BY_CHILD,
    FLAG_CORRECTED_BY_NAV,
    FLAG_NAV_MISMATCH,
    FLAG_NO_PAGE,
    FLAG_NOT_INCREASING,
    FLAG_OUT_OF_RANGE,
    FLAG_PARENT_AFTER_CHILD,
    FLAG_TITLE_NOT_FOUND,
    FLAG_UNCERTAIN_PAGE,
    TOC_PAGE_KEYWORDS,
)
from esgfin.textnorm import normalize

from .layout import Layout

_CODE_LIKE = re.compile(r"^[A-Za-z]{0,3}\s*\d+(\.\d+)*$")  # 「ch1」「1.1」：只有編號、沒有標題
_SECTION_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)*)")
# 編號後多餘的點：「2.1 .公司治理」（廣達目錄文字層原文如此）
_DOT_AFTER_NUMBER = re.compile(r"^(\s*\d+(?:\.\d+)*)\s*[.．、](?!\d)\s*")  # 點後不可接數字，否則「1.1」會被拆開
_NUMBER = re.compile(r"(?<!\d)\d{1,3}(?!\d)")
# 目錄的頁碼行：整行只有 1–3 位數字，或以點線接頁碼結尾（「1.1 關於廣達. . . .09」）
_PAGE_NUMBER_LINE = re.compile(r"^\d{1,3}$|([.．…·]\s*){3,}\d{1,3}$")


@dataclass
class TocEntry:
    title: str
    level: int
    evidence: str
    printed_page: int | None = None  # 目錄給的（toc_page）
    pdf_page: int | None = None  # 書籤給的，或由印刷頁碼加位移換算
    uncertain: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    pdf_end: int | None = None


# ---------------------------------------------------------------- 第 1 步


def bookmarks_usable(toc: list[list], min_entries: int) -> tuple[bool, str]:
    if len(toc) < min_entries:
        return False, f"書籤只有 {len(toc)} 項（少於 {min_entries}）"
    meaningful = [t for t in toc if not _CODE_LIKE.match(str(t[1]).strip())]
    if len(meaningful) * 2 < len(toc):
        return False, f"書籤 {len(toc)} 項中只有 {len(meaningful)} 項有標題文字（其餘只有編號）"
    return True, f"書籤 {len(toc)} 項"


def _page_number_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if _PAGE_NUMBER_LINE.search(line.strip()))


def find_toc_pages(layout: Layout, scan_pages: int, min_numbers: int) -> list[int]:
    """前 scan_pages 頁中第一個含「目錄／Contents」且頁碼數字夠多的頁。

    之後緊接的頁若頁碼行夠多，視為目錄的續頁（跨頁目錄常只有第一頁寫「目錄」，例如瑞昱 p.3–4）。
    """
    last = min(scan_pages, layout.page_count)
    first = next(
        (
            n
            for n in range(1, last + 1)
            if any(k in layout.pages[n].text for k in TOC_PAGE_KEYWORDS) and len(_NUMBER.findall(layout.pages[n].text)) >= min_numbers
        ),
        None,
    )
    if first is None:
        return []
    pages = [first]
    while pages[-1] < layout.page_count and _page_number_lines(layout.pages[pages[-1] + 1].text) >= min_numbers:
        pages.append(pages[-1] + 1)
    return pages


# ---------------------------------------------------------------- 第 3 步


def _children(entries: list[TocEntry], i: int) -> list[int]:
    out = []
    for j in range(i + 1, len(entries)):
        if entries[j].level <= entries[i].level:
            break
        out.append(j)
    return out


def _parent(entries: list[TocEntry], i: int) -> int | None:
    for j in range(i - 1, -1, -1):
        if entries[j].level < entries[i].level:
            return j
    return None


def check_rules(entries: list[TocEntry], page_attr: str, max_page: int) -> None:
    """同層頁碼遞增、章起始頁不大於第一個子項目、頁碼不超出總頁數。

    能以子項目校正的直接校正並標記；其餘只標記（第 4 步可再以導覽列校正）。
    """
    for e in entries:
        if "印刷頁碼" in e.uncertain:
            e.flags.append(FLAG_UNCERTAIN_PAGE)
        v = getattr(e, page_attr)
        if v is not None and not (1 <= v <= max_page):
            e.flags.append(FLAG_OUT_OF_RANGE)
            setattr(e, page_attr, None)

    for i, e in enumerate(entries):
        kids = [j for j in _children(entries, i) if getattr(entries[j], page_attr) is not None]
        if not kids:
            continue
        first_child = getattr(entries[kids[0]], page_attr)
        v = getattr(e, page_attr)
        if v is None or v > first_child:
            if v is not None:
                e.flags.append(FLAG_PARENT_AFTER_CHILD)
            setattr(e, page_attr, first_child)
            e.flags.append(FLAG_CORRECTED_BY_CHILD)

    last_by_parent: dict[int | None, int] = {}
    for i, e in enumerate(entries):
        v = getattr(e, page_attr)
        if v is None:
            continue
        parent = _parent(entries, i)
        prev = last_by_parent.get(parent)
        if prev is not None and v < prev:
            e.flags.append(FLAG_NOT_INCREASING)
        else:
            last_by_parent[parent] = v


# ---------------------------------------------------------------- 第 4 步


def to_pdf_pages(entries: list[TocEntry], offset: int | None, page_count: int) -> None:
    for e in entries:
        if e.pdf_page is None and e.printed_page is not None and offset is not None:
            e.pdf_page = e.printed_page + offset
        if e.printed_page is None and e.pdf_page is not None and offset is not None:
            e.printed_page = e.pdf_page - offset
        if e.pdf_page is not None and not (1 <= e.pdf_page <= page_count):
            e.flags.append(FLAG_OUT_OF_RANGE)
            e.pdf_page = None


def nav_ranges(layout: Layout, min_coverage: float) -> dict[str, tuple[int, int]]:
    """導覽列標示的各章範圍（正規化標題 → 最小、最大頁）。標示覆蓋率太低時不採用。"""
    if len(layout.current_nav) < layout.page_count * min_coverage:
        return {}
    out: dict[str, tuple[int, int]] = {}
    for n, title in layout.current_nav.items():
        k = normalize(title)
        lo, hi = out.get(k, (n, n))
        out[k] = (min(lo, n), max(hi, n))
    return out


def _nav_match(title: str, ranges: dict[str, tuple[int, int]]) -> tuple[int, int] | None:
    t = normalize(title)
    for k, r in ranges.items():
        if k == t or (len(k) >= 2 and (k in t or t in k)):
            return r
    return None


def apply_nav(entries: list[TocEntry], ranges: dict[str, tuple[int, int]], buffer: int) -> None:
    """第 1 層章節：規則檢查未解決時以導覽列校正起始頁；範圍與導覽列不符時標記。"""
    if not ranges:
        return
    for e in entries:
        if e.level != 1:
            continue
        r = _nav_match(e.title, ranges)
        if r is None:
            continue
        if e.pdf_page is None or FLAG_NOT_INCREASING in e.flags:
            e.pdf_page = r[0]
            e.flags.append(FLAG_CORRECTED_BY_NAV)


def compute_ends(entries: list[TocEntry], page_count: int) -> None:
    for i, e in enumerate(entries):
        if e.pdf_page is None:
            e.flags.append(FLAG_NO_PAGE)
            continue
        end = page_count
        for j in range(i + 1, len(entries)):
            nxt = entries[j]
            if nxt.level <= e.level and nxt.pdf_page is not None and nxt.pdf_page > e.pdf_page:
                end = nxt.pdf_page - 1
                break
        e.pdf_end = max(e.pdf_page, end)


def check_nav_ranges(entries: list[TocEntry], ranges: dict[str, tuple[int, int]], buffer: int) -> None:
    for e in entries:
        if e.level != 1 or e.pdf_page is None or e.pdf_end is None:
            continue
        r = _nav_match(e.title, ranges)
        if r is not None and (abs(r[0] - e.pdf_page) > buffer or abs(r[1] - e.pdf_end) > buffer + 1):
            e.flags.append(f"{FLAG_NAV_MISMATCH}（導覽列 p.{r[0]}–{r[1]}）")


# ---------------------------------------------------------------- 第 8 步


def check_titles(entries: list[TocEntry], layout: Layout, buffer: int) -> None:
    """標題（正規化）須出現在起始頁或其緩衝頁的原始文字層（含頁首頁尾）。"""
    for e in entries:
        if e.pdf_page is None:
            continue
        t = normalize(_DOT_AFTER_NUMBER.sub(r"\1 ", e.title))
        pages = range(max(1, e.pdf_page - buffer), min(layout.page_count, e.pdf_page + buffer) + 1)
        if t and not any(t in normalize(layout.pages[n].text) for n in pages):
            e.flags.append(FLAG_TITLE_NOT_FOUND)


def section_number(title: str) -> str | None:
    m = _SECTION_NUMBER.match(title)
    return m.group(1) if m else None


def sections_pointed(points_to: str, sections: list) -> list:
    """索引「指向」所指的章節：以章節編號比對（「3.1.2」），否則以標題比對（「詳見CH3 綠色營運」）。"""
    target = normalize(points_to)
    number = section_number(points_to)
    if number is not None and normalize(points_to) == number:
        return [s for s in sections if section_number(s.title) == number]
    return [s for s in sections if normalize(s.title) and normalize(s.title) in target]


def buffered(start: int, end: int, buffer: int, page_count: int) -> tuple[int, int]:
    return max(1, start - buffer), min(page_count, end + buffer)
