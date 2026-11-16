"""The providers a profile's models come from, in one table (PROVIDERS): where each one is, the API Claude
Code's requests go to it in, how its key is sent and to which addresses alone, its keyring entry, and the kinds
of profile it can serve. The settings (config.py), the keyring (creds.py), the proxy, the window (manager.py,
and web/app.js's PROVIDERS, held to this table by tests/test_providers.py) and the commands read it, so a
provider is added here, once.

A provider speaks one of two APIs to the proxy:
- "openai": Claude Code's requests are translated to chat completions (llm/anthropic_bridge.py), and a Claude
  model's goes as Claude Code wrote it to the provider's Anthropic endpoint, where it has one (`claude`: at its own
  address, or at another of its own, as Chutes' claude.chutes.ai);
- "anthropic": every request goes on as Claude Code wrote it, never translated (Anthropic's own API, or a
  provider of your own that copies it).

Its key goes to the provider's own addresses only: one with a fixed address (OpenRouter, Anthropic, Tinfoil) to
that address's origin (`key_origins`) and nowhere else, whatever is stored beside it, and Chutes' to Chutes' three
hosts (llm/privacy.py CHUTES_KEY_ORIGINS, the one stated exception: its end-to-end encryption is api.chutes.ai's);
NanoGPT's to the origin it was stored for (the settings' base_url, which a hand edit could point anywhere:
creds.py); a provider of your own's to its own server, by host and port (`account`).

How private a model is follows from the address its requests go to, never from the name of the provider chosen
(`privacy_of`, llm/privacy.py privacy_at): every model at Tinfoil's address is sealed to Tinfoil's attested router
(highest privacy), a -TEE model at one of Chutes' to an attested Chutes instance (partial), whichever provider the
profile names, a provider of your own typed with that address included. When in doubt, sealed."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from .llm import privacy
from .llm.privacy import CHUTES_BASE_URL, CHUTES_KEY_ORIGINS, TINFOIL_BASE_URL, TINFOIL_ORIGIN

NANOGPT_BASE_URL = "https://nano-gpt.com/api/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Anthropic's Messages API: Claude Code appends /v1/messages, as it does to its own ANTHROPIC_BASE_URL
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
# the Messages API's version, where the window lists a provider's models (Claude Code sends its own with each request)
ANTHROPIC_VERSION = "2023-06-01"

APIS = ("openai", "anthropic")
AUTHS = ("bearer", "x-api-key")
# `claude` for a provider whose Anthropic endpoint is at its own address (NanoGPT's, OpenRouter's); another
# provider's `claude` is that endpoint's address
HERE = "here"
# the keyring entry of a provider of your own's key: its server's, by host and port (creds.account)
OWN = "custom"


@dataclass(frozen=True)
class Provider:
    id: str                         # as the settings file and the window name it
    name: str                       # in a sentence ("No {name} API key is stored")
    label: str                      # in the window's Provider list
    api: str = "openai"             # APIS: how Claude Code's requests go to it
    auth: str = "bearer"            # AUTHS: Authorization: Bearer <key>, or Anthropic's x-api-key: <key>
    base_url: str = ""              # its own fixed address; "" for NanoGPT (the settings' base_url) and one of your own
    own_address: bool = False       # a provider of your own: the profile says its address (its base_url)
    key_optional: bool = False      # a server of your own may need no key: it says if it does
    kinds: tuple[str, ...] = ("standard",)      # the kinds of profile whose models it serves (config.KINDS)
    claude: str = ""                # where a Claude model goes as Claude Code wrote it, for an "openai" one: HERE, an
                                    # address of the provider's own, or "" (none)
    key_hint: str = ""              # where its key is found, after "paste the key … shows you"
    # the origins alone its key may go to (creds.origin's form): a provider whose address is fixed. Empty: the
    # origin the key was stored for (creds.py)
    key_origins: frozenset[str] = field(default_factory=frozenset)
    # a profile's models when it is switched to this provider, by the profile's kind: (model, opus, quick, images)
    defaults: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def account(self) -> str:
        """Its key's keyring entry: its id, or OWN for a provider of your own (with its host and port: creds.account)."""
        return OWN if self.own_address else self.id

    def address(self, settings_url: str = "", profile_url: str = "") -> str:
        """Where its models are: its own fixed address, the profile's (one of your own), else NanoGPT's base_url."""
        return self.base_url or (profile_url if self.own_address else settings_url)

    def anthropic_url(self, provider_url: str) -> str:
        """Where a request goes as Claude Code wrote it (`provider_url`: where its models are): the provider itself for
        one that speaks Anthropic's API or has its Anthropic endpoint there, else its `claude` address; "" for none."""
        if self.api == "anthropic" or self.claude == HERE:
            return provider_url
        return self.claude


def _origin(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.hostname}:{u.port or 443}"


PROVIDERS: dict[str, Provider] = {p.id: p for p in (
    Provider("nanogpt", "NanoGPT", "NanoGPT", kinds=("private", "tee", "standard"), claude=HERE,
             key_hint="nano-gpt.com/api"),
    Provider("chutes", "Chutes", "Chutes", base_url=CHUTES_BASE_URL, kinds=("tee", "standard"),
             key_hint="chutes.ai, API keys", key_origins=CHUTES_KEY_ORIGINS,
             defaults={"tee": ("zai-org/GLM-5.2-TEE", "moonshotai/Kimi-K3-TEE", "deepseek-ai/DeepSeek-V4-Flash-0731-TEE", ""),
                       "standard": ("zai-org/GLM-5.3", "deepseek-ai/DeepSeek-V4-Pro-0813",
                                    "deepseek-ai/DeepSeek-V4.1-Flash-1017", "")}),
    Provider("tinfoil", "Tinfoil", "Tinfoil", base_url=TINFOIL_BASE_URL, kinds=("private",),
             key_hint="tinfoil.sh, API keys", key_origins=frozenset({TINFOIL_ORIGIN}),
             defaults={"private": ("glm-5-3", "kimi-k3-1-pro", "deepseek-v4-1-flash", "deepseek-v4-1-flash")}),
    Provider("openrouter", "OpenRouter", "OpenRouter", base_url=OPENROUTER_BASE_URL, claude=HERE,
             key_hint="openrouter.ai/settings/keys", key_origins=frozenset({_origin(OPENROUTER_BASE_URL)})),
    Provider("anthropic", "Anthropic", "Anthropic", api="anthropic", auth="x-api-key", base_url=ANTHROPIC_BASE_URL,
             key_hint="console.anthropic.com, Settings, API keys",
             key_origins=frozenset({_origin(ANTHROPIC_BASE_URL)})),
    Provider("custom", "your provider", "Another provider (OpenAI-compatible)", own_address=True, key_optional=True),
    Provider("custom-anthropic", "your provider", "Another provider (Anthropic-compatible)", api="anthropic",
             auth="x-api-key", own_address=True, key_optional=True),
)}


def get(provider: str) -> Provider:
    """The provider called `provider`; KeyError for none."""
    return PROVIDERS[provider]


def for_kind(kind: str) -> list[str]:
    """The providers a profile of `kind` can take, in the table's order."""
    return [p.id for p in PROVIDERS.values() if kind in p.kinds]


def name(provider: str) -> str:
    """Whose it is, in a sentence: "NanoGPT", "your provider"; the id itself for one not in the table."""
    p = PROVIDERS.get(provider)
    return p.name if p else provider


def auth_headers(provider: str, key: str | None, url: str = "") -> dict[str, str]:
    """The header that carries `key` to `provider` at `url` ({} for none): Chutes' proxy of Anthropic's API
    (claude.chutes.ai) takes it only as a bearer token, whatever the provider (llm/privacy.py anthropic_bearer)."""
    if not key:
        return {}
    if PROVIDERS[provider].auth == "x-api-key" and not (url and privacy.anthropic_bearer(url)):
        return {"x-api-key": key}
    return {"authorization": f"Bearer {key}"}


# the provider whose service an address is, whichever provider names it: Tinfoil's host, Chutes' hosts
_SERVICES = {privacy.TINFOIL: "tinfoil", privacy.CHUTES: "chutes"}


def service_at(address: str) -> str:
    """The provider whose service `address` is ("tinfoil", "chutes"), recognised by its host however it came into
    the settings (chosen, or typed as a provider of your own's address); "" for any other."""
    if privacy.sealed_service(address):
        return "tinfoil"
    if privacy.on_chutes(address):
        return "chutes"
    return ""


def kinds_at(provider: str, address: str) -> tuple[str, ...]:
    """The kinds of profile `provider` at `address` serves: its own, or, for one of your own at Tinfoil's or
    Chutes' address, that service's."""
    spec = PROVIDERS[provider]
    found = service_at(address) if spec.own_address else ""
    return tuple(dict.fromkeys(spec.kinds + (PROVIDERS[found].kinds if found else ())))


def privacy_of(provider: str, address: str, model: str) -> privacy.Privacy:
    """How private `model` is through `provider` at `address` (llm/privacy.py privacy_at), as the proxy sends it:
    every model at Tinfoil's address sealed to Tinfoil, a -TEE one at Chutes' to a Chutes instance, a private/ one
    on any provider through NanoGPT's Private Mode; a TEE/ one attested only on NanoGPT, whose enclaves this app
    checks (elsewhere a model so named is sent as any other)."""
    found = privacy.privacy_at(model, address)
    if found.service == privacy.NANOGPT_TEE and provider != "nanogpt":
        return privacy.Privacy(plain_on=privacy.CHUTES if privacy.on_chutes(address) else "")
    return found


# the kind of profile (and the proxy's tier) each privacy level is: sealed with the highest privacy, partial, none
TIER_OF_LEVEL = {privacy.HIGHEST: "private", privacy.PARTIAL: "tee", privacy.NOT_PRIVATE: "standard"}
# what a session of each kind is kept to, as the move rule says it (llm/privacy.py move_refusal)
KIND_PRIVACY = {"private": privacy.Privacy("sealed", privacy.NANOGPT_PRIVATE),
                "tee": privacy.Privacy("tee", privacy.NANOGPT_TEE), "standard": privacy.NONE}


def tier_of(provider: str, address: str, model: str) -> str:
    """How a request for `model` travels through `provider` at `address`: "private" (highest privacy, sealed),
    "tee" (partial: NanoGPT's attested TEE/ models, Chutes' sealed -TEE ones) or "standard" (in the clear)."""
    return TIER_OF_LEVEL[privacy_of(provider, address, model).level]
