"""The window: the local server (server.py) shown in a small app window (pywebview on the host's
GTK and WebKitGTK), or in the web browser when there's no WebKitGTK or no display. After DA Vibe
Manager's app.py. Closing the window hides it to the tray (tray.py), where there is one: DA Private
Coder is still open, so the sessions on it go on until Quit. With no tray, closing it quits."""

from __future__ import annotations

import argparse
import os
import secrets
import shlex
import signal
import socket
import sys
import threading
import time
from pathlib import Path

from .hostenv import HOST_TYPELIB_DIRS, set_for_self

NAME = "DA Private Coder"
DESKTOP_ID = "daprivatecoder"
WEBKIT_INSTALL = ("Ubuntu, Mint, Debian: sudo apt install gir1.2-webkit2-4.1 · Fedora: sudo dnf install webkit2gtk4.1 · "
                  "Arch, CachyOS: sudo pacman -S webkit2gtk-4.1")
WINDOW_SIZE = (560, 860)
MIN_SIZE = (400, 480)
SCREEN_MARGIN = (40, 100)            # for the panel, the title bar and some desk around it
BACKGROUND = {"dark": "#111418", "light": "#f4f6f9", "system": "#111418"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Desktop:
    """What the page can't do itself inside the app window: the folder chooser (GTK's, on the main
    thread). The page reaches it through the server (token only): pywebview's own JS bridge builds
    its functions with `new Function`, which the page's Content-Security-Policy rightly refuses.
    Also the window's place in the tray: shown, or hidden there."""

    def __init__(self):
        self.window = None
        self.tray = None
        self.visible = True
        self._told = False

    @property
    def has_tray(self) -> bool:
        return bool(self.tray and self.tray.available)

    def show(self) -> None:
        """On the GTK main thread."""
        if self.window is not None:
            self.window.show()
            self.window.restore()
            self.visible = True

    def hide(self) -> None:
        """To the tray (on the GTK main thread). The first time, a notification says what it means: the
        sessions on DA Private Coder go on, which a closed window would otherwise suggest they don't."""
        if self.window is None or not self.has_tray:
            return
        self.window.hide()
        self.visible = False
        if not self._told:
            self._told = True
            from .tray import notify
            notify("DA Private Coder is still running in the tray",
                   "Sessions using it keep working. To stop them, choose Quit from its tray icon.")

    def choose_folder(self, start: str = "") -> str:
        """The folder the user picks ("" if they cancel). Blocks until they do."""
        from gi.repository import GLib, Gtk

        result: dict = {}
        done = threading.Event()

        def run():
            try:
                dialog = Gtk.FileChooserDialog(title="The folder to work in", action=Gtk.FileChooserAction.SELECT_FOLDER)
                dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.ACCEPT)
                dialog.set_modal(True)
                if start and os.path.isdir(start):
                    dialog.set_current_folder(start)
                if dialog.run() == Gtk.ResponseType.ACCEPT:
                    result["folder"] = dialog.get_filename() or ""
                dialog.destroy()
            finally:
                done.set()
            return False

        GLib.idle_add(run)
        done.wait()
        return result.get("folder", "")


def window_size(screen: tuple[int, int] | None = None) -> tuple[int, int]:
    """The window's size, fitted to `screen` (its monitor's geometry: read here when not given)."""
    width, height = WINDOW_SIZE
    if screen is None:
        try:
            from gi.repository import Gdk
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            geometry = monitor.get_geometry()
            screen = (geometry.width, geometry.height)
        except Exception:  # noqa: BLE001 - no display to ask: the size as it is
            screen = None
    if screen:
        width, height = min(width, screen[0] - SCREEN_MARGIN[0]), min(height, screen[1] - SCREEN_MARGIN[1])
    return max(width, MIN_SIZE[0]), max(height, MIN_SIZE[1])


def desktop_id() -> str:
    """The name the window identifies itself by (X11 WM_CLASS, Wayland app_id), which desktops match to
    an installed .desktop file for its icon. AppImage integrators (Gear Lever, AppImageLauncher) install
    our entry under a name of their own: running from an AppImage, the name of the installed entry that
    launches it; otherwise "daprivatecoder". From DA Vibe Manager's app.py."""
    appimage = os.environ.get("APPIMAGE")
    if not appimage:
        return DESKTOP_ID
    target = os.path.realpath(appimage)
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    dirs = [data_home] + (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    for base in dict.fromkeys(d for d in dirs if d):
        apps = Path(base) / "applications"
        try:
            entries = sorted(apps.rglob("*.desktop"))
        except OSError:
            continue
        for f in entries:
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key == "TryExec" and value:
                    program = value
                elif key == "Exec" and value:
                    try:
                        program = shlex.split(value)[0]
                    except (ValueError, IndexError):
                        continue
                else:
                    continue
                if os.path.realpath(os.path.expanduser(program)) == target:
                    return str(f.relative_to(apps))[:-len(".desktop")].replace("/", "-")
    return DESKTOP_ID


def window_icon() -> str | None:
    """The icon file for the window itself (X11 shows it; Wayland takes the desktop entry's)."""
    appdir = os.environ.get("APPDIR")
    for path in ([Path(appdir) / "daprivatecoder.png"] if appdir else []) + [Path(__file__).parent / "web" / "icon.svg"]:
        if not path.is_file():
            continue
        try:
            import gi
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf
            GdkPixbuf.Pixbuf.new_from_file(str(path))    # GTK would refuse a file it can't load
            return str(path)
        except Exception:  # noqa: BLE001 - no loader for it: try the next, or go without
            continue
    return None


def _bundled_girepository() -> None:
    """Fill gaps in the host's GObject introspection from the copies bundled in the AppImage (outside
    it there are none, and this does nothing); the host's own always win. As DA Vibe Manager does:
    PyGObject is built against girepository-2.0, which Debian and Ubuntu package apart from GLib, and
    GTK's typelibs refer to base ones that Arch packages apart."""
    import ctypes

    fallback = Path(sys.executable).resolve().parents[2] / "lib/girepository-fallback"
    if not fallback.is_dir():
        return
    try:
        ctypes.CDLL("libgirepository-2.0.so.0")
    except OSError:
        ctypes.CDLL(str(fallback / "libgirepository-2.0.so.0"), mode=ctypes.RTLD_GLOBAL)
    missing = [str(d) for d in sorted((fallback / "typelibs").glob("*"))
               if not any(os.path.exists(f"{h}/{d.name}.typelib") for h in HOST_TYPELIB_DIRS)]
    if missing:
        current = os.environ.get("GI_TYPELIB_PATH")
        set_for_self("GI_TYPELIB_PATH", ":".join(missing + ([current] if current else [])))


def open_in_browser(base: str, tickets, opener=None) -> tuple[str, bool]:
    """The window, in the web browser: started with a one-time address (server.Tickets), never the
    window's own, whose token anyone could read on the browser's command line (as on every other
    process's). Returns the address it was given, and whether a browser could be started."""
    if opener is None:
        import webbrowser
        opener = webbrowser.open
    address = f"{base}/open/{tickets.new()}"
    return address, opener(address) is not False


def window_problem() -> str | None:
    """Why the app window can't open here (no WebKitGTK, no display), or None if it can."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return "There is no graphical display."
    try:
        _bundled_girepository()
        import gi
        from gi.repository import GLib

        GLib.set_prgname(desktop_id())       # before Gtk: its class and app_id, for the desktop's icon
        GLib.set_application_name(NAME)
        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gtk, WebKit2  # noqa: F401
    except Exception as e:  # noqa: BLE001 - a missing typelib, an old GLib: all mean "no window"
        return f"The app window needs WebKitGTK 4.1, which isn't installed ({e}). Install it ({WEBKIT_INSTALL})"
    return None


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from . import commands
    from .manager import Manager
    from .server import Tickets, create_app

    ap = argparse.ArgumentParser(prog="daprivatecoder", description=f"{NAME}: Claude Code on NanoGPT's private models")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--browser", action="store_true", help="open in the web browser instead of the app window")
    mode.add_argument("--window", action="store_true", help="open the app window (the default)")
    ap.add_argument("--no-open", action="store_true", help="with the browser: only print the URL")
    ap.add_argument("--port", type=int, default=0, help="port to listen on (default: random)")
    args = ap.parse_args(argv)

    # one copy at a time: a second launch (the menu, while this one is in the tray) brings this one up
    from . import tray as tray_mod
    shown: dict = {}

    def show_from_elsewhere() -> str:
        if shown.get("show"):
            return shown["show"]()
        return "starting"
    first, said = tray_mod.claim_single_instance(show_from_elsewhere)
    if not first:
        print({"window": f"{NAME} is already running: its window was brought up.",
               "browser": f"{NAME} is already running: it was opened in your web browser."}.get(
            said, f"{NAME} is already running (with --no-open, at the address it printed)."), file=sys.stderr)
        return

    token = secrets.token_urlsafe(32)
    port = args.port or _free_port()
    use_browser = args.browser or args.no_open
    notice = ""
    if not use_browser:
        problem = window_problem()
        if problem:
            use_browser, notice = True, f"{problem}. Running in your web browser until then."
            print(notice, file=sys.stderr)
    desktop = None if use_browser else Desktop()
    # DA Private Coder is open: sessions may send while this process runs, however it ends (gate.py)
    from . import gate
    gate.hold()
    manager = Manager(ui_notice=notice, can_choose_folder=desktop is not None)
    try:
        commands.refresh()                   # an AppImage that moved: the commands it installed follow it
    except OSError:
        pass

    def quit_all():
        server.should_exit = True
        if desktop is not None and desktop.window is not None:
            from gi.repository import GLib
            GLib.idle_add(lambda: (desktop.window.destroy(), False)[1])

    tickets = Tickets()
    app = create_app(token, manager, desktop, on_quit=lambda: quit_all(), tickets=tickets)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", http="h11",
                                           ws="none", loop="asyncio", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            raise SystemExit("The window's server failed to start.")
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    # the token in the fragment, which a browser never sends anywhere (nor keeps in a server's logs); the
    # page moves it out of the address bar (web/app.js)
    url = f"{base}/#t={token}"

    try:
        if use_browser:
            told = f"{NAME} is running. Open this address (keep it to yourself: it opens the app):\n{url}"
            if args.no_open or sys.stdout.isatty():
                print(told, flush=True)
            else:
                print(f"{NAME} is running, in your web browser.", flush=True)     # stdout may be the session's log
            if not args.no_open:
                if not open_in_browser(base, tickets)[1] and not sys.stdout.isatty():
                    print(f"No web browser could be started. {told}", flush=True)      # the only way in, then

                def open_again() -> str:
                    open_in_browser(base, tickets)
                    return "browser"
                shown["show"] = open_again
            while thread.is_alive():          # until Quit in the page, or Ctrl+C
                thread.join(0.5)
        else:
            import webview
            from gi.repository import GLib

            theme = manager.prefs()["theme"]
            width, height = window_size()
            window = webview.create_window(NAME, f"{base}/?desktop=1#t={token}", width=width, height=height, min_size=MIN_SIZE,
                                           text_select=True, background_color=BACKGROUND.get(theme, "#111418"))
            desktop.window = window

            def on_closing():
                if desktop.has_tray and not server.should_exit:
                    desktop.hide()
                    return False                 # still open, in the tray: its sessions go on
                server.should_exit = True
                return True
            window.events.closing += on_closing

            def show_window() -> str:
                GLib.idle_add(lambda: (desktop.show(), False)[1])
                return "window"

            def on_started():
                desktop.tray = tray_mod.Tray(on_open=desktop.show, on_quit=quit_all)
                manager.tray = desktop.has_tray
                shown["show"] = show_window

                def close(*_):
                    quit_all()
                    return False
                for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, close)

            webview.start(on_started, gui="gtk", private_mode=True, icon=window_icon())
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=10)
