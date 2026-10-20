# Stitch Leonardo (stitch-leonardo)

Open-core **service plugin** for [Stitch Manager](https://github.com/StitchWB/Stitch-Manager):
a **Leonardo AI image-generation surface** over imported web sessions —
session import (cookie → Cognito JWT), token balance, generation
(GraphQL `Generate` + polling), local generation history.

- **Kind:** `service` (plugin id `stitch-leonardo`, category `ai-tools`)
- **Runtime:** spawned by the Stitch `ServicePluginHost` as `python -m stitch_leonardo`,
  speaks JSON-RPC 2.0 (vendored `RpcPluginServer`)
- **Self-contained:** no `stitch_backend` imports; stdlib + `httpx` +
  `cryptography` + `curl_cffi` (Chrome TLS impersonation for
  app.leonardo.ai — Vercel checkpoint precaution, same as the hub's
  leonardo provider)
- **Privacy:** session cookies and Bearer JWTs are Fernet-encrypted at
  rest and never leave the machine. `keys/` is gitignored — signing keys
  live outside the package.

## Wire-shape honesty (read this first)

Shapes come from the hirotomasato/leostudio + leoapi recon (2026-10-20):

| Surface | Status |
|---|---|
| `GET app.leonardo.ai/api/auth/get-session` (unauth) | **verified live** 2026-10-20 (200 `null`) |
| `POST api.leonardo.ai/v1/graphql` (unauth) | **verified live** 2026-10-20 (query root) |
| `GET app.leonardo.ai/api/auth/session` (legacy next-auth) | **verified dead** (404) — kept as source-era fallback |
| better-auth session JSON carrying a Cognito JWT | from source, **not live-verified** |
| `GetUserDetails` / `GetTokenBalance` queries | from source, **not live-verified** |
| `Generate` mutation, feed status/images queries | from leostudio `internal/leonardo/operations.go`, **not live-verified** |

No live Leonardo API calls were made while building this plugin; the
test suite runs against a mock server that reproduces the documented
wire shapes. The first real session import + generation against
app.leonardo.ai is the remaining live QA step (needs a real exported
cookie). The GraphQL schema may have drifted since Jun 2026 — the
source repos themselves warn "Internal APIs may shift between minor
versions".

## Architecture: own session store (why not hub accounts)

Leonardo accounts imported by the hub's `leonardo` provider live in the
core `accounts` table. This plugin **cannot read them**: the service
plugin host exposes no sanctioned hub-accounts read path — the only
reverse-RPC methods the host serves are `engine.oauth.*`,
`host.secrets.*` and `plugin.call_plugin` (plugin-to-plugin, gated by
`depends`); `docs/service-plugins.md` §Settings says any other config
must come from the plugin's own RPC/SQLite. So the plugin manages its
**own imported sessions** (same pattern as stitch-domains' gname
accounts): `leonardo_session_import` pastes an ExLeo-style cookie blob,
the plugin resolves the JWT and stores the session Fernet-encrypted in
its own SQLite. A hub-side bridge (dual-router pattern like
`mail_dual.py`) would be the hub's decision, not a plugin-side one.

## Commands

| Command | Readonly | Description |
|---|---|---|
| `health_check` | yes | Health check — `{pong: true}` |
| `leonardo_session_import` | no | Paste an ExLeo cookie blob (`cookie=…` / `token=…` lines or a raw cookie string); resolves the Cognito JWT via better-auth get-session, fetches email + token balance, upserts the session |
| `leonardo_accounts_list` | yes | Imported sessions (email, status, balance, token type, JWT expiry) — no secrets in the response |
| `leonardo_status` | yes | Refresh balance for a session (GetTokenBalance); stale JWTs are re-resolved from the stored cookie |
| `leonardo_generate` | no | Generate: prompt, model, width/height (64–2048), quantity (1–4); balance pre-check, `Generate` mutation, poll until COMPLETED/FAILED, image URLs in the result (`imagesText` is copyable) |
| `leonardo_history` | yes | Local generation history (plugin-initiated only — the remote feed shape is not in the recon verdict, so no remote sync) |
| `leonardo_session_remove` | no | Remove a session row (danger action, confirm-gated in the UI) |

Balance bookkeeping: the `Generate` mutation returns `apiCreditCost`,
which is subtracted from the stored balance after a completed
generation; `leonardo_status` refreshes the real balance from the API.
The per-image cost is otherwise unknown before submission — the
pre-check only refuses a zero balance.

## Development

```bash
# from the Stitch-Manager repo root (or with python/ on sys.path):
python -m stitch_plugin_tools run .        # interactive REPL, no host boot
python -m stitch_plugin_tools test .       # pytest suite (mock Leonardo API)
python -m stitch_plugin_tools manifest-lint plugin.json
python -m stitch_plugin_tools verify .     # ruff + manifest-lint + pytest + classify
python -m stitch_plugin_tools dev-install .
STITCH_DEV_MODE=1 python -m stitch_backend # plugin appears as an AI Hub tab
```

## Layout

```
stitch-leonardo/
├── plugin.json              manifest (schema v2, declarative UI + i18n ru/en)
├── ruff.toml                lint config (mirrors the Stitch monorepo)
├── stitch_leonardo/
│   ├── __main__.py          JSON-RPC entry (RpcPluginServer)
│   ├── service.py           command layer (import/status/generate/history)
│   ├── client.py            GraphQL client (user info + generation, httpx)
│   ├── auth.py              cookie → JWT resolution chain (curl_cffi)
│   ├── operations.py        GraphQL operations + result mapping
│   ├── tokens.py            JWT/cookie heuristics (Cognito, ExLeo parsing)
│   ├── storage.py           SQLite DDL + sessions CRUD
│   ├── generations.py       generation records CRUD
│   ├── crypto.py            Fernet at-rest encryption (same scheme as core)
│   └── _vendor/             vendored RpcPluginServer (regenerated; do not edit)
└── tests/                   protocol + mock-wire client/service/storage tests
```

## Disclaimer

For automation of your own accounts. Obey Leonardo AI's ToS and local
law.
