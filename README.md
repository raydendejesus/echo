# Jarvis

A local, self-hosted AI assistant - a real LLM as the brain (running on your own GPU via Ollama), a natural voice, wake-word listening, memory, and increasingly, real-world capability, all without a cloud AI subscription.

This repo holds every version of the project side by side, each in its own folder, so the progression is visible rather than hidden behind force-pushed history. Each version is a complete, independently-runnable copy with its own README - pick the one you actually want to run (almost certainly the newest), or read through them all to see how it evolved.

## [`00-1/`](00-1/) - the original

Qwen2.5 14B as the brain. Voice, vision (photos/video/screen/camera), persistent memory, web research, and real phone calling. The foundation everything else was built on top of.

## [`00-2/`](00-2/) - Hermes 3, a reactive knowledge base, and location

Swapped the brain to Hermes 3 (less restrictive for a personal assistant's everyday use), added a knowledge base that's separate from memory (things it learns by researching, not things you told it), location awareness, and a fixed tool-calling reliability issue that could otherwise exhaust the model's turn budget on a single request. Also introduces this project's first optional addon - **Jarvis Auto Research** - background self-directed research on a strict daily cap, deliberately kept out of the core install.

## [`00-3/`](00-3/) - Browser Control and a plugin system

Everything from 00-2, plus the ability to act inside your browser: a Chrome extension gives Jarvis his own on-screen cursor that can click, type, scroll, and read whatever page you're currently viewing, only when you ask him to. Reads a page's real structure rather than taking screenshots, so it needs no extra VRAM for the normal case, with a pixel-based fallback (behind its own toggle and an on-screen warning) for pages that don't have any readable structure at all. Also introduces a real plugin system - drop a `.py` file into `backend/plugins/` and it's auto-discovered, no core file editing required, toggle and all - with a working expense tracker (`billing_tracker.py`) as the reference example, plus a real OAuth "Connect Google Account" flow (Gmail/Calendar/Drive, read-only) as a second example of a plugin that needs an external account rather than just a local file. Plugins, browser control, and screen/camera access are all dashboard-only by construction; phone calls use a small fixed tool list that never touches any of them. Also adds persona-level rules against fabricating tool results or invented tool *inputs* - both were caught happening during testing and explicitly fixed.

## [`echo-4/`](echo-4/) - Echo-4 edition to the Jarvis series

The project is renamed from **Jarvis** to **Echo** - this is the Echo-4 edition of the Jarvis series. Everything from 00-3, with a neutral-and-green dashboard, "Echo" as the wake word ("Jarvis" still works), opening apps / Steam games / Spotify / shortcuts by voice, per-contact call permissions, greetings built from the call's subject, a "Sir Page" toggle that phones you when something happens, and a compact toggle panel. Launcher files are `ECHO.bat` / `ECHO_TRAY.pyw` and the persona model is `echo`.

## [`echo-5/`](echo-5/) - Echo-5, cleaned up

The clean-up release. It was meant to run smoother, faster and better - and to be less bloated than every version before it - while keeping all of their abilities and the new ones. Same model, same toggles, same behavior as echo-4, but the server loads in about 57 MB instead of 76 MB and uses about 24% less memory running, loads and replies faster, and no reply can hold everyone else up. Its README has the measured comparison with every earlier version.

## [`echo-6/`](echo-6/) - Echo-6, the big update

Hears far better (even through a cheap mic), builds apps and runs code on its own in a sandbox - only with your approval - controls the PC, the TV and Netflix, screens unknown callers, reports on the PC's health and its own, and has a brand-new dashboard: a living 3D orb with a moon for every switch you turn on, Mission Control, a Live panel, a camera view, night mode and colors you choose (the old look is one click away). One-click `SETUP.bat`.

## [`echo-7/`](echo-7/) - Echo-7, cleaned

The clean-up of the big update: everything echo-6 does, at least as fast, but lighter - the server loads
19% faster with 27% less memory and 90% less reserved memory (heavy camera, screenshot and web libraries
now load only when first used), and the dashboard stops doing work it doesn't need to. Adds **seasonal
mode**: open Echo in December and it offers Christmas mode - a red theme, gentle snow, and "All I Want for
Christmas Is You" if Spotify is connected (plus New Year's, Valentine's and Halloween).

## [`echo-8/`](echo-8/) - Echo-8: Echo over

**ECHO MINI IS ON BY DEFAULT** - Echo's own brain is kept for the owner, and everyone else (callers,
contacts) talks to Echo Mini, a small brain that loads only while it's needed; switch it off for two
full Echos at once. Also: Echo finally receives its own rulebook at the dashboard (Ollama had been
silently dropping it) and the rulebook is a third the size; compact 8-bit context memory on by default,
with a choice of how much Echo can keep in view; a **calendar** (events with times, places and spoken
reminders); **sound outputs** - Echo's voice on your headphones and a speaker for white noise and
wake-up alarms; 3D generation no longer squeezes Echo's brain; browser control of the whole browser; and
a GitHub page - "check the echo GitHub", "go to the echo GitHub".

## Security fix for every version

If you run any version with phone calling: the public ngrok tunnel used for calls used to forward
*everything* to the assistant's server, so someone who learned the tunnel's address could read the phone
book and settings or send it commands. Every folder in this repo is now fixed - through the tunnel only the
phone-call endpoints answer, and (from 00-2 on) only with a private token the tray registers with Telnyx
automatically. Update to the current files. Details in [`echo-6/README.md`](echo-6/README.md).

## Which one should I run?

`echo-8`, unless you have a specific reason to want an earlier version's exact behavior. Start with [`echo-8/README.md`](echo-8/README.md); the full guide to what Echo can do is [`echo-6/README.md`](echo-6/README.md).

## License

**MIT** - see [`LICENSE`](LICENSE). It covers every version in this repo. You can use, change, share and even sell this code; the one condition is that you keep the copyright notice - the credit to **Rayden DeJesus** - in every copy or substantial part of it. If you build something on Echo, a link back to [github.com/raydendejesus/jarvis](https://github.com/raydendejesus/jarvis) is appreciated.
