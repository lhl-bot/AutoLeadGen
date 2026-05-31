"""Resilient HTTP client with automatic retry on transient failures.

The production host routes HTTPS through a local proxy (127.0.0.1:7892,
typically Clash) which can intermittently drop SSL handshakes, causing
SSLEOFError across ALL outgoing HTTPS requests simultaneously.

This module provides a shared ``requests.Session`` with a ``urllib3.Retry``
adapter that transparently retries on:
  - SSL handshake failures (SSLEOFError, ConnectionResetError)
  - HTTP 429 / 5xx server errors
  - Read timeouts and mid-stream connection resets

Usage:
    from services.http_client import http
    resp = http.get("https://example.com", timeout=15)
    resp = http.post("https://api.example.com/v1", json={...}, timeout=20)
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def build_resilient_session(
    total: int = 4,
    connect: int = 3,
    read: int = 2,
    backoff_factor: float = 1.5,
    pool_connections: int = 10,
    pool_maxsize: int = 20,
) -> requests.Session:
    """Build a requests Session with automatic retry on transient failures."""
    retry_strategy = Retry(
        total=total,
        connect=connect,
        read=read,
        backoff_factor=backoff_factor,          # 0s → 1.5s → 3s → 4.5s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "HEAD", "PUT", "DELETE", "PATCH"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
    )
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# Module-level singleton — import and use directly:
#   from services.http_client import http
http = build_resilient_session()
