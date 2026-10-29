"""429、5xx 的指數退避重試。"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from esgfin.schemas.settings import RetrySettings

from .base import TransientLLMError

T = TypeVar("T")


@dataclass
class RetryOutcome:
    transient_retries: int = 0
    slept_s: float = 0.0


def backoff_delay(settings: RetrySettings, retry_index: int, rng: random.Random | None = None) -> float:
    """第 retry_index 次重試（從 0 起算）前要等待的秒數。"""
    delay = min(settings.initial_backoff_s * (settings.multiplier**retry_index), settings.max_backoff_s)
    if settings.jitter:
        delay *= (rng or random).uniform(0.5, 1.0)
    return delay


def call_with_retry(
    fn: Callable[[], T],
    settings: RetrySettings,
    outcome: RetryOutcome,
    *,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> T:
    for attempt in range(settings.max_attempts):
        try:
            return fn()
        except TransientLLMError:
            if attempt == settings.max_attempts - 1:
                raise
            delay = backoff_delay(settings, attempt, rng)
            outcome.transient_retries += 1
            outcome.slept_s += delay
            sleep(delay)
    raise AssertionError("unreachable")
