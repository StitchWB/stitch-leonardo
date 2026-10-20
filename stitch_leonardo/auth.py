"""Leonardo web-session token resolution (auth chain).

Strategy chain ported from the hub's leonardo provider package (source:
leoapi ``app/leonardo_client.py`` / leostudio ``internal/leonardo/client.go``):
better-auth ``GET /api/auth/get-session`` (curl_cffi Chrome TLS
impersonation — Vercel checkpoint precaution) → legacy next-auth
``/api/auth/session`` (dead live, kept as source-era fallback) → raw JWT
embedded in a session cookie.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import httpx
from curl_cffi import requests as cffi_requests
from curl_cffi.requests.exceptions import RequestException as CffiRequestException

from stitch_leonardo.config import (
    CSRF_COOKIE_NAMES,
    DEFAULT_IMPERSONATE,
    SESSION_COOKIE_NAMES,
)
from stitch_leonardo.tokens import (
    extract_cookie_value,
    find_token_in_object,
    is_fresh_token,
    normalize_token_candidate,
)


class LeonardoError(Exception):
    """API-level failure; ``error_class`` routes the failure for the caller."""

    def __init__(self, message: str, error_class: str = "api") -> None:
        super().__init__(message)
        self.error_class = error_class


def base_headers() -> dict[str, str]:
    return {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://app.leonardo.ai",
        "referer": "https://app.leonardo.ai/",
        "x-leo-schema-version": "latest",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
    }


class AuthResolver:
    """Resolves a Bearer JWT from a Leonardo session cookie string."""

    def __init__(
        self,
        auth_base_url: str,
        gql_client: httpx.Client,
        timeout: float,
        proxy: str | None = None,
        impersonate: str | None = DEFAULT_IMPERSONATE,
    ) -> None:
        self.auth_base_url = auth_base_url.rstrip("/")
        self._gql_client = gql_client
        self._auth = cffi_requests.Session(
            impersonate=impersonate,
            timeout=timeout,
            proxies={"all": proxy} if proxy else None,
        )

    def close(self) -> None:
        self._auth.close()

    def _fetch_better_auth_session(self, cookie_str: str) -> dict[str, Any] | None:
        try:
            response = self._auth.get(
                f"{self.auth_base_url}/get-session",
                headers={
                    "cookie": cookie_str,
                    "accept": "application/json",
                    "accept-language": "en-US,en;q=0.9",
                    "origin": "https://app.leonardo.ai",
                    "referer": "https://app.leonardo.ai/",
                },
            )
        except CffiRequestException as exc:
            raise LeonardoError(f"get-session request failed: {exc}", "network") from exc
        if response.status_code in (403, 429):
            raise LeonardoError(f"get-session blocked: HTTP {response.status_code}", "challenge")
        if response.status_code != 200 or not response.content:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def _fetch_next_auth_session(self, cookie_str: str, csrf: str) -> dict[str, Any] | None:
        headers = base_headers()
        headers["cookie"] = cookie_str
        try:
            if csrf:
                response = self._gql_client.post(
                    f"{self.auth_base_url}/session",
                    json={"csrfToken": csrf},
                    headers=headers,
                )
            else:
                response = self._gql_client.get(f"{self.auth_base_url}/session", headers=headers)
        except httpx.HTTPError as exc:
            raise LeonardoError(f"next-auth session request failed: {exc}", "network") from exc
        if response.status_code >= 400 or not response.content:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def get_token_from_cookie(self, cookie_str: str) -> str | None:
        failures: list[LeonardoError] = []

        try:
            session = self._fetch_better_auth_session(cookie_str)
            if session is not None:
                token = find_token_in_object(session)
                if token and is_fresh_token(token):
                    return token
        except LeonardoError as exc:
            failures.append(exc)

        csrf = ""
        for name in CSRF_COOKIE_NAMES:
            raw = extract_cookie_value(cookie_str, name)
            if raw:
                csrf = unquote(raw).split("|", 2)[0]
                break

        try:
            legacy = self._fetch_next_auth_session(cookie_str, csrf)
            if legacy is not None:
                token = find_token_in_object(legacy)
                if token and is_fresh_token(token):
                    return token
        except LeonardoError as exc:
            failures.append(exc)

        for name in SESSION_COOKIE_NAMES:
            raw = extract_cookie_value(cookie_str, name)
            token = normalize_token_candidate(raw or "")
            if token and is_fresh_token(token):
                return token

        if failures:
            raise failures[0]
        return None


__all__ = ["AuthResolver", "LeonardoError", "base_headers"]
