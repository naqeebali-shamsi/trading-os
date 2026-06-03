"""
autonome/execution/rate_limiter.py  v2.0
Token-bucket rate limiter for order submission.
Global cap + per-symbol cap. Prevents Alpaca rate-limit hits.
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("execution.rate_limiter")


@dataclass
class RateLimitConfig:
    global_orders_per_min: int = 6
    per_symbol_orders_per_min: int = 2


class TokenBucket:
    def __init__(self, rate: float, capacity: float):
        self.rate = rate          # tokens per second
        self.capacity = capacity  # max burst
        self.tokens = capacity
        self.last_update = time.monotonic()

    def consume(self, tokens: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_update = now
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def time_to_next(self) -> float:
        now = time.monotonic()
        elapsed = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_update = now
        if self.tokens >= 1.0:
            return 0.0
        return (1.0 - self.tokens) / self.rate


class OrderRateLimiter:
    """
    Dual rate limiter: global orders/min + per-symbol orders/min.
    """
    def __init__(self, cfg: Optional[RateLimitConfig] = None):
        cfg = cfg or RateLimitConfig()
        self.global_bucket = TokenBucket(
            rate=cfg.global_orders_per_min / 60.0,
            capacity=cfg.global_orders_per_min
        )
        self.symbol_buckets: dict[str, TokenBucket] = {}
        self.symbol_rate = cfg.per_symbol_orders_per_min / 60.0
        self.symbol_cap = cfg.per_symbol_orders_per_min

    def can_submit(self, symbol: str) -> bool:
        if not self.global_bucket.consume():
            return False
        # Per-symbol check
        bucket = self.symbol_buckets.get(symbol)
        if bucket is None:
            bucket = TokenBucket(rate=self.symbol_rate, capacity=self.symbol_cap)
            self.symbol_buckets[symbol] = bucket
        if not bucket.consume():
            # Refund global token
            self.global_bucket.tokens = min(
                self.global_bucket.capacity,
                self.global_bucket.tokens + 1.0
            )
            return False
        return True

    def time_to_next(self, symbol: str) -> float:
        global_wait = self.global_bucket.time_to_next()
        sym_bucket = self.symbol_buckets.get(symbol)
        sym_wait = sym_bucket.time_to_next() if sym_bucket else 0.0
        return max(global_wait, sym_wait)
