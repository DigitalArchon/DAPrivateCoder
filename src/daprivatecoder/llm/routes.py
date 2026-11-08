"""Which of a provider's hosts runs a model: a route, chosen per model in the window.

NanoGPT and OpenRouter run an open model (GLM-5.3) on whichever of many upstream hosts they choose:
on a NanoGPT subscription, usually a cheap one, which may be slow. A route asks for another, as the
request's `provider` object (NanoGPT's Provider Selection, after OpenRouter's Provider Routing):

- a priority is a `sort`: "speed" (NanoGPT only: the fastest estimated completion), "latency"
  (the first word soonest), "throughput" (the most tokens a second), "price";
- a host is a preference (`order`), never a pin: a host that is down falls back to another, so
  Claude Code isn't stopped in the middle of a task;
- "FP8 or better" leaves out hosts that run the model at a lower precision (NanoGPT's
  `min_quantization`; OpenRouter's `quantizations`, the precisions allowed); a host whose precision
  isn't known is left out too. FP4 hosts measured less accurate in SealedLore, so it's on by default;
- `require_parameters`: only hosts that take everything the request asks for (its tools).

**A route on NanoGPT is billed pay-as-you-go, even for a model a subscription includes**, at the
host's price plus NanoGPT's markup: its own choice ("auto") is what a subscription covers. Never for
a private or TEE model, whose protection is the enclave it was attested on, nor Claude, which only
Anthropic runs.

From DA Vibe Manager's llm/routes.py (ported there from SealedLore), with OpenRouter added.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from ..netpolicy import check_url
from .client import is_claude, tier_of

PRIORITIES = {
    "nanogpt": ("auto", "speed", "latency", "throughput", "price", "host"),
    "openrouter": ("auto", "latency", "throughput", "price", "host"),
}
LABELS = {
    "auto": "The provider's own choice",
    "speed": "Fastest overall",
    "latency": "Fastest to start answering",
    "throughput": "Fastest writing",
    "price": "Cheapest",
    "host": "A host I choose",
}
AUTO = {"nanogpt": "NanoGPT's own choice (what a subscription covers)", "openrouter": "OpenRouter's own choice"}
# bits per weight, for the "FP8 or better" floor; an unknown precision doesn't meet it
BITS = {"int4": 4, "fp4": 4, "mxfp4": 4, "nvfp4": 4, "fp6": 6, "int8": 8, "fp8": 8, "mxfp8": 8, "fp16": 16,
        "bf16": 16, "fp32": 32}
FP8_OR_BETTER = [q for q, bits in BITS.items() if bits >= 8]
PRIVACY = {
    "zdr": "Keeps nothing",
    "no_training": "Keeps prompts, doesn't train on them",
    "logs_training": "Keeps prompts and trains on them",
}


def routable(provider: str, model: str) -> bool:
    """Whether a route can be asked for `model` with `provider` ("nanogpt", "openrouter", "custom"):
    a provider that takes routes, and a model sent as it is (not sealed, not attested, not Claude)."""
    model = (model or "").strip()
    return provider in PRIORITIES and bool(model) and not is_claude(model) \
        and tier_of(model, provider == "nanogpt") == "standard"


def clean(provider: str, data: Any) -> dict | None:
    """A route from the window or the settings file, checked; None for the provider's own choice."""
    if data is None:
        return None
    if not isinstance(data, Mapping):
        raise ValueError("A route is a table: priority, and host for a host of your choice.")
    priority = str(data.get("priority") or "auto")
    allowed = PRIORITIES.get(provider, ("auto",))
    if priority not in allowed:
        raise ValueError(f"A route here is one of: {', '.join(allowed)} (not {priority!r}).")
    if priority == "auto":
        return None
    fp8 = data.get("fp8", True)
    if not isinstance(fp8, bool):
        raise ValueError("A route's fp8 is true or false.")
    out: dict[str, Any] = {"priority": priority, "fp8": fp8}
    if priority == "host":
        host = str(data.get("host") or "").strip()
        if not host or len(host) > 100 or not all(c.isalnum() or c in "-_./" for c in host):
            raise ValueError("Choose the host to use.")
        out["host"] = host
        out["host_name"] = str(data.get("host_name") or host)[:100]
    return out


def body(provider: str, route: Mapping | None) -> dict[str, Any]:
    """The request field for `route`: {} for the provider's own choice."""
    if not route or route.get("priority") in (None, "auto") or provider not in PRIORITIES:
        return {}
    out: dict[str, Any] = {"require_parameters": True}
    if route["priority"] == "host":
        out["order"] = [route["host"]]
        if provider == "openrouter":
            out["allow_fallbacks"] = True
    else:
        out["sort"] = route["priority"]
    if route.get("fp8", True):
        if provider == "nanogpt":
            out["min_quantization"] = "fp8"
        else:
            out["quantizations"] = FP8_OR_BETTER
    return {"provider": out}


def describe(provider: str, route: Mapping | None) -> str:
    """"Fastest overall · FP8 or better", for the window and the request log."""
    if not route or route.get("priority") in (None, "auto"):
        return AUTO.get(provider, LABELS["auto"])
    what = route.get("host_name") or route.get("host") if route["priority"] == "host" else LABELS[route["priority"]]
    return f"{what} · {'FP8 or better' if route.get('fp8', True) else 'any precision'}"


# ---------------------------------------------------------------- the hosts a provider lists for a model

def hosts_url(provider: str, base_url: str, model: str) -> str:
    """NanoGPT: `{origin}/api/models/{id}/providers` (outside /api/v1, the model's slash encoded).
    OpenRouter: `{base}/models/{id}/endpoints`."""
    if provider == "openrouter":
        return f"{base_url.rstrip('/')}/models/{quote(model, safe='/')}/endpoints"
    p = urlparse(base_url.strip())
    return f"{p.scheme}://{p.netloc}/api/models/{quote(model, safe='')}/providers"


def _num(v: Any) -> float | None:
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            return None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _text(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _per_million(pricing: Any, key: str, per: float) -> float | None:
    v = _num(pricing.get(key)) if isinstance(pricing, Mapping) else None
    return round(v * per, 4) if v is not None else None


def parse_hosts(model: str, data: Mapping[str, Any]) -> dict:
    """NanoGPT's listing, as the window shows it: each host's precision, privacy, region, price
    (USD per million tokens), whether it caches, and NanoGPT's own measure of its speed; and the
    same measure for NanoGPT's own routing."""
    hosts = []
    for e in data.get("providers") or []:
        if not isinstance(e, Mapping) or not _text(e.get("provider")):
            continue
        quant = (_text(e.get("quantization")) or "").lower() or None
        privacy = (e.get("privacy") or {}).get("classification") if isinstance(e.get("privacy"), Mapping) else None
        region = e.get("region") if isinstance(e.get("region"), Mapping) else {}
        price = (e.get("effectivePricing") or {}).get("effective_price") if isinstance(e.get("effectivePricing"), Mapping) \
            else None
        price = price if isinstance(price, Mapping) else e.get("pricing")
        hosts.append({
            "id": e["provider"], "name": str(e.get("displayName") or e["provider"])[:100],
            "available": e.get("available") is not False,
            "precision": quant, "fp8": BITS.get(quant or "", 0) >= 8,
            "privacy": PRIVACY.get(privacy or "", "Not known"), "keeps_nothing": privacy == "zdr",
            "region": _text(region.get("label")),
            "tokens_per_second": _num(e.get("tps")), "first_token_ms": _num(e.get("ttftMs")),
            "input": _per_million(price, "inputPer1kTokens", 1000), "output": _per_million(price, "outputPer1kTokens", 1000),
            "caches": e.get("supportsPromptCaching") is True,
        })
    return {
        "model": model,
        "supported": data.get("supportsProviderSelection") is True,
        "auto": {"tokens_per_second": _num(data.get("autoTps")), "first_token_ms": _num(data.get("autoTtftMs")),
                 "precision": _text(data.get("autoQuantization"))},
        "hosts": hosts,
    }


def parse_openrouter_hosts(model: str, data: Mapping[str, Any]) -> dict:
    """OpenRouter's endpoints for a model, in the same shape: `tag` is what `order` takes."""
    hosts = []
    listing = data.get("data") if isinstance(data.get("data"), Mapping) else {}
    for e in listing.get("endpoints") or []:
        if not isinstance(e, Mapping) or not _text(e.get("tag")):
            continue
        quant = (_text(e.get("quantization")) or "").lower() or None
        hosts.append({
            "id": e["tag"], "name": f"{e.get('provider_name') or e['tag']} ({e['tag']})"[:100],
            "available": e.get("status", 0) == 0,
            "precision": quant if quant != "unknown" else None, "fp8": BITS.get(quant or "", 0) >= 8,
            "privacy": "Not known", "keeps_nothing": False, "region": None,
            "tokens_per_second": _num(e.get("throughput_last_30m")), "first_token_ms": _num(e.get("latency_last_30m")),
            "input": _per_million(e.get("pricing"), "prompt", 1e6), "output": _per_million(e.get("pricing"), "completion", 1e6),
            "caches": e.get("supports_implicit_caching") is True,
        })
    return {"model": model, "supported": bool(hosts), "auto": {}, "hosts": hosts}


async def fetch_hosts(provider: str, base_url: str, model: str, http: httpx.AsyncClient | None = None) -> dict:
    """The provider's public listing: it needs no key, so none is sent, nor anything but the model's name."""
    url = check_url(hosts_url(provider, base_url, model), "The provider's list of hosts")
    client = http or httpx.AsyncClient(timeout=30)
    try:
        r = await client.get(url)          # never a redirect followed: httpx's default
        r.raise_for_status()
        data = r.json()
    finally:
        if http is None:
            await client.aclose()
    if not isinstance(data, Mapping):
        raise ValueError(f"The list of hosts for {model} wasn't what it should be.")
    return parse_openrouter_hosts(model, data) if provider == "openrouter" else parse_hosts(model, data)
