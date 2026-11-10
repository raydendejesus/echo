const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");

const WAKE_WORD = "echo";
const WAKE_WORDS = ["echo", "jarvis"];
const STATE = { LISTENING: "listening", THINKING: "thinking", SPEAKING: "speaking" };

let state = STATE.LISTENING;
let recognition = null;
let shouldAutoRestart = true;
let awake = sessionStorage.getItem("echo_awake") === "true";

// The native background listener (listener.py) already listens continuously,
// independent of any browser tab - so this tab's own speech recognition is
// redundant by default, and having both active at once meant two separate
// engines could transcribe the same thing you said slightly differently,
// each firing its own full reply (heard as overlapping or back-to-back
// "two voices"). Off by default; opt in per-browser via Settings if wanted.
const DASHBOARD_MIC_KEY = "echo_dashboard_mic_enabled";
let micEnabled = localStorage.getItem(DASHBOARD_MIC_KEY) === "true";

function setAwake(value) {
  awake = value;
  sessionStorage.setItem("echo_awake", value ? "true" : "false");
  if (state === STATE.LISTENING) setState(STATE.LISTENING);
}

function setState(next) {
  state = next;
  document.body.classList.remove("thinking", "speaking");
  if (next === STATE.THINKING) document.body.classList.add("thinking");
  if (next === STATE.SPEAKING) document.body.classList.add("speaking");
  statusEl.textContent = next === STATE.LISTENING ? (awake ? "awake" : "listening") : next;
}

function log(text) {
  logEl.textContent = text;
}

function extractCommand(transcript) {
  const lower = transcript.toLowerCase();
  let idx = -1, len = 0;
  for (const word of WAKE_WORDS) {
    const i = lower.indexOf(word);
    if (i !== -1 && (idx === -1 || i < idx)) { idx = i; len = word.length; }
  }
  if (idx === -1) return null;
  const after = transcript.slice(idx + len).replace(/^[\s,.:!-]+/, "");
  return after.trim();
}

function showCanvas(html, title) {
  const panel = document.getElementById("canvasPanel");
  const frame = document.getElementById("canvasFrame");
  const titleEl = document.getElementById("canvasTitle");
  if (!panel || !frame) return;
  frame.srcdoc = html;
  if (titleEl) titleEl.textContent = title ? `- ${title}` : "";
  panel.hidden = false;
}

let lastSeenCanvasUpdatedAt = null;

async function loadCanvasOnStartup() {
  try {
    const [canvasResp, settingsResp] = await Promise.all([fetch("/api/canvas"), fetch("/api/settings")]);
    const data = await canvasResp.json();
    const settings = await settingsResp.json().catch(() => ({}));
    lastSeenCanvasUpdatedAt = data.updated_at || null;
    if (data.html) {
      showCanvas(data.html, data.title);
    } else if (settings.code_canvas_enabled) {
      showCanvas(
        '<body style="font-family: system-ui, sans-serif; color: #9aa39d; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; text-align: center; padding: 20px;"><p>Nothing built yet - ask Echo to build you something.</p></body>',
        ""
      );
    }
  } catch (err) {
    console.error("Failed to load canvas:", err);
  }
}

// The dashboard only ever checked /api/canvas once, at page load - a canvas
// built later (e.g. via a spoken request through the native listener, which
// never touches this tab's own JS at all) never got picked up in an
// already-open tab. Same underlying gap as the listener-status poller below:
// re-check periodically instead of only once.
async function pollCanvasForUpdates() {
  try {
    const resp = await fetch("/api/canvas");
    if (!resp.ok) return;
    const data = await resp.json();
    if (data.html && data.updated_at && data.updated_at !== lastSeenCanvasUpdatedAt) {
      lastSeenCanvasUpdatedAt = data.updated_at;
      showCanvas(data.html, data.title);
    }
  } catch (err) {
    // best-effort background poll - never worth surfacing a network hiccup here
  }
}

function showModel3D(filename, title) {
  if (window.Model3DViewer) window.Model3DViewer.load(`/3d_models/${filename}`, title);
}

let lastSeenModel3DUpdatedAt = null;

async function loadModel3DOnStartup() {
  try {
    const resp = await fetch("/api/3d_model");
    const data = await resp.json();
    lastSeenModel3DUpdatedAt = data.updated_at || null;
    if (data.filename) showModel3D(data.filename, data.title);
  } catch (err) {
    console.error("Failed to load 3D model:", err);
  }
}

async function pollModel3DForUpdates() {
  try {
    const resp = await fetch("/api/3d_model");
    if (!resp.ok) return;
    const data = await resp.json();
    if (data.filename && data.updated_at && data.updated_at !== lastSeenModel3DUpdatedAt) {
      lastSeenModel3DUpdatedAt = data.updated_at;
      showModel3D(data.filename, data.title);
    }
  } catch (err) {
    // best-effort background poll - never worth surfacing a network hiccup here
  }
}

let lastSeenListenerSeq = 0;

// Mirrors listener.py's phase reports (see server.py's /api/listener/event)
// so this tab's status circle and transcript log reflect conversations that
// happened through the always-on background listener, not just ones driven
// by this tab's own (off-by-default) microphone.
async function pollListenerStatus() {
  try {
    const resp = await fetch("/api/listener/event");
    if (!resp.ok) return;
    const ev = await resp.json();
    if (!ev.seq || ev.seq === lastSeenListenerSeq) return;
    lastSeenListenerSeq = ev.seq;

    if (ev.phase === "thinking") {
      log(`You (voice): ${ev.text}`);
      setState(STATE.THINKING);
    } else if (ev.phase === "speaking") {
      log(`Echo: ${ev.text}`);
      setState(STATE.SPEAKING);
    } else if (ev.phase === "idle" && state !== STATE.LISTENING) {
      setState(STATE.LISTENING);
    }
  } catch (err) {
    // best-effort background poll - never worth surfacing a network hiccup here
  }
}

async function playReply(data) {
  if (data.canvas_html) showCanvas(data.canvas_html, data.canvas_title);

  if (!data.reply || !data.audio) {
    // Echo is switched off - stay properly silent, not even a spoken
    // acknowledgment, and just resume listening for when it's turned back on.
    shouldAutoRestart = true;
    setState(STATE.LISTENING);
    startRecognition();
    return;
  }

  log(`Echo: ${data.reply}`);
  setState(STATE.SPEAKING);

  const audio = new Audio(`data:${data.mime};base64,${data.audio}`);
  const speakerId = localStorage.getItem("echo_speaker");
  if (speakerId && typeof audio.setSinkId === "function") {
    try {
      await audio.setSinkId(speakerId);
    } catch (err) {
      console.warn("Could not switch output device:", err);
    }
  }
  const resume = () => {
    shouldAutoRestart = true;
    setState(STATE.LISTENING);
    startRecognition();
  };
  audio.onended = resume;
  audio.onerror = resume;
  await audio.play();
}

function pauseListening() {
  shouldAutoRestart = false;
  if (recognition) recognition.stop();
  setState(STATE.THINKING);
}

async function sendToEcho(message) {
  pauseListening();
  log(`You: ${message}`);

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!resp.ok) throw new Error(`Server error ${resp.status}`);
    const data = await resp.json();
    await playReply(data);
  } catch (err) {
    console.error(err);
    log(`Error: ${err.message}`);
    shouldAutoRestart = true;
    setState(STATE.LISTENING);
    startRecognition();
  }
}

async function uploadFile(file) {
  pauseListening();
  log(`Uploading ${file.name}...`);

  try {
    const form = new FormData();
    form.append("file", file);
    const resp = await fetch("/api/upload", { method: "POST", body: form });
    if (!resp.ok) throw new Error(`Server error ${resp.status}`);
    const data = await resp.json();
    await playReply(data);
  } catch (err) {
    console.error(err);
    log(`Error: ${err.message}`);
    shouldAutoRestart = true;
    setState(STATE.LISTENING);
    startRecognition();
  }
}

function startRecognition() {
  if (!micEnabled || !recognition || state !== STATE.LISTENING) return;
  try {
    recognition.start();
  } catch (e) {
    // already started; ignore
  }
}

function setDashboardMic(enabled) {
  micEnabled = enabled;
  localStorage.setItem(DASHBOARD_MIC_KEY, enabled ? "true" : "false");
  if (enabled) {
    shouldAutoRestart = true;
    setState(STATE.LISTENING);
    startRecognition();
    log(`Say "${WAKE_WORD}" followed by your request.`);
  } else {
    shouldAutoRestart = false;
    if (recognition) recognition.stop();
    statusEl.textContent = "tab mic off - background listener is hearing you";
    log("This tab's microphone is off - Echo is still listening via the background service.");
  }
}

function initRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    setState(STATE.LISTENING);
    statusEl.textContent = "unsupported browser";
    log("Speech recognition requires Chrome or Edge.");
    return;
  }

  recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = false;
  recognition.lang = "en-GB";

  recognition.onresult = (event) => {
    const last = event.results[event.results.length - 1];
    const transcript = last[0].transcript;
    const lower = transcript.toLowerCase();

    if (awake && lower.includes("night") && WAKE_WORDS.some((w) => lower.includes(w))) {
      setAwake(false);
      log('Back to sleep - say "Echo" to wake me again.');
      return;
    }

    if (awake) {
      sendToEcho(transcript.trim());
      return;
    }

    const command = extractCommand(transcript);
    if (command) {
      setAwake(true);
      sendToEcho(command);
    } else {
      log(`Heard: "${transcript}" (no wake word)`);
    }
  };

  recognition.onerror = (event) => {
    console.warn("Recognition error:", event.error);
    log(`Mic error: ${event.error}`);
  };

  recognition.onend = () => {
    if (shouldAutoRestart && state === STATE.LISTENING) {
      startRecognition();
    }
  };

  setState(STATE.LISTENING);
  if (micEnabled) {
    log(`Say "${WAKE_WORD}" followed by your request.`);
    startRecognition();
  } else {
    statusEl.textContent = "tab mic off - background listener is hearing you";
    log("This tab's microphone is off by default - the always-on background listener already covers voice commands. Turn this on in Settings if you also want this tab listening.");
  }
}

const TOGGLE_KEYS = ["ai_enabled", "screen_access", "camera_access", "desk_guard_enabled", "calling_enabled", "call_notifications_enabled", "sir_page_enabled", "screen_unknown_callers", "agent_suggestions_enabled", "ollama_parallel_2_enabled", "browser_control_enabled", "browser_pixel_fallback_enabled", "code_canvas_enabled", "vr_mode_enabled"];

function setIndicator(key, active) {
  const el = document.getElementById(`ind-${key}`);
  if (el) el.classList.toggle("active", Boolean(active));
}

function isMobileDevice() {
  if (navigator.userAgentData) return Boolean(navigator.userAgentData.mobile);
  return /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent);
}

const LOCATION_MODE_CYCLE = ["off", "pc", "phone"];
const LOCATION_MODE_LABEL = { off: "OFF", pc: "PC", phone: "PHONE" };
let currentLocationMode = "off";

function renderLocationMode(mode) {
  currentLocationMode = mode;
  const ind = document.getElementById("ind-location_mode");
  if (ind) {
    ind.textContent = `LOC: ${LOCATION_MODE_LABEL[mode]}`;
    ind.classList.toggle("active", mode !== "off");
    ind.classList.toggle("mode-pc", mode === "pc");
    ind.classList.toggle("mode-phone", mode === "phone");
  }
  const btn = document.getElementById("toggle-location_mode");
  if (btn) btn.textContent = `Location: ${LOCATION_MODE_LABEL[mode]} (click to change)`;
}

async function cycleLocationMode() {
  const errorEl = document.getElementById("settingsError");
  errorEl.textContent = "";
  const nextMode = LOCATION_MODE_CYCLE[(LOCATION_MODE_CYCLE.indexOf(currentLocationMode) + 1) % 3];
  try {
    const resp = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ location_mode: nextMode }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `Server error ${resp.status}`);
    }
  } catch (err) {
    errorEl.textContent = err.message;
    return;
  }
  renderLocationMode(nextMode);
  maybeShareLocation();
}

function maybeShareLocation() {
  const thisDeviceMode = isMobileDevice() ? "phone" : "pc";
  if (currentLocationMode !== thisDeviceMode) return;
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      fetch("/api/location", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
          label: thisDeviceMode === "phone" ? "phone" : "PC",
        }),
      }).catch((err) => console.warn("Failed to share location:", err));
    },
    (err) => console.warn("Location permission denied or unavailable:", err),
    { maximumAge: 60000, timeout: 10000 }
  );
}

const LOCATION_REFRESH_MS = 5 * 60 * 1000;

async function loadSettings() {
  try {
    const resp = await fetch("/api/settings");
    const data = await resp.json();
    for (const key of TOGGLE_KEYS) {
      const el = document.getElementById(`toggle-${key}`);
      if (el) el.checked = Boolean(data[key]);
      setIndicator(key, data[key]);
    }
    renderLocationMode(data.location_mode || "off");
  } catch (err) {
    console.error("Failed to load settings:", err);
  }
}

async function updateSetting(key, value) {
  const errorEl = document.getElementById("settingsError");
  errorEl.textContent = "";
  try {
    const resp = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: value }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `Server error ${resp.status}`);
    }
    setIndicator(key, value);
    if (key === "ollama_parallel_2_enabled" || key === "helper_brain_enabled") watchOllamaRestart();
    if (key === "code_canvas_enabled") {
      if (value) loadCanvasOnStartup();
      else {
        const panel = document.getElementById("canvasPanel");
        if (panel) panel.hidden = true;
      }
    }
  } catch (err) {
    errorEl.textContent = err.message;
    const el = document.getElementById(`toggle-${key}`);
    if (el) el.checked = !value;
    setIndicator(key, !value);
  }
}

const OLLAMA_PARALLEL_LABEL = { 1: "one request at a time", 2: "two requests at once" };

async function refreshOllamaStatus() {
  const el = document.getElementById("ollamaStatus");
  if (!el) return null;
  try {
    const status = await (await fetch("/api/ollama/status")).json();
    if (status.restarting) {
      el.textContent = `Restarting Ollama (${OLLAMA_PARALLEL_LABEL[status.wanted]}) - about 20 seconds; replies pause until it's back.`;
    } else if (status.error) {
      el.textContent = `Ollama: ${status.error}`;
    } else if (status.slots && status.slots !== status.wanted) {
      el.textContent = `Ollama is running ${OLLAMA_PARALLEL_LABEL[status.slots] || status.slots + " at once"}, but the switch is set to ${OLLAMA_PARALLEL_LABEL[status.wanted]}. Turn it off and on again to apply.`;
    } else if (status.slots) {
      el.textContent = `Ollama is running ${OLLAMA_PARALLEL_LABEL[status.slots] || status.slots + " at once"}.`;
    }
    return status;
  } catch (err) {
    return null;
  }
}

async function watchOllamaRestart() {
  for (let i = 0; i < 90; i++) {
    const status = await refreshOllamaStatus();
    if (status && !status.restarting && i > 0) return;
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

function initSettingsPanel() {
  refreshOllamaStatus();
  loadSettings().then(() => maybeShareLocation());
  for (const key of TOGGLE_KEYS) {
    const el = document.getElementById(`toggle-${key}`);
    if (el) el.addEventListener("change", () => updateSetting(key, el.checked));
  }
  const locBtn = document.getElementById("toggle-location_mode");
  if (locBtn) locBtn.addEventListener("click", cycleLocationMode);
  setInterval(maybeShareLocation, LOCATION_REFRESH_MS);
}

function initFileAttach() {
  const input = document.getElementById("fileInput");
  if (!input) return;
  input.addEventListener("change", () => {
    if (input.files && input.files[0]) {
      uploadFile(input.files[0]);
      input.value = "";
    }
  });
}

function initEnrollButton() {
  const btn = document.getElementById("enrollBtn");
  const resultEl = document.getElementById("enrollResult");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    resultEl.textContent = "Capturing... look at the camera.";
    try {
      const resp = await fetch("/api/security/enroll", { method: "POST" });
      const data = await resp.json();
      resultEl.textContent = `Captured ${data.captured}/${data.requested} reference photos.`;
    } catch (err) {
      resultEl.textContent = `Error: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });
}

function tickClock() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleTimeString([], { hour12: false });
}

let levelStream = null;
let levelAudioCtx = null;
let levelAnimFrame = null;

function stopLevelMeter() {
  if (levelAnimFrame) cancelAnimationFrame(levelAnimFrame);
  if (levelStream) levelStream.getTracks().forEach((t) => t.stop());
  if (levelAudioCtx) levelAudioCtx.close();
  levelStream = null;
  levelAudioCtx = null;
  levelAnimFrame = null;
  const bar = document.getElementById("levelBar");
  if (bar) bar.style.width = "0%";
}

async function startLevelMeter(deviceId) {
  stopLevelMeter();
  try {
    levelStream = await navigator.mediaDevices.getUserMedia({
      audio: deviceId ? { deviceId: { exact: deviceId } } : true,
    });
  } catch (err) {
    console.warn("Could not open microphone for level meter:", err);
    return;
  }

  levelAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = levelAudioCtx.createMediaStreamSource(levelStream);
  const analyser = levelAudioCtx.createAnalyser();
  analyser.fftSize = 256;
  source.connect(analyser);
  const data = new Uint8Array(analyser.frequencyBinCount);
  const bar = document.getElementById("levelBar");

  function tick() {
    analyser.getByteFrequencyData(data);
    const avg = data.reduce((a, b) => a + b, 0) / data.length;
    if (bar) bar.style.width = `${Math.min(100, (avg / 255) * 200)}%`;
    levelAnimFrame = requestAnimationFrame(tick);
  }
  tick();
}

function setAudioStatusMsg(text, isError) {
  const el = document.getElementById("audioStatusMsg");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("error", Boolean(isError));
}

async function populateAudioDevices() {
  const micSelect = document.getElementById("micSelect");
  const speakerSelect = document.getElementById("speakerSelect");
  if (!micSelect || !speakerSelect) return;

  const devices = await navigator.mediaDevices.enumerateDevices();
  const mics = devices.filter((d) => d.kind === "audioinput");
  const speakers = devices.filter((d) => d.kind === "audiooutput");

  micSelect.innerHTML = "";
  speakerSelect.innerHTML = "";
  mics.forEach((d, i) => micSelect.add(new Option(d.label || `Microphone ${i + 1}`, d.deviceId)));
  speakers.forEach((d, i) => speakerSelect.add(new Option(d.label || `Speaker ${i + 1}`, d.deviceId)));

  const savedMic = localStorage.getItem("echo_mic");
  const savedSpeaker = localStorage.getItem("echo_speaker");
  if (savedMic && mics.some((d) => d.deviceId === savedMic)) micSelect.value = savedMic;
  if (savedSpeaker && speakers.some((d) => d.deviceId === savedSpeaker)) speakerSelect.value = savedSpeaker;

  if (mics.length === 0) {
    setAudioStatusMsg(
      "The browser isn't reporting any microphones. Click the padlock/site-info icon in the address bar, set Microphone to Allow for this page, then reopen this tab.",
      true
    );
  } else if (!mics[0].label) {
    setAudioStatusMsg(
      "Devices found, but the browser hasn't granted microphone permission for this page yet, so names/level preview won't work. Click the padlock icon in the address bar and allow Microphone access, then reopen this tab.",
      true
    );
  } else {
    setAudioStatusMsg("");
  }
}

async function onAudioTabActivated() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setAudioStatusMsg(
      "This browser tab can't access microphones from this address. Open the dashboard as http://127.0.0.1:8765 or http://localhost:8765, not a different hostname/IP.",
      true
    );
    return;
  }
  try {
    const primer = await navigator.mediaDevices.getUserMedia({ audio: true });
    primer.getTracks().forEach((t) => t.stop());
  } catch (err) {
    console.warn("Microphone permission not granted:", err);
    setAudioStatusMsg(
      `Browser microphone access was denied (${err.name}). Click the padlock/site-info icon in the address bar, set Microphone to Allow, then reopen this tab. This only affects this preview panel - Echo's actual hearing runs outside the browser and is unaffected.`,
      true
    );
  }
  await populateAudioDevices();
  const micSelect = document.getElementById("micSelect");
  if (micSelect && micSelect.value) startLevelMeter(micSelect.value);
}

function initAudioPanel() {
  const micSelect = document.getElementById("micSelect");
  const speakerSelect = document.getElementById("speakerSelect");
  if (!micSelect || !speakerSelect) return;

  micSelect.addEventListener("change", () => {
    localStorage.setItem("echo_mic", micSelect.value);
    startLevelMeter(micSelect.value);
  });

  speakerSelect.addEventListener("change", () => {
    localStorage.setItem("echo_speaker", speakerSelect.value);
  });
}

// --- Setup checklist ---
async function loadSetup() {
  const list = document.getElementById("setupList");
  const badge = document.getElementById("setupBadge");
  if (!list) return;
  try {
    const items = await (await fetch("/api/setup")).json();
    const missingRequired = items.filter((i) => i.required && !i.ok).length;
    if (badge) {
      badge.hidden = missingRequired === 0;
      badge.textContent = missingRequired;
    }
    list.innerHTML = "";
    let shownOptional = false;
    for (const item of items) {
      if (!item.required && !shownOptional) {
        shownOptional = true;
        const h = document.createElement("li");
        h.className = "setup-heading";
        h.textContent = "Optional extras";
        list.appendChild(h);
      }
      const li = document.createElement("li");
      li.className = `setup-item ${item.ok ? "setup-ok" : item.required ? "setup-missing" : "setup-optional"}`;
      const mark = document.createElement("span");
      mark.className = "setup-mark";
      mark.textContent = item.ok ? "\u2713" : item.required ? "\u2717" : "\u2013";
      const body = document.createElement("span");
      body.className = "setup-body";
      const title = document.createElement("strong");
      title.textContent = item.label;
      const detail = document.createElement("span");
      detail.className = "pb-number";
      detail.textContent = ` - ${item.detail}`;
      body.append(title, detail);
      if (!item.ok && item.how) {
        const how = document.createElement("div");
        how.className = "hint-text";
        how.textContent = item.how;
        body.appendChild(how);
      }
      li.append(mark, body);
      if (!item.ok && item.fix) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "hud-btn";
        const busy = item.fixing === "running";
        btn.textContent = busy ? "Working..." : item.fixing && item.fixing.startsWith("failed") ? "Retry" : "Fix it";
        btn.disabled = busy;
        if (item.fixing && item.fixing.startsWith("failed")) btn.title = item.fixing;
        btn.addEventListener("click", async () => {
          btn.disabled = true;
          btn.textContent = "Working...";
          await fetch(`/api/setup/fix/${item.fix}`, { method: "POST" });
        });
        li.appendChild(btn);
      }
      list.appendChild(li);
    }
  } catch (err) {
    // server briefly unreachable
  }
}

// --- Brain memory: the context window's size, and what each size costs in graphics memory ---
let brainTimer = null;
async function loadBrain() {
  const box = document.getElementById("brainSizes");
  const note = document.getElementById("brainNote");
  if (!box || !note) return;
  let info;
  try {
    info = await (await fetch("/api/brain")).json();
  } catch {
    return;
  }
  const roomGb = info.gpu_total_gb ? info.gpu_total_gb - 2 : null; // Windows and apps keep a little
  const engine = info.engine || {};
  const onEngine = engine.enabled && engine.running;
  if (onEngine) for (const c of info.choices) c.vram_gb = engine.vram_gb[c.tokens] ?? c.vram_gb;
  box.innerHTML = "";
  for (const c of info.choices) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ui-chip" + (c.tokens === info.tokens ? " active" : "");
    b.textContent = `${Math.round(c.tokens / 1024)}K - ${onEngine ? "~" : ""}${c.vram_gb} GB`;
    const tooBig = roomGb !== null && c.vram_gb > roomGb;
    b.disabled = info.changing || tooBig;
    if (tooBig) b.title = `Won't fit on this ${info.gpu_total_gb} GB graphics card`;
    b.addEventListener("click", async () => {
      if (c.tokens === info.tokens) return;
      note.textContent = "Rebuilding the brain at the new size...";
      const resp = await fetch("/api/brain", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tokens: c.tokens }) });
      if (!resp.ok) note.textContent = (await resp.json().catch(() => ({}))).detail || "Couldn't change it.";
      else loadBrain();
    });
    box.appendChild(b);
  }
  const now = info.choices.find((c) => c.tokens === info.tokens);
  let text = `Now: ${Math.round(info.tokens / 1024)}K tokens (about ${Math.round(info.tokens * 0.75).toLocaleString()} words).`;
  if (info.slots > 1 && now) {
    text += ` Echo Mini is off, so two full Echos share this space - it's set aside twice (with Echo Mini on, ${Math.round(info.tokens / 1024)}K takes ${now.vram_gb_one_slot} GB).`;
  }
  if (info.gpu_total_gb) text += ` This graphics card has ${info.gpu_total_gb} GB in all, shared with image and 3D generation and games.`;
  const mini = info.helper || {};
  if (mini.enabled) {
    text += mini.installing ? " Echo Mini is downloading (about 2 GB)..."
      : mini.ready ? " The full Echo is kept for you; everyone else talks to Echo Mini (about 3 GB, only while it's used)."
      : mini.error ? ` Echo Mini: ${mini.error}` : "";
  }
  if (engine.enabled) {
    text += engine.starting ? " The TurboQuant engine is starting..."
      : engine.running ? " Echo's brain is running in the TurboQuant engine."
      : ` The TurboQuant engine isn't running, so Ollama runs Echo's brain${engine.error ? `: ${engine.error}` : "."}`;
  }
  if (engine.installing) text += " Downloading the TurboQuant engine (about 840 MB)...";
  const engineBox = document.getElementById("engineOn");
  const engineInstall = document.getElementById("engineInstall");
  if (engineBox) {
    engineBox.checked = Boolean(engine.enabled);
    engineBox.disabled = !engine.installed || engine.starting || engine.installing;
    engineInstall.hidden = engine.installed || engine.installing;
  }
  if (info.changing) text = "Rebuilding the brain at the new size - about half a minute...";
  if (info.restarting) text = "Restarting Ollama with the new setting...";
  if (info.error) text = info.error;
  note.textContent = text;
  const compact = document.getElementById("brainCompact");
  if (compact) {
    compact.checked = Boolean(info.compact);
    compact.disabled = Boolean(info.restarting || info.changing);
  }
  clearTimeout(brainTimer);
  if (info.changing || info.restarting || mini.installing || engine.starting || engine.installing) brainTimer = setTimeout(loadBrain, 3000);
}
window.loadBrain = loadBrain;

document.addEventListener("DOMContentLoaded", () => {
  const engineBox = document.getElementById("engineOn");
  if (engineBox) {
    engineBox.addEventListener("change", async () => {
      engineBox.disabled = true;
      const resp = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ turbo_engine_enabled: engineBox.checked }) });
      if (!resp.ok) {
        const note = document.getElementById("brainNote");
        if (note) note.textContent = (await resp.json().catch(() => ({}))).detail || "Couldn't change it.";
      }
      setTimeout(loadBrain, 1500);
    });
    document.getElementById("engineInstall").addEventListener("click", async () => {
      await fetch("/api/brain/engine/install", { method: "POST" });
      setTimeout(loadBrain, 1000);
    });
  }
  const compact = document.getElementById("brainCompact");
  if (!compact) return;
  compact.addEventListener("change", async () => {
    compact.disabled = true;
    const resp = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ brain_compact_enabled: compact.checked }) });
    if (!resp.ok) {
      const note = document.getElementById("brainNote");
      if (note) note.textContent = (await resp.json().catch(() => ({}))).detail || "Couldn't change it.";
    }
    setTimeout(loadBrain, 1500);
  });
});

// --- Sound outputs: Echo's voice (always on) and a speaker for white noise and alarms ---
async function soundSetting(body) {
  const resp = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!resp.ok) document.getElementById("soundNote").textContent = (await resp.json().catch(() => ({}))).detail || "Couldn't change it.";
  loadSound();
}

function fillOutputs(select, devices, chosen, firstLabel) {
  select.innerHTML = "";
  const options = [["", firstLabel], ...devices.map((d) => [d, d])];
  if (chosen && !devices.includes(chosen)) options.push([chosen, `${chosen} (not connected)`]);
  for (const [value, label] of options) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = label;
    select.append(o);
  }
  select.value = chosen;
}

async function loadSound() {
  const card = document.getElementById("soundCard");
  if (!card) return;
  let info;
  try {
    info = await (await fetch("/api/sound")).json();
  } catch {
    return;
  }
  fillOutputs(document.getElementById("voiceOut"), info.devices, info.voice_output_device, "Windows default (your headphones)");
  fillOutputs(document.getElementById("speakerOut"), info.devices, info.speaker_device, "Choose a speaker...");
  const on = document.getElementById("speakerOn");
  on.checked = info.speaker_enabled;
  document.getElementById("speakerOut").disabled = !info.speaker_enabled;
  const playing = [info.ringing && "an alarm is ringing", info.noise && `${info.noise} noise is playing`].filter(Boolean).join(" and ");
  let note = info.speaker_enabled && info.speaker_device
    ? `White noise and alarms play on ${info.speaker_device}; Echo's voice stays on ${info.voice_output_device || "your headphones"}.`
    : "No speaker in use - white noise and alarms play where Echo's voice does.";
  if (info.speaker_enabled && !info.speaker_device) note = "Pick the speaker from the list.";
  document.getElementById("soundNote").textContent = note + (playing ? ` Right now ${playing}.` : "");
  document.getElementById("soundStop").hidden = !playing;
  const list = document.getElementById("alarmList");
  list.innerHTML = "";
  for (const a of info.alarms) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "pb-name";
    name.textContent = a.spoken;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "pb-remove";
    del.textContent = "remove";
    del.addEventListener("click", async () => {
      await fetch(`/api/alarms/${encodeURIComponent(a.id)}`, { method: "DELETE" });
      loadSound();
    });
    li.append(name, del);
    list.append(li);
  }
}
window.loadSound = loadSound;

document.addEventListener("DOMContentLoaded", () => {
  const card = document.getElementById("soundCard");
  if (!card) return;
  document.getElementById("voiceOut").addEventListener("change", (e) => soundSetting({ voice_output_device: e.target.value }));
  document.getElementById("speakerOut").addEventListener("change", (e) => soundSetting({ speaker_device: e.target.value }));
  document.getElementById("speakerOn").addEventListener("change", (e) => soundSetting({ speaker_enabled: e.target.checked }));
  card.querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", async () => {
    const device = document.getElementById(b.dataset.test).value;
    const resp = await fetch("/api/sound/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device }) });
    document.getElementById("soundNote").textContent = resp.ok ? `Played a chime on ${device || "the Windows default"}.`
      : (await resp.json().catch(() => ({}))).detail || "Couldn't play on it.";
  }));
  document.getElementById("soundStop").addEventListener("click", async () => {
    await fetch("/api/sound/stop", { method: "POST" });
    loadSound();
  });
  document.getElementById("alarmForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { time: document.getElementById("alarmTime").value, repeat: document.getElementById("alarmRepeat").value,
      label: document.getElementById("alarmLabel").value };
    const resp = await fetch("/api/alarms", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!resp.ok) document.getElementById("soundNote").textContent = (await resp.json().catch(() => ({}))).detail || "Couldn't add it.";
    else e.target.reset();
    loadSound();
  });
});

function initSetup() {
  loadSetup();
  loadBrain();
  loadSound();
  setInterval(() => {
    if (!document.hidden) loadSetup();
  }, 6000);
}

document.addEventListener("DOMContentLoaded", initSetup);

// --- Words Echo should know (hearing hints) ---
function renderWords(data) {
  const list = document.getElementById("wordList");
  const learned = document.getElementById("learnedWords");
  if (!list) return;
  list.innerHTML = "";
  for (const word of data.custom) {
    const li = document.createElement("li");
    const text = document.createElement("span");
    text.textContent = word;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "pb-remove";
    del.textContent = "remove";
    del.addEventListener("click", async () => {
      const resp = await fetch(`/api/hearing_words/${encodeURIComponent(word)}`, { method: "DELETE" });
      if (resp.ok) renderWords(await resp.json());
    });
    li.append(text, del);
    list.appendChild(li);
  }
  const auto = data.all.filter((w) => !data.custom.includes(w));
  if (learned) learned.textContent = auto.length ? `Also listening for: ${auto.join(", ")}` : "";
}

function initWords() {
  const form = document.getElementById("wordForm");
  if (!form) return;
  fetch("/api/hearing_words").then((r) => r.json()).then(renderWords).catch(() => {});
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.getElementById("wordInput");
    const resp = await fetch("/api/hearing_words", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ word: input.value }),
    });
    if (resp.ok) {
      renderWords(await resp.json());
      input.value = "";
    }
  });
}

document.addEventListener("DOMContentLoaded", initWords);

// --- Projects (Echo working on its own) and approvals ---
const PROJECT_STATUS_WORDS = { working: "working", waiting_approval: "needs your OK", done: "done", stopped: "stopped" };
let openProjectId = null;

function hoursLeftText(h) {
  return h > 0 ? `approved for ${h < 1 ? "under an hour" : Math.round(h) + "h"} more` : "approval expired";
}

async function loadApprovals() {
  try {
    const records = await (await fetch("/api/approvals")).json();
    const waiting = records.filter((r) => r.status === "pending");
    const box = document.getElementById("approvalsBox");
    const list = document.getElementById("approvalsList");
    const badge = document.getElementById("approvalBadge");
    if (badge) {
      badge.hidden = waiting.length === 0;
      badge.textContent = waiting.length;
    }
    if (!box || !list) return;
    box.hidden = waiting.length === 0;
    list.innerHTML = "";
    for (const r of waiting) {
      const li = document.createElement("li");
      const text = document.createElement("span");
      text.textContent = r.what;
      text.title = r.detail || "";
      const yes = document.createElement("button");
      yes.type = "button";
      yes.className = "hud-btn";
      yes.textContent = "Approve";
      const no = document.createElement("button");
      no.type = "button";
      no.className = "hud-btn";
      no.textContent = "Decline";
      const decide = async (approve) => {
        await fetch(`/api/approvals/${r.id}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approve }),
        });
        loadApprovals();
        loadProjects();
      };
      yes.addEventListener("click", () => decide(true));
      no.addEventListener("click", () => decide(false));
      li.append(text, yes, no);
      list.appendChild(li);
    }
  } catch (err) {
    // server briefly unreachable
  }
}

async function loadProjects() {
  const list = document.getElementById("projectsList");
  if (!list) return;
  try {
    const projects = await (await fetch("/api/projects")).json();
    list.innerHTML = "";
    if (!projects.length) {
      const li = document.createElement("li");
      li.className = "hint-text";
      li.textContent = "No projects yet.";
      list.appendChild(li);
    }
    for (const p of projects) {
      const li = document.createElement("li");
      li.className = `project-row project-${p.status}`;
      const title = document.createElement("span");
      title.className = "pb-name";
      title.textContent = p.title;
      const meta = document.createElement("span");
      meta.className = "pb-number";
      const live = p.status === "working" || p.status === "waiting_approval";
      meta.textContent = `${PROJECT_STATUS_WORDS[p.status] || p.status} - ${p.steps} steps` + (live ? ` - ${hoursLeftText(p.approval_hours_left)}` : "");
      const view = document.createElement("button");
      view.type = "button";
      view.className = "hud-btn";
      view.textContent = "View";
      view.addEventListener("click", () => openProject(p.id));
      li.append(title, document.createElement("br"), meta, view);
      list.appendChild(li);
    }
  } catch (err) {
    // server briefly unreachable
  }
}

async function openProject(id) {
  openProjectId = id;
  const modal = document.getElementById("projectModal");
  if (!modal) return;
  modal.hidden = false;
  await refreshOpenProject(true);
}

async function refreshOpenProject(reloadFrame) {
  if (!openProjectId) return;
  try {
    const p = await (await fetch(`/api/projects/${openProjectId}`)).json();
    document.getElementById("projectModalTitle").textContent = p.title;
    document.getElementById("projectModalStatus").textContent = PROJECT_STATUS_WORDS[p.status] || p.status;
    document.getElementById("projectStopBtn").hidden = !(p.status === "working" || p.status === "waiting_approval");
    const frame = document.getElementById("projectFrame");
    const src = p.preview ? `/sandbox/${encodeURIComponent(p.slug)}/${p.preview}` : "about:blank";
    if (reloadFrame || (frame.dataset.src !== src)) {
      frame.dataset.src = src;
      frame.src = p.preview ? `${src}?t=${Date.now()}` : "about:blank";
    }
    const log = document.getElementById("projectLog");
    log.innerHTML = "";
    for (const e of p.log) addProjectLogRow(log, e);
    log.scrollTop = log.scrollHeight;
    document.getElementById("projectFiles").textContent = p.files.length ? `Files: ${p.files.join(", ")}` : "";
  } catch (err) {
    // server briefly unreachable
  }
}

function addProjectLogRow(log, e) {
  const li = document.createElement("li");
  li.className = `thought thought-${e.kind === "errors" || e.kind === "error" ? "error" : e.kind === "done" ? "reply" : "note"}`;
  const tag = document.createElement("span");
  tag.className = "thought-tag";
  tag.textContent = e.kind;
  const text = document.createElement("span");
  text.className = "thought-text";
  text.textContent = e.text;
  li.append(tag, text);
  log.appendChild(li);
}

function initProjects() {
  const close = document.getElementById("projectCloseBtn");
  const stop = document.getElementById("projectStopBtn");
  if (close) {
    close.addEventListener("click", () => {
      document.getElementById("projectModal").hidden = true;
      document.getElementById("projectFrame").src = "about:blank";
      openProjectId = null;
    });
  }
  if (stop) {
    stop.addEventListener("click", async () => {
      if (!openProjectId) return;
      await fetch(`/api/projects/${openProjectId}/stop`, { method: "POST" });
      refreshOpenProject(false);
      loadProjects();
    });
  }
  loadApprovals();
  loadProjects();
  setInterval(() => {
    if (document.hidden) return;
    loadApprovals();
    loadProjects();
    if (openProjectId) refreshOpenProject(false);
  }, 3000);
}

document.addEventListener("DOMContentLoaded", initProjects);

// --- Address book: named places ("gym", "home") for directions and lookups ---
function renderAddresses(entries) {
  const list = document.getElementById("addressList");
  if (!list) return;
  const count = document.getElementById("abCount");
  if (count) count.textContent = entries.length ? `(${entries.length})` : "";
  list.innerHTML = "";
  for (const entry of entries) {
    const li = document.createElement("li");
    const info = document.createElement("span");
    const name = document.createElement("span");
    name.className = "pb-name";
    name.textContent = entry.name;
    const addr = document.createElement("span");
    addr.className = "pb-number";
    addr.textContent = entry.address;
    info.append(name, document.createElement("br"), addr);
    const del = document.createElement("button");
    del.type = "button";
    del.className = "pb-remove";
    del.textContent = "remove";
    del.addEventListener("click", async () => {
      const resp = await fetch(`/api/addresses/${encodeURIComponent(entry.name)}`, { method: "DELETE" });
      if (resp.ok) renderAddresses(await resp.json());
    });
    li.append(info, del);
    list.appendChild(li);
  }
}

function initAddressBook() {
  const form = document.getElementById("addressForm");
  if (!form) return;
  fetch("/api/addresses").then((r) => r.json()).then(renderAddresses).catch(() => {});
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = document.getElementById("abName");
    const address = document.getElementById("abAddress");
    const resp = await fetch("/api/addresses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.value, address: address.value }),
    });
    if (resp.ok) {
      renderAddresses(await resp.json());
      name.value = "";
      address.value = "";
    }
  });
}

document.addEventListener("DOMContentLoaded", initAddressBook);

// --- GitHub page: repos saved under sir's own names, to check or open by voice ---
function renderGithubRepos(entries) {
  const list = document.getElementById("githubList");
  if (!list) return;
  const count = document.getElementById("ghCount");
  if (count) count.textContent = entries.length ? `(${entries.length})` : "";
  list.innerHTML = "";
  for (const entry of entries) {
    const li = document.createElement("li");
    const info = document.createElement("span");
    const name = document.createElement("span");
    name.className = "pb-name";
    name.textContent = entry.name;
    const link = document.createElement("a");
    link.className = "pb-number";
    link.href = entry.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = entry.url.replace(/^https:\/\//, "");
    info.append(name, document.createElement("br"), link);
    const ask = document.createElement("button");
    ask.type = "button";
    ask.className = "pb-remove";
    ask.textContent = "ask Echo";
    ask.title = `Has Echo check the ${entry.name} GitHub`;
    ask.addEventListener("click", () => window.sendToEcho && window.sendToEcho(`Check the ${entry.name} GitHub and tell me what's new.`));
    const del = document.createElement("button");
    del.type = "button";
    del.className = "pb-remove";
    del.textContent = "remove";
    del.addEventListener("click", async () => {
      const resp = await fetch(`/api/github_repos/${encodeURIComponent(entry.name)}`, { method: "DELETE" });
      if (resp.ok) renderGithubRepos(await resp.json());
    });
    li.append(info, ask, del);
    list.appendChild(li);
  }
}

function initGithubRepos() {
  const form = document.getElementById("githubForm");
  if (!form) return;
  fetch("/api/github_repos").then((r) => r.json()).then(renderGithubRepos).catch(() => {});
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = document.getElementById("ghName");
    const url = document.getElementById("ghUrl");
    const error = document.getElementById("ghError");
    const resp = await fetch("/api/github_repos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.value, url: url.value }),
    });
    if (resp.ok) {
      renderGithubRepos(await resp.json());
      name.value = "";
      url.value = "";
      error.hidden = true;
    } else {
      error.textContent = (await resp.json().catch(() => ({}))).detail || "Couldn't save that.";
      error.hidden = false;
    }
  });
}

document.addEventListener("DOMContentLoaded", initGithubRepos);

// --- Calendar: sir's events (name, day, time, place, what it's about) and their reminders ---
const cal = { month: null, selected: "", events: [], editing: null };
const CAL_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const CAL_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

function calYmd(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function calParse(ymd) {
  const [y, m, d] = ymd.split("-").map(Number);
  return new Date(y, m - 1, d);
}
function calTime(t) {
  if (!t) return "";
  const [h, m] = t.split(":").map(Number);
  return `${h % 12 || 12}${m ? `:${String(m).padStart(2, "0")}` : ""} ${h < 12 ? "AM" : "PM"}`;
}
function calDayLabel(ymd) {
  const d = calParse(ymd);
  const today = calYmd(new Date());
  const tomorrow = calYmd(new Date(Date.now() + 864e5));
  const name = `${CAL_WEEKDAYS[(d.getDay() + 6) % 7]}, ${CAL_MONTHS[d.getMonth()]} ${d.getDate()}`;
  return ymd === today ? `Today - ${name}` : ymd === tomorrow ? `Tomorrow - ${name}` : name;
}
function calWhen(e, withDay) {
  let when = withDay ? calDayLabel(e.date) : "";
  if (e.time) when += `${when ? ", " : ""}${calTime(e.time)}${e.end_time ? ` - ${calTime(e.end_time)}` : ""}`;
  else if (!withDay) when = "all day";
  if (e.location) when += ` - ${e.location}`;
  return when;
}

async function loadCalendar() {
  try {
    cal.events = await (await fetch("/api/calendar")).json();
  } catch {
    return;
  }
  renderCalendar();
}

function calEventRow(e, withDay) {
  const li = document.createElement("li");
  const info = document.createElement("span");
  const name = document.createElement("span");
  name.className = "pb-name";
  name.textContent = e.title;
  const when = document.createElement("span");
  when.className = "cal-when";
  when.textContent = calWhen(e, withDay) + (e.remind_minutes != null ? "  · reminder" : "");
  info.append(name, when);
  if (e.details) {
    const about = document.createElement("span");
    about.className = "cal-about";
    about.textContent = e.details;
    info.append(about);
  }
  const btns = document.createElement("span");
  btns.className = "cal-btns";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "pb-remove";
  edit.textContent = "edit";
  edit.addEventListener("click", () => calEdit(e));
  const del = document.createElement("button");
  del.type = "button";
  del.className = "pb-remove";
  del.textContent = "remove";
  del.addEventListener("click", async () => {
    if (!confirm(`Remove "${e.title}" from the calendar?`)) return;
    const resp = await fetch(`/api/calendar/${encodeURIComponent(e.id)}`, { method: "DELETE" });
    if (resp.ok) {
      if (cal.editing === e.id) calResetForm();
      loadCalendar();
    }
  });
  btns.append(edit, del);
  li.append(info, btns);
  return li;
}

function renderCalendar() {
  const grid = document.getElementById("calGrid");
  if (!grid) return;
  const today = calYmd(new Date());
  if (!cal.month) cal.month = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
  if (!cal.selected) cal.selected = today;
  const byDay = {};
  for (const e of cal.events) (byDay[e.date] = byDay[e.date] || []).push(e);
  document.getElementById("calMonthTitle").textContent = `${CAL_MONTHS[cal.month.getMonth()]} ${cal.month.getFullYear()}`;
  const upcoming = cal.events.filter((e) => e.date >= today);
  document.getElementById("calCount").textContent = upcoming.length ? `(${upcoming.length} coming up)` : "";

  grid.innerHTML = "";
  const start = new Date(cal.month);
  start.setDate(1 - ((cal.month.getDay() + 6) % 7)); // back to Monday
  for (let i = 0; i < 42; i++) {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    if (i >= 35 && d.getMonth() !== cal.month.getMonth()) break;
    const ymd = calYmd(d);
    const b = document.createElement("button");
    b.type = "button";
    b.className = "cal-day" + (d.getMonth() !== cal.month.getMonth() ? " other" : "") + (ymd === today ? " today" : "") + (ymd === cal.selected ? " selected" : "");
    const events = byDay[ymd] || [];
    b.setAttribute("aria-label", `${calDayLabel(ymd)}${events.length ? `, ${events.length} event${events.length > 1 ? "s" : ""}` : ""}`);
    const num = document.createElement("span");
    num.className = "cal-num";
    num.textContent = d.getDate();
    b.append(num);
    for (const e of events.slice(0, 2)) {
      const chip = document.createElement("span");
      chip.className = "cal-chip";
      chip.textContent = (e.time ? calTime(e.time).replace(" ", "").toLowerCase() + " " : "") + e.title;
      b.append(chip);
    }
    if (events.length > 2) {
      const more = document.createElement("span");
      more.className = "cal-more";
      more.textContent = `+${events.length - 2} more`;
      b.append(more);
    }
    b.addEventListener("click", () => {
      cal.selected = ymd;
      if (!cal.editing) document.getElementById("calDate").value = ymd;
      renderCalendar();
    });
    grid.append(b);
  }

  document.getElementById("calDayTitle").textContent = calDayLabel(cal.selected);
  const dayList = document.getElementById("calDayList");
  dayList.innerHTML = "";
  for (const e of byDay[cal.selected] || []) dayList.append(calEventRow(e, false));
  const up = document.getElementById("calUpcoming");
  up.innerHTML = "";
  for (const e of upcoming.slice(0, 12)) up.append(calEventRow(e, true));
  const dateIn = document.getElementById("calDate");
  if (!dateIn.value) dateIn.value = cal.selected;
}

function calResetForm() {
  cal.editing = null;
  document.getElementById("calForm").reset();
  document.getElementById("calDate").value = cal.selected || calYmd(new Date());
  document.getElementById("calRemind").value = "30";
  document.getElementById("calSave").textContent = "Add event";
  document.getElementById("calFormTitle").textContent = "Add an event";
  document.getElementById("calCancel").hidden = true;
  document.getElementById("calError").hidden = true;
}

function calEdit(e) {
  cal.editing = e.id;
  cal.selected = e.date;
  cal.month = new Date(calParse(e.date).getFullYear(), calParse(e.date).getMonth(), 1);
  document.getElementById("calName").value = e.title;
  document.getElementById("calDate").value = e.date;
  document.getElementById("calTime").value = e.time || "";
  document.getElementById("calEnd").value = e.end_time || "";
  document.getElementById("calLoc").value = e.location || "";
  document.getElementById("calDetails").value = e.details || "";
  const remind = document.getElementById("calRemind");
  remind.value = e.remind_minutes == null ? "" : String(e.remind_minutes);
  if (remind.value === "" && e.remind_minutes != null) remind.value = "30";
  document.getElementById("calSave").textContent = "Save changes";
  document.getElementById("calFormTitle").textContent = `Editing "${e.title}"`;
  document.getElementById("calCancel").hidden = false;
  renderCalendar();
  document.getElementById("calName").focus();
}

let calToastShown = new Set();
async function checkCalendarReminders() {
  let due;
  try {
    due = await (await fetch("/api/calendar/reminders")).json();
  } catch {
    return;
  }
  const fresh = due.filter((r) => !calToastShown.has(`${r.id}:${r.at}`));
  if (!fresh.length) return;
  const r = fresh[fresh.length - 1];
  fresh.forEach((x) => calToastShown.add(`${x.id}:${x.at}`));
  document.getElementById("calToastText").textContent = r.text;
  document.getElementById("calToast").hidden = false;
}

function initCalendar() {
  const form = document.getElementById("calForm");
  if (!form) return;
  const toast = document.getElementById("calToast");
  document.body.append(toast); // outside the sliding panels, so it shows on any page
  document.getElementById("calToastClose").addEventListener("click", () => { toast.hidden = true; });
  document.getElementById("calPrev").addEventListener("click", () => { cal.month = new Date(cal.month.getFullYear(), cal.month.getMonth() - 1, 1); renderCalendar(); });
  document.getElementById("calNext").addEventListener("click", () => { cal.month = new Date(cal.month.getFullYear(), cal.month.getMonth() + 1, 1); renderCalendar(); });
  document.getElementById("calToday").addEventListener("click", () => {
    const now = new Date();
    cal.month = new Date(now.getFullYear(), now.getMonth(), 1);
    cal.selected = calYmd(now);
    renderCalendar();
  });
  document.getElementById("calCancel").addEventListener("click", calResetForm);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.getElementById("calError");
    const remind = document.getElementById("calRemind").value;
    const body = {
      title: document.getElementById("calName").value,
      date: document.getElementById("calDate").value,
      time: document.getElementById("calTime").value,
      end_time: document.getElementById("calEnd").value,
      location: document.getElementById("calLoc").value,
      details: document.getElementById("calDetails").value,
      remind_minutes: remind === "" ? null : Number(remind),
    };
    const url = cal.editing ? `/api/calendar/${encodeURIComponent(cal.editing)}` : "/api/calendar";
    const resp = await fetch(url, { method: cal.editing ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!resp.ok) {
      error.textContent = (await resp.json().catch(() => ({}))).detail || "Couldn't save that.";
      error.hidden = false;
      return;
    }
    const saved = await resp.json();
    cal.selected = saved.date;
    cal.month = new Date(calParse(saved.date).getFullYear(), calParse(saved.date).getMonth(), 1);
    calResetForm();
    loadCalendar();
  });
  calResetForm();
  loadCalendar();
  checkCalendarReminders();
  setInterval(() => { if (!document.hidden) checkCalendarReminders(); }, 20000);
}
window.loadCalendar = loadCalendar;

document.addEventListener("DOMContentLoaded", initCalendar);

// --- Thinking window: what Echo heard, the tools it used, and what it said ---
const THOUGHT_ICONS = { heard: "you", tool: "tool", result: "got", reply: "echo", note: "note", error: "err" };
const MAX_THOUGHT_ROWS = 80;
const SPEECH_LEVEL = 500; // the listener's SILENCE_RMS_THRESHOLD
const METER_MAX = 2000;
let lastThoughtSeq = 0;

function addThoughtRow(event) {
  const log = document.getElementById("thoughtLog");
  if (!log) return;
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  const li = document.createElement("li");
  li.className = `thought thought-${event.kind}`;
  const tag = document.createElement("span");
  tag.className = "thought-tag";
  tag.textContent = (event.channel === "phone" ? "\u260E " : "") + (THOUGHT_ICONS[event.kind] || event.kind);
  const text = document.createElement("span");
  text.className = "thought-text";
  text.textContent = event.text;
  const time = document.createElement("span");
  time.className = "thought-time";
  time.textContent = new Date(event.at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  li.append(tag, text, time);
  log.appendChild(li);
  while (log.children.length > MAX_THOUGHT_ROWS) log.removeChild(log.firstChild);
  if (nearBottom) log.scrollTop = log.scrollHeight;
}

async function pollThoughts() {
  if (document.hidden) return;
  try {
    const resp = await fetch(`/api/activity?after=${lastThoughtSeq}`);
    const events = await resp.json();
    for (const event of events) {
      addThoughtRow(event);
      lastThoughtSeq = Math.max(lastThoughtSeq, event.seq);
    }
  } catch (err) {
    // server briefly unreachable - try again next tick
  }
}

async function pollMic() {
  if (document.hidden) return;
  const nameEl = document.getElementById("micName");
  const levelEl = document.getElementById("micLevel");
  const line = document.getElementById("micLine");
  if (!nameEl || !levelEl || !line) return;
  try {
    const mic = await (await fetch("/api/listener/mic")).json();
    const alive = mic.age !== null && mic.age < 6;
    line.classList.toggle("mic-dead", !alive);
    line.classList.toggle("mic-hearing", alive && mic.level >= SPEECH_LEVEL);
    if (!alive) {
      nameEl.textContent = "mic: voice listener isn't running";
      levelEl.style.width = "0%";
      return;
    }
    nameEl.textContent = `mic: ${mic.device}${mic.note ? ` (${mic.note})` : ""}`;
    levelEl.style.width = `${Math.min(100, (mic.level / METER_MAX) * 100)}%`;
  } catch (err) {
    line.classList.add("mic-dead");
    nameEl.textContent = "mic: can't reach Echo";
  }
}

function initThoughtPanel() {
  const mark = document.querySelector(".mic-mark");
  if (mark) mark.style.left = `${(SPEECH_LEVEL / METER_MAX) * 100}%`;
  pollThoughts();
  pollMic();
  setInterval(pollThoughts, 1500);
  setInterval(pollMic, 1500);
}

document.addEventListener("DOMContentLoaded", initThoughtPanel);

function renderPhoneBook(entries) {
  const list = document.getElementById("phoneBookList");
  if (!list) return;

  const countEl = document.getElementById("pbCount");
  if (countEl) countEl.textContent = entries.length ? `(${entries.length})` : "";

  list.innerHTML = "";
  for (const entry of entries) {
    const li = document.createElement("li");

    const info = document.createElement("span");
    const nameEl = document.createElement("span");
    nameEl.className = "pb-name";
    nameEl.textContent = entry.name;
    if (entry.scam) nameEl.classList.add("pb-scam");
    const numberEl = document.createElement("span");
    numberEl.className = "pb-number";
    numberEl.textContent = entry.number;
    info.append(nameEl);
    if (entry.scam) {
      const tag = document.createElement("button");
      tag.type = "button";
      tag.className = "pb-scam-tag";
      tag.textContent = "SCAM";
      tag.title = `${entry.scam_why || "Echo thought this call was a scam."} Click to clear.`;
      tag.addEventListener("click", async () => {
        try {
          const resp = await fetch(`/api/phonebook/${encodeURIComponent(entry.name)}/scam`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scam: false }),
          });
          if (!resp.ok) throw new Error(`Server error ${resp.status}`);
          renderPhoneBook(await resp.json());
        } catch (err) {
          console.error("Failed to clear scam mark:", err);
        }
      });
      info.append(tag);
    }
    info.append(document.createElement("br"), numberEl);
    if (entry.note || entry.screened_at) {
      const note = document.createElement("span");
      note.className = "pb-note";
      const when = entry.screened_at ? new Date(entry.screened_at * 1000).toLocaleDateString() : "";
      note.textContent = `called Echo${when ? ` ${when}` : ""}${entry.note ? `: ${entry.note}` : ""}`;
      info.append(document.createElement("br"), note);
    }

    const access = document.createElement("label");
    access.className = "switch-row pb-access";
    access.title = "When on, this contact can phone Echo. When off (or if the number isn't saved here), Echo turns the call away.";
    const accessText = document.createElement("span");
    accessText.textContent = "can call Echo";
    const accessInput = document.createElement("input");
    accessInput.type = "checkbox";
    accessInput.checked = entry.can_call_echo === true;
    const accessSwitch = document.createElement("span");
    accessSwitch.className = "switch";
    access.append(accessText, accessInput, accessSwitch);
    accessInput.addEventListener("change", async () => {
      try {
        const resp = await fetch(`/api/phonebook/${encodeURIComponent(entry.name)}/call_access`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ allowed: accessInput.checked }),
        });
        if (!resp.ok) throw new Error(`Server error ${resp.status}`);
        renderPhoneBook(await resp.json());
      } catch (err) {
        accessInput.checked = !accessInput.checked;
        console.error("Failed to change call access:", err);
      }
    });

    const sir = document.createElement("label");
    sir.className = "switch-row pb-access pb-sir";
    sir.title = "When on, a call from this number is sir himself: Echo lets you control the PC by phone (see and close apps, Spotify, shut down). Caller ID can be faked, so only switch this on for your own number.";
    const sirText = document.createElement("span");
    sirText.textContent = "sir";
    const sirInput = document.createElement("input");
    sirInput.type = "checkbox";
    sirInput.checked = entry.is_sir === true;
    const sirSwitch = document.createElement("span");
    sirSwitch.className = "switch";
    sir.append(sirText, sirInput, sirSwitch);
    sirInput.addEventListener("change", async () => {
      try {
        const resp = await fetch(`/api/phonebook/${encodeURIComponent(entry.name)}/sir`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_sir: sirInput.checked }),
        });
        if (!resp.ok) throw new Error(`Server error ${resp.status}`);
        renderPhoneBook(await resp.json());
      } catch (err) {
        sirInput.checked = !sirInput.checked;
        console.error("Failed to change sir mode:", err);
      }
    });

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "pb-remove";
    delBtn.textContent = "remove";
    delBtn.addEventListener("click", () => deletePhoneBookEntry(entry.name));

    const actions = document.createElement("div");
    actions.className = "pb-actions";
    actions.append(access, sir, delBtn);

    li.append(info, actions);
    list.appendChild(li);
  }
}

async function loadBlockedCalls() {
  const box = document.getElementById("blockedCalls");
  const list = document.getElementById("blockedCallsList");
  const count = document.getElementById("blockedCount");
  if (!box || !list) return;
  try {
    const resp = await fetch("/api/phonebook/blocked_calls");
    const attempts = await resp.json();
    list.innerHTML = "";
    box.hidden = attempts.length === 0;
    if (count) count.textContent = attempts.length ? `(${attempts.length})` : "";
    for (const attempt of attempts) {
      const li = document.createElement("li");
      li.textContent = `${attempt.name || attempt.number} - ${new Date(attempt.time * 1000).toLocaleString()}`;
      li.title = attempt.reason + (attempt.name ? ` (${attempt.number})` : "");
      list.appendChild(li);
    }
  } catch (err) {
    console.error("Failed to load turned-away calls:", err);
  }
}

async function loadPhoneBook() {
  try {
    const resp = await fetch("/api/phonebook");
    const entries = await resp.json();
    renderPhoneBook(entries);
  } catch (err) {
    console.error("Failed to load phone book:", err);
  }
}

async function deletePhoneBookEntry(name) {
  try {
    const resp = await fetch(`/api/phonebook/${encodeURIComponent(name)}`, { method: "DELETE" });
    const entries = await resp.json();
    renderPhoneBook(entries);
  } catch (err) {
    console.error("Failed to delete phone book entry:", err);
  }
}

function initPhoneBookPanel() {
  const form = document.getElementById("phoneBookForm");
  if (!form) return;
  loadPhoneBook();
  loadBlockedCalls();
  setInterval(loadBlockedCalls, 30000);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const nameEl = document.getElementById("pbName");
    const numberEl = document.getElementById("pbNumber");
    const name = nameEl.value.trim();
    const number = numberEl.value.trim();
    if (!name || !number) return;

    try {
      const resp = await fetch("/api/phonebook", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, number }),
      });
      const entries = await resp.json();
      renderPhoneBook(entries);
      nameEl.value = "";
      numberEl.value = "";
    } catch (err) {
      console.error("Failed to add phone book entry:", err);
    }
  });
}

function initDashboardMicToggle() {
  const el = document.getElementById("toggle-dashboard-mic");
  if (!el) return;
  el.checked = micEnabled;
  el.addEventListener("change", () => setDashboardMic(el.checked));
}

function buildPluginRow(plugin, errorEl) {
  const row = document.createElement("label");
  row.className = "switch-row";
  const span = document.createElement("span");
  span.textContent = plugin.label + (plugin.always_on ? " (always on)" : "");
  const vram = document.createElement("span");
  vram.className = "vram-tag";
  vram.textContent = plugin.vram_cost || "no local model";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = plugin.enabled;
  input.disabled = plugin.always_on;
  const switchSpan = document.createElement("span");
  switchSpan.className = "switch";
  row.append(span, vram, input, switchSpan);

  if (!plugin.always_on) {
    input.addEventListener("change", async () => {
      errorEl.textContent = "";
      try {
        const resp = await fetch(`/api/plugins/${encodeURIComponent(plugin.name)}/toggle`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: input.checked }),
        });
        if (!resp.ok) {
          const data = await resp.json().catch(() => ({}));
          throw new Error(data.detail || `Server error ${resp.status}`);
        }
      } catch (err) {
        errorEl.textContent = err.message;
        input.checked = !input.checked;
      }
    });
  }
  return row;
}

async function loadPlugins() {
  const connectedEl = document.getElementById("pluginsListConnected");
  const standaloneEl = document.getElementById("pluginsListStandalone");
  const errorEl = document.getElementById("pluginsError");
  if (!connectedEl || !standaloneEl) return;
  try {
    const resp = await fetch("/api/plugins");
    const plugins = await resp.json();
    connectedEl.innerHTML = "";
    standaloneEl.innerHTML = "";

    const connected = plugins.filter((p) => p.related_connection);
    const standalone = plugins.filter((p) => !p.related_connection);

    for (const plugin of connected) {
      const row = buildPluginRow(plugin, errorEl);
      row.title = `Needs the "${plugin.related_connection}" connection - see the Connections tab.`;
      connectedEl.appendChild(row);
    }
    for (const plugin of standalone) {
      standaloneEl.appendChild(buildPluginRow(plugin, errorEl));
      if (plugin.name === "pc_power") standaloneEl.appendChild(buildShutdownPhraseRow());
    }
    decorateToggleTooltips();
    updateToggleCount();
  } catch (err) {
    console.error("Failed to load plugins:", err);
  }
}

function buildShutdownPhraseRow() {
  // Write-only: the server only ever says whether a phrase is set, never what it is.
  const row = document.createElement("div");
  row.className = "phrase-row";
  const input = document.createElement("input");
  input.type = "password";
  input.autocomplete = "off";
  input.placeholder = "Secret passcode...";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "hud-btn";
  btn.textContent = "Save";
  const status = document.createElement("span");
  status.className = "vram-tag";
  row.append(input, btn, status);
  const showStatus = (isSet) => { status.textContent = isSet ? "passcode set" : "no passcode yet"; };
  fetch("/api/pc_power/phrase").then((r) => r.json()).then((d) => showStatus(d.set)).catch(() => {});
  btn.addEventListener("click", async () => {
    const phrase = input.value.trim();
    if (!phrase) return;
    try {
      const resp = await fetch("/api/pc_power/phrase", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phrase }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || `Server error ${resp.status}`);
      input.value = "";
      showStatus(true);
    } catch (err) {
      status.textContent = String(err.message || err);
    }
  });
  return row;
}

const TOKEN_FIELD_BY_SERVICE = {
  discord: { field: "bot_token", placeholder: "Bot token" },
  notion: { field: "integration_secret", placeholder: "Integration secret" },
};

async function loadConnections() {
  const listEl = document.getElementById("connectionsList");
  if (!listEl) return;
  try {
    const [connResp, pluginsResp] = await Promise.all([fetch("/api/connections"), fetch("/api/plugins")]);
    const connections = await connResp.json();
    const plugins = await pluginsResp.json().catch(() => []);
    listEl.innerHTML = "";
    for (const conn of connections) {
      const row = document.createElement("div");
      row.className = "connection-row";

      const label = document.createElement("span");
      label.textContent = conn.label;
      row.appendChild(label);

      if (conn.connected) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "hud-btn";
        btn.textContent = "Connected ✓ (Disconnect)";
        btn.addEventListener("click", async () => {
          await fetch(`/api/connections/${conn.name}/disconnect`, { method: "POST" });
          loadConnections();
        });
        row.appendChild(btn);
      } else if (conn.auth_style === "oauth" && !conn.configured) {
        row.classList.add("connection-row-setup");

        const intro = document.createElement("p");
        intro.className = "hint-text";
        intro.textContent = `Paste the Client ID and Client secret from your ${conn.label.split(" (")[0]} OAuth client below, then click Save. No file editing needed.`;
        row.appendChild(intro);

        if (conn.name === "spotify") {
          const help = document.createElement("div");
          help.className = "setup-steps";
          const redirect = `${window.location.origin}/api/connections/spotify/callback`;
          help.innerHTML =
            "<strong>Setting up Spotify (one time, about 3 minutes)</strong>" +
            "<ol>" +
            '<li>Open the <a href="https://developer.spotify.com/dashboard" target="_blank" rel="noopener">Spotify developer dashboard</a> and press <em>Create app</em>.</li>' +
            "<li>Any name. Under <em>Redirect URIs</em> add exactly this address, tick <em>Web API</em>, and save: " +
            '<code id="spotifyRedirect"></code> <button type="button" class="hud-btn" id="spotifyRedirectCopy">Copy</button></li>' +
            "<li>Open the app's <em>Settings</em>. Copy the <strong>Client ID</strong>, then press <em>View client secret</em> and copy the <strong>Client secret</strong>.</li>" +
            "<li>Paste both below, press <em>Save</em>, then press <em>Connect Spotify</em> and approve.</li>" +
            "</ol>" +
            '<p class="hint-text">These are two values, not an "API key". Spotify Premium is needed for Echo to start playback.</p>';
          row.appendChild(help);
          help.querySelector("#spotifyRedirect").textContent = redirect;
          help.querySelector("#spotifyRedirectCopy").addEventListener("click", async (event) => {
            try {
              await navigator.clipboard.writeText(redirect);
              event.target.textContent = "Copied";
            } catch (err) {
              event.target.textContent = "Select and copy it manually";
            }
          });
        }

        const idLabel = document.createElement("label");
        idLabel.className = "field-row";
        const idSpan = document.createElement("span");
        idSpan.textContent = "Client ID";
        const idInput = document.createElement("input");
        idInput.type = "text";
        idInput.placeholder = "Paste Client ID here";
        idLabel.append(idSpan, idInput);

        const secretLabel = document.createElement("label");
        secretLabel.className = "field-row";
        const secretSpan = document.createElement("span");
        secretSpan.textContent = "Client secret";
        const secretInput = document.createElement("input");
        secretInput.type = "password";
        secretInput.placeholder = "Paste Client secret here";
        secretLabel.append(secretSpan, secretInput);

        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "hud-btn";
        btn.textContent = "Save";
        const saveMsg = document.createElement("span");
        saveMsg.className = "hint-text";
        btn.addEventListener("click", async () => {
          if (!idInput.value.trim() || !secretInput.value.trim()) {
            saveMsg.textContent = "Both fields are required.";
            saveMsg.classList.add("error");
            return;
          }
          const resp = await fetch(`/api/connections/${conn.name}/credentials`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ client_id: idInput.value.trim(), client_secret: secretInput.value.trim() }),
          });
          if (resp.ok) {
            loadConnections();
          } else {
            const data = await resp.json().catch(() => ({}));
            saveMsg.textContent = data.detail || "Save failed - check the backend is running and try again.";
            saveMsg.classList.add("error");
          }
        });

        row.append(idLabel, secretLabel, btn, saveMsg);
      } else if (conn.auth_style === "oauth") {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "hud-btn";
        btn.textContent = "Connect " + conn.label.split(" (")[0];
        btn.addEventListener("click", () => {
          window.location.href = `/api/connections/${conn.name}/start`;
        });
        row.appendChild(btn);
      } else if (conn.auth_style === "token") {
        const spec = TOKEN_FIELD_BY_SERVICE[conn.name];
        const input = document.createElement("input");
        input.type = "password";
        input.placeholder = spec ? spec.placeholder : "Token";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "hud-btn";
        btn.textContent = "Save";
        btn.addEventListener("click", async () => {
          if (!input.value.trim()) return;
          await fetch(`/api/connections/${conn.name}/token`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ [spec.field]: input.value.trim() }),
          });
          loadConnections();
        });
        row.append(input, btn);
      }

      listEl.appendChild(row);

      const relatedPlugin = plugins.find((p) => p.related_connection === conn.name);
      if (relatedPlugin) {
        const pluginHint = document.createElement("p");
        pluginHint.className = "hint-text";
        pluginHint.textContent = conn.connected
          ? `Turn on the "${relatedPlugin.label}" plugin in the Plugins tab to actually use this.`
          : `Once connected, turn on the "${relatedPlugin.label}" plugin in the Plugins tab to use it.`;
        listEl.appendChild(pluginHint);
      }

      if (conn.name === "discord" && conn.invite_url) {
        const inviteP = document.createElement("p");
        inviteP.className = "hint-text";
        const a = document.createElement("a");
        a.href = conn.invite_url;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = "Invite the bot to a server";
        inviteP.appendChild(a);
        listEl.appendChild(inviteP);
      }
    }
  } catch (err) {
    console.error("Failed to load connections:", err);
  }
}

function checkConnectionRedirectResult() {
  const params = new URLSearchParams(window.location.search);
  const service = params.get("google_connect") !== null ? "google"
    : params.get("github_connect") !== null ? "github"
    : params.get("spotify_connect") !== null ? "spotify" : null;
  if (!service) return;
  const result = params.get(`${service}_connect`);
  const msgEl = document.getElementById("connectionsStatusMsg");
  if (msgEl) {
    msgEl.textContent = result === "success"
      ? `${service[0].toUpperCase()}${service.slice(1)} account connected.`
      : `${service[0].toUpperCase()}${service.slice(1)} connection failed: ${params.get("detail") || "unknown error"}`;
  }
  activateTab("connections");
  window.history.replaceState({}, "", window.location.pathname);
}

function activateTab(name) {
  for (const btn of document.querySelectorAll(".tab-btn")) {
    btn.classList.toggle("active", btn.dataset.tab === name);
  }
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.hidden = panel.id !== `tab-${name}`;
  }
  if (name === "audio") {
    onAudioTabActivated();
  } else {
    stopLevelMeter();
  }
  try {
    localStorage.setItem("echo_active_tab", name);
  } catch (err) {
    // storage unavailable - not worth failing over
  }
}

function initTabs() {
  const bar = document.querySelector(".tab-bar");
  if (!bar) return;
  bar.addEventListener("click", (event) => {
    const btn = event.target.closest(".tab-btn");
    if (btn) activateTab(btn.dataset.tab);
  });
  let saved = "settings";
  try {
    saved = localStorage.getItem("echo_active_tab") || "settings";
  } catch (err) {
    // storage unavailable - default to settings
  }
  if (!document.getElementById(`tab-${saved}`)) saved = "settings";
  activateTab(saved);
}

function updateToggleCount() {
  const box = document.getElementById("quickToggles");
  const el = document.getElementById("toggleCount");
  if (!box || !el) return;
  const boxes = [...box.querySelectorAll('input[type="checkbox"]')];
  el.textContent = `${boxes.filter((b) => b.checked).length}/${boxes.length} on`;
}

function decorateToggleTooltips() {
  for (const row of document.querySelectorAll("#quickToggles .switch-row")) {
    const tag = row.querySelector(".vram-tag");
    if (tag && !row.title) row.title = tag.textContent;
  }
}

function initToggleFilter() {
  const input = document.getElementById("toggleFilter");
  const box = document.getElementById("quickToggles");
  if (!input || !box) return;
  const apply = () => {
    const query = input.value.trim().toLowerCase();
    for (const row of box.querySelectorAll(".switch-row, .location-cycle-btn")) {
      row.hidden = query !== "" && !row.textContent.toLowerCase().includes(query);
    }
  };
  input.addEventListener("input", apply);
  box.addEventListener("change", updateToggleCount);
  // Settings and plugin states load asynchronously and flip checkboxes without
  // a change event, so keep the count and filter in step with what's on screen.
  setInterval(() => {
    if (document.hidden) return;
    updateToggleCount();
    decorateToggleTooltips();
    if (input.value) apply();
  }, 2500);
  decorateToggleTooltips();
  updateToggleCount();
}

initRecognition();
initDashboardMicToggle();
initSettingsPanel();
initTabs();
initToggleFilter();
initFileAttach();
initEnrollButton();
initAudioPanel();
initPhoneBookPanel();
loadPlugins();
loadConnections();
loadCanvasOnStartup();
loadModel3DOnStartup();
checkConnectionRedirectResult();
tickClock();
// A hidden tab does no work it can catch up on later: the clock, canvas and 3D checks pause, and the
// voice-status check (which keeps the conversation log complete) slows to every few seconds.
setInterval(() => { if (!document.hidden) tickClock(); }, 1000);

fetch("/api/listener/event")
  .then((r) => r.json())
  .then((ev) => { lastSeenListenerSeq = ev.seq || 0; })
  .catch(() => {});
let hiddenTicks = 0;
setInterval(() => { if (!document.hidden || ++hiddenTicks % 4 === 0) pollListenerStatus(); }, 800);
setInterval(() => { if (!document.hidden) pollCanvasForUpdates(); }, 4000);
setInterval(() => { if (!document.hidden) pollModel3DForUpdates(); }, 4000);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  tickClock();
  pollListenerStatus();
  pollCanvasForUpdates();
  pollModel3DForUpdates();
});