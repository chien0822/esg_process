# 提示檔

- 檔名：`<名稱>_v<版本>.md`，例如 `p2_reasoning_v1.md`
- 使用哪一版由 `config/settings.yaml` 的 `prompts` 指定，例如 `p2_reasoning: 1`
- 改內容就新增下一版的檔案，不覆寫舊檔
- `run.json` 會記錄每個提示的版本與檔案雜湊；若內容改了卻沒升版，`--resume` 會偵測到並拒絕接續
