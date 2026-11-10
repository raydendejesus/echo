# Echo-6

> **Echo-6 is the "control everything, hear better, work on its own" release.** Echo now hears far better
> (even through a cheap mic), builds apps and runs code by itself in a sandbox - only with your approval -
> controls the PC, the TV and Netflix, screens unknown callers, keeps an address book, and is much easier
> to set up. Same brain model (`hermes3`), same size - everything from echo-5 is still here.

## The new dashboard

Open `http://127.0.0.1:8765` once Echo is running.

- **The orb** - Echo is a living 3D orb with three rings. Every switch you turn on adds a small **moon** in
  its own color circling it, so you can see at a glance what Echo is allowed to do. The orb pulses when Echo
  listens, thinks and speaks. Click it to talk.
- **Left arrow -> Mission Control** - a grid of tiles (Talk, Phone, Places, Switches, Projects, Thinking,
  Camera, Canvas, 3D, Photos, Connections, Hearing, Setup, Appearance, Help). Each opens **one** page with
  its own tabs along the top, so nothing is spread out.
- **Right arrow -> Live** - what's happening right now: Echo's state, the mic level, your last request and
  what Echo did with it, Spotify, the TV, calls, projects, and **this PC** (processor, memory, graphics card
  temperature, power and drive space).
- **Switches** - every switch, sorted by category with a filter, each with a plain description and a
  **"What Echo did with it"** history of how it was actually used.
- **Camera** - see what Echo sees, have it **label** what's in the picture, and **Remember me** so it knows
  what you look like (camera access stays off until you switch it on).
- **Projects** - open each app Echo built, and switch to **Code** to read exactly what it wrote.
- **Photos, 3D and Canvas** each get their own page.
- **Appearance** - pick the orb's colors, the background and the accent color, or a preset; **Night mode**
  dims everything and slows the orb down. Prefer the old dashboard? **Classic look** is one click away, and
  the classic page has a button back.

## PC stats and Echo's own health

- "How's my PC doing?" / "How hot is the graphics card?" - processor and memory use, graphics card
  temperature, load, memory and power, drive space (warns when a drive is nearly full), uptime and what's
  using the most memory.
- "How are you running?" / "Give me a report on yourself" - how long Echo has been up, how much memory its
  parts use, which AI models are loaded on the graphics card, whether it can hear you and through which mic,
  how fast it has been answering, and whether its recent actions worked.

Read straight from Windows and `nvidia-smi` - no extra libraries, and it only reads.

## Setting up (new)

1. Double-click **`SETUP.bat`**. It checks for Python 3.12 and Ollama (and offers to install them), then
   downloads everything Echo needs, step by step. Safe to run again any time.
2. Start Echo with **`ECHO.bat`** - if anything essential is missing it sends you back to setup.
3. Open the dashboard's **Setup** tab: a live checklist of what's ready, what's optional, and how to fix
   anything missing - several items have a one-click **Fix it** button.

## Hearing (much better)

Measured on 160 recordings of real Echo requests (including names it used to mangle, like "The Burnt
Peanut") through three simulated microphones, plus pure noise:

| | Good mic | Cheap headset | Bad mic | Phrases made up from noise | Speed |
|---|---|---|---|---|---|
| echo-5 (Whisper medium.en) | 93% | 89% | 73% | 7 of 12 | 0.38 s |
| **echo-6** (large-v3-turbo + boost + word list + silence filter) | **94%** | **91%** | **82%** | **0 of 12** | **0.21 s** |

- **Same size, twice as fast:** Whisper large-v3-turbo is the same size class as medium.en.
- **Automatic volume boost:** quiet or cheap mics are brought up to a normal level before recognition.
- **Words Echo should know:** names it couldn't know (YouTubers, friends, games) - learned automatically
  from your phone book, address book and what you ask it to play, and editable on the Audio tab.
- **No more imagined commands:** the old hint sentence ("Hey Echo, how can I help you today?") was being
  "heard" in silence and sent as a command. Gone, with a filter for the phrases speech models invent.
- **Smart end of speech:** after 1.2 s of quiet Echo checks whether your sentence sounds finished and
  answers straight away; if you trail off ("and...", "um") it keeps listening up to 3 s.
- **Follows your mic** when you switch headsets, with a live level meter on the dashboard.

## Smarter, without a bigger brain

The brain is still Hermes 3 (8B). The gains come from how Echo uses it:

- **Tool router:** Echo has about 70 tools; an 8B brain picks badly from all of them at once. Echo now
  offers only the tool groups that fit what you said (TV, music, PC, phone...) plus the basics.
- **Recovers from near-misses:** if the brain writes a tool call as text, leaves out which action it
  meant ("pause", "skip"), drops the message for a call, or picks "call" when you said "hang up", the
  tool works it out from your own words instead of failing - and Echo never reads code aloud.
- **Knows the real state of things:** your address book and your projects' real progress are given to it
  with every message, so "how's the project going?" is answered from fact, never invented.
- **One call per request:** a placed call ends the turn and the same number is never redialled within
  two minutes.

## Working on its own - projects, approvals and the sandbox

Say "make me an app that...", "write a script that...", or "research the best..." and Echo works on it in
the background: it plans, builds the page or program, **checks its own work** (compiles the page's
scripts, or runs the program in the sandbox), reviews the result against what you asked, fixes what's
wrong, and tells you when it's done. Apps open on the dashboard's **Projects** tab - and from your phone
at the Echo Switch's `/apps` page over Tailscale.

**Approvals (your rules):**
- Always allowed: browsing the web, thinking and taking notes, and working on a project you approved
  **less than a day ago**.
- Everything else needs your OK first. Asking for a project is your approval for it, for 24 hours; after
  that it expires and Echo must ask again.
- Approve at the PC with a click or by saying so. **By phone, you must say your passcode** - same as
  shutting the PC down.
- When Echo works on its own, the only person it can phone is **you** (via Sir Page).
- Echo may also *suggest* a project of its own after you've been away a while - a suggestion only, nothing
  is built until you approve it.

**The sandbox:** code Echo writes runs at Windows' *low integrity* level (Windows itself blocks it from
changing your files or Echo's), inside a job object that caps memory at 1 GB and kills everything after
60 seconds, with its own standalone Python and a bare environment. Apps run in the browser's sandboxed
frame. `tests\sandbox_safety.py` checks all of this (8/8). It can still read files and reach the
internet; full isolation needs Windows Sandbox, which needs virtualization switched on in the BIOS.

## Also new

- **Respectful greetings** - "I just got done at the gym" -> "Good workout, sir. What can I assist you with?"
- **Address book** - name places ("home", "gym", "favorite restaurant"), ask for an address or directions.
- **TV** - "put the Burnt Peanut on the TV" plays his live stream if he's live, otherwise his newest video;
  "is he live?", fast forward / back, "what's on the TV", a description of the TV screen, exact volume,
  and a sleep timer.
- **Unknown callers** are asked who they are, saved with calling off, and marked **SCAM** in red if they
  sound like one.

## Security fix - please update (affects every earlier version too)

Phone calls reach Echo through a public ngrok tunnel. Until now that tunnel forwarded **everything** to
Echo's server, so anyone who learned the tunnel's address could read the phone book and settings, or send
Echo commands. Now:

- A request that didn't come straight from your PC (through the tunnel, or a web page using a fake host
  name - "DNS rebinding") only reaches the phone-call endpoints. Everything else answers "not found".
- Those phone endpoints also need a private random token. Echo makes it once, keeps it in your private
  `telephony_config.json`, and registers the tunnel address with the token at Telnyx automatically -
  nothing to set up.
- The same fix has been applied to every older folder in this repo (in 00-1, which sets its Telnyx
  address by hand, only the phone endpoints are reachable through the tunnel).

Also fixed while testing: the approve/decline tools now only act when your own words actually approve or
decline (the brain once "declined" a waiting request right after a wrong passcode), a YouTuber sent to the
Netflix tool now plays on YouTube, plain factual questions no longer occasionally get an empty answer, and
the dashboard no longer serves stray log files.

## Testing it all without touching anything real

`tests\sim_world.py` runs Echo's real brain and real tool code against a fake PC, TV, Spotify, phone line
and browser, from a temporary copy of Echo's data - every tool exercised, nothing real touched.
`tests\agent_projects.py` builds real projects and checks they're right. `tests\sandbox_safety.py`
checks the sandbox. `tests\router_coverage.py` checks that the tool router never hides the tool a request
needs (77 everyday requests, no brain needed - runs in a second).

## Casting to a TV: what you need

**YouTube works on any TV with Google Cast** (a Google TV, a Chromecast, or a TV with Chromecast built in).
Echo casts it the same way your phone's Cast button does - no setup.

**If you want to put anything besides YouTube on the TV - Netflix, Disney+, Hulu and the rest - you must
have a Google TV** (or another Android TV). Netflix and most streaming apps don't accept casting from
anything except their own phone apps, and mirroring a screen shows a black picture because they block
screen capture. A Google TV gets around both: it is a small Android computer, so Echo can open the real
Netflix app *on the TV* straight to the show you asked for, and press the remote's buttons for you.

### How it works

1. **Finding the TV** - Cast devices announce themselves on your Wi-Fi. Echo listens for that once,
   remembers the TV's address, and connects straight to it after that (about 0.3 s).
2. **YouTube (Google Cast)** - Echo finds the video, then tells the TV "open YouTube and play this".
   The TV streams it itself, so the PC does no video work.
3. **Netflix and other apps (ADB)** - with network debugging switched on, the TV accepts commands from
   your PC over Wi-Fi: "open the Netflix app at this show", "press pause". This uses Google's own
   Android Debug Bridge (`adb.exe`, signed by Google), kept in `tools/platform-tools/`.
4. **Pause, volume, skip, home, TV on/off** - the TV's remote buttons, sent the same way. Without ADB,
   Echo can still pause, resume, stop and change volume through Cast.

Everything stays on your home network; nothing goes through the internet except the video itself.

### One-time TV setup (Google TV), for Netflix and other apps

1. On the TV: **Settings -> System -> About**, scroll to **Android TV OS build**, and press OK on it
   **7 times** until it says you are now a developer.
2. **Settings -> System -> Developer options** -> turn on **USB debugging** (on some TVs it's called
   **Network debugging** or **ADB debugging**).
3. Make sure the TV and the PC are on the **same Wi-Fi**.
4. Ask Echo to put something on the TV. The first time, the TV asks **"Allow USB debugging?"** - tick
   **Always allow from this computer** and press **OK**.

Some newer Google TVs only offer **Wireless debugging** with a pairing code instead of the switch above;
those need a one-time pairing step that this release doesn't do for you yet.

### What to say

- "Play the Pokemon theme song on the TV" - YouTube
- "Put Stranger Things on the TV" - opens the Netflix app on the TV at the show
- "Open Disney Plus on the TV"
- "Pause the TV", "turn the TV up", "mute the TV", "turn the TV off"

## Everything new in Echo-6

| Ability | What it does |
|---|---|
| **See and close apps** | "What's running on my computer?", "close Spotify", "force close Discord". Closes politely first (apps can still ask to save); never closes Echo itself or force-closes Windows. |
| **Sir switch (phone book)** | A gold switch per contact. A call from that number is you: on the phone you get the same PC control as at the desk - apps, Spotify, Netflix, the TV, shutdown. Caller ID can be faked, so only switch it on for your own number. |
| **PC shutdown with a secret passcode** | "Turn off my PC" -> Echo asks for your passcode. Up to 5 a day, 3 wrong tries locks it for the day, one minute to say "cancel shutdown". |
| **Passcode that can't be read** | The passcode file holds only a salted scrypt fingerprint, not the words. What you say is checked the moment it arrives and the passcode is cut out before anything keeps a copy - memory, the AI model, call history, logs and the dashboard only ever see `[passcode]`. Change it any time on the dashboard. |
| **Unknown-caller screening** | A number not in the phone book is asked who they are and what it's about (no tools, no information, two sentences at most), then added with calling **off**. If it sounds like a scam, the name is shown in **red** with a **SCAM** tag and the number is turned away from then on. Tested on 140 sample calls (50 + 20 scams, 50 + 20 real): every scam caught, no real caller flagged. |
| **Thinking window** | A small panel left of the phone book showing what Echo heard, the tools it used, what they returned, what it said and how long it took. |
| **Mic follows your device** | Switch headsets and Echo switches with you; a live meter shows which mic it's on and whether it hears you. |
| **Netflix** | "Play Squid Game on Netflix" opens it playing; an unclear title opens Netflix's search instead of guessing. "Pause Netflix", "skip ahead 2 minutes", "make Netflix full screen". |
| **TV** | YouTube on any Cast TV; Netflix and other apps on a Google TV (see above). |
| **Tap to switch Echo on and off** | A tiny switch (`echo_switch.pyw`) that a phone automation - for example an iPhone Shortcut on an NFC tag - can call over Wi-Fi or Tailscale with a secret key. It loads no AI; Echo itself still doesn't start with Windows. |

| **PC stats & self-report** | "How's my PC doing?", "how are you running?" - see above. |
| **New dashboard** | The orb, Mission Control, Live panel, camera view, code view, night mode, colors, classic look - see above. |

Private files never leave your PC - your settings, accounts, passcode fingerprint, phone book, address book,
faces, memory, projects, approvals and history are all listed in this folder's `.gitignore`.

## License

MIT - see [`LICENSE`](../LICENSE). Use it, change it, share it or sell it; keep the copyright notice that
credits **Rayden DeJesus**.
