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


# ---------------------------------------------------------------------- what the build installs

def test_nothing_is_installed_but_from_a_lock_with_hashes_or_built_here():
    """The lock run installed "pip==26.2.2" "pip-tools==7.6.1": versions, not files, and what they pull
    in as it came on the day; what writes the locks is locked now too. (DA Assistant's test, dc34ab7.)"""
    build = (PACKAGING / "build.sh").read_text()
    installs = 0
    for line in _commands(build):
        command = line.split(" #", 1)[0].strip()
        if command.startswith("#") or not re.search(r'pip"? install ', command):
            continue
        installs += 1
        if re.search(r' -r "[^"]+\.lock"', command):
            assert "--require-hashes" in command, command
        else:
            assert re.search(r'pip"? install( -[-\w]+)* "\$W/wheels"/\S+\.whl$', command), command
    assert installs >= 5
    assert '--require-hashes --no-deps -r "$P/lock-requirements.lock"' in build
    assert "--output-file /out/lock-requirements.lock lock-requirements.in" in build


def test_every_locked_package_carries_a_hash():
    for name in ("requirements", "build-requirements", "sdists", "lock-requirements"):
        entries = re.findall(r"^([\w.-]+)==([^\s]+)((?:\s*\\\s*--hash=sha256:\w+)*)",
                             (PACKAGING / f"{name}.lock").read_text(), re.M)
        assert entries, name
        for package, version, hashes in entries:
            assert "--hash=sha256:" in hashes, f"{name}.lock: {package}=={version}"
    tools = dict(re.findall(r"^([\w.-]+)==(\S+)", (PACKAGING / "lock-requirements.lock").read_text(), re.M))
    assert (tools["pip"], tools["pip-tools"]) == ("26.2.2", "7.6.1")


# ---------------------------------------------------------------------- what of the build machine gets in

def test_nothing_of_the_build_machine_gets_in():
    build = (PACKAGING / "build.sh").read_text()
    for rule in ("-root-owned -no-xattrs", "--invalidation-mode unchecked-hash", "PYTHONHASHSEED=0",
                 '-ffile-prefix-map=$W/=', "direct_url.json -delete", 'grep -rlaF "$W/AppDir" "$W/AppDir"'):
        assert rule in build, rule


def test_bytecode_names_its_source_as_it_is_inside_the_image(tmp_path):
    """compileall as build.sh runs it, on a folder laid out as the AppDir is: each .pyc unchecked-hash,
    naming /usr/python/lib/..., not the folder the build ran in. (DA Assistant's test, 8cfe093.)"""
    import marshal
    import os
    import subprocess
    import sys

    build = (PACKAGING / "build.sh").read_text()
    command = next(line for line in _commands(build) if "-m compileall" in line)
    # -B: its interpreter would otherwise write its own imports' bytecode first, dated, and compileall
    # would leave those as they are
    assert '"$PY" -B -m compileall' in command
    lib = tmp_path / "AppDir" / "usr" / "python" / "lib" / "python3.12"
    lib.mkdir(parents=True)
    (lib / "mod.py").write_text("def f():\n    return 1\n")
    subprocess.run(["bash", "-c", command], check=True,
                   env={"PATH": os.environ.get("PATH", ""), "PY": sys.executable, "W": str(tmp_path)})
    names = []
    for pyc in tmp_path.rglob("*.pyc"):
        data = pyc.read_bytes()
        assert int.from_bytes(data[4:8], "little") == 1, pyc         # unchecked-hash
        code = marshal.loads(data[16:])
        names += [code.co_filename, code.co_consts[0].co_filename]
    assert sorted(set(names)) == ["/usr/python/lib/python3.12/mod.py"]


def test_console_scripts_that_name_the_build_python_are_left_out(tmp_path):
    import os
    import subprocess

    build = (PACKAGING / "build.sh").read_text()
    start = build.index('for script in "$W"/AppDir/usr/python/bin/*; do')
    loop = build[start:build.index("\ndone\n", start) + 6]
    bin_dir = tmp_path / "AppDir" / "usr" / "python" / "bin"
    bin_dir.mkdir(parents=True)
    scripts = {"daprivatecoder": f"#!{tmp_path}/AppDir/usr/python/bin/python3\nfrom daprivatecoder.cli import main\n",
               "pydoc3.12": "#!/bin/sh\n'''exec' \"$(dirname -- \"$0\")/python3.12\"\n",
               "python3.12": "\x7fELF\x02\x01\x01\0" + "\0" * 64}
    for name, text in scripts.items():
        (bin_dir / name).write_text(text, encoding="latin-1")
    (bin_dir / "python3").symlink_to("python3.12")
    subprocess.run(["bash", "-c", f"set -euo pipefail\n{loop}"], check=True,
                   env={"PATH": os.environ.get("PATH", ""), "W": str(tmp_path)})
    assert sorted(os.listdir(bin_dir)) == ["pydoc3.12", "python3", "python3.12"]
