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


def test_the_browser_the_app_opens_gets_the_desktops_choice_and_the_app_keeps_its_own(monkeypatch):
    """webbrowser starts the browser with this process's environment, GTK_USE_PORTAL=0 among it, which the
    browser's own dialogs would then follow: the browser gets what the desktop asked for (hostenv.open_url),
    and the app's own 0 stands again once it has started. The cross-app standard, Nov 2026."""
    import webbrowser

    monkeypatch.setattr(hostenv, "ORIGINAL", {})
    monkeypatch.setenv("GTK_USE_PORTAL", "1")
    app.own_dialogs()
    seen = []
    monkeypatch.setattr(webbrowser, "open", lambda url: seen.append((url, app.os.environ.get("GTK_USE_PORTAL"))) or True)
    opener = app.browser_opener(4321, type("L", (), {"new": staticmethod(lambda: "code")})())
    assert opener() is True
    assert seen == [("http://127.0.0.1:4321/#c=code", "1")]
    assert app.os.environ["GTK_USE_PORTAL"] == "0"
    # one that failed to start puts it back too
    monkeypatch.setattr(webbrowser, "open", lambda url: (_ for _ in ()).throw(OSError("no browser")))
    try:
        hostenv.open_url("http://127.0.0.1:1/")
    except OSError:
        pass
    assert app.os.environ["GTK_USE_PORTAL"] == "0"
