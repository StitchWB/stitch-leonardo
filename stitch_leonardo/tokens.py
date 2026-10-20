"""JWT/cookie heuristics for Leonardo web sessions.

Ported from the hub's leonardo provider package (itself ported from
leoapi ``app/leonardo_client.py`` / leostudio ``internal/leonardo/client.go``):
Cognito token detection, freshness, best-token ranking, chunked cookie
reassembly, ExLeo export parsing.
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any
from urllib.parse import unquote

MIN_TOKEN_TTL = 120

_JWT_SEGMENTS_RE = re.compile(r"[A-Za-z0-9_-]+")


def looks_like_jwt(value: str) -> bool:
    token = (value or "").strip()
    if token.count(".") != 2:
        return False
    return all(_JWT_SEGMENTS_RE.fullmatch(part) for part in token.split("."))


def decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    pad = "=" * ((4 - len(payload) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + pad).decode("utf-8")
        out = json.loads(decoded)
    except (ValueError, UnicodeDecodeError):
        return {}
    return out if isinstance(out, dict) else {}


def is_likely_leonardo_token(token: str) -> bool:
    if not looks_like_jwt(token):
        return False
    payload = decode_jwt_payload(token)
    if not payload:
        return False
    iss = str(payload.get("iss", "")).lower()
    if "cognito-idp" in iss:
        return True
    token_use = str(payload.get("token_use", "")).lower()
    if token_use in {"id", "access"}:
        return True
    if "cognito:username" in payload:
        return True
    aud = payload.get("aud")
    if isinstance(aud, str) and aud.startswith("https://cognito-idp"):
        return True
    return False


def token_exp(token: str) -> int:
    exp = decode_jwt_payload(token).get("exp")
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        return int(exp)
    return 0


def is_fresh_token(token: str, min_ttl_seconds: int = MIN_TOKEN_TTL) -> bool:
    if not looks_like_jwt(token):
        return False
    exp = token_exp(token)
    if not exp:
        return True
    return exp > int(time.time()) + max(30, int(min_ttl_seconds))


def normalize_token_candidate(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    token = unquote(value.strip())
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def pick_best_token(candidates: list[str], min_ttl_seconds: int = MIN_TOKEN_TTL) -> str | None:
    if not candidates:
        return None
    fresh = [t for t in candidates if is_fresh_token(t, min_ttl_seconds=min_ttl_seconds)]
    pool = fresh or [t for t in candidates if looks_like_jwt(t)]
    if not pool:
        return None
    likely = [t for t in pool if is_likely_leonardo_token(t)]
    if likely:
        pool = likely

    def rank(token: str) -> tuple[int, int]:
        token_use = str(decode_jwt_payload(token).get("token_use", "")).lower()
        use_score = 3 if token_use == "access" else 2 if token_use == "id" else 1
        return (use_score, token_exp(token) or int(time.time()) + 120)

    return max(pool, key=rank)


def find_token_in_object(data: Any) -> str | None:
    candidates: list[str] = []

    def add_candidate(raw: Any) -> None:
        token = normalize_token_candidate(raw)
        if token and looks_like_jwt(token):
            candidates.append(token)

    def walk(node: Any) -> None:
        if isinstance(node, str):
            add_candidate(node)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        known_paths = (
            ("accessToken",),
            ("access_token",),
            ("idToken",),
            ("id_token",),
            ("token",),
            ("user", "accessToken"),
            ("user", "access_token"),
            ("user", "idToken"),
            ("user", "id_token"),
            ("session", "accessToken"),
            ("session", "idToken"),
        )
        for path in known_paths:
            cur: Any = node
            for key in path:
                if not isinstance(cur, dict) or key not in cur:
                    cur = None
                    break
                cur = cur[key]
            if cur is not None:
                add_candidate(cur)
        for key, value in node.items():
            key_lower = str(key).lower()
            if "cf_access_token" in key_lower:
                continue
            if (
                key_lower in {"idtoken", "accesstoken", "id_token", "access_token", "token"}
                or "token" in key_lower
                or isinstance(value, (dict, list))
            ):
                walk(value)

    walk(data)
    return pick_best_token(candidates)


def extract_cookie_value(cookie_str: str, base_name: str) -> str | None:
    cookies: dict[str, str] = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        cookies[key.strip()] = value.strip()
    if base_name in cookies:
        return cookies[base_name]
    prefix = f"{base_name}."
    chunks: list[tuple[int, str]] = []
    for key, value in cookies.items():
        suffix = key[len(prefix) :] if key.startswith(prefix) else ""
        if suffix.isdigit():
            chunks.append((int(suffix), value))
    if not chunks:
        return None
    chunks.sort(key=lambda pair: pair[0])
    return "".join(value for _, value in chunks)


def parse_auth_blob(raw: str) -> tuple[str | None, str | None]:
    """Split an ExLeo-style export into ``(token, cookie)``.

    Accepts ``cookie=...``/``token=...`` lines, a ``Cookie:`` header line,
    a raw cookie string, or a bare JWT line.
    """
    text = (raw or "").strip()
    if not text:
        return None, None
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    token: str | None = None
    cookie: str | None = None
    for line in lines:
        if line.lower().startswith("cookie:"):
            line = line.split(":", 1)[1].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip().lower()
            value = value.strip()
            if key == "token" and value:
                token = value
                continue
            if key == "cookie" and value:
                cookie = value
                continue
            if ";" in line:
                cookie = line
                continue
            if "next-auth" in key or "authjs" in key or "better-auth" in key:
                cookie = line
                continue
        elif line.count(".") == 2 and " " not in line and len(line) > 40:
            token = line
            continue
        if ";" in line and "=" in line:
            cookie = line
    return token, cookie


__all__ = [
    "decode_jwt_payload",
    "extract_cookie_value",
    "find_token_in_object",
    "is_fresh_token",
    "is_likely_leonardo_token",
    "looks_like_jwt",
    "normalize_token_candidate",
    "parse_auth_blob",
    "pick_best_token",
    "token_exp",
]
