"""Mock Leonardo API — wire shapes from the leonardo provider package
(better-auth get-session, GetUserDetails/GetTokenBalance) and
hirotomasato/leostudio operations.go (Generate, feed status, feed images).

Serves:
  GET  /api/auth/get-session   better-auth session (cookie-gated)
  *    /api/auth/session       legacy next-auth (404 — dead live)
  POST /v1/graphql             dispatch by operationName

State (auth mode, balances, generation status) is inspectable for assertions.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VALID_COOKIE_VALUE = "session-ok"


def mint_jwt(
    sub: str = "test-sub",
    email: str = "test@example.com",
    exp: int | None = None,
    token_use: str = "access",
) -> str:
    def b64(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    header = b64({"alg": "HS256", "typ": "JWT"})
    payload = b64(
        {
            "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_ABC",
            "sub": sub,
            "email": email,
            "token_use": token_use,
            "exp": exp if exp is not None else int(time.time()) + 3600,
        }
    )
    return f"{header}.{payload}.sig"


class LeonardoState:
    def __init__(self) -> None:
        self.jwt = mint_jwt()
        #: "ok" | "dead" (get-session -> null) | "challenge" (403)
        self.auth_mode = "ok"
        self.balance = {"subscriptionTokens": 100, "paidTokens": 20, "rolloverTokens": 5}
        self.generate_requests: list[dict] = []
        self.generation_status: dict[str, str] = {}
        self.generation_images: dict[str, list[str]] = {}
        self.gql_requests: list[dict] = []
        #: new generations land COMPLETED with default images (False -> PENDING)
        self.auto_complete = True
        #: gen_id -> number of PENDING status polls before flipping to COMPLETED
        self.complete_after_polls: dict[str, int] = {}
        self._poll_counts: dict[str, int] = {}

    def add_generation(
        self, gen_id: str, status: str = "PENDING", images: list[str] | None = None
    ) -> None:
        self.generation_status[gen_id] = status
        self.generation_images[gen_id] = images or []

    def default_images(self, gen_id: str) -> list[str]:
        return [f"https://cdn.example/{gen_id}-0.png", f"https://cdn.example/{gen_id}-1.png"]


class _Handler(BaseHTTPRequestHandler):
    state: LeonardoState = None  # type: ignore[assignment]

    def log_message(self, format, *args):
        pass

    def _json(self, obj, code=200):
        body = b"null" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        st = self.state
        if self.path == "/api/auth/get-session":
            if st.auth_mode == "challenge":
                self._json({"error": "forbidden"}, 403)
                return
            cookie = self.headers.get("cookie", "")
            if VALID_COOKIE_VALUE in cookie:
                self._json(
                    {"session": {"accessToken": st.jwt, "user": {"email": "test@example.com"}}}
                )
            else:
                self._json(None)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        st = self.state
        if self.path == "/api/auth/session":
            self._json({"error": "not found"}, 404)
            return
        if self.path != "/v1/graphql":
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length).decode()) if length else {}
        st.gql_requests.append(payload)
        op = payload.get("operationName", "")
        if op == "GetUserDetails":
            self._json(
                {
                    "data": {
                        "users": [
                            {
                                "id": "u1",
                                "user_details": [{"auth0Email": "test@example.com", **st.balance}],
                            }
                        ]
                    }
                }
            )
        elif op == "GetTokenBalance":
            self._json(
                {"data": {"user_details": [{"auth0Email": "test@example.com", **st.balance}]}}
            )
        elif op == "Generate":
            request = payload.get("variables", {}).get("request", {})
            st.generate_requests.append(request)
            gen_id = f"gen-{len(st.generate_requests)}"
            if gen_id not in st.generation_status:
                if st.auto_complete:
                    st.add_generation(gen_id, status="COMPLETED", images=st.default_images(gen_id))
                else:
                    st.add_generation(gen_id, status="PENDING")
            self._json({"data": {"generate": {"generationId": gen_id, "apiCreditCost": 4}}})
        elif op == "GetAIGenerationFeedStatuses":
            where = payload.get("variables", {}).get("where", {})
            gen_id = where.get("id", {}).get("_eq", "")
            status = st.generation_status.get(gen_id, "PENDING")
            if status == "PENDING" and gen_id in st.complete_after_polls:
                count = st._poll_counts.get(gen_id, 0) + 1
                st._poll_counts[gen_id] = count
                if count >= st.complete_after_polls[gen_id]:
                    st.generation_status[gen_id] = "COMPLETED"
                    status = "COMPLETED"
            self._json({"data": {"generations": [{"id": gen_id, "status": status}]}})
        elif op == "GetAIGenerationFeed":
            where = payload.get("variables", {}).get("where", {})
            gen_id = where.get("id", {}).get("_eq", "")
            images = [
                {"url": url, "id": f"img-{i}"}
                for i, url in enumerate(st.generation_images.get(gen_id, []))
            ]
            self._json({"data": {"generations": [{"generated_images": images}]}})
        else:
            self._json({"errors": [{"message": f"unknown operation {op}"}]})


class MockLeonardo:
    """Threading HTTP server impersonating app.leonardo.ai + api.leonardo.ai."""

    def __init__(self) -> None:
        self.state = LeonardoState()
        handler = type("H", (_Handler,), {"state": self.state})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def auth_base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/api/auth"

    @property
    def graphql_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1/graphql"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
