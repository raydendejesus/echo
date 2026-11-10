const BACKEND = "http://127.0.0.1:8765";
// Sent with every poll, so Echo knows which controls this copy of the extension has.
const EXTENSION_VERSION = 3;

let paused = false;

chrome.storage.local.get(["paused"], (data) => {
  paused = Boolean(data.paused);
});

chrome.storage.onChanged.addListener((changes) => {
  if (changes.paused) paused = Boolean(changes.paused.newValue);
});

// A periodic alarm to nudge the service worker awake if Chrome ever suspends
// it during a quiet stretch - when that happens, this whole file re-runs from
// the top, which calls pollLoop() again at the bottom, so the long-poll loop
// naturally resumes either way.
chrome.alarms.create("echo-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => {});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "get_status") {
    sendResponse({ paused });
  }
  return true;
});

// Tracks whichever tab you last switched to inside Chrome, updated continuously
// via onActivated - this is deliberately NOT "whichever window currently has OS
// focus," since the normal way to use Echo is to be talking to it while Chrome
// sits unfocused in the background (querying by OS focus alone made the whole
// feature fail exactly when actually used that way).
let lastKnownActiveTabId = null;

chrome.tabs.onActivated.addListener(({ tabId }) => {
  lastKnownActiveTabId = tabId;
});

chrome.tabs.query({ active: true, lastFocusedWindow: true }, (tabs) => {
  if (tabs[0]) lastKnownActiveTabId = tabs[0].id;
});

async function getCurrentTabId() {
  if (lastKnownActiveTabId !== null) {
    try {
      await chrome.tabs.get(lastKnownActiveTabId);
      return lastKnownActiveTabId;
    } catch {
      lastKnownActiveTabId = null;
    }
  }
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tab ? tab.id : null;
}

async function ensureContentScript(tabId) {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    return { ok: true };
  } catch (err) {
    return {
      ok: false,
      error: "Sir is currently viewing a page Chrome doesn't allow extensions to act on (like a new tab page or a chrome:// page) - ask him to switch to a normal webpage first.",
    };
  }
}

async function report(result) {
  try {
    await fetch(`${BACKEND}/api/browser/report`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch (err) {
    console.warn("Echo: failed to report result to backend", err);
  }
}

async function handlePixelClick(tabId, command) {
  const approval = await chrome.tabs.sendMessage(tabId, {
    type: "show_pixel_warning",
    description: command.description || "",
  });
  if (!approval || !approval.ok) {
    return { ok: false, error: "Sir did not approve the on-screen warning, or it timed out." };
  }

  const [{ result: viewport }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => ({ width: window.innerWidth, height: window.innerHeight }),
  });
  const x = Math.round(command.x_frac * viewport.width);
  const y = Math.round(command.y_frac * viewport.height);

  await chrome.debugger.attach({ tabId }, "1.3");
  try {
    await chrome.debugger.sendCommand({ tabId }, "Input.dispatchMouseEvent", {
      type: "mousePressed", x, y, button: "left", clickCount: 1,
    });
    await chrome.debugger.sendCommand({ tabId }, "Input.dispatchMouseEvent", {
      type: "mouseReleased", x, y, button: "left", clickCount: 1,
    });
  } finally {
    await chrome.debugger.detach({ tabId });
  }
  return { ok: true };
}

// --- Whole-browser actions: the address bar, back/forward, tabs. These need no page script, so
// they work on any tab - even a new tab page, where nothing else can run.

function waitForLoad(tabId, checkNow, ms = 15000) {
  return new Promise((resolve) => {
    let timer = null;
    const finish = () => {
      chrome.tabs.onUpdated.removeListener(onUpdated);
      clearTimeout(timer);
      chrome.tabs.get(tabId).then(resolve, () => resolve(null));
    };
    const onUpdated = (id, info) => { if (id === tabId && info.status === "complete") finish(); };
    chrome.tabs.onUpdated.addListener(onUpdated);
    timer = setTimeout(finish, ms);
    if (checkNow) chrome.tabs.get(tabId).then((t) => { if (t && t.status === "complete") finish(); }, () => {});
  });
}

async function bringToFront(tab) {
  if (!tab) return;
  lastKnownActiveTabId = tab.id;
  try { await chrome.windows.update(tab.windowId, { focused: true }); } catch {}
}

const tabInfo = (t) => ({ id: t.id, title: t.title || "", url: t.url || "" });

async function handleBrowserAction(command, tabId) {
  if (command.type === "navigate") {
    const url = String(command.url || "");
    if (!/^https?:\/\//i.test(url)) return { ok: false, error: "Only web addresses (http or https) can be opened." };
    let tab;
    // Never navigate Echo's own dashboard away - the page opens beside it instead.
    const current = tabId === null ? null : await chrome.tabs.get(tabId).catch(() => null);
    const onDashboard = Boolean(current && (current.url || "").startsWith(BACKEND));
    if (command.new_tab || tabId === null || onDashboard) {
      tab = await chrome.tabs.create({ url, active: true });
      tab = await waitForLoad(tab.id, true);
    } else {
      const loaded = waitForLoad(tabId, false);
      await chrome.tabs.update(tabId, { url, active: true });
      tab = await loaded;
    }
    await bringToFront(tab);
    return tab ? { ok: true, data: tabInfo(tab) } : { ok: false, error: "The tab closed before the page loaded." };
  }
  if (command.type === "history") {
    if (tabId === null) return { ok: false, error: "No tab to do that in." };
    const loaded = waitForLoad(tabId, false, 10000);
    try {
      if (command.action === "back") await chrome.tabs.goBack(tabId);
      else if (command.action === "forward") await chrome.tabs.goForward(tabId);
      else await chrome.tabs.reload(tabId);
    } catch {
      return { ok: false, error: `There's no page to go ${command.action} to.` };
    }
    const tab = await loaded;
    return { ok: true, data: tab ? tabInfo(tab) : {} };
  }
  if (command.type === "new_tab") {
    const tab = await chrome.tabs.create({ active: true });
    await bringToFront(tab);
    return { ok: true, data: tabInfo(tab) };
  }
  if (command.type === "list_tabs") {
    const tabs = await chrome.tabs.query({});
    return { ok: true, data: { current: tabId, tabs: tabs.slice(0, 60).map(tabInfo) } };
  }
  if (command.type === "switch_tab") {
    try {
      const tab = await chrome.tabs.update(command.tab_id, { active: true });
      await bringToFront(tab);
      return { ok: true, data: tabInfo(tab) };
    } catch {
      return { ok: false, error: "That tab isn't open any more." };
    }
  }
  if (command.type === "close_tab") {
    const id = command.tab_id ?? tabId;
    if (id === null || id === undefined) return { ok: false, error: "No tab to close." };
    try {
      const tab = await chrome.tabs.get(id);
      await chrome.tabs.remove(id);
      if (id === lastKnownActiveTabId) lastKnownActiveTabId = null;
      return { ok: true, data: tabInfo(tab) };
    } catch {
      return { ok: false, error: "That tab isn't open any more." };
    }
  }
  return null;
}

const BROWSER_ACTIONS = new Set(["navigate", "history", "new_tab", "list_tabs", "switch_tab", "close_tab"]);

async function handleCommand(command) {
  if (paused) {
    return report({ ok: false, error: "The Echo browser extension is currently paused from its popup." });
  }

  if (BROWSER_ACTIONS.has(command.type)) {
    try {
      return report(await handleBrowserAction(command, await getCurrentTabId()));
    } catch (err) {
      return report({ ok: false, error: `Couldn't do that in the browser: ${err}` });
    }
  }

  if (command.type === "screenshot") {
    try {
      const dataUrl = await chrome.tabs.captureVisibleTab(undefined, { format: "jpeg", quality: 70 });
      return report({ ok: true, data: { image: dataUrl.split(",")[1] } });
    } catch (err) {
      return report({ ok: false, error: String(err) });
    }
  }

  const tabId = await getCurrentTabId();
  if (tabId === null) {
    return report({ ok: false, error: "No browser window is currently focused for Echo to act in." });
  }

  const injected = await ensureContentScript(tabId);
  if (!injected.ok) {
    return report(injected);
  }

  try {
    if (command.type === "click_at") {
      return report(await handlePixelClick(tabId, command));
    }
    const result = await chrome.tabs.sendMessage(tabId, command);
    return report(result);
  } catch (err) {
    return report({ ok: false, error: `Couldn't act on the current tab: ${err}` });
  }
}

console.log("[Echo] background script starting up, poll loop about to begin");

async function pollLoop() {
  let loggedFailureOnce = false;
  while (true) {
    try {
      const resp = await fetch(`${BACKEND}/api/browser/poll?v=${EXTENSION_VERSION}`);
      const data = await resp.json();
      if (loggedFailureOnce) {
        console.log("[Echo] connected to backend successfully");
        loggedFailureOnce = false;
      }
      if (data.command) {
        console.log("[Echo] received command:", data.command);
        await handleCommand(data.command);
      }
    } catch (err) {
      if (!loggedFailureOnce) {
        console.error("[Echo] poll to backend failed:", err);
        loggedFailureOnce = true;
      }
      await new Promise((r) => setTimeout(r, 5000));
    }
  }
}

pollLoop();
