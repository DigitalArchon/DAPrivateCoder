# DA Private Coder

Claude Code on NanoGPT's end-to-end encrypted private models.

> **Beta.** DA Private Coder is beta software: expect rough edges, and use it at your own risk.

DA Private Coder runs Claude Code, in a terminal or in VSCodium and VS Code, on NanoGPT's Private
Mode models. Every request Claude Code makes is sealed on your computer to a Tinfoil enclave whose
attestation was checked first; NanoGPT only relays ciphertext. Plain `claude` keeps running as
usual, on Anthropic's models.

Its window sets it up (your NanoGPT key, the switch for your editor and which folders it covers),
chooses the models and how Claude Code searches the web, checks the enclave, opens Claude Code in a
folder, shows each session running on it and where its last request went, and what each request
cost, never what was said. **Claude Code runs on DA Private Coder only while it is open**: once you
quit it, nothing more is sent. Closing its window keeps it open in the tray (see
[While it's open](#while-its-open)).

For convenience it also runs Claude Code on models that **aren't** end-to-end encrypted, and says so
wherever you choose them: NanoGPT's TEE models, each enclave attested before anything is sent to it,
and standard models of NanoGPT, OpenRouter or another OpenAI-compatible provider, in the clear (see
[TEE and standard sessions](#tee-and-standard-sessions)). `claude-private` is only ever private.

## What is private, and what isn't

- **Sealed:** everything Claude Code sends to the model: your prompts, the files and command
  output it reads, and images (for a model that can't see, a private model describes them).
- **Checked before anything is sent:** the enclave's hardware report (AMD SEV-SNP or Intel TDX),
  checked against Sigstore's record of Tinfoil's router release, again every five minutes (the time
  the computer slept included), every fetch of it over https only, redirects included. A reply
  must come back sealed by the enclave and marked as Private Mode, or it is refused. A private model
  NanoGPT has retired is refused before anything is sent.
- **NanoGPT sees:** your account, which model, when, how much, and how many tokens.
- **The prompt cache is yours:** each session's cache key is a secret of its own, inside the seal,
  so no one else (NanoGPT included) can probe it.
- **Switched off in a session:** Claude Code's telemetry, error reports, auto-updates, `/feedback`,
  `/bug` and `/share`, release notes, claude.ai's connectors, WebFetch's check of each site with
  Anthropic, and Anthropic's web search (DA Private Coder has its own, below).
- **Not covered:** web searches (see [Web search](#web-search)), and what Claude Code's tools do on
  your computer. WebFetch fetches pages itself (the site sees your address; an address that names
  this computer or you, or holds a secret, is refused before it's fetched, as a search is), Bash runs
  what it runs, and MCP servers you've added talk to whoever they talk to.

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
3. **Start Claude Code**: choose a folder, and **Open in VSCodium** (or VS Code), or **Open in a
   terminal**. **Private** is chosen; **TEE** and **Standard** aren't end-to-end encrypted. To type
   `claude-private` in a terminal yourself instead, **Install** the terminal commands (optional).
4. **Running now** lists each Claude Code session on DA Private Coder: its folder, the editor or
   terminal it's in, its kind, and the model its last request went to and how (sealed to the
   enclave, or not). Claude Code not listed there runs as usual, on Anthropic's models.

In a session, `/status` shows the base URL as `http://127.0.0.1:…`: that is DA Private Coder.
`/model` offers the profile's models (Sonnet is GLM-5.3, Opus Kimi K3, Haiku DeepSeek V4.1 Flash) and
the other models NanoGPT's Private Mode offers now. The model Claude Code shows is the one that runs:
one a session won't send to (a Claude model in a private session, say) is refused, saying why, and
never swapped for another; Claude Code then keeps the model it had.

## In your editor

In VSCodium and VS Code you choose per folder: some folders' Claude Code uses DA Private Coder, and
the rest stay normal Claude Code, on Anthropic's servers with your own settings. The window's
**Claude Code in VSCodium and VS Code** section has:

- a **Turn on** / **Turn off** switch for each editor;
- the list of **Folders** that use DA Private Coder, each set to Private, TEE or Standard. A listed
  folder includes the folders inside it. **Add** puts a folder on the list (as Private), and **×**
  takes it off;
- **All other folders**: normal Claude Code by default, or one of the three kinds.

**Open in VSCodium** (or VS Code) in **Start Claude Code** does all of this in one step: it puts the
folder on the list as the kind chosen there, turns DA Private Coder on in that editor, and opens the
folder.

The switch is for the whole editor, because the extension reads its process wrapper
(`claudeCode.claudeProcessWrapper`) only from your user settings. When it's on, DA Private Coder's
`claude-editor` starts every Claude Code session in that editor and runs it as its folder's setting
says. A session in a folder set to normal Claude Code is exactly what the editor would have started
on its own, with nothing of DA Private Coder's in it, whether DA Private Coder is open or not.

A conversation that is already open keeps the setting it started with. If its folder's setting
changes, or the editor is turned off, it stops at its next request and says why. Open a new
conversation (a new Claude Code tab) to use the new setting. **Running now** shows which sessions
will stop.

## While it's open

DA Private Coder runs Claude Code only while it is open (its window, or `daprivatecoder --browser`).
Closing the window doesn't close it where your desktop has a tray: it stays there, its sessions keep
working, and the first time a notification says so. Its tray icon opens the window again (as does
launching it again), and **Quit**, in its menu or at the bottom of the window, closes it. Without a
tray (GNOME without the AppIndicator extension, say), closing the window quits.

Once it has quit, every session on it stops at its next request, saying "DA Private Coder is closed,
so nothing was sent"; opened again, each goes on where it was. `claude-private` in a terminal doesn't
start at all while it's closed, and the editor's Claude Code in a folder chosen for it says so in the
conversation. Nothing reaches NanoGPT without it open.

A request NanoGPT or the provider refuses is said as it is: an empty balance as "your NanoGPT
balance is too low for this request", which Claude Code doesn't ask again (it would ten times, calling
it a temporary server-side issue); a busy or failing provider, it does.

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
starts: low, medium, high or max; high by default, and none of them recommended — for coding, how
much thinking a step is worth is up to each use case. In a session, Claude Code's own effort
changes it: the Effort control of the editor's Claude Code, or `/effort` (and `--effort`) in a
terminal. The model runs at the level Claude Code shows, or
the nearest it takes, the higher of two as near: the private models take only low, high and max, so
medium runs as high and xhigh as max (a private model refuses any other level, and GLM-5.3 ignores
one and reasons at its own default, the most). A TEE or standard model gets the nearest of the levels
its provider lists for it; one that refuses a level, naming those it takes, is asked again with one.

**Context** in the window (`context` in the file) is where Claude Code compacts the conversation
(summarises it, and goes on): 200K by default, which is what Claude Code assumes for models it
doesn't know, or 400K, or 1M, what NanoGPT lists for these. Claude Code sends the whole conversation
with every request, so a bigger one costs more each time, even from the cache: at 1M, about $1.60 a
Kimi K3 request. The window says so if a model of the profile holds less than the size chosen.

Each model's reasoning shows as Claude Code shows Claude's thinking (collapsed in the editor; Ctrl+O
in a terminal), unless Claude Code's thinking is off; the model still reasons then, at its effort. As
with Claude, what Claude Code sends back of it goes to the model with the rest of the conversation.
A provider that won't take it back is asked again without it, from then on (the log says so). It
never goes to a Claude model: Anthropic's API takes only its own thinking.

A model that sees images (Kimi K3, DeepSeek V4.1 Flash) gets them as they are. Only one that
doesn't (GLM-5.3) has them described, by the **Images** model, also a private one.

## TEE and standard sessions

Neither is end-to-end encrypted. The window warns of it where you choose one, and **Usage** marks
each request: sealed, TEE, or clear.

**TEE** sessions run NanoGPT's `TEE/` models (by default GLM-5.3, Kimi K3 for `/model opus`, and
GLM-5.3 Flash for quick tasks and images). Before anything is sent to one, its enclave is attested,
and again every 15 minutes, the time the computer slept included: its Intel TDX quote is verified up
to Intel's root (every certificate in its chain one allowed to issue it), with Intel's revocation
lists and TCB ratings, and its GPUs' evidence by NVIDIA's attestation service, all bound to a nonce
of this session's (DA Vibe Manager's verifier). An enclave that doesn't hold gets nothing.

Each reply is then checked against the record NanoGPT keeps of it, signed by the attested key: the
record's hash of the reply must be that of the exact bytes that came, and where RedPill's gateway
keeps a signed receipt, it must hold too, saying the model the reply came from was verified. The
check comes once the reply has reached Claude Code: it never holds a reply up or changes it.
**Usage** marks each TEE request with what it found:

- **reply verified**: the record is of the reply that came, byte for byte;
- **reply signed**: signed by the attested key, but NanoGPT rebuilds this model's replies, so the
  text can't be compared;
- **⚠ reply not verified**: no record of the reply could be read (some models' providers keep none),
  so nothing shows it came from the attested enclave;
- **⚠ reply check failed**: the reply doesn't match its record. The enclave is attested again before
  anything more is sent to that model.

NanoGPT's TEE models attest only in part for now, which **Enclave checks** in the window says, and
why: most pass through RedPill's gateway, whose enclave is checked but not the model's own; and
through NanoGPT the receipts' keys aren't in the quote. The quote binds the key that signs each
reply's record, so the record's tie to the attested enclave holds; the receipt's own key is only
published beside it, so a receipt's word that the model was verified rests on NanoGPT, not on the
hardware.

What an attestation doesn't prove: which software the enclave runs (no measurement is compared with a
published one: it shows genuine, current hardware holding a live key bound to this session's nonce,
not what runs on it), or what was sent (the record hashes the request as NanoGPT forwarded it, which
this app can't reproduce). And either way, **NanoGPT's gateway reads every prompt and reply in the
clear**: the enclave protects them from the host that runs the model, not from NanoGPT. A TEE session
never sends to a model in the clear: a request for one is refused, saying so.

**Standard** sessions run any model of their provider, **in the clear** to it and to the host that
runs the model:

- **NanoGPT** (with your NanoGPT key), **OpenRouter** (with an OpenRouter key), or **another
  provider** with an OpenAI-compatible API (its address: https, or http only on this computer or
  your network; a key if it needs one). Each key is in your keyring, set in the window. A user name
  and password written into the address (`https://user:password@host/v1`) are kept in your keyring
  too, not in the settings file, and nothing the app shows or logs names them.
- **Runs on** (per model, NanoGPT and OpenRouter): the provider's own choice of host (on NanoGPT,
  what a subscription covers), or the fastest, the cheapest, the quickest to answer, or a host you
  pick from the provider's list, with its precision, price and speed; FP8 or better by default.
  **On NanoGPT a host you choose is paid per use, even for a model your subscription includes.**
- A Claude model (`anthropic/…`) goes to the provider's Anthropic endpoint as Claude Code wrote it.
  A name of Claude Code's own (`claude-opus-…`) is refused, so nothing goes to Claude unless you pick
  it that way; only its quick background work under a Haiku name goes to the profile's quick model.
- A `private/` model you pick with `/model` in a standard session is still sealed, and a `TEE/` one
  on NanoGPT attested; once a private model has been used, nothing more of that session goes in the
  clear.

Both kinds have their own models, effort and web search (**Models and web search** in the window,
with the same Private, TEE, Standard choice), and start from the window, a folder chosen for them in
the editor, or `daprivatecoder claude --profile tee` (or `standard`) in a terminal.

## Web search

Claude Code's own web search runs on Anthropic's servers, so DA Private Coder gives it one of its
own, through NanoGPT. **No search is end-to-end encrypted**, whichever you choose: NanoGPT and the
search provider read every query.

| Choice | Searches with | What the provider keeps |
|---|---|---|
| Keep nothing (the default) | Linkup | Nothing: zero data retention. It finds less, and a failed search isn't retried elsewhere. |
| Best results | Perplexity to learn things, Kagi to find sites | Up to their own policies: not zero data retention. Better results. |
| No web search | — | — |

Whatever the choice, Claude Code asks you before each search and shows you the query (an earlier
"don't ask again" doesn't skip it); a query that names this computer or you (your login, real name,
host name, home folder, an e-mail, MAC or private IP address) or holds a secret (an API key, a token,
a private key, a password in an address, a long random string) is refused before it goes, read as the
search provider reads it, without characters no one can see; and a session makes at most 40
searches. With Best results, a provider that fails on NanoGPT's side is replaced by Valyu, and if
your NanoGPT account requires zero data retention, by Linkup; the result tells the model which ran.
Results are untrusted: text in them that looks like instructions to the model is named in the result
(which you see too), and hidden text is taken out before the model reads them.

The address of every page WebFetch reads is checked the same way before Claude Code fetches it, by
a hook DA Private Coder gives the session: one that carries this computer's names, an e-mail, MAC or
private IP address or a secret in its path, query or subdomains is refused, and Claude Code is told
why.

## Commands

**Install** in the window puts these in `~/.local/bin`, as small scripts that run the AppImage where
it is (they follow it when it moves). They are only for typing in a terminal: the window opens
Claude Code without them, and the editor runs a script of its own (`claude-editor`). Each Claude Code
session needs DA Private Coder open.

| Command | What it does |
|---|---|
| `daprivatecoder` | The window (`--browser`: in your web browser) |
| `claude-private [args]` | Claude Code on the private profile; any of `claude`'s arguments work |
| `daprivatecoder claude [--profile NAME] [args]` | Claude Code on a profile: `tee` and `standard` aren't end-to-end encrypted |
| `daprivatecoder editor on\|off\|status [vscodium\|code] [--profile NAME\|usual]` | The switch for Claude Code in the editor; with `--profile`, how any folder not chosen runs |
| `daprivatecoder editor folder PATH private\|tee\|standard\|usual` | How Claude Code in the editor runs in a folder (and the folders in it) |
| `daprivatecoder key set\|clear\|status [--provider openrouter\|custom]` | An API key, in your keyring (GNOME Keyring or KWallet): NanoGPT's unless said |
| `daprivatecoder check [--profile NAME]` | Attest the enclaves (a TEE profile's models too); list the models, their prices, and any your profile uses that are gone |
| `daprivatecoder log [-n N]` | The last requests and searches: model, sealed or not, tokens, cost |
| `daprivatecoder status` | What is set up here |

In `claude -p` (no one to ask), the web search tool runs only if you allow it:
`--allowedTools mcp__daprivatecoder__web_search`.

## Limits

- A private profile only takes `private/` models, and a TEE one `TEE/` (or private) ones; a settings
  file that says otherwise doesn't load, and the window won't save one.

## How it works

`claude-private` (with DA Private Coder open) makes the proxy's socket on 127.0.0.1, at a free port, and starts the proxy
(`daprivatecoder serve`) with it and a random token for the session. It then becomes Claude Code,
pointed at the proxy with environment variables and the same values in `--settings`, which outrank
any settings file but the managed ones. With web search on, it also gives Claude Code the search
server (`daprivatecoder search-server`, an MCP server). The proxy goes when Claude Code does. The
editor's switch names `claude-editor` (`~/.local/share/daprivatecoder/editor/`), which does the same
with the extension's own Claude Code in a folder chosen for it, and runs it as usual in any other.

The proxy takes Claude Code's Anthropic requests and, for a private model, turns them into chat
completions. Each is sealed with EHBP (HPKE X25519 and AES-GCM) to the attested enclave's key and
sent through NanoGPT's relay; the replies come back as Anthropic events. A TEE model's are translated
the same way and sent through NanoGPT once its enclave has attested; a standard model's go to its
provider, with the host chosen for it; a Claude model's to the provider's Anthropic endpoint as they
are. Before each one, the proxy asks whether it may: DA Private Coder holds a lock while it's open,
which ends with it however it ends, and a session the editor started reads the editor's switch and
its folder's choice (`~/.config/daprivatecoder/editor-folders.json`). The session's token is in a
`--settings` file only you can read (`~/.local/state/daprivatecoder/sessions/`, gone with the session),
not on Claude Code's command line, which every user of the computer can see. The Private Mode client, the TEE verifier, the routes and the translation come from DA Vibe
Manager, after DAToolkit and SealedLore.

The window is a small local web page (served on 127.0.0.1 with a token for each launch) shown with
your system's GTK and WebKitGTK, as DA Vibe Manager's is. In a web browser, it's opened on a one-time
code, which the page exchanges for the token once, within two minutes, and only for a program of
yours: the token itself is on no command line, which every user of the computer can read. A link or
file dropped on the window doesn't load in its place.

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

In the window: **Turn off** each editor, and **Remove** the keys. Then delete the AppImage,
`~/.local/bin/claude-private`, `~/.local/bin/daprivatecoder`, `~/.local/share/daprivatecoder` (the
editor's script), and if you like `~/.config/daprivatecoder` (the settings and the editor's folders)
and `~/.local/state/daprivatecoder`.

Licence: AGPL-3.0-or-later.
