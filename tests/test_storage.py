"""Storage roundtrip tests (sessions + generations)."""

from __future__ import annotations

from stitch_leonardo import generations, storage


def test_session_roundtrip(db_path: str) -> None:
    row = storage.upsert_session(
        db_path,
        email="a@example.com",
        cookie="cookie=abc",
        token="tok",
        token_use="access",
        exp=123,
        balance=10,
    )
    assert row["email"] == "a@example.com"
    listed = storage.list_sessions(db_path)
    assert len(listed) == 1
    assert listed[0]["id"] == row["id"]
    assert listed[0]["cookie_enc"]
    assert listed[0]["token_enc"]

    storage.update_session_balance(db_path, row["id"], balance=3)
    session = storage.get_session(db_path, row["id"])
    assert session["balance"] == 3

    storage.update_session_token(db_path, row["id"], token="tok2", exp=456, token_use="id")
    session = storage.get_session(db_path, row["id"])
    assert session["exp"] == 456
    assert session["token_use"] == "id"

    removed = storage.remove_session(db_path, row["id"])
    assert removed is True
    assert storage.list_sessions(db_path) == []


def test_session_upsert_same_email_updates(db_path: str) -> None:
    storage.upsert_session(
        db_path, email="a@example.com", cookie="c1", token="t1", exp=1, balance=1
    )
    storage.upsert_session(
        db_path, email="a@example.com", cookie="c2", token="t2", exp=2, balance=2
    )
    listed = storage.list_sessions(db_path)
    assert len(listed) == 1
    session = storage.get_session(db_path, listed[0]["id"])
    assert session["balance"] == 2


def test_generation_roundtrip(db_path: str) -> None:
    session = storage.upsert_session(
        db_path, email="a@example.com", cookie="c", token="t", exp=1, balance=10
    )
    gen = generations.insert_generation(
        db_path,
        session_id=session["id"],
        generation_id="gen-9",
        prompt="a cat",
        model="nano-banana-2",
        width=512,
        height=512,
        quantity=1,
        api_credit_cost=4,
    )
    generations.update_generation(
        db_path, gen["id"], status="COMPLETED", images=["https://cdn.example/a.png"]
    )
    rows = generations.list_generations(db_path)
    assert len(rows) == 1
    assert rows[0]["generation_id"] == "gen-9"
    assert rows[0]["status"] == "COMPLETED"
    assert rows[0]["images"] == 1
    assert rows[0]["prompt"] == "a cat"
