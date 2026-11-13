# Packaging: the AppImage

DA Private Coder ships as a single AppImage for x86-64 Linux, built so that anyone can rebuild a
release from its commit and get the same file, byte for byte. The build is DA Vibe Manager's, without
what only Vibe Manager carries (tor, the Claude Code CLI): Claude Code here is the user's own.

## What's in it, and what comes from the system

| In the AppImage | From the system |
|---|---|
| Python 3.12 ([python-build-standalone](https://github.com/astral-sh/python-build-standalone)) | GTK 3 and **WebKitGTK 4.1** (the window) |
| Every Python dependency, pinned with hashes, Tinfoil's SDK among them | **Claude Code**: the CLI, or the one in the VSCodium or VS Code extension |
| PyGObject and pycairo, built from pinned sources | The Secret Service keyring (GNOME Keyring, KWallet) |
| A fallback `libgirepository-2.0` for hosts that lack it | A terminal, and the CA store (`/etc/ssl`), used before the bundled certifi |
| | The tray, if there is one: XApp (Mint), Ayatana or AppIndicator (KDE, CachyOS, GNOME's extension) |

WebKitGTK is deliberately **not** bundled: it should get the distribution's security updates, not
wait for a DA Private Coder release. Without it, DA Private Coder opens in the default web browser
instead and says what to install:

| Distribution | The window's engine |
|---|---|
| Ubuntu 24.04+, Mint 22+, Debian 13+ | `sudo apt install gir1.2-webkit2-4.1` |
| Fedora | `sudo dnf install webkit2gtk4.1` |
| Arch, CachyOS | `sudo pacman -S webkit2gtk-4.1` |

The bundled PyGObject needs GLib 2.80 or newer, which sets the oldest supported systems:
Ubuntu 24.04 / Mint 22, Debian 13, and current Fedora and Arch. The AppImage uses the static
type 2 runtime, so it doesn't need `libfuse2`.

Run with no arguments, the AppImage opens the window; `DAPrivateCoder.AppImage claude-private …`
starts Claude Code on the private models, and any other command is `daprivatecoder`'s (key, check,
log…). The `claude-private` and `daprivatecoder` commands the window installs in `~/.local/bin` run
the AppImage that way.

## Building

The machine that builds needs four things, and `build-appimage.sh` says which is missing before
it starts: rootless [Podman](https://podman.io) (`sudo apt install podman`), with SELinux enforcing
or not, git, GNU tar (its `--sort=name` packs the commit's files in one order) and python3 3.11 or
newer (its `tomllib` reads the version from `pyproject.toml`). Everything else the build uses is in
the container.

```bash
packaging/build-appimage.sh build    # dist/DAPrivateCoder-<version>-x86_64.AppImage (+ .sha256)
packaging/build-appimage.sh check    # build twice from scratch, compare: must be identical
packaging/test-locked.sh             # the test suite, on the AppImage's own Python and packages
packaging/test-appimage.sh           # smoke-test it on Ubuntu, Debian, Fedora, Arch, CachyOS
packaging/build-appimage.sh lock     # lock what pyproject.toml and the *.in files add (review, commit)
UPGRADE=1 packaging/build-appimage.sh lock   # re-resolve every lock to what is newest now
DIST=/some/dir packaging/build-appimage.sh build   # the results there instead of dist/
```

Only committed files are built, each read from git's objects exactly as HEAD has it (not through
`git archive`, which would apply your git's line-ending settings, filters and attributes first), with
HEAD's version, and every timestamp is HEAD's commit time (`SOURCE_DATE_EPOCH`). What builds them is
the commit's too. `build.sh`, the locks and the pins come out of the commit with the sources;
`build-appimage.sh` itself is the file that was run, and it reads `pins.sh` and the Containerfile from
the working tree to make the builder image, so it first compares itself and everything in
`packaging/appimage/` with HEAD's, byte for byte, and builds nothing while any of them differs: it
names the files, to be committed or put aside. Other uncommitted changes are not built, and a note
says so. The lock run reads `pyproject.toml` and the `*.in` files as they are in the working tree, so
a requirement is locked before it is committed, and keeps every pin it can (`UPGRADE=1` moves them
all); a lock written for the first time is named, to be added. The host's CA bundle
(wherever the distribution keeps it) is copied into the builder image's build only for its apt to
reach the package snapshot over https; nothing of it is in the AppImage. Builds run in `packaging/appimage/Containerfile`: Ubuntu 24.04 pinned
by digest, with packages from the Ubuntu archive as it was at a fixed moment
([snapshot.ubuntu.com](https://snapshot.ubuntu.com)). The builder image is made on this machine
and run from there (`--pull=never`: no registry is asked for it). Everything is downloaded over https,
never plain http, not even through a redirect; downloads are cached in
`~/.cache/daprivatecoder-appimage` and verified on every build.

The smoke test attests the real enclave over the network from each distribution (free: NanoGPT's
relay and Tinfoil's public records), with the bundled Tinfoil SDK and that distribution's CA store.

## What is pinned, and where

| What | Pinned in |
|---|---|
| Base image (digest), apt snapshot, Python build, AppImage runtime | `appimage/pins.sh` |
| Runtime Python packages (wheels, with hashes) | `appimage/requirements.lock` |
| PyGObject, pycairo (source archives, with hashes) | `appimage/sdists.lock` |
| meson, meson-python, ninja, setuptools (with hashes) | `appimage/build-requirements.lock` |
| pip and pip-tools, which write the locks above, and what they run on (with hashes) | `appimage/lock-requirements.lock`, from `lock-requirements.in` |
| pytest and pytest-asyncio, for `test-locked.sh` (with hashes; not in the image) | `appimage/test-requirements.lock`, from `test-requirements.in` |

Changing any of these changes the AppImage, which is the point: an update is a reviewed commit.
Runtime packages are installed only as prebuilt wheels; the few without one (`SDIST_ONLY` in
`appimage/build.sh`) are built from their pinned sources with the pinned tools. The encryption
stack (tinfoil, tinfoil-ehbp, pyhpke) is pinned exactly in `pyproject.toml` as well, and the tests
check that the lock agrees.

What keeps the result the same wherever it is built: each source is unpacked into a fixed directory
and the build path is mapped out of compiled code (`-ffile-prefix-map`); bytecode is compiled once,
with a fixed hash seed, as *unchecked-hash* `.pyc` files, which store their source's hash where a date
would go and are loaded without being checked against the source (nothing can change it on the
read-only image); files are owned by root with no extended attributes, and dated `SOURCE_DATE_EPOCH`.

Nor does the build's own folder get in. Each `.pyc` names its source as it is inside the image
(`/usr/python/lib/...`); pip's `direct_url.json` files and the console scripts it writes
(`daprivatecoder`, `keyring`, ...), whose `#!` line names the build's Python, are left out; and the
build stops if anything in the image still names that folder. Each package's `RECORD` is then made
to say what is in the image (`appimage/records.py`): the lines of files the build left out
(`direct_url.json`, the console scripts) are dropped, and a file the build changed gets its present
hash and size; every other line stays as pip wrote it.

## Testing what ships

A developer's venv is not the AppImage: its packages are the newest of the day it was made, on the
system's Python. `packaging/test-locked.sh` runs the test suite on the AppImage instead:

```bash
packaging/test-locked.sh                          # the whole suite, on dist/'s newest AppImage
packaging/test-locked.sh -q tests/test_safety.py  # any pytest arguments
APPIMAGE=/elsewhere/DAPrivateCoder-<version>-x86_64.AppImage packaging/test-locked.sh
```

It unpacks the AppImage into a temporary folder and has its Python (`-I`, as AppRun runs it) run the
checkout's `tests/` against the `daprivatecoder` and the packages installed in the image. The image
must be of the checkout: if a file of its `daprivatecoder` is not the one in `src/daprivatecoder`, the
script names it and tests nothing. pytest and pytest-asyncio are not in the image; they are installed
from `appimage/test-requirements.lock`, with hashes, into a folder of their own that comes last on the
path, so that nothing they bring stands in for a package that ships. The pip that installs them is the
pinned Python's the build uses (`pins.sh`), taken from the build's cache or downloaded and checked as
the build does. The suite runs under a 3 GB memory cap where there is a systemd user session. The
script needs git, GNU tar, curl and the network (for those few wheels).

## Verifying a release

1. Check out the release's tag, with no line-ending conversion:
   `git clone -c core.autocrlf=false https://github.com/DigitalArchon/DAPrivateCoder`, then
   `git checkout v<version>` in it.
2. `packaging/build-appimage.sh build`.
3. Compare `sha256sum dist/DAPrivateCoder-*.AppImage` with the release's `.sha256`.

Your git settings can't change what is built: the build reads each file from git's objects as the
commit has it. They can change the checkout, though, and the build scripts run from there; with
`core.autocrlf=true` they would have CRLF line endings and not start at all, hence the `-c` in step 1.

If the hashes differ, `diffoscope` on the two files shows where.

## Releasing

1. Update the version in `pyproject.toml` and `src/daprivatecoder/__init__.py`, commit, tag.
2. `packaging/build-appimage.sh check` (reproducible), then `packaging/test-locked.sh` (the test
   suite passes on what was built) and `packaging/test-appimage.sh` (it starts on each distribution).
3. Write the release notes in `dist/release-notes-v<version>.md` (beside the AppImage, not in git).
4. Attach the AppImage and its `.sha256` to the release, with those notes.

Rebuild and re-release when a pinned dependency has a security fix: the locks don't move by
themselves. WebKitGTK and GTK update with the system.
