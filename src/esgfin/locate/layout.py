"""PDF 原始文字層的版面分析（S2 第 4、8 步；SPEC 17.10）。

01_pages.jsonl 不含頁首頁尾（17.9），所以印刷頁碼、章節導覽列與標題比對一律直接讀 PyMuPDF 文字層，
以位置判斷頁首、頁尾與側邊欄。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from esgfin.textnorm import normalize

_PAGE_NUMBER = re.compile(r"^\d{1,3}$")


@dataclass
class MarginLine:
    text: str
    color: int  # 第一個非空白 span 的顏色
    region: str  # top / bottom / left / right


@dataclass
class RawPage:
    pdf_page: int
    text: str  # 整頁文字層（含頁首頁尾與側邊欄）
    margin: list[MarginLine] = field(default_factory=list)


@dataclass
class Layout:
    page_count: int
    pages: dict[int, RawPage]
    nav_tokens: set[str]  # 導覽列詞（正規化後）
    printed_numbers: dict[int, list[int]]  # PDF 頁序 → 邊緣找到的頁碼候選
    current_nav: dict[int, str]  # PDF 頁序 → 以顏色標示的目前章節（找不到則無此鍵）


def _region(bbox, w: float, h: float, ratio: float) -> str | None:
    x0, y0, x1, y1 = bbox
    if y1 <= h * ratio:
        return "top"
    if y0 >= h * (1 - ratio):
        return "bottom"
    if x1 <= w * ratio:
        return "left"
    if x0 >= w * (1 - ratio):
        return "right"
    return None


def read_raw_pages(pdf: Path, margin_ratio: float) -> tuple[int, dict[int, RawPage]]:
    import pymupdf

    pages: dict[int, RawPage] = {}
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, start=1):
            w, h = page.rect.width, page.rect.height
            margin = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    spans = [s for s in line["spans"] if s["text"].strip()]
                    if not spans:
                        continue
                    region = _region(line["bbox"], w, h, margin_ratio)
                    if region is None:
                        continue
                    text = "".join(s["text"] for s in spans).strip()
                    margin.append(MarginLine(text=text, color=spans[0]["color"], region=region))
            pages[i] = RawPage(pdf_page=i, text=page.get_text(), margin=margin)
        return doc.page_count, pages


def analyze(pdf: Path, margin_ratio: float, nav_min_pages: int) -> Layout:
    page_count, pages = read_raw_pages(pdf, margin_ratio)

    # 導覽列：邊緣中在多頁重複出現的詞（以空白切開；純數字為頁碼，不算）
    token_pages: dict[str, set[int]] = defaultdict(set)
    for n, p in pages.items():
        for m in p.margin:
            for tok in m.text.split():
                t = normalize(tok)
                if t and not t.isdigit():
                    token_pages[t].add(n)
    nav_tokens = {t for t, ps in token_pages.items() if len(ps) >= nav_min_pages}

    printed = {
        n: [int(m.text) for m in p.margin if m.region in ("top", "bottom") and _PAGE_NUMBER.match(m.text)]
        for n, p in pages.items()
    }
    return Layout(page_count, pages, nav_tokens, printed, _current_nav_by_color(pages, nav_tokens))


def _current_nav_by_color(pages: dict[int, RawPage], nav_tokens: set[str]) -> dict[int, str]:
    """導覽列項目中，顏色與該項目平常顏色不同的那一個＝目前章節（例如瑞昱頁首）。

    同一頁恰好一個項目變色時才採用；沒有變色的報告書（例如廣達側邊欄）回傳空 dict。
    """
    usual: dict[str, Counter] = defaultdict(Counter)
    for p in pages.values():
        for m in p.margin:
            if normalize(m.text) and all(normalize(t) in nav_tokens for t in m.text.split()):
                usual[m.text][m.color] += 1
    out: dict[int, str] = {}
    for n, p in pages.items():
        odd = {
            m.text
            for m in p.margin
            if m.text in usual and len(usual[m.text]) > 1 and m.color != usual[m.text].most_common(1)[0][0]
        }
        if len(odd) == 1:
            out[n] = odd.pop()
    return out


def page_offset(layout: Layout, min_agreement: float) -> tuple[int | None, str]:
    """位移 = PDF 頁序 − 印刷頁碼，取眾數（SPEC 6.1 第 4 步）。"""
    votes = Counter(n - v for n, vals in layout.printed_numbers.items() for v in vals if 0 < v <= n + 10)
    pages_with_number = sum(1 for vals in layout.printed_numbers.values() if vals)
    if not votes:
        return None, "頁首頁尾找不到印刷頁碼"
    value, count = votes.most_common(1)[0]
    agree = sum(1 for n, vals in layout.printed_numbers.items() if any(n - v == value for v in vals))
    note = f"{layout.page_count} 頁中 {pages_with_number} 頁在頁首頁尾找到頁碼，其中 {agree} 頁符合位移 {value}"
    others = [f"{k}（{c} 頁）" for k, c in votes.most_common(4)[1:]]
    if others:
        note += f"；其他位移：{'、'.join(others)}"
    if agree / layout.page_count < min_agreement:
        return None, note + f"；符合比例低於 {min_agreement}，不採用"
    return value, note


def is_nav_line(line: str, nav_tokens: set[str]) -> bool:
    """一行（去掉 Markdown 標題符號後）全部由導覽列詞組成。"""
    toks = [normalize(t) for t in line.strip().lstrip("#").split()]
    toks = [t for t in toks if t]
    return bool(toks) and all(t in nav_tokens for t in toks)


_LINE_MARKER = re.compile(r"^\s*(#+|[-*•])\s*")


def line_key(line: str) -> str:
    """去掉 Markdown 標題與清單符號後正規化，用於比對重複行。"""
    return normalize(_LINE_MARKER.sub("", line))


def repeated_lines(texts: list[str], min_pages: int) -> set[str]:
    """在至少 min_pages 頁出現的非表格行（例如廣達側邊欄的「3.1.1 氣候變遷管理」）。

    以位置找導覽列時，側邊欄文字若超出邊緣範圍就抓不到；這裡改以「多頁重複」補足。
    """
    pages_per_line: Counter = Counter()
    for text in texts:
        keys = {line_key(line) for line in text.splitlines() if line.strip() and not line.lstrip().startswith("|")}
        pages_per_line.update(k for k in keys if k)
    return {k for k, c in pages_per_line.items() if c >= min_pages}


def strip_nav(text_md: str, nav_tokens: set[str], repeated: set[str] = frozenset()) -> tuple[str, list[str]]:
    """移除整行為導覽列或多頁重複的行（表格列不動）。回傳（剩下的文字, 被移除的行）。"""
    kept, removed = [], []
    for line in text_md.splitlines():
        if not line.lstrip().startswith("|") and (is_nav_line(line, nav_tokens) or (line.strip() and line_key(line) in repeated)):
            removed.append(line.strip())
        else:
            kept.append(line)
    return "\n".join(kept), removed
