"""The tray icon, and making sure only one copy of the app runs. After DA Vibe Manager's tray.py.

The icon is an XApp StatusIcon where the desktop has XApp (Cinnamon, MATE, Xfce on Mint: a left
click opens the window), otherwise an Ayatana AppIndicator (a menu, as on KDE, and GNOME with the
AppIndicator extension), or the original AppIndicator with the same API (what Arch and CachyOS ship).
An AppIndicator counts only if something shows it: a desktop with no tray (GNOME without the
extension) would otherwise hide the window to an icon nobody can see. With none, there is no tray
and closing the window quits the app, as before.

A second launch (from the menu, say, while the window is in the tray) finds the first through an
abstract Unix socket named for this user and asks it to show its window, then exits.
"""

from __future__ import annotations

import os
import socket
import threading
from pathlib import Path
from typing import Callable

NAME = "DA Private Coder"
ICON = Path(__file__).parent / "web" / "icon.svg"


def _socket_name() -> bytes:
    return f"\0daprivatecoder-{os.getuid()}".encode()


def claim_single_instance(on_show: Callable[[], str]) -> tuple[bool, str]:
    """(True, "") if this is the only copy: it then listens for later launches, and answers each with
    what on_show() did. (False, what the running copy did) if another copy is running, which has been
    asked to show its window."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(_socket_name())
    except OSError:
        srv.close()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                c.settimeout(2)
                c.connect(_socket_name())
                c.sendall(b"show\n")
                try:
                    said = c.recv(256).decode("utf-8", "replace").strip()
                except OSError:
                    said = ""
            return False, said
        except OSError:
            return True, ""          # a stale name nobody answers on: carry on (we just won't be found)
    srv.listen(4)

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(2)
                try:
                    if conn.recv(64).startswith(b"show"):
                        conn.sendall((on_show() or "").encode() + b"\n")
                except OSError:
                    continue
    threading.Thread(target=serve, daemon=True, name="single-instance").start()
    return True, ""


def _tray_host() -> bool:
    """Whether something on the desktop shows AppIndicators (a StatusNotifierWatcher on the session bus)."""
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
                          GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)), GLib.VariantType("(b)"),
                          Gio.DBusCallFlags.NONE, 1000, None)
    return bool(reply.unpack()[0])


class Tray:
    """A tray icon with Open / Quit. Made on the GTK main thread, and its callbacks run there."""

    def __init__(self, *, on_open: Callable[[], None], on_quit: Callable[[], None]):
        self.on_open, self.on_quit = on_open, on_quit
        self.kind = ""
        self._icon = None
        for make in (self._xapp, self._ayatana, self._appindicator):
            try:
                if make():
                    break
            except Exception:  # noqa: BLE001 - this kind isn't available here: try the next
                continue

    @property
    def available(self) -> bool:
        return bool(self.kind)

    def _menu(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        menu = Gtk.Menu()
        for label, fn in ((f"Open {NAME}", self.on_open), (None, None), ("Quit (stops its sessions)", self.on_quit)):
            item = Gtk.SeparatorMenuItem() if label is None else Gtk.MenuItem(label=label)
            if fn:
                item.connect("activate", lambda _w, f=fn: f())
            menu.append(item)
        menu.show_all()
        return menu

    def _xapp(self) -> bool:
        import gi
        gi.require_version("XApp", "1.0")
        from gi.repository import XApp
        if not XApp.StatusIcon.any_monitors():
            return False
        icon = XApp.StatusIcon()
        icon.set_name("daprivatecoder")
        icon.set_icon_name(str(ICON))
        icon.set_tooltip_text(NAME)
        icon.set_secondary_menu(self._menu())

        def activate(_icon, button, _time):
            if button == 1:
                self.on_open()
        icon.connect("activate", activate)
        icon.set_visible(True)
        self._icon, self.kind = icon, "xapp"
        return True

    def _ayatana(self) -> bool:
        return self._indicator("AyatanaAppIndicator3", "ayatana")

    def _appindicator(self) -> bool:
        # the original libappindicator, with the same API: what Arch and CachyOS ship (KDE's tray
        # speaks its protocol, StatusNotifierItem)
        return self._indicator("AppIndicator3", "appindicator")

    def _indicator(self, module: str, kind: str) -> bool:
        import importlib
        import gi
        gi.require_version(module, "0.1")
        AI = importlib.import_module(f"gi.repository.{module}")
        if not _tray_host():
            return False
        ind = AI.Indicator.new("daprivatecoder", str(ICON), AI.IndicatorCategory.APPLICATION_STATUS)
        ind.set_title(NAME)
        menu = self._menu()
        ind.set_menu(menu)
        ind.set_secondary_activate_target(menu.get_children()[0])   # middle click opens the window
        ind.set_status(AI.IndicatorStatus.ACTIVE)
        self._icon, self.kind = ind, kind
        return True


def notify(title: str, body: str) -> bool:
    """A desktop notification (libnotify): whether it was shown (not when there's no notification service)."""
    try:
        import gi
        gi.require_version("Notify", "0.7")
        from gi.repository import GLib, Notify
        if not Notify.is_initted():
            Notify.init(NAME)
        n = Notify.Notification.new(title, body, str(ICON))
        # our own desktop entry (the window's): libnotify would name the default GApplication's instead,
        # and pywebview's has no id, which makes a hint of nothing and the notification fail
        n.set_hint("desktop-entry", GLib.Variant("s", GLib.get_prgname() or "daprivatecoder"))
        return bool(n.show())
    except Exception:  # noqa: BLE001 - a missed notification is no reason to interrupt anything
        return False
