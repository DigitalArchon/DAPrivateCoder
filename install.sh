#!/bin/sh
# Install DA Private Coder from this folder into an environment of its own, with exactly the versions
# in uv.lock (their hashes checked), and put claude-private and daprivatecoder in ~/.local/bin.
# The installed copy doesn't follow this folder: run this again after pulling changes.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
data="${XDG_DATA_HOME:-$HOME/.local/share}/daprivatecoder"
bin="$HOME/.local/bin"
if ! command -v uv >/dev/null 2>&1; then
    echo "install.sh needs uv: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi
UV_PROJECT_ENVIRONMENT="$data/venv" uv sync --project "$here" --frozen --no-dev --no-editable
mkdir -p "$bin"
for name in claude-private daprivatecoder; do
    ln -sfn "$data/venv/bin/$name" "$bin/$name"
done
echo "Installed $bin/claude-private and $bin/daprivatecoder"
case ":$PATH:" in
    *":$bin:"*) ;;
    *) echo "$bin isn't on your PATH: add it to run them by name." ;;
esac
echo "Next: daprivatecoder key set    (or: daprivatecoder key set --from-vibe-manager)"
