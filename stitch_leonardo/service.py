"""Service layer: session import, status, generation, history commands."""

from __future__ import annotations

from typing import Any

from stitch_leonardo import generations, storage
from stitch_leonardo.client import LeonardoClient, LeonardoError
from stitch_leonardo.config import AUTH_BASE_URL, GRAPHQL_URL
from stitch_leonardo.operations import GenerateRequest
from stitch_leonardo.tokens import (
    decode_jwt_payload,
    is_fresh_token,
    is_likely_leonardo_token,
    parse_auth_blob,
    token_exp,
)

MAX_QUANTITY = 4


def health_check() -> dict[str, Any]:
    return {"pong": True}


def _error(error_class: str, message: str) -> dict[str, Any]:
    return {"success": False, "error": message, "error_class": error_class}


def _client(auth_base_url: str | None, graphql_url: str | None) -> LeonardoClient:
    return LeonardoClient(
        auth_base_url=auth_base_url or AUTH_BASE_URL,
        graphql_url=graphql_url or GRAPHQL_URL,
    )


def session_import(
    db_path: str,
    blob: str,
    *,
    auth_base_url: str | None = None,
    graphql_url: str | None = None,
) -> dict[str, Any]:
    raw = str(blob or "").strip()
    if not raw:
        return _error("missing_cookie", "no cookie blob supplied")

    token_hint, parsed_cookie = parse_auth_blob(raw)
    cookie = parsed_cookie or raw
    client = _client(auth_base_url, graphql_url)
    try:
        token = client.get_token_from_cookie(cookie)
        if not token and token_hint:
            if is_fresh_token(token_hint) and is_likely_leonardo_token(token_hint):
                token = token_hint
        if not token:
            return _error("no_token", "cookie yielded no Leonardo JWT")
        info = client.get_user_info(token)
    except LeonardoError as exc:
        return _error(exc.error_class, str(exc))
    except Exception as exc:  # noqa: BLE001 - RPC contract returns error dicts
        return _error("api", f"unexpected: {exc}")
    finally:
        client.close()

    payload = decode_jwt_payload(token)
    row = storage.upsert_session(
        db_path,
        info.email or "unknown@leonardo",
        cookie=cookie,
        token=token,
        token_use=payload.get("token_use"),
        exp=token_exp(token),
        balance=info.tokens,
    )
    return {
        "success": True,
        "id": row["id"],
        "email": row["email"],
        "balance": info.tokens,
    }


def accounts_list(db_path: str) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "email": row["email"],
            "status": row["status"],
            "balance": row["balance"],
            "token_use": row["token_use"] or "",
            "exp": row["exp"],
            "created_at": row["created_at"],
        }
        for row in storage.list_sessions(db_path)
    ]


def _resolve_session(db_path: str, account_id: str) -> dict[str, Any] | None:
    if account_id:
        return storage.get_session(db_path, account_id)
    return storage.latest_active_session(db_path)


def _fresh_token_or_reauth(
    db_path: str,
    session: dict[str, Any],
    client: LeonardoClient,
) -> str | None:
    token = storage.session_token(session)
    if token and is_fresh_token(token):
        return token
    cookie = storage.session_cookie(session)
    token = client.get_token_from_cookie(cookie) if cookie else None
    if not token:
        return None
    storage.update_session_token(
        db_path,
        session["id"],
        token=token,
        exp=token_exp(token),
        token_use=decode_jwt_payload(token).get("token_use"),
    )
    return token


def status_refresh(
    db_path: str,
    account_id: str = "",
    *,
    auth_base_url: str | None = None,
    graphql_url: str | None = None,
) -> dict[str, Any]:
    session = _resolve_session(db_path, account_id)
    if session is None:
        return _error("no_session", "no Leonardo session stored")
    client = _client(auth_base_url, graphql_url)
    try:
        token = _fresh_token_or_reauth(db_path, session, client)
        if not token:
            storage.set_session_status(db_path, session["id"], "expired")
            return _error("no_token", "session cookie yielded no Leonardo JWT")
        info = client.get_user_info(token)
    except LeonardoError as exc:
        return _error(exc.error_class, str(exc))
    except Exception as exc:  # noqa: BLE001 - RPC contract returns error dicts
        return _error("api", f"unexpected: {exc}")
    finally:
        client.close()
    storage.update_session_balance(db_path, session["id"], balance=info.tokens)
    storage.set_session_status(db_path, session["id"], "active")
    return {"success": True, "email": session["email"], "balance": info.tokens}


def generate(
    db_path: str,
    prompt: str,
    model: str = "nano-banana-2",
    width: int = 1024,
    height: int = 1024,
    quantity: int = 1,
    account_id: str = "",
    *,
    auth_base_url: str | None = None,
    graphql_url: str | None = None,
    poll_interval: float = 3.0,
    poll_timeout: float = 240.0,
) -> dict[str, Any]:
    session = _resolve_session(db_path, account_id)
    if session is None:
        return _error("no_session", "no Leonardo session stored")
    if int(session["balance"]) <= 0:
        return _error("insufficient_balance", "token balance is zero — refresh status")

    request = GenerateRequest(
        prompt=str(prompt or "").strip(),
        model=str(model or "nano-banana-2"),
        width=max(64, min(int(width), 2048)),
        height=max(64, min(int(height), 2048)),
        quantity=max(1, min(int(quantity), MAX_QUANTITY)),
    )
    if not request.prompt:
        return _error("missing_prompt", "prompt is required")

    client = _client(auth_base_url, graphql_url)
    try:
        token = _fresh_token_or_reauth(db_path, session, client)
        if not token:
            storage.set_session_status(db_path, session["id"], "expired")
            return _error("no_token", "session cookie yielded no Leonardo JWT")
        try:
            submission = client.create_generation(token, request)
        except LeonardoError as exc:
            return _error(exc.error_class, str(exc))
        gen_row = generations.insert_generation(
            db_path,
            session_id=session["id"],
            generation_id=submission.generation_id,
            prompt=request.prompt,
            model=request.model,
            width=request.width,
            height=request.height,
            quantity=request.quantity,
            api_credit_cost=submission.api_credit_cost,
        )
        result = client.wait_for_completion(
            token, submission.generation_id, timeout=poll_timeout, poll_interval=poll_interval
        )
    except Exception as exc:  # noqa: BLE001 - RPC contract returns error dicts
        return _error("api", f"unexpected: {exc}")
    finally:
        client.close()

    if result.success:
        generations.update_generation(
            db_path, gen_row["id"], status="COMPLETED", images=result.images
        )
        remaining = max(0, int(session["balance"]) - submission.api_credit_cost)
        storage.update_session_balance(db_path, session["id"], balance=remaining)
        return {
            "success": True,
            "generationId": submission.generation_id,
            "images": result.images,
            "imagesText": "\n".join(result.images),
            "status": "COMPLETED",
        }
    generations.update_generation(db_path, gen_row["id"], status="FAILED")
    return _error("generation_failed", result.error or "generation failed")


def history_list(db_path: str) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "prompt": row["prompt"],
            "model": row["model"],
            "status": row["status"],
            "images": row["images"],
        }
        for row in generations.list_generations(db_path)
    ]


def session_remove(db_path: str, account_id: str) -> dict[str, Any]:
    removed = storage.remove_session(db_path, str(account_id or ""))
    if not removed:
        return _error("no_session", "unknown session id")
    return {"success": True}
