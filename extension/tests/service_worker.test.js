"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const workerPath = path.join(__dirname, "..", "service_worker.js");
const TAB_LOCK = "Tabs cannot be edited right now (user may be dragging a tab).";
const realSetTimeout = global.setTimeout;

let session, tabs, created, windowUpdates, script, permitted, fetchLog, fetchImpl, removed;

function event() {
  const listeners = new Set();
  return { addListener: l => listeners.add(l), removeListener: l => listeners.delete(l), listeners, fire: (...a) => [...listeners].forEach(l => l(...a)) };
}

function setup({ stored = {}, openTabs = [] } = {}) {
  session = structuredClone(stored); created = 0; windowUpdates = []; permitted = false; fetchLog = []; removed = [];
  tabs = new Map(openTabs.map(t => [t.id, structuredClone(t)]));
  script = async () => [{ result: true }];
  fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({ action: null }) });
  const updated = event();
  global.chrome = {
    storage: { session: { async get(k) { return { [k]: structuredClone(session[k]) }; }, async set(v) { Object.assign(session, structuredClone(v)); } } },
    tabs: {
      async get(id) { if (!tabs.has(id)) throw new Error(`No tab with id: ${id}`); return structuredClone(tabs.get(id)); },
      async query() { return [...tabs.values()].map(t => structuredClone(t)); },
      async update(id, change) { if (!tabs.has(id)) throw new Error(`No tab with id: ${id}`); const n = { windowId: 1, ...tabs.get(id), ...change, status: "complete" }; tabs.set(id, n); return structuredClone(n); },
      async create(props) { created += 1; const t = { id: 100 + created, windowId: 1, status: "complete", ...props }; tabs.set(t.id, t); return structuredClone(t); },
      async remove(id) { removed.push(id); tabs.delete(id); },
      async goBack() {}, async goForward() {},
      onUpdated: updated, onRemoved: event(),
    },
    windows: { async update(id, c) { windowUpdates.push([id, c]); return { id }; } },
    scripting: { async executeScript(args) { return script(args); } },
    permissions: { async contains() { return permitted; } },
    alarms: { create() {}, onAlarm: event() }, runtime: { onInstalled: event(), onStartup: event() },
  };
  global.fetch = (...a) => { fetchLog.push(a); return fetchImpl(...a); };
  delete require.cache[workerPath];
  return require(workerPath);
}
const runPage = async args => [{ result: await args.func(...JSON.parse(JSON.stringify(args.args ?? []))) }];   // real Chrome JSON-serialises args
const yt = id => ({ id, url: "https://www.youtube.com/watch?v=one", windowId: 1, status: "complete" });
function fastSleeps() {   // make the 1 s/2 s retry sleeps instant without touching the longer timeout guards
  global.setTimeout = (fn, ms, ...a) => realSetTimeout(fn, ms === 1000 || ms === 2000 ? 0 : ms, ...a);
  return () => { global.setTimeout = realSetTimeout; };
}
function withDom(video, extra = {}) {
  const previous = { document: global.document, location: global.location };
  global.document = { querySelector: s => (/video/.test(s) && !/ytp|ytd/.test(s) ? video : null), getElementById: () => extra.player || null, ...extra.doc };
  if (extra.location) global.location = extra.location;
  return () => { global.document = previous.document; global.location = previous.location; };
}

// ------------------------------------------------------------------ hosts, tabs, recovery
test("normalizedHost groups www/non-www and rejects non-web URLs", () => {
  const w = setup();
  assert.equal(w.normalizedHost("https://www.youtube.com/watch?v=1"), "youtube.com");
  assert.equal(w.normalizedHost("https://youtube.com/results"), "youtube.com");
  assert.equal(w.normalizedHost("file:///C:/x.txt"), null);
  assert.equal(w.normalizedHost("not a url"), null);
});

test("one tab per website: concurrent requests share a single new tab, later requests reuse it", async () => {
  const w = setup();
  const ids = await Promise.all([1, 2, 3, 4, 5].map(i => w.findOrCreateTab(`https://github.com/page${i}`)));
  assert.equal(new Set(ids).size, 1);
  assert.equal(created, 1);
  assert.equal(await w.findOrCreateTab("https://www.github.com/issues"), ids[0]);
  assert.equal(created, 1);
  assert.equal(session.cozyTabs["github.com"], ids[0]);
});

test("the tab map lives in session storage so ids from a previous browser run are never trusted", async () => {
  const w = setup({ stored: { cozyTabs: { "github.com": 999 } }, openTabs: [{ id: 22, url: "https://github.com/", windowId: 1, status: "complete" }] });
  assert.equal(await w.findOrCreateTab("https://github.com/pulls"), 22);
  assert.equal(created, 0);
  assert.equal(session.cozyTabs["github.com"], 22);
});

test("a stored tab that closes between lookup and update is replaced once, with no 'kept closing' failure", async () => {
  const w = setup({ stored: { cozyTabs: { "youtube.com": 5 } }, openTabs: [yt(5)] });
  const realGet = chrome.tabs.get;
  chrome.tabs.get = async id => { const t = await realGet(id); tabs.delete(5); return t; };
  assert.equal(await w.findOrCreateTab("https://www.youtube.com/"), 101);
  assert.equal(created, 1);
});

test("when the first candidate vanishes the next matching tab is reused", async () => {
  const w = setup({ stored: { cozyTabs: { "youtube.com": 5 } }, openTabs: [yt(5), yt(6)] });
  const realUpdate = chrome.tabs.update;
  chrome.tabs.update = async (id, c) => { if (id === 5) { tabs.delete(5); throw new Error("No tab with id: 5"); } return realUpdate(id, c); };
  assert.equal(await w.findOrCreateTab("https://www.youtube.com/"), 6);
  assert.equal(created, 0);
});

test("a temporary tab-edit lock while creating is retried and creates exactly one tab", async () => {
  const w = setup();
  const real = chrome.tabs.create;
  let tries = 0;
  chrome.tabs.create = async (...a) => { tries += 1; if (tries < 3) throw new Error(TAB_LOCK); return real(...a); };
  assert.equal(await w.findOrCreateTab("https://github.com/"), 101);
  assert.equal(tries, 3);
  assert.equal(created, 1);
});

test("a persistent tab-edit lock never touches another tab or opens a duplicate", async () => {
  const w = setup({ openTabs: [yt(43), yt(44)] });
  const touched = [];
  chrome.tabs.update = async id => { touched.push(id); throw new Error(TAB_LOCK); };
  await assert.rejects(w.findOrCreateTab("https://www.youtube.com/"), e => /Release the tab and try again/i.test(e.message) && !/kept closing/i.test(e.message));
  assert.deepEqual([...new Set(touched)], [43]);
  assert.equal(created, 0);
});

test("opening in a new tab creates one even when the site already has a tab", async () => {
  const w = setup({ openTabs: [{ id: 22, url: "https://github.com/", windowId: 1, status: "complete" }] });
  const id = await w.findOrCreateTab("https://github.com/new", { newTab: true });
  assert.equal(created, 1);
  assert.notEqual(id, 22);
});

test("waitForTabReady catches a load that finishes between listener registration and the status check", async () => {
  const w = setup({ openTabs: [{ id: 7, url: "https://a.com/", status: "loading", windowId: 1 }] });
  const realGet = chrome.tabs.get;
  chrome.tabs.get = async id => { chrome.tabs.onUpdated.fire(id, { status: "complete" }); return { ...(await realGet(id)), status: "loading" }; };
  await w.waitForTabReady(7, 200);
  assert.equal(chrome.tabs.onUpdated.listeners.size, 0);
  chrome.tabs.get = async () => { throw new Error("No tab with id"); };
  await assert.rejects(w.waitForTabReady(8, 200), /closed before loading finished/i);
  assert.equal(chrome.tabs.onUpdated.listeners.size, 0);
});

// ------------------------------------------------------------------ YouTube
test("pageClickFirstResult only clicks the results page of THIS search (stale results are never clicked)", () => {
  const w = setup();
  const clicks = [];
  const link = { href: "https://www.youtube.com/watch?v=x", click: () => clicks.push(1) };
  const restore = withDom(null, { doc: { querySelector: s => (s.includes("ytd-video-renderer a#video-title") ? link : null) }, location: { pathname: "/results", search: "?search_query=old+song" } });
  try {
    assert.match(w.pageClickFirstResult("new song"), /different search/);
    assert.equal(clicks.length, 0);
    global.location = { pathname: "/watch", search: "?v=1" };
    assert.match(w.pageClickFirstResult("new song"), /not showing search results/);
    global.location = { pathname: "/results", search: "?search_query=NEW++song" };
    assert.equal(w.pageClickFirstResult("new song"), true);
    assert.equal(clicks.length, 1);
    global.document.querySelector = () => null;
    assert.match(w.pageClickFirstResult(null), /no playable video link/);
  } finally { restore(); }
});

test("clickFirstYouTubeResult waits out a stale page, then clicks and reports the watch page", async () => {
  const w = setup({ openTabs: [{ id: 72, url: "https://www.youtube.com/results?search_query=new+song", windowId: 1, status: "complete" }] });
  const unfast = fastSleeps();
  let reads = 0;
  const current = () => (reads < 2 ? "?search_query=old+song" : "?search_query=new+song");   // the page finishes rendering after the first check
  const clicked = [];
  const link = { href: "https://www.youtube.com/watch?v=x", click() { clicked.push(current()); tabs.set(72, { ...tabs.get(72), url: "https://www.youtube.com/watch?v=x" }); } };
  const restore = withDom(null, { doc: { querySelector: () => link }, location: { get pathname() { return "/results"; }, get search() { reads += 1; return current(); } } });
  script = runPage;
  try {
    assert.equal(await w.clickFirstYouTubeResult(72, "https://www.youtube.com/results?search_query=new+song"), 72);
    assert.deepEqual(clicked, ["?search_query=new+song"], "only the requested search may be clicked");
    assert.ok(reads >= 2, "the stale first check must have been rejected");
  } finally { restore(); unfast(); }
});

test("a final failure says what the tab was showing", async () => {
  const w = setup({ openTabs: [{ id: 72, url: "https://consent.youtube.com/", windowId: 1, status: "complete" }] });
  const unfast = fastSleeps();
  script = async () => [{ result: "the tab is not showing search results yet" }];
  try {
    await assert.rejects(w.clickFirstYouTubeResult(72, "https://www.youtube.com/results?search_query=x"), /Last check: the tab is not showing search results yet/);
  } finally { unfast(); }
});

test("a YouTube tab that closes before playback starts fails the action instead of re-opening the search page", async () => {
  const w = setup({ stored: { cozyTabs: { "youtube.com": 7 } }, openTabs: [yt(7)] });
  script = async () => { tabs.delete(7); throw new Error("No tab with id: 7"); };
  await assert.rejects(w.startYouTubePlayback(7), /closed before playback started/i);
  assert.equal(created, 0);
  assert.equal(session.cozyTabs["youtube.com"], undefined);
});

// ------------------------------------------------------------------ media controls
test("generic forward/rewind work when executeScript JSON-serialises omitted arguments", async () => {
  const w = setup({ stored: { cozyTabs: { "youtube.com": 73 } }, openTabs: [yt(73)] });
  const video = { currentTime: 30, duration: 600, volume: 0.5, muted: false, playbackRate: 1 };
  const restore = withDom(video);
  script = runPage;
  try {
    await w.executeMedia("forward");
    assert.equal(video.currentTime, 40);
    await w.executeMedia("rewind");
    assert.equal(video.currentTime, 30);
    await w.executeMedia("forward", 60);
    assert.equal(video.currentTime, 90);
    await w.executeMedia("seek_to", 0);
    assert.equal(video.currentTime, 0);
    await assert.rejects(w.executeMedia("seek_to"), /number is required/i);
    await assert.rejects(w.executeMedia("seek_to", 90000), /between 0 seconds and 24 hours/);
    await assert.rejects(w.executeMedia("forward", 0), /between 1 second/);
  } finally { restore(); }
});

test("seek clamps to the video length and detects a seek that did not happen", async () => {
  const w = setup();
  const video = { currentTime: 10, duration: 100 };
  const restore = withDom(video);
  try {
    assert.deepEqual(await w.pageMedia("seek_to", 500, null), { position: 100 });
    assert.equal(video.currentTime, 100);
    const stuck = { duration: 100, get currentTime() { return 5; }, set currentTime(_v) {} };
    global.document.querySelector = () => stuck;
    await assert.rejects(w.pageMedia("seek_to", 60, null), /could not move/);
  } finally { restore(); }
});

test("video volume uses YouTube's player API, verifies it, and validates the range", async () => {
  const w = setup();
  const video = { volume: 0.5, muted: false };
  let level = 50;
  const player = { setVolume: n => { level = n; }, getVolume: () => level, unMute() {}, mute() {} };
  const restore = withDom(video, { player });
  try {
    assert.deepEqual(await w.pageMedia("set_volume", 25, null), { volume: 25 });
    assert.equal(level, 25);
    assert.deepEqual(await w.pageMedia("volume_up", null, null), { volume: 35 });
    assert.deepEqual(await w.pageMedia("volume_down", null, null), { volume: 25 });
    await assert.rejects(w.pageMedia("set_volume", 101, null), /0 to 100/);
    await assert.rejects(w.pageMedia("set_volume", 2.5, null), /whole number/);
    player.setVolume = () => {};                                      // YouTube ignores the request
    await assert.rejects(w.pageMedia("set_volume", 70, null), /did not accept/);
  } finally { restore(); }
});

test("volume falls back to the video element when the player API is missing", async () => {
  const w = setup();
  const video = { volume: 0.5, muted: true };
  const restore = withDom(video);
  try {
    assert.deepEqual(await w.pageMedia("set_volume", 40, null), { volume: 40 });
    assert.equal(video.volume, 0.4);
    assert.equal(video.muted, false);
  } finally { restore(); }
});

test("playback speed: absolute value, relative step, clamped", async () => {
  const w = setup();
  const video = { playbackRate: 1 };
  const restore = withDom(video);
  try {
    assert.deepEqual(await w.pageMedia("speed", 1.5, null), { speed: 1.5 });
    assert.deepEqual(await w.pageMedia("speed", null, 0.25), { speed: 1.75 });
    assert.deepEqual(await w.pageMedia("speed", null, 0.5), { speed: 2 });
    assert.deepEqual(await w.pageMedia("speed", null, -0.25), { speed: 1.75 });
  } finally { restore(); }
});

test("missing video, missing ad button and unknown actions give clear errors", async () => {
  const w = setup();
  let restore = withDom(null);
  try { await assert.rejects(w.pageMedia("pause", null, null), /No video is present/); } finally { restore(); }
  restore = withDom({ pause() {}, paused: false });
  try {
    await assert.rejects(w.pageMedia("skip_ad", null, null), /no skippable ad/);
    await assert.rejects(w.pageMedia("explode", null, null), /Unsupported media action/);
  } finally { restore(); }
});

test("media commands report a clear error when no YouTube tab exists", async () => {
  const w = setup({ openTabs: [{ id: 1, url: "https://github.com/", windowId: 1, status: "complete" }] });
  await assert.rejects(w.executeMedia("pause"), /No YouTube tab is open/);
});

// ------------------------------------------------------------------ tabs, pages, permissions
test("tab operations: list exposes hosts only, switch reports misses, scroll/read need the optional permission", async () => {
  const w = setup({ openTabs: [
    { id: 1, url: "https://mail.google.com/mail/u/0/#inbox?token=secret", title: "Inbox", active: true, windowId: 1, status: "complete" },
    { id: 2, url: "https://github.com/zoya", title: "GitHub", windowId: 1, status: "complete" }] });
  const list = await w.tabOperation({ op: "list" });
  assert.deepEqual(list, [{ title: "Inbox", host: "mail.google.com" }, { title: "GitHub", host: "github.com" }]);
  await w.tabOperation({ op: "switch", query: "github" });
  assert.equal(tabs.get(2).active, true);
  await assert.rejects(w.tabOperation({ op: "switch", query: "zzz" }), /couldn't find an open tab/);
  await assert.rejects(w.tabOperation({ op: "scroll", direction: "down" }), /Enable page reading/);
  await assert.rejects(w.readPage(), /Enable page reading/);
  permitted = true;
  chrome.tabs.query = async () => [{ id: 2, url: "https://github.com/zoya", windowId: 1 }];
  script = async () => [{ result: { title: "Z", text: "hello" } }];
  assert.deepEqual(await w.readPage(), { title: "Z", text: "hello" });
  await w.tabOperation({ op: "close" });
  assert.deepEqual(removed, [2]);
});

test("closing a tab retries the temporary edit lock", async () => {
  const w = setup({ openTabs: [{ id: 3, url: "https://a.com/", active: true, windowId: 1, status: "complete" }] });
  let n = 0;
  const real = chrome.tabs.remove;
  chrome.tabs.remove = async id => { n += 1; if (n < 3) throw new Error(TAB_LOCK); return real(id); };
  await w.tabOperation({ op: "close" });
  assert.equal(n, 3);
});

test("page text extraction prefers the article, falls back to the body, and is capped", () => {
  const w = setup();
  const long = "word ".repeat(5000);
  let restore = withDom(null, { doc: { title: "T", querySelector: sel => (sel === "article" ? { innerText: long } : null), body: { innerText: "fallback body" } } });
  try {
    const out = w.pageText();
    assert.equal(out.title, "T");
    assert.equal(out.text.length, 12000);
  } finally { restore(); }
  restore = withDom(null, { doc: { title: "Short", querySelector: sel => (sel === "article" ? { innerText: "tiny" } : null), body: { innerText: "the   whole\n page text" } } });
  try { assert.equal(w.pageText().text, "the whole page text"); } finally { restore(); }
});

// ------------------------------------------------------------------ protocol with the backend
test("handleAction acknowledges success with data and failure with the reason", async () => {
  const w = setup({ stored: { cozyTabs: { "youtube.com": 73 } }, openTabs: [yt(73)] });
  const video = { currentTime: 1, duration: 100 };
  const restore = withDom(video);
  script = runPage;
  try {
    await w.handleAction({ id: "a1", type: "media", action: "seek_to", value: 50 });
    let [url, opts] = fetchLog.at(-1);
    assert.equal(url, "http://127.0.0.1:8000/api/ext/ack/a1");
    assert.deepEqual(JSON.parse(opts.body), { ok: true, detail: "", data: { position: 50 } });
    assert.equal(opts.headers["X-Cozy-Client"], "extension");
    await w.handleAction({ id: "a2", type: "teleport" });
    [url, opts] = fetchLog.at(-1);
    assert.deepEqual(JSON.parse(opts.body), { ok: false, detail: "Unknown action type: teleport", data: null });
  } finally { restore(); }
});

test("the poll loop is one long-poll request per round (no timer-driven polling), acks work, and failures back off", async () => {
  const w = setup();
  const unfast = fastSleeps();
  let n = 0;
  fetchImpl = async (url) => {
    n += 1;
    if (url.includes("/api/ext/next")) {
      if (n === 1) return { ok: true, status: 200, json: async () => ({ action: { id: "x1", type: "tab", op: "list" } }) };
      if (n === 3) throw new Error("backend down");
      return { ok: true, status: 200, json: async () => ({ action: null }) };
    }
    return { ok: true, status: 200 };
  };
  try {
    await w.pollLoop(3);
  } finally { unfast(); }
  const polls = fetchLog.filter(([u]) => u.includes("/api/ext/next"));
  assert.equal(polls.length, 3);
  assert.match(polls[0][0], /wait=25&v=2\.0\.0$/);
  assert.equal(polls[0][1].headers["X-Cozy-Client"], "extension");
  assert.ok(fetchLog.some(([u]) => u.endsWith("/api/ext/ack/x1")));
});

test("only one poll loop can run at a time", async () => {
  const w = setup();
  let release;
  fetchImpl = () => new Promise(resolve => { release = () => resolve({ ok: true, status: 200, json: async () => ({ action: null }) }); });
  const first = w.pollLoop(1);
  await new Promise(r => realSetTimeout(r, 10));
  await w.pollLoop(1);                                   // returns immediately: a loop is already running
  assert.equal(fetchLog.length, 1);
  release();
  await first;
});

test("manifest: version matches the worker, page access is optional, and nothing is requested for all sites up front", () => {
  const w = setup();
  const manifest = require(path.join(__dirname, "..", "manifest.json"));
  assert.equal(manifest.version, w.WORKER_VERSION);
  assert.ok(!manifest.host_permissions.some(p => p === "<all_urls>" || p === "*://*/*"));
  assert.deepEqual(manifest.optional_host_permissions, ["<all_urls>"]);
  assert.equal(manifest.manifest_version, 3);
  assert.ok(manifest.options_ui.page);
});
