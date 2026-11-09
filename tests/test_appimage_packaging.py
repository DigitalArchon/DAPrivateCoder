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


# ---------------------------------------------------------------------- the host's CA store

HOST_BUNDLES = ["/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/ca-bundle.pem",
                "/etc/pki/tls/cert.pem", "/etc/ssl/cert.pem"]


def apprun_bundles() -> list[str]:
    return [p for p in re.findall(r"(/etc/[^\s;]+)", (PACKAGING / "AppRun").read_text()) if p.endswith((".crt", ".pem"))]


def test_apprun_looks_for_the_hosts_ca_store_wherever_distributions_keep_it(tmp_path):
    """Debian and its kin, Fedora and RHEL, openSUSE (/etc/ssl/ca-bundle.pem) and the rest, in DA
    Assistant's order: with three, openSUSE's host went to the bundled certifi, past its own trust."""
    import os
    import subprocess

    assert apprun_bundles() == HOST_BUNDLES
    # AppRun as it is, the host's places under tmp_path, and a Python that says what it was given
    here = tmp_path / "AppDir"
    (here / "usr/python/bin").mkdir(parents=True)
    python = here / "usr/python/bin/python3"
    python.write_text('#!/bin/sh\necho "$SSL_CERT_FILE|$REQUESTS_CA_BUNDLE|$DAPRIVATECODER_APPRUN_SET"\n')
    python.chmod(0o755)
    apprun = here / "AppRun"
    apprun.write_text((PACKAGING / "AppRun").read_text().replace("/etc/", f"{tmp_path}/etc/"))
    apprun.chmod(0o755)
    env = {"PATH": os.environ.get("PATH", "")}
    suse = tmp_path / "etc/ssl/ca-bundle.pem"
    suse.parent.mkdir(parents=True)
    suse.write_text("certificates")
    said = subprocess.run([str(apprun)], env=env, capture_output=True, text=True, check=True).stdout.strip()
    assert said == f"{suse}|{suse}|SSL_CERT_FILE REQUESTS_CA_BUNDLE"
    chosen = subprocess.run([str(apprun)], env={**env, "SSL_CERT_FILE": "/my/bundle.pem"}, capture_output=True,
                            text=True, check=True).stdout.strip()
    assert chosen == "/my/bundle.pem|/my/bundle.pem| REQUESTS_CA_BUNDLE"     # the user's own is left alone


# ---------------------------------------------------------------------- what the build takes from this machine

WRAPPER = ROOT / "packaging" / "build-appimage.sh"


def test_it_builds_the_commit_with_the_commits_time_and_version():
    wrapper, build = WRAPPER.read_text(), (PACKAGING / "build.sh").read_text()
    commands = [line for line in _commands(wrapper) if not line.lstrip().startswith("#")]
    assert "git ls-tree -r -z HEAD" in wrapper and not any("git archive" in line for line in commands)
    assert 'EPOCH="$(git log -1 --format=%ct HEAD)"' in wrapper
    assert "git show HEAD:pyproject.toml" in wrapper
    assert 'find "$W/AppDir" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +' in build
    # and where its results go: dist/, or DIST
    assert 'DIST="$(mkdir -p "${DIST:-dist}" && cd "${DIST:-dist}" && pwd)"' in wrapper
    assert 'run "$DIST" build' in wrapper


def test_the_commit_is_built_as_committed_whatever_gits_settings(tmp_path):
    """A verifier's core.autocrlf, a smudge filter their attributes name: git archive hands each file
    through them, so the same commit made a different AppImage. The wrapper reads the files from git's
    objects instead, and sets their modes itself whatever the umask. (DA Assistant's test, d4153ff.)"""
    import os
    import shlex
    import shutil
    import subprocess
    import tarfile

    if not shutil.which("git"):
        pytest.skip("no git")
    wrapper = WRAPPER.read_text()
    start = wrapper.index('EPOCH="$(git log -1 --format=%ct HEAD)"')
    export = wrapper[start:wrapper.index("-- *)\n", start) + 5]
    repo = tmp_path / "repo"
    (tmp_path / "attributes").write_text("*.txt filter=pear\n")
    (tmp_path / "gitconfig").write_text(f"[core]\n\tautocrlf = true\n\tattributesFile = {tmp_path}/attributes\n"
                                        "[filter \"pear\"]\n\tsmudge = sed s/apple/pear/\n\tclean = cat\n"
                                        "[user]\n\tname = T\n\temail = t@example.org\n")
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig")}
    committed = {"notes.txt": b"apple\nbanana\n", "tools/run.sh": b"#!/bin/sh\necho a\n"}

    def git(*args, cwd=repo):
        return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True).stdout

    git("init", "-q", str(repo), cwd=tmp_path)
    for name, data in committed.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_bytes(data)
    (repo / "tools/run.sh").chmod(0o755)
    git("add", "-A")
    git("commit", "-q", "-m", "files")

    def unpack(tar: Path) -> tuple[dict, dict]:
        with tarfile.open(tar) as archive:
            members = {m.name: m for m in archive.getmembers()}
            return ({name: archive.extractfile(members[name]).read() for name in committed},
                    {name: members[name].mode & 0o777 for name in committed})

    (tmp_path / "archive.tar").write_bytes(git("archive", "--format=tar", "HEAD"))
    assert unpack(tmp_path / "archive.tar")[0] != committed            # what git archive does with them
    work = tmp_path / "work"
    work.mkdir()
    run_export = ["bash", "-c", f"set -euo pipefail\numask 077\nWORK={shlex.quote(str(work))}\n{export}"]
    subprocess.run(run_export, cwd=repo, env=env, check=True, capture_output=True)
    files, modes = unpack(work / "src.tar")
    assert files == committed and modes == {"notes.txt": 0o644, "tools/run.sh": 0o755}
    (repo / "link").symlink_to("notes.txt")                             # nothing but plain files is taken
    git("add", "-A")
    git("commit", "-q", "-m", "a link")
    shutil.rmtree(work)
    work.mkdir()
    refused = subprocess.run(run_export, cwd=repo, env=env, capture_output=True, text=True)
    assert refused.returncode != 0 and "link is a blob of mode 120000" in refused.stderr


def test_the_builder_image_takes_no_mount_that_needs_relabelling():
    """:Z on the host's root-owned CA bundle: rootless Podman on an SELinux host (Fedora, RHEL) can't
    relabel it, so nobody there could rebuild a release. (DA Assistant's test, d4153ff.)"""
    wrapper = WRAPPER.read_text()
    assert not re.search(r"podman build[^\n]*-v ", wrapper)
    assert 'install -m 0644 "$ca" "$WORK/context/hostca.crt"' in wrapper   # readable by apt's own user
    containerfile = (PACKAGING / "Containerfile").read_text()
    assert "COPY hostca.crt /hostca.crt" in containerfile
    assert re.search(r"rm [^\n]*/hostca\.crt", containerfile)


def _lookup(*candidates: str):
    """host_ca_bundle, as the wrapper defines it, given `candidates`."""
    import subprocess

    found = re.search(r"^host_ca_bundle\(\) \{\n.*?^\}\n", WRAPPER.read_text(), re.S | re.M)
    assert found, "the lookup is not where this test looks for it"
    return subprocess.run(["bash", "-c", found.group(0) + 'host_ca_bundle "$@"', "bash", *candidates],
                          capture_output=True, text=True, timeout=30)


def test_the_build_looks_for_the_hosts_ca_bundle_wherever_apprun_looks(tmp_path):
    """It looked in three places, and on openSUSE named the last it looked in, which wasn't there.
    (DA Assistant's tests, 2a569de.)"""
    listed = re.search(r"^CA_BUNDLES=\((.*?)\)", WRAPPER.read_text(), re.S | re.M).group(1).split()
    assert listed == apprun_bundles() == HOST_BUNDLES
    bundle = tmp_path / "ca-bundle.pem"
    bundle.write_text("certificates")
    done = _lookup(str(tmp_path / "missing.crt"), str(bundle), str(tmp_path / "also-missing.pem"))
    assert (done.returncode, done.stdout) == (0, f"{bundle}\n")
    done = _lookup("/nonexistent/one.crt", "/nonexistent/two.pem")
    assert done.returncode == 1 and done.stdout == ""
    assert "No CA bundle on this machine" in done.stderr and "/nonexistent/one.crt /nonexistent/two.pem" in done.stderr
    assert "ca-certificates" in done.stderr
    wrapper = WRAPPER.read_text()                   # and only when the builder image is to be made
    assert wrapper.index('ca="$(host_ca_bundle "${CA_BUNDLES[@]}")"') > wrapper.index('if ! podman image exists "$TAG"; then')
    assert not re.search(r"(?m)^for ca in ", wrapper)
