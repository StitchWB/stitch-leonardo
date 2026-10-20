"""SQLite storage for the stitch-leonardo service plugin (sessions).

The plugin owns its SQLite database at ``db_path`` (received in the
``plugin.init`` handshake).  Tables are created on ``_migrate_db``
(both sessions and generations — schema ownership lives here).

Schema (v1):
  - sessions(id, email, cookie_enc, token_enc, token_use, exp, balance,
             status, created_at, last_check)
  - generations(id, session_id, generation_id, prompt, model, width,
                height, quantity, status, images_json, api_credit_cost,
                created_at)  — CRUD in ``generations.py``

Secrets (session cookie, Bearer JWT) are stored ONLY as Fernet
ciphertext — see the encrypted accessors below.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from stitch_leonardo import crypto

try:
    from autoreg.plugin.helpers import connect_plugin_db
except ImportError:
    from ._vendor.plugin_helpers import connect_plugin_db


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def migrate(db_path: str) -> None:
    """Create tables if they do not exist (raw_sql migration)."""
    conn = connect_plugin_db(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                cookie_enc TEXT,
                token_enc TEXT,
                token_use TEXT,
                exp INTEGER NOT NULL DEFAULT 0,
                balance INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                last_check TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS generations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                generation_id TEXT NOT NULL,
                prompt TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                quantity INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'PENDING',
                images_json TEXT,
                api_credit_cost INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def upsert_session(
    db_path: str,
    email: str,
    *,
    cookie: str,
    token: str,
    token_use: str | None = None,
    exp: int = 0,
    balance: int = 0,
    status: str = "active",
) -> dict[str, Any]:
    conn = connect_plugin_db(db_path)
    try:
        row = conn.execute("SELECT * FROM sessions WHERE email = ?", (email,)).fetchone()
        now = _utcnow()
        if row is None:
            session_id = _new_id()
            conn.execute(
                """
                INSERT INTO sessions
                    (id, email, cookie_enc, token_enc, token_use, exp,
                     balance, status, created_at, last_check)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    email,
                    crypto.encrypt(cookie),
                    crypto.encrypt(token),
                    token_use,
                    exp,
                    balance,
                    status,
                    now,
                    now,
                ),
            )
            conn.commit()
            return get_session(db_path, session_id) or {"id": session_id, "email": email}
        conn.execute(
            """
            UPDATE sessions
            SET cookie_enc = ?, token_enc = ?, token_use = ?, exp = ?,
                balance = ?, status = ?, last_check = ?
            WHERE id = ?
            """,
            (
                crypto.encrypt(cookie),
                crypto.encrypt(token),
                token_use,
                exp,
                balance,
                status,
                now,
                row["id"],
            ),
        )
        conn.commit()
        return get_session(db_path, row["id"]) or dict(row)
    finally:
        conn.close()


def get_session(db_path: str, session_id: str) -> dict[str, Any] | None:
    conn = connect_plugin_db(db_path)
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_session_by_email(db_path: str, email: str) -> dict[str, Any] | None:
    conn = connect_plugin_db(db_path)
    try:
        row = conn.execute("SELECT * FROM sessions WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def latest_active_session(db_path: str) -> dict[str, Any] | None:
    conn = connect_plugin_db(db_path)
    try:
        row = conn.execute(
            """
            SELECT * FROM sessions
            WHERE status = 'active'
            ORDER BY last_check DESC, rowid DESC LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_sessions(db_path: str) -> list[dict[str, Any]]:
    conn = connect_plugin_db(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_session_balance(db_path: str, session_id: str, *, balance: int) -> None:
    conn = connect_plugin_db(db_path)
    try:
        conn.execute(
            "UPDATE sessions SET balance = ?, last_check = ? WHERE id = ?",
            (balance, _utcnow(), session_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_session_token(
    db_path: str, session_id: str, *, token: str, exp: int, token_use: str | None
) -> None:
    conn = connect_plugin_db(db_path)
    try:
        conn.execute(
            "UPDATE sessions SET token_enc = ?, exp = ?, token_use = ? WHERE id = ?",
            (crypto.encrypt(token), exp, token_use, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_session_status(db_path: str, session_id: str, status: str) -> None:
    conn = connect_plugin_db(db_path)
    try:
        conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))
        conn.commit()
    finally:
        conn.close()


def remove_session(db_path: str, session_id: str) -> bool:
    conn = connect_plugin_db(db_path)
    try:
        row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM generations WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def session_cookie(session: dict[str, Any]) -> str:
    enc = session.get("cookie_enc")
    if not enc:
        return ""
    return crypto.decrypt_lenient(enc)


def session_token(session: dict[str, Any]) -> str:
    enc = session.get("token_enc")
    if not enc:
        return ""
    return crypto.decrypt_lenient(enc)
