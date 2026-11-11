"""Smoke test for an extracted AppImage, run by the AppImage's own Python inside a distro
container with a virtual display (see packaging/test-appimage.sh). After DA Vibe Manager's.

    smoke.py <AppDir> <out-dir> <name> fallback   WebKitGTK absent: opens in the browser, says why
    smoke.py <AppDir> <out-dir> <name> full       WebKitGTK present: the app window, the API, the
                                                  commands, the proxy, the enclave's attestation
                                                  (over the network), and a screenshot of the page"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

APPDIR, OUT, NAME, MODE = sys.argv[1:5]
ENV = {**os.environ, "HOME": "/tmp/smoke/home", "XDG_CONFIG_HOME": "/tmp/smoke/config",
       "XDG_STATE_HOME": "/tmp/smoke/state", "XDG_DATA_HOME": "/tmp/smoke/data"}
os.makedirs(ENV["HOME"], exist_ok=True)


def start(*args: str) -> tuple[subprocess.Popen, str, str]:
    """Run the app, return (process, base URL, token): from the address it prints (--no-open), or as a
    browser's page gets it, for the one-time code it is opened on ($BROWSER=echo prints the address),
    exchanged once (POST /api/launch)."""
    proc = subprocess.Popen([f"{APPDIR}/AppRun", "--port", "8790", *args], env=ENV, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:8790"
    for line in proc.stdout:
        print(f"  | {line.rstrip()}")
        if line.startswith("http://") and "#t=" in line:
            return proc, base, line.strip().split("#t=", 1)[1]
        if line.startswith("http://") and "#c=" in line:
            code = line.strip().split("#c=", 1)[1]
            asked = urllib.request.Request(base + "/api/launch", method="POST", headers={"Content-Type": "application/json"},
                                           data=json.dumps({"code": code}).encode())
            token = json.load(urllib.request.urlopen(asked, timeout=30)).get("token", "")
            try:
                urllib.request.urlopen(asked, timeout=30)
                again = 200
            except urllib.error.HTTPError as refused:
                again = refused.code
            check(bool(token) and again == 403, f"{NAME}: the browser's one-time code opens the window, once")
            return proc, base, token
    raise SystemExit("the app exited before printing its URL")


def api(base: str, token: str, method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(base + path, method=method, headers={"X-Token": token, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    return json.load(urllib.request.urlopen(req, timeout=30))


def check(ok: bool, what: str) -> None:
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


def run(*args: str, stdin: str = "", timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run([f"{APPDIR}/AppRun", *args], env=ENV, input=stdin, capture_output=True, text=True,
                          timeout=timeout)


if MODE == "fallback":
    # webbrowser honours $BROWSER: "echo" stands in for opening a browser
    proc, base, token = start()
    st = api(base, token, "GET", "/api/state")
    check("WebKitGTK" in st["ui_notice"] and "Running in your web browser" in st["ui_notice"],
          f"{NAME}: without WebKitGTK it opens in the browser and says why")
    api(base, token, "POST", "/api/quit", {})
    proc.wait(10)
    check(proc.returncode is not None, f"{NAME}: Quit ends the process")
    sys.exit(0)

# --- full: the app window first
win = subprocess.Popen([f"{APPDIR}/AppRun"], env=ENV, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(8)
alive = win.poll() is None
win.terminate()
log = win.communicate(timeout=10)[0]
ok = alive and "Traceback" not in log and "Running in your web browser" not in log
check(ok, f"{NAME}: the app window starts and stays up" + ("" if ok else "\n" + "\n".join(f"  | {x}" for x in log.splitlines()[-15:])))

# the API, as the window drives it: not set up (no keyring in the container), the profile saved
proc, base, token = start("--no-open")
st = api(base, token, "GET", "/api/state")
profiles = st["config"]["profiles"]
check(not any(st["ready"].values()) and profiles["private"]["model"] == "private/glm-5-3"
      and profiles["tee"]["kind"] == "tee" and profiles["standard"]["kind"] == "standard",
      f"{NAME}: the API answers, not set up yet, with private, TEE and standard sessions")
api(base, token, "POST", "/api/profile", {"profile": "private", "fields": {"model": "private/kimi-k3-1-pro", "web_search": "best"}})
p = api(base, token, "GET", "/api/state")["config"]["profiles"]["private"]
check(p["model"] == "private/kimi-k3-1-pro" and p["web_search"] == "best", f"{NAME}: settings are saved")
api(base, token, "POST", "/api/profile", {"profile": "private", "fields": {"model": "private/glm-5-3", "web_search": "zero-retention"}})

# the commands the AppImage dispatches: claude-private (no Claude Code here: it says so), the search server
said = run("claude-private", "-p", "hi")
check(said.returncode == 1 and "Claude Code isn't installed" in said.stderr, f"{NAME}: claude-private runs, and says what's missing")
# the editor's script (claude-editor): Claude Code in a folder not chosen runs as usual, as the editor
# started it, with nothing of the AppImage's (its mount, the CA store AppRun chose) or DA Private Coder's
import tempfile  # noqa: E402

fake_dir = tempfile.mkdtemp()
with open(f"{fake_dir}/claude", "w") as f:
    f.write('#!/bin/sh\nenv > "$0.env"\necho "$@" > "$0.args"\n')
os.chmod(f"{fake_dir}/claude", 0o755)
usual = run("claude", "--folders", f"{fake_dir}/claude", "-p", "hi")
seen = dict(line.split("=", 1) for line in open(f"{fake_dir}/claude.env").read().splitlines() if "=" in line) \
    if os.path.exists(f"{fake_dir}/claude.env") else {}
check(usual.returncode == 0 and open(f"{fake_dir}/claude.args").read().strip() == "-p hi"
      and not any(k.startswith(("APPIMAGE", "APPDIR", "DAPRIVATECODER_", "ANTHROPIC_")) for k in seen)
      and ("SSL_CERT_FILE" in ENV) == ("SSL_CERT_FILE" in seen),
      f"{NAME}: the editor's Claude Code in a folder not chosen runs as usual" + ("" if usual.returncode == 0 else f" ({usual.stderr[-300:]})"))
mcp = run("search-server", "--profile", "private", stdin="".join(json.dumps(m) + "\n" for m in (
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})))
replies = {r["id"]: r for r in map(json.loads, mcp.stdout.splitlines())}
check(replies.get(2, {}).get("result", {}).get("tools", [{}])[0].get("name") == "web_search",
      f"{NAME}: the web search server answers Claude Code" + ("" if 2 in replies else f" ({mcp.stderr[-300:]})"))

# the proxy Claude Code talks to: started on its own, it says where and with what token
proxy = subprocess.Popen([f"{APPDIR}/AppRun", "serve", "--port", "8791"], env=ENV, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
proxy_token = ""
for line in proxy.stdout:
    if m := re.search(r"ANTHROPIC_AUTH_TOKEN=(\S+)", line):
        proxy_token = m.group(1)
        break
probe = urllib.request.Request("http://127.0.0.1:8791/api/hello", method="HEAD",
                               headers={"Authorization": f"Bearer {proxy_token}"})
listed = urllib.request.Request("http://127.0.0.1:8791/v1/models", headers={"Authorization": f"Bearer {proxy_token}"})
check(urllib.request.urlopen(probe, timeout=10).status == 200
      and json.load(urllib.request.urlopen(listed, timeout=10))["data"][0]["id"] == "private/glm-5-3",
      f"{NAME}: the proxy answers Claude Code")
proxy.terminate()
proxy.wait(10)

# the enclave, attested by the bundled Tinfoil SDK through NanoGPT's relay, trusting this host's CA store
att = run("check", timeout=120)
check(att.returncode == 0 and "Verified:" in att.stdout, f"{NAME}: the enclave is attested here\n"
      + "\n".join(f"  | {x}" for x in (att.stdout + att.stderr).strip().splitlines()[-4:]))

# the page itself, rendered by the host's WebKitGTK through the bundled PyGObject
sys.path.insert(0, f"{APPDIR}/usr/python/lib/python3.12/site-packages")
from daprivatecoder.app import _bundled_girepository  # noqa: E402

_bundled_girepository()
import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

shot = f"{OUT}/{NAME}.png"
window = Gtk.OffscreenWindow()
view = WebKit2.WebView()
view.set_size_request(600, 1500)
window.add(view)
window.show_all()


def snap() -> bool:
    view.get_snapshot(WebKit2.SnapshotRegion.FULL_DOCUMENT, WebKit2.SnapshotOptions.NONE, None,
                      lambda v, r: (v.get_snapshot_finish(r).write_to_png(shot), Gtk.main_quit()))
    return False


view.connect("load-changed", lambda v, e: e == WebKit2.LoadEvent.FINISHED and GLib.timeout_add(4000, snap))
view.load_uri(f"{base}/?desktop=1#t={token}")
GLib.timeout_add(30000, Gtk.main_quit)
Gtk.main()
check(os.path.exists(shot), f"{NAME}: the page renders ({shot})")
api(base, token, "POST", "/api/quit", {})
proc.wait(10)
