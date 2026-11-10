// Echo's orb dashboard. Everything the old dashboard did still happens in app.js (same element
// ids, same functions); this file draws the new look around it: the 3D orb and its feature
// moons, the two arrows, Mission Control's sections, the Switches page, the Live panel and
// Appearance. Classic look: classic.html.
import * as THREE from "three";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ago = (t) => {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
};
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} ${r.status}`);
  return r.json();
}

// --------------------------------------------------------------------------------------
// Every switch: what it's called, where it lives, what it does, its moon color, its tools
// --------------------------------------------------------------------------------------
const CATEGORIES = [
  ["echo", "Echo itself"], ["senses", "Eyes, ears & location"], ["phone", "Phone"], ["media", "Music & TV"],
  ["pc", "Your PC"], ["make", "Making things"], ["organize", "Organizing"], ["accounts", "Your accounts"], ["other", "Other"],
];
const FEATURES = {
  ai_enabled: { cat: "echo", name: "Echo", desc: "The master switch. Off: Echo stays silent and the orb sleeps.", color: "#5ee08a", moon: false },
  "dashboard-mic": { cat: "echo", name: "Listen through this tab too", desc: "Usually off - the always-on listener already hears you. On can make Echo answer twice.", color: "#9be7b5", special: "mic" },
  helper_brain_enabled: { cat: "echo", name: "Echo Mini", desc: "On: the full Echo is kept for you, and everyone else (callers, contacts) talks to Echo Mini - a small brain that loads only while it's used (about 3 GB). Off: two full Echos at once, so callers get the full Echo too - about twice the brain's context memory (see Setup). Switching restarts the brain.", color: "#c3ff6b" },
  agent_suggestions_enabled: { cat: "echo", name: "Echo suggests projects", desc: "After you've been away a while Echo may suggest one project. Only a suggestion - you approve it.", color: "#ffd166", tools: [] },
  vr_mode_enabled: { cat: "echo", name: "VR mode", desc: "Trusts a VR headset's microphone. Needs an Echo restart.", color: "#e5a3ff" },
  screen_access: { cat: "senses", name: "See the screen", desc: "Echo can look at your screen when you ask. Loads the 6 GB vision model when used.", color: "#4cc9f0", tools: ["view_screen"] },
  camera_access: { cat: "senses", name: "See the camera", desc: "Echo can look through your webcam when you ask, and the Camera section works.", color: "#3a86ff", tools: ["view_camera"] },
  desk_guard_enabled: { cat: "senses", name: "Desk Guard", desc: "Locks the PC if someone it doesn't recognize is at it. Learns your face as you unlock.", color: "#ff6b6b" },
  location_mode: { cat: "senses", name: "Location", desc: "Off, PC or Phone - only the device you pick shares where it is.", color: "#06d6a0", special: "location", tools: ["get_location"] },
  calling_enabled: { cat: "phone", name: "Phone calls", desc: "Echo can place calls for you and answer calls. Needs the phone line set up.", color: "#00f5d4", tools: ["call_phone_number", "hang_up_call"] },
  call_notifications_enabled: { cat: "phone", name: "Unanswered-call alerts", desc: "Tells you when a call Echo placed wasn't answered.", color: "#72efdd" },
  sir_page_enabled: { cat: "phone", name: "Sir Page", desc: "Echo phones you when something happens: a caller turned away, a contact on a call, Desk Guard locking.", color: "#e6b84a" },
  screen_unknown_callers: { cat: "phone", name: "Screen unknown callers", desc: "Numbers not in your phone book are asked who they are, then saved with calling off (scams in red).", color: "#f4a261" },
  spotify_control: { cat: "media", name: "Spotify", desc: "Play songs, playlists and your liked songs; pause, skip, seek, volume.", color: "#1ed760", tools: ["spotify_play", "spotify_my_playlists", "spotify_playback", "spotify_now_playing"] },
  speaker_enabled: { cat: "media", name: "Speaker", desc: "White noise and wake-up alarms play on a speaker you pick (Setup -> Sound outputs); Echo's voice stays on your headphones.", color: "#ffb4a2" },
  speaker_sounds: { cat: "media", name: "White noise & alarms", desc: "\"Play white noise\", \"rain sounds for an hour\", \"wake me up at 7\" - a chime, then Echo says the time and your day.", color: "#cdb4ff", tools: ["play_noise", "stop_sounds", "set_alarm", "list_alarms", "cancel_alarm"] },
  media_keys: { cat: "media", name: "Media keys", desc: "Play/pause, skip and volume for whatever is playing on the PC.", color: "#80ed99", tools: ["media_control"] },
  netflix_play: { cat: "media", name: "Netflix on the PC", desc: "Put shows on Netflix in your browser by name; pause, skip, fullscreen.", color: "#e50914", tools: ["netflix_play", "netflix_control"] },
  tv_control: { cat: "media", name: "TV", desc: "YouTube, live streams and Netflix on your Google TV; pause, volume, skip, sleep timer.", color: "#ff595e", tools: ["tv_youtube_creator", "tv_play_youtube", "tv_play_netflix", "tv_open_app", "tv_control", "tv_seek", "tv_whats_on", "tv_look", "tv_set_volume", "tv_sleep_timer"] },
  app_launcher: { cat: "pc", name: "Open apps", desc: "Opens installed apps by name.", color: "#8ecae6", tools: ["open_app"] },
  app_closer: { cat: "pc", name: "See & close apps", desc: "Says what's open and closes apps (politely first; force close only if you say so).", color: "#219ebc", tools: ["list_running_apps", "close_app"] },
  app_shortcuts: { cat: "pc", name: "Website shortcuts", desc: "Opens saved sites like YouTube or Netflix; add your own.", color: "#90e0ef", tools: ["open_shortcut", "add_shortcut", "remove_shortcut", "list_shortcuts"] },
  steam_launcher: { cat: "pc", name: "Steam games", desc: "Launches your Steam games by name.", color: "#66c0f4", tools: ["launch_steam_game"] },
  pc_stats: { cat: "pc", name: "PC stats & self-report", desc: "Ask how the PC is doing (processor, memory, graphics card heat, drive space) or how Echo itself is running.", color: "#7ce0c3", tools: ["pc_stats", "echo_self_report"] },
  pc_power: { cat: "pc", name: "Shut down the PC", desc: "Up to 5 a day, only after you say your passcode. One minute to cancel.", color: "#ff4d6d", special: "passcode", tools: ["shutdown_pc", "cancel_shutdown"] },
  browser_control_enabled: { cat: "pc", name: "Browser control", desc: "Echo goes to addresses, searches, switches and closes tabs, goes back, and clicks and types with its own cursor - never your mouse.", color: "#a0c4ff", tools: ["browser_scan_page", "browser_read_page", "browser_click", "browser_type", "browser_scroll", "browser_go_to", "browser_history", "browser_list_tabs", "browser_switch_tab", "browser_close_tab", "browser_press_key"] },
  browser_pixel_fallback_enabled: { cat: "pc", name: "Browser: click by sight", desc: "For pages it can't read normally. Uses the vision model; asks you first.", color: "#bdb2ff", tools: ["browser_click_by_sight"] },
  code_canvas_enabled: { cat: "make", name: "Canvas", desc: "Quick pages, animations and demos, shown live in the Canvas section.", color: "#ffbe0b", tools: ["write_canvas_code"] },
  projects: { cat: "make", name: "Projects", desc: "Bigger jobs Echo builds by itself in a sandbox and checks - with your approval.", color: "#fb8500", tools: ["start_project", "project_status", "list_projects", "stop_project", "list_requests", "approve_request", "deny_request"] },
  image_generator: { cat: "make", name: "Pictures", desc: "Makes images with your local Stable Diffusion. 4-6 GB while working.", color: "#f72585", tools: ["generate_image"] },
  model_3d_generator: { cat: "make", name: "3D models", desc: "Turns a picture into a 3D model you can spin. Heavy: most of the graphics card and about 6 minutes - Echo's brain steps aside meanwhile.", color: "#b5179e", tools: ["generate_3d_model"] },
  address_book: { cat: "organize", name: "Address book", desc: "Named places - home, gym - for addresses and directions.", color: "#2ec4b6", tools: ["save_address", "get_address", "list_addresses", "forget_address", "directions"] },
  github_repos: { cat: "organize", name: "GitHub page", desc: "Repos you save by name: \"check the echo GitHub\" reads one, \"go to the echo GitHub\" opens it.", color: "#c9d1d9", tools: ["check_github_repo", "open_github_repo", "save_github_repo", "list_github_repos", "forget_github_repo"] },
  calendar_events: { cat: "organize", name: "Calendar", desc: "Your events - name, day, time, place, what it's about. Echo adds, changes and reads them, and says reminders out loud.", color: "#ff9e7a", tools: ["calendar_add_event", "calendar_events", "calendar_change_event", "calendar_remove_event"] },
  billing_tracker: { cat: "organize", name: "Spending tracker", desc: "Logs what you spend and sums it up.", color: "#cdb4db", tools: ["log_expense", "get_spending_summary"] },
  google_workspace: { cat: "accounts", name: "Google", desc: "Reads Gmail, Calendar and Drive; can make Slides. Needs Google connected.", color: "#4285f4", tools: ["search_gmail", "list_calendar_events", "search_drive_files", "create_google_slides"] },
  github_connect: { cat: "accounts", name: "GitHub", desc: "Reads your repos' issues and activity.", color: "#c9d1d9", tools: ["list_repo_issues", "list_repo_pull_requests", "get_repo_activity"] },
  discord_connect: { cat: "accounts", name: "Discord", desc: "Reads your servers and channels.", color: "#5865f2", tools: ["list_discord_servers", "list_discord_channels", "read_discord_channel"] },
  notion_connect: { cat: "accounts", name: "Notion", desc: "Searches your Notion pages.", color: "#f2f2f2", tools: ["search_notion"] },
};
const CORE_KEYS = ["ai_enabled", "screen_access", "camera_access", "desk_guard_enabled", "calling_enabled", "call_notifications_enabled",
  "sir_page_enabled", "screen_unknown_callers", "agent_suggestions_enabled", "helper_brain_enabled", "speaker_enabled", "browser_control_enabled",
  "browser_pixel_fallback_enabled", "code_canvas_enabled", "vr_mode_enabled"];

// switches state: { key: { on, kind: setting|plugin|mic|location, label, cost, plugin } }
let switches = {};
let settingsCache = {};

async function loadSwitches() {
  const [settings, plugins] = await Promise.all([getJSON("/api/settings"), getJSON("/api/plugins")]);
  settingsCache = settings;
  const next = {};
  for (const key of CORE_KEYS) next[key] = { on: Boolean(settings[key]), kind: "setting" };
  next["dashboard-mic"] = { on: localStorageGet("echo_dashboard_mic_enabled") === "true", kind: "mic" };
  next.location_mode = { on: (settings.location_mode || "off") !== "off", kind: "location", mode: settings.location_mode || "off" };
  for (const p of plugins) {
    next[p.name] = { on: Boolean(p.enabled) || p.always_on, kind: "plugin", label: p.label, cost: p.vram_cost, alwaysOn: p.always_on, needs: p.related_connection };
  }
  switches = next;
  return next;
}

function localStorageGet(key) {
  try { return localStorage.getItem(key); } catch (err) { return null; }
}
function localStorageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (err) { /* private window */ }
}

function featureInfo(key) {
  const known = FEATURES[key];
  if (known) return known;
  const s = switches[key] || {};
  return { cat: "other", name: s.label || key, desc: s.cost ? `Uses ${s.cost}.` : "", color: "#9aa5ff" };
}
function moonColor(key) {
  return (appearance.moons && appearance.moons[key]) || featureInfo(key).color;
}

async function setSwitch(key, on) {
  const s = switches[key];
  if (!s) return;
  if (s.kind === "setting") {
    await window.updateSetting(key, on); // app.js: saves, plus the restart/canvas side effects
  } else if (s.kind === "mic") {
    window.setDashboardMic(on);
  } else if (s.kind === "plugin") {
    const resp = await fetch(`/api/plugins/${encodeURIComponent(key)}/toggle`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: on }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      $("pluginsError").textContent = data.detail || `Couldn't change ${featureInfo(key).name}.`;
    }
  }
  await loadSwitches();
  refreshMoons();
  if (currentSection === "switches") renderSwitches();
}

// --------------------------------------------------------------------------------------
// Appearance: presets, colors, orb settings - saved on the server so every browser matches
// --------------------------------------------------------------------------------------
const PRESETS = {
  "Echo Green": { bg: "#050807", panel: "#0c1612", accent: "#5ee08a", text: "#dcefe4", orb: "#5ee08a", rings: "#5ee08a" },
  "Midnight Gold": { bg: "#070912", panel: "#111526", accent: "#e6b84a", text: "#ece4d3", orb: "#e6b84a", rings: "#c9a35a" },
  Arctic: { bg: "#050a10", panel: "#0d1724", accent: "#6cc9ff", text: "#e3f1ff", orb: "#7fd4ff", rings: "#a8e1ff" },
  Ember: { bg: "#0d0705", panel: "#1a0f0b", accent: "#ff8a4c", text: "#ffe8dc", orb: "#ff7a3d", rings: "#ffb07a" },
  Violet: { bg: "#08060f", panel: "#140f22", accent: "#b18cff", text: "#ece6ff", orb: "#a77bff", rings: "#cdb6ff" },
  Mono: { bg: "#0a0a0a", panel: "#151515", accent: "#e6e6e6", text: "#f2f2f2", orb: "#d9d9d9", rings: "#9a9a9a" },
};
const COLOR_FIELDS = [
  ["bg", "Background"], ["panel", "Panels"], ["accent", "App color (buttons, headings)"], ["text", "Text"],
  ["orb", "Orb"], ["rings", "Orb rings"],
];
let appearance = { colors: { ...PRESETS["Echo Green"] }, moons: {}, orbSize: 1, float: 1, spin: 1, layout: "orb", preset: "Echo Green" };

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function mix(a, b, t) {
  const x = hexToRgb(a), y = hexToRgb(b);
  return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("");
}
function rgba(hex, alpha) {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function applyAppearance() {
  const c = appearance.colors;
  const root = document.documentElement.style;
  root.setProperty("--ui-bg", c.bg);
  root.setProperty("--ui-panel", rgba(c.panel, 0.94));
  root.setProperty("--ui-tile", mix(c.panel, c.accent, 0.04));
  root.setProperty("--ui-tile-hover", mix(c.panel, c.accent, 0.1));
  root.setProperty("--ui-line", mix(c.panel, c.accent, 0.22));
  root.setProperty("--ui-accent", c.accent);
  root.setProperty("--ui-accent-soft", rgba(c.accent, 0.45));
  root.setProperty("--ui-accent-faint", rgba(c.accent, 0.08));
  root.setProperty("--ui-text", c.text);
  root.setProperty("--ui-dim", mix(c.text, c.bg, 0.45));
  // the shared component styles from the old dashboard
  root.setProperty("--accent", c.accent);
  root.setProperty("--accent-dim", rgba(c.accent, 0.35));
  root.setProperty("--dim", mix(c.text, c.bg, 0.45));
  root.setProperty("--sand", c.text);
  root.setProperty("--bg", c.bg);
  root.setProperty("--panel", rgba(c.panel, 0.65));
  if (orbScene) orbScene.setColors(c.orb, c.rings);
  refreshMoons();
}

let saveTimer = null;
function saveAppearance() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    fetch("/api/appearance", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(appearance) }).catch(() => {});
  }, 400);
}

async function loadAppearance() {
  try {
    const saved = await getJSON("/api/appearance");
    if (saved.layout === "classic") {
      localStorageSet("echo_layout", "classic");
      location.replace("classic.html");
      return;
    }
    appearance = { ...appearance, ...saved, colors: { ...appearance.colors, ...(saved.colors || {}) }, moons: { ...(saved.moons || {}) } };
  } catch (err) { /* defaults */ }
  localStorageSet("echo_layout", "orb");
  applyAppearance();
}

function renderAppearance() {
  const box = $("appearanceBox");
  const presets = Object.keys(PRESETS).map((name) => {
    const p = PRESETS[name];
    return `<button type="button" class="ui-preset${appearance.preset === name ? " active" : ""}" data-preset="${esc(name)}"
      style="--p-bg:${p.bg};--p-acc:${p.accent};--p-orb:${p.orb}"><span class="pv"><i></i></span>${esc(name)}</button>`;
  }).join("");
  const colors = COLOR_FIELDS.map(([k, label]) => `
    <label class="ui-color"><input type="color" data-color="${k}" value="${appearance.colors[k]}" /><span>${esc(label)}</span></label>`).join("");
  const onKeys = Object.keys(FEATURES).filter((k) => FEATURES[k].moon !== false);
  const moons = onKeys.map((k) => `
    <label class="ui-color small"><input type="color" data-moon="${k}" value="${moonColor(k)}" /><span>${esc(FEATURES[k].name)}</span></label>`).join("");
  box.innerHTML = `
    <div class="ui-card" data-tab="Themes"><h3 class="ui-h3">Themes</h3><div class="ui-presets">${presets}</div></div>
    <div class="ui-card" data-tab="Colors"><h3 class="ui-h3">Colors</h3><div class="ui-colors">${colors}</div></div>
    <div class="ui-card" data-tab="Orb"><h3 class="ui-h3">The orb</h3>
      <label class="ui-slider"><span>Size</span><input type="range" min="0.6" max="1.4" step="0.05" data-num="orbSize" value="${appearance.orbSize}" /></label>
      <label class="ui-slider"><span>How much it floats around</span><input type="range" min="0" max="2" step="0.1" data-num="float" value="${appearance.float}" /></label>
      <label class="ui-slider"><span>Spin speed</span><input type="range" min="0" max="3" step="0.1" data-num="spin" value="${appearance.spin}" /></label>
    </div>
    <div class="ui-card" data-tab="Moons"><h3 class="ui-h3">Feature moons</h3><p class="hint-text">Each switch that's on gets a small orb in its color.</p><div class="ui-colors">${moons}</div></div>
    <div class="ui-card" data-tab="Seasons"><h3 class="ui-h3">Seasons</h3>${seasonsCard()}</div>
    <div class="ui-card" data-tab="Interface"><h3 class="ui-h3">Interface</h3>
      <div class="ui-row-wrap"><button type="button" class="ui-btn primary" disabled>Orb (this one)</button>
      <button type="button" class="ui-btn" id="goClassic">Classic - the old look</button>
      <button type="button" class="ui-btn" id="resetLook">Reset to Echo Green</button></div>
    </div>`;
  box.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
    appearance.preset = b.dataset.preset;
    appearance.colors = { ...PRESETS[b.dataset.preset] };
    applyAppearance(); saveAppearance(); renderAppearance();
  }));
  setupSubtabs(box.closest(".ui-section"));
  box.querySelectorAll("[data-color]").forEach((i) => i.addEventListener("input", () => {
    appearance.colors[i.dataset.color] = i.value;
    appearance.preset = "Custom";
    applyAppearance(); saveAppearance();
  }));
  box.querySelectorAll("[data-moon]").forEach((i) => i.addEventListener("input", () => {
    appearance.moons[i.dataset.moon] = i.value;
    refreshMoons(true); saveAppearance();
  }));
  box.querySelectorAll("[data-num]").forEach((i) => i.addEventListener("input", () => {
    appearance[i.dataset.num] = parseFloat(i.value);
    saveAppearance();
  }));
  $("goClassic").addEventListener("click", async () => {
    appearance.layout = "classic";
    localStorageSet("echo_layout", "classic");
    await fetch("/api/appearance", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ layout: "classic" }) }).catch(() => {});
    location.href = "classic.html";
  });
  $("resetLook").addEventListener("click", () => {
    if (Seasons && appearance.season) Seasons.turnOff(appearance, applyAppearance, saveAppearance);
    appearance = { ...appearance, colors: { ...PRESETS["Echo Green"] }, moons: {}, orbSize: 1, float: 1, spin: 1, preset: "Echo Green" };
    applyAppearance(); saveAppearance(); renderAppearance();
  });
  box.querySelectorAll("[data-season-on]").forEach((b) => b.addEventListener("click", async () => {
    const season = Seasons.SEASONS.find((s) => s.id === b.dataset.seasonOn);
    await Seasons.turnOn(season, appearance, applyAppearance, saveAppearance, $("seasonCardMusic")?.checked, true);
    renderAppearance();
  }));
  box.querySelector("[data-season-off]")?.addEventListener("click", () => {
    Seasons.turnOff(appearance, applyAppearance, saveAppearance);
    renderAppearance();
  });
  box.querySelectorAll("[data-season-flag]").forEach((i) => i.addEventListener("change", () => {
    appearance[i.dataset.seasonFlag] = i.checked;
    saveAppearance();
    if (i.dataset.seasonFlag === "seasonEffects" && appearance.season) {
      const active = Seasons.SEASONS.find((s) => s.id === appearance.season);
      i.checked ? Seasons.startEffect(active) : Seasons.stopEffect();
    }
  }));
}

// --------------------------------------------------------------------------------------
// Seasons (seasons.js, a small module loaded after the page is up)
// --------------------------------------------------------------------------------------
let Seasons = null;
let seasonSpotify = false;

function seasonsCard() {
  if (!Seasons) return `<p class="hint-text">Loading...</p>`;
  const now = Seasons.seasonFor();
  const rows = Seasons.SEASONS.map((s) => {
    const on = appearance.season === s.id;
    return `<div class="season-row${on ? " on" : ""}" style="--s-acc:${s.colors.accent}">
      <span class="season-emoji" aria-hidden="true">${s.emoji}</span>
      <span class="season-text"><b>${esc(s.name)}</b><small>${esc(Seasons.dates(s))} - ${esc(s.blurb)}${now === s ? " (now!)" : ""}</small></span>
      ${on ? `<button type="button" class="ui-btn" data-season-off>Turn off</button>`
           : `<button type="button" class="ui-btn" data-season-on="${s.id}">Turn on</button>`}
    </div>`;
  }).join("");
  return `<p class="hint-text">When you open Echo during one of these, it asks once if you'd like the seasonal look. Your own colors come back when the season ends.</p>
    ${rows}
    <label class="ui-check"><input type="checkbox" data-season-flag="seasonPrompts" ${appearance.seasonPrompts !== false ? "checked" : ""} /> Ask me when a season starts</label>
    <label class="ui-check"><input type="checkbox" data-season-flag="seasonEffects" ${appearance.seasonEffects !== false ? "checked" : ""} /> Snow, confetti and other falling effects</label>
    ${seasonSpotify ? `<label class="ui-check"><input type="checkbox" id="seasonCardMusic" /> Play the season's song on Spotify when I turn one on here</label>` : ""}`;
}

async function startSeasons() {
  try {
    Seasons = await import("./seasons.js");
    const preview = new URLSearchParams(location.search).get("season");
    await Seasons.onLoad(appearance, applyAppearance, saveAppearance, preview);
    seasonSpotify = await getJSON("/api/seasonal/music").then((r) => r.spotify).catch(() => false);
    if (currentSection === "appearance") renderAppearance();
  } catch (err) { /* seasonal looks are a nicety - never break the dashboard over them */ }
}

// --------------------------------------------------------------------------------------
// The orb
// --------------------------------------------------------------------------------------
let orbScene = null;
let orbMode = "idle"; // idle | listening | thinking | speaking | night

function makeGlowTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,255,255,0.55)");
  grad.addColorStop(0.35, "rgba(255,255,255,0.16)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
}

function buildOrb() {
  const stage = $("orbStage");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  stage.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
  camera.position.set(0, 0, 9);
  const group = new THREE.Group();
  scene.add(group);
  const glowTex = makeGlowTexture();

  const coreColor = new THREE.Color(appearance.colors.orb);
  const ringColor = new THREE.Color(appearance.colors.rings);
  const nightColor = new THREE.Color("#2c3f94");
  const nightRim = new THREE.Color("#c9d2ff");

  const coreMat = new THREE.ShaderMaterial({
    transparent: true,
    uniforms: { uTime: { value: 0 }, uGlow: { value: 1 }, uColor: { value: coreColor.clone() }, uHot: { value: new THREE.Color("#ffffff") }, uNight: { value: 0 } },
    vertexShader: `varying vec3 vN; varying vec3 vV; varying vec3 vP;
      void main(){ vec4 mv = modelViewMatrix * vec4(position,1.0); vN = normalize(normalMatrix*normal); vV = normalize(-mv.xyz); vP = position; gl_Position = projectionMatrix*mv; }`,
    fragmentShader: `uniform float uTime; uniform float uGlow; uniform vec3 uColor; uniform vec3 uHot; uniform float uNight;
      varying vec3 vN; varying vec3 vV; varying vec3 vP;
      void main(){
        float rim = pow(1.0 - max(dot(vN, vV), 0.0), 2.2);
        float bands = 0.5 + 0.5 * sin(vP.y * 9.0 + uTime * 1.6) * sin(vP.x * 7.0 - uTime * 1.1);
        vec3 hot = mix(uColor, uHot, 0.55);
        vec3 col = mix(uColor * 0.32, hot, rim * 0.9 + bands * 0.18 * uGlow);
        col *= (0.75 + 0.5 * uGlow) * (1.0 - 0.35 * uNight);
        gl_FragColor = vec4(col, 0.55 + rim * 0.45);
      }`,
  });
  const core = new THREE.Mesh(new THREE.SphereGeometry(1, 64, 64), coreMat);
  group.add(core);
  const shellMat = new THREE.MeshBasicMaterial({ color: coreColor.clone(), wireframe: true, transparent: true, opacity: 0.18 });
  const shell = new THREE.Mesh(new THREE.IcosahedronGeometry(1.08, 2), shellMat);
  group.add(shell);
  const glowMat = new THREE.SpriteMaterial({ map: glowTex, color: coreColor.clone(), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
  const glow = new THREE.Sprite(glowMat);
  glow.scale.set(6, 6, 1);
  group.add(glow);

  // stars for the night look
  const starGeo = new THREE.BufferGeometry();
  const starPos = new Float32Array(240 * 3);
  for (let i = 0; i < 240; i++) {
    starPos[i * 3] = (Math.random() - 0.5) * 28;
    starPos[i * 3 + 1] = (Math.random() - 0.5) * 16;
    starPos[i * 3 + 2] = -4 - Math.random() * 6;
  }
  starGeo.setAttribute("position", new THREE.BufferAttribute(starPos, 3));
  const starMat = new THREE.PointsMaterial({ color: "#dfe6ff", size: 0.05, transparent: true, opacity: 0 });
  scene.add(new THREE.Points(starGeo, starMat));

  const ringSpecs = [
    { r: 1.75, tilt: [1.15, 0.2, 0], speed: 0.55 },
    { r: 2.25, tilt: [0.35, -0.9, 0.3], speed: -0.38 },
    { r: 2.75, tilt: [-0.6, 0.5, -0.4], speed: 0.26 },
  ];
  const rings = ringSpecs.map((spec) => {
    const holder = new THREE.Group();
    holder.rotation.set(...spec.tilt);
    const mat = new THREE.MeshBasicMaterial({ color: ringColor.clone(), transparent: true, opacity: 0.55 });
    holder.add(new THREE.Mesh(new THREE.TorusGeometry(spec.r, 0.012, 8, 180), mat));
    group.add(holder);
    return { holder, mat, spec, moons: [] };
  });

  // feature moons: key -> { mesh, halo, ring, phase, target, scale }
  const moons = new Map();
  function syncMoons(list) {
    const wanted = new Set(list.map((m) => m.key));
    for (const [key, m] of moons) if (!wanted.has(key)) m.target = 0;
    list.forEach((item, idx) => {
      let m = moons.get(item.key);
      const ring = rings[idx % 3];
      if (!m) {
        const mesh = new THREE.Mesh(new THREE.SphereGeometry(0.085, 18, 18), new THREE.MeshBasicMaterial({ color: item.color, transparent: true }));
        const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: item.color, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
        halo.scale.set(0.8, 0.8, 1);
        mesh.add(halo);
        mesh.userData.key = item.key;
        mesh.scale.setScalar(0.001);
        ring.holder.add(mesh);
        m = { mesh, halo, ring, phase: 0, target: 1, scale: 0.001, key: item.key, name: item.name };
        moons.set(item.key, m);
      }
      if (m.ring !== ring) { m.ring.holder.remove(m.mesh); ring.holder.add(m.mesh); m.ring = ring; }
      m.target = 1;
      m.name = item.name;
      m.mesh.material.color.set(item.color);
      m.halo.material.color.set(item.color);
    });
    // spread each ring's moons evenly
    for (const ring of rings) {
      const onRing = [...moons.values()].filter((m) => m.ring === ring && m.target > 0);
      onRing.forEach((m, i) => { m.phase = (i / Math.max(1, onRing.length)) * Math.PI * 2; });
    }
  }

  function setColors(orbHex, ringHex) {
    coreColor.set(orbHex);
    ringColor.set(ringHex);
  }

  function resize() {
    const w = window.innerWidth, h = window.innerHeight;
    renderer.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize);
  resize();

  // click the orb: the talk box. Hover a moon: its feature's name.
  const ray = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const tip = document.createElement("div");
  tip.className = "ui-moontip";
  tip.hidden = true;
  document.body.appendChild(tip);
  function pick(e) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    ray.setFromCamera(pointer, camera);
  }
  renderer.domElement.addEventListener("click", (e) => {
    if (currentLevel !== "orb") return;
    pick(e);
    if (ray.intersectObjects([core, glow]).length) toggleTalk();
  });
  renderer.domElement.addEventListener("mousemove", (e) => {
    pick(e);
    const hits = ray.intersectObjects([...moons.values()].map((m) => m.mesh), false);
    if (hits.length && currentLevel === "orb") {
      const m = moons.get(hits[0].object.userData.key);
      tip.textContent = m ? m.name : "";
      tip.style.left = `${e.clientX + 14}px`;
      tip.style.top = `${e.clientY + 10}px`;
      tip.hidden = false;
      renderer.domElement.style.cursor = "help";
    } else {
      tip.hidden = true;
      const overCore = ray.intersectObjects([core]).length && currentLevel === "orb";
      renderer.domElement.style.cursor = overCore ? "pointer" : "default";
    }
  });

  const clock = new THREE.Clock();
  let spin = 1, glowLevel = 1, pulse = 0, night = 0, lastDraw = 0;
  const tmp = new THREE.Color();
  function frame(now = 0) {
    requestAnimationFrame(frame);
    // Behind an open panel the orb is blurred: ~20 frames a second is plenty there, and redrawing
    // (and re-blurring) it 60 times a second was the dashboard's biggest steady cost.
    if (document.body.classList.contains("panel-open") && now - lastDraw < 48) return;
    lastDraw = now;
    const dt = Math.min(clock.getDelta(), 0.1);
    const t = clock.elapsedTime;
    const motion = reduce ? 0.15 : 1;
    const want = { idle: [1, 1, 0], listening: [2.2, 1.5, 0.03], thinking: [2.8, 1.6, 0.02], speaking: [1.4, 1.8, 0.09], night: [0.3, 0.6, 0] }[orbMode];
    spin += (want[0] - spin) * 0.05;
    glowLevel += (want[1] - glowLevel) * 0.05;
    pulse += (want[2] - pulse) * 0.05;
    night += ((orbMode === "night" ? 1 : 0) - night) * 0.03;
    const userSpin = appearance.spin ?? 1;

    // drift around the screen, clear of the arrows
    const halfH = Math.tan((camera.fov * Math.PI) / 360) * camera.position.z;
    const halfW = halfH * camera.aspect;
    const drift = (appearance.float ?? 1) * motion * (1 - 0.6 * night);
    group.position.x = Math.sin(t * 0.13) * Math.max(0, halfW - 4.2) * 0.55 * drift;
    group.position.y = Math.sin(t * 0.19 + 1.3) * Math.max(0, halfH - 3.6) * 0.5 * drift + Math.sin(t * 1.1) * 0.08 * drift;
    const size = (appearance.orbSize ?? 1) * (Math.min(1, window.innerWidth / 900) * 0.95 + 0.05);
    group.scale.setScalar(size);

    core.rotation.y += 0.25 * dt * spin * userSpin * motion;
    shell.rotation.y -= 0.16 * dt * spin * userSpin * motion;
    shell.rotation.x += 0.08 * dt * spin * userSpin * motion;
    const breathe = night > 0.5 ? Math.sin(t * 0.9) * 0.04 * night : 0;
    core.scale.setScalar(1 + Math.sin(t * 7) * pulse + breathe);

    tmp.copy(coreColor).lerp(nightColor, night);
    coreMat.uniforms.uColor.value.copy(tmp);
    coreMat.uniforms.uHot.value.set("#ffffff").lerp(nightRim, night);
    coreMat.uniforms.uTime.value = t;
    coreMat.uniforms.uGlow.value = glowLevel;
    coreMat.uniforms.uNight.value = night;
    shellMat.color.copy(tmp);
    glowMat.color.copy(tmp);
    glowMat.opacity = (0.55 + 0.25 * (glowLevel - 1)) * (1 - 0.4 * night);
    starMat.opacity = 0.75 * night * (0.85 + 0.15 * Math.sin(t * 2));

    for (const ring of rings) {
      ring.holder.rotation.z += 0.09 * dt * spin * userSpin * motion;
      ring.mat.color.copy(ringColor).lerp(nightRim, night * 0.7);
      ring.mat.opacity = 0.55 - 0.37 * night;
      ring.holder.scale.setScalar(1 - 0.18 * night);
    }
    for (const [key, m] of moons) {
      m.scale += (m.target - m.scale) * 0.08;
      if (m.target === 0 && m.scale < 0.02) {
        m.ring.holder.remove(m.mesh);
        moons.delete(key);
        continue;
      }
      const a = m.phase + t * m.ring.spec.speed * spin * userSpin * motion;
      m.mesh.position.set(Math.cos(a) * m.ring.spec.r, Math.sin(a) * m.ring.spec.r, 0);
      m.mesh.scale.setScalar(Math.max(0.001, m.scale * (1 - 0.35 * night)));
      m.mesh.material.opacity = 1 - 0.55 * night;
      m.halo.material.opacity = 1 - 0.6 * night;
    }
    renderer.render(scene, camera);
  }
  requestAnimationFrame(frame);
  return { syncMoons, setColors };
}

function refreshMoons() {
  if (!orbScene) return;
  const list = Object.entries(switches)
    .filter(([key, s]) => s.on && featureInfo(key).moon !== false && key !== "dashboard-mic")
    .map(([key]) => ({ key, color: moonColor(key), name: featureInfo(key).name }));
  orbScene.syncMoons(list);
}

// --------------------------------------------------------------------------------------
// Orb state, caption and the talk box
// --------------------------------------------------------------------------------------
let live = null;
function updateOrbMode() {
  const body = document.body.classList;
  let mode = "idle";
  if (live && (!live.echo.enabled || live.echo.phase === "asleep")) mode = "night";
  else if (body.contains("speaking")) mode = "speaking";
  else if (body.contains("thinking")) mode = "thinking";
  else if (live && live.mic && live.mic.alive && live.mic.level >= 500) mode = "listening";
  orbMode = mode;
  const label = { idle: "Listening", listening: "Hearing you", thinking: "Thinking", speaking: "Talking", night: "Sleeping" }[mode];
  $("orbStateLabel").textContent = label;
  if (mode === "night") {
    $("captionWords").textContent = live && !live.echo.enabled ? "Echo is switched off." : "Say “Echo” to wake me.";
  }
}
new MutationObserver(updateOrbMode).observe(document.body, { attributes: true, attributeFilter: ["class"] });

// what Echo heard / said, from the old dashboard's log line
new MutationObserver(() => {
  const text = $("log").textContent || "";
  if (!text || /microphone is off|say "echo"/i.test(text)) return;
  $("captionWords").textContent = text.replace(/^(You \(voice\)|You|Echo):\s*/, (m, who) => (who.startsWith("You") ? "“" : "")) + (/^You/.test(text) ? "”" : "");
}).observe($("log"), { childList: true, characterData: true, subtree: true });

function toggleTalk(force) {
  const form = $("talkForm");
  const show = force !== undefined ? force : form.hidden;
  form.hidden = !show;
  if (show) $("talkInput").focus();
}
function bindTalk(formId, inputId) {
  $(formId).addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $(inputId).value.trim();
    if (!text) return;
    $(inputId).value = "";
    $("captionWords").textContent = `“${text}”`;
    window.sendToEcho(text);
    if (formId === "talkForm") toggleTalk(false);
  });
}

// --------------------------------------------------------------------------------------
// Navigation: the two arrows, Mission Control, sections
// --------------------------------------------------------------------------------------
const TILES = [
  { id: "talk", name: "Talk", icon: "chat" }, { id: "switches", name: "Switches", icon: "toggle" },
  { id: "phone", name: "Phone Book", icon: "phone" }, { id: "places", name: "Address Book", icon: "pin" },
  { id: "github", name: "GitHub", icon: "github" }, { id: "calendar", name: "Calendar", icon: "calendar" },
  { id: "projects", name: "Projects", icon: "cube" }, { id: "thinking", name: "Thinking", icon: "brain" },
  { id: "camera", name: "Camera", icon: "camera" }, { id: "canvas", name: "Canvas", icon: "canvas" },
  { id: "model3d", name: "3D Models", icon: "model" }, { id: "photos", name: "Photos", icon: "photo" },
  { id: "connections", name: "Connections", icon: "link" }, { id: "hearing", name: "Hearing", icon: "mic" },
  { id: "setup", name: "Setup", icon: "wrench" }, { id: "appearance", name: "Appearance", icon: "palette" },
  { id: "help", name: "Help", icon: "help" },
];
// Echo's own icon set: each glyph sits in a small orbit ring (like the orb) with a moon that
// circles it on hover; drawn two-tone (a soft fill under a crisp line) in its tile's own color.
const GLYPHS = {
  talk: '<path class="i-fill" d="M9 11h15a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3h-7l-5 4v-4H9a3 3 0 0 1-3-3v-7a3 3 0 0 1 3-3z"/><path class="i-line" d="M16 16h14a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3h-2v4l-4.5-4H16a3 3 0 0 1-3-3v-6a3 3 0 0 1 3-3z"/><circle class="i-dot" cx="19.5" cy="22" r="1.1"/><circle class="i-dot" cx="23" cy="22" r="1.1"/><circle class="i-dot" cx="26.5" cy="22" r="1.1"/>',
  switches: '<rect class="i-fill" x="9" y="11" width="22" height="8" rx="4"/><rect class="i-line" x="9" y="11" width="22" height="8" rx="4"/><circle class="i-dot i-glow" cx="27" cy="15" r="2.6"/><rect class="i-line" x="9" y="22" width="22" height="8" rx="4"/><circle class="i-line" cx="13" cy="26" r="2.3"/>',
  phone: '<path class="i-fill" d="M12 10h4l2 5-2.5 1.6a11 11 0 0 0 5.4 5.4l1.6-2.5 5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 10 12a2 2 0 0 1 2-2z"/><path class="i-line" d="M12 10h4l2 5-2.5 1.6a11 11 0 0 0 5.4 5.4l1.6-2.5 5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 10 12a2 2 0 0 1 2-2z"/><path class="i-thin" d="M23 9a8 8 0 0 1 8 8M23 13a4 4 0 0 1 4 4"/>',
  places: '<path class="i-fill" d="M20 31s-8-7-8-13a8 8 0 0 1 16 0c0 6-8 13-8 13z"/><path class="i-line" d="M20 31s-8-7-8-13a8 8 0 0 1 16 0c0 6-8 13-8 13z"/><circle class="i-dot i-glow" cx="20" cy="18" r="2.6"/><ellipse class="i-thin" cx="20" cy="32.5" rx="7" ry="1.6"/>',
  projects: '<path class="i-fill" d="M20 9l9 5-9 5-9-5z"/><path class="i-line" d="M20 9l9 5v10l-9 5-9-5V14z"/><path class="i-line" d="M11 14l9 5 9-5M20 19v10"/><path class="i-dot i-glow" d="M30.5 6.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>',
  thinking: '<path class="i-thin" d="M12 13l8 7 8-8M13 28l7-8 7 8M12 13l1 15M28 12l-1 16"/><circle class="i-line" cx="12" cy="13" r="2.1"/><circle class="i-line" cx="28" cy="12" r="2.1"/><circle class="i-line" cx="13" cy="28" r="2.1"/><circle class="i-line" cx="27" cy="28" r="2.1"/><circle class="i-dot i-glow" cx="20" cy="20" r="3.3"/>',
  camera: '<path class="i-fill" d="M9 15h5l2-3h8l2 3h5v13H9z"/><path class="i-line" d="M9 15h5l2-3h8l2 3h5v13H9z"/><circle class="i-line" cx="20" cy="21.5" r="5"/><circle class="i-thin" cx="20" cy="21.5" r="2.6"/><path class="i-thin" d="M18 19.3l3.8 1M21.9 20.5l-1 3.6M19.3 23.7l-1.9-2.7"/><circle class="i-hot i-glow" cx="28" cy="17.6" r="1.3"/>',
  canvas: '<rect class="i-fill" x="8" y="10" width="24" height="19" rx="2.5"/><rect class="i-line" x="8" y="10" width="24" height="19" rx="2.5"/><path class="i-thin" d="M8 14.5h24"/><circle class="i-dot" cx="11" cy="12.3" r=".8"/><circle class="i-dot" cx="13.5" cy="12.3" r=".8"/><path class="i-line" d="M16.5 18.5l-3 3 3 3M23.5 18.5l3 3-3 3M21.3 17.7l-2.6 7.6"/>',
  model3d: '<path class="i-fill" d="M11 16h14v14H11z"/><path class="i-line" d="M11 16h14v14H11zM11 16l5-5h14v14l-5 5M25 16l5-5"/><path class="i-thin" stroke-dasharray="1.6 1.6" d="M16 11v14h14M16 25l-5 5"/>',
  photos: '<rect class="i-thin" x="12" y="8" width="18" height="15" rx="2" transform="rotate(9 21 15.5)"/><rect class="i-fill" x="8" y="13" width="20" height="16" rx="2"/><rect class="i-line" x="8" y="13" width="20" height="16" rx="2"/><path class="i-line" d="M10 27l5-6 4 4 3-3 5 5"/><circle class="i-dot i-glow" cx="22.5" cy="18" r="1.8"/>',
  connections: '<circle class="i-fill" cx="12" cy="27" r="4"/><circle class="i-line" cx="12" cy="27" r="4"/><circle class="i-fill" cx="28" cy="13" r="4"/><circle class="i-line" cx="28" cy="13" r="4"/><path class="i-line" d="M15.2 24.4C18 23 18 19 20 17s5-2 5.4-1.6"/><circle class="i-dot i-glow" cx="19.6" cy="18.6" r="1.4"/>',
  hearing: '<rect class="i-fill" x="16.5" y="9" width="7" height="13" rx="3.5"/><rect class="i-line" x="16.5" y="9" width="7" height="13" rx="3.5"/><path class="i-line" d="M13 19a7 7 0 0 0 14 0M20 26v4M16.5 30h7"/><path class="i-thin" d="M10 13.5a10 10 0 0 0 0 11M30 13.5a10 10 0 0 1 0 11"/>',
  setup: '<circle class="i-fill" cx="20" cy="20" r="7.5"/><path class="i-line" d="M20 8.5v3.2M20 28.3v3.2M8.5 20h3.2M28.3 20h3.2M11.9 11.9l2.2 2.2M25.9 25.9l2.2 2.2M11.9 28.1l2.2-2.2M25.9 14.1l2.2-2.2"/><circle class="i-line" cx="20" cy="20" r="7.5"/><path class="i-line i-glow" d="M16.6 20.2l2.3 2.3 4.6-4.8"/>',
  appearance: '<path class="i-fill" d="M20 9a11 11 0 0 0 0 22c1.4 0 2.3-.9 2.3-2 0-.6-.2-1-.6-1.4-.3-.4-.6-.8-.6-1.4 0-1.1.9-2 2-2h2.6a5.3 5.3 0 0 0 5.3-5.3C31 13.5 26 9 20 9z"/><path class="i-line" d="M20 9a11 11 0 0 0 0 22c1.4 0 2.3-.9 2.3-2 0-.6-.2-1-.6-1.4-.3-.4-.6-.8-.6-1.4 0-1.1.9-2 2-2h2.6a5.3 5.3 0 0 0 5.3-5.3C31 13.5 26 9 20 9z"/><circle cx="14" cy="18.2" r="1.9" fill="#5ee08a"/><circle cx="17.2" cy="13.6" r="1.9" fill="#e6b84a"/><circle cx="22.7" cy="13.3" r="1.9" fill="#6cc9ff"/><circle cx="26.3" cy="17.4" r="1.9" fill="#ff7a3d"/>',
  help: '<path class="i-fill" d="M20 8.5l10 5.75v11.5L20 31.5l-10-5.75v-11.5z"/><path class="i-line" d="M20 8.5l10 5.75v11.5L20 31.5l-10-5.75v-11.5z"/><path class="i-line" d="M17.3 17.2a2.8 2.8 0 1 1 4 2.5c-.8.4-1.3 1-1.3 1.9v.7"/><circle class="i-dot" cx="20" cy="25.6" r="1.1"/>',
  echo: '<circle class="i-fill" cx="20" cy="20" r="6"/><circle class="i-line i-glow" cx="20" cy="20" r="6"/><ellipse class="i-thin" cx="20" cy="20" rx="12.5" ry="4.5" transform="rotate(-25 20 20)"/><circle class="i-dot" cx="30.5" cy="15.2" r="1.5"/>',
  play: '<path class="i-fill" d="M14 11l14 9-14 9z"/><path class="i-line" d="M14 11l14 9-14 9z"/><path class="i-thin" d="M31 14a8 8 0 0 1 0 12"/>',
  chip: '<rect class="i-fill" x="12" y="12" width="16" height="16" rx="2"/><rect class="i-line" x="12" y="12" width="16" height="16" rx="2"/><path class="i-thin" d="M16 8v4M20 8v4M24 8v4M16 28v4M20 28v4M24 28v4M8 16h4M8 20h4M8 24h4M28 16h4M28 20h4M28 24h4"/><circle class="i-dot i-glow" cx="20" cy="20" r="2.5"/>',
};
// a code branch: two commits splitting off and merging back
GLYPHS.github = '<circle class="i-fill" cx="14" cy="11" r="3"/><circle class="i-line" cx="14" cy="11" r="3"/><circle class="i-line" cx="14" cy="29" r="3"/><circle class="i-line i-glow" cx="27" cy="16" r="3"/><path class="i-line" d="M14 14v12M27 19c0 5-7 4-12 8"/>';
// a calendar page: two rings on top, the day marked
GLYPHS.calendar = '<rect class="i-fill" x="9" y="12" width="22" height="18" rx="2.5"/><rect class="i-line" x="9" y="12" width="22" height="18" rx="2.5"/><path class="i-line" d="M9 17.5h22M15 9.5v5M25 9.5v5"/><circle class="i-dot" cx="14.5" cy="22" r="1"/><circle class="i-dot" cx="20" cy="22" r="1"/><circle class="i-dot i-glow" cx="25.5" cy="25.5" r="1.8"/>';
GLYPHS.chat = GLYPHS.talk;
GLYPHS.toggle = GLYPHS.switches;
GLYPHS.cube = GLYPHS.projects;
GLYPHS.brain = GLYPHS.thinking;
GLYPHS.pin = GLYPHS.places;
GLYPHS.model = GLYPHS.model3d;
GLYPHS.photo = GLYPHS.photos;
GLYPHS.link = GLYPHS.connections;
GLYPHS.mic = GLYPHS.hearing;
GLYPHS.wrench = GLYPHS.setup;
GLYPHS.palette = GLYPHS.appearance;
const ICON_COLORS = {
  talk: "#5ee08a", switches: "#c3ff6b", phone: "#00f5d4", places: "#2ec4b6", github: "#c9d1d9", calendar: "#ff9e7a", projects: "#fb8500", thinking: "#b18cff",
  camera: "#3a86ff", canvas: "#ffbe0b", model3d: "#f72585", photos: "#ff7eb6", connections: "#4cc9f0", hearing: "#80ed99",
  setup: "#a8e1ff", appearance: "#e6b84a", help: "#dfe6ff", echo: "#5ee08a", play: "#1ed760", chip: "#ff9f43",
};
function icon(name, color) {
  const c = color || ICON_COLORS[name] || "var(--ui-accent)";
  return `<span class="ui-icon" style="--ic:${c}"><svg viewBox="0 0 40 40" aria-hidden="true">` +
    `<circle class="i-orbit" cx="20" cy="20" r="18.5"/><circle class="i-moon" cx="20" cy="1.5" r="1.6"/>${GLYPHS[name] || ""}</svg></span>`;
}
const svg = (name) => icon(name);

// Tabs inside a section: its parts marked data-tab="Name" are shown one at a time, chosen from a
// tab bar along the top (and remembered per section).
function setupSubtabs(section) {
  const panels = [...section.querySelectorAll(":scope [data-tab]")].filter((p) => p.closest(".ui-section") === section);
  section.querySelectorAll(":scope > .ui-subtabs").forEach((bar) => bar.remove());
  if (panels.length < 2) return;
  const key = `echo_tab_${section.dataset.section}`;
  const saved = localStorageGet(key);
  let active = panels.find((p) => p.dataset.tab === saved) ? saved : panels[0].dataset.tab;
  const bar = document.createElement("div");
  bar.className = "ui-subtabs";
  bar.setAttribute("role", "tablist");
  const show = (name) => {
    active = name;
    localStorageSet(key, name);
    panels.forEach((p) => { p.hidden = p.dataset.tab !== name; });
    bar.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.name === name));
    section.dispatchEvent(new CustomEvent("subtab", { detail: name }));
  };
  for (const p of panels) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "ui-subtab";
    b.dataset.name = p.dataset.tab;
    b.setAttribute("role", "tab");
    b.textContent = p.dataset.tab;
    b.addEventListener("click", () => show(p.dataset.tab));
    bar.appendChild(b);
  }
  const anchor = section.querySelector(":scope > .ui-sub") || section.querySelector(":scope > .ui-h");
  anchor.after(bar);
  show(active);
}
function setupAllSubtabs() {
  document.querySelectorAll(".ui-section").forEach(setupSubtabs);
}

let currentLevel = "orb"; // orb | menu | section
let currentSection = null;
const SECTION_HOOKS = {
  hearing: { open: () => window.onAudioTabActivated && window.onAudioTabActivated(), close: () => window.stopLevelMeter && window.stopLevelMeter() },
  model3d: { open: () => { requestAnimationFrame(() => window.dispatchEvent(new Event("resize"))); loadModelStrip(); } },
  canvas: { open: () => { $("canvasEmpty").hidden = !$("canvasPanel").hidden; } },
  photos: { open: () => loadPhotos() },
  switches: { open: () => { loadSwitches().then(renderSwitches); loadUsage(); } },
  appearance: { open: () => renderAppearance() },
  setup: { open: () => { if (window.loadBrain) window.loadBrain(); if (window.loadSound) window.loadSound(); } },
  calendar: { open: () => window.loadCalendar && window.loadCalendar() },
  talk: { open: () => loadConvo() },
  camera: { open: () => window.EchoCamera && window.EchoCamera.open(), close: () => window.EchoCamera && window.EchoCamera.close() },
};

function renderTiles() {
  $("missionTiles").innerHTML = TILES.map((t) => `
    <button class="ui-tile" type="button" data-id="${t.id}" style="--ic:${ICON_COLORS[t.id] || "var(--ui-accent)"}">
      ${icon(t.id)}<span class="t-name">${t.name}</span><span class="t-peek" id="peek-${t.id}"></span>
      <span class="t-badge" id="badge-${t.id}" hidden></span>
    </button>`).join("");
  $("missionTiles").addEventListener("click", (e) => {
    const tile = e.target.closest(".ui-tile");
    if (tile) openSection(tile.dataset.id);
  });
}

function openSection(id) {
  closeLive();
  if (currentSection && SECTION_HOOKS[currentSection] && SECTION_HOOKS[currentSection].close) SECTION_HOOKS[currentSection].close();
  document.querySelectorAll(".ui-section").forEach((s) => { s.hidden = s.dataset.section !== id; });
  $("missionMenu").hidden = true;
  currentSection = id;
  currentLevel = "section";
  syncNav();
  if (SECTION_HOOKS[id] && SECTION_HOOKS[id].open) SECTION_HOOKS[id].open();
  $("mission").scrollTop = 0;
}

function backOne() {
  if (currentLevel === "section") {
    if (SECTION_HOOKS[currentSection] && SECTION_HOOKS[currentSection].close) SECTION_HOOKS[currentSection].close();
    document.querySelectorAll(".ui-section").forEach((s) => { s.hidden = true; });
    $("missionMenu").hidden = false;
    currentSection = null;
    currentLevel = "menu";
  } else if (currentLevel === "menu") {
    currentLevel = "orb";
  } else {
    currentLevel = "menu";
    refreshPeeks();
  }
  syncNav();
}

function syncNav() {
  const open = currentLevel !== "orb";
  $("mission").classList.toggle("open", open);
  $("navLeft").classList.toggle("flip", open);
  $("navLeftTip").textContent = open ? "Back" : "Mission";
  $("navLeft").setAttribute("aria-label", open ? "Go back" : "Open Mission Control");
  $("navRight").hidden = open;
  document.body.classList.toggle("panel-open", open || $("livePanel").classList.contains("open"));
}

function openLive() {
  $("livePanel").classList.add("open");
  $("navRight").classList.remove("flip");
  $("navRightTip").textContent = "Close";
  $("navLeft").hidden = true;
  document.body.classList.add("panel-open");
  refreshLive();
}
function closeLive() {
  $("livePanel").classList.remove("open");
  $("navRight").classList.add("flip");
  $("navRightTip").textContent = "Live";
  $("navLeft").hidden = false;
  document.body.classList.toggle("panel-open", currentLevel !== "orb");
}

// the old dashboard asks for a tab after a Spotify/Google sign-in: open that section instead
window.activateTab = (name) => {
  const map = { connections: "connections", audio: "hearing", setup: "setup", projects: "projects", settings: "help" };
  if (map[name]) { currentLevel = "menu"; openSection(map[name]); }
};

// --------------------------------------------------------------------------------------
// Switches: by category, searchable, each with what Echo did with it
// --------------------------------------------------------------------------------------
let switchShow = "all";
let switchCat = localStorageGet("echo_switch_cat") || "echo";
let usage = [];
async function loadUsage() {
  try { usage = await getJSON("/api/usage"); } catch (err) { usage = []; }
  if (currentSection === "switches") renderSwitches();
}
function usageFor(key) {
  const tools = new Set(featureInfo(key).tools || []);
  return usage.filter((u) => tools.has(u.tool)).slice(0, 5);
}

function renderSwitches() {
  const query = ($("switchFilter").value || "").trim().toLowerCase();
  const byCat = {};
  for (const key of Object.keys(switches)) {
    const info = featureInfo(key);
    const s = switches[key];
    if (switchShow === "on" && !s.on) continue;
    if (switchShow === "off" && s.on) continue;
    if (query && !`${info.name} ${info.desc} ${s.label || ""}`.toLowerCase().includes(query)) continue;
    (byCat[info.cat] = byCat[info.cat] || []).push(key);
  }
  const present = CATEGORIES.filter(([cat]) => Object.keys(switches).some((k) => featureInfo(k).cat === cat));
  if (!present.some(([cat]) => cat === switchCat)) switchCat = present[0] ? present[0][0] : "echo";
  const tabs = present.map(([cat, title]) => {
    const keys = Object.keys(switches).filter((k) => featureInfo(k).cat === cat);
    const onCount = keys.filter((k) => switches[k].on).length;
    return `<button type="button" class="ui-subtab${!query && cat === switchCat ? " active" : ""}" data-cat="${cat}">${esc(title)} <span>${onCount}/${keys.length}</span></button>`;
  }).join("");
  const shownCats = query ? CATEGORIES : CATEGORIES.filter(([cat]) => cat === switchCat);
  const html = shownCats.filter(([cat]) => byCat[cat]).map(([cat, title]) => {
    const keys = byCat[cat];
    const onCount = keys.filter((k) => switches[k].on).length;
    const rows = keys.map((key) => {
      const info = featureInfo(key);
      const s = switches[key];
      const used = usageFor(key);
      const cost = s.cost && s.cost !== "no local model" ? `<span class="sw-cost">${esc(s.cost)}</span>` : "";
      const needs = s.needs ? `<span class="sw-cost">needs ${esc(s.needs)} connected</span>` : "";
      const control = s.kind === "location"
        ? `<button type="button" class="ui-btn small" data-location="1">${esc({ off: "Off", pc: "PC", phone: "Phone" }[s.mode] || "Off")}</button>`
        : `<label class="sw-toggle"><input type="checkbox" data-switch="${esc(key)}" ${s.on ? "checked" : ""} ${s.alwaysOn ? "disabled" : ""} /><span></span></label>`;
      const history = used.length
        ? used.map((u) => `<li><span class="sw-when">${esc(ago(u.at))}${u.channel === "phone" ? " - by phone" : ""}</span>${esc(u.result || u.tool)}</li>`).join("")
        : `<li class="sw-none">Echo hasn't used this yet.</li>`;
      return `
        <div class="sw-row${s.on ? " on" : ""}">
          <span class="sw-dot" style="--c:${moonColor(key)}"></span>
          <div class="sw-text">
            <div class="sw-name">${esc(info.name)} ${cost}${needs}</div>
            <div class="sw-desc">${esc(info.desc)}</div>
            ${(info.tools && info.tools.length) ? `<details class="sw-hist"><summary>What Echo did with it${used.length ? ` (${used.length})` : ""}</summary><ul>${history}</ul></details>` : ""}
            ${info.special === "passcode" ? '<div class="sw-extra" data-passcode="1"></div>' : ""}
          </div>
          ${control}
        </div>`;
    }).join("");
    return `<div class="sw-cat"><h3 class="ui-h3">${esc(title)} <span class="sw-count">${onCount}/${keys.length} on</span></h3>${rows}</div>`;
  }).join("") || `<p class="ui-empty">${query ? "Nothing matches." : "Nothing here with that filter."}</p>`;
  $("switchList").innerHTML = `<div class="ui-subtabs" role="tablist">${tabs}</div>${html}`;
  $("switchList").querySelectorAll("[data-cat]").forEach((b) => b.addEventListener("click", () => {
    switchCat = b.dataset.cat;
    localStorageSet("echo_switch_cat", switchCat);
    $("switchFilter").value = "";
    renderSwitches();
  }));
  $("switchList").querySelectorAll("[data-switch]").forEach((i) => i.addEventListener("change", () => setSwitch(i.dataset.switch, i.checked)));
  $("switchList").querySelectorAll("[data-location]").forEach((b) => b.addEventListener("click", async () => {
    await window.cycleLocationMode();
    await loadSwitches();
    refreshMoons();
    renderSwitches();
  }));
  $("switchList").querySelectorAll("[data-passcode]").forEach((slot) => {
    if (window.buildShutdownPhraseRow) slot.appendChild(window.buildShutdownPhraseRow());
  });
}

function initSwitchControls() {
  $("switchFilter").addEventListener("input", renderSwitches);
  $("switchChips").addEventListener("click", (e) => {
    const chip = e.target.closest("[data-show]");
    if (!chip) return;
    switchShow = chip.dataset.show;
    $("switchChips").querySelectorAll(".ui-chip").forEach((c) => c.classList.toggle("active", c === chip));
    renderSwitches();
  });
}

// --------------------------------------------------------------------------------------
// Live panel
// --------------------------------------------------------------------------------------
function card(iconName, title, body, section) {
  return `<button type="button" class="ui-livecard" style="--ic:${ICON_COLORS[iconName] || "var(--ui-accent)"}" ${section ? `data-go="${section}"` : "disabled"}>
    ${icon(iconName)}<span class="lc-body"><span class="lc-title">${title}</span>${body}</span></button>`;
}

async function refreshLive() {
  try { live = await getJSON("/api/live"); } catch (err) { return; }
  updateOrbMode();
  refreshPeeks();
  if (!$("livePanel").classList.contains("open")) return;
  const L = live;
  const parts = [];

  const echoState = !L.echo.enabled ? "Switched off" : L.echo.phase === "asleep" ? "Asleep - say “Echo”" : L.echo.busy ? "Working on a request" : "Listening";
  const mic = L.mic.alive ? `${esc(L.mic.device || "microphone")}${L.mic.level >= 500 ? " - hearing you now" : ""}` : "Listener isn't running";
  parts.push(card("echo", `Echo: ${esc(echoState)}`, `<span class="lc-line">${mic}</span>`, "hearing"));

  if (L.last) {
    const tools = L.last.tools.length ? `<span class="lc-chips">${L.last.tools.map((t) => `<i>${esc(t)}</i>`).join("")}</span>` : "";
    parts.push(card("chat", `Last request <small>${esc(ago(L.last.at))}${L.last.channel === "phone" ? " - by phone" : ""}</small>`,
      `<span class="lc-quote">“${esc(L.last.heard.slice(0, 120))}”</span><span class="lc-line">${esc((L.last.reply || "").replace(/\s*\(\d+(\.\d+)?s.*\)$/, "").slice(0, 160))}</span>${tools}`, "thinking"));
  } else {
    parts.push(card("chat", "Last request", `<span class="lc-line">Nothing yet today.</span>`, "talk"));
  }

  const playing = [];
  if (L.spotify && L.spotify.track) playing.push(`<span class="lc-line"><b>Spotify</b> ${L.spotify.playing ? "playing" : "paused"}: ${esc(L.spotify.track)}${L.spotify.artist ? ` - ${esc(L.spotify.artist)}` : ""}</span>`);
  else if (L.spotify) playing.push(`<span class="lc-line"><b>Spotify</b> nothing playing</span>`);
  if (L.tv) playing.push(`<span class="lc-line"><b>${esc(L.tv.name || "TV")}</b> ${esc(L.tv.app)}${L.tv.title ? `: ${esc(L.tv.title.slice(0, 70))}` : ""}${L.tv.state === "PAUSED" ? " (paused)" : ""}</span>`);
  parts.push(card("play", "Playing", playing.join("") || `<span class="lc-line">Nothing that Echo can see.</span>`, "switches"));

  const P = L.phone;
  let phone = !P.configured ? "Not set up" : !P.enabled ? "Calling is switched off" : P.in_call.length ? `In a call with ${esc(P.in_call.join(", "))}` : "Ready - line works";
  const flags = [`Sir Page ${P.sir_page ? "on" : "off"}`, P.screening ? "screening unknown callers" : "turning unknown callers away"];
  parts.push(card("phone", `Phone: ${phone}`, `<span class="lc-line">${esc(flags.join(" - "))}</span>`, "phone"));

  const working = L.projects.filter((p) => p.status === "working");
  const projLine = working.length ? working.map((p) => `${esc(p.title)} (step ${p.steps})`).join(", ") : (L.projects[0] ? `Latest: ${esc(L.projects[0].title)} - ${esc(p2(L.projects[0].status))}` : "No projects yet");
  parts.push(card("cube", `Projects${L.approvals ? ` <em class="lc-warn">${L.approvals} waiting for you</em>` : ""}`, `<span class="lc-line">${projLine}</span>`, "projects"));

  const made = [L.canvas ? `Canvas: ${esc(L.canvas)}` : "", L.model3d ? `3D: ${esc(L.model3d)}` : ""].filter(Boolean).join(" - ");
  if (made) parts.push(card("canvas", "Made recently", `<span class="lc-line">${made}</span>`, L.model3d ? "model3d" : "canvas"));

  if (L.pc) {
    const g = L.pc.gpu;
    const lines = [`Processor ${Math.round(L.pc.cpu)}% - memory ${L.pc.ram_used} of ${L.pc.ram_total} GB`];
    if (g) lines.push(`Graphics card ${Math.round(g.temp)}°C - ${Math.round(g.busy)}% busy - ${g.used_gb} of ${g.total_gb} GB${g.power ? ` - ${Math.round(g.power)} W` : ""}`);
    let html = lines.map((l) => `<span class="lc-line">${l}</span>`).join("");
    if (L.pc.low_drives.length) html += `<span class="lc-line" style="color:var(--ui-warn)">Low on space: ${L.pc.low_drives.map(esc).join(", ")}</span>`;
    parts.push(card("chip", "This PC", html, "setup"));
  }

  const on = Object.keys(switches).filter((k) => switches[k].on && k !== "dashboard-mic");
  parts.push(card("toggle", `${on.length} switches on`, `<span class="lc-dots">${on.map((k) => `<i title="${esc(featureInfo(k).name)}" style="--c:${moonColor(k)}"></i>`).join("")}</span>`, "switches"));

  $("liveCards").innerHTML = parts.join("");
  $("liveUpdated").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
}
const p2 = (s) => ({ working: "in progress", waiting_approval: "needs your OK", done: "done", stopped: "stopped" }[s] || s);

function refreshPeeks() {
  if (!live) return;
  const set = (id, text, badge) => {
    const el = $(`peek-${id}`);
    if (el) el.textContent = text;
    const b = $(`badge-${id}`);
    if (b) { b.hidden = !badge; b.textContent = badge || ""; }
  };
  const on = Object.values(switches).filter((s) => s.on).length;
  set("switches", `${on} on`);
  set("talk", live.last ? `Last: “${live.last.heard.slice(0, 40)}”` : "Type to Echo");
  set("projects", live.projects[0] ? `${live.projects[0].title} - ${p2(live.projects[0].status)}` : "Nothing yet", live.approvals ? `${live.approvals} waiting` : "");
  set("thinking", live.last ? `${live.last.tools.length} tools last time` : "");
  set("hearing", live.mic.alive ? live.mic.device.replace(/^Microphone\s*\(?/i, "").replace(/\)?$/, "") : "listener off");
  set("canvas", live.canvas || "empty");
  set("model3d", live.model3d || "none yet");
  set("phone", live.phone.in_call.length ? `in a call` : live.phone.enabled ? "line ready" : "calling off");
  set("camera", switches.camera_access && switches.camera_access.on ? "see what it sees" : "camera access off");
}

// --------------------------------------------------------------------------------------
// Galleries: photos, 3D models; conversation
// --------------------------------------------------------------------------------------
async function loadPhotos() {
  let items = [];
  try { items = await getJSON("/api/images"); } catch (err) { /* empty */ }
  $("photoGallery").innerHTML = items.length
    ? items.map((i) => `<button type="button" class="ui-photo" data-url="${esc(i.url)}" data-prompt="${esc(i.prompt)}"><img src="${esc(i.url)}" alt="${esc(i.prompt.slice(0, 80))}" loading="lazy" /><span>${esc(ago(i.at))}</span></button>`).join("")
    : `<p class="ui-empty">No pictures yet. Ask Echo to make one (turn on Pictures in Switches).</p>`;
}
function initLightbox() {
  $("photoGallery").addEventListener("click", (e) => {
    const b = e.target.closest(".ui-photo");
    if (!b) return;
    $("lightboxImg").src = b.dataset.url;
    $("lightboxCaption").textContent = b.dataset.prompt;
    $("lightbox").hidden = false;
  });
  $("lightboxClose").addEventListener("click", () => { $("lightbox").hidden = true; });
  $("lightbox").addEventListener("click", (e) => { if (e.target === $("lightbox")) $("lightbox").hidden = true; });
}

async function loadModelStrip() {
  let items = [];
  try { items = await getJSON("/api/models3d"); } catch (err) { /* empty */ }
  $("modelStrip").innerHTML = items.length
    ? items.map((m) => `<button type="button" class="ui-btn small" data-model="${esc(m.file)}">${esc(new Date(m.at * 1000).toLocaleDateString())} ${esc(m.file.slice(0, 18))}</button>`).join("")
    : `<span class="hint-text">No 3D models yet.</span>`;
  $("modelStrip").querySelectorAll("[data-model]").forEach((b) => b.addEventListener("click", () => {
    if (window.Model3DViewer) window.Model3DViewer.load(`/3d_models/${b.dataset.model}`, b.dataset.model);
    const viewerTab = document.querySelector('.ui-section[data-section="model3d"] .ui-subtab[data-name="Viewer"]');
    if (viewerTab) viewerTab.click();
    requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
  }));
}

async function loadConvo() {
  let events = [];
  try { events = await getJSON("/api/activity"); } catch (err) { /* empty */ }
  const rows = events.filter((e) => e.kind === "heard" || e.kind === "reply").slice(-30);
  $("convoList").innerHTML = rows.length
    ? rows.map((e) => `<li class="${e.kind === "heard" ? "you" : "echo"}"><b>${e.kind === "heard" ? "You" : "Echo"}${e.channel === "phone" ? " (phone)" : ""}</b> ${esc(e.text.replace(/\s*\(\d+(\.\d+)?s.*\)$/, ""))}</li>`).join("")
    : `<li class="ui-empty">Nothing said yet.</li>`;
}


// --------------------------------------------------------------------------------------
// Camera: a look through the webcam with labelled boxes; "remember me"
// --------------------------------------------------------------------------------------
let camTimer = null;
let camBusy = false;
async function camStatus() {
  let st = { access: false, known_faces: 0, photos: [] };
  try { st = await getJSON("/api/camera/status"); } catch (err) { /* off */ }
  $("camOff").hidden = st.access;
  $("camOn").hidden = !st.access;
  const camTabs = document.querySelector('.ui-section[data-section="camera"] > .ui-subtabs');
  if (camTabs) camTabs.hidden = !st.access;
  $("camKnown").textContent = st.known_faces
    ? `Echo knows your face from ${st.known_faces} reference photo${st.known_faces === 1 ? "" : "s"}. It also adds one each time you really unlock Windows, so it keeps up as you change.`
    : "Echo doesn't know your face yet. Press Remember me while looking at the camera.";
  $("camRefs").innerHTML = st.photos.map((n) => `<img src="/api/camera/ref/${encodeURIComponent(n)}" alt="Reference photo" loading="lazy" />`).join("");
  return st;
}
async function camLook() {
  if (camBusy) return;
  camBusy = true;
  $("camLook").disabled = true;
  $("camLook").textContent = "Looking...";
  try {
    const resp = await fetch("/api/camera/look", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ labels: $("camLabels").checked, describe: true }) });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "The camera didn't answer.");
    $("camImg").src = data.image;
    $("camImg").hidden = false;
    $("camEmpty").hidden = true;
    const boxes = [
      ...data.faces.map((f) => ({ ...f, kind: f.you ? "you" : "face" })),
      ...data.objects.filter((o) => !/^(person|man|woman|face|human)$/i.test(o.label) || !data.faces.length).map((o) => ({ ...o, kind: "thing" })),
    ];
    $("camBoxes").innerHTML = boxes.map((b) => {
      const [x1, y1, x2, y2] = b.box;
      const style = `left:${(x1 / data.width) * 100}%;top:${(y1 / data.height) * 100}%;width:${((x2 - x1) / data.width) * 100}%;height:${((y2 - y1) / data.height) * 100}%`;
      return `<div class="cam-box ${b.kind}" style="${style}"><span>${esc(b.label)}</span></div>`;
    }).join("");
    $("camDesc").textContent = data.description || "";
    const counts = {};
    for (const b of boxes) counts[b.label] = (counts[b.label] || 0) + 1;
    $("camList").innerHTML = Object.entries(counts).map(([label, n]) => `<li><b>${esc(label)}</b>${n > 1 ? ` x${n}` : ""}</li>`).join("")
      || `<li class="ui-empty">${$("camLabels").checked ? "Nothing labelled." : "Labels are off."}</li>`;
  } catch (err) {
    $("camDesc").textContent = err.message;
  } finally {
    camBusy = false;
    $("camLook").disabled = false;
    $("camLook").textContent = "Look now";
  }
}
window.EchoCamera = {
  open: () => camStatus(),
  close: () => { clearInterval(camTimer); camTimer = null; $("camAuto").checked = false; },
};
function initCamera() {
  $("camLook").addEventListener("click", camLook);
  $("camGoSwitches").addEventListener("click", () => openSection("switches"));
  $("camAuto").addEventListener("change", () => {
    clearInterval(camTimer);
    camTimer = $("camAuto").checked ? setInterval(camLook, 15000) : null;
    if ($("camAuto").checked) camLook();
  });
  $("camRemember").addEventListener("click", async () => {
    $("camRemember").disabled = true;
    $("camRememberMsg").textContent = "Look at the camera...";
    try {
      const resp = await fetch("/api/camera/remember", { method: "POST" });
      const data = await resp.json();
      $("camRememberMsg").textContent = !resp.ok ? (data.detail || "Couldn't do that.") : data.saved ? "Got it - thank you, sir." : `Not saved: ${data.why}.`;
      await camStatus();
    } catch (err) {
      $("camRememberMsg").textContent = err.message;
    } finally {
      $("camRemember").disabled = false;
    }
  });
}

// --------------------------------------------------------------------------------------
// Project viewer: the running app, or the code Echo wrote
// --------------------------------------------------------------------------------------
async function showProjectCode(show) {
  $("projectFrame").hidden = show;
  $("projectCode").hidden = !show;
  $("projectCodeBtn").classList.toggle("active", show);
  $("projectAppBtn").classList.toggle("active", !show);
  if (!show || typeof openProjectId === "undefined" || !openProjectId) return;
  const p = await getJSON(`/api/projects/${openProjectId}`);
  const files = p.files || [];
  $("projectCodeFiles").innerHTML = files.length
    ? files.map((f) => `<button type="button" class="ui-chip" data-file="${esc(f)}">${esc(f)}</button>`).join("")
    : '<span class="hint-text">No files yet.</span>';
  const open = async (path) => {
    $("projectCodeFiles").querySelectorAll(".ui-chip").forEach((c) => c.classList.toggle("active", c.dataset.file === path));
    const data = await getJSON(`/api/projects/${openProjectId}/file?path=${encodeURIComponent(path)}`);
    $("projectCodeText").textContent = data.text;
  };
  $("projectCodeFiles").querySelectorAll("[data-file]").forEach((b) => b.addEventListener("click", () => open(b.dataset.file)));
  if (files[0]) open(files.includes(p.preview) ? p.preview : files[0]);
}
function initProjectCode() {
  $("projectCodeBtn").addEventListener("click", () => showProjectCode(true));
  $("projectAppBtn").addEventListener("click", () => showProjectCode(false));
  $("projectCloseBtn").addEventListener("click", () => showProjectCode(false));
}

// --------------------------------------------------------------------------------------
// Start
// --------------------------------------------------------------------------------------
async function start() {
  renderTiles();
  setupAllSubtabs();
  document.querySelector('.ui-section[data-section="model3d"]').addEventListener("subtab", (e) => {
    if (e.detail === "Viewer") requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
  });
  initSwitchControls();
  initLightbox();
  initCamera();
  initProjectCode();
  bindTalk("talkForm", "talkInput");
  bindTalk("talkForm2", "talkInput2");
  $("navLeft").addEventListener("click", () => {
    if ($("livePanel").classList.contains("open")) closeLive();
    if (currentLevel === "orb") { currentLevel = "menu"; refreshPeeks(); syncNav(); } else backOne();
  });
  $("navRight").addEventListener("click", () => ($("livePanel").classList.contains("open") ? closeLive() : openLive()));
  $("liveCards").addEventListener("click", (e) => {
    const c = e.target.closest("[data-go]");
    if (c) { currentLevel = "menu"; openSection(c.dataset.go); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("lightbox").hidden) $("lightbox").hidden = true;
      else if ($("livePanel").classList.contains("open")) closeLive();
      else if (currentLevel !== "orb") backOne();
      else toggleTalk(false);
    }
  });

  await loadAppearance();
  try { orbScene = buildOrb(); } catch (err) { console.error("The 3D orb couldn't start:", err); }
  await loadSwitches().catch(() => {});
  refreshMoons();
  await refreshLive();
  // a sign-in just finished (Spotify/Google): show where it landed
  if (($("connectionsStatusMsg").textContent || "").trim()) { currentLevel = "menu"; openSection("connections"); }
  // links straight to a screen: #live, #mission, or a section name (#switches, #camera...)
  const hash = location.hash.replace("#", "");
  if (hash === "live") openLive();
  else if (hash === "mission") { currentLevel = "menu"; refreshPeeks(); syncNav(); }
  else if (TILES.some((t) => t.id === hash)) { currentLevel = "menu"; openSection(hash); }
  startSeasons(); // offers Christmas mode and the like when it's the season (?season=christmas previews it)

  setInterval(() => { if (!document.hidden) refreshLive(); }, 3000);
  setInterval(async () => {
    if (document.hidden) return;
    await loadSwitches().catch(() => {});
    refreshMoons();
    if (currentSection === "switches") loadUsage();
    if (currentSection === "talk") loadConvo();
    if (currentSection === "canvas") $("canvasEmpty").hidden = !$("canvasPanel").hidden;
  }, 6000);
}
start();
