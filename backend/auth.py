"""Authentication & authorisation: Keycloak JWT validation + local dev bypass.

When AUTH_ENABLED is false (default, local development) every request is
attributed to a hard-coded dev user with ``app_admin`` privileges so that
the full application is usable without a running Keycloak instance.

When AUTH_ENABLED is true the module:
1. Fetches the Keycloak JWKS (cached) to verify RS256 signatures.
2. Validates issuer and expiration.
3. Extracts ``sub``, ``email``, ``preferred_username`` and client roles.
4. Auto-provisions a local ``users`` row on first login.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from jwt import PyJWKClient

from backend.config import AUTH_ENABLED, KEYCLOAK_AUDIENCE, KEYCLOAK_REALM, KEYCLOAK_URL
from backend.database import get_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model returned by auth dependencies
# ---------------------------------------------------------------------------


@dataclass
class AuthUser:
    """Authenticated user representation available to route handlers."""

    id: int
    keycloak_sub: str
    email: str | None = None
    username: str | None = None
    roles: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Dev bypass user
# ---------------------------------------------------------------------------

_DEV_USER = AuthUser(
    id=1,
    keycloak_sub="dev-local-user",
    email="dev@localhost",
    username="dev",
    roles=["app_user", "app_admin"],
)

# ---------------------------------------------------------------------------
# JWKS client (lazy-initialised, thread-safe via PyJWKClient cache)
# ---------------------------------------------------------------------------

_jwks_client: PyJWKClient | None = None
_JWKS_CACHE_SECONDS = 300


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client  # noqa: PLW0603
    if _jwks_client is None:
        jwks_url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
        _jwks_client = PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=_JWKS_CACHE_SECONDS)
    return _jwks_client


# ---------------------------------------------------------------------------
# Token decoding
# ---------------------------------------------------------------------------


def _decode_token(token: str) -> dict[str, Any]:
    """Validate and decode a Keycloak-issued JWT.

    Checks:
    - RS256 signature via JWKS
    - ``exp`` (expiration)
    - ``iss`` (issuer matches our realm)

    Returns the full decoded payload.
    """
    expected_issuer = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
    client = _get_jwks_client()

    try:
        signing_key = client.get_signing_key_from_jwt(token)
    except Exception as exc:
        logger.warning("JWKS key lookup failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid token signature") from exc

    try:
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=expected_issuer,
            options={
                "verify_aud": False,  # Keycloak defaults aud to "account"
                "verify_exp": True,
                "verify_iss": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except jwt.InvalidIssuerError as exc:
        raise HTTPException(status_code=401, detail="Invalid token issuer") from exc
    except jwt.InvalidTokenError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    return payload


def _extract_roles(payload: dict[str, Any]) -> list[str]:
    """Extract client roles for ``tradingtool-api`` from the token payload."""
    try:
        return payload["resource_access"][KEYCLOAK_AUDIENCE]["roles"]
    except (KeyError, TypeError):
        return []


# ---------------------------------------------------------------------------
# User provisioning
# ---------------------------------------------------------------------------


async def _ensure_user(
    keycloak_sub: str,
    email: str | None,
    username: str | None,
    roles: list[str],
) -> AuthUser:
    """Return an existing local user or create one on first login."""
    now = datetime.now(UTC).isoformat()
    roles_json = json.dumps(roles, sort_keys=True)

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, keycloak_sub, email, username, roles FROM users WHERE keycloak_sub = ?",
            (keycloak_sub,),
        )
        row = await cursor.fetchone()

        if row is not None:
            # Update last_login_at, email, username, roles on every login
            user_id = row[0] if isinstance(row, (list, tuple)) else row["id"]
            await db.execute(
                "UPDATE users SET email = ?, username = ?, roles = ?, last_login_at = ? WHERE id = ?",
                (email, username, roles_json, now, user_id),
            )
            await db.commit()
            return AuthUser(
                id=user_id,
                keycloak_sub=keycloak_sub,
                email=email,
                username=username,
                roles=roles,
            )

        # First login — create user
        cursor = await db.execute(
            """INSERT INTO users (keycloak_sub, email, username, roles, created_at, last_login_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (keycloak_sub, email, username, roles_json, now, now),
        )
        await db.commit()
        new_id = cursor.lastrowid

    return AuthUser(
        id=new_id,
        keycloak_sub=keycloak_sub,
        email=email,
        username=username,
        roles=roles,
    )


# ---------------------------------------------------------------------------
# Ensure dev user exists in DB (called once at startup when auth is off)
# ---------------------------------------------------------------------------

_dev_user_provisioned = False


async def _ensure_dev_user() -> None:
    """Create the hard-coded dev user row in the DB if it does not exist."""
    global _dev_user_provisioned  # noqa: PLW0603
    if _dev_user_provisioned:
        return
    now = datetime.now(UTC).isoformat()
    roles_json = json.dumps(_DEV_USER.roles, sort_keys=True)
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id FROM users WHERE keycloak_sub = ?",
            (_DEV_USER.keycloak_sub,),
        )
        row = await cursor.fetchone()
        if row is None:
            await db.execute(
                """INSERT INTO users (keycloak_sub, email, username, roles, created_at, last_login_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (_DEV_USER.keycloak_sub, _DEV_USER.email, _DEV_USER.username, roles_json, now, now),
            )
            await db.commit()
    _dev_user_provisioned = True


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


async def get_current_user(request: Request) -> AuthUser:
    """FastAPI dependency: returns the authenticated user.

    In dev bypass mode returns a fixed admin user.
    In production validates the JWT and provisions the user.
    """
    if not AUTH_ENABLED:
        await _ensure_dev_user()
        return _DEV_USER

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]
    payload = _decode_token(token)

    keycloak_sub: str = payload.get("sub", "")
    if not keycloak_sub:
        raise HTTPException(status_code=401, detail="Token missing sub claim")

    email: str | None = payload.get("email")
    username: str | None = payload.get("preferred_username")
    roles = _extract_roles(payload)

    user = await _ensure_user(keycloak_sub, email, username, roles)
    return user


async def require_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """FastAPI dependency: requires ``app_admin`` role."""
    if "app_admin" not in user.roles:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
