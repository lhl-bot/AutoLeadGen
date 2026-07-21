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

    def is_blocked(self, key: str) -> bool:
        """Check a bucket without charging successful requests against it."""
        now = time.time()
        with self._lock:
            window_start, count = self._buckets.get(key, (0, 0))
            if now - window_start > self.window_seconds:
                self._buckets.pop(key, None)
                return False
            return count >= self.max_requests

    def record(self, key: str) -> None:
        """Record an event after it has been classified as a failure."""
        now = time.time()
        with self._lock:
            window_start, count = self._buckets.get(key, (now, 0))
            if now - window_start > self.window_seconds:
                window_start, count = now, 0
            self._buckets[key] = (window_start, count + 1)

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)

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


# Failed login limits: contain both targeted brute force and password spraying.
# Successful authentication is not a failure and therefore does not consume
# the budget. The IP-wide bucket is deliberately broader than the identity
# bucket so one actor cannot evade limits by rotating usernames.
login_ip_failure_limiter = RateLimiter(max_requests=20, window_seconds=300)
login_identity_failure_limiter = RateLimiter(max_requests=5, window_seconds=300)

# Global: 100 requests per minute per IP
global_limiter = RateLimiter(max_requests=100, window_seconds=60)


def get_client_ip(request: Request) -> str:
    """Extract client IP, respecting proxy headers."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _login_identity_key(request: Request, username: str) -> str:
    return f"{get_client_ip(request)}:{username.strip().casefold()}"


def check_login_rate_limit(request: Request, username: str) -> None:
    ip = get_client_ip(request)
    if login_ip_failure_limiter.is_blocked(ip) or login_identity_failure_limiter.is_blocked(
        _login_identity_key(request, username)
    ):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试。")


def record_login_failure(request: Request, username: str) -> None:
    login_ip_failure_limiter.record(get_client_ip(request))
    login_identity_failure_limiter.record(_login_identity_key(request, username))


def reset_login_identity_limit(request: Request, username: str) -> None:
    login_identity_failure_limiter.reset(_login_identity_key(request, username))


def check_global_rate_limit(request: Request) -> None:
    ip = get_client_ip(request)
    if not global_limiter.is_allowed(ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试。")
