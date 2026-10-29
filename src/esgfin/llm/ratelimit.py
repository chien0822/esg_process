"""每分鐘請求數與 token 數的滑動視窗限速。"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable


class RateLimiter:
    WINDOW_S = 60.0

    def __init__(
        self,
        requests_per_minute: int | None,
        tokens_per_minute: int | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.rpm = requests_per_minute
        self.tpm = tokens_per_minute
        self._clock = clock
        self._sleep = sleep
        self._events: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0][0] >= self.WINDOW_S:
            self._events.popleft()

    def _wait_needed(self, now: float, tokens: int) -> float:
        waits = [0.0]
        if self.rpm is not None and len(self._events) >= self.rpm:
            waits.append(self._events[len(self._events) - self.rpm][0] + self.WINDOW_S - now)
        if self.tpm is not None:
            # 單次請求超過上限時不能無限等待，視為獨占整個視窗
            budget = self.tpm - min(tokens, self.tpm)
            used = 0
            for t, n in reversed(self._events):
                used += n
                if used > budget:
                    waits.append(t + self.WINDOW_S - now)
                    break
        return max(waits)

    def acquire(self, estimated_tokens: int = 0) -> float:
        """必要時等待；回傳總等待秒數。"""
        waited = 0.0
        with self._lock:
            while True:
                now = self._clock()
                self._prune(now)
                wait = self._wait_needed(now, estimated_tokens)
                if wait <= 0:
                    self._events.append((now, estimated_tokens))
                    return waited
                self._sleep(wait)
                waited += wait
