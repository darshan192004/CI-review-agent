"""Provider-agnostic LLM rate limiting and retry.

Guards every LLM call so the agent stays under whatever requests-per-minute
limit the configured provider enforces (OpenAI, Anthropic, Groq, ...). The
limiter is a shared sliding token bucket; the retry wrapper handles HTTP 429
(rate limited) and transient 5xx/network errors with exponential backoff that
honours any Retry-After header.

Rate limiting is disabled when ``llm_rate_limit_per_minute`` is 0.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

TimeFn = Callable[[], float]
SleepFn = Callable[[float], Awaitable[None]]

_MAX_RETRY_AFTER_SECONDS = 120.0
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


class AsyncRateLimiter:
    """Token-bucket limiter. ``acquire()`` blocks until a slot is free.

    A fresh bucket is created when ``rate_per_minute`` is first read; later
    config changes are picked up on the next ``acquire()``.
    """

    def __init__(
        self,
        rate_per_minute: int,
        max_burst: int | None = None,
        max_wait_seconds: float = 600.0,
        *,
        monotonic: TimeFn = time.monotonic,
        sleep: SleepFn | None = None,
    ) -> None:
        self.rate_per_minute = max(0, int(rate_per_minute))
        self.max_wait_seconds = max(0.0, max_wait_seconds)
        self.capacity = float(max_burst if max_burst is not None and max_burst > 0 else max(1, self.rate_per_minute))
        self._tokens = self.capacity
        self._last_refill = monotonic()
        self._rate_per_second = self.rate_per_minute / 60.0 if self.rate_per_minute > 0 else 0.0
        self._monotonic = monotonic
        self._sleep = sleep or asyncio.sleep
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.rate_per_minute > 0

    def _refill(self) -> None:
        if not self.enabled:
            return
        now = self._monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self._rate_per_second)

    async def acquire(self) -> None:
        if not self.enabled:
            return
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate_per_second
            if wait > self.max_wait_seconds:
                logger.warning(
                    "LLM rate limiter saturated: next slot in %.1fs exceeds max_wait %.1fs. "
                    "Reduce concurrent load or raise LLM_RATE_LIMIT_PER_MINUTE.",
                    wait,
                    self.max_wait_seconds,
                )
                await self._sleep(self.max_wait_seconds)
            else:
                await self._sleep(wait)


class LLMRateLimiter(AsyncRateLimiter):
    """Rate limiter wired to settings values. Returns a shared singleton."""

    _instance: LLMRateLimiter | None = None

    def __init__(self) -> None:
        super().__init__(
            rate_per_minute=settings.llm_rate_limit_per_minute,
            max_burst=settings.llm_rate_limit_burst or None,
            max_wait_seconds=settings.llm_rate_limit_max_wait_seconds,
        )

    @classmethod
    def get(cls) -> LLMRateLimiter:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None


def is_rate_limit_error(exc: Exception) -> bool:
    """True when the exception indicates the provider is rate limiting us."""
    if _extract_status_code(exc) == 429:
        return True
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "rate_limit" in name:
        return True
    return "rate limit" in str(exc).lower()


def is_transient_error(exc: Exception) -> bool:
    """True for rate-limit (429) or transient 5xx/network errors worth a retry."""
    status = _extract_status_code(exc)
    if status in _TRANSIENT_STATUS_CODES:
        return True
    if isinstance(exc, (TimeoutError, ConnectionError, asyncio.TimeoutError)):
        return True
    name = type(exc).__name__.lower()
    return "timeout" in name or "connection" in name or "transient" in name


def _extract_status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    return status


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None)
    if headers is None:
        return None
    value = None
    if hasattr(headers, "get"):
        value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    try:
        return min(float(value), _MAX_RETRY_AFTER_SECONDS)
    except (TypeError, ValueError):
        return None


async def invoke_with_llm_rate_limit(
    llm: Any,
    messages: list[Any],
    *,
    rate_limiter: AsyncRateLimiter | None = None,
    max_retries: int = 3,
    base_backoff_seconds: float = 2.0,
    sleep: SleepFn | None = None,
) -> Any:
    """Rate-limit a single ``llm.ainvoke(messages)`` and retry transient errors.

    Raises the last exception if retries are exhausted or the error is not
    transient — callers keep their existing error handling.
    """
    limiter = rate_limiter or LLMRateLimiter.get()
    do_sleep = sleep or asyncio.sleep
    retries = 0
    while True:
        await limiter.acquire()
        try:
            return await llm.ainvoke(messages)
        except Exception as exc:
            retryable = is_rate_limit_error(exc) or is_transient_error(exc)
            if not retryable or retries >= max_retries:
                raise
            retries += 1
            delay = base_backoff_seconds * (2 ** (retries - 1))
            retry_after = _retry_after_seconds(exc)
            if retry_after is not None:
                delay = max(delay, retry_after)
            logger.warning(
                "LLM call rate-limited or transient (retry %d/%d): %s. Backing off %.1fs.",
                retries,
                max_retries,
                exc,
                delay,
            )
            await do_sleep(delay)
