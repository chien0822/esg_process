"""引文驗證（SPEC 10.2；P1、P2 與 S3a 的文字存在檢查共用）。

正規化後為子字串 → 完全通過；否則以「覆蓋比例」做模糊比對：
反覆在原文中找引文剩餘片段的最長共同子字串（不要求先後順序，處理項目順序不同的情形，見 SPEC 17.9 第 9 點），
覆蓋的字數 / 引文字數 ≥ check.fuzzy_pass_ratio → 模糊通過，否則未通過。
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from esgfin.constants import CITATION_EXACT, CITATION_FAIL, CITATION_FUZZY
from esgfin.textnorm import normalize

MIN_PIECE = 4  # 短於此長度的共同片段不計入覆蓋（避免單字湊數）


@dataclass(frozen=True)
class Match:
    level: str
    ratio: float


def coverage_ratio(quote_norm: str, text_norm: str) -> float:
    """兩者皆已正規化。回傳引文被原文片段覆蓋的比例（0–1）。"""
    if not quote_norm:
        return 1.0
    if not text_norm:
        return 0.0
    covered = 0
    pieces = [quote_norm]
    while pieces:
        best = None
        for i, piece in enumerate(pieces):
            m = SequenceMatcher(None, piece, text_norm, autojunk=False).find_longest_match(0, len(piece), 0, len(text_norm))
            if best is None or m.size > best[1].size:
                best = (i, m)
        i, m = best
        if m.size < MIN_PIECE:
            break
        piece = pieces.pop(i)
        covered += m.size
        pieces += [p for p in (piece[: m.a], piece[m.a + m.size :]) if len(p) >= MIN_PIECE]
    return covered / len(quote_norm)


def match_quote(quote: str, text: str, fuzzy_ratio: float) -> Match:
    q, t = normalize(quote), normalize(text)
    if q in t:
        return Match(CITATION_EXACT, 1.0)
    r = coverage_ratio(q, t)
    return Match(CITATION_FUZZY if r >= fuzzy_ratio else CITATION_FAIL, round(r, 4))


def same_text(a: str, b: str, fuzzy_ratio: float) -> Match:
    """兩段文字是否相同（正規化後相等，或雙向覆蓋比例都達門檻）。用於長文字儲存格的比對。"""
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return Match(CITATION_EXACT, 1.0)
    r = min(coverage_ratio(na, nb), coverage_ratio(nb, na))
    return Match(CITATION_FUZZY if r >= fuzzy_ratio else CITATION_FAIL, round(r, 4))
