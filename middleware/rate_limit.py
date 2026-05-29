"""Simple in-memory rate limiter for FastAPI."""
import time
import threading
from typing import Dict, Tuple
from fastapi import Request, HTTPException


class RateLimiter:
    """Token-bucket-like rate limiter. Per-key limits tracked in memory."""

    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: Dict[str, Tuple[float, int]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            window_start, count = self._buckets.get(key, (0, 0))
            if now - window_start > self.window_seconds:
                # Window expired - reset
                self._buckets[key] = (now, 1)
                return True
            if count >= self.max_requests:
                return False
            self._buckets[key] = (window_start, count + 1)
            return True

    def cleanup(self):
        """Remove expired entries. Call periodically."""
        now = time.time()
        with self._lock:
            expired = [
                k for k, (ws, _) in self._buckets.items()
                if now - ws > self.window_seconds * 2
            ]
            for k in expired:
                del self._buckets[k]


# Per-IP: 5 login attempts per minute
login_limiter = RateLimiter(max_requests=5, window_seconds=60)

# Global: 100 requests per minute per IP
global_limiter = RateLimiter(max_requests=100, window_seconds=60)


def get_client_ip(request: Request) -> str:
    """Extract client IP, respecting proxy headers."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_login_rate_limit(request: Request) -> None:
    ip = get_client_ip(request)
    if not login_limiter.is_allowed(ip):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试。")


def check_global_rate_limit(request: Request) -> None:
    ip = get_client_ip(request)
    if not global_limiter.is_allowed(ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试。")
