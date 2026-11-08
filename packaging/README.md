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

You need rootless [Podman](https://podman.io) (`sudo apt install podman`).

```bash
packaging/build-appimage.sh build    # dist/DAPrivateCoder-<version>-x86_64.AppImage (+ .sha256)
packaging/build-appimage.sh check    # build twice from scratch, compare: must be identical
packaging/test-appimage.sh           # smoke-test it on Ubuntu, Debian, Fedora, Arch, CachyOS
packaging/build-appimage.sh lock     # re-resolve the dependency locks (review the diff, commit)
```

Only committed files are built (`git archive HEAD`), and every timestamp is HEAD's commit
time (`SOURCE_DATE_EPOCH`). Builds run in `packaging/appimage/Containerfile`: Ubuntu 24.04 pinned
by digest, with packages from the Ubuntu archive as it was at a fixed moment
([snapshot.ubuntu.com](https://snapshot.ubuntu.com)). Downloads are cached in
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

Changing any of these changes the AppImage, which is the point: an update is a reviewed commit.
Runtime packages are installed only as prebuilt wheels; the few without one (`SDIST_ONLY` in
`appimage/build.sh`) are built from their pinned sources with the pinned tools. The encryption
stack (tinfoil, tinfoil-ehbp, pyhpke) is pinned exactly in `pyproject.toml` as well, and the tests
check that the lock agrees.

## Verifying a release

1. Check out the release's tag.
2. `packaging/build-appimage.sh build`.
3. Compare `sha256sum dist/DAPrivateCoder-*.AppImage` with the release's `.sha256`.

If they differ, `diffoscope` on the two files shows where.

## Releasing

1. Update the version in `pyproject.toml` and `src/daprivatecoder/__init__.py`, commit, tag.
2. `packaging/build-appimage.sh check` (reproducible), then `packaging/test-appimage.sh`.
3. Write the release notes in `dist/release-notes-v<version>.md` (beside the AppImage, not in git).
4. Attach the AppImage and its `.sha256` to the release, with those notes.

Rebuild and re-release when a pinned dependency has a security fix: the locks don't move by
themselves. WebKitGTK and GTK update with the system.
