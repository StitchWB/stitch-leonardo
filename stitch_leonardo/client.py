"""Leonardo AI GraphQL client (user info + generation).

Wire shapes verified by recon probes (2026-10-20, unauthenticated):
  POST https://api.leonardo.ai/v1/graphql (no auth) -> 200 {"data":{"__typename":"query_root"}}
Shapes below marked "source" come from leostudio/leoapi (Apr-Jun 2026),
unverified live (no account during recon):
  POST /v1/graphql GetUserDetails     -> users.user_details{subscriptionTokens,paidTokens,rolloverTokens,auth0Email}
  POST /v1/graphql GetTokenBalance    -> user_details{...}
  POST /v1/graphql Generate           -> generate{apiCreditCost,generationId}
  POST /v1/graphql feed status/images -> generations{status,generated_images{url}}
GraphQL requests carry sentry-trace/baggage + x-leo-schema-version: latest.
api.leonardo.ai GraphQL goes through plain httpx; the auth chain lives in
``auth.py`` (curl_cffi Chrome TLS impersonation for app.leonardo.ai).
"""

from __future__ import annotations

import random
import string
import time
from typing import Any

import httpx

from stitch_leonardo.auth import AuthResolver, LeonardoError, base_headers
from stitch_leonardo.config import (
    AUTH_BASE_URL,
    DEFAULT_IMPERSONATE,
    GRAPHQL_URL,
    SENTRY_ORG_ID,
    SENTRY_PUBLIC_KEY,
    SENTRY_RELEASE,
)
from stitch_leonardo.operations import (
    GenerateRequest,
    GenerationResult,
    GenerationSubmission,
    LeonardoUserInfo,
    generate_mutation,
    generation_feed_query,
    generation_status_query,
    graphql_error_message,
    token_balance_query,
    user_details_query,
    user_info_from_details,
)
from stitch_leonardo.tokens import decode_jwt_payload

DEFAULT_TIMEOUT = 60.0


class LeonardoClient:
    """Leonardo AI web-session client (auth + GraphQL + generation)."""

    def __init__(
        self,
        auth_base_url: str = AUTH_BASE_URL,
        graphql_url: str = GRAPHQL_URL,
        timeout: float = DEFAULT_TIMEOUT,
        proxy: str | None = None,
        impersonate: str | None = DEFAULT_IMPERSONATE,
    ) -> None:
        self.auth_base_url = auth_base_url.rstrip("/")
        self.graphql_url = graphql_url
        self.timeout = timeout
        # trust_env=False: proxy only from the explicit param, never ambient env vars
        self._gql_client = httpx.Client(timeout=timeout, proxy=proxy, trust_env=False)
        self._auth_resolver = AuthResolver(
            auth_base_url,
            self._gql_client,
            timeout,
            proxy=proxy,
            impersonate=impersonate,
        )

    def close(self) -> None:
        self._gql_client.close()
        self._auth_resolver.close()

    def get_token_from_cookie(self, cookie_str: str) -> str | None:
        return self._auth_resolver.get_token_from_cookie(cookie_str)

    def _sentry_headers(self, token: str) -> dict[str, str]:
        tid = _make_id() + _make_id()
        headers = base_headers()
        headers["authorization"] = f"Bearer {token}"
        headers["sentry-trace"] = f"{tid}-{_make_id()[:16]}-0"
        headers["baggage"] = ",".join(
            [
                "sentry-environment=vercel-production",
                f"sentry-release={SENTRY_RELEASE}",
                f"sentry-public_key={SENTRY_PUBLIC_KEY}",
                f"sentry-trace_id={tid}",
                f"sentry-org_id={SENTRY_ORG_ID}",
                "sentry-sampled=false",
            ]
        )
        return headers

    def _gql(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._gql_client.post(
                self.graphql_url, json=payload, headers=self._sentry_headers(token)
            )
        except httpx.HTTPError as exc:
            raise LeonardoError(f"graphql request failed: {exc}", "network") from exc
        if response.status_code == 204:
            return {}
        body: Any = None
        if response.content:
            try:
                body = response.json()
            except ValueError as exc:
                raise LeonardoError(
                    f"graphql HTTP {response.status_code}: undecodable body", "graphql"
                ) from exc
        if response.status_code >= 400:
            snippet = response.text[:200]
            raise LeonardoError(f"graphql HTTP {response.status_code}: {snippet}", "graphql")
        return body if isinstance(body, dict) else {}

    def get_user_info(self, token: str) -> LeonardoUserInfo:
        payload = decode_jwt_payload(token)
        cognito_sub = str(payload.get("sub", "") or "")
        token_email = str(payload.get("email", "") or "").strip()

        response = self._gql(token, user_details_query(cognito_sub))
        users = (response.get("data") or {}).get("users") or []
        if users:
            user = users[0] if isinstance(users[0], dict) else {}
            details_list = user.get("user_details") or []
            if details_list and isinstance(details_list[0], dict):
                return user_info_from_details(details_list[0], token_email)

        response = self._gql(token, token_balance_query())
        details_list = (response.get("data") or {}).get("user_details") or []
        if details_list and isinstance(details_list[0], dict):
            return user_info_from_details(details_list[0], token_email)
        message = graphql_error_message(response)
        if message:
            raise LeonardoError(f"get user info failed: {message}", "graphql")
        raise LeonardoError("get user info failed: empty user_details", "user_details")

    def create_generation(self, token: str, request: GenerateRequest) -> GenerationSubmission:
        response = self._gql(token, generate_mutation(request))
        message = graphql_error_message(response)
        if message:
            raise LeonardoError(f"generate failed: {message}", "graphql")
        gen = (response.get("data") or {}).get("generate") or {}
        generation_id = str(gen.get("generationId", "") or "")
        if not generation_id:
            raise LeonardoError("generate failed: empty generationId", "graphql")
        cost = gen.get("apiCreditCost")
        api_credit_cost = int(cost) if isinstance(cost, (int, float)) else 0
        return GenerationSubmission(generation_id=generation_id, api_credit_cost=api_credit_cost)

    def poll_status(self, token: str, generation_id: str) -> str:
        response = self._gql(token, generation_status_query(generation_id))
        gens = (response.get("data") or {}).get("generations") or []
        if not gens:
            return "PENDING"
        first = gens[0] if isinstance(gens[0], dict) else {}
        return str(first.get("status", "") or "") or "PENDING"

    def get_image_urls(self, token: str, generation_id: str) -> list[str]:
        response = self._gql(token, generation_feed_query(generation_id))
        gens = (response.get("data") or {}).get("generations") or []
        if not gens:
            return []
        first = gens[0] if isinstance(gens[0], dict) else {}
        images = first.get("generated_images") or []
        urls: list[str] = []
        for item in images:
            if isinstance(item, dict):
                url = str(item.get("url", "") or "")
                if url:
                    urls.append(url)
        return urls

    def wait_for_completion(
        self,
        token: str,
        generation_id: str,
        timeout: float = 240.0,
        poll_interval: float = 3.0,
    ) -> GenerationResult:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.poll_status(token, generation_id)
            if status == "COMPLETED":
                return GenerationResult(
                    success=True, images=self.get_image_urls(token, generation_id)
                )
            if status in ("FAILED", "ERROR"):
                return GenerationResult(success=False, images=[], error="generation failed")
            time.sleep(poll_interval)
        images = self.get_image_urls(token, generation_id)
        if images:
            return GenerationResult(success=True, images=images)
        return GenerationResult(success=False, images=[], error="generation timeout")


def _make_id() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


__all__ = ["LeonardoClient", "LeonardoError"]
