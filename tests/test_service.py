"""Service-layer command tests (session import, status, generate, history)."""

from __future__ import annotations

import time

from stitch_leonardo import service

from .mock_leonardo import VALID_COOKIE_VALUE, mint_jwt

BLOB = f"cookie=__Secure-better-auth.session_data.0={VALID_COOKIE_VALUE}; other=1"


def import_session(db_path: str, mock) -> dict:
    return service.session_import(
        db_path, BLOB, auth_base_url=mock.auth_base_url, graphql_url=mock.graphql_url
    )


def test_session_import_happy_path(db_path: str, mock) -> None:
    result = import_session(db_path, mock)
    assert result["success"] is True
    assert result["email"] == "test@example.com"
    assert result["balance"] == 125
    rows = service.accounts_list(db_path)
    assert len(rows) == 1
    assert rows[0]["email"] == "test@example.com"
    assert rows[0]["balance"] == 125
    assert rows[0]["status"] == "active"
    assert "cookie" not in rows[0]
    assert "token" not in rows[0]


def test_session_import_stores_secrets_encrypted(db_path: str, mock) -> None:
    import_session(db_path, mock)
    from stitch_leonardo import storage

    session = storage.get_session_by_email(db_path, "test@example.com")
    assert session is not None
    assert session["cookie_enc"] != BLOB
    assert VALID_COOKIE_VALUE not in session["cookie_enc"]
    assert session["token_enc"] != mock.state.jwt


def test_session_import_missing_blob(db_path: str, mock) -> None:
    result = service.session_import(db_path, "  ", auth_base_url=mock.auth_base_url)
    assert result["success"] is False
    assert result["error_class"] == "missing_cookie"


def test_session_import_dead_cookie(db_path: str, mock) -> None:
    mock.state.auth_mode = "dead"
    result = service.session_import(
        db_path,
        "cookie=not-a-session; x=1",
        auth_base_url=mock.auth_base_url,
        graphql_url=mock.graphql_url,
    )
    assert result["success"] is False
    assert result["error_class"] == "no_token"


def test_session_import_reuses_row_per_email(db_path: str, mock) -> None:
    import_session(db_path, mock)
    import_session(db_path, mock)
    rows = service.accounts_list(db_path)
    assert len(rows) == 1


def test_status_refresh_updates_balance(db_path: str, mock) -> None:
    import_session(db_path, mock)
    mock.state.balance = {"subscriptionTokens": 40, "paidTokens": 0, "rolloverTokens": 0}
    account_id = service.accounts_list(db_path)[0]["id"]
    result = service.status_refresh(
        db_path, account_id, auth_base_url=mock.auth_base_url, graphql_url=mock.graphql_url
    )
    assert result["success"] is True
    assert result["balance"] == 40
    assert service.accounts_list(db_path)[0]["balance"] == 40


def test_generate_happy_path(db_path: str, mock) -> None:
    import_session(db_path, mock)
    result = service.generate(
        db_path,
        prompt="a cat",
        model="nano-banana-2",
        width=512,
        height=512,
        quantity=1,
        auth_base_url=mock.auth_base_url,
        graphql_url=mock.graphql_url,
        poll_interval=0.05,
    )
    expected = mock.state.default_images("gen-1")
    assert result["success"] is True
    assert result["generationId"] == "gen-1"
    assert result["images"] == expected
    assert result["imagesText"] == "\n".join(expected)
    history = service.history_list(db_path)
    assert len(history) == 1
    assert history[0]["prompt"] == "a cat"
    assert history[0]["status"] == "COMPLETED"
    assert history[0]["images"] == 2
    assert service.accounts_list(db_path)[0]["balance"] == 121


def test_generate_insufficient_balance(db_path: str, mock) -> None:
    import_session(db_path, mock)
    account_id = service.accounts_list(db_path)[0]["id"]
    from stitch_leonardo import storage

    storage.update_session_balance(db_path, account_id, balance=0)
    result = service.generate(
        db_path,
        prompt="a cat",
        model="nano-banana-2",
        width=512,
        height=512,
        quantity=1,
        auth_base_url=mock.auth_base_url,
        graphql_url=mock.graphql_url,
    )
    assert result["success"] is False
    assert result["error_class"] == "insufficient_balance"
    assert mock.state.generate_requests == []


def test_generate_no_session(db_path: str, mock) -> None:
    result = service.generate(
        db_path,
        prompt="a cat",
        model="nano-banana-2",
        width=512,
        height=512,
        quantity=1,
        auth_base_url=mock.auth_base_url,
        graphql_url=mock.graphql_url,
    )
    assert result["success"] is False
    assert result["error_class"] == "no_session"


def test_generate_stale_token_reauthenticates(db_path: str, mock) -> None:
    from stitch_leonardo import storage

    storage.upsert_session(
        db_path,
        email="test@example.com",
        cookie=BLOB,
        token=mint_jwt(exp=int(time.time()) - 10),
        token_use="access",
        exp=int(time.time()) - 10,
        balance=100,
    )
    result = service.generate(
        db_path,
        prompt="a cat",
        model="nano-banana-2",
        width=512,
        height=512,
        quantity=1,
        auth_base_url=mock.auth_base_url,
        graphql_url=mock.graphql_url,
        poll_interval=0.05,
    )
    assert result["success"] is True


def test_generate_auth_failure_structured(db_path: str, mock) -> None:
    from stitch_leonardo import storage

    storage.upsert_session(
        db_path,
        email="test@example.com",
        cookie="cookie=dead; x=1",
        token=mint_jwt(exp=int(time.time()) - 10),
        token_use="access",
        exp=int(time.time()) - 10,
        balance=100,
    )
    mock.state.auth_mode = "dead"
    result = service.generate(
        db_path,
        prompt="a cat",
        model="nano-banana-2",
        width=512,
        height=512,
        quantity=1,
        auth_base_url=mock.auth_base_url,
        graphql_url=mock.graphql_url,
    )
    assert result["success"] is False
    assert result["error_class"] == "no_token"


def test_session_remove(db_path: str, mock) -> None:
    import_session(db_path, mock)
    account_id = service.accounts_list(db_path)[0]["id"]
    result = service.session_remove(db_path, account_id)
    assert result["success"] is True
    assert service.accounts_list(db_path) == []


def test_session_remove_unknown(db_path: str) -> None:
    result = service.session_remove(db_path, "nope")
    assert result["success"] is False
