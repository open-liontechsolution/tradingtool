"""Rate-limiting plumbing — single ``Limiter`` shared by app + route modules.

This module exists separately from ``backend.app`` so route handlers can
import the limiter without forming an import cycle (``app.py`` imports
the routers, the routers can't import back into ``app.py``).

Defaults can be overridden at deploy time via env vars:

- ``RATE_LIMIT_DEFAULT`` (default ``"60/minute"``): per-client global cap.
- ``RATE_LIMIT_ENABLED`` (default ``"true"``): hard kill-switch — set to
  ``"false"`` in test environments where 429s would mask real failures.

The key function prefers the actual client IP propagated by Cloudflare /
upstream proxies (``cf-connecting-ip``, then the first hop of
``x-forwarded-for``) over ``request.client.host``, which behind the
Cloudflare Tunnel sidecar is always the cloudflared peer and would
collapse all traffic into a single bucket.
"""

from __future__ import annotations

import os

from fastapi import Request
from slowapi import Limiter

_DEFAULT_LIMIT = os.environ.get("RATE_LIMIT_DEFAULT", "60/minute")
_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() in ("1", "true", "yes")


def client_key(request: Request) -> str:
    """Return the rate-limit bucket key for ``request``.

    Order of preference: ``cf-connecting-ip`` → first hop of
    ``x-forwarded-for`` → ``request.client.host`` → ``"unknown"``.
    """
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


limiter = Limiter(
    key_func=client_key,
    application_limits=[_DEFAULT_LIMIT],
    enabled=_ENABLED,
)
