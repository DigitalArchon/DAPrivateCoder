"use strict";
// DA Private Coder's window: what is set up (the keys, Claude Code, the commands, the editor switch),
// the private, TEE and standard profiles' models, the hosts that run them, and their web search, the
// enclaves' attestations, and what it cost. Every change goes through the local server (server.py),
// which writes the same files the commands do.

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $ = (sel) => document.querySelector(sel);
// kept across renders, as the folder typed is: the Models tab shown (a profile), each profile's models and
// the hosts listed for a model, the key whose Replace field is open, a route being chosen (and, per model,
// whether its hosts below FP8 are left out), what is typed and not yet saved (drafts: a key, an address),
// the actions under way (busy: not sent twice), what is being checked
const S = { state: null, folder: "", tab: "", models: {}, modelsError: {}, hosts: {}, routeHost: {}, routeFp8: {},
  customUrl: false, replacing: "", checking: "", drafts: {}, busy: {}, asked: 0, shown: 0 };
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
const PROVIDERS = { nanogpt: "NanoGPT", openrouter: "OpenRouter", custom: "Another provider (OpenAI-compatible)" };

const chipOf = (kind) => h("span", { class: `chip ${KINDS[kind].chip[0]}` }, KINDS[kind].chip[1]);

function providerName(st, profile) {
  const p = st.config && st.config.profiles[profile];
  if (!p || p.kind !== "standard") return "NanoGPT";
  return p.provider === "custom" ? (p.base_url || "your provider") : PROVIDERS[p.provider];
}

// what a session of `kind` is, in a sentence; and for one that isn't private, what it gives away
function leadOf(st, kind) {
  if (kind === "private") return "Each request is sealed on this computer to an enclave whose attestation is checked first; NanoGPT only relays it.";
  if (kind === "tee") return "NanoGPT's TEE models. Each runs in an enclave this app attests before it sends it anything (Intel TDX, NVIDIA GPUs), and again every 15 minutes.";
  return `Any model of ${providerName(st, "standard")}, as you choose it below (Models).`;
}

function warningOf(st, kind) {
  if (kind === "tee") return h("div", { class: "warnbox" }, h("b", {}, "Not end-to-end encrypted. "),
    "NanoGPT's gateway reads every prompt, the files Claude Code sends and every reply, in the clear: the enclave protects them only from the host that runs the model. For that too, choose Private.");
  if (kind === "standard") return h("div", { class: "warnbox" }, h("b", {}, "Not private. "),
    `Every prompt, the files Claude Code reads and every reply go in the clear to ${providerName(st, "standard")} and the host that runs the model, and what they keep is up to them.`);
  return null;
}

// why a session of `kind` can't start yet, or "" when it can
function notReady(st, kind) {
  if (st.ready[kind]) return "";
  if (!st.claude.path) return "Claude Code isn't found here (Setup).";
  if (kind === "standard" && st.provider_key) return `Store your ${providerName(st, "standard")} API key first (Models, Standard).`;
  return "Store your NanoGPT API key first (Setup).";
}

// three buttons, one pressed: the kinds of session
function seg(current, pick) {
  return h("div", { class: "seg" }, SESSIONS.map((k) => h("button", {
    class: `seg-btn kind-${k}${k === current ? " on" : ""}`, "aria-pressed": k === current ? "true" : "false",
    onclick: () => pick(k) }, KINDS[k].label)));
}

// ------------------------------------------------------------------ the top bar

function renderTop(st) {
  const bar = $("#status");
  let text = "Ready", cls = "ok";
  if (st.config_error) { text = "The settings file has a mistake"; cls = "bad"; }
  else if (!st.claude.path) { text = "Needs Claude Code"; cls = "wait"; }
  else if (!SESSIONS.some((k) => st.ready[k])) { text = "Needs an API key"; cls = "wait"; }
  else text = `Ready: Start opens ${KINDS[st.start].label} sessions`;
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
    `Claude Code is starting in a terminal window, on ${KINDS[kind].models}.`));
  // the folder in the editor, its Claude Code on this kind of session; other folders stay as they are
  const openIn = (e, f) => once(`open:${e.name}`, () => act("POST", "/api/open-editor", { name: e.name, folder: f, profile: kind },
    `${e.name} opens it: Claude Code there runs on ${KINDS[kind].models} (in a new conversation, if one was open). Other folders are as they were.`));
  const editorsHere = (st.editors || []).filter((e) => e.opens && e.state !== "other");
  const folder = h("input", { type: "text", class: "mono folder", placeholder: "The project's folder", value: S.folder,
    oninput: (e) => { S.folder = e.target.value; },
    onkeydown: (e) => { if (e.key === "Enter" && !why) open(S.folder); } });
  const recent = (st.recent || []).map((f) => h("div", { class: "item" },
    // cut at the start when it's too long (direction: rtl), the path itself kept left to right:
    // otherwise its first "/" is drawn at its end; a click puts it in the folder field
    h("span", { class: "path link", title: `${f} (click: open it in the editor or a terminal above)`,
      onclick: () => { S.folder = f; render(); } }, h("bdi", { dir: "ltr" }, f)),
    h("button", { class: "small", disabled: Boolean(why) || opening, onclick: () => open(f) }, "Open"),
    h("button", { class: "small ghost", title: "Forget this folder", onclick: () => act("POST", "/api/forget", { folder: f }) }, "×")));
  const installed = st.commands["claude-private"].state === "current";
  const command = kind === "private" ? "claude-private" : `daprivatecoder claude --profile ${kind}`;
  return h("div", { class: `section start ${kind}` },
    h("h3", {}, "Start Claude Code", chipOf(kind)),
    seg(kind, (pick) => { if (pick !== kind) act("POST", "/api/start", { session: pick }); }),
    h("p", { class: "lead" }, leadOf(st, kind)),
    warningOf(st, kind),
    h("div", { class: "row" }, folder,
      st.can_choose_folder ? h("button", { onclick: () => guarded(async () => {
        const { folder: picked } = await api("POST", "/api/choose-folder", { start: S.folder });
        if (picked) { S.folder = picked; render(); }
      }) }, "Choose…") : null,
      h("button", { class: kind, disabled: Boolean(why) || opening, onclick: () => open(S.folder) }, "Open in a terminal")),
    editorsHere.length ? h("div", { class: "row" }, ...editorsHere.map((e) => h("button", { class: kind,
      disabled: Boolean(why) || opening, title: `Opens the folder in ${e.name}, its Claude Code on ${KINDS[kind].models}: other folders stay as they are`,
      onclick: () => openIn(e, S.folder) }, `Open in ${e.name}`))) : null,
    why ? h("div", { class: "small muted" }, why) : null,
    recent.length ? h("div", { class: "recent" }, h("div", { class: "small muted" }, "Recent folders"), recent) : null,
    installed && st.commands_on_path ? h("div", { class: "small muted" }, "In a terminal, ", h("code", {}, command),
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

// a key in the keyring: stored (Replace, Remove: a second click), or a field to paste it in. `slot` keeps
// the Replace field open across renders (S.replacing)
function keyControls(slot, state, provider, label, said) {
  const draft = `key:${slot}`;
  const saveKey = () => act("POST", "/api/key", { key: keyInput.value, provider }, said)
    .then((done) => { if (done) { delete S.drafts[draft]; if (S.replacing === slot) S.replacing = ""; render(); } });
  const keyInput = h("input", { type: "password", placeholder: `Paste your ${label} API key`, autocomplete: "off",
    value: S.drafts[draft] || "", oninput: (e) => { S.drafts[draft] = e.target.value; },
    onkeydown: (e) => { if (e.key === "Enter") saveKey(); } });
  const saveButton = h("button", { class: "primary small", onclick: saveKey }, "Save");
  if (!state.stored) return { stored: false, controls: [keyInput, saveButton], extra: null };
  const replacing = h("div", { class: `row${S.replacing === slot ? "" : " hidden"}` }, keyInput, saveButton);
  // removing takes a second click: a slip would stop every session until the key is pasted again
  const remove = h("button", { class: "small ghost", onclick: () => {
    if (remove.dataset.sure) act("POST", "/api/key", { clear: true, provider }, "The key was removed from your keyring.");
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

function setupSection(st) {
  const k = st.key;
  const nano = keyControls("nanogpt", k, "nanogpt", "NanoGPT", "The key is stored in your keyring.");
  const keyRow = check(nano.stored, "NanoGPT API key",
    nano.stored ? `Stored in your keyring (ending ${k.ending}).`
      : k.error || "For private and TEE sessions, a standard one on NanoGPT, and web search: paid per use from your NanoGPT balance. Get a key at nano-gpt.com/api.",
    ...nano.controls,
    !nano.stored && k.vibe_manager ? h("button", { class: "small", onclick: () => act("POST", "/api/key", { from: "vibe-manager" },
      "DA Vibe Manager's key is stored for DA Private Coder too.") }, "Use DA Vibe Manager's") : null);
  if (nano.extra) keyRow.querySelector(".body").append(nano.extra);

  const claude = check(Boolean(st.claude.path), "Claude Code",
    st.claude.path ? h("span", { class: "mono" }, st.claude.path) : st.claude.error);

  const cmds = Object.entries(st.commands);
  const allIn = cmds.every(([, c]) => c.state === "current");
  const taken = cmds.filter(([, c]) => c.state === "taken").map(([, c]) => c.path);
  // only for typing them in a terminal: this window runs the app itself, and the editor's switch puts
  // what it runs in place when it's turned on
  const commandsRow = check(allIn || "optional", "Terminal commands",
    allIn ? (st.commands_on_path ? "claude-private and daprivatecoder are in ~/.local/bin: type them in any terminal."
      : "In ~/.local/bin, which isn't on your PATH: add it to run claude-private and daprivatecoder by name.")
      : taken.length ? `${taken.join(", ")} is someone else's program, so it's left as it is.`
        : "Optional: puts claude-private and daprivatecoder in ~/.local/bin, to type in any terminal. Opening Claude Code "
          + "from this window doesn't need them, and the editor's switch puts what it runs in place itself.",
    allIn ? null : h("button", { class: "small", onclick: () => act("POST", "/api/commands", {}, "Installed.") }, "Install"));

  // each editor's switch: off (as usual everywhere), on (each folder as chosen: Folders in your editor), or
  // another program's (left alone)
  const editors = st.editors.length ? st.editors.map((e) => {
    const on = e.state === "on";
    return check(on || "optional", `Claude Code in ${e.name}`,
      e.state === "other" ? `Runs through another program (${e.value}), so this is left as it is.`
        : !on ? "Runs as usual in every folder, on Anthropic's models. Switched on, the folders you choose (below, or Open in "
          + `${e.name} above) run on DA Private Coder, and the rest as usual.`
          : e.mode === PER_FOLDER ? "On: each folder runs as chosen in Folders in your editor, below. Other windows run as usual."
            : `On: every session on DA Private Coder's ${e.mode} profile (set by hand to claude-private).`,
      e.state === "off" ? h("button", { class: "small", onclick: () => act("POST", "/api/editor", { name: e.name, on: true },
        `${e.name}'s Claude Code now runs as each folder says, in a new conversation.`) }, "Switch on") : null,
      on ? h("button", { class: "small", onclick: () => act("POST", "/api/editor", { name: e.name, on: false },
        `${e.name}'s Claude Code runs as usual everywhere. Conversations open on DA Private Coder stop at their next request.`) },
        "Back to normal") : null);
  }) : [check("optional", "Claude Code in your editor", "No VSCodium or VS Code here: use claude-private in a terminal.")];

  return h("div", { class: "section" }, h("h3", {}, "Setup"), keyRow, claude, commandsRow, ...editors);
}

// ------------------------------------------------------------------ each profile's models, and the hosts that run them

const ROLES = [
  ["model", "Default", "What Claude Code starts with, and /model sonnet"],
  ["opus_model", "Opus", "What /model opus picks"],
  ["fast_model", "Quick", "/model haiku, and Claude Code's quick background tasks"],
  ["vision_model", "Images", "Describes screenshots for a model that can't see them (one that can sees them itself)"],
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
  const id = kind === "private" ? m.id.replace(/^private\//, "") : kind === "tee" ? m.id.replace(/^TEE\//i, "") : m.id;
  return `${id}${p}${m.vision ? " · sees images" : ""}${m.tools === false ? " · no tool calls" : ""}${m.included ? " · subscription" : ""}`;
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
        hosts.unshift({ id: route.host, name: `${route.host_name || route.host} (not listed now)` });
      }
      const speed = (x) => x.tokens_per_second ? ` · ${Math.round(x.tokens_per_second)} tok/s` : "";
      const cost = (x) => x.input !== null && x.input !== undefined ? ` · $${price(x.input)} / $${price(x.output)}` : "";
      parts.push(h("select", { onchange: (e) => {
        const x = listed.hosts.find((y) => y.id === e.target.value);
        if (x) save({ priority: "host", host: x.id, host_name: x.name, fp8 });
      } }, [h("option", { value: "", selected: !(route && route.host) }, hosts.length ? "Choose the host…" : "No host listed")]
        .concat(hosts.map((x) => h("option", { value: x.id, selected: route && route.host === x.id },
          `${x.name}${x.precision ? ` · ${x.precision.toUpperCase()}` : ""}${cost(x)}${speed(x)}${x.keeps_nothing ? " · keeps nothing" : ""}`)))));
    } else parts.push(h("span", { class: "small muted" }, listed && listed.error ? listed.error : "Reading its hosts…"));
  }
  if (route || choosing) parts.push(h("label", { class: "small muted tick", title: "Leave out hosts that run it at less than FP8 (or don't say)" },
    h("input", { type: "checkbox", checked: fp8,
      onchange: (e) => { if (route) save({ ...route, fp8: e.target.checked }); else { S.routeFp8[model] = e.target.checked; render(); } } }),
    "FP8 or better"));
  return h("div", { class: "model-row route" }, h("label", {}, "Runs on"), h("div", { class: "row" }, ...parts));
}

// a standard profile's provider: NanoGPT, OpenRouter, or an OpenAI-compatible endpoint of your own, and its key
function providerRows(st, tab, p, save) {
  const custom = p.provider === "custom" || S.customUrl;
  const pick = h("select", { onchange: (e) => {
    const v = e.target.value;
    if (v === "custom" && !p.base_url) { S.customUrl = true; render(); }
    else { S.customUrl = false; save({ provider: v }, `Saved: standard sessions use ${PROVIDERS[v]}.`); }
  } }, Object.entries(PROVIDERS).map(([v, l]) => h("option", { value: v, selected: v === (custom ? "custom" : p.provider) }, l)));
  const rows = [h("div", { class: "model-row" }, h("label", {}, "Provider"), pick)];
  if (custom) {
    const url = h("input", { type: "text", class: "mono", value: S.drafts.url ?? (p.base_url || ""), placeholder: "https://api.example.com/v1",
      oninput: (e) => { S.drafts.url = e.target.value; }, onkeydown: (e) => { if (e.key === "Enter") go(); } });
    const go = () => save({ provider: "custom", base_url: url.value.trim() }, "Saved: standard sessions use it.")
      .then((done) => { if (done) { S.customUrl = false; delete S.drafts.url; } });
    rows.push(h("div", { class: "model-row" }, h("label", {}, "Address"), h("div", { class: "row" }, url,
      h("button", { class: "small primary", onclick: go }, "Save"))),
      h("div", { class: "model-row" }, h("span", {}), h("div", { class: "small muted" },
        "Its OpenAI-compatible endpoint: https, or http only on this computer or your network.")));
  }
  const pk = st.provider_key;
  if (pk && !S.customUrl) {
    const label = p.provider === "openrouter" ? "OpenRouter" : "your provider's";
    const keys = keyControls(`provider:${p.provider}`, pk, p.provider, label, "The key is stored in your keyring.");
    rows.push(h("div", { class: "model-row" }, h("label", {}, "API key"), h("div", { class: "col" },
      h("div", { class: "row" }, keys.stored ? h("span", { class: "small muted" }, `Stored (ending ${pk.ending})`) : null,
        ...keys.controls),
      keys.extra, !keys.stored && p.provider === "custom" ? h("div", { class: "small muted" }, "If it needs one.") : null)));
  }
  return rows;
}

function modelsSection(st) {
  const tab = S.tab || st.start;
  const p = st.config && st.config.profiles[tab];
  if (!p) return null;
  const save = (fields, said = "Saved: the next session uses it.") => act("POST", "/api/profile", { profile: tab, fields }, said)
    .then((done) => { if (done && ("provider" in fields || "base_url" in fields)) { S.hosts = {}; loadModels(tab, true); } return done; });
  const list = S.models[tab] || [];
  const routed = new Set();
  const rows = ROLES.flatMap(([key, label, hint]) => {
    const current = p[key] || "";
    const usable = list.filter((m) => key === "vision_model" ? m.vision : m.tools !== false);
    const options = [];
    if (key === "vision_model") options.push(h("option", { value: "", selected: !current }, "None: a model that can't see is told so"));
    if (current && !list.some((m) => m.id === current)) {
      options.push(h("option", { value: current, selected: true }, S.models[tab] ? `${current} (not listed now)` : current));
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
  const lead = tab === "private" ? "NanoGPT's Private Mode models, per million tokens: input / cached input / output, with its 5% markup."
    : tab === "tee" ? "NanoGPT's TEE models, per million tokens: input / cached input / output."
      : `${providerName(st, tab)}'s models that take tool calls${p.provider === "custom" ? "" : ", per million tokens: input / cached input / output"}.`
        + (p.provider === "nanogpt" ? " On NanoGPT's own choice of host, a subscription covers the ones it includes; a host you choose is paid per use." : "");
  const reading = tab === "standard" ? `Reading ${providerName(st, tab)}'s list of models…` : `Reading NanoGPT's list of ${KINDS[tab].label === "TEE" ? "TEE" : "private"} models…`;
  return h("div", { class: "section" }, h("h3", {}, "Models", chipOf(tab)),
    seg(tab, (pick) => { S.tab = pick; S.customUrl = false; if (!S.models[pick]) loadModels(pick); render(); }),
    warningOf(st, tab),
    p.kind === "standard" ? providerRows(st, tab, p, save) : null,
    h("p", { class: "lead" }, lead),
    rows,
    // where each session's effort starts: Claude Code's own (the editor's Effort, /effort) changes it
    // in a session, and the model runs at whatever Claude Code says (proxy.py)
    h("div", { class: "model-row" }, h("label", {}, "Effort"),
      h("select", { onchange: (e) => save({ reasoning: e.target.value }) },
        [["low", "Low (fastest)"], ["medium", "Medium"], ["high", "High (slow)"], ["max", "Max (slowest)"]]
          .map(([v, l]) => h("option", { value: v, selected: p.reasoning === v }, l)))),
    h("div", { class: "model-row" }, h("span", {}), h("div", { class: "small muted" },
      "How hard the models think as each session starts. In a session, Claude Code's own Effort (in the editor, or ",
      h("code", {}, "/effort"), ") changes it. A model that doesn't take a level runs at the nearest it does: ",
      tab === "private" ? "the private models have no medium (it runs as high), nor xhigh (as max)." : "as its provider lists them.")),
    S.modelsError[tab] ? h("div", { class: "warnbox" }, `The list of models couldn't be read: ${S.modelsError[tab]}`) : null,
    S.models[tab] ? null : h("div", { class: "small muted row" }, h("span", { class: "spinner" }), reading));
}

// ------------------------------------------------------------------ what runs on it now

// where a request went, in words, after its chip
function whereItWent(last) {
  if (last.sent === "sealed") return "sealed to the attested enclave";
  if (last.sent === "tee") return "to an attested enclave, through NanoGPT (not end-to-end encrypted)";
  if (last.sent === "clear") return `in the clear, to ${last.provider === "openrouter" ? "OpenRouter" : last.provider === "custom" ? "your provider" : "NanoGPT"}`;
  return "not sent";
}

// each session on DA Private Coder now: what it runs, where, and where its last request went
function sessionsSection(st) {
  const list = st.sessions || [];
  const rows = list.map((s) => {
    const kind = SESSIONS.includes(s.kind) ? s.kind : "standard";
    const last = s.last;
    const [chip, how] = last ? (SENT[last.sent] || SENT[""]) : ["", ""];
    return h("div", { class: "session" },
      h("div", { class: "row" }, chipOf(kind),
        h("span", { class: "path", title: s.folder }, h("bdi", { dir: "ltr" }, s.folder || "?")),
        h("span", { class: "small muted" }, s.editor ? `in ${s.editor}` : "in a terminal")),
      last ? h("div", { class: "small" }, `Last request ${last.t.slice(11, 16)}: `, h("b", { class: "mono" }, last.model || "?"),
        last.asked_for ? ` (Claude Code's quick work, for ${last.asked_for})` : "", " ", h("span", { class: `chip ${chip}` }, how), " ",
        whereItWent(last), last.error ? h("div", { class: "small bad-text" }, last.error) : null)
        : h("div", { class: "small muted" }, "No request yet."),
      s.stops ? h("div", { class: "warnbox" }, h("b", {}, "Stops at its next request. "), s.stops) : null);
  });
  return h("div", { class: "section" }, h("h3", {}, "Running now"),
    h("p", { class: "lead" }, list.length
      ? "The Claude Code sessions on DA Private Coder now, and where each one's last request went: the model named is the one that ran. Claude Code not listed here runs as usual, on Anthropic's models."
      : "No Claude Code runs on DA Private Coder now: any that's open runs as usual, on Anthropic's models."),
    rows);
}

// which folders' Claude Code runs on DA Private Coder in the editor, and every other folder's
function foldersSection(st) {
  if (!st.editors.length) return null;
  const f = st.folders;
  const options = (current) => [["", "Claude as usual"], ...SESSIONS.map((k) => [k, `${KINDS[k].label} models`])]
    .map(([v, l]) => h("option", { value: v, selected: v === current }, l));
  const set = (body, said) => act("POST", "/api/folder", body, said);
  const rows = f.list.map((x) => h("div", { class: "item" },
    h("span", { class: "path", title: x.folder }, h("bdi", { dir: "ltr" }, x.folder)),
    SESSIONS.includes(x.profile) ? chipOf(x.profile) : null,
    h("select", { class: "inline", onchange: (e) => set({ folder: x.folder, profile: e.target.value },
      e.target.value ? "Saved: a new conversation there runs so." : "Saved: Claude Code there runs as usual (in a new conversation).") },
      options(x.profile))));
  const on = st.editors.some((e) => e.state === "on" && e.mode === PER_FOLDER);
  const other = h("div", { class: "model-row" }, h("label", {}, "Any other"),
    h("select", { onchange: (e) => set({ other: e.target.value }, "Saved: every other folder runs so, in a new conversation.") },
      options(f.other)));
  return h("div", { class: "section" }, h("h3", {}, "Folders in your editor"),
    h("p", { class: "lead" }, "Claude Code in a folder chosen here (or a folder inside it) runs on DA Private Coder in VSCodium and VS Code; "
      + "in any other folder, as Any other says: as usual, on Anthropic's models, unless you choose otherwise. Open in VSCodium (Start) adds one. "
      + "A conversation open in a folder whose choice changes stops at its next request: start a new one."),
    f.error ? h("div", { class: "warnbox" }, f.error) : null,
    on ? null : h("div", { class: "small muted" }, "The editor's switch is off, so these don't apply yet (Setup)."),
    rows.length ? h("div", { class: "recent" }, rows) : h("div", { class: "small muted" }, "No folder chosen yet."),
    other, SESSIONS.includes(f.other) ? warningOf(st, f.other) : null);
}

// ------------------------------------------------------------------ web search

function searchSection(st) {
  const tab = S.tab || st.start;
  const p = st.config && st.config.profiles[tab];
  if (!p) return null;
  const pick = (value, said) => act("POST", "/api/profile", { profile: tab, fields: { web_search: value } }, said);
  const radio = (value, title, desc) => h("label", { class: "radio" },
    h("input", { type: "radio", name: "search", checked: p.web_search === value,
      onchange: () => pick(value, "Saved: the next session searches this way.") }),
    h("div", {}, h("div", {}, title), h("div", { class: "small muted" }, desc)));
  return h("div", { class: "section" }, h("h3", {}, `Web search in ${KINDS[tab].label} sessions`),
    h("p", { class: "lead" }, "Claude Code's own search runs on Anthropic's servers, so DA Private Coder gives it one through NanoGPT (with its key). Claude Code asks you before each search, and a search naming this computer or holding a secret is refused."),
    radio("zero-retention", "Keep nothing: Linkup only",
      "Linkup keeps nothing it's sent (zero data retention), and no search goes anywhere else, even when Linkup fails. It finds less. NanoGPT and Linkup still read each search: no search is end-to-end encrypted."),
    radio("best", "Best results: Perplexity and Kagi",
      "Perplexity to learn things, Kagi to find sites. Not zero data retention: NanoGPT and the provider read each search, and what the provider keeps is up to its own policy."),
    radio("off", "No web search", "Claude Code works with what it knows, and pages it's given the address of."),
    p.web_search === "best" && tab === "private" ? h("div", { class: "warnbox" }, h("b", {}, "Your sessions are private; their searches aren't. "),
      "Perplexity and Kagi aren't zero data retention. Choose Keep nothing to search with Linkup, which keeps nothing.") : null);
}

// ------------------------------------------------------------------ the enclaves

function verifySection(st) {
  const a = st.attestation;
  const tee = st.config && st.config.profiles.tee;
  const run = (model) => guarded(async () => {
    S.checking = model || "private"; render();
    try {
      const { attestation } = await api("POST", "/api/check", model ? { model } : {});
      const n = ++S.asked;
      take(n, await api("GET", "/api/state"));
      toast(model ? `${model}: ${attestation.level === "full" ? "verified" : "verified in part"}.` : "The enclave is verified.", "ok", 4000);
    } finally { S.checking = ""; render(); }
  });
  const teeRows = (st.tee_attestations || []).slice(0, 4).map((t) => h("div", { class: "attest" },
    h("div", {}, t.error ? h("span", { class: "chip bad" }, "didn't hold") : h("span", { class: `chip ${t.level === "full" ? "ok" : "tee"}` },
      t.level === "full" ? "verified" : "in part"), " ", h("b", {}, t.model), " ", t.error || t.summary),
    h("div", { class: "small muted" }, `Checked ${t.t ? t.t.slice(0, 16).replace("T", " ") : ""}.`)));
  return h("div", { class: "section" }, h("h3", {}, "The enclaves"),
    h("div", { class: "small muted" }, "Private models"),
    a ? h("div", { class: "attest" },
      h("div", {}, h("span", { class: "chip ok" }, "verified"), " ", a.summary),
      h("div", { class: "small muted" }, `Last checked ${a.t ? a.t.slice(0, 16).replace("T", " ") : ""}. Every session checks it again before it sends anything, and every five minutes.`),
      a.key_sha256 ? h("div", { class: "fingerprint" }, `Its key's fingerprint (sha256): ${a.key_sha256}`) : null)
      : h("p", { class: "lead" }, "Not checked yet here. Every session checks it before it sends anything; you can check it now too (free)."),
    h("div", { class: "row" }, h("button", { disabled: Boolean(S.checking), onclick: () => run("") },
      S.checking === "private" ? "Checking…" : "Check it now"), S.checking === "private" ? h("span", { class: "spinner" }) : null),
    h("div", { class: "small muted spaced" }, "TEE models"),
    teeRows.length ? teeRows : h("p", { class: "lead" }, "None checked yet here. A TEE session checks each model's enclave before it sends it anything, and every 15 minutes; with your NanoGPT key, you can check one now too."),
    tee && /^tee\//i.test(tee.model) ? h("div", { class: "row" }, h("button", { disabled: Boolean(S.checking) || !st.key.stored, onclick: () => run(tee.model),
      title: st.key.stored ? "" : "NanoGPT shows a TEE model's attestation only with your API key" },
      S.checking === tee.model ? "Checking…" : `Check ${tee.model}`), S.checking === tee.model ? h("span", { class: "spinner" }) : null) : null);
}

// ------------------------------------------------------------------ usage

// how each request left this computer (manager.py's "sent"): sealed to the enclave, to an attested TEE
// through NanoGPT, in the clear (a search, a standard session's models), or not at all (refused here first)
const SENT = { sealed: ["private", "sealed"], tee: ["tee", "TEE"], clear: ["warn", "clear"], "": ["", "not sent"] };

function usageSection(st) {
  const u = st.usage;
  const rows = u.last.flatMap((r) => {
    const [chip, how] = SENT[r.sent] || SENT[""];
    const where = r.provider && r.provider !== "nanogpt" ? ` · ${r.provider === "openrouter" ? "OpenRouter" : "your provider"}` : "";
    const what = r.kind === "search"
      ? `web search${r.what ? `, ${r.what}` : ""}${r.results !== null && r.results !== undefined ? ` · ${r.results} results` : ""}`
      : r.what + (r.kind === "vision" ? " (image)" : "") + where;
    const row = h("tr", { class: r.error ? "bad has-note" : "" },
      h("td", {}, r.t.slice(5, 16)),
      h("td", { title: r.asked_for ? `Claude Code asked for ${r.asked_for}` : "" }, what),
      h("td", {}, h("span", { class: `chip ${chip}` }, how)),
      h("td", { class: "num" }, tokens(r.input)),
      h("td", { class: "num" }, tokens(r.cached)),
      h("td", { class: "num" }, tokens(r.output)),
      h("td", { class: "num", title: r.estimated ? "estimated from the provider's listed prices" : "" },
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
      clearInterval(looking);
      document.body.replaceChildren(h("p", { class: "lead" }, "DA Private Coder has quit, so the sessions on it stop at "
        + "their next request (open it again to go on). Claude Code as usual isn't affected."));
    }) }, "Quit")));
}

// ------------------------------------------------------------------ the page

function render() {
  const st = S.state;
  if (!st) return;
  applyTheme(st.theme);
  renderTop(st);
  const scroll = $("#page").scrollTop;
  $("#page").replaceChildren(...[startSection(st), sessionsSection(st), setupSection(st), foldersSection(st),
    modelsSection(st), searchSection(st), verifySection(st), usageSection(st), appSection(st)].flat().filter(Boolean));
  $("#page").scrollTop = scroll;
}

async function refresh() {
  const n = ++S.asked;
  take(n, await api("GET", "/api/state"));
}

// a profile's list of models (the one shown when not said)
async function loadModels(profile, fresh = false) {
  profile = profile || S.tab || (S.state && S.state.start) || "private";
  try {
    S.models[profile] = (await api("GET", `/api/models?profile=${encodeURIComponent(profile)}${fresh ? "&fresh=1" : ""}`)).models;
    S.modelsError[profile] = "";
  } catch (e) {
    S.models[profile] = [];
    S.modelsError[profile] = e.message;
  }
  render();
}

$("#refresh-btn").addEventListener("click", () => guarded(async () => { S.hosts = {}; await refresh(); await loadModels("", true); }));
// whether the user is in the middle of something the page would lose if drawn again: a field or a
// list with the focus, or a key typed and not yet saved
function editing() {
  const a = document.activeElement;
  if (a && $("#page").contains(a) && a.matches("input, select, textarea")) return true;
  return [...document.querySelectorAll("#page input[type=password]")].some((i) => i.value);
}

// usage and the editor's settings can change outside the window: looked at again now and then
const looking = setInterval(() => { if (!document.hidden && !editing()) guarded(refresh); }, 20000);
guarded(async () => { await refresh(); await loadModels(); });
