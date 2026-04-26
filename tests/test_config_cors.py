"""Unit tests for backend.config._resolve_cors_origins."""

from __future__ import annotations

import pytest

from backend.config import _resolve_cors_origins


class TestWildcardWithAuth:
    def test_wildcard_with_auth_enabled_raises(self):
        with pytest.raises(RuntimeError, match="CORS_ORIGINS=\\* is unsafe"):
            _resolve_cors_origins("*", auth_enabled=True)

    def test_wildcard_with_auth_disabled_allowed(self):
        assert _resolve_cors_origins("*", auth_enabled=False) == ["*"]


class TestExplicitOrigins:
    def test_single_origin_with_auth(self):
        result = _resolve_cors_origins("https://app.example.com", auth_enabled=True)
        assert result == ["https://app.example.com"]

    def test_multi_origin_with_auth(self):
        result = _resolve_cors_origins(
            "https://app.example.com,https://qa.example.com",
            auth_enabled=True,
        )
        assert result == ["https://app.example.com", "https://qa.example.com"]

    def test_strips_whitespace_and_drops_empties(self):
        result = _resolve_cors_origins(
            "  https://a.example.com , , https://b.example.com  ",
            auth_enabled=True,
        )
        assert result == ["https://a.example.com", "https://b.example.com"]


class TestEdgeCases:
    def test_empty_string_returns_empty(self):
        assert _resolve_cors_origins("", auth_enabled=True) == []

    def test_only_commas_returns_empty(self):
        assert _resolve_cors_origins(",,,", auth_enabled=True) == []

    def test_wildcard_among_others_is_not_rejected(self):
        # `*` only triggers the safety check when it's the only origin —
        # mixing it in a list is unusual but not handled here (the developer
        # asked for it explicitly).
        result = _resolve_cors_origins("*,https://app.example.com", auth_enabled=True)
        assert "*" in result
        assert "https://app.example.com" in result
