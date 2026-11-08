"""DA Private Coder's settings: ~/.config/daprivatecoder/config.toml, written with the defaults (and
what each one means) on first use. It never holds secrets: the NanoGPT key lives in the keyring
(creds.py). The window saves them by writing the whole file again, with the same notes (render).

A profile is a way to start Claude Code: the models it uses, and whether it is private. A private
profile takes NanoGPT Private Mode models only (private/…), so every request is sealed to an
attested enclave; a model that isn't one is a mistake in the file, and nothing starts with it."""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .llm.client import is_private_mode, private_id
from .netpolicy import InsecureURL, check_url

APP_ID = "daprivatecoder"
NANOGPT_BASE_URL = "https://nano-gpt.com/api/v1"
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
    private: bool
    model: str
    opus_model: str
    fast_model: str
    vision_model: str = ""
    reasoning: str = "low"
    web_search: str = "off"

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


DEFAULTS = Config(profiles={
    "private": Profile("private", True, "private/glm-5-3", "private/kimi-k3", "private/deepseek-v4-1-flash",
                       "private/deepseek-v4-1-flash", "low", "zero-retention"),
    "nano": Profile("nano", False, "z-ai/glm-5.3", "moonshotai/kimi-k3", "z-ai/glm-5.3-flash", "", "low", "best"),
})

# ---------------------------------------------------------------------- the file, written

_HEADER = """\
# DA Private Coder settings: its window's Settings, or this file. The next Claude Code session it
# starts uses what is saved here. Saved from the window, the file is written again with these notes
# (notes of your own aren't kept). The NanoGPT API key isn't here but in your keyring.
"""
_PROFILE_INTRO = {
    "private": ("claude-private: NanoGPT Private Mode models only. Each request is sealed on this computer to a\n"
                "# Tinfoil enclave whose attestation was checked first; NanoGPT relays ciphertext."),
    "nano": ("daprivatecoder claude --profile nano: any NanoGPT model. Requests go to NanoGPT in the clear, as\n"
             "# with NanoGPT's own Claude Code setup; a private/ model you pick with /model is still sealed."),
}
_NOTES = {
    "model": "Claude Code's default model, and what /model sonnet picks",
    "opus_model": "what /model opus picks",
    "fast_model": "what /model haiku picks, and what Claude Code uses for quick background tasks",
    "vision_model": "describes images for a model that can't see them; empty: such a model is told it can't see",
    "reasoning": ("how hard the model reasons before it answers, as each session starts: low, medium, high or max.\n"
                  "# In a session, Claude Code's Effort (the editor's, or /effort) changes it"),
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


def render(cfg: Config) -> str:
    """The settings file for `cfg`, with what each setting means."""
    out = [_HEADER,
           "# NanoGPT's API (https only)", f"base_url = {_q(cfg.base_url)}", "",
           "# The Claude Code program to start. Empty: the one on your PATH, else the newest one bundled with",
           "# the Claude Code extension of VSCodium or VS Code.", f"claude = {_q(cfg.claude)}"]
    for p in cfg.profiles.values():
        intro = _PROFILE_INTRO.get(p.name) or ("A private profile: NanoGPT Private Mode models only." if p.private
                                               else "A profile that isn't private: requests go to NanoGPT in the clear.")
        out += ["", f"# {intro}", f"[profiles.{p.name}]", f"private = {'true' if p.private else 'false'}"]
        for key in ("model", "opus_model", "fast_model", "vision_model", "reasoning", "web_search"):
            if p.private or key == "web_search":
                out.append(f"# {_NOTES[key]}")
            out.append(f"{key} = {_q(getattr(p, key))}")
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
    private = table.get("private", False)
    if not isinstance(private, bool):
        raise ConfigError(f"{where}: private must be true or false.")
    model = _text(table, "model", where)
    if not model:
        raise ConfigError(f"{where}: model is missing.")
    opus = _text(table, "opus_model", where) or model
    fast = _text(table, "fast_model", where) or model
    vision = _text(table, "vision_model", where)
    reasoning = _text(table, "reasoning", where, "low").lower()
    if reasoning not in REASONING:
        raise ConfigError(f"{where}: reasoning must be one of {', '.join(REASONING)} (not {reasoning!r}).")
    # a private profile searches with Linkup unless told otherwise: of the searches, it alone keeps nothing
    web_search = _text(table, "web_search", where, "zero-retention" if private else "best").lower()
    if web_search not in WEB_SEARCH:
        raise ConfigError(f"{where}: web_search must be one of {', '.join(WEB_SEARCH)} (not {web_search!r}).")
    if private:
        # in a private profile, anything else would be sent in the clear: refused here, before anything starts
        for key, value in (("model", model), ("opus_model", opus), ("fast_model", fast), ("vision_model", vision)):
            if value and not is_private_mode(value):
                raise ConfigError(f"{where}: {key} is {value!r}, which isn't a NanoGPT Private Mode model (private/…). "
                                  "A private profile only uses those, so nothing would be sent in the clear.")
        model, opus, fast, vision = (private_id(m) if m else "" for m in (model, opus, fast, vision))
    return Profile(name, private, model, opus, fast, vision, reasoning, web_search)


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
