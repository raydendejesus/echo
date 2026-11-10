// Seasonal mode: when the dashboard opens during a holiday season, Echo offers to dress up for it -
// that season's colors, a light falling effect (snow for Christmas), and, if Spotify is connected,
// a song. Asked once per season per year; the normal look comes back by itself when the season ends.
// A small module loaded after the page is up; outside a season it only checks the date.

export const SEASONS = [
  {
    id: "christmas", name: "Christmas", emoji: "\u{1F384}", from: [12, 1], to: [12, 26],
    blurb: "a red theme and a little snow", song: "All I Want for Christmas Is You",
    colors: { bg: "#0b0405", panel: "#1a090c", accent: "#e8434f", text: "#fdeceb", orb: "#e8434f", rings: "#e9c46a" },
    effect: { shape: "snow", count: 70, color: "#ffffff", speed: 0.6 },
  },
  {
    id: "newyear", name: "New Year's", emoji: "\u{1F386}", from: [12, 31], to: [1, 1],
    blurb: "midnight and gold with falling confetti", song: "Auld Lang Syne",
    colors: { bg: "#05060d", panel: "#10122a", accent: "#f2c94c", text: "#f4efe0", orb: "#f2c94c", rings: "#c0c6ff" },
    effect: { shape: "confetti", count: 55, color: ["#f2c94c", "#c0c6ff", "#ff7aa8", "#7ce0c3"], speed: 0.9 },
  },
  {
    id: "valentines", name: "Valentine's", emoji: "\u{1F496}", from: [2, 7], to: [2, 14],
    blurb: "a pink theme with floating hearts", song: "Can't Help Falling in Love",
    colors: { bg: "#0d0509", panel: "#1f0c16", accent: "#ff6fa5", text: "#ffe9f1", orb: "#ff6fa5", rings: "#ffb3cd" },
    effect: { shape: "heart", count: 26, color: "#ff8fb8", speed: 0.45 },
  },
  {
    id: "halloween", name: "Halloween", emoji: "\u{1F383}", from: [10, 20], to: [10, 31],
    blurb: "orange and purple with falling leaves", song: "Thriller",
    colors: { bg: "#0a0610", panel: "#170e22", accent: "#ff8a1f", text: "#f6e8ff", orb: "#ff8a1f", rings: "#9b5cff" },
    effect: { shape: "leaf", count: 34, color: ["#ff8a1f", "#c4541a", "#9b5cff"], speed: 0.55 },
  },
];

const byId = (id) => SEASONS.find((s) => s.id === id) || null;

/** The season today falls in (handles New Year's crossing the year end), or null. */
export function seasonFor(date = new Date()) {
  const md = (date.getMonth() + 1) * 100 + date.getDate();
  return SEASONS.find((s) => {
    const from = s.from[0] * 100 + s.from[1], to = s.to[0] * 100 + s.to[1];
    return from <= to ? md >= from && md <= to : md >= from || md <= to;
  }) || null;
}

/** "Dec 1 - Dec 26" */
export function dates(s) {
  const m = (x) => new Date(2000, x[0] - 1, x[1]).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return `${m(s.from)} - ${m(s.to)}`;
}

// The season a dated choice belongs to: New Year's on Jan 1 belongs to the year that's ending.
function seasonYear(s, date = new Date()) {
  return s.from[0] > s.to[0] && date.getMonth() === 0 ? date.getFullYear() - 1 : date.getFullYear();
}
export const askKey = (s) => `${s.id}-${seasonYear(s)}`;

// --- the falling effect: a few dozen particles on one canvas, paused when the tab is hidden -----
let fx = null;

export function startEffect(season) {
  stopEffect();
  if (!season) return;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const canvas = document.createElement("canvas");
  canvas.id = "seasonFx";
  canvas.setAttribute("aria-hidden", "true");
  document.body.appendChild(canvas);
  const ctx = canvas.getContext("2d");
  const cfg = season.effect;
  const colors = Array.isArray(cfg.color) ? cfg.color : [cfg.color];
  let w = 0, h = 0;
  const size = () => {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    w = innerWidth; h = innerHeight;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };
  size();
  addEventListener("resize", size);
  const count = Math.round(cfg.count * (reduce ? 0.35 : 1) * Math.min(1, (w * h) / (1400 * 900) + 0.35));
  const rand = (a, b) => a + Math.random() * (b - a);
  const parts = Array.from({ length: count }, () => ({
    x: rand(0, w), y: rand(-h, h), r: cfg.shape === "snow" ? rand(1.2, 3.2) : cfg.shape === "heart" ? rand(3.5, 7) : rand(2, 6),
    vy: rand(0.4, 1) * cfg.speed,
    sway: rand(0.3, 1.2), phase: rand(0, 6.28), spin: rand(-0.03, 0.03), angle: rand(0, 6.28),
    color: colors[Math.floor(Math.random() * colors.length)], alpha: rand(0.45, 0.95),
  }));
  let last = 0, raf = 0;
  function draw(now) {
    raf = requestAnimationFrame(draw);
    if (document.hidden || now - last < 33) return; // ~30 frames a second is plenty for snow
    const dt = Math.min((now - last) / 16.7, 3);
    last = now;
    ctx.clearRect(0, 0, w, h);
    for (const p of parts) {
      p.y += p.vy * dt * (reduce ? 0.4 : 1);
      p.phase += 0.02 * dt;
      p.angle += p.spin * dt;
      const x = p.x + Math.sin(p.phase) * p.sway * 12;
      if (p.y - p.r > h) { p.y = -10; p.x = rand(0, w); }
      ctx.globalAlpha = p.alpha;
      ctx.fillStyle = p.color;
      if (cfg.shape === "snow") {
        ctx.beginPath(); ctx.arc(x, p.y, p.r, 0, 6.283); ctx.fill();
      } else {
        ctx.save(); ctx.translate(x, p.y); ctx.rotate(p.angle);
        if (cfg.shape === "confetti") ctx.fillRect(-p.r / 2, -p.r / 4, p.r, p.r / 2);
        else if (cfg.shape === "leaf") { ctx.beginPath(); ctx.ellipse(0, 0, p.r, p.r / 2.2, 0, 0, 6.283); ctx.fill(); }
        else { ctx.font = `${p.r * 3}px serif`; ctx.fillText("♥", -p.r, p.r); }
        ctx.restore();
      }
    }
    ctx.globalAlpha = 1;
  }
  raf = requestAnimationFrame(draw);
  fx = { canvas, stop: () => { cancelAnimationFrame(raf); removeEventListener("resize", size); canvas.remove(); } };
}

export function stopEffect() {
  if (fx) fx.stop();
  fx = null;
}

// --- turning a season on and off ---------------------------------------------------------------
// `look` is the dashboard's appearance object; `apply` re-colors the page, `save` stores it.

export async function turnOn(season, look, apply, save, withMusic, byHand = false) {
  if (!look.season) look.beforeSeason = { colors: { ...look.colors }, preset: look.preset || "Custom" };
  look.season = season.id;
  look.seasonByHand = byHand; // picked on the Seasons tab: stays until turned off, whatever the date
  look.colors = { ...season.colors };
  look.preset = season.id; // a plain name the server will store ("New Year's" has an apostrophe)
  apply(); save();
  if (look.seasonEffects !== false) startEffect(season);
  if (!withMusic) return "";
  try {
    const r = await fetch("/api/seasonal/music", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ season: season.id }) });
    return (await r.json()).said || "";
  } catch (err) { return ""; }
}

export function turnOff(look, apply, save) {
  stopEffect();
  if (look.beforeSeason) {
    look.colors = { ...look.beforeSeason.colors };
    look.preset = look.beforeSeason.preset;
  }
  look.season = null;
  look.beforeSeason = null;
  look.seasonByHand = false;
  apply(); save();
}

/** On every dashboard load: keep the active season going, end it when its dates pass, or offer it. */
export async function onLoad(look, apply, save, previewId) {
  const today = previewId ? byId(previewId) : seasonFor();
  const active = byId(look.season);
  if (active && active !== today && !previewId && !look.seasonByHand) { turnOff(look, apply, save); return; }
  if (active) { if (look.seasonEffects !== false) startEffect(active); return; }
  if (!today || look.seasonPrompts === false) return;
  look.seasonAsked = look.seasonAsked || {};
  if (look.seasonAsked[askKey(today)] && !previewId) return;
  let spotify = false;
  try { spotify = (await (await fetch("/api/seasonal/music")).json()).spotify; } catch (err) { /* no music offer */ }
  offer(today, look, apply, save, spotify);
}

function offer(season, look, apply, save, spotify) {
  document.getElementById("seasonOffer")?.remove();
  const box = document.createElement("div");
  box.id = "seasonOffer";
  box.className = "season-offer";
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-label", `${season.name} mode`);
  box.innerHTML = `
    <div class="season-offer-emoji" aria-hidden="true">${season.emoji}</div>
    <div class="season-offer-body">
      <strong>It's ${season.name} time! Switch Echo to ${season.name} mode?</strong>
      <span>${season.blurb[0].toUpperCase() + season.blurb.slice(1)}. You can change it any time under Appearance.</span>
      ${spotify ? `<label class="season-offer-music"><input type="checkbox" id="seasonMusic" checked /> Play "${season.song}" on Spotify</label>` : ""}
      <div class="season-offer-buttons">
        <button type="button" class="ui-btn primary" id="seasonYes">Yes, switch</button>
        <button type="button" class="ui-btn" id="seasonNo">Not this year</button>
      </div>
      <span class="season-offer-note" id="seasonNote" aria-live="polite"></span>
    </div>`;
  document.body.appendChild(box);
  startEffect(season); // a preview of the effect while asking
  const remember = (answer) => {
    look.seasonAsked = { ...(look.seasonAsked || {}), [askKey(season)]: answer };
    save();
  };
  box.querySelector("#seasonYes").addEventListener("click", async () => {
    remember("yes");
    const music = !!box.querySelector("#seasonMusic")?.checked;
    box.querySelector("#seasonNote").textContent = music ? "Switching... starting the music" : "";
    const said = await turnOn(season, look, apply, save, music);
    box.querySelector("#seasonNote").textContent = said;
    setTimeout(() => box.remove(), said ? 3500 : 0);
  });
  box.querySelector("#seasonNo").addEventListener("click", () => {
    remember("no");
    stopEffect();
    box.remove();
  });
}
