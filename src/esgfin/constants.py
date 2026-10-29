"""所有會計科目、排除項目與封閉清單的唯一定義處（CLAUDE.md 必守規則 1）。

提示、schema、檢查模組一律引用本檔，不得在別處重複定義字串。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 會計科目（SPEC 9.2）【已決定】
# 科目名稱 = 括號前字串；括號內容為說明，供提示使用。
# 清單順序即排序準則第 5 項的平手順序。
# ---------------------------------------------------------------------------

BALANCE_SHEET = "資產負債表"
INCOME_STATEMENT = "綜合損益表"
STATEMENTS: tuple[str, ...] = (BALANCE_SHEET, INCOME_STATEMENT)  # 顯示順序固定

BALANCE_SHEET_ACCOUNTS: dict[str, str] = {
    "應收款項及合約資產": "應收票據、應收帳款、其他應收款、合約資產",
    "存貨": "",
    "生物資產": "",
    "金融資產及權益法投資": "透過損益按公允價值衡量、透過其他綜合損益按公允價值衡量、按攤銷後成本衡量、避險之金融資產；採用權益法之投資",
    "不動產、廠房及設備": "含使用權資產、投資性不動產",
    "無形資產及商譽": "",
    "待出售資產及相關負債": "待出售非流動資產、待分配予業主之非流動資產、與待出售非流動資產直接相關之負債",
    "借款及租賃負債": "短期借款、應付短期票券、應付公司債、長期借款、租賃負債",
    "應付款項及合約負債": "應付票據、應付帳款、其他應付款、合約負債",
    "負債準備": "",
    "金融負債": "透過損益按公允價值衡量之金融負債、避險之金融負債",
}

INCOME_STATEMENT_ACCOUNTS: dict[str, str] = {
    "營業收入": "",
    "營業成本": "",
    "研發費用": "",
    "推銷及管理費用": "推銷費用、管理費用",
    "資產減損及處分損益": "PP&E 減損損失、商譽減損損失、處分 PP&E 損益",
    "投資類損益": "權益法損益份額、利息收入、投資性不動產租金收入與處分損益、股利收入、透過損益按公允價值衡量之金融資產（負債）淨損益、除列按攤銷後成本衡量之金融資產淨損益、金融資產重分類淨損益",
    "利息費用": "借款、租賃負債、淨確定福利負債之利息費用",
    "停業單位損益": "",
}

BALANCE_SHEET_ACCOUNT_NAMES: tuple[str, ...] = tuple(BALANCE_SHEET_ACCOUNTS)
INCOME_STATEMENT_ACCOUNT_NAMES: tuple[str, ...] = tuple(INCOME_STATEMENT_ACCOUNTS)
ACCOUNTS_BY_STATEMENT: dict[str, tuple[str, ...]] = {
    BALANCE_SHEET: BALANCE_SHEET_ACCOUNT_NAMES,
    INCOME_STATEMENT: INCOME_STATEMENT_ACCOUNT_NAMES,
}
ALL_ACCOUNT_NAMES: tuple[str, ...] = BALANCE_SHEET_ACCOUNT_NAMES + INCOME_STATEMENT_ACCOUNT_NAMES

# 排除項目（不得選）
EXCLUDED_ITEMS: tuple[str, ...] = (
    "小計與合計",
    "所得稅相關",
    "現金及約當現金",
    "權益各項與其他綜合損益",
    "預期信用減損損失與淨外幣兌換損益",
    "預付款項與其他資產負債",
    "現金流量表全部項目",
)

# ---------------------------------------------------------------------------
# P1 條目欄位封閉清單（SPEC 7.3、7.4）
# ---------------------------------------------------------------------------

METHOD_1 = "方法一"
METHODS: tuple[str, ...] = (METHOD_1,)

ITEM_TYPES: tuple[str, ...] = ("轉型風險", "實體風險", "機會")

# 子類別（TCFD）【提案，待使用者對照 TCFD 原文】
SUBCATEGORIES: dict[str, tuple[str, ...]] = {
    "轉型風險": ("政策與法規", "技術", "市場", "聲譽"),
    "實體風險": ("急性", "慢性"),
    "機會": ("資源效率", "能源來源", "產品與服務", "市場", "韌性"),
}
ALL_SUBCATEGORIES: tuple[str, ...] = tuple(dict.fromkeys(s for v in SUBCATEGORIES.values() for s in v))

SUBCATEGORY_SOURCES: tuple[str, ...] = ("原文", "AI歸類")
TIME_HORIZONS: tuple[str, ...] = ("短", "中", "長")
LEVELS: tuple[str, ...] = ("低", "中", "高")
IMPACT_SCOPES: tuple[str, ...] = ("全公司", "特定單位或據點", "子公司", "供應商", "上下游")

# ---------------------------------------------------------------------------
# P2 推理封閉清單（SPEC 9.3、9.4）
# ---------------------------------------------------------------------------

SOURCE_KINDS: tuple[str, ...] = ("原文明示", "AI推論")
OCCURRENCE_STAGES: tuple[str, ...] = ("已發生或已執行", "規劃中或預期", "未說明")
GAP_TYPES: tuple[str, ...] = ("成本歸屬或用途", "受影響資產或業務範圍", "規模或金額", "因應執行狀態", "其他")
CHANNELS: tuple[str, ...] = (
    "收入",
    "營運成本",
    "義務與預提",
    "資產價值",
    "新增投資",
    "融資",
    "事業調整",
    "投資與被投資",
    "應付款項及合約負債",
)

IMPACT_YES = "有影響"
IMPACT_NO = "無影響"
OVERALL_JUDGEMENTS: tuple[str, ...] = (IMPACT_YES, IMPACT_NO)

NO_IMPACT_REASONS: tuple[str, ...] = ("與財務無關", "描述不足以建立傳導路徑")

DIRECTION_UP = "上升"
DIRECTION_DOWN = "下降"
DIRECTION_UNCERTAIN = "不確定"
DIRECTION_NONE = "不影響"  # 只由程式在「無影響」時填，LLM 不得輸出
DIRECTIONS_ALL: tuple[str, ...] = (DIRECTION_UP, DIRECTION_DOWN, DIRECTION_UNCERTAIN, DIRECTION_NONE)
DIRECTIONS_LLM: tuple[str, ...] = (DIRECTION_UP, DIRECTION_DOWN, DIRECTION_UNCERTAIN)
DIRECTIONS_CERTAIN: tuple[str, ...] = (DIRECTION_UP, DIRECTION_DOWN)  # 確定比例的分子

DIRECTNESS_LEVELS: tuple[str, ...] = ("一", "二", "三")
EVIDENCE_GRADES: tuple[str, ...] = ("A", "B", "C")
# 【待確認 15-4】證據等級 C 是否允許原文依據為 null；目前允許
EVIDENCE_GRADES_REQUIRING_QUOTE: tuple[str, ...] = ("A", "B")

# 推理狀態（07_final.json）
REASONING_OK = "成功"
REASONING_FAILED = "推理失敗"
# S6 標記為內部不一致（例如整體判斷無影響但表格非空）：不由程式猜哪個對，網頁顯示此字串
REASONING_CHECK_FAILED = "檢查未通過"
REASONING_STATUSES: tuple[str, ...] = (REASONING_OK, REASONING_FAILED, REASONING_CHECK_FAILED)

TOP_N_PER_STATEMENT = 3  # 【已決定】網頁每表取前三

# ---------------------------------------------------------------------------
# 定位（SPEC 6）
# ---------------------------------------------------------------------------

SECTION_ROLES: tuple[str, ...] = ("環境", "治理", "社會", "索引附錄", "其他")
TOC_SOURCES: tuple[str, ...] = ("bookmarks", "toc_page")
STRATEGY_TABLE_FOUND_BY: tuple[str, ...] = ("keyword+llm", "index_fallback", "override")
STRATEGY_TABLE_KEYWORDS: tuple[str, ...] = (
    "氣候", "風險", "機會", "財務影響", "轉型", "實體", "TCFD", "IFRS S2",
)

# ---------------------------------------------------------------------------
# 引文驗證（SPEC 10.2）
# ---------------------------------------------------------------------------

CITATION_EXACT = "完全通過"
CITATION_FUZZY = "模糊通過"
CITATION_FAIL = "未通過"
CITATION_LEVELS: tuple[str, ...] = (CITATION_EXACT, CITATION_FUZZY, CITATION_FAIL)

# ---------------------------------------------------------------------------
# 行業大類（中華民國統計資訊網行業統計分類，SPEC 4.1）
# ---------------------------------------------------------------------------

INDUSTRY_SECTIONS: dict[str, str] = {
    "A": "農、林、漁、牧業",
    "B": "礦業及土石採取業",
    "C": "製造業",
    "D": "電力及燃氣供應業",
    "E": "用水供應及污染整治業",
    "F": "營造業",
    "G": "批發及零售業",
    "H": "運輸及倉儲業",
    "I": "住宿及餐飲業",
    "J": "資訊及通訊傳播業",
    "K": "金融及保險業",
    "L": "不動產業",
    "M": "專業、科學及技術服務業",
    "N": "支援服務業",
    "O": "公共行政及國防、強制性社會安全",
    "P": "教育服務業",
    "Q": "醫療保健及社會工作服務業",
    "R": "藝術、娛樂及休閒服務業",
    "S": "其他服務業",
}
INDUSTRY_SECTION_CODES: tuple[str, ...] = tuple(INDUSTRY_SECTIONS)
REJECTED_INDUSTRY_SECTIONS: dict[str, str] = {
    "K": "金融及保險業的財務報表結構與一般產業不同，本系統的 19 項濃縮科目不適用",
}

# ---------------------------------------------------------------------------
# 報告書處理狀態
# ---------------------------------------------------------------------------

REPORT_STATUS_PENDING = "待處理"
REPORT_STATUS_RUNNING = "處理中"
REPORT_STATUS_OK = "成功"
REPORT_STATUS_FAILED = "失敗"
REPORT_STATUS_REJECTED = "拒絕處理"
REPORT_STATUS_NO_TABLE = "未找到氣候策略表"
REPORT_STATUSES: tuple[str, ...] = (
    REPORT_STATUS_PENDING,
    REPORT_STATUS_RUNNING,
    REPORT_STATUS_OK,
    REPORT_STATUS_FAILED,
    REPORT_STATUS_REJECTED,
    REPORT_STATUS_NO_TABLE,
)
