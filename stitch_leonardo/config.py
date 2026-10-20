"""Leonardo AI plugin configuration (URLs, sentry constants, cookie names)."""

APP_URL = "https://app.leonardo.ai"
AUTH_BASE_URL = f"{APP_URL}/api/auth"
GRAPHQL_URL = "https://api.leonardo.ai/v1/graphql"

SENTRY_RELEASE = "6a0bd1b5b7ef23a4f22608a2ed90c5e753cbc669"
SENTRY_PUBLIC_KEY = "a851bd902378477eae99cf74c62e142a"
SENTRY_ORG_ID = "4504767521292288"

DEFAULT_IMPERSONATE = "chrome131"

SESSION_COOKIE_NAMES = (
    "__Secure-next-auth.session-token",
    "next-auth.session-token",
    "__Secure-authjs.session-token",
    "authjs.session-token",
)

CSRF_COOKIE_NAMES = (
    "__Host-next-auth.csrf-token",
    "__Secure-next-auth.csrf-token",
    "next-auth.csrf-token",
    "__Host-authjs.csrf-token",
    "__Secure-authjs.csrf-token",
    "authjs.csrf-token",
)

DEFAULT_MODEL = "nano-banana-2"
KNOWN_MODELS = ("nano-banana-2",)
