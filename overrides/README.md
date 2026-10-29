# 人工取代（SPEC 3.3，實驗二用）

把人工整理的檔案放在 `overrides/<report_id>/<階段輸出檔名>`，該階段就不執行，改用人工檔。
人工檔必須通過與程式輸出相同的 pydantic schema 驗證，否則整個執行會停下來並說明錯誤。

| 階段 | 人工檔 |
|---|---|
| S1 解析 | `01_pages.jsonl` |
| S2 定位 | `02_manifest.json`（例如手動指定策略表頁碼，`found_by` 填 `override`） |
| S3 方法一 | `03_p1_items.json` |
| S5 P2 推理 | `05_p2/<代碼>.json`（可逐條目取代） |
| S7 彙整 | `07_final.json` 與 `07_metrics.json`（兩個都要有） |

- `report_id` = PDF 檔名去掉副檔名，例如 `realtek_2024_ESG`
- 每次使用人工檔都會記在 `runs/<run_id>/run.json` 該報告書的 `overrides`（檔案路徑、雜湊；S5 另記條目代碼）
- 人工檔的內容與既有輸出不同時，其後的階段會重跑
