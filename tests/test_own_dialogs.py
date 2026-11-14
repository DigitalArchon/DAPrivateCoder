"""DA Private Coder isn't dumpable, and xdg-desktop-portal refuses a process that isn't: where the
desktop asks GTK for the portal's dialogs, the app's own process asks for GTK's, and what it starts
keeps the desktop's choice."""

from daprivatecoder import app, hostenv


def test_the_portal_is_asked_for_by_nothing_of_the_apps_own(monkeypatch):
    monkeypatch.setattr(hostenv, "ORIGINAL", {})
    monkeypatch.setenv("GTK_USE_PORTAL", "1")
    app.own_dialogs()
    assert app.os.environ["GTK_USE_PORTAL"] == "0"
    assert hostenv.host_env()["GTK_USE_PORTAL"] == "1"


def test_a_desktop_that_asks_for_no_portal_is_left_as_it_is(monkeypatch):
    monkeypatch.setattr(hostenv, "ORIGINAL", {})
    monkeypatch.delenv("GTK_USE_PORTAL", raising=False)
    app.own_dialogs()
    assert "GTK_USE_PORTAL" not in app.os.environ
    assert "GTK_USE_PORTAL" not in hostenv.host_env()
