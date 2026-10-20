"""Leonardo GraphQL operations and result mapping.

User/balance queries ported from the hub's leonardo provider package
(source: hirotomasato/leoapi + leostudio, wire shapes verified 2026-10-20
where probed). Generation operations ported from hirotomasato/leostudio
``internal/leonardo/operations.go`` (public source) — NOT live-verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LeonardoUserInfo:
    email: str

    tokens: int


@dataclass(frozen=True)
class GenerateRequest:
    prompt: str
    model: str = "nano-banana-2"
    width: int = 1024
    height: int = 1024
    quantity: int = 1
    model_id: str = ""


@dataclass(frozen=True)
class GenerationSubmission:
    generation_id: str
    api_credit_cost: int


@dataclass(frozen=True)
class GenerationResult:
    success: bool
    images: list[str]
    error: str = ""


def user_details_query(user_sub: str) -> dict[str, Any]:
    return {
        "operationName": "GetUserDetails",
        "variables": {"userSub": user_sub},
        "query": (
            "query GetUserDetails($userSub: String) {\n"
            "  users(where: {user_details: {cognitoId: {_eq: $userSub}}}) {\n"
            "    id\n"
            "    user_details {\n"
            "      subscriptionTokens paidTokens rolloverTokens auth0Email __typename\n"
            "    }\n"
            "    __typename\n"
            "  }\n"
            "}"
        ),
    }


def token_balance_query() -> dict[str, Any]:
    return {
        "operationName": "GetTokenBalance",
        "variables": {},
        "query": (
            "query GetTokenBalance { user_details { subscriptionTokens "
            "paidTokens rolloverTokens auth0Email __typename } }"
        ),
    }


def generate_mutation(request: GenerateRequest) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "width": request.width,
        "height": request.height,
        "prompt": request.prompt.strip(),
        "quantity": request.quantity,
        "style_ids": ["111dc692-d470-4eec-b791-3475abac4c46"],
        "prompt_enhance": "ON",
        "dimensions": f"{request.width}x{request.height}",
        "negative_prompt": "",
        "guidance_scale": 7.0,
        "num_inference_steps": 30,
    }
    if request.model_id:
        parameters["modelId"] = request.model_id
    return {
        "operationName": "Generate",
        "variables": {
            "request": {
                "model": request.model,
                "parameters": parameters,
                "public": True,
            }
        },
        "query": (
            "mutation Generate($request: CreateGenerationRequest!) {\n"
            "  generate(request: $request) {\n"
            "    apiCreditCost generationId __typename\n"
            "  }\n"
            "}"
        ),
    }


def generation_status_query(generation_id: str) -> dict[str, Any]:
    return {
        "operationName": "GetAIGenerationFeedStatuses",
        "variables": {"where": {"id": {"_eq": generation_id}}},
        "query": (
            "query GetAIGenerationFeedStatuses($where: generations_bool_exp = {}) {\n"
            "  generations(where: $where) {\n"
            "    id status __typename\n"
            "  }\n"
            "}"
        ),
    }


def generation_feed_query(generation_id: str) -> dict[str, Any]:
    return {
        "operationName": "GetAIGenerationFeed",
        "variables": {"where": {"id": {"_eq": generation_id}}, "limit": 1},
        "query": (
            "query GetAIGenerationFeed($where: generations_bool_exp = {}, $limit: Int) {\n"
            "  generations(where: $where, limit: $limit) {\n"
            "    generated_images(order_by: [{url: desc}]) {\n"
            "      url id __typename\n"
            "    }\n"
            "    __typename\n"
            "  }\n"
            "}"
        ),
    }


def graphql_error_message(response_json: dict[str, Any]) -> str:
    errors = (response_json or {}).get("errors") or []
    messages: list[str] = []
    for err in errors:
        if isinstance(err, dict):
            message = str(err.get("message", "")).strip()
            if message:
                messages.append(message)
        elif err:
            messages.append(str(err))
    return " | ".join(messages)


def user_info_from_details(details: dict[str, Any], fallback_email: str) -> LeonardoUserInfo:
    email = str(details.get("auth0Email", "") or "").strip() or fallback_email

    def as_int(key: str) -> int:
        value = details.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
        return 0

    tokens = as_int("subscriptionTokens") + as_int("paidTokens") + as_int("rolloverTokens")
    return LeonardoUserInfo(email=email, tokens=tokens)


__all__ = [
    "GenerateRequest",
    "GenerationResult",
    "GenerationSubmission",
    "LeonardoUserInfo",
    "generate_mutation",
    "generation_feed_query",
    "generation_status_query",
    "graphql_error_message",
    "token_balance_query",
    "user_details_query",
    "user_info_from_details",
]
