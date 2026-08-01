import asyncio

import pytest

from services.rate_limiter import (
    AsyncRateLimiter,
    invoke_with_llm_rate_limit,
    is_rate_limit_error,
    is_transient_error,
)


class _RateLimitError(Exception):
    status_code = 429


class _ServerError(Exception):
    status_code = 500


class _AuthError(Exception):
    status_code = 401


def test_disabled_limiter_never_blocks():
    limiter = AsyncRateLimiter(rate_per_minute=0, sleep=asyncio.sleep)

    async def run():
        for _ in range(10):
            await limiter.acquire()

    asyncio.run(run())


def test_limiter_throttles_second_call_within_window():
    now = [1000.0]
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = AsyncRateLimiter(
        rate_per_minute=60,
        max_burst=1,
        monotonic=lambda: now[0],
        sleep=fake_sleep,
    )

    async def run():
        await limiter.acquire()
        assert sleeps == []
        await limiter.acquire()
        assert len(sleeps) == 1
        assert sleeps[0] == pytest.approx(1.0)

    asyncio.run(run())


def test_limiter_allows_burst_then_throttles():
    now = [1000.0]
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = AsyncRateLimiter(
        rate_per_minute=60,
        max_burst=3,
        monotonic=lambda: now[0],
        sleep=fake_sleep,
    )

    async def run():
        for _ in range(3):
            await limiter.acquire()
        assert sleeps == []
        await limiter.acquire()
        assert len(sleeps) == 1

    asyncio.run(run())


def test_limiter_refills_over_time():
    now = [1000.0]
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = AsyncRateLimiter(
        rate_per_minute=60,
        max_burst=1,
        monotonic=lambda: now[0],
        sleep=fake_sleep,
    )

    async def run():
        await limiter.acquire()
        now[0] += 0.5
        await limiter.acquire()
        assert len(sleeps) == 1
        assert sleeps[0] == pytest.approx(0.5)

    asyncio.run(run())


def test_rate_limit_error_detection():
    assert is_rate_limit_error(_RateLimitError())
    assert not is_rate_limit_error(_ServerError())
    assert not is_rate_limit_error(_AuthError())
    assert is_rate_limit_error(Exception("Rate limit reached for llama-3.3-70b"))


def test_transient_error_detection():
    assert is_transient_error(_RateLimitError())
    assert is_transient_error(_ServerError())
    assert not is_transient_error(_AuthError())
    assert is_transient_error(TimeoutError())


class _FakeLLM:
    def __init__(self, failures: list[Exception]):
        self.failures = failures
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return "ok"


def test_invoke_retries_rate_limit_and_succeeds():
    llm = _FakeLLM([_RateLimitError()])
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def run():
        result = await invoke_with_llm_rate_limit(
            llm,
            [],
            rate_limiter=AsyncRateLimiter(0),
            max_retries=3,
            base_backoff_seconds=1.0,
            sleep=fake_sleep,
        )
        assert result == "ok"
        assert llm.calls == 2
        assert len(sleeps) == 1
        assert sleeps[0] == 1.0

    asyncio.run(run())


def test_invoke_exhausts_retries_on_persistent_rate_limit():
    llm = _FakeLLM([_RateLimitError(), _RateLimitError(), _RateLimitError()])

    async def fake_sleep(seconds: float) -> None:
        pass

    async def run():
        with pytest.raises(_RateLimitError):
            await invoke_with_llm_rate_limit(
                llm,
                [],
                rate_limiter=AsyncRateLimiter(0),
                max_retries=2,
                base_backoff_seconds=1.0,
                sleep=fake_sleep,
            )
        assert llm.calls == 3

    asyncio.run(run())


def test_invoke_does_not_retry_non_transient_error():
    llm = _FakeLLM([_AuthError()])

    async def fake_sleep(seconds: float) -> None:
        pass

    async def run():
        with pytest.raises(_AuthError):
            await invoke_with_llm_rate_limit(
                llm,
                [],
                rate_limiter=AsyncRateLimiter(0),
                max_retries=3,
                base_backoff_seconds=1.0,
                sleep=fake_sleep,
            )
        assert llm.calls == 1

    asyncio.run(run())
