"""Generation records for the stitch-leonardo service plugin.

CRUD for the ``generations`` table (schema in ``storage.migrate``):
plugin-initiated generations only — the remote Leonardo feed shape is
not in the recon verdict, so there is no remote sync.
"""

from __future__ import annotations

import json
from typing import Any

try:
    from autoreg.plugin.helpers import connect_plugin_db
except ImportError:
    from ._vendor.plugin_helpers import connect_plugin_db

from .storage import _new_id, _utcnow


def insert_generation(
    db_path: str,
    *,
    session_id: str,
    generation_id: str,
    prompt: str,
    model: str,
    width: int,
    height: int,
    quantity: int,
    api_credit_cost: int,
) -> dict[str, Any]:
    gen_row_id = _new_id()
    conn = connect_plugin_db(db_path)
    try:
        conn.execute(
            """
            INSERT INTO generations
                (id, session_id, generation_id, prompt, model, width, height,
                 quantity, status, images_json, api_credit_cost, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gen_row_id,
                session_id,
                generation_id,
                prompt,
                model,
                width,
                height,
                quantity,
                "PENDING",
                None,
                api_credit_cost,
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "id": gen_row_id,
        "session_id": session_id,
        "generation_id": generation_id,
    }


def update_generation(
    db_path: str, gen_row_id: str, *, status: str, images: list[str] | None = None
) -> None:
    conn = connect_plugin_db(db_path)
    try:
        if images is not None:
            conn.execute(
                "UPDATE generations SET status = ?, images_json = ? WHERE id = ?",
                (status, json.dumps(images), gen_row_id),
            )
        else:
            conn.execute("UPDATE generations SET status = ? WHERE id = ?", (status, gen_row_id))
        conn.commit()
    finally:
        conn.close()


def list_generations(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = connect_plugin_db(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM generations ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            row = dict(r)
            images = row.pop("images_json", None)
            try:
                parsed = json.loads(images) if images else []
            except ValueError:
                parsed = []
            row["images"] = len(parsed) if isinstance(parsed, list) else 0
            out.append(row)
        return out
    finally:
        conn.close()
