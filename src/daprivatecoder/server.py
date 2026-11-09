"""The window's server: the page, and its API (manager.py), on 127.0.0.1 only. Every API call must
carry the token of this launch (X-Token), which only the window's own URL holds, in its fragment. After
DA Vibe Manager's server/app.py.

A web browser is never handed that URL: whatever a browser is started with is on its command line,
which every user of the computer can read. It is handed a one-time address instead (Tickets), good for
a minute, which only this user's own programs can use, and which sends it on to the window's URL."""

from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
import time
from pathlib import Path
from typing import Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import creds
from .config import ConfigError
from .editor import EditorError
from .manager import Manager, UserError, in_thread
from .netpolicy import addresses_shown

WEB_DIR = Path(__file__).resolve().parent / "web"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
# how long a browser has to open the one-time address it was started with
TICKET_SECONDS = 60
SPENT = ("<!doctype html><meta charset=utf-8><title>DA Private Coder</title><p>This address opens DA Private "
         "Coder once, for a minute, and only for you. Open DA Private Coder again (its menu entry, or "
         "<code>daprivatecoder --browser</code>) to have it opened here.")


class Tickets:
    """One-time addresses for opening the window in a web browser: each good once, for TICKET_SECONDS."""

    def __init__(self, seconds: float = TICKET_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._live: dict[str, float] = {}
        self._seconds = seconds
        self._clock = clock

    def new(self) -> str:
        now = self._clock()
        self._live = {t: until for t, until in self._live.items() if until > now}
        ticket = secrets.token_urlsafe(24)
        self._live[ticket] = now + self._seconds
        return ticket

    def redeem(self, ticket: str) -> bool:
        """Whether `ticket` opens the window now (once: it's spent)."""
        until = self._live.pop(ticket, None)
        return until is not None and self._clock() < until


def _socket_owner(client: tuple[str, int] | None, server: tuple[str, int] | None,
                  tables: tuple[str, ...] = ("/proc/net/tcp", "/proc/net/tcp6")) -> int | None:
    """The user id that owns the TCP connection from `client` to `server` (this computer's own: both
    ends are on it), from the kernel's table of sockets; None if it can't be told."""
    if not client or not server:
        return None

    def address(text: str) -> tuple:
        host, port = text.split(":")
        raw = bytes.fromhex(host)
        # each 32-bit word in the host's byte order (little-endian on x86)
        raw = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
        ip = ipaddress.ip_address(raw)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return ip, int(port, 16)

    want = ((ipaddress.ip_address(client[0]), client[1]), (ipaddress.ip_address(server[0]), server[1]))
    for table in tables:
        try:
            with open(table, encoding="ascii") as f:
                rows = f.read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            try:
                if (address(fields[1]), address(fields[2])) == want:
                    return int(fields[7])
            except (IndexError, ValueError):
                continue
    return None


class _Static(StaticFiles):
    """The page's script and styles, asked for again each time (revalidated, not taken from a cache): a
    browser that kept an older version would show an older window with the newer app."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def create_app(token: str, manager: Manager, desktop=None, on_quit: Callable[[], None] | None = None,
               tickets: Tickets | None = None, owner: Callable[..., int | None] = _socket_owner) -> Starlette:
    """`desktop` (app.Desktop, in the app window only) opens the folder chooser; `on_quit` ends the app;
    `tickets`, the one-time addresses a web browser is started with."""

    def api(handler):
        async def endpoint(request: Request) -> Response:
            given = request.headers.get("x-token")
            # as bytes: a header with characters beyond ASCII is a wrong token, not an error
            if given is None or not hmac.compare_digest(given.encode("utf-8", "surrogateescape"), token.encode()):
                return JSONResponse({"error": "This page isn't DA Private Coder's window."}, status_code=403, headers=HEADERS)
            try:
                body = await request.json() if request.method == "POST" and await request.body() else {}
            except ValueError:
                return JSONResponse({"error": "That wasn't JSON."}, status_code=400, headers=HEADERS)
            if not isinstance(body, dict):
                return JSONResponse({"error": "That wasn't a JSON object."}, status_code=400, headers=HEADERS)
            try:
                result = await handler(request, body)
            except (UserError, ConfigError, EditorError, creds.KeyringUnavailable) as e:
                return JSONResponse({"error": addresses_shown(str(e))}, status_code=400, headers=HEADERS)
            except Exception as e:  # noqa: BLE001 - the window says it, rather than nothing happening
                # an address it quotes never with its password (httpx's errors name the URL they were for)
                return JSONResponse({"error": addresses_shown(f"{type(e).__name__}: {e}")}, status_code=500,
                                    headers=HEADERS)
            return JSONResponse(result if result is not None else {"ok": True}, headers=HEADERS)
        return endpoint

    async def state(request, body):
        return await in_thread(manager.state)

    async def key(request, body):
        provider = str(body.get("provider") or "nanogpt")
        if body.get("from") == "vibe-manager":
            await in_thread(manager.import_key)
        elif body.get("clear"):
            await in_thread(manager.clear_key, provider)
        else:
            await in_thread(manager.set_key, str(body.get("key") or ""), provider)
        return await in_thread(manager.state)

    async def install(request, body):
        await in_thread(manager.install_commands)
        return await in_thread(manager.state)

    async def editor_switch(request, body):
        await in_thread(manager.set_editor, str(body.get("name") or ""), bool(body.get("on")))
        return await in_thread(manager.state)

    async def folder(request, body):
        if "other" in body:
            await in_thread(manager.set_other, str(body.get("other") or ""))
        else:
            await in_thread(manager.set_folder, str(body.get("folder") or ""), str(body.get("profile") or ""))
        return await in_thread(manager.state)

    async def open_editor(request, body):
        await in_thread(manager.open_in_editor, str(body.get("name") or ""), str(body.get("folder") or ""),
                        str(body.get("profile") or "private"))
        return await in_thread(manager.state)

    async def profile(request, body):
        await in_thread(manager.save_profile, str(body.get("profile") or "private"), body.get("fields") or {})
        return await in_thread(manager.state)

    async def theme(request, body):
        await in_thread(manager.set_theme, str(body.get("theme") or ""))
        return {"ok": True}

    async def models(request, body):
        return {"models": await manager.models(str(request.query_params.get("profile") or "private"),
                                               fresh=bool(request.query_params.get("fresh")))}

    async def hosts(request, body):
        return await manager.hosts(str(body.get("profile") or "standard"), str(body.get("model") or ""))

    async def route(request, body):
        await in_thread(manager.save_route, str(body.get("profile") or "standard"), str(body.get("model") or ""),
                        body.get("route"))
        return await in_thread(manager.state)

    async def start(request, body):
        await in_thread(manager.set_start, str(body.get("session") or ""))
        return await in_thread(manager.state)

    async def check(request, body):
        return {"attestation": await manager.check(str(body.get("model") or ""))}

    async def open_folder(request, body):
        await in_thread(manager.open, str(body.get("folder") or ""), str(body.get("profile") or "private"))
        return await in_thread(manager.state)

    async def forget(request, body):
        await in_thread(manager.forget_folder, str(body.get("folder") or ""))
        return await in_thread(manager.state)

    async def choose(request, body):
        if desktop is None:
            raise UserError("Type the folder's path: there's no folder chooser in the browser.")
        return {"folder": await in_thread(desktop.choose_folder, str(body.get("start") or ""))}

    async def quit_app(request, body):
        if on_quit is not None:
            on_quit()
        return {"ok": True}

    async def index(request: Request) -> Response:
        return FileResponse(WEB_DIR / "index.html", headers=HEADERS)

    async def opened(request: Request) -> Response:
        """A browser started with a one-time address: sent on to the window, its token in the fragment
        (which the browser never sends anywhere). Only this user's own programs may use one: another
        user's, who could read it on the browser's command line, is turned away and doesn't spend it."""
        ticket = request.path_params["ticket"]
        server = request.scope.get("server")
        who = owner(request.client and (request.client.host, request.client.port), server and tuple(server))
        if tickets is None or (who is not None and who != os.getuid()) or not tickets.redeem(ticket):
            return HTMLResponse(SPENT, status_code=403, headers=HEADERS)
        return RedirectResponse(f"/#t={token}", status_code=303, headers=HEADERS)

    routes = [
        Route("/", index),
        Route("/open/{ticket}", opened),
        Route("/api/state", api(state)),
        Route("/api/key", api(key), methods=["POST"]),
        Route("/api/commands", api(install), methods=["POST"]),
        Route("/api/editor", api(editor_switch), methods=["POST"]),
        Route("/api/folder", api(folder), methods=["POST"]),
        Route("/api/open-editor", api(open_editor), methods=["POST"]),
        Route("/api/profile", api(profile), methods=["POST"]),
        Route("/api/theme", api(theme), methods=["POST"]),
        Route("/api/hosts", api(hosts), methods=["POST"]),
        Route("/api/route", api(route), methods=["POST"]),
        Route("/api/start", api(start), methods=["POST"]),
        Route("/api/models", api(models)),
        Route("/api/check", api(check), methods=["POST"]),
        Route("/api/open", api(open_folder), methods=["POST"]),
        Route("/api/forget", api(forget), methods=["POST"]),
        Route("/api/choose-folder", api(choose), methods=["POST"]),
        Route("/api/quit", api(quit_app), methods=["POST"]),
        Mount("/static", _Static(directory=WEB_DIR)),
    ]
    return Starlette(routes=routes)
