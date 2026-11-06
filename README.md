# PrivateCode

Claude Code on NanoGPT's end-to-end encrypted private models.

`claude-private` starts Claude Code with a small proxy beside it. Every request Claude Code makes
is sealed on your computer to a Tinfoil enclave whose attestation was checked first; NanoGPT only
relays ciphertext. Plain `claude`, or Claude Code in your editor with the switch off, runs as
usual on Anthropic's models.

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
- **Switched off in a PrivateCode session:** Claude Code's telemetry, error reports, auto-updates,
  `/feedback`, `/bug` and `/share`, release notes, claude.ai's connectors, WebFetch's check of each
  site with Anthropic, and Anthropic's web search (PrivateCode has its own, below).
- **Not covered:** web searches (see [Web search](#web-search)), and what Claude Code's tools do on
  your computer. WebFetch fetches pages itself (the site sees your address), Bash runs what it runs,
  and MCP servers you've added talk to whoever they talk to. The `nano` profile sends requests to
  NanoGPT in the clear. NanoGPT's `TEE/` models aren't end-to-end encrypted (their prompts pass
  NanoGPT's gateway); only `private/` ones are.

## Install

You need Linux, [uv](https://docs.astral.sh/uv/), Claude Code (the CLI, or the Claude Code
extension of VSCodium or VS Code, whose own Claude Code is used), and a NanoGPT API key with
credit (Private Mode is paid per use).

```sh
./install.sh
privatecode key set                  # or: privatecode key set --from-vibe-manager
privatecode check                    # attests the enclave and lists the private models (free)
```

## Use

In a terminal:

```sh
claude-private                       # Claude Code on private models; any of claude's arguments work
claude                               # as usual
```

In VSCodium or VS Code:

```sh
privatecode editor on                # Claude Code in the editor runs on private models
privatecode editor off               # back to normal
```

Open a new Claude Code tab (or reload the window) after switching. The extension takes this
setting for the whole editor, not per project.

In a session, `/status` shows the base URL as `http://127.0.0.1:…`: that is PrivateCode. `/model`
offers the profile's models (Sonnet is GLM-5.3, Opus Kimi K3, Haiku DeepSeek V4.1 Flash) and the
other models NanoGPT's Private Mode offers now.

## Models and cost

The defaults, in `~/.config/privatecode/config.toml` (written on first use, with what each
setting means):

| Role | Model | Input | Cached input | Output |
|---|---|---|---|---|
| Default, `/model sonnet` | `private/glm-5-3` | $1.47 | $0.27 | $4.62 |
| `/model opus` | `private/kimi-k3` | $3.15 | $1.58 | $15.75 |
| `/model haiku`, background tasks, describing images | `private/deepseek-v4-1-flash` | $0.68 | $0.14 | $1.52 |

Prices are per million tokens, in November 2026, with NanoGPT's 5% Private Mode markup;
`privatecode check` shows today's. Kimi K3 and DeepSeek V4.1 Flash see images, while GLM-5.3 has
them described by the vision model.

Claude Code sends the whole conversation with every request; what repeats comes from the prompt
cache, at the cached price. `privatecode log` shows each request's tokens and cost. Ignore Claude
Code's own `/cost`: it doesn't know these models' prices.

`reasoning` sets how hard the models think (low, medium, high or max; low by default, since GLM
at its default can reason for minutes a step). Claude Code's `/effort` doesn't change it.

## Web search

Claude Code's own web search runs on Anthropic's servers, so PrivateCode gives it one of its own,
through NanoGPT: the `web_search` tool. **No search is end-to-end encrypted**, whichever you
choose: NanoGPT and the search provider read every query. Each profile chooses with `web_search`
in its settings:

| `web_search` | Searches with | What the provider keeps |
|---|---|---|
| `zero-retention` (private profile's default) | Linkup | Nothing: zero data retention. It finds less, and a failed search isn't retried elsewhere. |
| `best` (nano profile's default) | Perplexity to learn things, Kagi to find sites | Up to their own policies: not zero data retention. Better results. |
| `off` | — | — |

Whatever the choice:

- Claude Code asks you before each search and shows you the query (allow it for the session if you
  like).
- A query that names this computer or you (your login, real name, host name, home folder, an
  e-mail, MAC or private IP address) or holds a secret (an API key, a token, a private key, a long
  random string) is refused before it goes.
- A session makes at most 40 searches. Each costs a little: Linkup $0.006; `privatecode log` shows
  what each cost, but never the query.
- With `best`, a provider that fails on NanoGPT's side is replaced by Valyu, and if your NanoGPT
  account requires zero data retention, by Linkup; the result tells the model which ran.

## Commands

| Command | What it does |
|---|---|
| `claude-private [args]` | Claude Code on the `private` profile |
| `privatecode claude [--profile NAME] [args]` | Claude Code on any profile; `nano` has NanoGPT's other models, in the clear |
| `privatecode editor on\|off\|status [vscodium\|code]` | The switch for Claude Code in the editor |
| `privatecode key set\|clear\|status` | The NanoGPT key, in your keyring (GNOME Keyring or KWallet) |
| `privatecode check` | Attest the enclave; list the private models, their prices, and any your profile uses that are gone |
| `privatecode log [-n N]` | The last requests: model, sealed or not, tokens, cost |
| `privatecode status` | What is set up here |

## Limits

- Claude Code assumes a 200K-token context window for models it doesn't know; these have 1M.
  It compacts the conversation sooner, which also keeps each request cheaper. To change it, set
  `CLAUDE_CODE_MAX_CONTEXT_TOKENS`.
- The model's reasoning isn't shown: Anthropic's thinking blocks carry Anthropic's signatures.
- A private profile only takes `private/` models; a settings file that says otherwise doesn't load.
- In `claude -p` (no one to ask), the web search tool runs only if you allow it:
  `--allowedTools mcp__privatecode__web_search`.

## How it works

`claude-private` makes the proxy's socket on 127.0.0.1, at a free port, and starts the proxy
(`privatecode serve`) with it and a random token for the session. It then becomes Claude Code,
pointed at the proxy with environment variables and the same values in `--settings`, which
outrank any settings file but the managed ones. With web search on, it also gives Claude Code
PrivateCode's search server (`privatecode search-server`, an MCP server it starts and talks to over
stdin). The proxy goes when Claude Code does. As the editor's process wrapper
(`claudeCode.claudeProcessWrapper`), it runs the extension's own Claude Code.

The proxy takes Claude Code's Anthropic requests and, for a private model, turns them into chat
completions. Each is sealed with EHBP (HPKE X25519 and AES-GCM) to the attested enclave's key and
sent through NanoGPT's relay; the replies come back as Anthropic events. The Private Mode client and
the translation come from DA Vibe Manager, after DAToolkit and SealedLore.

## Development

```sh
uv sync
uv run pytest
```

## Uninstall

```sh
privatecode editor off
privatecode key clear                # removes the key from your keyring
rm -r ~/.local/share/privatecode ~/.local/bin/claude-private ~/.local/bin/privatecode
```

Licence: AGPL-3.0-or-later.
