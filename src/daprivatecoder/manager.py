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

from . import __version__, commands, config, creds, editor, folders, gate, hostenv, launch, usagelog
from .config import ConfigError
from .llm import capabilities, routes
from .llm.client import is_tee
from .llm.private_mode import Enclave, list_private_models, offers_private_mode, relay_url
from .netpolicy import shown
from .search import NAMES as SEARCH_NAMES

PROFILE_FIELDS = ("model", "opus_model", "fast_model", "vision_model", "reasoning", "web_search", "provider", "base_url")
# the window's three kinds of session, each a profile of that name (config.DEFAULTS)
SESSIONS = ("private", "tee", "standard")
THEMES = ("dark", "light", "system")
RECENT = 8                                  # folders remembered for "Open Claude Code"
MODELS_FOR = 10 * 60                        # the list of private models is read again after this
SHOWN = 40                                  # requests shown in Usage


class UserError(Exception):
    """Something the user can fix, said as it is."""


def _sent(record: dict) -> str:
    """How a logged request or search left this computer: "sealed", "tee" (to an attested enclave,
    through NanoGPT's gateway), "clear", or "" (it didn't: refused here first). A request the proxy
    sent says which (its tier, or before there were TEE sessions, "sealed"); one it refused doesn't."""
    kind = record.get("kind")
    if kind == "search":
        return "" if record.get("status") == "refused" else "clear"
    tier = record.get("tier")
    if tier in ("private", "tee", "standard"):
        return {"private": "sealed", "tee": "tee", "standard": "clear"}[tier]
    if kind == "vision":
        return "sealed"                       # before there were other helpers, only ever a private model's
    if "sealed" not in record:
        return ""
    return "sealed" if record["sealed"] else "clear"


def _key_state(provider: str = "nanogpt", base_url: str = "") -> dict:
    out = {"provider": provider, "stored": False, "ending": "", "error": ""}
    try:
        stored = creds.get_key(provider, base_url)
        out.update(stored=bool(stored), ending=stored[-4:] if stored else "")
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
                "start": data.get("start") if data.get("start") in SESSIONS else "private"}

    def _save_prefs(self, prefs: dict) -> None:
        import tempfile
        path = prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(prefs, indent=1))
        os.replace(tmp, path)

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
                                          for n, p in cfg.profiles.items()}}
            out["config_error"] = ""
        except ConfigError as e:
            cfg, out["config"], out["config_error"] = None, None, str(e)
        key = {**_key_state(), "vibe_manager": False}
        if not key["stored"] and not key["error"]:
            try:
                key["vibe_manager"] = bool(creds.vibe_manager_key())
            except creds.KeyringUnavailable:
                pass
        out["key"] = key
        # the standard profile's provider's key, when it isn't NanoGPT's (a server of your own may need none)
        standard = cfg.profiles.get("standard") if cfg else None
        out["provider_key"] = _key_state(standard.provider, cfg.provider_url(standard)) \
            if standard and standard.provider != "nanogpt" else None
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
        out["attestation"] = self.last_attestation()
        out["tee_attestations"] = self.tee_attestations()
        # whether a session of each kind can start: Claude Code, and the key its models need
        base = bool(cfg) and bool(out["claude"]["path"])
        provider_ok = out["provider_key"] is None or out["provider_key"]["stored"] or out["provider_key"]["provider"] == "custom"
        out["ready"] = {"private": base and key["stored"], "tee": base and key["stored"],
                        "standard": base and (key["stored"] if out["provider_key"] is None else provider_ok)}
        return out

    def usage(self) -> dict:
        records = usagelog.read(config.state_dir() / "requests.log")
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
                         "asked_for": r.get("asked_for", ""), "sent": sent, "sealed": sent == "sealed",
                         "status": r.get("status", ""),
                         # no usage (refused, failed, a search): no tokens, rather than none
                         "input": u.get("input", 0) + u.get("cache_read", 0) if u else None,
                         "cached": u.get("cache_read", 0) if u else None, "output": u.get("output", 0) if u else None,
                         "results": r.get("results"),
                         "cost": cost if isinstance(cost, (int, float)) else None, "estimated": bool(u.get("cost_estimated")),
                         "seconds": r.get("seconds"), "error": r.get("error") or r.get("reason") or ""})
        return {"today": round(spent_today, 4), "month": round(spent_month, 4), "requests_today": asked_today,
                "last": rows[-SHOWN:][::-1], "log": str(config.state_dir() / "requests.log")}

    def last_attestation(self) -> dict | None:
        found = None
        for r in usagelog.read(config.state_dir() / "requests.log"):
            if r.get("kind") == "attested" and not r.get("error"):
                found = r
        if not found:
            return None
        return {k: found.get(k) for k in ("t", "summary", "hardware", "release", "latest", "key_sha256", "enclave")}

    def tee_attestations(self) -> list[dict]:
        """Each TEE model's last attestation (the newest first): what was found, or why it didn't hold."""
        last: dict[str, dict] = {}
        for r in usagelog.read(config.state_dir() / "requests.log"):
            if r.get("kind") == "attested_tee" and r.get("model"):
                last[r["model"]] = {k: r.get(k) for k in ("t", "model", "level", "summary", "shortfalls", "error")}
        return sorted(last.values(), key=lambda a: str(a.get("t")), reverse=True)

    # ------------------------------------------------------------------ the keys

    def _provider_of(self, provider: str) -> tuple[str, str]:
        """(provider, its base URL) for a key: a custom one's is the standard profile's endpoint."""
        if provider not in config.PROVIDERS:
            raise UserError(f"There's no provider called {provider!r}.")
        if provider != "custom":
            return provider, ""
        cfg = config.load()
        return provider, cfg.provider_url(cfg.profile("standard"))

    def set_key(self, key: str, provider: str = "nanogpt") -> None:
        from .netpolicy import key_problem
        key = (key or "").strip()
        if not key:
            where = {"nanogpt": " (nano-gpt.com/api)", "openrouter": " (openrouter.ai/settings/keys)"}.get(provider, "")
            raise UserError(f"That isn't an API key: paste the key {config.PROVIDERS.get(provider, 'the provider')} "
                            f"shows you{where}.")
        if key_problem(key):                       # said without anything of the key: it may be most of it
            raise UserError("That isn't an API key: it has a space, a line break or another character no API key "
                            "has, picked up where it was copied from, perhaps. Nothing was stored: copy it again and "
                            "paste it here.")
        creds.set_key(key, *self._provider_of(provider))

    def import_key(self) -> None:
        key = creds.vibe_manager_key()
        if not key:
            raise UserError("DA Vibe Manager has no NanoGPT key in your keyring.")
        creds.set_key(key)

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
                                 "error": str(r.get("error") or "")[:300], "provider": r.get("provider", "")} if r else None,
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
        if p.kind != "standard" and not offers_private_mode(cfg.base_url):
            raise UserError(f"{shown(cfg.base_url)} isn't NanoGPT, so it has no Private Mode or TEE models.")
        caps: dict | None
        if p.kind == "private":
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
            caps, ids = None, await self._listed(url, await in_thread(creds.get_key, "custom", url))
        out = []
        for m in ids:
            c = capabilities.lookup(caps, m) or {} if caps else {}
            price = c.get("pricing")
            markup = capabilities.PRIVATE_MARKUP if p.kind == "private" else 1.0
            out.append({"id": m, "vision": bool(c.get("vision")) if c else None, "tools": bool(c.get("tools")) if c else None,
                        "context": c.get("context"), "included": bool(c.get("included")) and p.provider == "nanogpt",
                        "routable": routes.routable(p.provider, m),
                        "price": {k: round(price[k] * markup, 4) for k in ("input", "cache_read", "output")} if price else None})
        self._models[cache] = (time.monotonic(), out)
        return out

    @staticmethod
    async def _listed(url: str, key: str | None) -> list[str]:
        """A provider of your own: the ids its /models lists (OpenAI's shape), with its key."""
        import httpx
        from .netpolicy import check_url
        check_url(url, "Your provider's base URL")
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            r = await client.get(url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"} if key else {})
            if r.status_code >= 300:            # said without the address's password (httpx's own error quotes it)
                raise UserError(f"{shown(url)} answered HTTP {r.status_code} when asked for its models.")
            data = r.json().get("data") if isinstance(r.json(), dict) else None
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

    async def check(self, model: str = "") -> dict:
        """Attest the private enclave now (or with `model`, a TEE model's), as a session does before it
        sends anything."""
        cfg = config.load()
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

    async def check_tee(self, cfg, model: str) -> dict:
        """A TEE model's enclave, attested now (llm/tee.py): NanoGPT asks for the key to answer."""
        from .proxy import attest_tee
        if not is_tee(model):
            raise UserError(f"{model} isn't one of NanoGPT's TEE models.")
        key = await in_thread(creds.get_key)
        if not key:
            raise UserError("NanoGPT shows a TEE model's attestation only with your API key: store it first (Setup).")
        try:
            att = await in_thread(attest_tee, cfg.base_url, key, model)
        except Exception as e:  # noqa: BLE001 - said as it is
            self.log.write("attested_tee", model=model, error=str(e)[:300])
            raise UserError(f"{model}'s enclave didn't attest: {e}") from None
        self.log.write("attested_tee", model=model, **{k: att.get(k) for k in (
            "level", "summary", "shortfalls", "tcb_status", "gpus", "instances", "signing_address")})
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
