"""DA Private Coder's settings: ~/.config/daprivatecoder/config.toml, written with the defaults (and
what each one means) on first use. It never holds secrets: the NanoGPT key lives in the keyring
(creds.py), and so does an address typed with a user name and password (_as_typed). The window saves
them by writing the whole file again, with the same notes (render).

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
import textwrap
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import providers as providers_mod
from .llm import routes as routes_mod
from .llm.client import RANK, private_id, tier_of
from .netpolicy import InsecureURL, check_url, shown, without_password
from .providers import NANOGPT_BASE_URL, OPENROUTER_BASE_URL  # noqa: F401 - named here since before providers.py

APP_ID = "daprivatecoder"
# how a profile's requests travel (llm/client.py's tiers): sealed, attested TEE, or in the clear
KINDS = ("private", "tee", "standard")
# where a profile's models are, and the kinds of profile each serves (providers.py: private and TEE profiles are
# NanoGPT's): id -> its name in a sentence
PROVIDERS = {p.id: p.name for p in providers_mod.PROVIDERS.values()}
# where a session's effort starts (Claude Code's own changes it); a model is sent the nearest level it
# takes (proxy.effort_for). Never "none": GLM with its thinking off reasons all the more, in its answer
REASONING = ("low", "medium", "high", "max")
# the context window Claude Code is told (CLAUDE_CODE_MAX_CONTEXT_TOKENS), where it compacts the
# conversation: for a model it doesn't know, it assumes 200K. The window offers these; a file may say
# any size between the first and the last
CONTEXTS = (200_000, 400_000, 1_000_000)
CONTEXT_RANGE = (100_000, 1_048_576)
# web search (search.py): Linkup, which keeps nothing; Perplexity and Kagi; or none
WEB_SEARCH = ("zero-retention", "best", "off")
PROFILE_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")        # the whole name (fullmatch)


class ConfigError(Exception):
    """The settings file can't be used as it is; the message says what to change."""


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / APP_ID


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / APP_ID


def data_dir() -> Path:
    """The app's data folder: the editor's script (commands.wrapper_dir) is in it."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / APP_ID


def config_path() -> Path:
    return config_dir() / "config.toml"


def write_private(path: Path, data: bytes | str, mode: int = 0o600) -> None:
    """`data` as `path`'s whole content, or the file as it was: written to a file of its own in the same folder
    (unique, and 0600 from the start: mkstemp; `mode` once it is written, for a script or a file that keeps its
    own), flushed to the disk, put in place in one step (os.replace), and the folder flushed too, so that a crash
    or a full disk never leaves half a file, nor a rename the disk never saw. Every write of a file that matters
    goes through it (the cross-app standard, Nov 2026, as DA Vibe Manager's config.write_private): the settings,
    the folders' choices, the window's preferences, an editor's settings, the commands it installs, a session's
    --settings and its record. The folder is the caller's to make (`made`)."""
    path = Path(path)
    body = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            if mode != 0o600:
                os.fchmod(f.fileno(), mode)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(folder)
    except OSError:
        pass
    finally:
        os.close(folder)


def made(folder: Path) -> Path:
    """`folder`, made if it isn't there. Where it is the app's settings, state or data folder, or one inside them, that
    folder and it are readable by this user alone (0700), made so or set so if they were there already: the request
    log, each session's token and what it runs where, the window's lock, the settings and the editor's script are
    nobody else's to list, read or change. They were made as the umask said, which lets every user of the computer
    list them (0755), and where it is 002 (Mint, Ubuntu) the user's group change what is in them (0775). A folder
    elsewhere (a --config file's, ~/.local/bin) is made as the umask says and left as it is."""
    folder = Path(folder)
    for own in (config_dir(), state_dir(), data_dir()):
        if folder == own or own in folder.parents:
            own.mkdir(parents=True, exist_ok=True, mode=0o700)
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            for each in {own, folder}:
                try:
                    os.chmod(each, 0o700)
                except OSError:
                    pass                    # not ours to change: used as it is
            return folder
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@dataclass(frozen=True)
class Profile:
    name: str
    kind: str                       # private, tee or standard (KINDS)
    model: str
    opus_model: str
    fast_model: str
    vision_model: str = ""
    reasoning: str = "high"         # no level is called recommended: for coding it's up to the use case
    web_search: str = "off"
    provider: str = "nanogpt"       # where its models are (providers.py): a standard profile's any one
    base_url: str = ""              # a provider of your own's (providers.Provider.own_address): its endpoint
    # which of the provider's hosts runs a model (llm/routes.py): model -> route; none for its own choice
    routes: dict = field(default_factory=dict, hash=False)
    context: int = CONTEXTS[0]      # the conversation's size where Claude Code compacts it (CONTEXTS)

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
    # after a TEE reply whose receipt holds, name the provider that ran its model, from RedPill's public record
    # of its gateway's session (llm/tee.py served_by), beside the reply's mark. Off by default: it connects to
    # api.redpill.ai directly, not through NanoGPT, so RedPill sees this computer's address (SealedLore's, the
    # author's, Nov 11 2026: a clear choice, off by default). A running session reads it afresh (names_upstreams)
    tee_name_upstreams: bool = False
    path: Path | None = None
    # the addresses whose password the keyring couldn't be asked for when the file was read (_as_typed):
    # saved, they don't take out the one it may have
    unread: frozenset = field(default=frozenset(), compare=False, repr=False)

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
        """Where `profile`'s models are: NanoGPT's base_url, a provider's own fixed address, or the profile's own."""
        return providers_mod.get(profile.provider).address(self.base_url, profile.base_url)


DEFAULTS = Config(profiles={
    # Opus: Kimi K3.1 Pro, since NanoGPT retired private/kimi-k3 from Private Mode (Nov 11 2026). A settings file
    # keeps the model it names, never changed for another: one naming private/kimi-k3 is told it's no longer
    # offered (the window's Models, `daprivatecoder check`, and a session's refusal: private_mode.ModelRetired)
    "private": Profile("private", "private", "private/glm-5-3", "private/kimi-k3-1-pro", "private/deepseek-v4-1-flash",
                       "private/deepseek-v4-1-flash", "high", "zero-retention"),
    "tee": Profile("tee", "tee", "TEE/glm-5.3", "TEE/kimi-k3", "TEE/glm-5.3-flash", "TEE/glm-5.3-flash", "high",
                   "zero-retention"),
    "standard": Profile("standard", "standard", "z-ai/glm-5.3", "moonshotai/kimi-k3", "z-ai/glm-5.3-flash",
                        "z-ai/glm-5.3-flash", "high", "best"),
})

# ---------------------------------------------------------------------- the file, written

_HEADER = """\
# DA Private Coder settings: its window's Settings, or this file. The next Claude Code session it
# starts uses what is saved here. Saved from the window, the file is written again with these notes
# (notes of your own aren't kept). The API keys aren't here but in your keyring.
"""
# beside "Name the provider behind each TEE reply" (the window's Enclave checks, tee_name_upstreams): what it
# does, and whom it tells (SealedLore's TEE_UPSTREAMS_NOTE, Nov 11 2026); the window says the same
TEE_UPSTREAMS_NOTE = (
    "Most TEE models are attested as RedPill's gateway, which passes each request on to the provider that runs "
    "the model. With this on, after a TEE reply checks out, DA Private Coder asks RedPill which provider that was "
    "(Phala, Fortanix, NEAR AI, 0G, Tinfoil…) and shows it beside the reply's mark in Usage and Running now. This "
    "connects to api.redpill.ai directly, not through NanoGPT, so RedPill sees this computer's internet address "
    "and that one of its models was used around then. Only the session's id is sent, never your messages or your "
    "key. It is one small download a model an hour, and never for models Chutes serves, whose records are "
    "megabytes each. What it names is RedPill's word, matched to the receipt the attested gateway signed.")
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
    "context": ("the conversation's size, in tokens, where Claude Code compacts it (summarises it, and goes on):\n"
                "# 200000 (what it assumes for models it doesn't know), 400000 or 1000000 (what NanoGPT lists for these).\n"
                "# Every request sends the whole conversation, so a bigger one costs more each time, even from the cache"),
    "provider": ('"nanogpt", "openrouter", "anthropic" (Anthropic\'s own API: Claude Code\'s requests go to it as they\n'
                 '# are), "custom" (any OpenAI-compatible endpoint: base_url, https unless it is on this computer or\n'
                 '# your network) or "custom-anthropic" (any Anthropic-compatible one, its base_url as Claude Code\n'
                 "# would be given it: requests go to it as they are). Its API key is in your keyring, set in the window"),
    "web_search": """\
Web search, through NanoGPT. No search is end-to-end encrypted: NanoGPT and the search provider
# read every query (Claude Code asks you before each one, and refuses one naming this computer).
#   "zero-retention"  Linkup only, which keeps nothing (zero data retention). Finds less.
#   "best"            Perplexity to learn things, Kagi to find sites: better results, but not zero
#                     data retention: what they keep is up to their own policies.
#   "off"             no web search""",
}


# what a provider of your own's base_url is, when it is missing
_ENDPOINT = {"custom": "the OpenAI-compatible endpoint of your provider (as https://api.example.com/v1)",
             "custom-anthropic": "the Anthropic-compatible endpoint of your provider, the address Claude Code would be "
                                 "given as ANTHROPIC_BASE_URL (as https://api.example.com/anthropic)"}


def _own(provider: str) -> bool:
    """Whether `provider` is one of your own, whose address the profile says."""
    found = providers_mod.PROVIDERS.get(provider)
    return bool(found and found.own_address)


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
           "# the Claude Code extension of VSCodium or VS Code.", f"claude = {_q(cfg.claude)}", "",
           "# Name the provider behind each TEE reply: true or false (false unless you turn it on). Running",
           "# sessions follow it from their next reply.",
           *("# " + line for line in textwrap.wrap(TEE_UPSTREAMS_NOTE, 98)),
           f"tee_name_upstreams = {'true' if cfg.tee_name_upstreams else 'false'}"]
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
            if _own(p.provider):
                out.append(f"base_url = {_q(p.base_url)}")
        for key in ("model", "opus_model", "fast_model", "vision_model", "reasoning", "web_search"):
            out += [*note(key), f"{key} = {_q(getattr(p, key))}"]
        out += [*note("context"), f"context = {p.context}"]
        if p.routes:
            out += ["# which of the provider's hosts runs a model (the window's Route): auto is the provider's own choice",
                    f"[profiles.{p.name}.routes]"]
            out += [f"{_q(model)} = {_inline(route)}" for model, route in p.routes.items()]
    return "\n".join(out) + "\n"


DEFAULT_TEXT = render(DEFAULTS)

# ---------------------------------------------------------------------- addresses with a password in them

# The settings that are addresses (NanoGPT's base_url, a profile's own provider's) may have a user name
# and password written into them: a provider of your own behind a password. The file has the address
# without them (netpolicy.without_password), and the keyring the address as typed, under the setting's
# name (creds.address): put back together when the file is read, and only while the file's address is
# that one less its password, so a password goes to no address but the one it was typed for, whatever
# the file is edited to. Until Nov 2026 the address went into the file as typed. (DA Assistant's
# config.py, 729a415.)


def _addresses(cfg: Config) -> list[tuple[str, str]]:
    """(setting, address) for each address `cfg` has: NanoGPT's, and each profile's own provider's (one of your own:
    another's isn't written, render)."""
    return [("base_url", cfg.base_url)] + [(f"profiles.{name}.base_url", p.base_url) for name, p in cfg.profiles.items()
                                           if _own(p.provider) and p.base_url]



def _with_address(cfg: Config, setting: str, value: str) -> Config:
    if setting == "base_url":
        return replace(cfg, base_url=value)
    name = setting.split(".")[1]
    return cfg.with_profile(replace(cfg.profiles[name], base_url=value))


def _has_password(url: str) -> bool:
    return without_password(url) != url


def _as_typed(cfg: Config) -> Config:
    """`cfg`, as its file has it, with each address as it was typed: the file's, with the user name and
    password the keyring has for it. One the file has a password in (written so before, or typed in by
    hand) is moved to the keyring, and out of the file once it's there; where the keyring can't take
    it, it stays in the file and is used, as it was."""
    from . import creds
    loose = []
    for setting, written in _addresses(cfg):
        if _has_password(written):
            loose.append((setting, written))
            continue
        try:
            stored = creds.address(setting)
        except creds.KeyringUnavailable:
            # used without: a server that needs its password says so
            cfg = replace(cfg, unread=cfg.unread | {setting})
            continue
        if stored and without_password(stored) == written:
            cfg = _with_address(cfg, setting, stored)
    if loose:
        try:
            for setting, written in loose:
                creds.keep_address(setting, written)
            save(cfg)                       # the file again, without them (and with its notes, as the window writes it)
        except (creds.KeyringUnavailable, ConfigError, OSError):
            pass                            # used as they are, and moved at the next save that can
    return cfg


def _in_file(path: Path) -> dict[str, str]:
    """The addresses the file at `path` has now, by setting ({} if it can't be read)."""
    try:
        return dict(_addresses(parse(path.read_text(), path)))
    except (OSError, ConfigError):
        return {}


# ---------------------------------------------------------------------- the file, read


def _text(table: dict, key: str, where: str, default: str = "") -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{where}: {key} must be text in quotes.")
    return value.strip()


def _profile(name: str, table: dict, where: str) -> Profile:
    if not PROFILE_NAME.fullmatch(name):
        raise ConfigError(f"{where}: a profile's name is lower-case letters, digits and dashes ({name!r} isn't).")
    if not isinstance(table, dict):
        raise ConfigError(f"{where}: [profiles.{name}] must be a table of settings.")
    where = f"{where} [profiles.{name}]"
    if "kind" in table:
        kind = _text(table, "kind", where).lower()
    elif "private" in table:
        private = table.get("private")               # how the first settings files said it
        if not isinstance(private, bool):
            raise ConfigError(f"{where}: private must be true or false.")
        kind = "private" if private else "standard"
    else:
        # claude-private, the window's Private and TEE sessions and the editor's choices run these by name
        kind = name if name in ("private", "tee") else "standard"
    if kind not in KINDS:
        raise ConfigError(f"{where}: kind must be one of {', '.join(KINDS)} (not {kind!r}).")
    if name in ("private", "tee") and kind != name:
        raise ConfigError(f"{where}: the {name} profile is what {name} sessions run on, so its kind is {name} "
                          f"(not {kind}). Give a profile of another kind a name of its own.")
    provider = _text(table, "provider", where, "nanogpt").lower()
    if provider not in PROVIDERS:
        raise ConfigError(f"{where}: provider must be one of {', '.join(PROVIDERS)} (not {provider!r}).")
    if kind not in providers_mod.get(provider).kinds:
        allowed = providers_mod.for_kind(kind)
        raise ConfigError(f"{where}: a {kind} profile's models are "
                          f"{' or '.join(providers_mod.name(a) for a in allowed)}'s, so its provider is "
                          f"{' or '.join(allowed)}.")
    own = _own(provider)
    base_url = _text(table, "base_url", where).rstrip("/") if own else ""
    if own:
        if not base_url:
            raise ConfigError(f"{where}: base_url is missing: {_ENDPOINT[provider]}.")
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
    reasoning = _text(table, "reasoning", where, "high").lower()
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
    context = table.get("context", CONTEXTS[0])
    if isinstance(context, bool) or not isinstance(context, int) or not CONTEXT_RANGE[0] <= context <= CONTEXT_RANGE[1]:
        raise ConfigError(f"{where}: context must be a number of tokens from {CONTEXT_RANGE[0]} to {CONTEXT_RANGE[1]}, "
                          f"such as 200000 (not {context!r}).")
    return Profile(name, kind, model, opus, fast, vision, reasoning, web_search, provider, base_url, checked, context)


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
    names = data.get("tee_name_upstreams", False)
    if not isinstance(names, bool):
        raise ConfigError(f"{where}: tee_name_upstreams must be true or false (not {names!r}).")
    profiles_table = data.get("profiles", {})
    if not isinstance(profiles_table, dict):
        raise ConfigError(f"{where}: profiles must be tables, as [profiles.private].")
    profiles = {name: _profile(name, table, where) for name, table in profiles_table.items()}
    # the window's three kinds of session are always there: one left out of the file is its default
    profiles.update({name: p for name, p in DEFAULTS.profiles.items() if name not in profiles})
    return Config(base_url=base_url.rstrip("/"), claude=claude, profiles=profiles, path=path, tee_name_upstreams=names)


def names_upstreams(path: Path | None = None) -> bool:
    """Whether the settings file says to name the provider behind each TEE reply (tee_name_upstreams), read
    afresh: a running session's proxy asks before each reply's check, so switching it off stops the next
    lookup, as closing the window stops the next request. A file that can't be read says no: nothing goes to
    api.redpill.ai on a guess."""
    try:
        data = tomllib.loads((path or config_path()).read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return data.get("tee_name_upstreams") is True


def load(path: Path | None = None) -> Config:
    """The settings, from `path` (default: config_path()), written with the defaults if there are none;
    each address as it was typed (_as_typed)."""
    path = path or config_path()
    made(path.parent)                       # readable by this user alone, if it is the settings folder
    if not path.exists():
        write_private(path, DEFAULT_TEXT)
    try:
        text = path.read_text()
    except OSError as e:
        raise ConfigError(f"{path} couldn't be read: {e}") from None
    return _as_typed(parse(text, path))


def save(cfg: Config, path: Path | None = None) -> Config:
    """Write `cfg` (checked first: what is written is read back the same) and return it as read, each
    address as typed. An address with a user name and password goes to the keyring as typed, and into
    the file without them; one without takes out of the keyring any kept for its setting. A password
    the keyring can't take is written nowhere, and nothing is saved, but for one the file has already,
    with that very address, which stays there as it was."""
    from . import creds
    path = path or cfg.path or config_path()
    typed = dict(_addresses(cfg))
    loose = {setting: value for setting, value in typed.items() if _has_password(value)}
    on_file = cfg
    for setting, value in loose.items():
        on_file = _with_address(on_file, setting, without_password(value))
    parse(render(on_file), path)                 # refuses what it couldn't load again, before anything is kept
    there = None
    for setting, value in loose.items():
        try:
            creds.keep_address(setting, value)
        except creds.KeyringUnavailable as e:
            there = _in_file(path) if there is None else there
            if there.get(setting) != value:
                raise ConfigError(f"{shown(value)} has a user name and password in it, which are kept in your "
                                  f"keyring, and the keyring couldn't take them ({e}). Nothing was saved: unlock "
                                  "your keyring, or leave them out of the address.") from None
            on_file = _with_address(on_file, setting, value)    # in the file already: it stays, until the keyring can take it
    for setting in ["base_url"] + [f"profiles.{name}.base_url" for name in cfg.profiles]:
        if setting not in loose and setting not in cfg.unread:
            try:
                if creds.address(setting):
                    creds.forget_address(setting)    # taken out of the address, or the address gone
            except creds.KeyringUnavailable:
                pass                                # and never put back: it fits no address but its own
    text = render(on_file)
    saved = parse(text, path)
    made(path.parent)
    write_private(path, text)
    for setting, written in _addresses(saved):
        if setting in typed and without_password(typed[setting]) == without_password(written):
            saved = _with_address(saved, setting, typed[setting])
    return saved
