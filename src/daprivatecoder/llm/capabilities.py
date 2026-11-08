"""What a model can do (vision, tool calls, limits) and what it costs, from NanoGPT's detailed
model list (GET /models?detailed=true, public: no key is sent). From DA Vibe Manager's
llm/capabilities.py.

Private Mode models (private/kimi-k3) are not in that list, so they are matched to their public
counterpart by name (TEE/kimi-k3, moonshotai/kimi-k3), the enclave build first.

OpenRouter's list (GET /models, public too) says the same in its own shape: read by parse_openrouter.
Another provider's list, if it has one, names its models and nothing more: what they can do isn't
known (a model then has images described for it, if a helper is set)."""

from __future__ import annotations

import re

import httpx

from ..netpolicy import check_url

# NanoGPT's published Private Mode markup on its TEE/ counterpart's prices (Sept 2026)
PRIVATE_MARKUP = 1.05


def norm_id(model_id: str) -> str:
    """A model id without its provider prefix, dots and underscores as dashes: NanoGPT names the
    same model glm-5.3 in one list and glm-5-3 in another. A :thinking suffix stays: that is
    another model."""
    return re.sub(r"[._]", "-", model_id.split("/")[-1].lower())


def parse(items: list[dict]) -> dict[str, dict]:
    out = {}
    for m in items:
        if not isinstance(m, dict) or not isinstance(m.get("id"), str):
            continue
        caps = m.get("capabilities") or {}
        out[m["id"]] = {
            "vision": bool(caps.get("vision")),
            "tools": bool(caps.get("tool_calling")),
            "context": m.get("context_length"),
            "max_output": m.get("max_output_tokens"),
            "pricing": _pricing(m.get("pricing")),
            # in a NanoGPT subscription, when NanoGPT routes it itself (llm/routes.py)
            "included": bool((m.get("subscription") or {}).get("included")),
            # the reasoning efforts it takes (["low", "high", "max"]); None: not said
            "efforts": _efforts(m.get("reasoning_efforts")),
        }
    return out


def parse_openrouter(items: list[dict]) -> dict[str, dict]:
    """OpenRouter's model list: prices per token, as text; what a model takes in its architecture."""
    out = {}
    for m in items:
        if not isinstance(m, dict) or not isinstance(m.get("id"), str):
            continue
        arch = m.get("architecture") or {}
        params = m.get("supported_parameters") or []
        out[m["id"]] = {
            "vision": "image" in (arch.get("input_modalities") or []),
            "tools": "tools" in params,
            "context": m.get("context_length"),
            "max_output": (m.get("top_provider") or {}).get("max_completion_tokens"),
            "pricing": _per_token_pricing(m.get("pricing")),
            "included": False,
            "efforts": _efforts((m.get("reasoning") or {}).get("supported_efforts")),
        }
    return out


def _efforts(listed) -> list[str] | None:
    return [e for e in listed if isinstance(e, str)] if isinstance(listed, list) and listed else None


def _per_token_pricing(p) -> dict | None:
    """OpenRouter's prices (USD a token, as text) per million tokens."""
    def num(v) -> float | None:
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v * 1_000_000 if v >= 0 else None
    if not isinstance(p, dict):
        return None
    inp, out = num(p.get("prompt")), num(p.get("completion"))
    if inp is None or out is None:
        return None
    read = num(p.get("input_cache_read"))
    return {"input": inp, "output": out, "cache_read": inp if read is None else read}


def _pricing(p) -> dict | None:
    """USD per million tokens: input, output and cache reads (NanoGPT lists the cache's per 1k)."""
    if not isinstance(p, dict) or str(p.get("currency", "USD")).upper() != "USD":
        return None

    def num(v) -> float | None:
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None
    inp, out = num(p.get("prompt")), num(p.get("completion"))
    if inp is None or out is None or p.get("unit", "per_million_tokens") != "per_million_tokens":
        return None
    read = num(p.get("cacheReadInputPer1kTokens"))
    return {"input": inp, "output": out, "cache_read": inp if read is None else read * 1000}


async def fetch_nanogpt(base_url: str, http: httpx.AsyncClient | None = None) -> dict[str, dict]:
    check_url(base_url, "NanoGPT's base URL")
    client = http or httpx.AsyncClient(timeout=30)
    try:
        r = await client.get(base_url.rstrip("/") + "/models", params={"detailed": "true"})
        r.raise_for_status()
        return parse(r.json().get("data", []))
    finally:
        if http is None:
            await client.aclose()


async def fetch(provider: str, base_url: str, http: httpx.AsyncClient | None = None) -> dict[str, dict] | None:
    """The capabilities `provider` lists ("nanogpt", "openrouter"); None for one that lists none."""
    if provider == "nanogpt":
        return await fetch_nanogpt(base_url, http)
    if provider != "openrouter":
        return None
    check_url(base_url, "OpenRouter's base URL")
    client = http or httpx.AsyncClient(timeout=30)
    try:
        r = await client.get(base_url.rstrip("/") + "/models")
        r.raise_for_status()
        return parse_openrouter(r.json().get("data", []))
    finally:
        if http is None:
            await client.aclose()


def lookup(caps: dict[str, dict], model_id: str) -> dict | None:
    """Capabilities of `model_id`; a private/ model borrows those of its public twin."""
    if model_id in caps:
        return caps[model_id]
    n = norm_id(model_id)
    twins = [mid for mid in caps if norm_id(mid) == n]
    twins.sort(key=lambda mid: (not mid.startswith("TEE/"), mid))     # the enclave build first
    return caps[twins[0]] if twins else None


def cost(caps: dict | None, usage: dict, private: bool) -> float | None:
    """What these tokens cost at the model's listed prices (None: its prices aren't known).
    `usage` is tokens by kind: input (not cached), cache_read, output."""
    price = (caps or {}).get("pricing")
    if not price:
        return None
    total = sum(usage.get(k, 0) * price[k] for k in ("input", "cache_read", "output")) / 1_000_000
    return total * (PRIVATE_MARKUP if private else 1.0)
