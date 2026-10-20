"""LeonardoClient tests against the mock wire shapes."""

from __future__ import annotations

import pytest

from stitch_leonardo.client import LeonardoClient, LeonardoError
from stitch_leonardo.operations import GenerateRequest

from .mock_leonardo import VALID_COOKIE_VALUE, mint_jwt

COOKIE = f"__Secure-better-auth.session_data.0={VALID_COOKIE_VALUE}; other=1"


def make_client(mock) -> LeonardoClient:
    return LeonardoClient(auth_base_url=mock.auth_base_url, graphql_url=mock.graphql_url)


def test_get_token_from_cookie_better_auth(mock) -> None:
    client = make_client(mock)
    try:
        token = client.get_token_from_cookie(COOKIE)
    finally:
        client.close()
    assert token == mock.state.jwt


def test_get_token_from_cookie_dead_cookie(mock) -> None:
    mock.state.auth_mode = "dead"
    client = make_client(mock)
    try:
        token = client.get_token_from_cookie("some=other; cookie=1")
    finally:
        client.close()
    assert token is None


def test_get_token_from_cookie_challenge(mock) -> None:
    mock.state.auth_mode = "challenge"
    client = make_client(mock)
    try:
        with pytest.raises(LeonardoError) as excinfo:
            client.get_token_from_cookie(COOKIE)
    finally:
        client.close()
    assert excinfo.value.error_class == "challenge"


def test_get_user_info_sums_token_kinds(mock) -> None:
    client = make_client(mock)
    try:
        info = client.get_user_info(mock.state.jwt)
    finally:
        client.close()
    assert info.email == "test@example.com"
    assert info.tokens == 125


def test_create_generation_wire_shape(mock) -> None:
    client = make_client(mock)
    request = GenerateRequest(
        prompt="a cat", model="nano-banana-2", width=768, height=1024, quantity=2
    )
    try:
        submission = client.create_generation(mock.state.jwt, request)
    finally:
        client.close()
    assert submission.generation_id == "gen-1"
    assert submission.api_credit_cost == 4
    wire = mock.state.generate_requests[0]
    assert wire["model"] == "nano-banana-2"
    assert wire["public"] is True
    params = wire["parameters"]
    assert params["prompt"] == "a cat"
    assert params["width"] == 768
    assert params["height"] == 1024
    assert params["quantity"] == 2
    assert params["dimensions"] == "768x1024"


def test_wait_for_completion_polls_until_completed(mock) -> None:
    mock.state.add_generation("gen-done", status="PENDING")
    mock.state.generation_images["gen-done"] = ["https://cdn.example/1.png"]
    mock.state.complete_after_polls["gen-done"] = 2
    client = make_client(mock)
    try:
        result = client.wait_for_completion(
            mock.state.jwt, "gen-done", timeout=5.0, poll_interval=0.05
        )
    finally:
        client.close()
    assert result.success is True
    assert result.images == ["https://cdn.example/1.png"]


def test_wait_for_completion_failed(mock) -> None:
    mock.state.add_generation("gen-fail", status="FAILED")
    client = make_client(mock)
    try:
        result = client.wait_for_completion(
            mock.state.jwt, "gen-fail", timeout=5.0, poll_interval=0.05
        )
    finally:
        client.close()
    assert result.success is False
    assert result.error


def test_expired_token_is_not_fresh() -> None:
    from stitch_leonardo.tokens import is_fresh_token

    assert is_fresh_token(mint_jwt()) is True
    assert is_fresh_token(mint_jwt(exp=1)) is False
