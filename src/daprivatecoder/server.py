"""The window's server: the page, and its API (manager.py), on 127.0.0.1 only. Every API call must
carry the token of this launch (X-Token), which only the window's own URL holds. After DA Vibe
Manager's server/app.py."""

from __future__ import annotations

import hmac
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

WEB_DIR = Path(__file__).resolve().parent / "web"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}


class _Static(StaticFiles):
    """The page's script and styles, asked for again each time (revalidated, not taken from a cache): a
    browser that kept an older version would show an older window with the newer app."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def create_app(token: str, manager: Manager, desktop=None, on_quit: Callable[[], None] | None = None) -> Starlette:
    """`desktop` (app.Desktop, in the app window only) opens the folder chooser; `on_quit` ends the app."""

    def api(handler):
        async def endpoint(request: Request) -> Response:
            given = request.headers.get("x-token")
            if given is None or not hmac.compare_digest(given, token):
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
                return JSONResponse({"error": str(e)}, status_code=400, headers=HEADERS)
            except Exception as e:  # noqa: BLE001 - the window says it, rather than nothing happening
                return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500, headers=HEADERS)
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
        await in_thread(manager.set_editor, str(body.get("name") or ""), bool(body.get("on")),
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

    routes = [
        Route("/", index),
        Route("/api/state", api(state)),
        Route("/api/key", api(key), methods=["POST"]),
        Route("/api/commands", api(install), methods=["POST"]),
        Route("/api/editor", api(editor_switch), methods=["POST"]),
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
