(() => {
  // Versioned, so a reloaded extension's newer script still loads into a tab the old one ran in.
  if (window.__echoContentScriptLoaded === 2) return;
  window.__echoContentScriptLoaded = 2;

  const STYLE = `
    #echo-cursor {
      position: fixed; z-index: 2147483647; pointer-events: none;
      transition: left 0.4s ease, top 0.4s ease; left: -100px; top: -100px;
      display: flex; align-items: center; gap: 6px;
    }
    #echo-cursor .dot {
      width: 18px; height: 18px; border-radius: 50% 50% 50% 0;
      background: #2f9e5a; border: 2px solid #ffffff;
      box-shadow: 0 0 8px rgba(47, 158, 90, 0.8);
      transform: rotate(-45deg);
    }
    #echo-cursor .label {
      background: #2f9e5a; color: #fff; font: 600 11px/1.4 system-ui, sans-serif;
      padding: 2px 6px; border-radius: 4px; white-space: nowrap;
      box-shadow: 0 1px 4px rgba(0,0,0,0.4);
    }
    .echo-ripple {
      position: fixed; z-index: 2147483646; pointer-events: none;
      width: 24px; height: 24px; margin-left: -12px; margin-top: -12px;
      border-radius: 50%; border: 2px solid #2f9e5a;
      animation: echo-ripple-anim 0.5s ease-out forwards;
    }
    @keyframes echo-ripple-anim {
      from { transform: scale(0.3); opacity: 1; }
      to { transform: scale(2.2); opacity: 0; }
    }
    #echo-pixel-warning {
      position: fixed; inset: 0; z-index: 2147483647;
      background: rgba(0, 0, 0, 0.75); display: flex;
      align-items: center; justify-content: center;
    }
    #echo-pixel-warning .box {
      background: #161b18; color: #e4ebe6; max-width: 420px; padding: 20px 24px;
      border: 2px solid #c9c4b4; border-radius: 8px; font: 14px/1.5 system-ui, sans-serif;
      box-shadow: 0 0 30px rgba(201, 196, 180, 0.4);
    }
    #echo-pixel-warning .title {
      font-weight: 700; color: #c9c4b4; margin-bottom: 8px; font-size: 15px;
    }
    #echo-pixel-warning button {
      margin-top: 14px; margin-right: 8px; padding: 8px 14px; border: none;
      border-radius: 4px; cursor: pointer; font: 600 13px system-ui, sans-serif;
    }
    #echo-pixel-warning #echo-warn-allow { background: #c9c4b4; color: #12140f; }
    #echo-pixel-warning #echo-warn-deny { background: #333; color: #eee; }
  `;

  function ensureStyle() {
    if (document.getElementById("echo-style")) return;
    const style = document.createElement("style");
    style.id = "echo-style";
    style.textContent = STYLE;
    document.documentElement.appendChild(style);
  }

  function ensureCursor() {
    ensureStyle();
    let cursor = document.getElementById("echo-cursor");
    if (!cursor) {
      cursor = document.createElement("div");
      cursor.id = "echo-cursor";
      cursor.innerHTML = '<div class="dot"></div><div class="label">Echo</div>';
      document.documentElement.appendChild(cursor);
    }
    return cursor;
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  async function moveCursorTo(x, y) {
    const cursor = ensureCursor();
    cursor.style.left = `${x - 9}px`;
    cursor.style.top = `${y - 9}px`;
    await sleep(420);
  }

  function showClickRipple(x, y) {
    const ripple = document.createElement("div");
    ripple.className = "echo-ripple";
    ripple.style.left = `${x}px`;
    ripple.style.top = `${y}px`;
    document.documentElement.appendChild(ripple);
    setTimeout(() => ripple.remove(), 550);
  }

  // --- Page scanning -------------------------------------------------------

  let elementMap = new Map();
  let nextId = 1;
  const MAX_ELEMENTS = 150;

  const INTERACTIVE_SELECTOR = [
    "button", "a[href]", "input", "textarea", "select",
    '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]',
    '[role="textbox"]', '[role="combobox"]', '[role="menuitem"]', '[role="tab"]',
    '[contenteditable="true"]', "[onclick]",
  ].join(", ");

  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return false;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return false;
    if (parseFloat(style.opacity) === 0) return false;
    return true;
  }

  function describeElement(el) {
    const tag = el.tagName.toLowerCase();
    let role = el.getAttribute("role");
    if (!role) {
      if (tag === "a") role = "link";
      else if (tag === "button") role = "button";
      else if (tag === "input") {
        const type = (el.getAttribute("type") || "text").toLowerCase();
        role = type === "checkbox" ? "checkbox" : type === "radio" ? "radio" : "textbox";
      } else if (tag === "textarea") role = "textbox";
      else if (tag === "select") role = "combobox";
      else if (el.isContentEditable) role = "textbox";
      else role = "element";
    }

    let label = el.getAttribute("aria-label") || "";
    if (!label && el.labels && el.labels.length) label = el.labels[0].innerText.trim();
    if (!label) {
      label = (el.innerText || el.value || el.getAttribute("placeholder") || el.getAttribute("title") || el.getAttribute("alt") || "")
        .trim().slice(0, 80);
    }

    const value = tag === "input" || tag === "textarea" ? el.value : el.isContentEditable ? el.innerText : "";

    return {
      role,
      label: label || "(unlabeled)",
      value: value ? String(value).slice(0, 80) : "",
      disabled: Boolean(el.disabled),
    };
  }

  function scanPage() {
    elementMap = new Map();
    nextId = 1;
    const elements = [];
    for (const el of document.querySelectorAll(INTERACTIVE_SELECTOR)) {
      if (!isVisible(el)) continue;
      const id = nextId++;
      elementMap.set(id, el);
      elements.push({ id, ...describeElement(el) });
      if (elements.length >= MAX_ELEMENTS) break;
    }
    return { ok: true, data: { elements } };
  }

  // --- Actions ---------------------------------------------------------

  function setNativeValue(el, value) {
    const proto = Object.getPrototypeOf(el);
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
  }

  async function clickElement(targetId) {
    const el = elementMap.get(targetId);
    if (!el) return { ok: false, error: `No element with id ${targetId} - the page may have changed, scan it again.` };
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    await sleep(300);
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    await moveCursorTo(x, y);
    showClickRipple(x, y);
    el.click();
    return { ok: true };
  }

  const KEYS = {
    enter: ["Enter", 13], escape: ["Escape", 27], esc: ["Escape", 27], tab: ["Tab", 9], space: [" ", 32],
    backspace: ["Backspace", 8], delete: ["Delete", 46], arrowdown: ["ArrowDown", 40], arrowup: ["ArrowUp", 38],
    arrowleft: ["ArrowLeft", 37], arrowright: ["ArrowRight", 39], down: ["ArrowDown", 40], up: ["ArrowUp", 38],
    left: ["ArrowLeft", 37], right: ["ArrowRight", 39], pagedown: ["PageDown", 34], pageup: ["PageUp", 33],
    home: ["Home", 36], end: ["End", 35],
  };

  function pressKey(key, el) {
    const target = el || document.activeElement || document.body;
    const found = KEYS[String(key || "").toLowerCase().replace(/[\s_-]/g, "")];
    if (!found) return { ok: false, error: `I can't press "${key}" - I can press ${Object.keys(KEYS).join(", ")}.` };
    const [name, code] = found;
    const opts = { key: name, code: name === " " ? "Space" : name, keyCode: code, which: code, bubbles: true, cancelable: true };
    const notCancelled = target.dispatchEvent(new KeyboardEvent("keydown", opts));
    target.dispatchEvent(new KeyboardEvent("keypress", opts));
    target.dispatchEvent(new KeyboardEvent("keyup", opts));
    // A made-up Enter doesn't submit a form by itself: do what the browser would for a real one.
    if (name === "Enter" && notCancelled && target.tagName === "INPUT" && target.form) {
      if (typeof target.form.requestSubmit === "function") target.form.requestSubmit();
      else target.form.submit();
    }
    return { ok: true };
  }

  async function typeIntoElement(targetId, text, submit) {
    const el = elementMap.get(targetId);
    if (!el) return { ok: false, error: `No element with id ${targetId} - the page may have changed, scan it again.` };
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    await sleep(300);
    const rect = el.getBoundingClientRect();
    await moveCursorTo(rect.left + rect.width / 2, rect.top + rect.height / 2);
    el.focus();

    if (el.isContentEditable) {
      el.innerText = text;
      el.dispatchEvent(new InputEvent("input", { bubbles: true }));
    } else {
      setNativeValue(el, "");
      for (const ch of text) {
        setNativeValue(el, el.value + ch);
        el.dispatchEvent(new InputEvent("input", { bubbles: true, data: ch }));
        await sleep(20);
      }
    }
    el.dispatchEvent(new Event("change", { bubbles: true }));
    if (submit) {
      await sleep(150);
      pressKey("enter", el);
    }
    return { ok: true };
  }

  function scrollPage(direction) {
    if (direction === "top") window.scrollTo({ top: 0, behavior: "smooth" });
    else if (direction === "bottom") window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
    else window.scrollBy({ top: direction === "down" ? window.innerHeight * 0.8 : -window.innerHeight * 0.8, behavior: "smooth" });
    return { ok: true };
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function showPixelWarning(description) {
    ensureStyle();
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.id = "echo-pixel-warning";
      overlay.innerHTML = `
        <div class="box">
          <div class="title">⚠ Echo wants direct screen control</div>
          <p>Echo couldn't read this page's structure normally, so it wants to click directly
          on a pixel location it believes matches: "<b>${escapeHtml(description)}</b>".</p>
          <p>This briefly attaches Chrome's own debugger to this tab (you'll see Chrome's own
          warning bar while it happens) and clicks that exact spot, once.</p>
          <button id="echo-warn-allow">Allow this one click</button>
          <button id="echo-warn-deny">Cancel</button>
        </div>`;
      document.documentElement.appendChild(overlay);
      const finish = (ok) => { overlay.remove(); resolve({ ok }); };
      overlay.querySelector("#echo-warn-allow").addEventListener("click", () => finish(true));
      overlay.querySelector("#echo-warn-deny").addEventListener("click", () => finish(false));
    });
  }

  const MAX_PAGE_TEXT_CHARS = 4000; // unless Echo asks for more (a bigger brain context fits more)

  function readPage(maxChars) {
    const title = document.title;
    const url = location.href;
    const limit = Math.min(Number(maxChars) || MAX_PAGE_TEXT_CHARS, 40000);
    const text = (document.body.innerText || "").replace(/\s+/g, " ").trim().slice(0, limit);
    return { ok: true, data: { title, url, text } };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    (async () => {
      if (msg.type === "scan") sendResponse(scanPage());
      else if (msg.type === "read_page") sendResponse(readPage(msg.max_chars));
      else if (msg.type === "click") sendResponse(await clickElement(msg.target_id));
      else if (msg.type === "type") sendResponse(await typeIntoElement(msg.target_id, msg.text, msg.submit));
      else if (msg.type === "press_key") sendResponse(pressKey(msg.key));
      else if (msg.type === "scroll") sendResponse(scrollPage(msg.direction));
      else if (msg.type === "show_pixel_warning") sendResponse(await showPixelWarning(msg.description));
      else sendResponse({ ok: false, error: `Unknown command: ${msg.type}` });
    })();
    return true;
  });
})();
