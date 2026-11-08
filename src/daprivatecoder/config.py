"""DA Private Coder's settings: ~/.config/daprivatecoder/config.toml, written with the defaults (and
what each one means) on first use. It never holds secrets: the NanoGPT key lives in the keyring
(creds.py). The window saves them by writing the whole file again, with the same notes (render).

A profile is a way to start Claude Code: the models it uses, and how their requests travel (its
kind). A private profile takes NanoGPT Private Mode models only (private/…), so every request is
sealed to an attested enclave; a TEE profile NanoGPT's TEE/ models (or private ones), each attested
before anything is sent, but read by NanoGPT's gateway; a standard profile any model of its provider
(NanoGPT, OpenRouter, or another OpenAI-compatible one), in the clear. A model below a profile's
kind is a mistake in the file, and nothing starts with it."""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .llm import routes as routes_mod
from .llm.client import RANK, is_private_mode, private_id, tier_of
from .netpolicy import InsecureURL, check_url

APP_ID = "daprivatecoder"
NANOGPT_BASE_URL = "https://nano-gpt.com/api/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# how a profile's requests travel (llm/client.py's tiers): sealed, attested TEE, or in the clear
KINDS = ("private", "tee", "standard")
# where a standard profile's models are: private and TEE profiles are NanoGPT's
PROVIDERS = {"nanogpt": "NanoGPT", "openrouter": "OpenRouter", "custom": "another provider"}
# what the private models accept (Kimi K3 and GLM-5.3 take low, high and max; DeepSeek V4.1 Flash
# all of these but xhigh); never "none": GLM with its thinking off reasons all the more, in its answer
REASONING = ("low", "medium", "high", "max")
# web search (search.py): Linkup, which keeps nothing; Perplexity and Kagi; or none
WEB_SEARCH = ("zero-retention", "best", "off")
PROFILE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


class ConfigError(Exception):
    """The settings file can't be used as it is; the message says what to change."""


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / APP_ID


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / APP_ID


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass(frozen=True)
class Profile:
    name: str
    kind: str                       # private, tee or standard (KINDS)
    model: str
    opus_model: str
    fast_model: str
    vision_model: str = ""
    reasoning: str = "low"
    web_search: str = "off"
    provider: str = "nanogpt"       # a standard profile's: nanogpt, openrouter or custom (PROVIDERS)
    base_url: str = ""              # custom: its OpenAI-compatible endpoint
    # which of the provider's hosts runs a model (llm/routes.py): model -> route; none for its own choice
    routes: dict = field(default_factory=dict, hash=False)

    @property
    def private(self) -> bool:
        return self.kind == "private"

    @property
    def models(self) -> tuple[str, ...]:
        """The profile's models, each once: the default first, the quick one last."""
        out: list[str] = []
        for m in (self.model, self.opus_model, self.fast_model):
            if m and m not in out:
                out.append(m)
        return tuple(out)


@dataclass(frozen=True)
class Config:
    base_url: str = NANOGPT_BASE_URL
    claude: str = ""
    profiles: dict[str, Profile] = field(default_factory=dict)
    path: Path | None = None

    def profile(self, name: str) -> Profile:
        try:
            return self.profiles[name]
        except KeyError:
            known = ", ".join(sorted(self.profiles)) or "none"
            where = f" in {self.path}" if self.path else ""
            raise ConfigError(f"There's no profile called {name!r}{where} (there are: {known}).") from None

    def with_profile(self, profile: Profile) -> Config:
        return replace(self, profiles={**self.profiles, profile.name: profile})

    def provider_url(self, profile: Profile) -> str:
        """Where `profile`'s models are: NanoGPT's base_url, OpenRouter's, or its own."""
        if profile.provider == "openrouter":
            return OPENROUTER_BASE_URL
        if profile.provider == "custom":
            return profile.base_url
        return self.base_url


DEFAULTS = Config(profiles={
    "private": Profile("private", "private", "private/glm-5-3", "private/kimi-k3", "private/deepseek-v4-1-flash",
                       "private/deepseek-v4-1-flash", "low", "zero-retention"),
    "tee": Profile("tee", "tee", "TEE/glm-5.3", "TEE/kimi-k3", "TEE/glm-5.3-flash", "TEE/glm-5.3-flash", "low",
                   "zero-retention"),
    "standard": Profile("standard", "standard", "z-ai/glm-5.3", "moonshotai/kimi-k3", "z-ai/glm-5.3-flash",
                        "z-ai/glm-5.3-flash", "low", "best"),
})

# ---------------------------------------------------------------------- the file, written

_HEADER = """\
# DA Private Coder settings: its window's Settings, or this file. The next Claude Code session it
# starts uses what is saved here. Saved from the window, the file is written again with these notes
# (notes of your own aren't kept). The API keys aren't here but in your keyring.
"""
_KIND_INTRO = {
    "private": ("NanoGPT Private Mode models only (private/…). Each request is sealed on this computer to a\n"
                "# Tinfoil enclave whose attestation was checked first; NanoGPT relays ciphertext."),
    "tee": ("NanoGPT's TEE models (TEE/…), or private ones. Each runs in an enclave this app attests before it\n"
            "# sends it anything, but they're NOT end-to-end encrypted: NanoGPT's gateway reads every prompt."),
    "standard": ("Any model of its provider, NOT private: requests go in the clear to the provider and the\n"
                 "# model's host. A private/ model picked with /model is still sealed, and a TEE/ one attested."),
}
_NOTES = {
    "model": "Claude Code's default model, and what /model sonnet picks",
    "opus_model": "what /model opus picks",
    "fast_model": "what /model haiku picks, and what Claude Code uses for quick background tasks",
    "vision_model": "describes images for a model that can't see them; empty: such a model is told it can't see",
    "reasoning": ("how hard the model reasons before it answers, as each session starts: low, medium, high or max.\n"
                  "# In a session, Claude Code's Effort (the editor's, or /effort) changes it"),
    "provider": ('"nanogpt", "openrouter", or "custom" (any OpenAI-compatible endpoint: base_url, https unless it\n'
                 "# is on this computer or your network). Its API key is in your keyring, set in the window"),
    "web_search": """\
Web search, through NanoGPT. No search is end-to-end encrypted: NanoGPT and the search provider
# read every query (Claude Code asks you before each one, and refuses one naming this computer).
#   "zero-retention"  Linkup only, which keeps nothing (zero data retention). Finds less.
#   "best"            Perplexity to learn things, Kagi to find sites: better results, but not zero
#                     data retention: what they keep is up to their own policies.
#   "off"             no web search""",
}


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)        # a TOML basic string


def _inline(route: dict) -> str:
    """A route as a TOML inline table."""
    parts = [f"{k} = {_q(v) if isinstance(v, str) else ('true' if v else 'false')}" for k, v in route.items()]
    return "{ " + ", ".join(parts) + " }"


def render(cfg: Config) -> str:
    """The settings file for `cfg`, with what each setting means."""
    out = [_HEADER,
           "# NanoGPT's API (https only)", f"base_url = {_q(cfg.base_url)}", "",
           "# The Claude Code program to start. Empty: the one on your PATH, else the newest one bundled with",
           "# the Claude Code extension of VSCodium or VS Code.", f"claude = {_q(cfg.claude)}"]
    noted: set[str] = set()                     # each setting's note, the first time it's written

    def note(key: str) -> list[str]:
        if key in noted:
            return []
        noted.add(key)
        return [f"# {_NOTES[key]}"]

    for p in cfg.profiles.values():
        start = "claude-private" if p.name == "private" else f"daprivatecoder claude --profile {p.name}"
        out += ["", f"# {start}", f"# {_KIND_INTRO[p.kind]}", f"[profiles.{p.name}]", f"kind = {_q(p.kind)}"]
        if p.kind == "standard":
            out += [*note("provider"), f"provider = {_q(p.provider)}"]
            if p.provider == "custom":
                out.append(f"base_url = {_q(p.base_url)}")
        for key in ("model", "opus_model", "fast_model", "vision_model", "reasoning", "web_search"):
            out += [*note(key), f"{key} = {_q(getattr(p, key))}"]
        if p.routes:
            out += ["# which of the provider's hosts runs a model (the window's Route): auto is the provider's own choice",
                    f"[profiles.{p.name}.routes]"]
            out += [f"{_q(model)} = {_inline(route)}" for model, route in p.routes.items()]
    return "\n".join(out) + "\n"


DEFAULT_TEXT = render(DEFAULTS)

# ---------------------------------------------------------------------- the file, read


def _text(table: dict, key: str, where: str, default: str = "") -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{where}: {key} must be text in quotes.")
    return value.strip()


def _profile(name: str, table: dict, where: str) -> Profile:
    if not PROFILE_NAME.match(name):
        raise ConfigError(f"{where}: a profile's name is lower-case letters, digits and dashes ({name!r} isn't).")
    if not isinstance(table, dict):
        raise ConfigError(f"{where}: [profiles.{name}] must be a table of settings.")
    where = f"{where} [profiles.{name}]"
    if "kind" in table:
        kind = _text(table, "kind", where).lower()
    else:
        private = table.get("private", False)        # how the first settings files said it
        if not isinstance(private, bool):
            raise ConfigError(f"{where}: private must be true or false.")
        kind = "private" if private else "standard"
    if kind not in KINDS:
        raise ConfigError(f"{where}: kind must be one of {', '.join(KINDS)} (not {kind!r}).")
    provider = _text(table, "provider", where, "nanogpt").lower()
    if provider not in PROVIDERS:
        raise ConfigError(f"{where}: provider must be one of {', '.join(PROVIDERS)} (not {provider!r}).")
    if kind != "standard" and provider != "nanogpt":
        raise ConfigError(f"{where}: a {kind} profile's models are NanoGPT's, so its provider is nanogpt.")
    base_url = _text(table, "base_url", where).rstrip("/") if provider == "custom" else ""
    if provider == "custom":
        if not base_url:
            raise ConfigError(f"{where}: base_url is missing: the OpenAI-compatible endpoint of your provider "
                              "(as https://api.example.com/v1).")
        try:
            check_url(base_url, f"{where}: base_url")
        except InsecureURL as e:
            raise ConfigError(str(e)) from None
    model = _text(table, "model", where)
    if not model:
        raise ConfigError(f"{where}: model is missing.")
    opus = _text(table, "opus_model", where) or model
    fast = _text(table, "fast_model", where) or model
    vision = _text(table, "vision_model", where)
    reasoning = _text(table, "reasoning", where, "low").lower()
    if reasoning not in REASONING:
        raise ConfigError(f"{where}: reasoning must be one of {', '.join(REASONING)} (not {reasoning!r}).")
    # a profile searches with Linkup unless told otherwise (a standard one, with the best results): of the
    # searches, Linkup alone keeps nothing
    web_search = _text(table, "web_search", where, "best" if kind == "standard" else "zero-retention").lower()
    if web_search not in WEB_SEARCH:
        raise ConfigError(f"{where}: web_search must be one of {', '.join(WEB_SEARCH)} (not {web_search!r}).")
    nanogpt = provider == "nanogpt"
    for key, value in (("model", model), ("opus_model", opus), ("fast_model", fast), ("vision_model", vision)):
        # below the profile's kind, a model would get what was said with less protection: refused here,
        # before anything starts
        if value and RANK[tier_of(value, nanogpt)] < RANK[kind]:
            if kind == "private":
                raise ConfigError(f"{where}: {key} is {value!r}, which isn't a NanoGPT Private Mode model (private/…). "
                                  "A private profile only uses those, so nothing would be sent in the clear.")
            raise ConfigError(f"{where}: {key} is {value!r}, which isn't one of NanoGPT's TEE models (TEE/…). A TEE "
                              "profile only uses those (or private ones), so nothing goes to a host unattested.")
    if kind == "private":
        model, opus, fast, vision = (private_id(m) if m else "" for m in (model, opus, fast, vision))
    routes = table.get("routes", {})
    if not isinstance(routes, dict):
        raise ConfigError(f"{where}: routes must be a table: [profiles.{name}.routes].")
    checked = {}
    for routed, route in routes.items():
        if not routes_mod.routable(provider, routed):
            continue                    # a route for a model it can't apply to (private, TEE, Claude) is no route
        try:
            route = routes_mod.clean(provider, route)
        except ValueError as e:
            raise ConfigError(f"{where}: the route for {routed}: {e}") from None
        if route:
            checked[routed] = route
    return Profile(name, kind, model, opus, fast, vision, reasoning, web_search, provider, base_url, checked)


def parse(text: str, path: Path | None = None) -> Config:
    where = str(path) if path else "The settings"
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{where} isn't valid TOML: {e}") from None
    base_url = _text(data, "base_url", where, NANOGPT_BASE_URL) or NANOGPT_BASE_URL
    try:
        check_url(base_url, f"{where}: base_url")
    except InsecureURL as e:
        raise ConfigError(str(e)) from None
    claude = os.path.expanduser(_text(data, "claude", where))
    profiles_table = data.get("profiles", {})
    if not isinstance(profiles_table, dict):
        raise ConfigError(f"{where}: profiles must be tables, as [profiles.private].")
    profiles = {name: _profile(name, table, where) for name, table in profiles_table.items()}
    # the window's three kinds of session are always there: one left out of the file is its default
    profiles.update({name: p for name, p in DEFAULTS.profiles.items() if name not in profiles})
    return Config(base_url=base_url.rstrip("/"), claude=claude, profiles=profiles, path=path)


def load(path: Path | None = None) -> Config:
    """The settings, from `path` (default: config_path()), written with the defaults if there are none."""
    path = path or config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_TEXT)
    try:
        text = path.read_text()
    except OSError as e:
        raise ConfigError(f"{path} couldn't be read: {e}") from None
    return parse(text, path)


def save(cfg: Config, path: Path | None = None) -> Config:
    """Write `cfg` (checked first: what is written is read back the same) and return it as read."""
    path = path or cfg.path or config_path()
    text = render(cfg)
    saved = parse(text, path)                    # refuses what it couldn't load again
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    return saved
