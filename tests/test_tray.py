"""The tray: one copy at a time, a second launch bringing the first up, and closing the window hiding it
there only when a tray shows it (and saying, once, that its sessions go on)."""

import os

from daprivatecoder import app, tray
from daprivatecoder.manager import Manager


def test_a_second_launch_brings_the_first_up_and_says_how(monkeypatch):
    monkeypatch.setattr(tray, "_socket_name", lambda: f"\0daprivatecoder-test-{os.getpid()}".encode())
    asked = []
    assert tray.claim_single_instance(lambda: asked.append(1) or "window") == (True, "")
    assert tray.claim_single_instance(lambda: "never") == (False, "window")
    assert asked == [1]


def test_with_no_tray_there_is_none_to_hide_to(monkeypatch):
    def unavailable(self):
        raise ValueError("Namespace not available")
    for kind in ("_xapp", "_ayatana", "_appindicator"):
        monkeypatch.setattr(tray.Tray, kind, unavailable)
    t = tray.Tray(on_open=lambda: None, on_quit=lambda: None)
    assert not t.available and t.kind == ""


class Window:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append(name)


class Shown:
    available = True


def test_closing_hides_to_the_tray_and_says_once_that_sessions_go_on(monkeypatch):
    said = []
    monkeypatch.setattr(tray, "notify", lambda title, body: said.append((title, body)))
    d = app.Desktop()
    d.window = Window()
    d.hide()                                     # no tray: nothing to hide to
    assert d.window.calls == [] and d.visible
    d.tray = Shown()
    d.hide()
    d.show()
    d.hide()
    assert d.window.calls == ["hide", "show", "restore", "hide"] and not d.visible
    assert len(said) == 1 and "still running" in said[0][0] and "Quit" in said[0][1]


def test_the_window_is_told_whether_closing_it_keeps_it_in_the_tray(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    m = Manager()
    monkeypatch.setattr(m, "prefs", lambda: {"theme": "dark", "recent": [], "start": "private"})
    assert m.tray is False
    m.tray = True
    assert m.state()["tray"] is True
