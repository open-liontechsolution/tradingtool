"""Binance API client with rate limiting, retry logic, and backoff handling."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BINANCE_BASE_URL = "https://api.binance.com"
KLINES_ENDPOINT = "/api/v3/klines"

# Binance public endpoint rate limits
WEIGHT_LIMIT_PER_MINUTE = 1200
KLINES_REQUEST_WEIGHT = 2  # weight per klines request

# Minimum delay between requests in seconds
MIN_REQUEST_INTERVAL = 0.1  # 100ms


@dataclass
class RateLimitState:
    used_weight: int = 0
    weight_limit: int = WEIGHT_LIMIT_PER_MINUTE
    last_request_time: float = field(default_factory=time.monotonic)
    blocked_until: float = 0.0
    backoff_until: float = 0.0

    @property
    def status(self) -> str:
        now = time.monotonic()
        if self.blocked_until > now:
            return "blocked"
        if self.backoff_until > now:
            return "backoff"
        ratio = self.used_weight / max(self.weight_limit, 1)
        if ratio >= 0.9:
            return "warning"
        return "ok"

    def to_dict(self) -> dict:
        now = time.monotonic()
        return {
            "used_weight": self.used_weight,
            "weight_limit": self.weight_limit,
            "status": self.status,
            "blocked_until": max(0.0, self.blocked_until - now),
            "backoff_until": max(0.0, self.backoff_until - now),
        }


class BinanceClient:
    """Async Binance API client with rate limiting."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self.rate_limit = RateLimitState()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BINANCE_BASE_URL,
                timeout=30.0,
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _wait_for_rate_limit(self) -> None:
        """Block if we are in a blocked or backoff state, and pace requests."""
        now = time.monotonic()
        if self.rate_limit.blocked_until > now:
            wait = self.rate_limit.blocked_until - now
            logger.warning("Rate limited (418): waiting %.1fs", wait)
            await asyncio.sleep(wait)

        now = time.monotonic()
        if self.rate_limit.backoff_until > now:
            wait = self.rate_limit.backoff_until - now
            logger.warning("Backoff (429): waiting %.1fs", wait)
            await asyncio.sleep(wait)

        # Minimum pacing
        elapsed = time.monotonic() - self.rate_limit.last_request_time
        if elapsed < MIN_REQUEST_INTERVAL:
            await asyncio.sleep(MIN_REQUEST_INTERVAL - elapsed)

    def _parse_rate_limit_headers(self, headers: httpx.Headers) -> None:
        weight_str = headers.get("X-MBX-USED-WEIGHT-1M") or headers.get("x-mbx-used-weight-1m")
        if weight_str:
            try:
                self.rate_limit.used_weight = int(weight_str)
            except ValueError:
                pass

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 500,
    ) -> list[list[Any]]:
        """
        Fetch klines from Binance with retry/backoff on 429/418.
        Returns list of raw candle arrays.
        """
        params: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time

        max_retries = 8
        for attempt in range(max_retries):
            async with self._lock:
                await self._wait_for_rate_limit()
                client = await self._get_client()
                try:
                    response = await client.get(KLINES_ENDPOINT, params=params)
                    self.rate_limit.last_request_time = time.monotonic()
                    self._parse_rate_limit_headers(response.headers)

                    if response.status_code == 200:
                        return response.json()

                    elif response.status_code == 429:
                        retry_after = float(response.headers.get("Retry-After", 0))
                        backoff = max(retry_after, _exponential_backoff(attempt))
                        self.rate_limit.backoff_until = time.monotonic() + backoff
                        logger.warning("429 received, backing off %.1fs (attempt %d)", backoff, attempt + 1)
                        await asyncio.sleep(backoff)

                    elif response.status_code == 418:
                        retry_after = float(response.headers.get("Retry-After", 60))
                        self.rate_limit.blocked_until = time.monotonic() + retry_after
                        logger.error("418 IP banned for %.0fs", retry_after)
                        await asyncio.sleep(retry_after)

                    else:
                        response.raise_for_status()

                except httpx.TimeoutException as exc:
                    backoff = _exponential_backoff(attempt)
                    logger.warning("Timeout on attempt %d, retrying in %.1fs: %s", attempt + 1, backoff, exc)
                    await asyncio.sleep(backoff)

        raise RuntimeError(f"Failed to fetch klines for {symbol}/{interval} after {max_retries} attempts")


def _exponential_backoff(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """Exponential backoff with jitter."""
    delay = min(base * (2 ** attempt), cap)
    return delay * (0.5 + random.random() * 0.5)


def parse_candle(raw: list[Any], symbol: str, interval: str, downloaded_at: str) -> dict:
    """Convert raw Binance kline array to a candle dict."""
    return {
        "symbol": symbol,
        "interval": interval,
        "open_time": int(raw[0]),
        "open": str(raw[1]),
        "high": str(raw[2]),
        "low": str(raw[3]),
        "close": str(raw[4]),
        "volume": str(raw[5]),
        "close_time": int(raw[6]),
        "quote_asset_volume": str(raw[7]),
        "number_of_trades": int(raw[8]),
        "taker_buy_base_vol": str(raw[9]),
        "taker_buy_quote_vol": str(raw[10]),
        "ignore_field": str(raw[11]) if len(raw) > 11 else None,
        "source": "binance_spot",
        "downloaded_at": downloaded_at,
    }


def validate_candle(candle: dict) -> bool:
    """Validate OHLC consistency."""
    try:
        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        c = float(candle["close"])
        return h >= max(o, c) and l <= min(o, c) and l > 0 and h > 0
    except (ValueError, KeyError):
        return False


# Singleton instance
binance_client = BinanceClient()
