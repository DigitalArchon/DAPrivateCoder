"""What the window shows and does: the state of everything DA Private Coder needs (the keys, Claude
Code, the commands, the editor switch), the private, TEE and standard profiles' models and the hosts
that run them, what it cost, the enclaves' last attestations, and the user's actions on them. The window's server (server.py) calls these; they
do what the commands do (cli.py), on the same files."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, replace
from pathlib import Path

from . import __version__, commands, config, creds, editor, folders, gate, hostenv, launch, providers, usagelog
from .config import ConfigError
from .llm import capabilities, routes
from .llm.client import is_tee
from .llm.private_mode import Enclave, list_private_models, offers_private_mode, relay_url
from .netpolicy import shown
from .search import NAMES as SEARCH_NAMES

PROFILE_FIELDS = ("model", "opus_model", "fast_model", "vision_model", "reasoning", "web_search", "provider", "base_url")
# the window's three kinds of session, each a profile of that name (config.DEFAULTS)
SESSIONS = ("private", "tee", "standard")
# the kind of session a provider chosen in Setup runs (setup_provider): Tinfoil's private sessions, Chutes' TEE ones
# (its -TEE models, end-to-end encrypted), any other's standard ones; NanoGPT runs all three
SETUP_KIND = {"tinfoil": "private", "chutes": "tee"}
THEMES = ("dark", "light", "system")
RECENT = 8                                  # folders remembered for "Open Claude Code"
MODELS_FOR = 10 * 60                        # the list of private models is read again after this
SHOWN = 40                                  # requests shown in Usage


class UserError(Exception):
    """Something the user can fix, said as it is."""


def _sent(record: dict) -> str:
    """How a logged request or search left this computer: "sealed", "chutes" (sealed to an attested Chutes instance,
    partial privacy), "tee" (to an attested enclave, through NanoGPT's gateway), "clear", or "" (it didn't: refused
    here first). A request the proxy
    sent says which (its tier, or before there were TEE sessions, "sealed"); one it refused doesn't."""
    kind = record.get("kind")
    if kind == "search":
        return "" if record.get("status") == "refused" else "clear"
    tier = record.get("tier")
    if record.get("service") == "chutes" and tier == "tee":
        return "chutes"                       # sealed end to end to an attested Chutes instance: partial privacy
    if tier in ("private", "tee", "standard"):
        return {"private": "sealed", "tee": "tee", "standard": "clear"}[tier]
    if kind == "vision":
        return "sealed"                       # before there were other helpers, only ever a private model's
    if "sealed" not in record:
        return ""
    return "sealed" if record["sealed"] else "clear"


def _reply_checks(records: list[dict]) -> dict[tuple, dict]:
    """What each TEE reply's check found (proxy.py's "tee_reply" lines), by the session and the request
    it names."""
    return {(r.get("session"), r.get("of")): r for r in records if r.get("kind") == "tee_reply" and r.get("of")}


def _reply_of(record: dict, checks: dict[tuple, dict]) -> dict:
    """A TEE request's reply as its check found it ("verified", "signed", "failed", "unchecked", or
    "checking" until then), and why when it couldn't be checked; {} for any other request, and for one whose
    whole reply never came (refused, stopped, cut short), which has nothing to check: the proxy logs none of
    those as checking now (proxy.as_sent), but did until Nov 11 2026, and they read so for ever."""
    if not record.get("ref"):
        return {}
    found = checks.get((record.get("session"), record["ref"])) or {}
    if not found and (record.get("error") or record.get("status") != 200):
        return {}
    return {"reply": found.get("reply") or record.get("reply") or "checking", "reply_detail": found.get("detail", ""),
            # whether a verified or signed reply's receipt was read under the attested keys and held (proxy.py, from
            # Nov 13 2026; a line from before doesn't say): the mark's tooltip words one without as what it is
            **({"reply_receipt": found["receipt"]} if isinstance(found.get("receipt"), bool) else {}),
            # who ran its model, where the user asked for it (proxy.upstream_view: RedPill's word)
            **({"reply_upstream": found["upstream"]} if isinstance(found.get("upstream"), dict) else {})}


def _key_state(provider: str = "nanogpt", base_url: str = "") -> dict:
    """What the window shows of a provider's key: whether one is stored, and, for one that would go over plain http
    to another machine on your network, what you said of that (creds.in_the_clear_state: "ask" until you answer;
    `clear_host` the machine's address as shown)."""
    out = {"provider": provider, "stored": False, "ending": "", "error": "", "in_the_clear": "", "clear_host": ""}
    try:
        stored = creds.get_key(provider, base_url, sending=False)
        out.update(stored=bool(stored), ending=stored[-4:] if stored else "")
        if (stored or creds.has_password(base_url)) and base_url:
            out["in_the_clear"] = creds.in_the_clear_state(provider, base_url)
            out["clear_host"] = shown(base_url) if out["in_the_clear"] else ""
    except creds.KeyringUnavailable as e:
        out["error"] = str(e)
    return out


def prefs_path() -> Path:
    return config.config_dir() / "window.json"


class Manager:
    def __init__(self, ui_notice: str = "", can_choose_folder: bool = False):
        self.ui_notice = ui_notice
        self.can_choose_folder = can_choose_folder
        self.tray = False            # whether closing the window keeps DA Private Coder open, in the tray (app.py)
        self._models: dict[tuple, tuple[float, list[dict]]] = {}
        self.log = usagelog.UsageLog(config.state_dir() / "requests.log", "window")

    # ------------------------------------------------------------------ what there is

    def prefs(self) -> dict:
        try:
            data = json.loads(prefs_path().read_text())
        except (OSError, ValueError):
            data = {}
        return {"theme": data.get("theme") if data.get("theme") in THEMES else "dark",
                "recent": [f for f in data.get("recent", []) if isinstance(f, str)][:RECENT],
                # what "Start Claude Code" opens: a private, TEE or standard session
                "start": data.get("start") if data.get("start") in SESSIONS else "private",
                # the provider Setup was given (setup_provider): NanoGPT unless another was chosen there
                "setup": data.get("setup") if data.get("setup") in providers.PROVIDERS else "nanogpt"}

    def _save_prefs(self, prefs: dict) -> None:
        path = prefs_path()
        config.made(path.parent)
        config.write_private(path, json.dumps(prefs, indent=1))

    def state(self) -> dict:
        """Everything the window shows (blocking: the keyring is asked)."""
        from . import launcher
        out: dict = {"version": __version__, "ui_notice": self.ui_notice, "appimage": bool(os.environ.get("APPIMAGE")),
                     "can_choose_folder": self.can_choose_folder, "tray": self.tray, **self.prefs()}
        try:
            cfg = config.load()
            # an address with a password: as typed for its field, never so in what the window says of it
            out["config"] = {"base_url": shown(cfg.base_url), "path": str(cfg.path),
                             "profiles": {n: {**asdict(p), "base_url_shown": shown(p.base_url)}
                                          for n, p in cfg.profiles.items()},
                             # Enclave checks' switch: off unless the user turned it on (config.py)
                             "tee_name_upstreams": cfg.tee_name_upstreams}
            out["config_error"] = ""
        except ConfigError as e:
            cfg, out["config"], out["config_error"] = None, None, str(e)
        key = {**_key_state("nanogpt", cfg.base_url if cfg else ""), "vibe_manager": False}
        if not key["stored"] and not key["error"]:
            try:
                key["vibe_manager"] = bool(creds.vibe_manager_key())
            except creds.KeyringUnavailable:
                pass
        out["key"] = key
        # each session's provider's key, where it isn't NanoGPT's (a server of your own may need none); provider_key
        # is the standard one's, as the window had it before every kind could take another provider
        out["provider_keys"] = {name: _key_state(p.provider, cfg.provider_url(p))
                                for name, p in (cfg.profiles.items() if cfg else ())
                                if name in SESSIONS and p.provider != "nanogpt"}
        out["provider_key"] = out["provider_keys"].get("standard")
        try:
            out["claude"] = {"path": launcher.find_claude(cfg.claude if cfg else ""), "error": ""}
        except launcher.LaunchError as e:
            out["claude"] = {"path": "", "error": str(e)}
        out["commands"] = {name: {"state": commands.state(name), "path": str(commands.path_of(name))}
                           for name in commands.COMMANDS}
        out["commands_on_path"] = commands.on_path()
        out["editors"] = []
        for name, path in editor.editors():
            value = editor.current(path)
            state = {"ours": "on", "": "off"}.get(editor.owner(value), "other")      # other: another program's
            # mode: PER_FOLDER (each folder as chosen), or a profile (claude-private set by hand: all private)
            out["editors"].append({"name": name, "path": str(path), "value": value or "", "state": state,
                                   "mode": editor.profile_of(value), "opens": editor.open_command(name) is not None})
        try:
            chosen = folders.load()
            out["folders"] = {"list": [{"folder": f, "profile": p} for f, p in sorted(chosen.folders.items())],
                              "other": chosen.other, "error": ""}
        except folders.FoldersError as e:
            out["folders"] = {"list": [], "other": "", "error": str(e)}
        out["sessions"] = self.sessions()
        out["usage"] = self.usage()
        out["tee_upstreams_note"] = config.TEE_UPSTREAMS_NOTE          # beside its switch, in Enclave checks
        out["attestation"] = self.last_attestation()
        out["tinfoil_attestation"] = self.last_attestation("tinfoil")
        # the service each window profile's address is ("tinfoil", "chutes", ""), whichever provider names it
        out["services"] = {name: providers.service_at(cfg.provider_url(p)) for name, p in cfg.profiles.items()
                           if name in SESSIONS} if cfg else {}
        out["tee_attestations"] = self.tee_attestations()
        # whether a session of each kind can start: Claude Code, and the key its models need
        base = bool(cfg) and bool(out["claude"]["path"])

        def key_ok(name: str) -> bool:
            pk = out["provider_keys"].get(name)
            if pk is None:
                return key["stored"]
            return pk["stored"] or providers.get(pk["provider"]).key_optional
        out["ready"] = {name: base and key_ok(name) for name in SESSIONS}
        # Setup's provider and its key: NanoGPT's, or the one another provider's session runs on (setup_provider), so
        # a key of Anthropic's, Tinfoil's or Chutes' alone gets through Setup
        chosen = out.get("setup") if out.get("setup") in providers.PROVIDERS else "nanogpt"
        out["setup"] = chosen
        spec = providers.get(chosen)
        if chosen == "nanogpt":
            setup_key = key
        else:
            kind = SETUP_KIND.get(chosen, "standard")
            profile = cfg.profiles.get(kind) if cfg else None
            setup_key = out["provider_keys"].get(kind) if profile and profile.provider == chosen else None
            setup_key = setup_key or {"provider": chosen, "stored": False, "ending": "", "error": "", "in_the_clear": "",
                                      "clear_host": ""}
        out["setup_key"] = setup_key
        out["setup_ready"] = bool(cfg) and (setup_key["stored"] or spec.key_optional)
        return out

    def usage(self) -> dict:
        records = usagelog.read(config.state_dir() / "requests.log")
        checks = _reply_checks(records)
        today, month = time.strftime("%Y-%m-%d"), time.strftime("%Y-%m")
        rows, spent_today, spent_month, asked_today = [], 0.0, 0.0, 0
        for r in records:
            kind = r.get("kind")
            if kind not in ("request", "vision", "search"):
                continue
            u = r.get("usage") or {}
            cost = r.get("cost") if kind == "search" else u.get("cost")
            day = str(r.get("t", ""))[:10]
            if isinstance(cost, (int, float)):
                spent_month += cost if day.startswith(month) else 0
                spent_today += cost if day == today else 0
            asked_today += day == today
            sent = _sent(r)
            rows.append({"t": str(r.get("t", ""))[:19].replace("T", " "), "kind": kind,
                         "provider": r.get("provider") if kind != "search" else "",
                         "what": SEARCH_NAMES.get(r.get("provider"), r.get("provider") or "") if kind == "search"
                         else r.get("model", ""),
                         "asked_for": r.get("asked_for", ""), "sent": sent, "sealed": sent in ("sealed", "chutes"),
                         "service": r.get("service", ""),
                         "status": r.get("status", ""),
                         # no usage (refused, failed, a search): no tokens, rather than none
                         "input": u.get("input", 0) + u.get("cache_read", 0) if u else None,
                         "cached": u.get("cache_read", 0) if u else None, "output": u.get("output", 0) if u else None,
                         "results": r.get("results"),
                         "cost": cost if isinstance(cost, (int, float)) else None, "estimated": bool(u.get("cost_estimated")),
                         "seconds": r.get("seconds"), "error": r.get("error") or r.get("reason") or "",
                         **_reply_of(r, checks)})
        return {"today": round(spent_today, 4), "month": round(spent_month, 4), "requests_today": asked_today,
                "last": rows[-SHOWN:][::-1], "log": str(config.state_dir() / "requests.log")}

    def last_attestation(self, service: str = "") -> dict | None:
        """The last attestation of NanoGPT's Private Mode enclave that held (or with `service` "tinfoil", of
        Tinfoil's router, reached directly)."""
        found = None
        for r in usagelog.read(config.state_dir() / "requests.log"):
            if r.get("kind") == "attested" and not r.get("error") and (r.get("service") or "") == service:
                found = r
        if not found:
            return None
        return {k: found.get(k) for k in ("t", "summary", "hardware", "release", "latest", "key_sha256", "enclave")}

    def tee_attestations(self) -> list[dict]:
        """Each TEE model's last attestation (the newest first): what was found, or why it didn't hold. One that
        held says what was attested ("TEE gateway attested" for RedPill's gateway) and, first in what it says of
        it, what that leaves to someone's word (proxy.attested_as, what_attested)."""
        from .llm.tee import shortfall_cost
        from .proxy import attested_as, what_attested
        last: dict[str, dict] = {}
        for r in usagelog.read(config.state_dir() / "requests.log"):
            if r.get("kind") == "attested_tee" and r.get("model"):
                found = {k: r.get(k) for k in ("t", "model", "level", "summary", "shortfalls", "attested", "error",
                                               "service")}
                if not r.get("error"):
                    # and what each shortfall costs, for its tooltip (llm/tee.py SHORTFALL_COSTS: the standard every
                    # DA app keeps), read now, so an attestation logged before has its words too
                    shortfalls = r.get("shortfalls") if isinstance(r.get("shortfalls"), list) else []
                    found.update(attested_as=attested_as(r), what=what_attested(r),
                                 costs=[shortfall_cost(str(w)) for w in shortfalls])
                last[r["model"]] = found
        return sorted(last.values(), key=lambda a: str(a.get("t")), reverse=True)

    # ------------------------------------------------------------------ the keys

    def _provider_of(self, provider: str, profile: str = "standard") -> tuple[str, str]:
        """(provider, its base URL) for a key: the address it goes to, whose origin is kept beside it (creds.py).
        NanoGPT's is the settings' base_url, a provider's with a fixed address that one (providers.py), one of
        your own's the endpoint of `profile` (the window's tab: the standard one's unless said)."""
        spec = providers.PROVIDERS.get(provider)
        if spec is None:
            raise UserError(f"There's no provider called {provider!r}.")
        if spec.base_url:
            return provider, spec.base_url
        cfg = config.load()
        if provider == "nanogpt":
            return provider, cfg.base_url
        return provider, cfg.provider_url(cfg.profile(profile))

    def set_key(self, key: str, provider: str = "nanogpt") -> None:
        from .netpolicy import key_problem
        key = (key or "").strip()
        if not key:
            spec = providers.PROVIDERS.get(provider)
            where = f" ({spec.key_hint})" if spec and spec.key_hint else ""
            raise UserError(f"That isn't an API key: paste the key {spec.name if spec else 'the provider'} "
                            f"shows you{where}.")
        if key_problem(key):                       # said without anything of the key: it may be most of it
            raise UserError("That isn't an API key: it has a space, a line break or another character no API key "
                            "has, picked up where it was copied from, perhaps. Nothing was stored: copy it again and "
                            "paste it here.")
        creds.set_key(key, *self._provider_of(provider))

    def answer_in_the_clear(self, provider: str, allow) -> None:
        """Your answer for a provider's key going over plain http to another machine on your network (the window's
        question): True sends it, False doesn't and isn't asked again for that address, None asks again."""
        if allow not in (True, False, None):
            raise UserError("The answer must be yes, no, or ask again.")
        creds.answer_in_the_clear(*self._provider_of(provider), allow)

    def import_key(self) -> None:
        key = creds.vibe_manager_key()
        if not key:
            raise UserError("DA Vibe Manager has no NanoGPT key in your keyring.")
        creds.set_key(key, *self._provider_of("nanogpt"))

    def clear_key(self, provider: str = "nanogpt") -> None:
        creds.delete_key(*self._provider_of(provider))

    # ------------------------------------------------------------------ commands and the editor

    def install_commands(self) -> dict:
        done = commands.install()
        taken = [commands.path_of(n) for n, s in done.items() if s == "taken"]
        if taken:
            raise UserError(f"{', '.join(map(str, taken))} is someone else's program, so it was left as it is.")
        return done

    def set_editor(self, name: str, on: bool) -> None:
        """The editor's switch: on, its Claude Code runs as each folder says (set_folder); off, as usual."""
        found = dict(editor.editors())
        if name not in found:
            raise UserError(f"{name}'s settings weren't found here.")
        path = found[name]
        value = editor.current(path)
        if editor.owner(value) == "other":
            raise UserError(f"{name}'s Claude Code already runs through another program ({value}): it was left as it is.")
        editor.switch(path, editor.wrapper_path() if on else None)

    def set_folder(self, folder: str, profile: str) -> None:
        """How the editor's Claude Code runs in `folder` (and the folders in it): on `profile`, or as
        usual ("")."""
        folder = os.path.expanduser((folder or "").strip())
        if profile:
            config.load().profile(profile)                 # said now if there's no such profile
        if profile and not os.path.isdir(folder):
            raise UserError(f"{folder or 'That'} isn't a folder.")
        folders.save(folders.with_folder(self._folders(), folder, profile))

    def set_other(self, profile: str) -> None:
        """How the editor's Claude Code runs in every folder not chosen: as usual (""), or on `profile`."""
        if profile:
            config.load().profile(profile)
        f = self._folders()
        folders.save(folders.Folders(f.folders, profile))

    @staticmethod
    def _folders() -> folders.Folders:
        try:
            return folders.load()
        except folders.FoldersError as e:
            raise UserError(str(e)) from None

    def open_in_editor(self, name: str, folder: str, profile: str, popen=None) -> None:
        """`folder` in editor `name`, its Claude Code on `profile`: the folder chosen so, the editor's switch
        on, and the editor opened on it."""
        folder = os.path.expanduser((folder or "").strip())
        if not folder:
            raise UserError("Choose the folder to work in first.")
        if not os.path.isdir(folder):
            raise UserError(f"{folder} isn't a folder.")
        command = editor.open_command(name)
        if command is None:
            raise UserError(f"{name} couldn't be found here to open it.")
        self.set_folder(folder, profile)
        self.set_editor(name, True)
        import subprocess
        (popen or subprocess.Popen)([*command, folder], env=hostenv.host_env(), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        prefs = self.prefs()
        prefs["recent"] = [folder] + [f for f in prefs["recent"] if f != folder][:RECENT - 1]
        self._save_prefs(prefs)

    def sessions(self) -> list[dict]:
        """The sessions running on DA Private Coder now: what started each, in which folder, on which
        profile; the last request it made and how it went; and why it would stop at its next one."""
        from . import launcher
        records = usagelog.read(config.state_dir() / "requests.log")
        checks = _reply_checks(records)
        last: dict[str, dict] = {}
        for r in records:
            if r.get("kind") == "request" and r.get("session"):
                last[r["session"]] = r
        out = []
        for pid, path in launcher.live_sessions():
            try:
                info = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            editors = [(n, Path(p)) for n, p in info.get("editors") or []]
            r = last.get(info.get("session"))
            out.append({"pid": pid, "profile": info.get("profile", ""), "kind": info.get("kind", ""),
                        "folder": info.get("folder", ""), "editor": ", ".join(n for n, _ in editors),
                        "started": info.get("started"),
                        "last": {"t": r.get("t", "")[:19].replace("T", " "), "model": r.get("model", ""),
                                 "asked_for": r.get("asked_for", ""), "sent": _sent(r), "status": r.get("status"),
                                 "error": str(r.get("error") or "")[:300], "provider": r.get("provider", ""),
                                 "service": r.get("service", ""),
                                 # what the TEE model's attestation was of ("gateway": RedPill's), for the window
                                 "attested": r.get("attested", ""), **_reply_of(r, checks)} if r else None,
                        "stops": gate.Gate(info.get("profile", ""), editors, info.get("folder", "")).why()})
        return sorted(out, key=lambda s: s.get("started") or 0, reverse=True)

    # ------------------------------------------------------------------ settings

    def save_profile(self, name: str, fields: dict) -> None:
        cfg = config.load()
        profile = cfg.profile(name)
        change = {}
        if "context" in fields:
            if fields["context"] not in config.CONTEXTS:
                raise UserError(f"The context must be one of {', '.join(map(str, config.CONTEXTS))}.")
            change["context"] = fields["context"]
        for key in PROFILE_FIELDS:
            if key in fields:
                if not isinstance(fields[key], str):
                    raise UserError(f"{key} must be text.")
                change[key] = fields[key].strip()
        if change.get("provider", profile.provider) != profile.provider or \
                change.get("base_url", profile.base_url) != profile.base_url:
            change["routes"] = {}           # one provider's hosts mean nothing to another
        new = providers.PROVIDERS.get(change.get("provider", profile.provider))
        if new is not None and new.id != profile.provider:
            # another provider's models: its own for this kind, unless chosen with it (a model of NanoGPT's private/
            # isn't one of Tinfoil's, nor a TEE/ one Chutes'); NanoGPT's are the settings' own defaults
            own = config.DEFAULTS.profiles.get(profile.kind)
            models = new.defaults.get(profile.kind) or (
                (own.model, own.opus_model, own.fast_model, own.vision_model) if new.id == "nanogpt" and own and profile.kind != "standard" else None)
            for key, value in zip(("model", "opus_model", "fast_model", "vision_model"), models or ()):
                change.setdefault(key, value)
        config.save(cfg.with_profile(replace(profile, **change)))

    def save_route(self, name: str, model: str, route) -> None:
        """Which of the provider's hosts runs `model` in profile `name` (None or auto: its own choice)."""
        cfg = config.load()
        profile = cfg.profile(name)
        if not routes.routable(profile.provider, model):
            raise UserError(f"{model} can't be given a route here: only a model of NanoGPT or OpenRouter that goes in "
                            "the clear (not a private, TEE or Claude one).")
        try:
            route = routes.clean(profile.provider, route)
        except ValueError as e:
            raise UserError(str(e)) from None
        chosen = {k: v for k, v in profile.routes.items() if k != model}
        if route:
            chosen[model] = route
        config.save(cfg.with_profile(replace(profile, routes=chosen)))

    def set_tee_names(self, on: bool) -> None:
        """Enclave checks' "Name the provider behind each TEE reply" (config.py tee_name_upstreams): saved in the
        settings file, which every running session's proxy reads before each TEE reply's check, so it holds from
        the next reply on; off, nothing more is asked of api.redpill.ai."""
        cfg = config.load()
        config.save(replace(cfg, tee_name_upstreams=bool(on)))

    def setup_provider(self, provider: str, base_url: str = "") -> None:
        """Setup's provider: NanoGPT (as before: every kind of session on it), or another, whose kind of session is
        set to it (SETUP_KIND: Tinfoil's private one, Chutes' TEE one, any other's standard one), with its models,
        and made the session Start opens. A provider of your own needs its address."""
        spec = providers.PROVIDERS.get(provider)
        if spec is None:
            raise UserError(f"There's no provider called {provider!r}.")
        prefs = self.prefs()
        if provider != "nanogpt":
            kind = SETUP_KIND.get(provider, "standard")
            fields = {"provider": provider, **({"base_url": base_url} if spec.own_address else {})}
            if spec.own_address and not (base_url or "").strip():
                raise UserError("Give your provider's address first.")
            current = config.load().profile(kind)
            if current.provider != provider or (spec.own_address and current.base_url != base_url.strip()):
                self.save_profile(kind, fields)
            prefs["start"] = kind
        prefs["setup"] = provider
        self._save_prefs(prefs)

    def set_start(self, session: str) -> None:
        if session not in SESSIONS:
            raise UserError(f"A session is one of {', '.join(SESSIONS)}.")
        prefs = self.prefs()
        prefs["start"] = session
        self._save_prefs(prefs)

    def set_theme(self, theme: str) -> None:
        if theme not in THEMES:
            raise UserError(f"The colours are one of {', '.join(THEMES)}.")
        prefs = self.prefs()
        prefs["theme"] = theme
        self._save_prefs(prefs)

    # ------------------------------------------------------------------ NanoGPT: models and the enclave

    async def models(self, profile: str = "private", fresh: bool = False) -> list[dict]:
        """The models a profile can choose, with what they can do and cost: for a private one NanoGPT's
        Private Mode models now, for a TEE one its TEE models, for a standard one its provider's that take
        tool calls (public lists: no key is sent, but to a provider of your own that asks for one)."""
        cfg = config.load()
        p = cfg.profile(profile)
        url = cfg.provider_url(p)
        cache = (p.kind, p.provider, url)
        hit = self._models.get(cache)
        if hit and not fresh and time.monotonic() - hit[0] < MODELS_FOR:
            return hit[1]
        service = providers.service_at(url)
        if p.kind != "standard" and not service and not offers_private_mode(cfg.base_url):
            raise UserError(f"{shown(cfg.base_url)} isn't NanoGPT, so it has no Private Mode or TEE models.")
        caps: dict | None
        if service:
            # Tinfoil's or Chutes' public list (no key is sent): every model at Tinfoil's address is sealed to its
            # attested router, a -TEE one at Chutes' to an attested instance; a profile lists those of its kind
            caps = await self._sealed_listing(service, url)
            ids = sorted(m for m, c in caps.items() if c.get("tools")
                         and config.RANK[providers.tier_of(p.provider, url, m)] >= config.RANK[p.kind])
        elif p.kind == "private":
            ids = await list_private_models(cfg.base_url)
            try:
                caps = await capabilities.fetch_nanogpt(cfg.base_url)
            except Exception:  # noqa: BLE001 - listed without what they can do
                caps = {}
        elif p.kind == "tee":
            caps = await capabilities.fetch_nanogpt(cfg.base_url)
            ids = sorted(m for m in caps if is_tee(m))
        elif p.provider in ("nanogpt", "openrouter"):
            caps = await capabilities.fetch(p.provider, url) or {}
            ids = sorted(m for m, c in caps.items() if c.get("tools"))
        else:
            spec = providers.get(p.provider)
            key = await in_thread(creds.get_key, p.provider, url)
            if not key and not spec.key_optional:
                raise UserError(f"{spec.name}'s list of models needs your {spec.name} API key: add it below, and its "
                                "models are listed here.")
            caps, ids = None, await self._listed(url, key, p.provider)
        out = []
        for m in ids:
            c = (caps.get(m) if service else capabilities.lookup(caps, m)) or {} if caps else {}
            price = c.get("pricing")
            found = providers.privacy_of(p.provider, url, m)
            # NanoGPT's Private Mode costs its TEE twin's listed prices and 5%; Tinfoil's are its own
            markup = capabilities.PRIVATE_MARKUP if found.service == "nanogpt-private" and not service else 1.0
            out.append({"id": m, "vision": bool(c.get("vision")) if c else None, "tools": bool(c.get("tools")) if c else None,
                        # how private it is where its requests go (llm/privacy.py): its level, words and mark
                        "privacy": found.to_dict(),
                        "context": c.get("context"), "included": bool(c.get("included")) and p.provider == "nanogpt",
                        "routable": routes.routable(p.provider, m),
                        "price": {k: round(price[k] * markup, 4) for k in ("input", "cache_read", "output")} if price else None})
        self._models[cache] = (time.monotonic(), out)
        return out

    @staticmethod
    async def _sealed_listing(service: str, url: str) -> dict:
        """Tinfoil's or Chutes' public list of models, what each can do and costs (no key is sent)."""
        try:
            if service == "tinfoil":
                from .llm.tinfoil_direct import Listing, listing_capabilities
                listed = listing_capabilities(await Listing(url).fetch())
            else:
                from .llm.chutes_client import list_chutes_models
                from .llm.chutes_client import listing_capabilities as chutes_capabilities
                listed = chutes_capabilities(await list_chutes_models())
        except Exception as e:  # noqa: BLE001 - said as it is
            raise UserError(f"{providers.name(service)}'s list of models couldn't be read: {e}") from None
        return capabilities.from_listing(listed)

    @staticmethod
    async def _listed(url: str, key: str | None, provider: str = "custom") -> list[str]:
        """The ids a provider's list of models has, asked for with its key: OpenAI's /models (a provider of your
        own), or the Messages API's /v1/models (one that speaks Anthropic's: each model it lists, at most 1000)."""
        import httpx

        from .llm.client import reading_options
        from .llm.secure_http import loads
        from .netpolicy import check_url
        check_url(url, "Your provider's base URL")
        # a provider on this computer or its network is reached directly, never through a proxy the environment
        # names: its key went to it (DA Toolkit's c6de017, as DA Vibe Manager's)
        if providers.get(provider).api == "anthropic":
            from .proxy import upstream_url
            where = upstream_url(url, "/v1/models", "limit=1000")
            headers = {"anthropic-version": providers.ANTHROPIC_VERSION}
        else:
            where, headers = url.rstrip("/") + "/models", {}
        headers.update(providers.auth_headers(provider, key))
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, **reading_options(url)) as client:
            r = await client.get(where, headers=headers)
            if r.status_code >= 300:            # said without the address's password (httpx's own error quotes it)
                raise UserError(f"{shown(url)} answered HTTP {r.status_code} when asked for its models.")
            listed = loads(r.content)
            data = listed.get("data") if isinstance(listed, dict) else None
        return sorted(m["id"] for m in data or [] if isinstance(m, dict) and isinstance(m.get("id"), str))

    async def hosts(self, profile: str, model: str) -> dict:
        """The hosts the profile's provider lists for `model`, for a route of the user's choosing."""
        cfg = config.load()
        p = cfg.profile(profile)
        if not routes.routable(p.provider, model):
            raise UserError(f"{model} can't be given a route here.")
        try:
            return await routes.fetch_hosts(p.provider, cfg.provider_url(p), model)
        except Exception as e:  # noqa: BLE001 - said as it is
            raise UserError(f"The list of hosts for {model} couldn't be read: {e}") from None

    async def check(self, model: str = "", service: str = "") -> dict:
        """Attest the private enclave now (or with `model`, a TEE model's; with `service` "tinfoil", Tinfoil's router,
        reached directly; with "chutes" and `model`, Chutes' instances of that -TEE model, as far as is shown without
        the key), as a session does before it sends anything."""
        cfg = config.load()
        if service == "tinfoil":
            return await self.check_tinfoil()
        if service == "chutes":
            return await self.check_chutes(model)
        if model:
            return await self.check_tee(cfg, model)
        if not offers_private_mode(cfg.base_url):
            raise UserError(f"{shown(cfg.base_url)} isn't NanoGPT, so it has no Private Mode.")
        try:
            att = (await Enclave(relay_url(cfg.base_url)).current()).attestation
        except Exception as e:  # noqa: BLE001 - said as it is
            self.log.write("attested", error=str(e)[:300])
            raise UserError(f"The enclave couldn't be verified: {e}") from None
        self.log.write("attested", enclave=att.enclave, hardware=att.hardware, release=att.release_tag,
                       digest=att.release_digest, latest=att.latest_release, latest_checked=att.latest_checked,
                       key_sha256=att.hpke_key_sha256, summary=att.summary)
        return self.last_attestation() or {}

    async def check_tinfoil(self) -> dict:
        """Tinfoil's router attested now, from Tinfoil's own bundle and Sigstore's record (no key is sent), as a
        session on Tinfoil does before it sends anything (llm/tinfoil_direct.py)."""
        from .llm.tinfoil_direct import TinfoilState
        try:
            att = (await TinfoilState(providers.TINFOIL_BASE_URL).enclave.current()).attestation
        except Exception as e:  # noqa: BLE001 - said as it is
            self.log.write("attested", service="tinfoil", error=str(e)[:300])
            raise UserError(f"Tinfoil's router couldn't be verified: {e}") from None
        self.log.write("attested", service="tinfoil", enclave=att.enclave, hardware=att.hardware,
                       release=att.release_tag, digest=att.release_digest, latest=att.latest_release,
                       latest_checked=att.latest_checked, key_sha256=att.hpke_key_sha256, summary=att.summary)
        return self.last_attestation("tinfoil") or {}

    async def check_chutes(self, model: str) -> dict:
        """What can be checked of Chutes' instances of -TEE `model` without the key (llm/chutes_client.py
        attest_public: Chutes' public evidence for a nonce of ours, Intel's and NVIDIA's checks, the published
        images; each instance's key binding is shown only to a session, which has the key)."""
        from .llm.chutes_client import ChutesE2EEClient
        from .llm.privacy import CHUTES_BASE_URL, is_tee_id
        from .proxy import ATTESTED
        if not is_tee_id(model):
            raise UserError(f"{model} isn't one of Chutes' end-to-end encrypted models (…-TEE).")
        client = ChutesE2EEClient(CHUTES_BASE_URL, None)
        try:
            att = await in_thread(client.attest_public, model)
        except Exception as e:  # noqa: BLE001 - said as it is
            self.log.write("attested_tee", model=model, service="chutes", error=str(e)[:300])
            raise UserError(f"Chutes' instances of {model} couldn't be checked: {e}") from None
        finally:
            client.close()
        found = att.to_dict()
        self.log.write("attested_tee", model=model, service="chutes", **{k: found.get(k) for k in ATTESTED})
        return next(a for a in self.tee_attestations() if a["model"] == model)

    async def check_tee(self, cfg, model: str) -> dict:
        """A TEE model's enclave, attested now (llm/tee.py): NanoGPT asks for the key to answer."""
        from .proxy import ATTESTED, attest_tee
        if not is_tee(model):
            raise UserError(f"{model} isn't one of NanoGPT's TEE models.")
        try:
            key = await in_thread(creds.get_key, "nanogpt", cfg.base_url)
        except creds.KeyringUnavailable as e:
            raise UserError(str(e)) from None
        if not key:
            raise UserError("NanoGPT shows a TEE model's attestation only with your API key: store it first (Setup).")
        try:
            att = await in_thread(attest_tee, cfg.base_url, key, model)
        except Exception as e:  # noqa: BLE001 - said as it is
            self.log.write("attested_tee", model=model, error=str(e)[:300])
            raise UserError(f"{model}'s enclave didn't attest: {e}") from None
        self.log.write("attested_tee", model=model, **{k: att.get(k) for k in ATTESTED})
        return next(a for a in self.tee_attestations() if a["model"] == model)

    # ------------------------------------------------------------------ opening Claude Code

    def open(self, folder: str, profile: str = "private", popen=None) -> None:
        folder = os.path.expanduser((folder or "").strip())
        if not folder:
            raise UserError("Choose the folder to work in first.")
        config.load().profile(profile)                     # said now if there's no such profile
        command = commands.target_argv() + (["claude-private"] if profile == "private"
                                            else ["claude", "--profile", profile])
        try:
            launch.open_in_terminal(folder, command, hostenv.host_env(), **({"popen": popen} if popen else {}))
        except launch.LaunchError as e:
            raise UserError(str(e)) from None
        prefs = self.prefs()
        prefs["recent"] = [folder] + [f for f in prefs["recent"] if f != folder][:RECENT - 1]
        self._save_prefs(prefs)

    def forget_folder(self, folder: str) -> None:
        prefs = self.prefs()
        prefs["recent"] = [f for f in prefs["recent"] if f != folder]
        self._save_prefs(prefs)


async def in_thread(fn, *args, **kwargs):
    """Blocking work (the keyring, files, starting a terminal) off the server's loop."""
    return await asyncio.to_thread(fn, *args, **kwargs)
