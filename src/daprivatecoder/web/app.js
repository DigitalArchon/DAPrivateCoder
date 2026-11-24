"use strict";
// DA Private Coder's window: what is set up (the keys, Claude Code, the commands, the editor switch),
// the private, TEE and standard profiles' models, the hosts that run them, and their web search, the
// enclaves' attestations, and what it cost. Every change goes through the local server (server.py),
// which writes the same files the commands do.

// in the app window (app.py adds desktop=1), not a web browser; kept for a reload, as the token is
const DESKTOP = (() => {
  const fromUrl = new URLSearchParams(location.search).get("desktop") === "1";
  try {
    if (fromUrl) sessionStorage.setItem("dpc-desktop", "1");
    return fromUrl || sessionStorage.getItem("dpc-desktop") === "1";
  } catch { return fromUrl; }
})();

// The window's token: in the app window's address (#t=, given it in-process: no command line shows it) and
// in the one --no-open prints; in a browser, exchanged once for the one-time code its address carries (#c=:
// what a browser is opened with is on its command line, for every user of this computer to read; app.py
// browser_opener). Kept for this tab, and the address cleared of both, as DA Vibe Manager's window does.
const LAUNCH = /(?:^#|&)c=([\w-]+)/.exec(location.hash)?.[1] || null;
let TOKEN = (() => {
  const fromUrl = new URLSearchParams(location.hash.slice(1)).get("t") || new URLSearchParams(location.search).get("t");
  try {
    if (fromUrl) sessionStorage.setItem("dpc-token", fromUrl);
    const t = fromUrl || sessionStorage.getItem("dpc-token") || "";
    history.replaceState(null, "", "/");
    return t;
  } catch { return fromUrl || ""; }
})();

async function launchToken(code) {
  const res = await fetch("/api/launch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
  let data = {};
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok || !data.token) throw new Error(data.error || data.detail || `${res.status} ${res.statusText}`);
  try { sessionStorage.setItem("dpc-token", data.token); } catch { /* kept for this page only */ }
  return data.token;
}
const $ = (sel) => document.querySelector(sel);
// kept across renders, as the folder typed is: the Models tab shown (a profile), each profile's models and
// the hosts listed for a model, the key whose Replace field is open, a route being chosen (and, per model,
// whether its hosts below FP8 are left out), what is typed and not yet saved (drafts: a key, an address),
// the actions under way (busy: not sent twice), what is being checked
const S = { state: null, folder: "", tab: "", models: {}, modelsError: {}, hosts: {}, routeHost: {}, routeFp8: {},
  customUrl: "", setupOwn: "", replacing: "", checking: "", drafts: {}, busy: {}, asked: 0, shown: 0,
  setupFirst: null, usageOpen: false, verifyOpen: null, modelsAsked: 0, modelsLoading: {} };
// what the editor's switch runs when it runs each folder as chosen (editor.PER_FOLDER)
const PER_FOLDER = ":folders";

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method, headers: { "X-Token": TOKEN, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = {};
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

function toast(text, kind = "info", ms = 6000) {
  const el = h("div", { class: `toast ${kind}` }, text);
  $("#toasts").append(el);
  setTimeout(() => el.remove(), ms);
}

async function guarded(fn) {
  try { return await fn(); } catch (e) { toast(e.message, "error", 9000); }
}

// an action that returns the new state, and what to say when it's done (true when it was). If it
// fails, the page is drawn again from the state as it was, so a choice that wasn't saved (a model,
// a search) doesn't look as if it was.
async function act(method, path, body, said) {
  return guarded(async () => {
    let st;
    const n = ++S.asked;
    try { st = await api(method, path, body); } catch (e) { render(); throw e; }
    if (st && st.version) take(n, st);
    if (said) toast(said, "ok", 3000);
    return true;
  });
}

// a state the server sent, unless one asked for after it is already shown: the 20 s look's answer,
// slow behind the keyring, mustn't undo a choice made meanwhile (Start's kind of session, say)
function take(n, st) {
  if (n < S.shown) return;
  S.shown = n;
  S.state = st;
  render();
}

// an action that can't be under way twice (a double click would open two terminals)
async function once(name, fn) {
  if (S.busy[name]) return;
  S.busy[name] = true;
  render();
  try { return await fn(); } finally { delete S.busy[name]; render(); }
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme || "dark");
}

// a total in cents; one request's cost to a hundredth of a cent (most cost less than one)
const money = (n, exact = false) => (n === null || n === undefined) ? "" : `$${exact && n < 1 ? n.toFixed(4) : n.toFixed(2)}`;
// to the cent, a half cent up (1.575 is $1.58, as the README and the check command say)
const price = (n) => (Math.round(n * 100) / 100).toFixed(2);
// none: a request without usage (refused, failed), which is no tokens to show rather than 0
const tokens = (n) => (n === null || n === undefined) ? ""
  : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n);

// ------------------------------------------------------------------ the three kinds of session

// how each kind's requests travel, and what the window says of it (config.py's kinds; a profile of each name)
const SESSIONS = ["private", "tee", "standard"];
const KINDS = {
  private: { label: "Private", chip: ["private", "end-to-end encrypted"], models: "private models" },
  tee: { label: "TEE", chip: ["tee", "TEE: not end-to-end encrypted"], models: "TEE models (not end-to-end encrypted)" },
  standard: { label: "Standard", chip: ["warn", "not private"], models: "standard models (not private)" },
};
// where a profile's models are (providers.py's PROVIDERS, in its order: tests/test_providers.py holds the two to one
// list): its name in the Provider list (label) and in a sentence, the kinds of session it serves, whether it lists
// what its models cost, and for a provider of your own (own), what its address is
const PROVIDERS = {
  nanogpt: { label: "NanoGPT", name: "NanoGPT", kinds: ["private", "tee", "standard"], prices: true },
  chutes: { label: "Chutes", name: "Chutes", kinds: ["tee", "standard"], prices: true },
  tinfoil: { label: "Tinfoil", name: "Tinfoil", kinds: ["private"], prices: true },
  openrouter: { label: "OpenRouter", name: "OpenRouter", kinds: ["standard"], prices: true },
  anthropic: { label: "Anthropic", name: "Anthropic", kinds: ["standard"] },
  custom: { label: "Another provider (OpenAI-compatible)", name: "your provider", kinds: ["standard"], own: true,
    placeholder: "https://api.example.com/v1", address: "The provider's OpenAI-compatible API address." },
  "custom-anthropic": { label: "Another provider (Anthropic-compatible)", name: "your provider", kinds: ["standard"],
    own: true, placeholder: "https://api.example.com/anthropic",
    address: "The provider's Anthropic-compatible API address, as you would give Claude Code as ANTHROPIC_BASE_URL: "
      + "Claude Code's requests go to it as they are." },
};
// whose a request was, in a sentence ("your provider" for one of your own)
const providerSaid = (id) => (PROVIDERS[id] ? PROVIDERS[id].name : id || "NanoGPT");
// whose key a key field asks for, after "Paste your" ("Paste your provider's API key" for one of your own)
const keyName = (id) => (PROVIDERS[id] && PROVIDERS[id].own ? "provider's" : providerSaid(id));

// the service a session of `kind` is sealed or attested by, by its address (manager.py's services: "tinfoil",
// "chutes", or "" for NanoGPT's own), whichever provider names it
const serviceOf = (st, kind) => ((st && st.services) || {})[kind] || "";
// what a kind of session is, on the service its address is: Tinfoil's private sessions are end-to-end encrypted
// straight to Tinfoil; Chutes' TEE sessions end-to-end encrypted to a Chutes instance, with partial privacy
const SEALED_KINDS = {
  "private:tinfoil": { chip: ["private", "end-to-end encrypted (Tinfoil)"], models: "Tinfoil's models (end-to-end encrypted)" },
  "tee:chutes": { chip: ["tee", "end-to-end encrypted (Chutes, partial)"],
    models: "Chutes' -TEE models (end-to-end encrypted, partial privacy)" },
};
const kindOn = (st, kind) => ({ ...KINDS[kind], ...(SEALED_KINDS[`${kind}:${serviceOf(st, kind)}`] || {}) });
const kindModels = (kind) => kindOn(S.state, kind).models;
const chipOf = (kind) => { const k = kindOn(S.state, kind); return h("span", { class: `chip ${k.chip[0]}` }, k.chip[1]); };

// a provider of your own by its address, without the user name and password one may have in it
function providerName(st, profile) {
  const p = st.config && st.config.profiles[profile];
  if (!p) return "NanoGPT";
  const spec = PROVIDERS[p.provider];
  return spec && spec.own ? (p.base_url_shown || "your provider") : providerSaid(p.provider);
}

// what a session of `kind` is, in a sentence; and for one that isn't private, what it gives away
function leadOf(st, kind) {
  if (kind === "private" && serviceOf(st, kind) === "tinfoil") return "Requests are encrypted on this computer so that "
    + "only Tinfoil's verified router enclave can read them, and sent straight to Tinfoil, over a connection pinned to the "
    + "enclave's verified key (preliminary). Tinfoil sees your account, the model, timing and sizes, never the text.";
  if (kind === "tee" && serviceOf(st, kind) === "chutes") return "Chutes' -TEE models (preliminary). Each request is "
    + "encrypted on this computer to the key of one Chutes instance whose Intel TDX attestation, for our nonce, binds that "
    + "key, whose GPUs NVIDIA vouches for, and whose VM image is one Chutes publishes. Replies stream, each piece held to "
    + "its number.";
  if (kind === "private") return "Requests are encrypted on this computer so that only a verified enclave can read them. "
    + "NanoGPT passes them on but can't read them.";
  if (kind === "tee") return "NanoGPT's TEE models, which run in secure enclaves (Intel TDX with NVIDIA GPUs). "
    + "DA Private Coder verifies each enclave before sending it anything, and again every 15 minutes. For most of "
    + "them, the enclave verified is RedPill's gateway, which passes requests on to the machine that runs the model: "
    + "that machine is the gateway's word.";
  return "";
}

// a model as a kind's own lists show it: without "private/" or "TEE/" there, where every model is one
const shortModel = (m, kind) => kind === "private" ? m.replace(/^private\//, "") : kind === "tee" ? m.replace(/^TEE\//i, "") : m;
const EFFORTS = { low: "Low", medium: "Medium", high: "High", max: "Max" };
const SEARCHES = { "zero-retention": "Linkup (keeps nothing)", best: "Perplexity and Kagi", off: "off" };

// what a session of `kind` runs, in one line: its provider, its models, its effort and web search
function kindSummary(st, kind) {
  const p = st.config && st.config.profiles[kind];
  if (!p) return "";
  const m = (x) => shortModel(x || "", kind);
  const e = (x) => (EFFORTS[x] || x || "").toLowerCase();
  const checks = (p.check_effort || "low") !== p.reasoning ? `, checks ${e(p.check_effort || "low")}` : "";
  return `${providerName(st, kind)} · ${m(p.model)} · Opus: ${m(p.opus_model)} · Quick: ${m(p.fast_model)} · `
    + `effort ${e(p.reasoning)}${checks} · web search ${SEARCHES[p.web_search] || p.web_search}`;
}

function warningOf(st, kind) {
  if (kind === "tee" && serviceOf(st, kind) === "chutes") return h("div", { class: "warnbox" }, h("b", {}, "Partial privacy. "),
    "That only the model can read your text rests on software Chutes signs: the program that makes and holds the key "
    + "(Chutes' aegis library, a closed binary) is admitted by Chutes' signature, not measured, and the list of measured "
    + "images is Chutes' own. Chutes sees your account, the model, timing and sizes. A session that began with the "
    + "highest privacy (Private) never moves here.");
  if (kind === "tee") return h("div", { class: "warnbox" }, h("b", {}, "Not end-to-end encrypted. "),
    "NanoGPT can read every prompt, file and reply. The enclave only hides them from the company running the model. "
    + "To hide them from NanoGPT too, choose Private.");
  if (kind === "standard") return h("div", { class: "warnbox" }, h("b", {}, "Not private. "),
    `${providerName(st, "standard")} and the company running the model can read every prompt, file and reply, `
    + "and keep them under their own policies.");
  return null;
}

// why a session of `kind` can't start yet, or "" when it can
function notReady(st, kind) {
  if (st.ready[kind]) return "";
  if (!st.claude.path) return "Can't start yet: Claude Code wasn't found. See Setup.";
  if ((st.provider_keys || {})[kind]) {
    return `Can't start yet: add your ${providerName(st, kind)} API key under Settings (the ${KINDS[kind].label} tab).`;
  }
  return "Can't start yet: add your NanoGPT API key in Setup.";
}

// three buttons, one pressed: the kinds of session
function seg(current, pick) {
  return h("div", { class: "seg" }, SESSIONS.map((k) => h("button", {
    class: `seg-btn kind-${k}${k === current ? " on" : ""}`, "aria-pressed": k === current ? "true" : "false",
    onclick: () => pick(k) }, KINDS[k].label)));
}

// tabs, one selected: which kind's settings are shown (Settings), drawn unlike seg's buttons, which choose
// what new sessions start as: the two were alike, and one of them changed nothing but the view
function tabs(current, pick) {
  return h("div", { class: "tabs", role: "tablist" }, SESSIONS.map((k) => h("button", {
    class: `tab kind-${k}${k === current ? " on" : ""}`, role: "tab", "aria-selected": k === current ? "true" : "false",
    onclick: () => pick(k) }, KINDS[k].label)));
}

// ------------------------------------------------------------------ the top bar

function renderTop(st) {
  const bar = $("#status");
  let text = "Ready", cls = "ok";
  if (st.config_error) { text = "The settings file has an error"; cls = "bad"; }
  else if (!st.claude.path) { text = "Needs Claude Code"; cls = "wait"; }
  else if (!SESSIONS.some((k) => st.ready[k])) { text = "Needs an API key"; cls = "wait"; }
  else text = `Ready · new sessions are ${KINDS[st.start].label}`;
  bar.className = `status ${cls}`;
  $("#status-text").textContent = text;
  const notes = [st.ui_notice, st.config_error].filter(Boolean);
  $("#banner").textContent = notes.join(" ");
  $("#banner").classList.toggle("hidden", !notes.length);
}

// ------------------------------------------------------------------ start

function startSection(st) {
  const kind = st.start;
  const why = notReady(st, kind);
  const opening = Object.keys(S.busy).some((k) => k.startsWith("open"));
  const open = (f) => once("open", () => act("POST", "/api/open", { folder: f, profile: kind },
    `Starting Claude Code in a terminal, on ${kindModels(kind)}.`));
  // the folder in the editor, its Claude Code on this kind of session; other folders stay as they are
  const openIn = (e, f) => once(`open:${e.name}`, () => act("POST", "/api/open-editor", { name: e.name, folder: f, profile: kind },
    `Opening in ${e.name}. Claude Code in this folder now uses ${kindModels(kind)}. `
    + "If a conversation was already open there, start a new one."));
  const editorsHere = (st.editors || []).filter((e) => e.opens && e.state !== "other");
  const folder = h("input", { type: "text", class: "mono folder", value: S.folder,
    placeholder: st.can_choose_folder ? "Project folder: type its path, or Choose…" : "Project folder: type its path",
    oninput: (e) => { S.folder = e.target.value; },
    onkeydown: (e) => { if (e.key === "Enter" && !why) open(S.folder); } });
  const recent = (st.recent || []).map((f) => h("div", { class: "item" },
    // cut at the start when it's too long (direction: rtl), the path itself kept left to right:
    // otherwise its first "/" is drawn at its end; a click puts it in the folder field
    h("span", { class: "path link", title: `${f}\nClick to choose it, then open it with a button above.`,
      onclick: () => { S.folder = f; render(); } }, h("bdi", { dir: "ltr" }, f)),
    h("button", { class: "small", disabled: Boolean(why) || opening, onclick: () => open(f) }, "Open in terminal"),
    h("button", { class: "small ghost", title: "Remove from this list", onclick: () => act("POST", "/api/forget", { folder: f }) }, "×")));
  const installed = st.commands["claude-private"].state === "current";
  const command = kind === "private" ? "claude-private" : `daprivatecoder claude --profile ${kind}`;
  return h("div", { class: `section start ${kind}` },
    h("h3", {}, "Start Claude Code", chipOf(kind)),
    seg(kind, (pick) => { if (pick !== kind) act("POST", "/api/start", { session: pick }); }),
    leadOf(st, kind) ? h("p", { class: "lead" }, leadOf(st, kind)) : null,
    // what it runs, where it starts: changed in its settings, below
    h("div", { class: "summary row" }, h("span", { class: "small" }, kindSummary(st, kind)),
      h("button", { class: "small ghost", onclick: () => {
        // at once while its list is still to come: the page drawn again as it arrives stopped a smooth scroll where
        // it was, and the first Change… didn't reach Settings (until Nov 2026)
        const smooth = Boolean(S.models[kind]);
        S.tab = kind; render();
        const at = document.getElementById("settings");
        if (at) at.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
      } }, "Change…")),
    warningOf(st, kind),
    h("div", { class: "row" }, folder,
      st.can_choose_folder ? h("button", { onclick: () => guarded(async () => {
        const { folder: picked } = await api("POST", "/api/choose-folder", { start: S.folder });
        if (picked) { S.folder = picked; render(); }
      }) }, "Choose…") : null),
    h("div", { class: "row" },
      h("button", { class: kind, disabled: Boolean(why) || opening, onclick: () => open(S.folder) }, "Open in a terminal"),
      ...editorsHere.map((e) => h("button", { class: kind, disabled: Boolean(why) || opening,
        title: `Opens the folder in ${e.name}, with Claude Code there on ${kindModels(kind)}. `
          + `This turns DA Private Coder on in ${e.name}; other folders aren't affected.`,
        onclick: () => openIn(e, S.folder) }, `Open in ${e.name}`))),
    why ? h("div", { class: "small muted" }, why) : null,
    recent.length ? h("div", { class: "recent" }, h("div", { class: "small muted" }, "Recent folders"), recent) : null,
    installed && st.commands_on_path ? h("div", { class: "small muted spaced" }, "In a terminal, ", h("code", {}, command),
      ` starts a ${KINDS[kind].label} session in the current folder. Plain `, h("code", {}, "claude"), " is unaffected.") : null);
}

// ------------------------------------------------------------------ setup

// done: true (✓), false (• still to do), or "optional" (– up to the user: an editor's switch)
function check(done, what, detail, ...actions) {
  const [cls, mark] = done === "optional" ? ["opt", "–"] : done ? ["ok", "✓"] : ["todo", "•"];
  return h("div", { class: "check" },
    h("div", { class: `mark ${cls}` }, mark),
    h("div", { class: "body" }, h("div", { class: "what" }, what),
      // what the commands say for a terminal (`claude`), as plain words here
      detail ? h("div", { class: "detail" }, typeof detail === "string" ? detail.replace(/`/g, "") : detail) : null,
      actions.some(Boolean) ? h("div", { class: "row" }, ...actions) : null));
}

// a key in the keyring: stored (Replace, Remove: a second click), or a field to paste it in. `slot` keeps
// the Replace field open across renders (S.replacing)
// `profile`: the tab a provider's box is on, whose address the key is for (a provider of your own's: each kind of
// session may have its own server); the standard one's when not said
function keyControls(slot, state, provider, label, said, profile) {
  const draft = `key:${slot}`;
  const saveKey = () => act("POST", "/api/key", { key: keyInput.value, provider, profile }, said)
    .then((done) => { if (done) { delete S.drafts[draft]; if (S.replacing === slot) S.replacing = ""; render(); } });
  const keyInput = h("input", { type: "password", placeholder: `Paste your ${label} API key`, autocomplete: "off",
    value: S.drafts[draft] || "", oninput: (e) => { S.drafts[draft] = e.target.value; },
    onkeydown: (e) => { if (e.key === "Enter") saveKey(); } });
  const saveButton = h("button", { class: "primary small", onclick: saveKey }, "Save");
  if (!state.stored) return { stored: false, controls: [keyInput, saveButton], extra: null };
  const replacing = h("div", { class: `row${S.replacing === slot ? "" : " hidden"}` }, keyInput, saveButton);
  // removing takes a second click: a slip would stop every session until the key is pasted again
  const remove = h("button", { class: "small ghost", onclick: () => {
    if (remove.dataset.sure) act("POST", "/api/key", { clear: true, provider, profile }, "The key was removed from your keyring.");
    else {
      remove.dataset.sure = "1"; remove.textContent = "Click again to remove it";
      setTimeout(() => { delete remove.dataset.sure; remove.textContent = "Remove"; }, 4000);
    }
  } }, "Remove");
  const replace = h("button", { class: "small", onclick: () => {
    S.replacing = S.replacing === slot ? "" : slot;
    replacing.classList.toggle("hidden", S.replacing !== slot);
    if (S.replacing === slot) keyInput.focus();
  } }, "Replace");
  return { stored: true, controls: [replace, remove], extra: replacing };
}

// what Setup still needs before any session can start: it's shown first until then. The key is Setup's provider's
// (manager.py setup_ready): NanoGPT's, or Anthropic's, Tinfoil's, Chutes' or another's alone
function needsSetup(st) {
  const keyed = st.setup_ready !== undefined ? st.setup_ready : st.key.stored;
  return Boolean(st.config_error) || !st.claude.path || !keyed;
}

// Setup's provider (manager.py SETUP_KIND): which kind of session it runs, and what its key is for
const SETUP_KINDS = { tinfoil: "private", chutes: "tee" };
function setupWhat(provider) {
  if (provider === "nanogpt") return "Needed for Private and TEE sessions, web search, and Standard sessions on NanoGPT. "
    + "Usage is paid from your NanoGPT balance. Get a key at nano-gpt.com/api.";
  if (provider === "tinfoil") return "Private sessions run on Tinfoil, every request end-to-end encrypted straight to "
    + "Tinfoil's verified router (preliminary). Usage is paid from your Tinfoil account.";
  if (provider === "chutes") return "TEE sessions run on Chutes' -TEE models, each request end-to-end encrypted to a verified "
    + "Chutes instance, with partial privacy (preliminary). Usage is paid from your Chutes account.";
  const own = PROVIDERS[provider] && PROVIDERS[provider].own;
  return `Standard sessions run on ${own ? "your provider" : providerSaid(provider)}, not private: it can read every prompt, `
    + `file and reply.${own ? " Only if your provider needs a key." : ""}`;
}

// web search goes through NanoGPT whatever the session's provider: without its key, sessions start with it off
function searchNeedsNanogpt() {
  return "Web search goes through NanoGPT, so it needs a NanoGPT API key: without one, sessions start with web search off.";
}

function setupSection(st) {
  const k = st.key;
  // set up: each kind's provider and its key are in its settings (Settings); here, NanoGPT's key, which web search
  // and NanoGPT's sessions use, and what isn't a provider's. Setup's Provider is for the first start only
  if (!needsSetup(st) && !S.setupOwn) {
    const nano = keyControls("nanogpt", k, "nanogpt", "NanoGPT", "The key is stored in your keyring.");
    const keyRow = check(nano.stored || "optional", "NanoGPT API key",
      nano.stored ? `Stored in your keyring (ending ${k.ending}). Used by web search and by sessions on NanoGPT.`
        : k.error || `${searchNeedsNanogpt()} Sessions on NanoGPT need it too.`, ...nano.controls);
    if (nano.extra) keyRow.querySelector(".body").append(nano.extra);
    return h("div", { class: "section" }, h("h3", {}, "Setup"), keyRow, ...setupRest(st));
  }
  const chosen = st.setup || "nanogpt";
  const pickRow = h("div", { class: "model-row" }, h("label", {}, "Provider"),
    h("select", { onchange: (e) => {
      const v = e.target.value;
      if (PROVIDERS[v].own) { S.setupOwn = v; render(); }
      else {
        S.setupOwn = "";
        act("POST", "/api/setup", { provider: v }).then((done) => { if (done) toast(setupDone(S.state, v), "ok", 6000); });
      }
    } }, Object.keys(PROVIDERS).map((v) => h("option", { value: v, selected: v === (S.setupOwn || chosen) }, PROVIDERS[v].label))));
  const rows = [pickRow];
  if (S.setupOwn) {
    const spec = PROVIDERS[S.setupOwn];
    // once saved, drawn again: the address and its Save stayed, and a second Save sent no provider ("There's no
    // provider called ''"); Enter in the field did nothing (until Nov 2026)
    const go = () => act("POST", "/api/setup", { provider: S.setupOwn, base_url: url.value.trim() })
      .then((done) => {
        if (!done) return;
        toast(setupDone(S.state, S.setupOwn), "ok", 3000);
        S.setupOwn = ""; delete S.drafts.setupUrl; render();
      });
    const url = h("input", { type: "text", class: "mono", placeholder: spec.placeholder, value: S.drafts.setupUrl || "",
      oninput: (e) => { S.drafts.setupUrl = e.target.value; }, onkeydown: (e) => { if (e.key === "Enter") go(); } });
    rows.push(h("div", { class: "model-row" }, h("label", {}, "Address"), h("div", { class: "row" }, url,
      h("button", { class: "small primary", onclick: go }, "Save"))));
  }
  let keyRow;
  if (chosen === "nanogpt" || S.setupOwn) {
    const nano = keyControls("nanogpt", k, "nanogpt", "NanoGPT", "The key is stored in your keyring.");
    keyRow = check(nano.stored, "NanoGPT API key",
      nano.stored ? `Stored in your keyring (ending ${k.ending}).` : k.error || setupWhat("nanogpt"),
      ...nano.controls,
      !nano.stored && k.vibe_manager ? h("button", { class: "small", onclick: () => act("POST", "/api/key", { from: "vibe-manager" },
        "DA Vibe Manager's key is now stored for DA Private Coder too.") }, "Use DA Vibe Manager's key") : null);
    if (nano.extra) keyRow.querySelector(".body").append(nano.extra);
  } else {
    const sk = st.setup_key || { stored: false };
    const own = Boolean(PROVIDERS[chosen].own);
    const keys = keyControls(`setup:${chosen}`, sk, chosen, keyName(chosen), "The key is stored in your keyring.");
    keyRow = check(sk.stored || (own ? "optional" : false), `${own ? "Your provider's" : providerSaid(chosen)} API key`,
      sk.stored ? `Stored in your keyring (ending ${sk.ending}).` : sk.error || setupWhat(chosen), ...keys.controls);
    if (keys.extra) keyRow.querySelector(".body").append(keys.extra);
    // NanoGPT's key, for web search alone here: optional, and said so
    const nano = keyControls("nanogpt", k, "nanogpt", "NanoGPT", "The key is stored in your keyring. Web search can be used.");
    const searchRow = check(nano.stored || "optional", "NanoGPT API key, for web search",
      nano.stored ? `Stored in your keyring (ending ${k.ending}).` : searchNeedsNanogpt(), ...nano.controls);
    if (nano.extra) searchRow.querySelector(".body").append(nano.extra);
    rows.push(keyRow);
    keyRow = searchRow;
  }
  return h("div", { class: "section" }, h("h3", {}, "Setup"), ...rows, keyRow, ...setupRest(st));
}

// What Setup's provider, once saved, did: the kind it set runs on it, and that kind's list of models is its provider's
// now (the list shown was the one before: Tinfoil's models "no longer listed" among NanoGPT's, until Nov 2026).
// NanoGPT chosen moves no kind back from the provider it was set to: those still on another are said, where "NanoGPT
// is set up" alone read as if every kind were back on it (Nov 2026)
function setupDone(st, provider) {
  if (provider !== "nanogpt") {
    const kind = SETUP_KINDS[provider] || "standard";
    S.hosts = {};
    delete S.models[kind];
    loadModels(kind, true);
    return `${PROVIDERS[provider].own ? "Your provider" : PROVIDERS[provider].name} is set up for ${KINDS[kind].label} sessions.`;
  }
  const profiles = (st.config && st.config.profiles) || {};
  const elsewhere = SESSIONS.filter((k) => profiles[k] && profiles[k].provider !== "nanogpt")
    .map((k, i) => `${KINDS[k].label} sessions ${i ? "" : "still run "}on ${providerName(st, k)}`);
  if (!elsewhere.length) return "NanoGPT is set up.";
  const said = elsewhere.length > 1 ? `${elsewhere.slice(0, -1).join(", ")} and ${elsewhere[elsewhere.length - 1]}` : elsewhere[0];
  return `NanoGPT is set up. ${said}: change that in Settings, in each kind's tab.`;
}

// Setup's rows that aren't a key: Claude Code, the terminal commands, and an editor not found
function setupRest(st) {
  const claude = check(Boolean(st.claude.path), "Claude Code",
    st.claude.path ? h("span", { class: "mono" }, st.claude.path) : st.claude.error);

  const cmds = Object.entries(st.commands);
  const allIn = cmds.every(([, c]) => c.state === "current");
  const taken = cmds.filter(([, c]) => c.state === "taken").map(([, c]) => c.path);
  // only for typing them in a terminal: this window runs the app itself, and the editor's switch puts
  // what it runs in place when it's turned on
  const commandsRow = check(allIn || "optional", "Terminal commands",
    allIn ? (st.commands_on_path ? "Installed in ~/.local/bin. Type claude-private in any terminal for a Private session."
      : "Installed in ~/.local/bin, but that folder isn't on your PATH. Add it to your PATH to type "
        + "claude-private and daprivatecoder in a terminal.")
      : taken.length ? `${taken.join(", ")} already exists and belongs to another program, so it was left alone.`
        : "Optional. Adds the claude-private and daprivatecoder commands to ~/.local/bin, so you can start sessions "
          + "from any terminal. Nothing in this window needs them.",
    allIn ? null : h("button", { class: "small", onclick: () => act("POST", "/api/commands", {}, "Installed.") }, "Install"));

  // with no editor, nothing else says so: the editor's own section isn't shown
  const noEditor = st.editors.length ? null
    : check("optional", "Claude Code in your editor", "VSCodium and VS Code weren't found. Use a terminal instead.");

  return [claude, commandsRow, noEditor];
}

// ------------------------------------------------------------------ each profile's models, and the hosts that run them

const ROLES = [
  ["model", "Default", "What sessions start with, what /model sonnet picks, and what checks each action in auto mode"],
  ["opus_model", "Opus", "What /model opus picks"],
  ["fast_model", "Quick", "What /model haiku picks, and what runs Claude Code's background tasks"],
  ["vision_model", "Images", "Describes images for a model that can't see them (a model that can sees them itself)"],
];
const ROUTES = {
  nanogpt: ["auto", "speed", "latency", "throughput", "price", "host"],
  openrouter: ["auto", "latency", "throughput", "price", "host"],
};
const ROUTE_LABELS = { speed: "Fastest overall", latency: "Fastest to start answering", throughput: "Fastest writing",
  price: "Cheapest", host: "A host I choose…" };

// without "private/" or "TEE/" in their own lists: every model there is one, and the label then fits
function modelLabel(m, kind) {
  const p = m.price ? ` · $${price(m.price.input)} / $${price(m.price.cache_read)} / $${price(m.price.output)}` : "";
  const id = shortModel(m.id, kind);
  // a model sealed where the others of its list aren't (Chutes' -TEE ones in a standard session): marked so
  const sealed = kind === "standard" && m.privacy && m.privacy.kind === "sealed" ? "🔐 " : "";
  return `${sealed}${id}${p}${m.vision ? " · sees images" : ""}${m.tools === false ? " · no tool calls" : ""}${m.included ? " · subscription" : ""}`;
}

async function loadHosts(profile, model) {
  S.hosts[model] = { loading: true };
  render();
  try { S.hosts[model] = await api("POST", "/api/hosts", { profile, model }); }
  catch (e) { S.hosts[model] = { error: e.message }; }
  render();
}

// which of the provider's hosts runs `model`: its own choice, a priority, or a host the user picks
function routeRow(st, tab, p, model) {
  const list = S.models[tab] || [];
  const info = list.find((m) => m.id === model);
  if (!ROUTES[p.provider] || !info || !info.routable) return null;
  const route = (p.routes || {})[model] || null;
  const choosing = S.routeHost[model] || (route && route.priority === "host");
  const fp8 = route ? route.fp8 !== false : S.routeFp8[model] !== false;
  const save = (r) => act("POST", "/api/route", { profile: tab, model, route: r },
    r && p.provider === "nanogpt" ? "Saved: paid per use, even if a subscription includes the model." : "Saved.")
    .then((done) => { if (done) delete S.routeHost[model]; });
  const auto = p.provider === "nanogpt" ? "NanoGPT's own choice (what a subscription covers)" : "OpenRouter's own choice";
  const priority = h("select", { onchange: (e) => {
    const v = e.target.value;
    if (v === "auto") save(null);
    else if (v === "host") { S.routeHost[model] = true; if (!S.hosts[model]) loadHosts(tab, model); else render(); }
    else save({ priority: v, fp8 });
  } }, ROUTES[p.provider].map((v) => h("option", { value: v, selected: v === (choosing ? "host" : route ? route.priority : "auto") },
    v === "auto" ? auto : ROUTE_LABELS[v])));
  const parts = [priority];
  if (choosing) {
    const listed = S.hosts[model];
    if (!listed) { S.hosts[model] = { loading: true }; setTimeout(() => loadHosts(tab, model), 0); }   // not while drawing
    if (listed && listed.hosts) {
      const hosts = listed.hosts.filter((x) => x.available && (!fp8 || x.fp8));
      // the host saved, even when it isn't listed now (gone, or below FP8): what the requests still go to
      if (route && route.host && !hosts.some((x) => x.id === route.host)) {
        hosts.unshift({ id: route.host, name: `${route.host_name || route.host} (no longer listed)` });
      }
      const speed = (x) => x.tokens_per_second ? ` · ${Math.round(x.tokens_per_second)} tok/s` : "";
      const cost = (x) => x.input !== null && x.input !== undefined ? ` · $${price(x.input)} / $${price(x.output)}` : "";
      parts.push(h("select", { onchange: (e) => {
        const x = listed.hosts.find((y) => y.id === e.target.value);
        if (x) save({ priority: "host", host: x.id, host_name: x.name, fp8 });
      } }, [h("option", { value: "", selected: !(route && route.host) }, hosts.length ? "Choose the host…" : "No host listed")]
        .concat(hosts.map((x) => h("option", { value: x.id, selected: route && route.host === x.id },
          `${x.name}${x.precision ? ` · ${x.precision.toUpperCase()}` : ""}${cost(x)}${speed(x)}${x.keeps_nothing ? " · keeps nothing" : ""}`)))));
    } else parts.push(h("span", { class: "small muted" }, listed && listed.error ? listed.error : "Loading hosts…"));
  }
  if (route || choosing) parts.push(h("label", { class: "small muted tick", title: "Leave out hosts that run it below FP8 precision, or don't say" },
    h("input", { type: "checkbox", checked: fp8,
      onchange: (e) => { if (route) save({ ...route, fp8: e.target.checked }); else { S.routeFp8[model] = e.target.checked; render(); } } }),
    "FP8 or better"));
  return h("div", { class: "model-row route" }, h("label", {}, "Runs on"), h("div", { class: "row" }, ...parts));
}

// where Claude Code compacts the conversation (config.CONTEXTS), and a model that holds less than that
const CONTEXTS = [[200000, "200K"], [400000, "400K"], [1000000, "1M"]];
function contextRows(tab, p, save) {
  const smallest = (S.models[tab] || []).filter((m) => [p.model, p.opus_model, p.fast_model].includes(m.id) && m.context)
    .reduce((least, m) => Math.min(least, m.context), Infinity);
  return [h("div", { class: "model-row" }, h("label", {}, "Context"),
    h("select", { onchange: (e) => save({ context: Number(e.target.value) }, "Saved. New sessions use it.") },
      CONTEXTS.map(([v, l]) => h("option", { value: String(v), selected: p.context === v }, l)))),
  h("div", { class: "model-row" }, h("span", {}), h("div", { class: "small muted" },
    "When a conversation reaches this size, Claude Code summarises it and carries on. Every request resends the whole "
    + "conversation, so a larger size costs more per request, even with caching: at 1M, about $0.30 per GLM-5.3 request. "
    + "200K is Claude Code's default for these models.",
    smallest < p.context ? h("div", { class: "warnbox" }, `One of these models holds only ${Math.round(smallest / 1000)}K, `
      + "so its requests would fail before Claude Code summarises.") : null))];
}

// the providers a session of `kind` can take (PROVIDERS' kinds), in the list's order
const providersFor = (kind) => Object.keys(PROVIDERS).filter((v) => PROVIDERS[v].kinds.includes(kind));

// a profile's provider, where its kind can take more than one (a standard one: NanoGPT, OpenRouter, or an endpoint
// of your own), and its key
function providerRows(st, tab, p, save) {
  const offered = providersFor(p.kind);
  if (offered.length < 2) return [];
  // one of your own being given its address (S.customUrl), before it is saved: shown as chosen
  const choosing = S.customUrl && PROVIDERS[S.customUrl] && PROVIDERS[S.customUrl].own ? S.customUrl : "";
  const shownNow = choosing || p.provider;
  const spec = PROVIDERS[shownNow] || {};
  const label = KINDS[p.kind].label;
  const pick = h("select", { onchange: (e) => {
    const v = e.target.value;
    // one of your own needs its address first: asked, then saved with it
    if (PROVIDERS[v].own && v !== p.provider) { S.customUrl = v; delete S.drafts.url; render(); }
    else { S.customUrl = ""; save({ provider: v }, `Saved. ${label} sessions now use ${PROVIDERS[v].name}.`); }
  } }, offered.map((v) => h("option", { value: v, selected: v === shownNow }, PROVIDERS[v].label)));
  const rows = [h("div", { class: "model-row" }, h("label", {}, "Provider"), pick)];
  if (spec.own) {
    const url = h("input", { type: "text", class: "mono", value: S.drafts.url ?? (choosing ? "" : p.base_url || ""),
      placeholder: spec.placeholder, oninput: (e) => { S.drafts.url = e.target.value; },
      onkeydown: (e) => { if (e.key === "Enter") go(); } });
    const go = () => save({ provider: shownNow, base_url: url.value.trim() }, `Saved. ${label} sessions now use it.`)
      .then((done) => { if (done) { S.customUrl = ""; delete S.drafts.url; } });
    rows.push(h("div", { class: "model-row" }, h("label", {}, "Address"), h("div", { class: "row" }, url,
      h("button", { class: "small primary", onclick: go }, "Save"))),
      h("div", { class: "model-row" }, h("span", {}), h("div", { class: "small muted" },
        `${spec.address} It must be https, except on this computer or your local network. `
        + "A user name and password in it are kept in your keyring, not in the settings file.")));
  }
  const pk = (st.provider_keys || {})[tab];
  if (pk && !choosing) {
    const own = Boolean(PROVIDERS[p.provider] && PROVIDERS[p.provider].own);
    const keys = keyControls(`provider:${tab}:${p.provider}`, pk, p.provider, keyName(p.provider), "The key is stored in your keyring.", tab);
    rows.push(h("div", { class: "model-row" }, h("label", {}, "API key"), h("div", { class: "col" },
      h("div", { class: "row" }, keys.stored ? h("span", { class: "small muted" }, `Stored (ending ${pk.ending})`) : null,
        ...keys.controls),
      keys.extra, !keys.stored && own ? h("div", { class: "small muted" }, "Only if the provider needs one.") : null)));
    const clear = inTheClear(pk, p.provider, tab);
    if (clear) rows.push(h("div", { class: "model-row" }, h("span", {}), clear));
    // a saved key held to the address it was stored for, not this one (creds.KeyElsewhere): it says which, and the
    // field above takes a key for this one
    if (pk.error) rows.push(h("div", { class: "model-row" }, h("span", {}), h("div", { class: "warnbox" }, pk.error)));
  }
  return rows;
}

// A key that would go over plain http to another machine on your network (creds.py, the cross-app standard): asked
// once for that address, No the first button and the one focused, and a No kept, so it isn't asked at every save;
// "Ask me again" takes it back. Until you say Yes the key isn't sent there.
function inTheClear(pk, provider, profile) {
  const answer = (value, said) => act("POST", "/api/key", { provider, profile, in_the_clear: value }, said);
  const where = pk.clear_host || "that address";
  if (pk.in_the_clear === "ask") {
    const no = h("button", { class: "small primary", onclick: () => answer(false, "The key isn't sent there.") }, "No, don't send it");
    setTimeout(() => no.focus(), 0);
    return h("div", { class: "warnbox" },
      h("div", {}, `Send the API key unencrypted to ${where}? It goes over plain http to another machine on your network, `
        + "where anyone on that network can read it (a café's or a hotel's Wi-Fi too). Until you say yes it isn't sent there; "
        + "the server's https:// address needs no question."),
      h("div", { class: "row" }, no, h("button", { class: "small standard", onclick: () => answer(true,
        "The key is sent there unencrypted from now on.") }, "Send it unencrypted")));
  }
  if (pk.in_the_clear === "declined") {
    return h("div", { class: "small muted" }, `The saved key isn't sent to ${where} over plain http (you said no). `,
      h("button", { class: "small ghost", onclick: () => answer(null) }, "Ask me again"));
  }
  if (pk.in_the_clear === "allowed") {
    return h("div", { class: "small muted" }, `The key goes unencrypted to ${where}, as you said. `,
      h("button", { class: "small ghost", onclick: () => answer(null) }, "Ask me again"));
  }
  return null;
}

function modelsSection(st) {
  const tab = S.tab || st.start;
  const p = st.config && st.config.profiles[tab];
  if (!p) return null;
  const save = (fields, said = "Saved. New sessions use it.") => act("POST", "/api/profile", { profile: tab, fields }, said)
    .then((done) => {
      // the list before is another provider's: not shown meanwhile as this one's
      if (done && ("provider" in fields || "base_url" in fields)) { S.hosts = {}; delete S.models[tab]; loadModels(tab, true); render(); }
      return done;
    });
  wantModels(tab);
  const list = S.models[tab] || [];
  const routed = new Set();
  const rows = ROLES.flatMap(([key, label, hint]) => {
    const current = p[key] || "";
    const usable = list.filter((m) => key === "vision_model" ? m.vision : m.tools !== false);
    const options = [];
    if (key === "vision_model") options.push(h("option", { value: "", selected: !current }, "None (a model that can't see is told so)"));
    if (current && !list.some((m) => m.id === current)) {
      options.push(h("option", { value: current, selected: true }, S.models[tab] ? `${current} (no longer listed)` : current));
    }
    for (const m of usable) options.push(h("option", { value: m.id, selected: m.id === current }, modelLabel(m, tab)));
    const out = [h("div", { class: "model-row", title: hint }, h("label", {}, label),
      h("select", { onchange: (e) => save({ [key]: e.target.value }) }, options))];
    if (p.kind === "standard" && current && !routed.has(current)) {
      routed.add(current);
      const route = routeRow(st, tab, p, current);
      if (route) out.push(route);
    }
    return out;
  });
  const prices = "Prices per million tokens: input / cached input / output";
  const sealedBy = serviceOf(st, tab);
  const lead = sealedBy === "tinfoil" ? `Tinfoil's models, every one end-to-end encrypted. ${prices}, as Tinfoil lists them.`
    : sealedBy === "chutes" && tab === "tee" ? `Chutes' -TEE models, every one end-to-end encrypted (partial privacy). ${prices}.`
    : sealedBy === "chutes" ? `Chutes' models that support tool calls: its -TEE ones (marked 🔐) end-to-end encrypted with `
      + `partial privacy, the others in the clear. ${prices}.`
    : tab === "private" ? `${prices}, including NanoGPT's 5% Private Mode markup.`
    : tab === "tee" ? `${prices}.`
      : `${providerName(st, tab)}'s models that support tool calls.${PROVIDERS[p.provider] && PROVIDERS[p.provider].prices ? ` ${prices}.` : ""}`
        + (p.provider === "nanogpt" ? " When NanoGPT chooses the host, your subscription covers the models it includes; "
          + "a host you choose is always paid per use." : "");
  const reading = tab === "standard" || sealedBy ? `Loading ${providerName(st, tab)}'s models…`
    : `Loading NanoGPT's ${tab === "tee" ? "TEE" : "private"} models…`;
  // tabs, not the kind of session new ones start as (Start's): which kind's settings are shown here
  return h("div", { class: "section", id: "settings" }, h("h3", {}, "Settings for each kind of session"),
    tabs(tab, (pick) => { S.tab = pick; S.customUrl = ""; render(); }),
    h("div", { class: "tab-body" },
    h("div", { class: "row" }, chipOf(tab), tab === st.start ? h("span", { class: "small muted" }, "New sessions start as this kind.") : null),
    // what this kind gives away, said here only when Start, above, doesn't already
    tab === st.start ? null : warningOf(st, tab),
    providerRows(st, tab, p, save),
    h("p", { class: "lead" }, lead),
    h("p", { class: "lead small" }, "Default is what sessions start with, and what checks each action in auto mode. Opus and "
      + "Quick are what ", h("code", {}, "/model opus"), " and ", h("code", {}, "/model haiku"), " pick; Quick also runs "
      + "Claude Code's background tasks. Images describes images for a model that can't see them."),
    rows,
    // where each session's effort starts: Claude Code's own (the editor's Effort, /effort) changes it
    // in a session, and the model runs at whatever Claude Code says (proxy.py)
    h("div", { class: "model-row" }, h("label", {}, "Effort"),
      h("select", { onchange: (e) => save({ reasoning: e.target.value }) },
        [["low", "Low (fastest)"], ["medium", "Medium"], ["high", "High (slow)"], ["max", "Max (slowest)"]]
          .map(([v, l]) => h("option", { value: v, selected: p.reasoning === v }, l)))),
    h("div", { class: "model-row" }, h("span", {}), h("div", { class: "small muted" },
      "How much the models think at the start of each session. During a session, Claude Code's own Effort setting (or ",
      h("code", {}, "/effort"), ") changes it. Low is the default: in a test of GLM-5.3 on four coding tasks, High gave the "
      + "same results and took about four times as long. A model without the chosen level uses the nearest one it has: ",
      tab === "private" ? "private models have no medium (it runs as high) or xhigh (it runs as max)." : "as its provider lists them.")),
    // auto mode's checks of each action: their own effort (proxy.is_auto_mode_check), Claude Code's doesn't reach them
    h("div", { class: "model-row" }, h("label", {}, "Checks"),
      h("select", { onchange: (e) => save({ check_effort: e.target.value }) },
        [["low", "Low (fastest)"], ["medium", "Medium"], ["high", "High (slow)"], ["max", "Max (slowest)"]]
          .map(([v, l]) => h("option", { value: v, selected: (p.check_effort || "low") === v }, l)))),
    h("div", { class: "model-row" }, h("span", {}), h("div", { class: "small muted" },
      "How much the Default model thinks when it checks an action in auto mode, before the action runs. Claude Code's own "
      + "Effort doesn't change it." + (["anthropic", "custom-anthropic"].includes(p.provider) ? " On an Anthropic-compatible provider, Claude Code's "
        + "requests go as they are, and this setting isn't used." : ""))),
    contextRows(tab, p, save),
    S.modelsError[tab] ? h("div", { class: "warnbox" }, `The list of models couldn't be loaded: ${S.modelsError[tab]}`) : null,
    S.models[tab] && !S.modelsLoading[tab] ? null : h("div", { class: "small muted row" }, h("span", { class: "spinner" }), reading),
    searchPart(st, tab, p)));
}

// ------------------------------------------------------------------ what runs on it now

// What every TEE mark leaves out, short of end-to-end encryption (SealedLore's TEE_MARK_TIPS, Nov 11 2026)
const TEE_CLEAR = " What was sent passed NanoGPT's servers in the clear: a private model is end-to-end encrypted instead.";
// a TEE reply's check (proxy.py), against the record its attested enclave signed: [chip, label, what it means]. For
// a model RedPill serves, the enclave attested is RedPill's gateway, and the machine that ran the model is the
// gateway's word (SealedLore's TEE_MARK_TIPS, Nov 11 2026)
const REPLY = {
  verified: ["tee", "TEE, reply verified", "Reply verified: the attested enclave's key signed this reply's record, and the record is for the exact text that came back. For a model RedPill serves, that enclave is RedPill's gateway, which passed the request on to the machine running the model: the gateway's signed receipt, read too, says that machine was verified, which is the gateway's word, not checked by this app. No measurement of the software is compared either." + TEE_CLEAR],
  signed: ["tee", "TEE, reply signed", "Reply signed: the attested enclave's key signed this reply's record, but NanoGPT rebuilds this model's replies, so the text couldn't be compared with it, and a change on the way here wouldn't show. As for a verified reply, the machine running the model is the gateway's word." + TEE_CLEAR],
  failed: ["bad", "TEE ⚠ reply check failed", "This reply doesn't match its record: not signed by the attested key, not the text that was signed, or its receipt doesn't hold (another key, another reply, one served before this request was sent, or a model that wasn't verified). Its enclave is attested again before anything more is sent to it."],
  unchecked: ["warn", "TEE ⚠ reply not verified", "Not verified: no signed record of this reply could be read (this model's provider keeps none, as Chutes doesn't, or the check couldn't be made), so nothing shows it came from the enclave that was attested. NanoGPT routes each request, and a model may be served elsewhere when its provider is busy." + TEE_CLEAR],
  checking: ["tee", "TEE, checking reply", "The reply is being checked against the record its attested enclave signed." + TEE_CLEAR],
};
// a TEE request with no reply to check (refused, stopped or cut short, or logged before replies were checked): sent
// while its model's attestation held, but nothing shows the attested enclave received it (SealedLore's, Nov 11 2026)
const TEE_UNPROVEN = "Not shown to reach the attested enclave: sent to the TEE model while its attestation held, but no reply's signed record shows the enclave received it, since no whole reply came back to check (it was refused, stopped or cut short), or it was logged before replies were checked. NanoGPT routes each request, and a model may be served elsewhere when its provider is busy." + TEE_CLEAR;

// how a request left this computer, as a chip [class, label] and what it means: a TEE one, its reply's check, or
// that there is none to show it reached the attested enclave. Who ran its model, where the user asked for it
// (Enclave checks; proxy.upstream_view, RedPill's word), goes beside a mark whose receipt the name rests on, never
// beside a failed one, and what that means in its tooltip
function sentChip(r) {
  if (r.sent === "tee" && REPLY[r.reply]) {
    const [chip, label] = REPLY[r.reply];
    const up = ["verified", "signed"].includes(r.reply) ? r.reply_upstream : null;
    return [chip, label + (up && up.name ? ` · run by ${up.label || up.name}` : ""),
      replyTip(r) + (r.reply_detail ? ` (${r.reply_detail})` : "") + (up && up.words ? `\n\n${up.words}` : "")];
  }
  const [chip, how] = SENT[r.sent] || SENT[""];
  return [chip, how + (r.sent === "sealed" && r.service === "tinfoil" ? " (Tinfoil)" : ""),
    r.sent === "tee" ? TEE_UNPROVEN : r.sent === "chutes" ? chutesTip() : ""];
}

// A TEE reply's tooltip, by whether its receipt was read under the attested keys and held (r.reply_receipt:
// proxy.py's tee_reply line). A model attested itself is verified by its own key's signature, with or without one,
// and the tooltip said a receipt was read either way (DA Toolkit's review, Nov 2026; DA Vibe Manager's teeTip).
// Without one it says so, and what the signature alone shows; a line logged before Nov 13 2026 doesn't say whether
// one was read, and its tooltip says that.
function replyTip(r) {
  const tip = REPLY[r.reply][2];
  if (!["verified", "signed"].includes(r.reply) || r.reply_receipt === true) return tip;
  if (r.reply_receipt !== false) return `${tip} Whether a receipt was read for this reply wasn't kept with it (it was logged by an earlier version).`;
  const compared = r.reply === "verified" ? "and the record is for the exact text that came back"
    : "but the reply's text can't be compared with it (NanoGPT rebuilds this model's replies), so a change on the way here wouldn't show";
  return `Checked against the attested signing key: that key signed this reply's record, ${compared}. No receipt was read `
    + "(none came with the record, or the attestation kept no receipt keys to read one by). So for a model RedPill serves, "
    + "nothing here says the machine running the model was verified: the signature shows the reply passed through the "
    + "attested gateway." + TEE_CLEAR;
}

// where a request went, in words, after its chip. A TEE one reached the attested enclave only as far as its reply's
// check shows (SealedLore's, Nov 11 2026): NanoGPT routes each request, and only a record the attested key signed
// shows that enclave received it. For most TEE models the enclave attested is RedPill's gateway ("attested")
function whereItWent(last) {
  if (last.sent === "sealed") return last.service === "tinfoil" ? "end-to-end encrypted, straight to Tinfoil's verified router"
    : "end-to-end encrypted, to the verified enclave";
  if (last.sent === "chutes") return "end-to-end encrypted, to an attested Chutes instance (partial privacy)";
  if (last.sent === "tee") {
    if (["verified", "signed"].includes(last.reply)) {
      // signed by a key of RedPill's gateway: a model it serves, or one Chutes serves (whose instances sign no
      // replies) that the gateway answered this time
      const gateway = last.attested === "gateway" || last.attested === "instances";
      return `received by the attested TEE${gateway ? " gateway" : ""}, through NanoGPT (which can read it)`;
    }
    if (last.reply === "checking") return "sent through NanoGPT (which can read it) while its attestation held; its reply is being checked";
    return "⚠ not shown to reach the attested enclave; sent through NanoGPT (which can read it)";
  }
  if (last.sent === "clear") return `unencrypted, to ${providerSaid(last.provider)}`;
  return "not sent";
}

// each session on DA Private Coder now: what it runs, where, and where its last request went
// what a session runs, in a line: its provider and the models it started with (default, Opus, Quick)
function sessionRuns(s, kind) {
  const [model, opus, quick] = (s.models || []).map((m) => shortModel(m, kind));
  if (!model) return "";
  const more = [opus && opus !== model ? `Opus: ${opus}` : "", quick && quick !== model ? `Quick: ${quick}` : ""].filter(Boolean);
  return `Runs on ${providerSaid(s.provider)}: ${model}${more.length ? ` (${more.join(", ")})` : ""}`;
}

// requests refused here since the session's last answer, which went nowhere: said apart from where its answers come
// from (a refusal shown as its "last request" read as calls to Claude, Nov 2026). For a conversation that began in
// Claude Code as usual, why its summaries ask for that model first
function refusedSaid(s) {
  const r = s.refused;
  if (!r) return "";
  const n = r.count > 1 ? ` (${r.count} requests)` : "";
  const why = s.began_on && r.models.includes(s.began_on)
    ? ` This conversation began in Claude Code as usual, on ${s.began_on}: when it summarises itself, Claude Code asks `
      + "for that model first, then makes the summary on this session's own."
    : "";
  return `Refused ${r.t.slice(11, 16)}: ${r.models.join(", ")}${n}, nothing sent.${why}`;
}

function sessionsSection(st) {
  const list = st.sessions || [];
  const rows = list.map((s) => {
    const kind = SESSIONS.includes(s.kind) ? s.kind : "standard";
    const ok = s.answered;
    const [chip, how, means] = ok ? sentChip(ok) : ["", "", ""];
    const runs = sessionRuns(s, kind);
    return h("div", { class: "session" },
      h("div", { class: "row" }, chipOf(kind),
        h("span", { class: "path", title: s.folder }, h("bdi", { dir: "ltr" }, s.folder || "?")),
        h("span", { class: "small muted" }, s.editor ? `in ${s.editor}` : "in a terminal")),
      runs ? h("div", { class: "small muted" }, runs) : null,
      ok ? h("div", { class: "small" }, `Last answer ${ok.t.slice(11, 16)}: `, h("b", { class: "mono" }, ok.model || "?"),
        ok.asked_for ? ` (a background task; Claude Code asked for ${ok.asked_for})` : "", " ", h("span", { class: `chip ${chip}`, title: means || null }, how), " ",
        whereItWent(ok))
        : h("div", { class: "small muted" }, "No answers yet."),
      s.refused ? h("div", { class: "small refused", title: s.refused.error || null }, refusedSaid(s)) : null,
      // an error the provider gave, after the last answer (out of credit, say): said as it came
      s.last && s.last.error && s.last.sent ? h("div", { class: "small bad-text" }, s.last.error) : null,
      s.began_on && !s.refused ? h("div", { class: "small muted" }, `Began in Claude Code as usual, on ${s.began_on}.`) : null,
      s.stops ? h("div", { class: "warnbox" }, h("b", {}, "Will stop at its next request: "), s.stops) : null);
  });
  return h("div", { class: "section" }, h("h3", {}, "Running now"),
    h("p", { class: "lead" }, list.length
      ? "Claude Code sessions using DA Private Coder, what each runs on, and where its last answer came from. Claude Code "
        + "not listed here is normal Claude Code, on Anthropic's servers."
      : "No Claude Code sessions are using DA Private Coder. Any Claude Code you have open is normal Claude Code, "
        + "on Anthropic's servers."),
    rows);
}

// "Normal Claude Code": the editor's own, untouched, on Anthropic's servers ("" in editor-folders.json)
const NORMAL = "Normal Claude Code (Anthropic)";
const kindName = (k) => k ? KINDS[k].label : "normal Claude Code";

// Claude Code in VSCodium and VS Code: each editor's switch, which folders use DA Private Coder there, and
// what every other folder uses
function editorSection(st) {
  if (!st.editors.length) return null;
  const f = st.folders;
  const names = [...new Set(st.editors.map((e) => e.name.replace(/ \(Flatpak\)$/, "")))];
  const set = (body, said) => act("POST", "/api/folder", body, said);

  // each editor's switch: off (normal everywhere), on (the folders below), or another program's (left alone)
  const switches = st.editors.map((e) => {
    const on = e.state === "on";
    return check(on || "optional", e.name,
      e.state === "other" ? `Off. ${e.name} starts Claude Code through another program (${e.value}), so DA Private Coder leaves it alone.`
        : !on ? `Off. Claude Code in ${e.name} is normal everywhere.`
          : e.mode === PER_FOLDER ? `On. Claude Code in ${e.name} follows the folder list below.`
            : `On, set by hand: every Claude Code session in ${e.name} is ${kindName(e.mode)}.`,
      e.state === "off" ? h("button", { class: "small", onclick: () => act("POST", "/api/editor", { name: e.name, on: true },
        `${e.name} now follows the folder list. It applies to new conversations.`) }, "Turn on") : null,
      on ? h("button", { class: "small", onclick: () => act("POST", "/api/editor", { name: e.name, on: false },
        `Claude Code in ${e.name} is normal everywhere again. Conversations that were using DA Private Coder stop at their next request.`) },
        "Turn off") : null);
  });

  // a folder's choice: a kind of session, or off the list (it then does as every other folder does)
  const rows = f.list.map((x) => h("div", { class: "item" },
    h("span", { class: "path", title: x.folder }, h("bdi", { dir: "ltr" }, x.folder)),
    SESSIONS.includes(x.profile) ? chipOf(x.profile) : null,
    h("select", { class: "inline", onchange: (e) => set({ folder: x.folder, profile: e.target.value },
      `Saved. New conversations in this folder use ${kindModels(e.target.value)}.`) },
      SESSIONS.map((k) => h("option", { value: k, selected: k === x.profile }, KINDS[k].label))),
    h("button", { class: "small ghost", title: `Remove from the list. This folder then uses ${f.other ? KINDS[f.other].label : "normal Claude Code"}, like all other folders.`,
      onclick: () => set({ folder: x.folder, profile: "" },
        `Removed. New conversations there use ${f.other ? kindModels(f.other) : "normal Claude Code"}, like all other folders.`) }, "×")));

  // a folder typed or chosen here goes on the list as Private: the safe choice, changed in its row
  const add = () => set({ folder: S.drafts.editorFolder || "", profile: "private" },
    "Added as Private. New conversations there use private models.")
    .then((done) => { if (done) { delete S.drafts.editorFolder; render(); } });
  const addInput = h("input", { type: "text", class: "mono", value: S.drafts.editorFolder || "",
    placeholder: st.can_choose_folder ? "Add a folder: type its path, or Choose…" : "Add a folder: type its path",
    oninput: (e) => { S.drafts.editorFolder = e.target.value; }, onkeydown: (e) => { if (e.key === "Enter") add(); } });
  const addRow = h("div", { class: "row" }, addInput,
    st.can_choose_folder ? h("button", { class: "small", onclick: () => guarded(async () => {
      const { folder: picked } = await api("POST", "/api/choose-folder", { start: S.drafts.editorFolder || "" });
      if (picked) { S.drafts.editorFolder = picked; render(); }
    }) }, "Choose…") : null,
    h("button", { class: "small", onclick: add }, "Add"));

  const other = h("div", { class: "model-row" }, h("label", {}, "All other folders"),
    h("select", { onchange: (e) => set({ other: e.target.value },
      `Saved. New conversations in all other folders use ${e.target.value ? kindModels(e.target.value) : "normal Claude Code"}.`) },
      [["", NORMAL], ...SESSIONS.map((k) => [k, KINDS[k].label])].map(([v, l]) => h("option", { value: v, selected: v === f.other }, l))));

  const on = st.editors.some((e) => e.state === "on" && e.mode === PER_FOLDER);
  return h("div", { class: "section" }, h("h3", {}, `Claude Code in ${names.join(" and ")}`),
    h("p", { class: "lead" }, "Choose which folders' Claude Code uses DA Private Coder. A listed folder includes the folders inside it. "
      + "Turn DA Private Coder on in each editor for the list to apply there."),
    switches,
    f.error ? h("div", { class: "warnbox" }, f.error) : null,
    h("div", { class: "small muted spaced" }, "Folders"),
    rows.length ? h("div", { class: "recent" }, rows) : h("div", { class: "small muted" }, "No folders yet."),
    addRow,
    h("div", { class: "spaced" }), other,
    SESSIONS.includes(f.other) ? warningOf(st, f.other) : null,
    on ? null : h("div", { class: "warnbox" }, `DA Private Coder is off in ${names.length > 1 ? "both editors" : names[0]}, so this list isn't used yet. Turn it on above.`),
    h("div", { class: "small muted spaced" }, "Changes apply to new conversations. A conversation already open in a folder whose "
      + "setting changes stops at its next request: open a new Claude Code tab to continue."));
}

// ------------------------------------------------------------------ web search

// a profile's web search, under its models (the same tab)
function searchPart(st, tab, p) {
  const pick = (value, said) => act("POST", "/api/profile", { profile: tab, fields: { web_search: value } }, said);
  const radio = (value, title, desc) => h("label", { class: "radio" },
    h("input", { type: "radio", name: "search", checked: p.web_search === value,
      onchange: () => pick(value, "Saved. New sessions search this way.") }),
    h("div", {}, h("div", {}, title), h("div", { class: "small muted" }, desc)));
  return h("div", { class: "subsection" }, h("h4", {}, `Web search in ${KINDS[tab].label} sessions`),
    h("p", { class: "lead" }, "Claude Code's built-in search goes through Anthropic, so DA Private Coder replaces it with a search "
      + "through NanoGPT. Claude Code asks you before each search, and a search that names this computer or contains a secret is "
      + "blocked. No search is end-to-end encrypted."),
    radio("zero-retention", "Keep nothing: Linkup only",
      "Linkup doesn't store searches (zero data retention), and a search never falls back to another provider. Fewer results. "
      + "NanoGPT and Linkup can still read each search."),
    radio("best", "Best results: Perplexity and Kagi",
      "Perplexity for answers, Kagi for finding sites. Not zero data retention: NanoGPT and the search provider can read each "
      + "search, and the provider keeps it under its own policy."),
    radio("off", "No web search", "Claude Code can still fetch a page if you give it the address."),
    st.key.stored || p.web_search === "off" ? null : h("div", { class: "warnbox" }, searchNeedsNanogpt(),
      " Add it in Setup."),
    p.web_search === "best" && tab === "private" ? h("div", { class: "warnbox" }, h("b", {}, "Your sessions are private, but these searches aren't. "),
      "Perplexity and Kagi may keep your searches. Choose Keep nothing to use Linkup, which doesn't.") : null);
}

// ------------------------------------------------------------------ the enclaves

// a TEE attestation that held, as its chip names it (manager.tee_attestations, proxy.attested_as: "TEE gateway
// attested" for RedPill's gateway); one logged before Nov 11 2026 doesn't say what was attested
function attestedAs(t) {
  return t.attested_as || (t.level === "full" ? "TEE attested" : "TEE attested, only in part");
}

// whether a Private or TEE session is in use: new sessions start as one, a folder runs as one, or one is running
function sealedInUse(st) {
  const f = st.folders || {};
  const kinds = [st.start, f.other, ...(f.list || []).map((x) => x.profile), ...(st.sessions || []).map((s) => s.kind)];
  return kinds.some((k) => k === "private" || k === "tee");
}

function verifySection(st) {
  const a = st.attestation;
  const tee = st.config && st.config.profiles.tee;
  // `service`: "tinfoil" (Tinfoil's router, reached directly) or "chutes" (with `model`, its instances, without the key)
  const run = (model, service = "") => guarded(async () => {
    S.checking = model || service || "private"; render();
    try {
      const { attestation } = await api("POST", "/api/check", { ...(model ? { model } : {}), ...(service ? { service } : {}) });
      const n = ++S.asked;
      take(n, await api("GET", "/api/state"));
      toast(model ? `${model}: ${attestedAs(attestation)}.` : service === "tinfoil" ? "Tinfoil's router is verified."
        : "The enclave is verified.", "ok", 4000);
    } finally { S.checking = ""; render(); }
  });
  const ta = st.tinfoil_attestation;
  const tinfoil = serviceOf(st, "private") === "tinfoil" || ta ? [h("div", { class: "small muted spaced" }, "Tinfoil (preliminary)"),
    ta ? h("div", { class: "attest" }, h("div", {}, h("span", { class: "chip ok" }, "verified"), " ", ta.summary),
      h("div", { class: "small muted" }, `Last checked ${ta.t ? ta.t.slice(0, 16).replace("T", " ") : ""}. A session on Tinfoil `
        + "checks it before sending anything, from Tinfoil's own bundle, and pins the connection to its attested key."),
      ta.key_sha256 ? h("div", { class: "fingerprint" }, `Router key fingerprint (SHA-256): ${ta.key_sha256}`) : null)
      : h("p", { class: "lead" }, "Not checked yet. A session on Tinfoil checks it before sending anything. You can also check it now (free, no key)."),
    h("div", { class: "row" }, h("button", { disabled: Boolean(S.checking), onclick: () => run("", "tinfoil") },
      S.checking === "tinfoil" ? "Checking…" : "Check Tinfoil now"), S.checking === "tinfoil" ? h("span", { class: "spinner" }) : null)] : [];
  // a TEE profile on Chutes: what its model's instances show without the key (a session checks each one's key too)
  const chutes = serviceOf(st, "tee") === "chutes" && tee ? h("div", { class: "row" }, h("button", { disabled: Boolean(S.checking),
    onclick: () => run(tee.model, "chutes"), title: "Chutes' public evidence for a nonce of ours, checked here: no key is sent" },
    S.checking === tee.model ? "Checking…" : `Check ${tee.model}`), S.checking === tee.model ? h("span", { class: "spinner" }) : null) : null;
  // each TEE model's last attestation: what was attested ("TEE gateway attested": RedPill's gateway, not the
  // machine that runs the model), then what that leaves to someone's word, then what was found
  const teeRows = (st.tee_attestations || []).slice(0, 4).map((t) => h("div", { class: "attest" },
    h("div", {}, h("span", { class: `chip ${t.error ? "bad" : t.level === "full" ? "ok" : "tee"}` }, t.error ? "failed" : attestedAs(t)),
      " ", h("b", {}, t.model), t.error ? ` ${t.error}` : ""),
    t.what ? h("div", { class: "small" }, t.what) : null,
    // what each shortfall costs, as its tooltip (manager.tee_attestations: llm/tee.py SHORTFALL_COSTS)
    t.error ? null : h("div", { class: "small muted", title: (t.costs || []).join("\n\n") || null }, t.summary),
    h("div", { class: "small muted" }, `Checked ${t.t ? t.t.slice(0, 16).replace("T", " ") : ""}.`)));
  // folded while no Private or TEE session is in use: its checks are theirs (open or shut as the user leaves it)
  const open = S.verifyOpen === null ? sealedInUse(st) : S.verifyOpen;
  const said = a ? `Private: verified ${a.t ? a.t.slice(0, 10) : ""}` : "Private: not checked yet";
  return h("details", { class: "section fold", open, ontoggle: (e) => { S.verifyOpen = e.target.open; } },
    h("summary", {}, h("h3", {}, "Enclave checks", h("span", { class: "small muted fold-said" },
      `${said} · TEE: ${(st.tee_attestations || []).length ? "checked" : "none checked"}`))),
    h("div", { class: "small muted" }, "Private models"),
    a ? h("div", { class: "attest" },
      h("div", {}, h("span", { class: "chip ok" }, "verified"), " ", a.summary),
      h("div", { class: "small muted" }, `Last checked ${a.t ? a.t.slice(0, 16).replace("T", " ") : ""}. Every session also checks it before sending anything, and every five minutes after that.`),
      a.key_sha256 ? h("div", { class: "fingerprint" }, `Enclave key fingerprint (SHA-256): ${a.key_sha256}`) : null)
      : h("p", { class: "lead" }, "Not checked yet. Every session checks it before sending anything. You can also check it now (free)."),
    h("div", { class: "row" }, h("button", { disabled: Boolean(S.checking), onclick: () => run("") },
      S.checking === "private" ? "Checking…" : "Check now"), S.checking === "private" ? h("span", { class: "spinner" }) : null),
    ...tinfoil,
    h("div", { class: "small muted spaced" }, "TEE models"),
    teeRows.length ? teeRows : h("p", { class: "lead" }, "None checked yet. A TEE session checks each model's enclave before sending it anything, and every 15 minutes after that. You can also check one now (this needs your NanoGPT key)."),
    tee && /^tee\//i.test(tee.model) ? h("div", { class: "row" }, h("button", { disabled: Boolean(S.checking) || !st.key.stored, onclick: () => run(tee.model),
      title: st.key.stored ? "" : "NanoGPT only shows a TEE model's attestation to someone with an API key" },
      S.checking === tee.model ? "Checking…" : `Check ${tee.model}`), S.checking === tee.model ? h("span", { class: "spinner" }) : null) : null,
    chutes,
    // Name the provider behind each TEE reply (config.py tee_name_upstreams): off by default, and what it does said
    // beside it (SealedLore's, the author's, Nov 11 2026): the one request that goes to RedPill directly, not through
    // NanoGPT. Running sessions follow it from their next reply
    st.config ? h("label", { class: "radio spaced" }, h("input", { type: "checkbox", checked: Boolean(st.config.tee_name_upstreams),
      onchange: (e) => act("POST", "/api/tee-names", { on: e.target.checked }, e.target.checked
        ? "TEE replies name the provider that ran them, from the next one." : "Nothing more is asked of api.redpill.ai.") }),
      h("div", {}, h("div", {}, "Name the provider behind each TEE reply"), h("div", { class: "small muted" }, st.tee_upstreams_note))) : null);
}

// ------------------------------------------------------------------ usage

// how each request left this computer (manager.py's "sent"): sealed to the enclave, to an attested TEE
// through NanoGPT, in the clear (a search, a standard session's models), or not at all (refused here first).
// A TEE request shows its reply's check (REPLY); one with no reply to check isn't shown to have reached the
// attested enclave, and says so rather than "TEE" (SealedLore's, Nov 11 2026)
const SENT = {
  sealed: ["private", "sealed"], chutes: ["tee", "sealed (Chutes, partial)"],
  tee: ["warn", "TEE ⚠ not shown to reach the enclave"], clear: ["warn", "clear"], "": ["", "not sent"],
};
// what a request sealed to a Chutes instance was (llm/privacy.py CHUTES_NOTE, in short)
function chutesTip() {
  return "End-to-end encrypted to one Chutes instance (preliminary), whose Intel TDX attestation, for our nonce, binds the "
    + "key it was sealed to. Partial privacy: that only the model can read it rests on software Chutes signs (its closed "
    + "aegis library, admitted by Chutes' signature, not measured). Its reply streamed, each piece held to its number.";
}

// Usage's line while it is folded: what today and this month cost, and how many requests today
function usageSaid(u) {
  const n = u.requests_today;
  return `${money(u.today) || "$0.00"} today (${n} request${n === 1 ? "" : "s"}) · ${money(u.month) || "$0.00"} this month`;
}

function usageSection(st) {
  const u = st.usage;
  const rows = u.last.flatMap((r) => {
    const [chip, how, means] = sentChip(r);
    const where = r.provider && r.provider !== "nanogpt" ? ` · ${providerSaid(r.provider)}` : "";
    const what = r.kind === "search"
      ? `web search${r.what ? `, ${r.what}` : ""}${r.results !== null && r.results !== undefined ? ` · ${r.results} results` : ""}`
      : r.what + (r.kind === "vision" ? " (image)" : "") + (r.check ? " (auto mode check)" : "") + where;
    const row = h("tr", { class: r.error ? "bad has-note" : "" },
      h("td", {}, r.t.slice(5, 16)),
      h("td", { title: r.asked_for ? `Claude Code asked for ${r.asked_for}` : "" }, what),
      h("td", {}, h("span", { class: `chip ${chip}`, title: means || null }, how)),
      h("td", { class: "num" }, tokens(r.input)),
      h("td", { class: "num" }, tokens(r.cached)),
      h("td", { class: "num" }, tokens(r.output)),
      h("td", { class: "num", title: r.estimated ? "estimated from the provider's listed prices" : "" },
        money(r.cost, true) + (r.estimated ? "~" : "")));
    // what went wrong, under it and wrapped: a column of its own would be off the window's edge
    return r.error ? [row, h("tr", { class: "bad note" }, h("td", { colspan: "7" }, r.error))] : [row];
  });
  // folded until opened (and kept open or shut as the page is drawn again every 20 s): its list of requests and
  // their errors took up the middle of the window
  return h("details", { class: "section fold", open: S.usageOpen, ontoggle: (e) => { S.usageOpen = e.target.open; } },
    h("summary", {}, h("h3", {}, "Usage", h("span", { class: "small muted fold-said" }, usageSaid(u)))),
    h("div", { class: "totals" },
      h("div", {}, h("div", { class: "big" }, money(u.today) || "$0.00"), h("div", { class: "small muted" }, `today, ${u.requests_today} requests`)),
      h("div", {}, h("div", { class: "big" }, money(u.month) || "$0.00"), h("div", { class: "small muted" }, "this month"))),
    rows.length ? h("div", { class: "usage-box" }, h("table", { class: "usage" },
      h("thead", {}, h("tr", {}, ["When", "Model", "Sent", "In", "Cached", "Out", "Cost"].map((c) => h("th", {}, c)))),
      h("tbody", {}, rows))) : h("p", { class: "lead" }, "Nothing yet. Each request's cost will show here, never its content."),
    rows.length ? h("div", { class: "small muted spaced" }, "Sent: sealed = end-to-end encrypted · TEE = to an attested "
      + "enclave (for most TEE models RedPill's gateway, which passes it on to the machine running the model), through "
      + "NanoGPT, which can read it · clear = unencrypted, readable by the provider · not sent = stopped on this computer. "
      + "Each TEE reply is checked against the record its enclave signed: verified (the same, byte for byte), signed "
      + "(its text couldn't be compared), ⚠ not verified (no record could be read), or ⚠ check failed. Only a verified "
      + "or signed reply shows its request reached the attested enclave: one with no reply to check is ⚠ not shown to "
      + "reach it. Hover over a mark for what in it is the gateway's word.") : null);
}

// ------------------------------------------------------------------ this app

function appSection(st) {
  return h("div", { class: "section" }, h("h3", {}, "This app"),
    h("div", { class: "row small" }, "Colours:",
      h("select", { onchange: (e) => { applyTheme(e.target.value); act("POST", "/api/theme", { theme: e.target.value }, "Saved."); } },
        [["dark", "Dark"], ["light", "Light"], ["system", "Match my system"]].map(([v, l]) => h("option", { value: v, selected: st.theme === v }, l)))),
    h("div", { class: "paths" },
      st.config ? h("div", {}, "Settings: ", h("span", { class: "mono" }, st.config.path)) : null,
      h("div", {}, "Request log: ", h("span", { class: "mono" }, st.usage.log)),
      h("div", {}, `DA Private Coder ${st.version}`)),
    // what closing the window does: in the tray, DA Private Coder is still open and its sessions go on
    st.tray ? h("div", { class: "small muted spaced" }, "Closing this window keeps DA Private Coder running in the tray, so "
      + "sessions using it keep working. You can also quit from the tray icon.")
      : DESKTOP ? h("div", { class: "small muted spaced" }, "Closing this window quits DA Private Coder.") : null,
    h("div", { class: "row spaced" }, h("button", { class: "small", onclick: () => guarded(async () => {
      await api("POST", "/api/quit", {});
      clearInterval(looking);
      document.body.replaceChildren(h("p", { class: "lead" }, "DA Private Coder has quit. Sessions that were using it stop "
        + "at their next request; open DA Private Coder again to continue them. Normal Claude Code isn't affected."));
    }) }, "Quit"), h("span", { class: "small muted" }, "Stops every session using DA Private Coder at its next request.")));
}

// ------------------------------------------------------------------ the page

function render() {
  const st = S.state;
  if (!st) return;
  applyTheme(st.theme);
  renderTop(st);
  const scroll = $("#page").scrollTop;
  // where Setup goes is settled once, as the window opens: choosing a provider there whose key isn't stored yet
  // (Tinfoil, say) made it needed again and moved the whole section to the top, and back on choosing NanoGPT
  if (S.setupFirst === null) S.setupFirst = needsSetup(st);
  const sections = { setup: setupSection, start: startSection, sessions: sessionsSection, editor: editorSection,
    models: modelsSection, verify: verifySection, app: appSection, usage: usageSection };
  const focused = fieldAt($("#page"), document.activeElement);
  $("#page").replaceChildren(...pageOrder(S.setupFirst).map((name) => sections[name](st)).flat().filter(Boolean));
  $("#page").scrollTop = scroll;
  fieldBack($("#page"), focused);
}

// The field with the focus, by where it is (its section, and its place among that section's fields), and where in
// its text the cursor was: the page is drawn anew, and a field drawn again in its place is given them back. The
// list of models arriving as the window opened took the focus from the project folder being typed, and the rest of
// the path went nowhere; a list changed with the keyboard lost it too (until Nov 2026).
const FIELDS = "input, select, textarea";
function fieldAt(page, el) {
  if (!el || !el.matches || !el.matches(FIELDS) || !page.contains(el)) return null;
  const sections = [...page.children];
  const section = sections.find((s) => s.contains(el));
  if (!section) return null;
  let start = null, end = null;
  try { start = el.selectionStart ?? null; end = el.selectionEnd ?? null; } catch { /* a field without a cursor */ }
  return { section: sections.indexOf(section), index: [...section.querySelectorAll(FIELDS)].indexOf(el),
    tag: el.tagName, type: el.type || "", start, end };
}
function fieldBack(page, at) {
  const section = at && page.children[at.section];
  const el = section ? section.querySelectorAll(FIELDS)[at.index] : null;
  if (!el || el.tagName !== at.tag || (el.type || "") !== at.type) return;
  el.focus({ preventScroll: true });
  if (at.start !== null) try { el.setSelectionRange(at.start, at.end); } catch { /* a field without a cursor */ }
}

// The window's sections, in order: Setup first if it was still needed when the window opened, else near the end;
// Usage, whose list of requests grows long, last of all, folded until opened
function pageOrder(setupFirst) {
  return [setupFirst && "setup", "start", "sessions", "editor", "models", "verify", !setupFirst && "setup", "app",
    "usage"].filter(Boolean);
}

async function refresh() {
  const n = ++S.asked;
  take(n, await api("GET", "/api/state"));
}

// the list of models of the tab shown, asked for unless it is there or on its way (not while drawing). The tab follows
// Start's kind until one is picked, and choosing that kind showed its tab without asking for its list: "Loading
// NanoGPT's TEE models…" stayed for good (until Nov 2026)
function wantModels(tab) {
  if (S.models[tab] || S.modelsLoading[tab]) return;
  S.modelsLoading[tab] = -1;
  setTimeout(() => loadModels(tab), 0);
}

// a profile's list of models (the one shown when not said). Only the last one asked for is kept: the list of an
// address left behind, slower to answer (one that never did, after 30 s), came after the new address's and put its
// error in its place (until Nov 2026). While one is on its way, the tab says so (S.modelsLoading)
async function loadModels(profile, fresh = false) {
  profile = profile || S.tab || (S.state && S.state.start) || "private";
  const n = ++S.modelsAsked;
  S.modelsLoading[profile] = n;
  let models = [], error = "";
  try {
    models = (await api("GET", `/api/models?profile=${encodeURIComponent(profile)}${fresh ? "&fresh=1" : ""}`)).models;
  } catch (e) {
    error = e.message;
  }
  if (S.modelsLoading[profile] !== n) return;         // asked for again since: that answer is the one shown
  delete S.modelsLoading[profile];
  S.models[profile] = models;
  S.modelsError[profile] = error;
  render();
}

$("#refresh-btn").addEventListener("click", () => guarded(async () => { S.hosts = {}; await refresh(); await loadModels("", true); }));

// A link or a file dropped on the window would load in its place: in the app window, a web page (or a
// file) where DA Private Coder's was, as if it were the app's. Every drop is cancelled, but for words
// dropped into a text field, which go into it as typed ones would.
function intoField(e) {
  const types = [...((e.dataTransfer && e.dataTransfer.types) || [])];
  return e.target instanceof Element && Boolean(e.target.closest("input, textarea")) && !types.includes("Files");
}
for (const kind of ["dragover", "drop"]) {
  document.addEventListener(kind, (e) => { if (!intoField(e)) e.preventDefault(); });
}
// whether the user is in the middle of something the page would lose if drawn again: a field or a
// list with the focus, or a key typed and not yet saved
function editing() {
  const a = document.activeElement;
  if (a && $("#page").contains(a) && a.matches("input, select, textarea")) return true;
  return [...document.querySelectorAll("#page input[type=password]")].some((i) => i.value);
}

// usage and the editor's settings can change outside the window: looked at again now and then
let looking = null;
function begin() {
  looking = setInterval(() => { if (!document.hidden && !editing()) guarded(refresh); }, 20000);
  guarded(async () => { await refresh(); await loadModels(); });
}
// in a browser opened on a one-time code: the token first, for it (launchToken)
if (!LAUNCH) begin();
else launchToken(LAUNCH).then((t) => { TOKEN = t; begin(); }, (e) => {
  document.body.replaceChildren(h("div", { class: "section" }, h("h3", {}, "This link can't open DA Private Coder."),
    h("p", { class: "lead" }, e.message)));
});
