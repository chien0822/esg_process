"""S3a 表格重建（SPEC 7.6）：03a_tables.json 與 03a_table_check.json。

TableDraft 是 LLM 的 structured output（一張表一次呼叫）；
ReconstructedTables 是階段輸出，可被 overrides/<report_id>/03a_tables.json 取代。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from esgfin.constants import CITATION_LEVELS

from .base import StrictModel, ZhModel, zh


class TableRow(StrictModel):
    pdf_page: int = Field(ge=1)
    row_on_page: int = Field(ge=1)  # 只算資料列，從 1 起算
    cells: list[str | None]  # 與 header 等長；合併格展開到每一列；原表真正空白填 null


def _check_cells(header: list[str], rows: list[TableRow]) -> None:
    n = len(header)
    bad = [f"PDF 第 {r.pdf_page} 頁第 {r.row_on_page} 列有 {len(r.cells)} 格" for r in rows if len(r.cells) != n]
    if bad:
        raise ValueError(f"每列的 cells 必須與 header 等長（{n} 欄）：" + "；".join(bad))


class TableDraft(StrictModel):
    """LLM 回應。merge_notes 在最前面：先說明合併儲存格的判讀依據，再輸出表格。"""

    merge_notes: str  # 例如「類型欄：PDF 上第 1–3 列為同一格『轉型風險』」
    header: list[str] = Field(min_length=1)  # 原表表頭逐字；群組表頭跨兩欄時兩欄填同一字串
    rows: list[TableRow] = Field(min_length=1)

    @model_validator(mode="after")
    def _cells_match_header(self):
        _check_cells(self.header, self.rows)
        return self


class ReconstructedTable(StrictModel):
    pages: list[int] = Field(min_length=1)
    header: list[str] = Field(min_length=1)
    rows: list[TableRow] = Field(min_length=1)
    merge_notes: str | None = None

    @model_validator(mode="after")
    def _cells_match_header(self):
        _check_cells(self.header, self.rows)
        return self


class ReconstructedTables(StrictModel):
    report_id: str
    input_mode: Literal["text", "text+pdf", "override"]
    tables: list[ReconstructedTable] = Field(min_length=1)


# ---------------------------------------------------------------- 檢查結果

CHECK_TEXT = "文字存在"
CHECK_SHAPE = "欄數與列數"
CHECK_COLUMN = "逐欄對照"
CHECK_HEADER_AS_ROW = "表頭不當資料列"
CHECK_NAMES: tuple[str, ...] = (CHECK_TEXT, CHECK_SHAPE, CHECK_COLUMN, CHECK_HEADER_AS_ROW)


class CheckProblem(ZhModel):
    check: Literal[CHECK_NAMES] = zh("檢查")
    pdf_page: int | None = zh("PDF頁")
    row_on_page: int | None = zh("頁內列", default=None)
    column: int | None = zh("欄序", default=None)  # 從 1 起算
    message: str = zh("說明")


class FuzzyCell(ZhModel):
    pdf_page: int = zh("PDF頁")
    row_on_page: int = zh("頁內列")
    column: int = zh("欄序")
    check: Literal[CHECK_NAMES] = zh("檢查")
    level: Literal[CITATION_LEVELS] = zh("等級")
    ratio: float = zh("比例")


class BoundaryRow(ZhModel):
    """值無法由 Docling 放值位置唯一決定的格子（不算錯誤；人工標註量測的部分）。"""

    pdf_page: int = zh("PDF頁")
    row_on_page: int = zh("頁內列")
    column: int = zh("欄序")
    header: str = zh("欄名")
    candidates: list[str] = zh("可能的值")


class PageCheck(ZhModel):
    pdf_page: int = zh("PDF頁")
    docling_table_index: int | None = zh("Docling表格序")  # 該頁 table_cells 的第幾張（0 起算）
    docling_header_rows: list[int] = zh("Docling表頭列")  # Docling 列號（0 起算）
    docling_data_rows: list[int] = zh("Docling資料列")
    docling_empty_rows: list[int] = zh("Docling空白列", default_factory=list)  # 沒有任何文字的列，不算資料列
    llm_rows: int = zh("重建列數")


class AttemptCheck(ZhModel):
    attempt: int = zh("第幾次")  # 0 = 首次
    passed: bool = zh("通過")
    problems: list[CheckProblem] = zh("問題")


class TableCheckResult(ZhModel):
    pages: list[int] = zh("頁碼")
    passed: bool = zh("通過")
    attempts: list[AttemptCheck] = zh("各次檢查")  # 最後一筆為最終結果
    page_checks: list[PageCheck] = zh("各頁")  # 最終結果
    fuzzy_cells: list[FuzzyCell] = zh("模糊通過格子")
    boundary_rows: list[BoundaryRow] = zh("邊界待確認")


class TableCheckReport(ZhModel):
    report_id: str = zh("report_id")
    input_mode: Literal["text", "text+pdf", "override"] = zh("input_mode")
    passed: bool = zh("通過")
    tables: list[TableCheckResult] = zh("表格")
