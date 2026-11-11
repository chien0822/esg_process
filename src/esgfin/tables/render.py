"""把 Docling 格子與重建表轉成給 LLM 看的文字（S3a 輸入、S3 輸入）。"""

from __future__ import annotations

from esgfin.schemas.pages import PageRecord, PageTable
from esgfin.schemas.tables import ReconstructedTable

EMPTY = "（空）"


def grid_text(table: PageTable) -> str:
    lines = [f"（{table.num_rows} 列 × {table.num_cols} 欄；列號、欄號從 0 起算）"]
    for r in range(table.num_rows):
        cols = []
        for c in range(table.num_cols):
            cell = next((x for x in table.cells if x.start_row <= r < x.end_row and x.start_col <= c < x.end_col), None)
            if cell is None or not cell.text.strip():
                cols.append(f"欄{c}={EMPTY}")
            elif cell.start_row != r or cell.start_col != c:
                cols.append(f"欄{c}=（與列{cell.start_row}欄{cell.start_col}為同一格）")
            else:
                span = f"（Docling 標示跨列 {cell.start_row}–{cell.end_row - 1}）" if cell.end_row - cell.start_row > 1 else ""
                cols.append(f"欄{c}={' '.join(cell.text.split())}{span}")
        lines.append(f"列{r}：" + "｜".join(cols))
    return "\n".join(lines)


def page_block(page: PageRecord) -> str:
    parts = [f"===== PDF 第 {page.pdf_page} 頁 =====", "【text_md（Docling 輸出的 Markdown）】", page.text_md.strip()]
    if page.table_cells:
        for i, t in enumerate(page.table_cells, start=1):
            parts += [f"【Docling 格子表 {i}】", grid_text(t)]
    else:
        parts.append("【Docling 格子表】（該頁沒有表格）")
    return "\n".join(parts)


def reconstructed_text(table: ReconstructedTable) -> str:
    """S3 的輸入：重建表的純文字。"""
    lines = ["表頭（欄序：欄名）："]
    lines += [f"  {i}：{h}" for i, h in enumerate(table.header, start=1)]
    lines.append("")
    lines.append("資料列（依表格由上而下）：")
    for k, row in enumerate(table.rows, start=1):
        lines.append(f"第 {k} 列（PDF 第 {row.pdf_page} 頁，頁內第 {row.row_on_page} 列）")
        for i, (h, v) in enumerate(zip(table.header, row.cells), start=1):
            lines.append(f"  欄{i}「{h}」：{v if v is not None else '（空白）'}")
    return "\n".join(lines)
