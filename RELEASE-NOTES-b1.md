# DA Private Coder 0.1.0b1 (beta 1)

The first beta. DA Private Coder runs Claude Code on NanoGPT's end-to-end encrypted private models,
in a terminal or in VSCodium and VS Code. Every request is sealed on your computer to a Tinfoil
enclave whose attestation was checked first, and NanoGPT relays only ciphertext.

For convenience, it can also run NanoGPT's TEE models, and standard models from NanoGPT, OpenRouter
or another OpenAI-compatible provider. **Neither kind is end-to-end encrypted**, and the window says
so wherever you choose one.

> **Beta:** expect rough edges, and use it at your own risk. Please report what breaks.

## Download

Download `DAPrivateCoder-0.1.0b1-x86_64.AppImage`, make it executable, and open it.

It needs:
- x86-64 Linux with GLib 2.80 or newer: Ubuntu 24.04, Mint 22, Debian 13, or current Fedora or Arch.
- WebKitGTK 4.1 for the window. Without it, the app opens in your web browser.
- Claude Code: the CLI, or the Claude Code extension for VSCodium or VS Code.
- A NanoGPT API key with credit.

The AppImage is built reproducibly: `packaging/build-appimage.sh check` builds it twice from the same
source and compares the hashes.

## Your editor, window by window

- You choose which folders run on DA Private Coder in VSCodium and VS Code, and how: Private, TEE or
  Standard. Every other window keeps running Claude Code as usual, on Anthropic's models with your
  own settings.
- **Open in VSCodium** (in Start) chooses the folder, switches the editor on and opens the folder.
- **Folders in your editor** lists your choices, which you can change at any time.

## Only while it's open

DA Private Coder runs Claude Code only while its window is open.

- **Closed or quit:** every session stops at its next request, saying "DA Private Coder is closed,
  so nothing was sent". Open it again and each session continues.
- **A folder's choice changed, or the editor switched back to normal:** a conversation already open
  there stops at its next request and says why. Start a new conversation to run as the folder now
  says.

## Seeing where it goes

- **Running now** lists each session: its folder, the editor or terminal it's in, its kind, and the
  model its last request went to and how (sealed to the enclave, attested, or in the clear).
- **Usage** shows each request's tokens and cost, but never what was said.
- **The model Claude Code shows is the one that runs.** If you pick a model the session won't send
  to, such as a Claude model in a private session, it is refused with the reason, never swapped for
  another, and Claude Code keeps the model it had.

## Also in this release

- **Private sessions:** your prompts, files, command output and images are sealed. The enclave is
  attested (AMD SEV-SNP or Intel TDX, checked against Sigstore's record of Tinfoil's release) before
  anything is sent, and again every five minutes.
- **TEE sessions:** each model's enclave is attested (Intel TDX up to Intel's root, its GPUs by
  NVIDIA) before anything is sent, and again every 15 minutes. NanoGPT's gateway still reads every
  prompt.
- **Standard sessions:** you choose which host runs each model, or let the provider decide. On
  NanoGPT, letting it decide is what a subscription covers.
- **Effort:** sessions start at High, and Claude Code's own Effort control changes it. Each model is
  sent the nearest level it accepts: the private models take only low, high and max.
- **Images:** a model that can see gets them directly; one that can't has them described by a
  private model.
- **Clear errors:** an empty NanoGPT balance is reported as such, not as a temporary server error,
  and Claude Code doesn't keep retrying it.
- **Web search** goes through NanoGPT. You can choose Linkup (keeps nothing), Perplexity and Kagi
  (better results, not zero retention), or none. Claude Code asks before each search.

## Known limits

- Claude Code assumes a 200K-token context for models it doesn't know; these models have 1M, so it
  compacts sooner. To change it, set `CLAUDE_CODE_MAX_CONTEXT_TOKENS`.
- The model's reasoning isn't shown, because Anthropic's thinking blocks carry Anthropic's
  signatures.
- `TEE/deepseek-v4.1-flash` is always refused, because NanoGPT serves no fresh attestation for it.
  The private DeepSeek works.
- OpenRouter routing and its per-model effort levels are included, but have had less live testing
  than NanoGPT.

Smoke-tested on Ubuntu, Debian, Fedora, Arch and CachyOS. Licence: AGPL-3.0-or-later.
