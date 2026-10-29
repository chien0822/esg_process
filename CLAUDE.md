# CLAUDE.md

## 專案
碩士論文研究系統：用 LLM 讀企業永續報告書的氣候風險與機會策略表，判斷每一列可能影響資產負債表與綜合損益表的哪些濃縮會計科目與方向，輸出投資人網頁。完整規格見 `SPEC.md`，以它為準。

## 與使用者合作
- 使用者是研究生，用繁體中文溝通；回報與訊息使用繁體中文
- 準確優先。不確定時先問，不要猜；不要過度稱讚或過度承諾
- 依 `SPEC.md` 第 13 節的里程碑開發，每個里程碑完成後停下來報告並等使用者確認
- 標【已決定】的內容不得自行更改；要改先說明理由並詢問
- 涉及外部套件或 API（google-genai、Docling、PyMuPDF4LLM）的用法，以當下官方文件為準，不憑記憶

## 環境
- Windows 上的 WSL Ubuntu、VSCode、Python 3.11+、無 GPU
- 模型：`gemini-3.8-flash`（thinking 只支援 low／medium／high，不可用 minimal）
- API key 在 `.env` 的 `GEMINI_API_KEY`；`.env`、`cache/`、`runs/`、`report/*.pdf` 不進 git

## 指令
套件以 uv 管理（`pyproject.toml`、`uv.lock`），一律透過 `uv run` 執行：
- 執行：`uv run python run.py`（選項見 SPEC 3.1；另有 `--resume [run_id]`、`--from-stage X --source run_id`，見 `run.py` 開頭說明）
- 測試：`uv run pytest`（測試不得呼叫真實 API）
- 真實 API 測試（手動、會花 token）：`uv run python scripts/smoke_gemini.py`

## 必守規則
1. 會計科目、排除項目、封閉清單只在 `src/esgfin/constants.py` 定義一次
2. 所有 JSON 用 pydantic 定義與驗證；LLM 一律用 structured output
3. 各階段以檔案傳遞，可斷點續跑，可被 `overrides/` 的人工檔取代
4. LLM 回應必須快取；每次執行保存模型代碼、提示版本、參數、token 與耗時
5. 提示檔放 `prompts/`，改內容就升版本號
6. AI 不得補寫原文沒有的資訊；原文引用逐字保留原語言；原文與 AI 推論分開記錄
7. 檢查模組只標記問題，不自動修改 LLM 輸出
8. 頁碼一律存 PDF 頁序；印刷頁碼另存
