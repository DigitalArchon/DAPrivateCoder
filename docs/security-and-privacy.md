# Security and privacy

How DA Private Coder handles your security and privacy: what leaves your computer and who gets it,
what is checked and how, what is kept on your computer, and what the app can't protect you from.
It describes the app as it is now (November 2026). The [README](../README.md) says how to use each
part; this says what each part does, and what it doesn't. To report a vulnerability, see
[SECURITY.md](../SECURITY.md).

## Contents

- [In short](#in-short)
- [Who the app talks to](#who-the-app-talks-to)
- [The network rules](#the-network-rules)
- [Keys and passwords](#keys-and-passwords)
- [How a session runs: the proxy](#how-a-session-runs-the-proxy)
- [Private sessions](#private-sessions)
- [TEE sessions](#tee-sessions)
  - [What is attested](#what-is-attested)
  - [Each reply's check, and its mark](#each-replys-check-and-its-mark)
  - [Naming the provider behind a reply](#naming-the-provider-behind-a-reply)
- [Standard sessions](#standard-sessions)
- [In your editor](#in-your-editor)
- [Web searches and the pages Claude Code reads](#web-searches-and-the-pages-claude-code-reads)
- [The window](#the-window)
- [What is kept on this computer](#what-is-kept-on-this-computer)
- [What the app can't protect you from](#what-the-app-cant-protect-you-from)
- [Reporting a vulnerability](#reporting-a-vulnerability)

## In short

**What leaves your computer:**

- **What Claude Code sends its model**: your prompts, the files and command output it reads, and
  images. In a **private** session it is sealed on this computer for an attested enclave, and
  NanoGPT relays what it can't read. In a **TEE** session it goes to NanoGPT in the clear, and on to
  an attested enclave. In a **standard** session it goes in the clear to your provider and the
  company that runs the model.
- **Web searches**, through NanoGPT to a search provider, in the clear, whatever the session.
- **The app's checks of an enclave**, before anything is sent to it: to NanoGPT, Intel, NVIDIA,
  Tinfoil and Sigstore, and to RedPill only if you turn that on.
- **What Claude Code does itself**: the pages WebFetch reads, what Bash runs, and the MCP servers
  you added. The app checks WebFetch's addresses; the rest is Claude Code's, as usual.

**What never does:**

- **Your API keys**, except each to its own provider with your requests. They are kept in your
  keyring, never in a file, a log or a message. Claude Code never holds them: it has a token for
  its session's proxy, which adds the key on the way out.
- **A password written into an address**, except to that address. It is kept in your keyring, and
  the address is shown and logged without it.
- **What was said**, in the app's own records: its request log keeps models, tokens, costs and how
  each request went, never a prompt, a reply, a file, an image or a search's query.
- **Anything of a private session in the clear**: a private model is sent sealed or not at all,
  and once a session has used one, nothing more of it goes in the clear.
- **Anything of a session's while DA Private Coder is closed**: a session on it stops at its next
  request, and its web search at its next search.
- **Your computer's names or a secret in a web search**, or in an address WebFetch reads: the app
  refuses such a search or address in code.

## Who the app talks to

Every connection the app makes, and why. **Usage** in the window lists each request to a model and
each search, with how it left this computer; the connections that check an enclave or a reply,
below, aren't listed there: what each reply's check found is shown as its mark.

### NanoGPT (nano-gpt.com)

Your AI provider for private and TEE sessions, and for a standard session on NanoGPT; always over
HTTPS. With your NanoGPT key, the app sends it:

- Claude Code's requests to its model, translated or forwarded by the session's proxy
  ([below](#how-a-session-runs-the-proxy));
- for a private model: the Private Mode preflight before each request (NanoGPT checks your balance
  and reserves the charge), and the sealed request itself ([Private sessions](#private-sessions));
- for a TEE model: the request for its attestation, and, after each reply, for the record NanoGPT
  keeps of it ([TEE sessions](#tee-sessions));
- the web searches ([below](#web-searches-and-the-pages-claude-code-reads)).

Without a key: its list of models, with their prices and what each can do; the list of Private Mode
models; a private model's attestation, which comes through NanoGPT's relay; and the list of hosts
that run an open model, when you open its **Runs on** choice.

NanoGPT can read everything sent to a TEE or standard model. Of a private model's requests it sees
your account, the model, when, how much and what it costs, not what was said.

### OpenRouter, or a provider of your own (standard sessions)

Only for a **standard** profile set to it. Claude Code's requests go there in the clear, with that
provider's key, as does a request for its list of models (OpenRouter's and its list of hosts need
no key; a provider of your own gets its key with the request for its list, if you stored one).

### The companies that run the models

Each provider passes a request on to whoever runs the model: one of many hosts for an open model
(the provider's choice, or the one you choose under **Runs on**), Anthropic for a Claude model, the
TEE provider for a TEE model, Tinfoil's enclave for a private one. The app never talks to them
itself. Each reads what it is sent, except a private model's requests, which only the attested
enclave can open.

### Tinfoil, Sigstore and GitHub (private models)

When a private model's enclave is attested (before a session's first private request, and again
once that attestation is five minutes old), Tinfoil's own software, pinned to an exact version,
checks the attestation that comes through NanoGPT's relay: against AMD's or Intel's roots for its
hardware, and against Sigstore's public record of the release of Tinfoil's router it runs. For
that it fetches Sigstore's trust root (tuf-repo-cdn.sigstore.dev), the router's latest release and
its record through Tinfoil's proxy of GitHub (github-proxy.tinfoil.sh), and for AMD hardware the
processor's certificate through Tinfoil's proxy (kds-proxy.tinfoil.sh) or, for Intel, Intel's
records ([below](#intel-tee-models)). These are public records: nothing of yours goes, and no key.

### Intel (TEE models)

When a TEE model is attested: Intel's public certificates, revocation lists and its ratings of the
platform's firmware (api.trustedservices.intel.com, certificates.trustedservices.intel.com), to
check the enclave's quote up to Intel's root. The request names the platform family the quote is
from; nothing of yours goes, and no key.

### NVIDIA (TEE models)

When a TEE model is attested: the GPU evidence in its attestation, made for the app's fresh random
number, goes to NVIDIA's attestation service (nras.attestation.nvidia.com), and its signed verdict
is checked with the keys NVIDIA publishes there. Nothing of yours goes, and no key.

### RedPill (api.redpill.ai): only with Name the provider behind each TEE reply

Off unless you turn it on (**Enclave checks** in the window). With it on, after a TEE reply whose
signed receipt holds, the app fetches RedPill's public record of the session that served it, to
name the provider that ran the model ([below](#naming-the-provider-behind-a-reply)). The request
is the session's id in the record's address, and nothing else: no key, none of your messages.
RedPill then sees your computer's internet address, and that one of its models was used around
then. It goes over HTTPS, by the same rules as the app's other checks.

### Search providers (through NanoGPT)

The app never talks to a search provider itself: NanoGPT passes each search on, to Linkup with
**Keep nothing** (the default), or to Perplexity and Kagi with **Best results**, and then to Valyu
when the chosen one fails on NanoGPT's side, or to Linkup when your NanoGPT account requires zero
data retention. The provider reads the query; what it keeps is its own policy, except Linkup's,
which keeps nothing.

### Anthropic

The app never sends anything to Anthropic but a request for a Claude model you chose in a standard
session, which goes to your provider's Anthropic endpoint (NanoGPT's or OpenRouter's), not to
Anthropic directly. In a session, Claude Code's own traffic to Anthropic is switched off: its
telemetry, error reports, auto-updates, release notes, `/feedback`, `/bug` and `/share`, the
feedback survey, claude.ai's connectors, the official plugin marketplace's automatic install,
WebFetch's check of each site with Anthropic, and Anthropic's own web search. Claude Code you start
as usual (plain `claude`, or a folder not chosen for DA Private Coder) is Anthropic's, untouched.

## The network rules

- **HTTPS only.** Every address the app sends anything to is HTTPS, except an address on this
  computer or your local network (localhost, the loopback and private ranges 10.0.0.0/8,
  172.16.0.0/12, 192.168.0.0/16 and fc00::/7, link-local addresses and `.local` names) or a Tor
  onion service (`.onion`, which Tor itself encrypts and authenticates end to end), where plain
  `http://` is allowed. An address that is neither is refused, saying why, in the settings file and
  in the window alike: it is never used, and never quietly rewritten.
- **Redirects.** A provider's redirect is never followed, since the request, your words in it and
  the key would go wherever it points: the request fails, and Claude Code is told so. Nor is a
  redirect followed in a TEE model's checks, or for RedPill's record. Tinfoil's checks of a private
  model follow one only to HTTPS, and never from a server on the internet into this computer or
  your network. Sigstore's trust root is fetched by Sigstore's own library, by its own rules; what
  it fetches is signed, and checked.
- **Proxies.** The app has no proxy setting of its own: its requests use your system's proxy
  settings (`HTTPS_PROXY` and the like), as most programs do. It doesn't run Tor or send anything
  through it itself.
- **Its own servers** listen on 127.0.0.1 only: the window's, and one proxy for each session. Each
  does what it is asked only with its own random token ([below](#the-window)).

## Keys and passwords

- **Your API keys** (NanoGPT's, OpenRouter's, a provider of your own's, each server its own) are
  kept in your system's keyring (GNOME Keyring or KWallet), never in a file. Without a secure
  keyring the app keeps no key at all, rather than one in a plain file, and says what to install.
  Once read, a key is kept in memory while that part of the app runs, so that a keyring that locks
  itself again doesn't stop a session. A key goes only to its own provider, with your requests:
  the session's proxy adds it on the way out, and Claude Code never has it. It isn't in any log,
  message or error. A key with a space, a line break or a character no key has in it is refused
  when it is stored and whenever it is read, without being repeated back.
- **A password written into an address** (`https://user:password@host/v1`, for a provider of your
  own behind a password, or in NanoGPT's address) goes to your keyring with the address as typed;
  the settings file has the address without it. It is put back only while the file's address is
  that very one without it, so it goes to no address but the one it was typed for. Wherever an
  address is shown, logged or said to Claude Code, the user name and password are left out. If the
  keyring can't take it, nothing is saved. One you wrote into the settings file by hand is moved to
  the keyring when the file is next read, and stays in the file only while the keyring can't take
  it.
- **The tokens** that let Claude Code use its session's proxy, and the window use its server, are
  random, made for each run, and never on a command line, which anyone logged in to the computer can
  read. A session's token is in a settings file only you can read, gone with the session.

The app doesn't warn before a key would go over plain `http://` to another machine on your network:
a provider of your own may have such an address, and its key, and a password written into its
address, then go unencrypted to it. Anyone on that network could read them. Use the server's
`https://` address where it has one.

## How a session runs: the proxy

Every Claude Code session on DA Private Coder (from the window, from `claude-private` or
`daprivatecoder claude`, or from your editor) talks only to a proxy of its own, which the app
starts with it on 127.0.0.1 and which ends with it:

- **Its own token.** Claude Code is pointed at the proxy with a random token for that session, in
  `--settings`, which outrank any settings file but your managed ones. The proxy answers nothing
  without it. Claude Code's own ways to reach a model otherwise (an Anthropic API key, Bedrock,
  Vertex, Foundry) are emptied for the session.
- **Only what Claude Code needs**: its messages, counting their tokens, and its list of models.
  Nothing else is passed on.
- **Only the session's kind of model.** A request for a model below the session's kind (a model in
  the clear in a private or TEE session) is refused, saying why; nothing is ever swapped for
  another model you didn't choose. Only Claude Code's own quick background work, under a Haiku
  name of Claude's, goes to the profile's quick model, of the same kind.
- **Only while DA Private Coder is open.** Before each request the proxy asks whether it may: the
  app holds a lock while it is open, which ends with it however it ends, and a session your editor
  started also reads the editor's switch and its folder's choice. Closed, or switched off, the next
  request is refused, saying so, and nothing is sent. A request already under way when you quit
  finishes, its reply's check included. The session's web search asks the same before each search
  ([below](#web-searches-and-the-pages-claude-code-reads)).
- **Nothing kept of what was said.** Each request is logged with its model, how it was sent,
  status, tokens, cost and timing, and for a TEE model what its reply's check found; never its
  content.

## Private sessions

NanoGPT's `private/` models (Private GLM-5.3, Kimi K3.1 Pro, DeepSeek V4.1 Flash) run in Tinfoil's
enclaves, and are end-to-end encrypted:

- **Attested before anything is sent.** Before a session's first private request, and before any
  request once the attestation is five minutes old (on a clock that counts while the computer
  sleeps), or when the enclave says its key changed, the app checks the enclave's attestation
  ([Tinfoil, Sigstore and GitHub](#tinfoil-sigstore-and-github-private-models)). One that doesn't
  hold refuses the request: nothing is sent. The app says when the enclave runs an older release
  than the latest (it is still verified).
- **Sealed on this computer.** Each request is sealed (EHBP: HPKE X25519 and AES-GCM) to the key the
  attestation proved only the enclave holds, and the reply comes back sealed the same way. NanoGPT
  relays what it can't read: your account, the model, when, how much and what it costs.
- **Sealed, or not at all.** A private model is never sent anything any other way: a reply that
  isn't sealed, isn't marked as Private Mode, or ends before its last part is refused, and nothing
  falls back to a plain request. A model NanoGPT no longer offers is refused before anything is
  sent.
- **The whole session.** In a private session, everything goes sealed: Claude Code's background
  tasks, and the images it reads, which a private model describes for one that can't see them.
- **The prompt cache** is the session's own: its key is made on this computer, sealed with each
  request, and kept in memory only, so nobody else, NanoGPT included, can probe it.

## TEE sessions

NanoGPT's `TEE/` models run in trusted execution environments, but aren't end-to-end encrypted:
what you send them passes NanoGPT's servers in the clear, and NanoGPT can read it. The enclave is
there to keep it from whoever runs the machine, as far as the attestation shows (below). A TEE
session takes only TEE (or private) models: a request for any other is refused. A TEE model picked
in a standard session is attested the same way.

### What is attested

**Before anything is sent** to a TEE model, and again before a request once its attestation is 15
minutes old (on a clock that counts while the computer sleeps), the app asks for the enclave's
attestation, with a fresh random number of its own, and checks:

- that the Intel TDX quote, read from its own signed bytes, binds the enclave's signing key to that
  number; where NanoGPT has it, RedPill's gateway's own report too, for another fresh number, whose
  quote binds all the gateway's keys, those that sign each reply's record and its receipts;
- that each quote verifies up to Intel's root (every certificate in its chain one allowed to issue
  it), isn't revoked or a debug enclave, and what Intel says of the platform's firmware;
- that NVIDIA's signed verdict on the GPUs is for the app's number, and passes.

Anything forged, revoked or made for another number is refused, and nothing is sent. What couldn't
be checked, or isn't current, makes the attestation **partial**: requests still go. **Enclave
checks** in the window (and `daprivatecoder check --profile tee`) says what was attested, first
what that leaves to someone's word, and each shortfall:

- **TEE gateway attested**: most of NanoGPT's TEE models are served by RedPill, and what is attested
  is RedPill's gateway, which NanoGPT sends their requests through and which passes them on to the
  machine that runs the model (in November 2026 Phala's, Fortanix's, NEAR AI's, 0G's, Tinfoil's
  router or Chutes'). The app doesn't attest that machine: that it is a verified TEE is the
  gateway's word, in each reply's signed receipt ("the model's own enclave unchecked"). The GPU
  evidence in NanoGPT's report is made for the app's number, but the gateway's quote doesn't commit
  to it, so the GPUs may be another machine's ("GPUs not tied to the quote"). A model whose own
  attestation can't be had for the app's number (relayed for another, or too slow) is attested
  through the gateway alone, with "no GPU evidence" too.
- **Each instance**, for a model Chutes serves itself: each instance's quote binds its own key and
  GPU evidence. Chutes signs no replies, so nothing shows which instance answered: NanoGPT routes
  each request, and a model may be served elsewhere when its provider is busy. Where RedPill's
  gateway answers some of these models' requests instead, it is attested too ("RedPill's gateway
  unchecked" when NanoGPT couldn't give its report just then).
- Where the receipts' keys aren't bound by any quote, "receipt keys not in the quote": a receipt
  then rests on NanoGPT's word, not on the hardware.

A model offering no attestation the app can check for its own number (the Tinfoil-hosted `TEE/`
models) is refused. The app has no setting that refuses a partial attestation: a partial one is
sent to, and said.

### Each reply's check, and its mark

**After each reply**, once it has reached Claude Code (it is never held up or changed), the app
fetches the record NanoGPT keeps of it, signed by an attested key, and checks it against the exact
bytes that came back; and where the gateway kept a signed receipt for the reply, that too, under
the attested keys: for the same exchange, saying the machine the reply came from was verified,
which is the gateway's word. A TEE **Images** model's description of an image is a reply too,
checked the same way. **Usage** and **Running now** mark each TEE request with what its reply's
check found:

- **TEE, reply verified**: the attested key signed this reply's record, for the exact text that
  came back, and its receipt (where read) holds.
- **TEE, reply signed**: signed by the attested key, but NanoGPT rebuilds this model's replies, so
  the text can't be compared with the record.
- **TEE ⚠ reply not verified**: no record of the reply could be read (Chutes keeps none, or the
  check couldn't be made), so nothing shows it came from the attested enclave. A model attested
  through the gateway alone has its own enclave only as the gateway's word, so a reply of it with
  no receipt that holds is marked so too.
- **TEE ⚠ reply check failed**: the reply doesn't match its record. The app attests the model
  again before anything more is sent to it.

Hovering over a mark says what it means, what in it is the gateway's word, and that what was sent
passed NanoGPT in the clear. The request itself is marked only as far as its reply shows: **Running
now** says it was received by the attested TEE (its gateway, for a model RedPill serves) only when
its reply is verified or signed. A request with no reply to check, because it was refused, stopped
or cut short, is marked **TEE ⚠ not shown to reach the enclave**: it went while the model's
attestation held, but NanoGPT routes each request, and only a record the attested key signed shows
the enclave received it.

What a TEE attestation doesn't prove: which software the enclave runs (no measurement of it is
compared with a published one), what was sent (the record holds the request as NanoGPT forwarded
it, which the app can't reproduce), and, for a model RedPill serves, which machine ran the model.
For prompts nobody else can read, use a private session.

### Naming the provider behind a reply

**Enclave checks → Name the provider behind each TEE reply** (off by default; `tee_name_upstreams`
in the settings file) shows who ran the model beside the reply's mark: "TEE, reply verified · run
by Phala", say. Each receipt names the gateway's session with the machine that answered by a hash
of RedPill's public record of that session, which says which provider it is. With the option on,
after a reply whose receipt holds, the app fetches that record from api.redpill.ai and believes it
only if it is exactly the record the receipt names, and the reply was served within the session's
time. The cost to your privacy:

- it connects to api.redpill.ai directly, not through NanoGPT, so RedPill sees your computer's
  internet address and that one of its models was used around then;
- only the session's id is sent, never your messages or your key;
- each of RedPill's records is fetched at most once by each Claude Code session's proxy (one of its
  sessions serves a provider for about an hour), and is a few kilobytes to a few hundred: one over
  1 MB isn't read;
- it is never fetched for a model Chutes serves: each Chutes instance has a session of its own,
  megabytes each, and replies are spread across them.

Sessions already running follow the switch from their next reply: switched off, nothing more is
asked of RedPill. What the record says is RedPill's word: the app doesn't check the machine it
names. A record that can't be fetched or believed names nobody, and the mark's tooltip says why;
the reply's mark never changes.

## Standard sessions

A standard session runs any model of its provider (NanoGPT, OpenRouter, or an OpenAI-compatible
provider of your own), **in the clear**: the provider and the company that runs the model read every
prompt, file and reply, and keep them under their own policies. A provider's host you choose under
**Runs on** is asked for by name; NanoGPT and OpenRouter may still fall back to another host if
it's down.

- **A Claude model** you choose (`anthropic/…`) goes to the provider's Anthropic endpoint as
  Claude Code wrote the request, with the provider's key, less Claude Code's metadata (this
  computer's device id and the session's id) and the reasoning blocks of other models. A name of
  Claude Code's own (`claude-opus-…`) is refused, so nothing goes to Claude unless you pick it that
  way.
- **A `private/` or `TEE/` model** you pick in a standard session is still sealed, or attested, as
  in those sessions. Once a private model has been used, nothing more of that session goes in the
  clear: everything said so far is in every request.
- **Images** are described for a model that can't see them by the profile's **Images** model, but
  only by one at least as protected as the request they are in (a private one for a private
  model's): without one, the model is told it can't see them.

## In your editor

The window's switch for VSCodium or VS Code sets Claude Code's process wrapper in your user
settings (`claudeCode.claudeProcessWrapper`) to DA Private Coder's `claude-editor`, after keeping a
copy of the settings file beside it. A folder you chose then runs its Claude Code on DA Private
Coder; any other folder's Claude Code is started exactly as the editor would start it, with nothing
of DA Private Coder's in it, whether DA Private Coder is open or not. A conversation keeps the kind
it started with: if its folder's choice changes, or the switch is turned off, it stops at its next
request, saying why. **Running now** lists each session on DA Private Coder, where its last request
went and how, and which will stop.

## Web searches and the pages Claude Code reads

Claude Code's own web search runs on Anthropic's servers, so it is switched off in a session, and
DA Private Coder gives it one of its own: a small server Claude Code starts, which searches through
NanoGPT with your key. **No search is end-to-end encrypted**, whichever session it is in: NanoGPT
and the search provider read each one.

- **Asked first.** Claude Code asks you before each search, showing the query; an earlier "don't
  ask again" doesn't skip it. With no one to ask (`claude -p`), a search runs only where you allowed
  the tool by name.
- **Refused in code.** A query that names this computer or you (your login, real name, host name,
  home folder, an e-mail, MAC or private IP address) or holds a secret (an API key, a token, a
  private key, a password in an address, a long random string) is refused before it goes, saying
  what it found, a secret only by its first characters. It is read as the search provider reads
  it: characters no one can see are taken out first, so they can't hide anything from the check,
  and the query checked is the one sent.
- **Only while DA Private Coder is open.** Before each search, the search server asks what the
  session's proxy asks before each request: the app open, and for a session your editor started,
  its switch and its folder's choice still naming the session's kind. If not, nothing is sent, and
  Claude Code is told why.
- **Limited.** A session makes at most 40 searches. Each is logged with its provider, status, the
  number of results and the cost, never the query.
- **Results are untrusted.** Text in them that looks like instructions to the model is named in the
  result, which you see too, and text no one can see is taken out before the model reads them.

**WebFetch** fetches pages itself, from this computer: the site sees your internet address. Before
Claude Code reads a page, a check DA Private Coder gives the session (a hook Claude Code runs,
whatever its permission mode) reads its address the same way: one that carries this computer's
names, an e-mail, MAC or private IP address or a secret in its path, query, fragment or the start of
its host name is refused, and Claude Code is told why. The site itself is what is read, so its own
name isn't checked.

## The window

The window is a small web page served by the app on 127.0.0.1, shown in a window of your system's
GTK and WebKitGTK, which keeps nothing between runs, or in your web browser. Its server does what
the page asks only with a random token made for that run. The token is never on a command line,
where anyone else logged in could read it: the app's window gets it in its address's fragment, and
a web browser is opened on a one-time code instead, which the page exchanges for the token once,
within two minutes, and only for a program of yours. The page then takes it out of the address bar.
The window's answers are never cached. A link or file dropped on the window doesn't load in its
place.

## What is kept on this computer

- **Settings**: `~/.config/daprivatecoder/config.toml`, with no key in it, and no password but one
  written into it by hand while the keyring couldn't take it ([above](#keys-and-passwords)); the
  folders chosen for your editor (`editor-folders.json`) and the window's own choices
  (`window.json`) beside it.
- **The request log**: `~/.local/state/daprivatecoder/requests.log` (and one older file once it
  passes 5 MB), readable only by you: each session's start (its profile, models, provider address
  without a password, folder and editor), each request (model, how it was sent, status, tokens,
  cost, seconds, and errors without an address's password), each enclave's attestation and what was
  attested, each TEE reply's check (and with the setting on, who ran its model, as RedPill's record
  said), each image described and each search. Never what was said.
- **Running sessions**: `~/.local/state/daprivatecoder/sessions/`, each session's settings file with
  its token, and what it runs where, readable only by you and gone with the session;
  `window.lock`, held while the app is open; and `proxy-errors.log`, what the proxies write when
  something goes wrong.
- **The editor's script** in `~/.local/share/daprivatecoder/editor/`, and, if you installed them,
  `claude-private` and `daprivatecoder` in `~/.local/bin`. With the editor's switch on, its user
  settings name the script, and a copy of them from before is kept beside them
  (`*.daprivatecoder-backup`).
- **Your keyring**: your API keys, and an address typed with a password.
- **Public records the private models' checks fetched**: Tinfoil's software and Sigstore's library
  keep them in their own cache folders (`~/.cache/tinfoil`, `~/.local/share/sigstore-python` and
  `~/.cache/sigstore-python`).
- **Claude Code's own files**: Claude Code keeps its conversations, settings and history in
  `~/.claude`, as it always does, in the clear on this computer, whichever kind of session they
  were. DA Private Coder doesn't change that.

Kept only in memory, and gone when the part of the app that holds them stops: your keys once read
from the keyring, the window's token, the private sessions' cache keys, the enclaves' attestations
themselves (the log keeps what was found), Intel's records and NVIDIA's keys the checks fetched, and
RedPill's session records.

## What the app can't protect you from

- **What isn't sealed is read.** NanoGPT reads everything sent to a TEE model, and NanoGPT or your
  other provider, and the company running the model, everything sent to a standard one. Even a
  private model's requests show NanoGPT when, how much and what they cost.
- **A TEE attestation shows hardware, not software.** It shows genuine, current hardware holding a
  live key, bound to the app's fresh number; not which software that machine runs, and for a model
  RedPill serves, the machine that runs the model is RedPill's word. (A private model's attestation
  checks its software too, against Tinfoil's signed release.)
- **Search providers keep what they keep.** Only Linkup keeps nothing.
- **Claude Code acts on your computer.** It reads your files, runs commands and talks to the MCP
  servers you added, by its own permission rules: DA Private Coder sees only what it sends its
  model, its searches and the addresses WebFetch reads. A page WebFetch reads sees your internet
  address, and text in a page or a file can try to steer the model: read what Claude Code asks to
  run before you allow it.
- **A key over plain http** to another machine on your network is readable by anyone on that
  network ([above](#keys-and-passwords)).
- **Your computer itself.** Anyone, or anything, that already controls your account or your
  computer can read what the app and Claude Code keep there.

## Reporting a vulnerability

Please report it privately, as [SECURITY.md](../SECURITY.md) says, not in a public issue or pull
request. It lists what is in scope: anything that breaks one of the promises above.
