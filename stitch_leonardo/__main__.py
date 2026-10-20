"""RPC entry point for the stitch-leonardo service plugin.

Spawned by ``ServicePluginHost`` as ``python -m stitch_leonardo``.
Implements the JSON-RPC 2.0 line protocol via ``RpcPluginServer``.

Protocol methods handled automatically by ``RpcPluginServer``:
  - ``plugin.init``    -> stores handshake params via ``_Ctx``.
  - ``plugin.ping``    -> returns ``"pong"``.
  - ``plugin.shutdown``-> returns ``None`` and exits.

``plugin.call`` dispatches to registered handlers.  ``_migrate_db``
is a reserved call name used by the host after init to create SQLite
tables (``contributions.storage.migrations = "raw_sql"``).

Standalone: tries to import ``RpcPluginServer`` from
``autoreg.plugin.rpc`` (available when ``autoreg`` is on ``sys.path``).
If the import fails (standalone plugin without the host's python tree),
a vendored equivalent is loaded from ``._vendor.rpc_server``.
"""

# _generated_by: stitch_plugin_tools scaffold v3

from __future__ import annotations

from typing import Any

from . import service, storage

try:
    from autoreg.plugin.rpc import RpcPluginServer
except ImportError:
    from ._vendor.rpc_server import RpcPluginServer


class _Ctx:
    """Mutable container for plugin.init handshake state."""

    db_path: str = ""
    data_dir: str = ""
    supported: list[str] = []


ctx = _Ctx()


def _handle_init(params: dict[str, Any]) -> dict[str, Any]:
    ctx.db_path = str(params.get("db_path", ""))
    ctx.data_dir = str(params.get("data_dir", ""))
    supported = params.get("supported")
    ctx.supported = list(supported) if isinstance(supported, list) else []
    from . import crypto

    if ctx.data_dir:
        crypto.init_crypto(ctx.data_dir)
    return {
        "plugin_id": params.get("plugin_id", ""),
        "db_path": ctx.db_path,
        "data_dir": ctx.data_dir,
        "capabilities": [],
    }


def _handle_migrate_db(params: dict[str, Any]) -> dict[str, Any]:
    if ctx.db_path:
        storage.migrate(ctx.db_path)
    return {
        "from_version": params.get("from_version", 0),
        "to_version": params.get("to_version", 1),
    }


def _handle_health_check(params: dict[str, Any]) -> dict[str, Any]:
    return service.health_check()


def _handle_session_import(params: dict[str, Any]) -> dict[str, Any]:
    blob = str(params.get("blob", params.get("cookie", "")) or "")
    return service.session_import(ctx.db_path, blob)


def _handle_accounts_list(params: dict[str, Any]) -> list[dict[str, Any]]:
    return service.accounts_list(ctx.db_path)


def _handle_status(params: dict[str, Any]) -> dict[str, Any]:
    account_id = str(params.get("accountId", params.get("account_id", "")) or "")
    return service.status_refresh(ctx.db_path, account_id)


def _to_int(value: Any, default: int) -> int:
    try:
        return int(str(value) if isinstance(value, str) else value)
    except (TypeError, ValueError):
        return default


def _handle_generate(params: dict[str, Any]) -> dict[str, Any]:
    account_id = str(params.get("accountId", params.get("account_id", "")) or "")
    return service.generate(
        ctx.db_path,
        str(params.get("prompt", "") or ""),
        model=str(params.get("model", "nano-banana-2") or "nano-banana-2"),
        width=_to_int(params.get("width"), 1024),
        height=_to_int(params.get("height"), 1024),
        quantity=_to_int(params.get("quantity"), 1),
        account_id=account_id,
    )


def _handle_history(params: dict[str, Any]) -> list[dict[str, Any]]:
    return service.history_list(ctx.db_path)


def _handle_session_remove(params: dict[str, Any]) -> dict[str, Any]:
    account_id = str(params.get("accountId", params.get("account_id", "")) or "")
    return service.session_remove(ctx.db_path, account_id)


def main() -> None:
    """Register handlers and serve the JSON-RPC loop."""
    server = RpcPluginServer()
    server.set_init_handler(_handle_init)
    server.register("_migrate_db", _handle_migrate_db)
    server.register("health_check", _handle_health_check)
    server.register("leonardo_session_import", _handle_session_import)
    server.register("leonardo_accounts_list", _handle_accounts_list)
    server.register("leonardo_status", _handle_status)
    server.register("leonardo_generate", _handle_generate)
    server.register("leonardo_history", _handle_history)
    server.register("leonardo_session_remove", _handle_session_remove)
    server.serve()


if __name__ == "__main__":
    main()
