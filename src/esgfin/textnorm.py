"""文字正規化（SPEC 10.2）：全半形統一、去除空白換行與 Markdown 表格符號。

S2 的標題比對與 M5 的引文驗證共用此函式。
"""

from __future__ import annotations

import re
import unicodedata

_MD_TOKENS = re.compile(r"<br\s*/?>|[|*#`>_]")
_TABLE_RULE = re.compile(r"^[\s:\-]+$", re.MULTILINE)  # 表格分隔列 |---|:---|


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _MD_TOKENS.sub(" ", text)
    text = _TABLE_RULE.sub("", text)
    return "".join(text.split())
