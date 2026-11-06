"use strict";
// DA Private Coder's window: what is set up (the key, Claude Code, the commands, the editor switch),
// the private profile's models and web search, the enclave's attestation, and what it cost. Every
// change goes through the local server (server.py), which writes the same files the commands do.

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $ = (sel) => document.querySelector(sel);
// replacing: the key's Replace field is open (kept across renders, as the folder typed is)
const S = { state: null, models: null, modelsError: "", checking: false, folder: "", replacing: false };

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
    try { st = await api(method, path, body); } catch (e) { render(); throw e; }
    if (st && st.version) { S.state = st; render(); }
    if (said) toast(said, "ok", 3000);
    return true;
  });
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

// ------------------------------------------------------------------ the top bar

function renderTop(st) {
  const bar = $("#status");
  const p = st.config && st.config.profiles.private;
  let text = "Ready", cls = "ok";
  if (st.config_error) { text = "The settings file has a mistake"; cls = "bad"; }
  else if (!st.key.stored) { text = "Needs your NanoGPT API key"; cls = "wait"; }
  else if (!st.claude.path) { text = "Needs Claude Code"; cls = "wait"; }
  else if (p) text = `Ready: private sessions run on ${p.model}`;
  bar.className = `status ${cls}`;
  $("#status-text").textContent = text;
  const notes = [st.ui_notice, st.config_error].filter(Boolean);
  $("#banner").textContent = notes.join(" ");
  $("#banner").classList.toggle("hidden", !notes.length);
}

// ------------------------------------------------------------------ start

function startSection(st) {
  const open = (f) => act("POST", "/api/open", { folder: f, profile: "private" },
    "Claude Code is starting in a terminal window, on private models.");
  const ready = st.ready;
  const folder = h("input", { type: "text", class: "mono folder", placeholder: "The project's folder", value: S.folder,
    oninput: (e) => { S.folder = e.target.value; },
    onkeydown: (e) => { if (e.key === "Enter" && ready) open(S.folder); } });
  const recent = (st.recent || []).map((f) => h("div", { class: "item" },
    // cut at the start when it's too long (direction: rtl), the path itself kept left to right:
    // otherwise its first "/" is drawn at its end
    h("span", { class: "path", title: f }, h("bdi", { dir: "ltr" }, f)),
    h("button", { class: "small", disabled: !ready, onclick: () => open(f) }, "Open"),
    h("button", { class: "small ghost", title: "Forget this folder", onclick: () => act("POST", "/api/forget", { folder: f }) }, "×")));
  const installed = st.commands["claude-private"].state === "current";
  return h("div", { class: "section start" },
    h("h3", {}, "Start Claude Code", h("span", { class: "chip private" }, "private")),
    h("p", { class: "lead" }, "Each request it makes is sealed on this computer to an enclave whose attestation is checked first; NanoGPT only relays it."),
    h("div", { class: "row" }, folder,
      st.can_choose_folder ? h("button", { onclick: () => guarded(async () => {
        const { folder: picked } = await api("POST", "/api/choose-folder", { start: S.folder });
        if (picked) { S.folder = picked; render(); }
      }) }, "Choose…") : null,
      h("button", { class: "private", disabled: !ready, onclick: () => open(S.folder) }, "Open in a terminal")),
    ready ? null : h("div", { class: "small muted" }, "Set it up first (below)."),
    recent.length ? h("div", { class: "recent" }, h("div", { class: "small muted" }, "Recent folders"), recent) : null,
    installed && st.commands_on_path ? h("div", { class: "small muted" }, "In a terminal, ", h("code", {}, "claude-private"),
      " does the same in the folder you're in; ", h("code", {}, "claude"), " still runs as usual.") : null);
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

function setupSection(st) {
  const k = st.key;
  const saveKey = () => act("POST", "/api/key", { key: keyInput.value }, "The key is stored in your keyring.")
    .then((done) => { if (done && S.replacing) { S.replacing = false; render(); } });
  const keyInput = h("input", { type: "password", placeholder: "Paste your NanoGPT API key", autocomplete: "off",
    onkeydown: (e) => { if (e.key === "Enter") saveKey(); } });
  const saveButton = () => h("button", { class: "primary small", onclick: saveKey }, "Save");
  let keyRow;
  if (k.stored) {
    const replacing = h("div", { class: `row${S.replacing ? "" : " hidden"}` }, keyInput, saveButton());
    // removing takes a second click: a slip would stop every session until the key is pasted again
    const remove = h("button", { class: "small ghost", onclick: () => {
      if (remove.dataset.sure) act("POST", "/api/key", { clear: true }, "The key was removed from your keyring.");
      else {
        remove.dataset.sure = "1"; remove.textContent = "Click again to remove it";
        setTimeout(() => { delete remove.dataset.sure; remove.textContent = "Remove"; }, 4000);
      }
    } }, "Remove");
    keyRow = check(true, "NanoGPT API key", `Stored in your keyring (ending ${k.ending}).`,
      h("button", { class: "small", onclick: () => {
        S.replacing = !S.replacing;
        replacing.classList.toggle("hidden", !S.replacing);
        if (S.replacing) keyInput.focus();
      } }, "Replace"), remove);
    keyRow.querySelector(".body").append(replacing);
  } else {
    keyRow = check(false, "NanoGPT API key", k.error || "Private Mode is paid per use from your NanoGPT balance: get a key at nano-gpt.com/api.",
      keyInput, saveButton(),
      k.vibe_manager ? h("button", { class: "small", onclick: () => act("POST", "/api/key", { from: "vibe-manager" },
        "DA Vibe Manager's key is stored for DA Private Coder too.") }, "Use DA Vibe Manager's") : null);
  }

  const claude = check(Boolean(st.claude.path), "Claude Code",
    st.claude.path ? h("span", { class: "mono" }, st.claude.path) : st.claude.error);

  const cmds = Object.entries(st.commands);
  const allIn = cmds.every(([, c]) => c.state === "current");
  const taken = cmds.filter(([, c]) => c.state === "taken").map(([, c]) => c.path);
  const commandsRow = check(allIn, "The claude-private command",
    allIn ? (st.commands_on_path ? "Installed in ~/.local/bin: claude-private and daprivatecoder."
      : "Installed in ~/.local/bin, which isn't on your PATH: add it to run them by name.")
      : taken.length ? `${taken.join(", ")} is someone else's program, so it's left as it is.`
        : "For a terminal (claude-private), and for the editor's switch, which runs it.",
    allIn ? null : h("button", { class: "small primary", onclick: () => act("POST", "/api/commands", {}, "Installed.") }, "Install"));

  // on, off, other (another program's wrapper: left alone), or earlier (PrivateCode's: still the old app)
  const switchOn = (e, label) => h("button", { class: "small private", disabled: !st.ready,
    title: st.ready ? "" : "Set up the key and Claude Code first", onclick: () => act("POST", "/api/editor",
      { name: e.name, on: true }, `${e.name}'s new Claude Code tabs run on private models.`) }, label);
  const switchOff = (e) => h("button", { class: "small", onclick: () => act("POST", "/api/editor", { name: e.name, on: false },
    `${e.name}'s Claude Code runs as usual again (open a new Claude Code tab).`) }, "Back to normal");
  const editors = st.editors.length ? st.editors.map((e) => check(
    e.state === "on" ? true : e.state === "earlier" ? false : "optional", `Claude Code in ${e.name}`,
    e.state === "on" ? "Runs on private models (new Claude Code tabs). The extension takes this for the whole editor."
      : e.state === "earlier" ? `Runs PrivateCode's claude-private (${e.value}), this app's earlier version, not DA Private Coder's.`
        : e.state === "other" ? `Runs through another program (${e.value}), so this is left as it is.`
          : "Runs as usual, on Anthropic's models.",
    e.state === "on" ? switchOff(e) : null,
    e.state === "off" ? switchOn(e, "Use private models") : null,
    e.state === "earlier" ? switchOn(e, "Use DA Private Coder's") : null,
    e.state === "earlier" ? switchOff(e) : null))
    : [check("optional", "Claude Code in your editor", "No VSCodium or VS Code here: use claude-private in a terminal.")];

  return h("div", { class: "section" }, h("h3", {}, "Setup"), keyRow, claude, commandsRow, ...editors);
}

// ------------------------------------------------------------------ the private profile's models

const ROLES = [
  ["model", "Default", "What Claude Code starts with, and /model sonnet"],
  ["opus_model", "Opus", "What /model opus picks"],
  ["fast_model", "Quick", "/model haiku, and Claude Code's quick background tasks"],
  ["vision_model", "Images", "Describes screenshots for a model that can't see them"],
];

// without "private/": every model here is one, and the label then fits the window's width
function modelLabel(m) {
  const p = m.price ? ` · $${price(m.price.input)} / $${price(m.price.cache_read)} / $${price(m.price.output)}` : "";
  return `${m.id.replace(/^private\//, "")}${p}${m.vision ? " · sees images" : ""}${m.tools === false ? " · no tool calls" : ""}`;
}

function modelsSection(st) {
  const p = st.config && st.config.profiles.private;
  if (!p) return null;
  const save = (fields, said = "Saved: the next session uses it.") => act("POST", "/api/profile", { profile: "private", fields }, said);
  const list = S.models || [];
  const rows = ROLES.map(([key, label, hint]) => {
    const current = p[key] || "";
    const usable = list.filter((m) => key === "vision_model" ? m.vision : m.tools !== false);
    const options = [];
    if (key === "vision_model") options.push(h("option", { value: "", selected: !current }, "None: the model is told it can't see"));
    if (current && !list.some((m) => m.id === current)) {
      options.push(h("option", { value: current, selected: true }, S.models ? `${current} (no longer offered)` : current));
    }
    for (const m of usable) options.push(h("option", { value: m.id, selected: m.id === current }, modelLabel(m)));
    return h("div", { class: "model-row", title: hint }, h("label", {}, label),
      h("select", { onchange: (e) => save({ [key]: e.target.value }) }, options));
  });
  return h("div", { class: "section" }, h("h3", {}, "Models", h("span", { class: "chip private" }, "end-to-end encrypted")),
    h("p", { class: "lead" }, "NanoGPT's Private Mode models, per million tokens: input / cached input / output, with its 5% markup."),
    rows,
    h("div", { class: "model-row" }, h("label", {}, "Reasoning"),
      h("select", { onchange: (e) => save({ reasoning: e.target.value }) },
        [["low", "Low (fast; recommended)"], ["medium", "Medium"], ["high", "High (slow)"], ["max", "Max (slowest)"]]
          .map(([v, l]) => h("option", { value: v, selected: p.reasoning === v }, l)))),
    S.modelsError ? h("div", { class: "warnbox" }, `NanoGPT's list of models couldn't be read: ${S.modelsError}`) : null,
    S.models ? null : h("div", { class: "small muted row" }, h("span", { class: "spinner" }), "Reading NanoGPT's list of private models…"));
}

// ------------------------------------------------------------------ web search

function searchSection(st) {
  const p = st.config && st.config.profiles.private;
  if (!p) return null;
  const pick = (value, said) => act("POST", "/api/profile", { profile: "private", fields: { web_search: value } }, said);
  const radio = (value, title, desc) => h("label", { class: "radio" },
    h("input", { type: "radio", name: "search", checked: p.web_search === value,
      onchange: () => pick(value, "Saved: the next session searches this way.") }),
    h("div", {}, h("div", {}, title), h("div", { class: "small muted" }, desc)));
  return h("div", { class: "section" }, h("h3", {}, "Web search"),
    h("p", { class: "lead" }, "Claude Code's own search runs on Anthropic's servers, so DA Private Coder gives it one through NanoGPT. Claude Code asks you before each search, and a search naming this computer or holding a secret is refused."),
    radio("zero-retention", "Keep nothing: Linkup only",
      "Linkup keeps nothing it's sent (zero data retention), and no search goes anywhere else, even when Linkup fails. It finds less. NanoGPT and Linkup still read each search: no search is end-to-end encrypted."),
    radio("best", "Best results: Perplexity and Kagi",
      "Perplexity to learn things, Kagi to find sites. Not zero data retention: NanoGPT and the provider read each search, and what the provider keeps is up to its own policy."),
    radio("off", "No web search", "Claude Code works with what it knows, and pages it's given the address of."),
    p.web_search === "best" ? h("div", { class: "warnbox" }, h("b", {}, "Your sessions are private; their searches aren't. "),
      "Perplexity and Kagi aren't zero data retention. Choose Keep nothing to search with Linkup, which keeps nothing.") : null);
}

// ------------------------------------------------------------------ the enclave

function verifySection(st) {
  const a = st.attestation;
  const run = () => guarded(async () => {
    S.checking = true; render();
    try {
      await api("POST", "/api/check", {});
      S.state = await api("GET", "/api/state");
      toast("The enclave is verified.", "ok", 3000);
    } finally { S.checking = false; render(); }
  });
  return h("div", { class: "section" }, h("h3", {}, "The enclave"),
    a ? h("div", { class: "attest" },
      h("div", {}, h("span", { class: "chip ok" }, "verified"), " ", a.summary),
      h("div", { class: "small muted" }, `Last checked ${a.t ? a.t.slice(0, 16).replace("T", " ") : ""}. Every session checks it again before it sends anything, and every five minutes.`),
      a.key_sha256 ? h("div", { class: "fingerprint" }, `Its key's fingerprint (sha256): ${a.key_sha256}`) : null)
      : h("p", { class: "lead" }, "Not checked yet here. Every session checks it before it sends anything; you can check it now too (free)."),
    h("div", { class: "row" }, h("button", { disabled: S.checking, onclick: run },
      S.checking ? "Checking…" : "Check it now"), S.checking ? h("span", { class: "spinner" }) : null));
}

// ------------------------------------------------------------------ usage

// how each request left this computer (manager.py's "sent"): sealed to the enclave, in the clear
// (a search, or the nano profile's models), or not at all (refused here first)
const SENT = { sealed: ["private", "sealed"], clear: ["warn", "clear"], "": ["", "not sent"] };

function usageSection(st) {
  const u = st.usage;
  const rows = u.last.flatMap((r) => {
    const [chip, how] = SENT[r.sent] || SENT[""];
    const what = r.kind === "search"
      ? `web search${r.what ? `, ${r.what}` : ""}${r.results !== null && r.results !== undefined ? ` · ${r.results} results` : ""}`
      : r.what + (r.kind === "vision" ? " (image)" : "");
    const row = h("tr", { class: r.error ? "bad has-note" : "" },
      h("td", {}, r.t.slice(5, 16)),
      h("td", { title: r.asked_for ? `Claude Code asked for ${r.asked_for}` : "" }, what),
      h("td", {}, h("span", { class: `chip ${chip}` }, how)),
      h("td", { class: "num" }, tokens(r.input)),
      h("td", { class: "num" }, tokens(r.cached)),
      h("td", { class: "num" }, tokens(r.output)),
      h("td", { class: "num", title: r.estimated ? "estimated from NanoGPT's listed prices" : "" },
        money(r.cost, true) + (r.estimated ? "~" : "")));
    // what went wrong, under it and wrapped: a column of its own would be off the window's edge
    return r.error ? [row, h("tr", { class: "bad note" }, h("td", { colspan: "7" }, r.error))] : [row];
  });
  return h("div", { class: "section" }, h("h3", {}, "Usage"),
    h("div", { class: "totals" },
      h("div", {}, h("div", { class: "big" }, money(u.today) || "$0.00"), h("div", { class: "small muted" }, `today, ${u.requests_today} requests`)),
      h("div", {}, h("div", { class: "big" }, money(u.month) || "$0.00"), h("div", { class: "small muted" }, "this month"))),
    rows.length ? h("div", { class: "usage-box" }, h("table", { class: "usage" },
      h("thead", {}, h("tr", {}, ["When", "Model", "", "In", "Cached", "Out", "Cost"].map((c) => h("th", {}, c)))),
      h("tbody", {}, rows))) : h("p", { class: "lead" }, "Nothing yet. What each request costs shows here; never what was said."));
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
    h("div", { class: "row" }, h("button", { class: "small ghost", onclick: () => guarded(async () => {
      await api("POST", "/api/quit", {});
      document.body.replaceChildren(h("p", { class: "lead" }, "DA Private Coder has quit. Sessions it opened go on in their terminals."));
    }) }, "Quit")));
}

// ------------------------------------------------------------------ the page

function render() {
  const st = S.state;
  if (!st) return;
  applyTheme(st.theme);
  renderTop(st);
  const scroll = $("#page").scrollTop;
  $("#page").replaceChildren(...[startSection(st), setupSection(st), modelsSection(st), searchSection(st),
    verifySection(st), usageSection(st), appSection(st)].filter(Boolean));
  $("#page").scrollTop = scroll;
}

async function refresh() {
  S.state = await api("GET", "/api/state");
  render();
}

async function loadModels(fresh = false) {
  try {
    S.models = (await api("GET", `/api/models${fresh ? "?fresh=1" : ""}`)).models;
    S.modelsError = "";
  } catch (e) {
    S.models = [];
    S.modelsError = e.message;
  }
  render();
}

$("#refresh-btn").addEventListener("click", () => guarded(async () => { await refresh(); await loadModels(true); }));
// whether the user is in the middle of something the page would lose if drawn again: a field or a
// list with the focus, or a key typed and not yet saved
function editing() {
  const a = document.activeElement;
  if (a && $("#page").contains(a) && a.matches("input, select, textarea")) return true;
  return [...document.querySelectorAll("#page input[type=password]")].some((i) => i.value);
}

// usage and the editor's settings can change outside the window: looked at again now and then
setInterval(() => { if (!document.hidden && !editing()) guarded(refresh); }, 20000);
guarded(async () => { await refresh(); await loadModels(); });
