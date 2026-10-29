"""手動執行的真實 API 測試（不在 pytest 內）。會花一點點 token。

  uv run python scripts/smoke_gemini.py

確認三件事：
1. API key、模型代碼、thinking_level 設定可用
2. 中文 key 的 structured output 回應能通過 P2Result 的 pydantic 驗證
3. 回應 JSON 的 key 順序與 schema 一致（理解紀錄、傳導管道在結論之前）

不讀寫快取；輸入是測試用的虛構條目，不是真實報告書內容。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import ValidationError  # noqa: E402

from esgfin.config import get_api_key, load_settings  # noqa: E402
from esgfin.llm.base import LLMError, LLMParams, LLMRequest, TextPart  # noqa: E402
from esgfin.llm.gemini import GeminiProvider  # noqa: E402
from esgfin.llm.service import key_order_issues, llm_schema  # noqa: E402
from esgfin.schemas.p2 import P2Result  # noqa: E402

SYSTEM = (
    "你是會計研究助理。只依輸入的條目內容判斷，不得補寫原文沒有的事實。"
    "會計科目只能從 schema 的列舉值中選。回答使用繁體中文。"
)
ITEM = """這是測試用的虛構條目（不是真實報告書）：
代碼：TEST-2024-M1-001
類型：轉型風險／市場
事件描述：電價調漲，預估每年電費增加約 1,770 萬元。
原文內容.事件描述：「電價調漲，預估每年電費增加約 1,770 萬元」
請依 schema 輸出 P2 推理結果，代碼填 TEST-2024-M1-001。"""


def main() -> int:
    settings = load_settings(ROOT / "config" / "settings.yaml")
    params = LLMParams(settings.llm.model, settings.llm.thinking_level, settings.llm.max_output_tokens)
    try:
        api_key = get_api_key(ROOT)
    except RuntimeError as e:
        print(f"[失敗] {e}")
        return 1
    provider = GeminiProvider(api_key, timeout_s=settings.llm.timeout_s)
    schema = llm_schema(P2Result)
    request = LLMRequest("smoke_test", 0, SYSTEM, (TextPart(ITEM),), schema)

    print(f"模型：{params.model}　thinking_level：{params.thinking_level}")
    t0 = time.monotonic()
    try:
        raw = provider.generate(request, params)
    except LLMError as e:
        print(f"[失敗] API 呼叫錯誤：{e}")
        return 1
    elapsed = time.monotonic() - t0
    u = raw.usage
    print(f"耗時 {elapsed:.1f} 秒；token 輸入 {u.input_tokens}、輸出 {u.output_tokens}、思考 {u.thought_tokens}；model_version={raw.model_version}")

    data = json.loads(raw.text)
    print("頂層 key 順序：", list(data))
    try:
        P2Result.model_validate(data)
        print("[通過] pydantic 驗證")
        ok = True
    except ValidationError as e:
        print(f"[失敗] pydantic 驗證：\n{e}")
        ok = False

    issues = key_order_issues(data, schema)
    if issues:
        print("[注意] key 順序與 schema 不一致：")
        for i in issues:
            print("  ", i)
    else:
        print("[通過] 所有物件的 key 順序與 schema 一致")

    out = ROOT / "runs" / "smoke_gemini_last.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完整回應已存到 {out.relative_to(ROOT)}")
    return 0 if ok and not issues else 1


if __name__ == "__main__":
    sys.exit(main())
