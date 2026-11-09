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


def test_a_name_another_user_holds_is_taken_for_no_copy_of_ours(monkeypatch, capsys):
    """An abstract socket's name has no permissions: another user could bind ours first, and every
    launch then took itself for a second copy, asked the squatter to show its window, and quit."""
    import socket
    import threading

    name = f"\0daprivatecoder-test-squat-{os.getpid()}".encode()
    monkeypatch.setattr(tray, "_socket_name", lambda: name)
    squatter = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    squatter.bind(name)
    squatter.listen(1)
    heard = []

    def answer():
        conn, _ = squatter.accept()
        with conn:
            conn.settimeout(1)
            try:
                heard.append(conn.recv(64))
                conn.sendall(b"window\n")
            except OSError:
                pass
    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    monkeypatch.setattr(tray, "peer_uid", lambda sock: os.getuid() + 1)      # it is someone else's
    try:
        assert tray.claim_single_instance(lambda: "never") == (True, "")
        thread.join(3)
        assert heard in ([], [b""]), "it was asked nothing"
        assert "Another user's program holds DA Private Coder's name" in capsys.readouterr().err
    finally:
        squatter.close()


def test_another_users_program_is_told_nothing_and_shows_nothing(monkeypatch):
    import socket

    monkeypatch.setattr(tray, "_socket_name", lambda: f"\0daprivatecoder-test-other-{os.getpid()}".encode())
    asked = []
    assert tray.claim_single_instance(lambda: asked.append(1) or "window") == (True, "")
    monkeypatch.setattr(tray, "peer_uid", lambda sock: os.getuid() + 1)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
        c.settimeout(2)
        c.connect(tray._socket_name())
        c.sendall(b"show\n")
        try:
            said = c.recv(256)
        except ConnectionResetError:
            said = b""
        assert said == b""                                                 # hung up on, untold
    assert asked == []


def test_the_peer_of_a_unix_socket_is_who_the_kernel_says():
    import socket

    a, b = socket.socketpair()
    with a, b:
        assert tray.peer_uid(a) == tray.peer_uid(b) == os.getuid()


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
