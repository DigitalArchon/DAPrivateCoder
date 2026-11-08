# DA Private Coder

Claude Code on NanoGPT's end-to-end encrypted private models.

DA Private Coder runs Claude Code, in a terminal or in VSCodium and VS Code, on NanoGPT's Private
Mode models. Every request Claude Code makes is sealed on your computer to a Tinfoil enclave whose
attestation was checked first; NanoGPT only relays ciphertext. Plain `claude` keeps running as
usual, on Anthropic's models.

Its window sets it up (your NanoGPT key, the `claude-private` command, the switch for your editor),
chooses the models and how Claude Code searches the web, checks the enclave, opens Claude Code in a
folder, and shows what each request cost, never what was said.

For convenience it also runs Claude Code on models that **aren't** end-to-end encrypted, and says so
wherever you choose them: NanoGPT's TEE models, each enclave attested before anything is sent to it,
and standard models of NanoGPT, OpenRouter or another OpenAI-compatible provider, in the clear (see
[TEE and standard sessions](#tee-and-standard-sessions)). `claude-private` is only ever private.

## What is private, and what isn't

- **Sealed:** everything Claude Code sends to the model: your prompts, the files and command
  output it reads, and images (for a model that can't see, a private model describes them).
- **Checked before anything is sent:** the enclave's hardware report (AMD SEV-SNP or Intel TDX),
  checked against Sigstore's record of Tinfoil's router release, again every five minutes. A reply
  must come back sealed by the enclave and marked as Private Mode, or it is refused. A private model
  NanoGPT has retired is refused before anything is sent.
- **NanoGPT sees:** your account, which model, when, how much, and how many tokens.
- **The prompt cache is yours:** each session's cache key is a secret of its own, inside the seal,
  so no one else (NanoGPT included) can probe it.
- **Switched off in a session:** Claude Code's telemetry, error reports, auto-updates, `/feedback`,
  `/bug` and `/share`, release notes, claude.ai's connectors, WebFetch's check of each site with
  Anthropic, and Anthropic's web search (DA Private Coder has its own, below).
- **Not covered:** web searches (see [Web search](#web-search)), and what Claude Code's tools do on
  your computer. WebFetch fetches pages itself (the site sees your address), Bash runs what it runs,
  and MCP servers you've added talk to whoever they talk to.

All of this is a private session's. A TEE session's prompts are read by NanoGPT's gateway, and a
standard session's go in the clear to its provider and the model's host: see
[TEE and standard sessions](#tee-and-standard-sessions).

## Getting started

You need x86-64 Linux with GLib 2.80 or newer (Ubuntu 24.04, Mint 22, Debian 13, current Fedora and
Arch), Claude Code (the CLI, or the Claude Code extension of VSCodium or VS Code, whose own Claude
Code is used), and a NanoGPT API key with credit (Private Mode is paid per use).

1. Download `DAPrivateCoder-<version>-x86_64.AppImage`, make it executable, and open it.
   The window needs WebKitGTK 4.1 (`sudo apt install gir1.2-webkit2-4.1`, `sudo dnf install
   webkit2gtk4.1`, `sudo pacman -S webkit2gtk-4.1`); without it, it opens in your web browser.
2. In **Setup**: paste your NanoGPT key (or use DA Vibe Manager's).
3. **Start Claude Code**: choose a folder and open Claude Code in a terminal there. **Private** is
   chosen; **TEE** and **Standard** aren't end-to-end encrypted. To type `claude-private` in a
   terminal yourself instead, **Install** the terminal commands (optional).
4. For your editor: choose **Private models** (or TEE, Standard) and **Switch on** next to VSCodium or
   VS Code, then open a new Claude Code tab; **Back to normal** undoes it. The extension takes this
   for the whole editor, not per project.

In a session, `/status` shows the base URL as `http://127.0.0.1:…`: that is DA Private Coder.
`/model` offers the profile's models (Sonnet is GLM-5.3, Opus Kimi K3, Haiku DeepSeek V4.1 Flash) and
the other models NanoGPT's Private Mode offers now.

## Models and cost

Chosen in the window (**Models**, Private), or in `~/.config/daprivatecoder/config.toml`:

| Role | Model | Input | Cached input | Output |
|---|---|---|---|---|
| Default, `/model sonnet` | `private/glm-5-3` | $1.47 | $0.27 | $4.62 |
| `/model opus` | `private/kimi-k3` | $3.15 | $1.58 | $15.75 |
| `/model haiku`, background tasks, describing images | `private/deepseek-v4-1-flash` | $0.68 | $0.14 | $1.52 |

Prices are per million tokens, in November 2026, with NanoGPT's 5% Private Mode markup; the window
shows today's. Kimi K3 and DeepSeek V4.1 Flash see images, while GLM-5.3 has them described.

Claude Code sends the whole conversation with every request; what repeats comes from the prompt
cache, at the cached price. **Usage** in the window shows each request's tokens and cost. Ignore
Claude Code's own `/cost`: it doesn't know these models' prices.

**Effort** in the window (`reasoning` in the file) is how hard the models think as each session
starts: low, medium, high or max; low by default, since GLM at its default can reason for minutes a
step. In a session, Claude Code's own effort changes it: the Effort control of the editor's Claude
Code, or `/effort` (and `--effort`) in a terminal. The model runs at the level Claude Code shows, or
the nearest it takes, the higher of two as near: the private models take only low, high and max, so
medium runs as high and xhigh as max (a private model refuses any other level, and GLM-5.3 ignores
one and reasons at its own default, the most). A TEE or standard model gets the nearest of the levels
its provider lists for it; one that refuses a level, naming those it takes, is asked again with one.

A model that sees images (Kimi K3, DeepSeek V4.1 Flash) gets them as they are. Only one that
doesn't (GLM-5.3) has them described, by the **Images** model, also a private one.

## TEE and standard sessions

Neither is end-to-end encrypted. The window warns of it where you choose one, and **Usage** marks
each request: sealed, TEE, or clear.

**TEE** sessions run NanoGPT's `TEE/` models (by default GLM-5.3, Kimi K3 for `/model opus`, and
GLM-5.3 Flash for quick tasks and images). Before anything is sent to one, its enclave is attested,
and again every 15 minutes: its Intel TDX quote is verified up to Intel's root, with Intel's
revocation lists and TCB ratings, and its GPUs' evidence by NVIDIA's attestation service, all bound
to a nonce of this session's (DA Vibe Manager's verifier). An enclave that doesn't hold gets
nothing. Some attest only in part, which **The enclaves** in the window says: NanoGPT's TEE GLM
models pass through RedPill's gateway, whose enclave is checked but not the model's own. Either way,
**NanoGPT's gateway reads every prompt and reply in the clear**: the enclave protects them from the
host that runs the model, not from NanoGPT. A TEE session never sends to a model in the clear: a
request for one goes to the session's own model.

**Standard** sessions run any model of their provider, **in the clear** to it and to the host that
runs the model:

- **NanoGPT** (with your NanoGPT key), **OpenRouter** (with an OpenRouter key), or **another
  provider** with an OpenAI-compatible API (its address: https, or http only on this computer or
  your network; a key if it needs one). Each key is in your keyring, set in the window.
- **Runs on** (per model, NanoGPT and OpenRouter): the provider's own choice of host (on NanoGPT,
  what a subscription covers), or the fastest, the cheapest, the quickest to answer, or a host you
  pick from the provider's list, with its precision, price and speed; FP8 or better by default.
  **On NanoGPT a host you choose is paid per use, even for a model your subscription includes.**
- A Claude model (`anthropic/…`) goes to the provider's Anthropic endpoint as Claude Code wrote it;
  Claude Code's own default names (`claude-haiku-…`) are the session's models instead, so nothing
  goes to Claude unless you pick it.
- A `private/` model you pick with `/model` in a standard session is still sealed, and a `TEE/` one
  on NanoGPT attested; once a private model has been used, nothing more of that session goes in the
  clear.

Both kinds have their own models, effort and web search (**Models** and **Web search** in the window,
with the same Private, TEE, Standard choice), and start from the window, the editor's switch, or
`daprivatecoder claude --profile tee` (or `standard`) in a terminal.

## Web search

Claude Code's own web search runs on Anthropic's servers, so DA Private Coder gives it one of its
own, through NanoGPT. **No search is end-to-end encrypted**, whichever you choose: NanoGPT and the
search provider read every query.

| Choice | Searches with | What the provider keeps |
|---|---|---|
| Keep nothing (the default) | Linkup | Nothing: zero data retention. It finds less, and a failed search isn't retried elsewhere. |
| Best results | Perplexity to learn things, Kagi to find sites | Up to their own policies: not zero data retention. Better results. |
| No web search | — | — |

Whatever the choice, Claude Code asks you before each search and shows you the query; a query that
names this computer or you (your login, real name, host name, home folder, an e-mail, MAC or
private IP address) or holds a secret (an API key, a token, a private key, a long random string) is
refused before it goes; and a session makes at most 40 searches. With Best results, a provider that
fails on NanoGPT's side is replaced by Valyu, and if your NanoGPT account requires zero data
retention, by Linkup; the result tells the model which ran.

## Commands

**Install** in the window puts these in `~/.local/bin`, as small scripts that run the AppImage where
it is (they follow it when it moves). They are only for typing in a terminal: the window opens
Claude Code without them, and the editor's switch writes `claude-private` there itself, since the
editor needs a path that stays put.

| Command | What it does |
|---|---|
| `daprivatecoder` | The window (`--browser`: in your web browser) |
| `claude-private [args]` | Claude Code on the private profile; any of `claude`'s arguments work |
| `daprivatecoder claude [--profile NAME] [args]` | Claude Code on a profile: `tee` and `standard` aren't end-to-end encrypted |
| `daprivatecoder editor on\|off\|status [--profile NAME] [vscodium\|code]` | The switch for Claude Code in the editor (on: private unless said) |
| `daprivatecoder key set\|clear\|status [--provider openrouter\|custom]` | An API key, in your keyring (GNOME Keyring or KWallet): NanoGPT's unless said |
| `daprivatecoder check [--profile NAME]` | Attest the enclaves (a TEE profile's models too); list the models, their prices, and any your profile uses that are gone |
| `daprivatecoder log [-n N]` | The last requests and searches: model, sealed or not, tokens, cost |
| `daprivatecoder status` | What is set up here |

In `claude -p` (no one to ask), the web search tool runs only if you allow it:
`--allowedTools mcp__daprivatecoder__web_search`.

## Limits

- Claude Code assumes a 200K-token context window for models it doesn't know; these have 1M. It
  compacts the conversation sooner, which also keeps each request cheaper. To change it, set
  `CLAUDE_CODE_MAX_CONTEXT_TOKENS`.
- The model's reasoning isn't shown: Anthropic's thinking blocks carry Anthropic's signatures.
- A private profile only takes `private/` models, and a TEE one `TEE/` (or private) ones; a settings
  file that says otherwise doesn't load, and the window won't save one.

## How it works

`claude-private` makes the proxy's socket on 127.0.0.1, at a free port, and starts the proxy
(`daprivatecoder serve`) with it and a random token for the session. It then becomes Claude Code,
pointed at the proxy with environment variables and the same values in `--settings`, which outrank
any settings file but the managed ones. With web search on, it also gives Claude Code the search
server (`daprivatecoder search-server`, an MCP server). The proxy goes when Claude Code does. As the
editor's process wrapper (`claudeCode.claudeProcessWrapper`), it runs the extension's own Claude Code.

The proxy takes Claude Code's Anthropic requests and, for a private model, turns them into chat
completions. Each is sealed with EHBP (HPKE X25519 and AES-GCM) to the attested enclave's key and
sent through NanoGPT's relay; the replies come back as Anthropic events. A TEE model's are translated
the same way and sent through NanoGPT once its enclave has attested; a standard model's go to its
provider, with the host chosen for it; a Claude model's to the provider's Anthropic endpoint as they
are. The Private Mode client, the TEE verifier, the routes and the translation come from DA Vibe
Manager, after DAToolkit and SealedLore.

The window is a small local web page (served on 127.0.0.1 with a token for each launch) shown with
your system's GTK and WebKitGTK, as DA Vibe Manager's is.

## Running from source

```sh
uv venv --system-site-packages     # the window uses the system's PyGObject (python3-gi)
uv sync
uv run daprivatecoder              # the window
uv run pytest
```

## Building the AppImage

Reproducibly, with rootless Podman: `packaging/build-appimage.sh check` builds it twice from the
committed source and compares. See [packaging/README.md](packaging/README.md).

## Uninstall

In the window: **Back to normal** for your editor, and **Remove** the keys. Then delete the AppImage,
`~/.local/bin/claude-private`, `~/.local/bin/daprivatecoder`, `~/.local/share/daprivatecoder` (the
editor's scripts for TEE and standard sessions), and if you like `~/.config/daprivatecoder` and
`~/.local/state/daprivatecoder`.

Licence: AGPL-3.0-or-later.
