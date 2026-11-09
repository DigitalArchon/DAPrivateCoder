"""The AppImage's build: what it downloads, what it installs, what it takes from the machine it runs on,
and what of that machine gets into the image. `packaging/build-appimage.sh check` is the real test of a
reproducible build; these catch a change that would quietly break one of its rules. After DA Assistant's
tests/test_appimage_packaging.py and tests/test_build_scripts.py (Nov 2026)."""

import re
import tomllib
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging" / "appimage"


def _name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _version(text: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", text))


def lock_pins() -> dict[str, str]:
    """The AppImage's runtime lock (packaging/appimage/requirements.lock)."""
    text = (PACKAGING / "requirements.lock").read_text()
    return {_name(n): v for n, v in re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", text, re.M)}


def uv_pins() -> dict[str, str]:
    """uv.lock's, for running from source."""
    return {_name(p["name"]): p["version"] for p in tomllib.loads((ROOT / "uv.lock").read_text())["package"]
            if "version" in p}


# ---------------------------------------------------------------------- the locks

def test_both_locks_pin_every_package_they_share_alike():
    """Run from source (uv.lock) or as the AppImage (requirements.lock), it is the same code."""
    appimage, source = lock_pins(), uv_pins()
    shared = (appimage.keys() & source.keys()) - {"daprivatecoder"}
    assert {"tinfoil", "pyasn1", "dnspython", "cryptography"} <= shared
    assert {n: appimage[n] for n in shared} == {n: source[n] for n in shared}


def test_both_locks_meet_every_floor_pyproject_sets():
    """A floor added for an advisory (certifi's root, brotli's decoder, pyasn1's and dnspython's denials
    of service) is only as good as the lock that follows it: nothing held them but the locks."""
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    floors = [re.fullmatch(r"([A-Za-z0-9_.-]+)>=([0-9.]+)", d).groups() for d in deps if ">=" in d]
    assert ("pyasn1", "0.6.5") in floors and ("dnspython", "2.9.0") in floors
    for pins in (lock_pins(), uv_pins()):
        for name, floor in floors:
            if _name(name) in pins:
                assert _version(pins[_name(name)]) >= _version(floor), f"{name}=={pins[_name(name)]}"
    assert {"pyasn1", "dnspython"} <= lock_pins().keys()


# ---------------------------------------------------------------------- what the build downloads

def _commands(text: str) -> list[str]:
    """A script's commands, one to a line: a command continued over several lines is joined into one."""
    return re.sub(r"\\\n\s*", " ", text).splitlines()


def test_every_download_is_https_only():
    """A hash proves what arrived, not that nobody saw it asked for: no download may fall back to http,
    through a redirect either. (DA Assistant's test, 8c9bf5e.)"""
    curls = []
    for path in sorted((ROOT / "packaging").rglob("*.sh")):
        for line in _commands(path.read_text()):
            if re.search(r"\bcurl\s+-", line) and not line.lstrip().startswith("#"):
                curls.append(path.name)
                assert "--proto '=https' --tlsv1.2" in line, f"{path.name}: {line.strip()}"
    assert "build.sh" in curls
    # the source archives are fetched by Python, which follows a redirect to http by itself
    build = (PACKAGING / "build.sh").read_text()
    assert "class HttpsOnly(urllib.request.HTTPRedirectHandler)" in build
    assert "urllib.request.urlopen(" not in build


def test_the_source_archives_fetch_refuses_plain_http_and_a_redirect_to_it():
    build = (PACKAGING / "build.sh").read_text()
    code = build[build.index("class HttpsOnly"):build.index("dest, lockfile, names")]
    found: dict = {}
    exec("import sys, urllib.request\n" + code, found)
    request = urllib.request.Request("https://files.pythonhosted.org/packages/x.tar.gz")
    with pytest.raises(SystemExit, match="redirects to http://files.pythonhosted.org/x.tar.gz, which is not https"):
        found["HttpsOnly"]().redirect_request(request, None, 302, "Found", {}, "http://files.pythonhosted.org/x.tar.gz")
    onward = found["HttpsOnly"]().redirect_request(request, None, 302, "Found", {}, "https://mirror.example/x.tar.gz")
    assert onward.full_url == "https://mirror.example/x.tar.gz"
    with pytest.raises(SystemExit, match="is not https"):
        found["urlopen"]("http://pypi.org/pypi/pycairo/1.0/json")
