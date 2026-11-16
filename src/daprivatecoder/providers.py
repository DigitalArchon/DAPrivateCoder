"""The providers a profile's models come from, in one table (PROVIDERS): where each one is, the API Claude
Code's requests go to it in, how its key is sent and to which addresses alone, its keyring entry, and the kinds
of profile it can serve. The settings (config.py), the keyring (creds.py), the proxy, the window (manager.py,
and web/app.js's PROVIDERS, held to this table by tests/test_providers.py) and the commands read it, so a
provider is added here, once.

A provider speaks one of two APIs to the proxy:
- "openai": Claude Code's requests are translated to chat completions (llm/anthropic_bridge.py), and a Claude
  model's goes as Claude Code wrote it to the provider's Anthropic endpoint, where it has one (`claude`);
- "anthropic": every request goes on as Claude Code wrote it, never translated (Anthropic's own API, or a
  provider of your own that copies it).

Its key goes to the provider's own addresses only: one with a fixed address (OpenRouter, Anthropic) to that
address's origin (`key_origins`) and nowhere else, whatever is stored beside it; NanoGPT's to the origin it was
stored for (the settings' base_url, which a hand edit could point anywhere: creds.py); a provider of your own's
to its own server, by host and port (`account`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

NANOGPT_BASE_URL = "https://nano-gpt.com/api/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Anthropic's Messages API: Claude Code appends /v1/messages, as it does to its own ANTHROPIC_BASE_URL
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
# the Messages API's version, where the window lists a provider's models (Claude Code sends its own with each request)
ANTHROPIC_VERSION = "2023-06-01"

APIS = ("openai", "anthropic")
AUTHS = ("bearer", "x-api-key")
# `claude` for a provider whose Anthropic endpoint is at its own address (NanoGPT's, OpenRouter's)
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
    claude: str = ""                # where a Claude model goes as Claude Code wrote it, for an "openai" one: HERE, or none
    key_hint: str = ""              # where its key is found, after "paste the key … shows you"
    # the origins alone its key may go to (creds.origin's form): a provider whose address is fixed. Empty: the
    # origin the key was stored for (creds.py)
    key_origins: frozenset[str] = field(default_factory=frozenset)

    @property
    def account(self) -> str:
        """Its key's keyring entry: its id, or OWN for a provider of your own (with its host and port: creds.account)."""
        return OWN if self.own_address else self.id

    def address(self, settings_url: str = "", profile_url: str = "") -> str:
        """Where its models are: its own fixed address, the profile's (one of your own), else NanoGPT's base_url."""
        return self.base_url or (profile_url if self.own_address else settings_url)


def _origin(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.hostname}:{u.port or 443}"


PROVIDERS: dict[str, Provider] = {p.id: p for p in (
    Provider("nanogpt", "NanoGPT", "NanoGPT", kinds=("private", "tee", "standard"), claude=HERE,
             key_hint="nano-gpt.com/api"),
    Provider("openrouter", "OpenRouter", "OpenRouter", base_url=OPENROUTER_BASE_URL, claude=HERE,
             key_hint="openrouter.ai/settings/keys", key_origins=frozenset({_origin(OPENROUTER_BASE_URL)})),
    Provider("custom", "your provider", "Another provider (OpenAI-compatible)", own_address=True, key_optional=True),
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


def auth_headers(provider: str, key: str | None) -> dict[str, str]:
    """The header that carries `key` to `provider` ({} for none)."""
    if not key:
        return {}
    if PROVIDERS[provider].auth == "x-api-key":
        return {"x-api-key": key}
    return {"authorization": f"Bearer {key}"}
