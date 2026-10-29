from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from esgfin.llm.base import LLMParams, LLMRequest, PermanentLLMError, TextPart, TransientLLMError
from esgfin.llm.cache import LLMCache, cache_key
from esgfin.llm.fake import FakeLLM
from esgfin.llm.ratelimit import RateLimiter
from esgfin.llm.retry import backoff_delay
from esgfin.llm.service import LLMService, key_order_issues, llm_schema
from esgfin.schemas.settings import RetrySettings


class Out(BaseModel):
    思考: str
    結論: int


PARAMS = LLMParams("gemini-3.8-flash", "medium")


def req(text: str = "q", version: int = 1) -> LLMRequest:
    return LLMRequest("t", version, "sys", (TextPart(text),), llm_schema(Out))


def make_service(tmp_path, llm, **kw) -> tuple[LLMService, list]:
    records: list = []
    slept: list = kw.pop("slept", [])
    svc = LLMService(
        llm, PARAMS, LLMCache(tmp_path / "cache"), RateLimiter(None, None),
        RetrySettings(max_attempts=3, initial_backoff_s=1, multiplier=2, jitter=False),
        validation_retries=kw.pop("validation_retries", 2), sink=records.append, sleep=slept.append,
    )
    return svc, records


def test_cache_key_changes_with_inputs():
    k = cache_key("p", req(), PARAMS)
    assert k == cache_key("p", req(), PARAMS)
    assert k != cache_key("p", req(version=2), PARAMS)
    assert k != cache_key("p", req(text="other"), PARAMS)
    assert k != cache_key("p", req(), LLMParams("gemini-3.8-flash", "high"))
    assert k != cache_key("other", req(), PARAMS)


def test_success_is_cached_and_reused(tmp_path):
    llm = FakeLLM({"t": [{"思考": "a", "結論": 1}]})
    svc, records = make_service(tmp_path, llm)
    r1 = svc.structured(req(), Out)
    r2 = svc.structured(req(), Out)
    assert r1.value == r2.value == Out(思考="a", 結論=1)
    assert len(llm.calls) == 1
    assert [r.cache_hit for r in records] == [False, True]
    assert r1.first_pass and r2.first_pass


def test_invalid_response_retried_with_feedback_and_not_cached(tmp_path):
    llm = FakeLLM({"t": ['{"思考": "a"}', {"思考": "a", "結論": 2}]})
    svc, records = make_service(tmp_path, llm)
    r = svc.structured(req(), Out)
    assert r.value == Out(思考="a", 結論=2)
    assert not r.first_pass and len(r.attempts) == 1
    assert "沒有通過 JSON schema 驗證" in llm.calls[1].parts[-1].text
    # 失敗的那一次不在快取：原始請求的 key 沒有快取檔
    assert LLMCache(tmp_path / "cache").get(cache_key("fake", req(), PARAMS)) is None
    assert [r.ok for r in records] == [False, True]


def test_all_attempts_invalid_returns_failure_without_cache(tmp_path):
    llm = FakeLLM({"t": "not json"})
    svc, _ = make_service(tmp_path, llm, validation_retries=2)
    r = svc.structured(req(), Out)
    assert not r.ok and len(r.attempts) == 3 and len(llm.calls) == 3
    assert not list((tmp_path / "cache").rglob("*.json"))
    # 再呼叫一次仍會真的打（失敗不會被當成已完成）
    svc.structured(req(), Out)
    assert len(llm.calls) == 6


def test_transient_errors_backoff_then_succeed(tmp_path):
    slept: list = []
    llm = FakeLLM({"t": [TransientLLMError("429", 429), TransientLLMError("503", 503), {"思考": "a", "結論": 1}]})
    svc, records = make_service(tmp_path, llm, slept=slept)
    r = svc.structured(req(), Out)
    assert r.ok
    assert slept == [1, 2]
    assert records[-1].transient_retries == 2


def test_permanent_error_not_retried(tmp_path):
    llm = FakeLLM({"t": [PermanentLLMError("400", 400)]})
    svc, _ = make_service(tmp_path, llm)
    r = svc.structured(req(), Out)
    assert not r.ok and len(llm.calls) == 1 and "400" in r.error


def test_backoff_capped():
    s = RetrySettings(initial_backoff_s=2, multiplier=2, max_backoff_s=10, jitter=False)
    assert [backoff_delay(s, i) for i in range(5)] == [2, 4, 8, 10, 10]


def test_rate_limiter_waits_for_window():
    now = [0.0]
    sleeps: list = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    lim = RateLimiter(2, None, clock=lambda: now[0], sleep=sleep)
    lim.acquire()
    lim.acquire()
    assert sleeps == []
    lim.acquire()
    assert sleeps == [60.0]


def test_rate_limiter_tokens():
    now = [0.0]
    sleeps: list = []
    lim = RateLimiter(None, 100, clock=lambda: now[0], sleep=lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s)))
    lim.acquire(60)
    lim.acquire(60)
    assert sleeps == [60.0]


def test_key_order_issues_detects_reordering():
    schema = llm_schema(Out)
    assert key_order_issues({"思考": "a", "結論": 1}, schema) == []
    assert key_order_issues(json.loads('{"結論": 1, "思考": "a"}'), schema)


# ---- Gemini adapter：用 httpx MockTransport，不連網


def _gemini_with(handler):
    from google import genai
    from google.genai import types

    from esgfin.llm.gemini import GeminiProvider

    p = GeminiProvider("dummy-key")
    p._client = genai.Client(
        api_key="dummy-key",
        http_options=types.HttpOptions(client_args={"transport": httpx.MockTransport(handler)},
                                       retry_options=types.HttpRetryOptions(attempts=1)),
    )
    return p


@pytest.mark.parametrize("status,exc", [(429, TransientLLMError), (503, TransientLLMError), (400, PermanentLLMError)])
def test_gemini_error_mapping(status, exc):
    p = _gemini_with(lambda r: httpx.Response(status, json={"error": {"code": status, "message": "x"}}))
    with pytest.raises(exc):
        p.generate(req(), PARAMS)


def test_gemini_request_body_and_usage():
    seen = {}

    def handler(r):
        seen["body"] = json.loads(r.content)
        return httpx.Response(200, json={
            "id": "i1", "status": "completed", "model": "gemini-3.8-flash",
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": '{"思考":"a","結論":1}'}]}],
            "usage": {"total_input_tokens": 10, "total_output_tokens": 5, "total_thought_tokens": 3},
        })

    out = _gemini_with(handler).generate(req(), PARAMS)
    body = seen["body"]
    assert body["generation_config"] == {"thinking_level": "medium"}
    assert "temperature" not in body["generation_config"]
    assert list(body["response_format"]["schema"]["properties"]) == ["思考", "結論"]
    assert body["store"] is False
    assert out.text == '{"思考":"a","結論":1}'
    assert (out.usage.input_tokens, out.usage.output_tokens, out.usage.thought_tokens) == (10, 5, 3)
