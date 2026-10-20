"use strict";
// Cozy Tab Controller (Manifest V3). Keeps ONE long-poll request open to the local Cozy backend:
// the backend answers the instant a command exists, so there is no polling loop to burn battery.

const WORKER_VERSION = "2.0.0"; // keep in sync with manifest.json (a test enforces it)
const API = "http://127.0.0.1:8000";
const POLL_WAIT_S = 25;
const HEADERS = { "X-Cozy-Client": "extension" };
const TAB_EDIT_RETRY_DELAYS_MS = [150, 300, 600];
const PAGE_ORIGINS = { origins: ["<all_urls>"] };

const hostLocks = new Map();
let mapLock = Promise.resolve();
let polling = false;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// ------------------------------------------------------------------ helpers
function normalizedHost(rawUrl) {
  try {
    const u = new URL(rawUrl);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return u.hostname.toLowerCase().replace(/^www\./, "");
  } catch { return null; }
}
const isTemporaryTabEditError = e => /tabs cannot be edited right now/i.test(String(e?.message || e));
const isStaleTabError = e => /no tab with id|tab.*not found/i.test(String(e?.message || e));

// Chrome rejects tab-strip changes while the user drags a tab. Retry briefly, then report; never open a duplicate.
async function withTabEditRetry(operation) {
  let last;
  for (let attempt = 0; attempt <= TAB_EDIT_RETRY_DELAYS_MS.length; attempt++) {
    try { return await operation(); } catch (error) {
      last = error;
      if (!isTemporaryTabEditError(error)) throw error;
      if (attempt === TAB_EDIT_RETRY_DELAYS_MS.length) break;
      await sleep(TAB_EDIT_RETRY_DELAYS_MS[attempt]);
    }
  }
  throw new Error("Chrome is temporarily blocking tab changes, usually while a tab is being dragged. Release the tab and try again. " +
    String(last?.message || last || ""));
}
const updateTab = (id, change) => withTabEditRetry(() => chrome.tabs.update(id, change));
const createTab = props => withTabEditRetry(() => chrome.tabs.create(props));

async function withHostLock(host, operation) {
  const previous = hostLocks.get(host) || Promise.resolve();
  let release;
  const turn = new Promise(resolve => { release = resolve; });
  const queued = previous.then(() => turn);
  hostLocks.set(host, queued);
  await previous;
  try { return await operation(); } finally {
    release();
    if (hostLocks.get(host) === queued) hostLocks.delete(host);
  }
}

// Tab ids only mean something until the browser restarts, so the host -> tab map lives in session storage.
async function updateTabMap(mutator) {
  const previous = mapLock;
  let release;
  mapLock = new Promise(resolve => { release = resolve; });
  await previous;
  try {
    const { cozyTabs = {} } = await chrome.storage.session.get("cozyTabs");
    mutator(cozyTabs);
    await chrome.storage.session.set({ cozyTabs });
  } finally { release(); }
}
const forgetHost = (host, tabId) => updateTabMap(m => { if (m[host] === tabId) delete m[host]; });

async function candidateTabIds(host) {
  const ids = [];
  const { cozyTabs = {} } = await chrome.storage.session.get("cozyTabs");
  const mapped = cozyTabs[host];
  if (mapped !== undefined) {
    let tab = null;
    try { tab = await chrome.tabs.get(mapped); } catch { /* stale */ }
    if (tab && normalizedHost(tab.url || tab.pendingUrl || "") === host) ids.push(mapped);
    else await forgetHost(host, mapped);
  }
  for (const tab of await chrome.tabs.query({})) {
    if (tab.id !== undefined && !ids.includes(tab.id) && normalizedHost(tab.url || tab.pendingUrl || "") === host) ids.push(tab.id);
  }
  return ids;
}

// null => the tab is gone. Anything else (including a persistent edit lock) is thrown.
async function navigateExistingTab(tabId, url) {
  try { return await updateTab(tabId, { url, active: true }); } catch (error) {
    if (isStaleTabError(error)) return null;
    throw error;
  }
}

async function focusTabWindow(tab) {
  try { await chrome.windows.update(tab.windowId, { focused: true }); } catch (error) {
    throw new Error(`Chrome could not focus the tab's window: ${error.message || error}`);
  }
}

async function waitForTabReady(tabId, timeoutMs = 12000) {
  await new Promise((resolve, reject) => {
    let done = false, timer = null;
    const finish = fn => value => { if (done) return; done = true; clearTimeout(timer); chrome.tabs.onUpdated.removeListener(listener); fn(value); };
    const ok = finish(resolve), fail = finish(reject);
    const listener = (id, info) => { if (id === tabId && info.status === "complete") ok(); };
    chrome.tabs.onUpdated.addListener(listener);
    timer = setTimeout(() => fail(new Error("The tab did not finish loading in time.")), timeoutMs);
    chrome.tabs.get(tabId).then(tab => { if (tab.status === "complete") ok(); }, () => fail(new Error("The tab was closed before loading finished.")));
  });
}

// One tab per website. Finite candidate list: no retry loop that can spin on a closing tab.
async function findOrCreateTab(url, { newTab = false } = {}) {
  const host = normalizedHost(url);
  if (!host) throw new Error("Cozy can only open http/https URLs.");
  return withHostLock(host, async () => {
    if (!newTab) {
      for (let round = 0; round < 3; round++) {
        const candidates = await candidateTabIds(host);
        if (candidates.length === 0) break;
        for (const tabId of candidates) {
          const updated = await navigateExistingTab(tabId, url);
          if (updated === null) { await forgetHost(host, tabId); continue; }
          await updateTabMap(m => { m[host] = updated.id; });
          await focusTabWindow(updated);
          await waitForTabReady(updated.id);
          return updated.id;
        }
      }
    }
    const created = await createTab({ url, active: true });
    await updateTabMap(m => { m[host] = created.id; });
    await focusTabWindow(created);
    await waitForTabReady(created.id);
    return created.id;
  });
}

// ------------------------------------------------------------------ YouTube search + playback
const youTubeSearchQuery = raw => { try { return new URL(raw).searchParams.get("search_query"); } catch { return null; } };
const isWatchUrl = u => { try { const x = new URL(u); return /(^|\.)youtube\.com$/.test(x.hostname) && x.pathname === "/watch" && x.searchParams.has("v"); } catch { return false; } };

// Runs inside the page. Several selectors because YouTube keeps changing its result markup.
function pageClickFirstResult(expected) {
  const normalize = v => String(v ?? "").trim().replace(/\s+/g, " ").toLowerCase();
  if (location.pathname !== "/results") return "the tab is not showing search results yet";
  if (expected !== null && normalize(new URLSearchParams(location.search).get("search_query")) !== normalize(expected)) {
    return "the tab is still showing results for a different search";
  }
  const selectors = ["ytd-video-renderer a#video-title", "ytd-video-renderer a#thumbnail",
    "ytd-search yt-lockup-view-model a[href*='/watch?v=']", "ytd-search a#video-title"];
  for (const selector of selectors) {
    const link = document.querySelector(selector);
    if (link && link.href && link.href.includes("/watch")) { link.click(); return true; }
  }
  return "the result list has no playable video link yet";
}

async function waitForYouTubeWatchUrl(tabId, timeoutMs = 15000) {
  await new Promise((resolve, reject) => {
    let done = false, timer = null;
    const finish = fn => value => { if (done) return; done = true; clearTimeout(timer); chrome.tabs.onUpdated.removeListener(listener); fn(value); };
    const ok = finish(resolve), fail = finish(reject);
    const listener = (id, info, tab) => {
      if (id !== tabId) return;
      const url = info?.url || tab?.url || "";
      if (isWatchUrl(url) && (info.status === "complete" || info.status === undefined)) ok();
    };
    chrome.tabs.onUpdated.addListener(listener);
    timer = setTimeout(() => fail(new Error("YouTube did not open the selected video in time.")), timeoutMs);
    chrome.tabs.get(tabId).then(tab => { if (isWatchUrl(tab.url || "") && tab.status === "complete") ok(); }, () => fail(new Error("The YouTube tab was closed.")));
  });
}

async function clickFirstYouTubeResult(initialTabId, url) {
  let tabId = initialTabId;
  const expected = youTubeSearchQuery(url);
  let lastReason = "";
  for (let attempt = 0; attempt < 6; attempt++) {
    try {
      const result = await chrome.scripting.executeScript({ target: { tabId }, args: [expected], func: pageClickFirstResult });
      const outcome = result?.[0]?.result;
      if (outcome === true) { await waitForYouTubeWatchUrl(tabId); return tabId; }
      if (typeof outcome === "string") lastReason = outcome;
    } catch (error) {
      if (isStaleTabError(error)) { await forgetHost("youtube.com", tabId); tabId = await findOrCreateTab(url); }
    }
    await sleep(1000);
  }
  throw new Error("YouTube did not expose a playable search result after tab recovery." + (lastReason ? ` Last check: ${lastReason}.` : ""));
}

function pagePlay() {
  const video = document.querySelector("video.html5-main-video") || document.querySelector("video");
  if (!video) return "loading";
  return video.play().then(() => (video.paused ? "paused" : "playing"), e => `blocked:${e && e.name}`);
}

async function startYouTubePlayback(tabId) {
  for (let attempt = 0; attempt < 8; attempt++) {
    try {
      const result = await chrome.scripting.executeScript({ target: { tabId }, func: pagePlay });
      if (result?.[0]?.result === "playing") return;
    } catch (error) {
      if (isStaleTabError(error)) {
        // Re-opening the search page would play nothing; fail so the backend re-delivers the whole action.
        await forgetHost("youtube.com", tabId);
        throw new Error("The YouTube tab closed before playback started.");
      }
      if (attempt === 7) throw new Error(`YouTube playback could not start: ${error.message || error}`);
    }
    await sleep(1000);
  }
  throw new Error("YouTube opened the video, but Chrome blocked automatic playback.");
}

async function playYouTube(action) {
  let tabId = await findOrCreateTab(action.url);
  tabId = await clickFirstYouTubeResult(tabId, action.url);
  await startYouTubePlayback(tabId);
  return await pageInfoSafe(tabId);
}

// ------------------------------------------------------------------ media controls (run inside the page; no closures!)
async function pageMedia(a, amount, step) {
  const v = document.querySelector("video.html5-main-video") || document.querySelector("video");
  if (!v) throw new Error("No video is present on this page.");
  const player = document.getElementById("movie_player");
  // executeScript serialises args as JSON, so an omitted amount arrives as null, not undefined.
  const has = amount !== null && amount !== undefined;
  const number = () => { if (!Number.isFinite(amount)) throw new Error("A number is required for this action."); return amount; };
  const setVolume = n => {
    if (player && typeof player.setVolume === "function") {
      player.setVolume(n);
      if (n > 0 && typeof player.unMute === "function") player.unMute();
      if (typeof player.getVolume === "function" && Math.abs(player.getVolume() - n) > 1) throw new Error("YouTube did not accept the new volume.");
    } else {
      v.volume = n / 100; v.muted = false;
      if (Math.abs(Math.round(v.volume * 100) - n) > 1) throw new Error("The video did not accept the new volume.");
    }
    return { volume: n };
  };
  const currentVolume = () => (player && typeof player.getVolume === "function" ? player.getVolume() : Math.round(v.volume * 100));
  switch (a) {
    case "pause": v.pause(); return {};
    case "play": await v.play(); return {};
    case "toggle": if (v.paused) await v.play(); else v.pause(); return {};
    case "mute": if (player && player.mute) player.mute(); else v.muted = true; return {};
    case "unmute": if (player && player.unMute) player.unMute(); else v.muted = false; return {};
    case "volume_up": return setVolume(Math.min(100, currentVolume() + 10));
    case "volume_down": return setVolume(Math.max(0, currentVolume() - 10));
    case "set_volume": {
      const n = number();
      if (!Number.isInteger(n) || n < 0 || n > 100) throw new Error("Volume must be a whole number from 0 to 100.");
      return setVolume(n);
    }
    case "seek_to": {
      const n = number();
      if (n < 0 || n > 86400) throw new Error("The timestamp must be between 0 seconds and 24 hours.");
      const dest = Number.isFinite(v.duration) ? Math.min(n, v.duration) : n;
      v.currentTime = dest;
      if (Math.abs(v.currentTime - dest) > 1.5) throw new Error("The video could not move to that time.");
      return { position: Math.round(dest) };
    }
    case "forward": case "rewind": {
      if (has && (!Number.isFinite(amount) || amount < 1 || amount > 86400)) throw new Error("The seek duration must be between 1 second and 24 hours.");
      const delta = has ? amount : 10;
      const dest = a === "forward" ? Math.min(Number.isFinite(v.duration) ? v.duration : Infinity, v.currentTime + delta) : Math.max(0, v.currentTime - delta);
      v.currentTime = dest;
      return { position: Math.round(dest) };
    }
    case "speed": {
      const current = v.playbackRate || 1;
      const rate = Math.min(2, Math.max(0.25, has ? amount : current + (Number.isFinite(step) ? step : 0)));
      v.playbackRate = rate;
      if (player && typeof player.setPlaybackRate === "function") player.setPlaybackRate(rate);
      return { speed: rate };
    }
    case "fullscreen":
      if (document.fullscreenElement) await document.exitFullscreen(); else await (player || v).requestFullscreen();
      return {};
    case "next": { const b = document.querySelector(".ytp-next-button"); if (!b) throw new Error("There is no next video."); b.click(); return {}; }
    case "previous": { const b = document.querySelector(".ytp-prev-button"); if (!b) throw new Error("There is no previous video."); b.click(); return {}; }
    case "skip_ad": {
      const b = document.querySelector(".ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern");
      if (!b) throw new Error("There is no skippable ad right now."); b.click(); return {};
    }
    default: throw new Error(`Unsupported media action: ${a}`);
  }
}

function pageNowPlaying() {
  const v = document.querySelector("video.html5-main-video") || document.querySelector("video");
  const title = (document.querySelector("h1.ytd-watch-metadata yt-formatted-string")?.textContent || document.title || "").replace(/ - YouTube$/, "").trim();
  const channel = (document.querySelector("ytd-video-owner-renderer ytd-channel-name a, #owner #text a, ytd-channel-name a")?.textContent || "").trim();
  return { title, channel, position: v ? Math.round(v.currentTime) : null, duration: v && Number.isFinite(v.duration) ? Math.round(v.duration) : null, paused: v ? v.paused : null };
}

function pageText() {
  const root = document.querySelector("article") || document.querySelector("main") || document.body;
  let text = (root?.innerText || "").replace(/\s+/g, " ").trim();
  if (text.length < 500) text = (document.body?.innerText || "").replace(/\s+/g, " ").trim();
  return { title: document.title, text: text.slice(0, 12000) };
}

function pageScroll(direction) {
  const h = Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0);
  const step = Math.round(window.innerHeight * 0.8);
  if (direction === "top") window.scrollTo({ top: 0 });
  else if (direction === "bottom") window.scrollTo({ top: h });
  else window.scrollBy({ top: direction === "up" ? -step : step });
  return {};
}

async function mediaTab() {
  const { cozyTabs = {} } = await chrome.storage.session.get("cozyTabs");
  const mapped = cozyTabs["youtube.com"];
  if (mapped !== undefined) {
    try { const tab = await chrome.tabs.get(mapped); if (normalizedHost(tab.url || "") === "youtube.com") return tab.id; } catch { await forgetHost("youtube.com", mapped); }
  }
  const tabs = (await chrome.tabs.query({})).filter(t => normalizedHost(t.url || "") === "youtube.com");
  const pick = tabs.find(t => t.audible) || tabs.find(t => isWatchUrl(t.url || "")) || tabs[0];
  if (!pick) throw new Error("No YouTube tab is open.");
  await updateTabMap(m => { m["youtube.com"] = pick.id; });
  return pick.id;
}

async function runInPage(tabId, func, args = []) {
  const result = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return result?.[0]?.result;
}
async function pageInfoSafe(tabId) { try { return await runInPage(tabId, pageNowPlaying); } catch { return null; } }

async function executeMedia(action, value, step) {
  const tabId = await mediaTab();
  return (await runInPage(tabId, pageMedia, [action, value ?? null, step ?? null])) || {};
}

// ------------------------------------------------------------------ tabs and pages
async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) throw new Error("There is no active tab.");
  return tab;
}
async function requirePageAccess() {
  if (!(await chrome.permissions.contains(PAGE_ORIGINS))) {
    throw new Error("Enable page reading in the Cozy extension options (chrome://extensions, then Details, then Extension options).");
  }
}

async function tabOperation(action) {
  switch (action.op) {
    case "close": { const t = await activeTab(); await withTabEditRetry(() => chrome.tabs.remove(t.id)); return {}; }
    case "back": { const t = await activeTab(); await chrome.tabs.goBack(t.id); return {}; }
    case "forward": { const t = await activeTab(); await chrome.tabs.goForward(t.id); return {}; }
    case "scroll": { await requirePageAccess(); const t = await activeTab(); await runInPage(t.id, pageScroll, [action.direction || "down"]); return {}; }
    case "switch": {
      const wanted = String(action.query || "").toLowerCase();
      const tab = (await chrome.tabs.query({})).find(t => (t.title || "").toLowerCase().includes(wanted) || (normalizedHost(t.url || "") || "").includes(wanted));
      if (!tab) throw new Error(`I couldn't find an open tab matching "${action.query}".`);
      await updateTab(tab.id, { active: true });
      await focusTabWindow(tab);
      return {};
    }
    case "list": return (await chrome.tabs.query({})).map(t => ({ title: t.title || "", host: normalizedHost(t.url || "") || "" }));
    default: throw new Error(`Unsupported tab operation: ${action.op}`);
  }
}

async function readPage() {
  await requirePageAccess();
  const tab = await activeTab();
  if (!normalizedHost(tab.url || "")) throw new Error("This kind of page can't be read.");
  return (await runInPage(tab.id, pageText)) || { title: "", text: "" };
}

async function executeAction(action) {
  switch (action.type) {
    case "open_url": await findOrCreateTab(action.url, { newTab: !!action.new_tab }); return {};
    case "play_youtube": return await playYouTube(action);
    case "media": return await executeMedia(action.action, action.value ?? null, action.step ?? null);
    case "now_playing": return await runInPage(await mediaTab(), pageNowPlaying);
    case "page_text": return await readPage();
    case "tab": return await tabOperation(action);
    default: throw new Error(`Unknown action type: ${action.type}`);
  }
}

// ------------------------------------------------------------------ talking to the backend
async function acknowledge(id, ok, detail, data) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await fetch(`${API}/api/ext/ack/${id}`, { method: "POST", headers: { ...HEADERS, "Content-Type": "application/json" }, body: JSON.stringify({ ok, detail, data }) });
      if (r.ok || r.status === 404) return;
    } catch { /* backend restarting */ }
    await sleep(500 * (attempt + 1));
  }
}

async function handleAction(action) {
  let ok = true, detail = "", data = null;
  try { data = await executeAction(action); } catch (error) { ok = false; detail = String(error?.message || error); }
  await acknowledge(action.id, ok, detail, data);
}

async function pollLoop(maxRounds = Infinity) {
  if (polling) return;
  polling = true;
  let backoff = 1000;
  try {
    for (let round = 0; round < maxRounds; round++) {
      try {
        const r = await fetch(`${API}/api/ext/next?wait=${POLL_WAIT_S}&v=${WORKER_VERSION}`, { headers: HEADERS, cache: "no-store", signal: AbortSignal.timeout((POLL_WAIT_S + 8) * 1000) });
        if (!r.ok) throw new Error(`backend answered ${r.status}`);
        backoff = 1000;
        const { action } = await r.json();
        if (action) await handleAction(action);
      } catch {
        await sleep(backoff);                      // backend not running: back off up to 30 s instead of hammering it
        backoff = Math.min(backoff * 2, 30000);
      }
    }
  } finally { polling = false; }
}

function installListeners() {
  chrome.alarms.create("cozy-poll", { periodInMinutes: 0.5 });          // restarts the loop if Chrome suspended the worker
  chrome.alarms.onAlarm.addListener(a => { if (a.name === "cozy-poll") pollLoop(); });
  chrome.runtime.onInstalled.addListener(() => pollLoop());
  chrome.runtime.onStartup.addListener(() => pollLoop());
  chrome.tabs.onRemoved.addListener(tabId => updateTabMap(m => { for (const h of Object.keys(m)) if (m[h] === tabId) delete m[h]; }).catch(() => {}));
  console.info(`Cozy Tab Controller worker ${WORKER_VERSION} started`);
  pollLoop();
}

const api = {
  WORKER_VERSION, normalizedHost, isStaleTabError, isTemporaryTabEditError, withTabEditRetry, findOrCreateTab, waitForTabReady,
  clickFirstYouTubeResult, startYouTubePlayback, pageMedia, pageClickFirstResult, pageNowPlaying, executeAction, executeMedia,
  handleAction, pollLoop, tabOperation, readPage, updateTabMap, youTubeSearchQuery, pageText, pageScroll,
};
if (typeof module !== "undefined" && module.exports) module.exports = api;
else installListeners();
