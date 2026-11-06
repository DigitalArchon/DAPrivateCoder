"""What the window shows and does: the state of everything DA Private Coder needs (the key, Claude
Code, the commands, the editor switch, the private profile's models), what it cost, the enclave's
last attestation, and the user's actions on them. The window's server (server.py) calls these; they
do what the commands do (cli.py), on the same files."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, replace
from pathlib import Path

from . import __version__, commands, config, creds, editor, hostenv, launch, usagelog
from .config import ConfigError
from .llm import capabilities
from .llm.private_mode import Enclave, list_private_models, offers_private_mode, relay_url

PROFILE_FIELDS = ("model", "opus_model", "fast_model", "vision_model", "reasoning", "web_search")
THEMES = ("dark", "light", "system")
RECENT = 8                                  # folders remembered for "Open Claude Code"
MODELS_FOR = 10 * 60                        # the list of private models is read again after this
SHOWN = 40                                  # requests shown in Usage


class UserError(Exception):
    """Something the user can fix, said as it is."""


def prefs_path() -> Path:
    return config.config_dir() / "window.json"


class Manager:
    def __init__(self, ui_notice: str = "", can_choose_folder: bool = False):
        self.ui_notice = ui_notice
        self.can_choose_folder = can_choose_folder
        self._models: tuple[float, list[dict]] | None = None
        self.log = usagelog.UsageLog(config.state_dir() / "requests.log", "window")

    # ------------------------------------------------------------------ what there is

    def prefs(self) -> dict:
        try:
            data = json.loads(prefs_path().read_text())
        except (OSError, ValueError):
            data = {}
        return {"theme": data.get("theme") if data.get("theme") in THEMES else "dark",
                "recent": [f for f in data.get("recent", []) if isinstance(f, str)][:RECENT]}

    def _save_prefs(self, prefs: dict) -> None:
        path = prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(prefs, indent=1))
        os.replace(tmp, path)

    def state(self) -> dict:
        """Everything the window shows (blocking: the keyring is asked)."""
        from . import launcher
        out: dict = {"version": __version__, "ui_notice": self.ui_notice, "appimage": bool(os.environ.get("APPIMAGE")),
                     "can_choose_folder": self.can_choose_folder, **self.prefs()}
        try:
            cfg = config.load()
            out["config"] = {"base_url": cfg.base_url, "path": str(cfg.path),
                             "profiles": {n: asdict(p) for n, p in cfg.profiles.items()}}
            out["config_error"] = ""
        except ConfigError as e:
            cfg, out["config"], out["config_error"] = None, None, str(e)
        key: dict = {"stored": False, "ending": "", "error": "", "vibe_manager": False}
        try:
            stored = creds.get_key()
            key.update(stored=bool(stored), ending=stored[-4:] if stored else "")
            key["vibe_manager"] = not stored and bool(creds.vibe_manager_key())
        except creds.KeyringUnavailable as e:
            key["error"] = str(e)
        out["key"] = key
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
            ours = isinstance(value, str) and os.path.basename(value) == "claude-private"
            out["editors"].append({"name": name, "path": str(path), "value": value or "",
                                   "state": "on" if ours else "other" if value else "off"})
        out["usage"] = self.usage()
        out["attestation"] = self.last_attestation()
        out["ready"] = bool(cfg) and key["stored"] and bool(out["claude"]["path"])
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
            rows.append({"t": str(r.get("t", ""))[:19].replace("T", " "), "kind": kind,
                         "what": r.get("provider") if kind == "search" else r.get("model", ""),
                         "asked_for": r.get("asked_for", ""), "sealed": kind != "search" and bool(r.get("sealed", kind == "vision")),
                         "status": r.get("status", ""), "input": u.get("input", 0) + u.get("cache_read", 0),
                         "cached": u.get("cache_read", 0), "output": u.get("output", 0), "results": r.get("results"),
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

    # ------------------------------------------------------------------ the key

    def set_key(self, key: str) -> None:
        key = (key or "").strip()
        if not key or any(c.isspace() for c in key):
            raise UserError("That isn't an API key: paste the key NanoGPT shows you (nano-gpt.com/api).")
        creds.set_key(key)

    def import_key(self) -> None:
        key = creds.vibe_manager_key()
        if not key:
            raise UserError("DA Vibe Manager has no NanoGPT key in your keyring.")
        creds.set_key(key)

    def clear_key(self) -> None:
        creds.delete_key()

    # ------------------------------------------------------------------ commands and the editor

    def install_commands(self) -> dict:
        done = commands.install()
        taken = [commands.path_of(n) for n, s in done.items() if s == "taken"]
        if taken:
            raise UserError(f"{', '.join(map(str, taken))} is someone else's program, so it was left as it is.")
        return done

    def set_editor(self, name: str, on: bool) -> None:
        found = dict(editor.editors())
        if name not in found:
            raise UserError(f"{name}'s settings weren't found here.")
        path = found[name]
        value = editor.current(path)
        if value and os.path.basename(value) != "claude-private":
            raise UserError(f"{name}'s Claude Code already runs through another program ({value}): it was left as it is.")
        editor.switch(path, editor.wrapper_path() if on else None)

    # ------------------------------------------------------------------ settings

    def save_profile(self, name: str, fields: dict) -> None:
        cfg = config.load()
        profile = cfg.profile(name)
        change = {}
        for key in PROFILE_FIELDS:
            if key in fields:
                if not isinstance(fields[key], str):
                    raise UserError(f"{key} must be text.")
                change[key] = fields[key].strip()
        config.save(cfg.with_profile(replace(profile, **change)))

    def set_theme(self, theme: str) -> None:
        if theme not in THEMES:
            raise UserError(f"The colours are one of {', '.join(THEMES)}.")
        prefs = self.prefs()
        prefs["theme"] = theme
        self._save_prefs(prefs)

    # ------------------------------------------------------------------ NanoGPT: models and the enclave

    async def models(self, fresh: bool = False) -> list[dict]:
        """NanoGPT's Private Mode models now (public lists: no key is sent), with what they can do and cost."""
        if self._models and not fresh and time.monotonic() - self._models[0] < MODELS_FOR:
            return self._models[1]
        cfg = config.load()
        if not offers_private_mode(cfg.base_url):
            raise UserError(f"{cfg.base_url} isn't NanoGPT, so it has no Private Mode models.")
        ids = await list_private_models(cfg.base_url)
        try:
            caps = await capabilities.fetch_nanogpt(cfg.base_url)
        except Exception:  # noqa: BLE001 - listed without what they can do
            caps = {}
        out = []
        for m in ids:
            c = capabilities.lookup(caps, m) or {}
            price = c.get("pricing")
            out.append({"id": m, "vision": bool(c.get("vision")), "tools": bool(c.get("tools")) if c else None,
                        "context": c.get("context"),
                        "price": {k: round(price[k] * capabilities.PRIVATE_MARKUP, 4)
                                  for k in ("input", "cache_read", "output")} if price else None})
        self._models = (time.monotonic(), out)
        return out

    async def check(self) -> dict:
        """Attest the enclave now, as a session does before it sends anything."""
        cfg = config.load()
        if not offers_private_mode(cfg.base_url):
            raise UserError(f"{cfg.base_url} isn't NanoGPT, so it has no Private Mode.")
        try:
            att = (await Enclave(relay_url(cfg.base_url)).current()).attestation
        except Exception as e:  # noqa: BLE001 - said as it is
            self.log.write("attested", error=str(e)[:300])
            raise UserError(f"The enclave couldn't be verified: {e}") from None
        self.log.write("attested", enclave=att.enclave, hardware=att.hardware, release=att.release_tag,
                       digest=att.release_digest, latest=att.latest_release, latest_checked=att.latest_checked,
                       key_sha256=att.hpke_key_sha256, summary=att.summary)
        return self.last_attestation() or {}

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
