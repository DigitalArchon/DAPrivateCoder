# Security policy

## Reporting a vulnerability

Please email **jason@digitalarchon.com.au** with the details and, if you can, steps to
reproduce. Don't open a public issue or pull request for a vulnerability.

You'll get a reply as soon as I can manage. This is a one-person project with no bug bounty,
but reports are taken seriously and credited in the fix unless you'd rather not be named.

## Supported versions

DA Private Coder is in beta. Only the latest release (or `main`) gets fixes.

## What's in scope

Anything that breaks one of DA Private Coder's promises, as
[docs/security-and-privacy.md](docs/security-and-privacy.md) sets them out, for example:

- a session sending anything, a request to its model or a web search, while DA Private Coder is
  closed, or once its editor's switch or its folder's choice no longer names the session's kind;
- a request going to a model below its session's kind (in the clear in a private or TEE session),
  or a model swapped for one you didn't choose;
- a private model's request leaving this computer unsealed, a reply taken that the attested enclave
  didn't seal, or anything sent to a private model whose attestation doesn't hold;
- anything sent to a TEE model whose attestation doesn't hold, a TEE reply its check passes that
  isn't its attested enclave's, or a mark that says more than was checked;
- an API key reaching Claude Code or going anywhere but its own provider; a secret (an API key, a
  password written into an address) written in the clear to a file, a log or a message; or a
  session's or the window's token reaching anyone else logged in to the computer;
- an address used that isn't HTTPS, but for this computer, your local network or a Tor onion
  service, or a provider's redirect followed;
- something from your computer, or a key or password, getting into a web search or an address
  WebFetch reads, or text aimed at the model in search results reaching it unmarked;
- the editor's switch running Claude Code on DA Private Coder in a folder not chosen for it;
- bypassing a session proxy's or the local server's token, or anything loading in place of the
  window's page;
- anything asked of api.redpill.ai while **Name the provider behind each TEE reply** is off.

Weaknesses in upstream projects (Claude Code, NanoGPT, Tinfoil, WebKitGTK and so on) should go to
those projects, though a heads-up is welcome if DA Private Coder can mitigate them.
