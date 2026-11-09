"""The window's server: the page, and its API (manager.py), on 127.0.0.1 only. Every API call must
carry the token of this launch (X-Token), which only the window's own URL holds, in its fragment. After
DA Vibe Manager's server/app.py.

A web browser is never handed that URL: whatever a browser is started with is on its command line,
which every user of the computer can read. It is opened on a one-time code instead (Launches), which the
page exchanges once for the token (POST /api/launch), as DA Vibe Manager's window is."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import sys
import time
from pathlib import Path
from typing import Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import creds
from .config import ConfigError
from .editor import EditorError
from .manager import Manager, UserError, in_thread
from .netpolicy import scrubbed

WEB_DIR = Path(__file__).resolve().parent / "web"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
# A browser is opened with a one-time code, never the token (app.py browser_opener): this long to be used
LAUNCH_SECONDS = 120
# what a launch code's exchange may send (POST /api/launch, the one call without the token)
LAUNCH_BODY = 1024


def peer_uid(peer: tuple | None, server: tuple | None) -> int | None:
    """The user whose socket at `peer` is connected to `server` on this computer (a loopback TCP
    connection), as the kernel's table of TCP sockets has it; None where that can't be said."""
    if not peer or not server or not all(isinstance(at[1], int) for at in (peer, server)):
        return None

    def written(host: str, port: int) -> str:
        try:
            raw = socket.inet_aton(host)
        except OSError:
            return ""
        return f"{(raw[::-1] if sys.byteorder == 'little' else raw).hex().upper()}:{port:04X}"
    local, remote = written(peer[0], peer[1]), written(server[0], server[1])
    try:
        with open("/proc/net/tcp", encoding="ascii") as f:
            lines = f.read().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) > 7 and fields[1] == local and fields[2] == remote:
            return int(fields[7])
    return None


class Launches:
    """The one-time codes the browser is opened with (http://127.0.0.1:PORT/#c=CODE), each exchanged once
    for the window's token (POST /api/launch). A browser's command line shows every user of this computer
    what it was opened with, and the token in it gave another user the app for as long as it ran (Nov 2026:
    it was ?t=TOKEN). A code is good once, for LAUNCH_SECONDS, and only to a process of this user's: one
    taken first by anyone else would be refused. (DA Vibe Manager's, 41f957d.)"""

    def __init__(self) -> None:
        self._codes: dict[str, float] = {}

    def new(self) -> str:
        code = secrets.token_urlsafe(24)
        now = time.monotonic()
        self._codes = {c: at for c, at in self._codes.items() if now - at < LAUNCH_SECONDS}
        self._codes[code] = now
        return code

    def take(self, code: str, peer: tuple | None = None, server: tuple | None = None) -> bool:
        """Whether `code` is one given out, not used yet, still current, and asked for by this user."""
        found = next((c for c in self._codes if hmac.compare_digest(c.encode(), str(code).encode("utf-8", "replace"))), None)
        if found is None:
            return False
        uid = peer_uid(peer, server)
        if uid is not None and uid != os.getuid():
            return False                    # another user's process: refused, and the code stands for this one's
        at = self._codes.pop(found)
        return time.monotonic() - at < LAUNCH_SECONDS


class _Static(StaticFiles):
    """The page's script and styles, asked for again each time (revalidated, not taken from a cache): a
    browser that kept an older version would show an older window with the newer app."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def create_app(token: str, manager: Manager, desktop=None, on_quit: Callable[[], None] | None = None,
               launches: Launches | None = None) -> Starlette:
    """`desktop` (app.Desktop, in the app window only) opens the folder chooser; `on_quit` ends the app;
    `launches`, the one-time codes a browser is opened with, exchanged for the token (POST /api/launch)."""

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
                return JSONResponse({"error": scrubbed(str(e))}, status_code=400, headers=HEADERS)
            except Exception as e:  # noqa: BLE001 - the window says it, rather than nothing happening
                # an address it quotes never with its password (httpx's errors name the URL they were for)
                return JSONResponse({"error": scrubbed(f"{type(e).__name__}: {e}")}, status_code=500,
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

    async def launch(request: Request) -> Response:
        """The token, once, for the one-time code a browser was opened with (Launches): the one call
        without the token, so nothing more than a code's worth is read of it."""
        try:
            if int(request.headers.get("content-length") or 0) > LAUNCH_BODY:
                return JSONResponse({"error": "request too large"}, status_code=413, headers=HEADERS)
        except ValueError:
            return JSONResponse({"error": "bad Content-Length"}, status_code=400, headers=HEADERS)
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > LAUNCH_BODY:
                return JSONResponse({"error": "request too large"}, status_code=413, headers=HEADERS)
        try:
            code = str(json.loads(body).get("code") or "")
        except (ValueError, AttributeError):
            code = ""
        client = request.client
        if launches is None or not launches.take(code, (client.host, client.port) if client else None,
                                                 request.scope.get("server")):
            return JSONResponse({"error": "This link has been used already, or is too old. Open DA Private Coder "
                                          "again from its menu entry: it opens a new one."}, status_code=403,
                                headers=HEADERS)
        return JSONResponse({"token": token}, headers=HEADERS)

    routes = [
        Route("/", index),
        Route("/api/launch", launch, methods=["POST"]),
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
