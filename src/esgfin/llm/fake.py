"""測試用假 LLM：依提示名稱回傳預先寫好的回應，不打真 API。"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Callable, Union

from .base import LLMParams, LLMRawResponse, LLMRequest, TokenUsage

# 回應可以是：字串、dict（轉成 JSON）、Exception（丟出）、或 callable(request) -> 上述之一
Scripted = Union[str, dict, Exception, Callable[[LLMRequest], Any]]


class FakeLLM:
    name = "fake"

    def __init__(self, scripts: dict[str, list[Scripted] | Scripted] | None = None):
        self._scripts: dict[str, list[Scripted]] = defaultdict(list)
        self._sticky: dict[str, Scripted] = {}
        for k, v in (scripts or {}).items():
            if isinstance(v, list):
                self._scripts[k].extend(v)
            else:
                self._sticky[k] = v  # 每次都回同一個
        self.calls: list[LLMRequest] = []

    def queue(self, prompt_name: str, *responses: Scripted) -> None:
        self._scripts[prompt_name].extend(responses)

    def generate(self, request: LLMRequest, params: LLMParams) -> LLMRawResponse:
        self.calls.append(request)
        if self._scripts[request.prompt_name]:
            r = self._scripts[request.prompt_name].pop(0)
        elif request.prompt_name in self._sticky:
            r = self._sticky[request.prompt_name]
        else:
            raise AssertionError(f"FakeLLM 沒有為 {request.prompt_name} 準備回應")
        if callable(r) and not isinstance(r, Exception):
            r = r(request)
        if isinstance(r, Exception):
            raise r
        text = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        return LLMRawResponse(text=text, usage=TokenUsage(input_tokens=100, output_tokens=50, thought_tokens=10))
