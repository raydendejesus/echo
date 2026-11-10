# Echo-7 - cleaned

> **Echo-7 is the clean-up of the big update.** Everything echo-6 does, still here and still as fast -
> but lighter to load and lighter to keep open, with a few bugs fixed along the way. And one new thing:
> **seasonal mode** - open Echo in December and it offers to switch to Christmas mode.

For what Echo can do and how each part works, see [`../echo-6/README.md`](../echo-6/README.md) - echo-7
has every ability described there. This page covers what changed.

## Setting up

Same as echo-6: double-click **`SETUP.bat`** (it installs what's missing, step by step), then start Echo
with **`ECHO.bat`**. The dashboard is at `http://127.0.0.1:8765`; its **Setup** page shows what's ready
and fixes most things in one click.

## Seasonal mode (new)

When you open the dashboard during a holiday season, Echo asks once whether to dress up for it:

| Season | Dates | Look | Song (only if Spotify is connected, and only if you tick it) |
|---|---|---|---|
| Christmas | Dec 1 - Dec 26 | red theme, gold rings, gentle snow | "All I Want for Christmas Is You" |
| New Year's | Dec 31 - Jan 1 | midnight and gold, falling confetti | "Auld Lang Syne" |
| Valentine's | Feb 7 - Feb 14 | pink theme, floating hearts | "Can't Help Falling in Love" |
| Halloween | Oct 20 - Oct 31 | orange and purple, falling leaves | "Thriller" |

- **Yes, switch** changes the colors, starts the effect and (if you left the box ticked) plays the song.
  **Not this year** - it won't ask again until next year.
- When the season's dates pass, **your own colors come back by themselves**.
- **Appearance -> Seasons** lists every season: turn any of them on whenever you like (one you pick
  yourself stays until you turn it off), switch the "ask me" prompt off, or turn the falling effects off.
- Want a look now? Open `http://127.0.0.1:8765/?season=christmas` (or `newyear`, `valentines`,
  `halloween`) to preview the offer.
- The effect is a few dozen particles at 30 frames a second, paused whenever the tab is hidden, gentler
  with Windows' "reduce motion" setting, and it never blocks a click. The page can only ask the server for
  a *season's* song - it can't name a song or anything else to play.

## What got lighter (measured)

Measured on this project's own PC: the server loaded in a fresh process 7 times, median.

| | echo-6 | **echo-7** |
|---|---|---|
| Server load time | 888 ms | **717 ms** (-19%) |
| Server memory after loading (working set) | 71.6 MB | **52.4 MB** (-27%) |
| Server memory reserved (private) | 409.9 MB | **40.3 MB** (-90%) |
| Python modules loaded at start | 688 | **519** |
| Heavy libraries loaded at start | OpenCV, NumPy, mss, BeautifulSoup, lxml, ddgs | **none** |
| Dashboard JavaScript fetched on every open, beyond the page itself | +174 KB (3D-model loader and controls) | **0** until a 3D model is shown |

How:

- **Heavy libraries load on first use.** The camera (OpenCV/NumPy), screenshots (mss), web pages
  (BeautifulSoup) and web search (ddgs) were all loaded the moment the server started, even with those
  switches off. Now each loads the first time a tool actually needs it. This is the -90% above.
- **Settings are read from disk only when they change.** The listener and the dashboard ask for the
  settings many times a second; each ask used to re-read and re-parse the file.
- **Reference faces are cached.** Desk Guard re-read every saved face from disk every 15 seconds:
  89 ms -> 1 ms per check.
- **The launcher runs `pip` only when `requirements.txt` changed** - about a second off every start.
- **The dashboard does no work it can catch up on later.** In a hidden tab the clock, the switch counter
  and the canvas/3D checks pause and catch up the moment you come back; the voice-status check slows down
  instead of stopping, so the conversation log stays complete.
- **The orb draws at ~20 frames a second behind an open panel** (it's blurred there) instead of 60 - the
  dashboard's biggest steady cost, since the blur is redone every frame.
- **The 3D model viewer** fetches its loader and controls only when a model is shown, and stops drawing
  when you leave the 3D page (it used to keep drawing at 60 frames a second forever).
- **Phone call records are capped** at the newest 50 - inbound calls get no "call ended" event, so they
  used to build up for as long as Echo ran.
- Smaller things: one camera routine instead of two copies, unused imports removed, the listener's
  loudness math twice as fast (7.6 -> 3.6 microseconds per audio block), and wake-word matching skips
  impossible comparisons early.

What was deliberately **not** done: unloading the AI brain from the graphics card after it sits idle (an
idea from echo-5). It frees about 6 GB of graphics memory, but the first reply after a quiet spell gets
slower - and this release had to stay at least as fast as echo-6.

## Fixed

- **`requirements.txt` had two packages glued together** (`pygamepychromecast`), so a fresh install never
  got the TV library (and `pip` failed on every start). Fixed here and in echo-6.
- **Your chosen theme name was never remembered** - the settings store dropped any name it wasn't
  expecting, so "Midnight Gold" came back as unnamed. Names made of letters, digits, spaces and dashes are
  now kept (still nothing that could carry code into the page).
- **Asked to check a web address, Echo searched for it instead** - and once mistyped it and mixed in other
  sites' results ("check your GitHub repo" came back with another project's version numbers). Now when you
  give an address and ask what's there, Echo reads that exact page first. GitHub repos, folders and files
  are read through GitHub's public API - what's in it, the newest commits, the README - instead of as a
  page of menus, and a long page is cut at a line break and marked as cut (cut mid-word, the brain
  finished the sentence instead of answering).
- **"Put Stranger Things on the TV" sometimes played a YouTube clip** - when the search engine answered
  with next to nothing, Echo took that as "not on Netflix". A near-empty answer now proves nothing, and a
  netflix.com page for the show counts as proof it's there. YouTubers still go to YouTube.
- **"I couldn't find that on YouTube"** when the search engine came back empty - YouTube's own search is
  now the last fallback.

## Tests

All of echo-6's tests pass on echo-7: `tests\sim_world.py` 61/61 (including a fake GitHub repo whose
newest version can only be known by reading it), `tests\router_coverage.py` 78/78,
`tests\agent_projects.py` 4/4, `tests\sandbox_safety.py` 8/8. Seasonal mode was checked in a real
browser (offer, switching on and off, colors restored, a season you pick yourself surviving a reload, no
offer outside a season, every season's effect) and its music path against the test world's fake Spotify.

## License

MIT - see [`LICENSE`](../LICENSE). Use it, change it, share it or sell it; keep the copyright notice that
credits **Rayden DeJesus**.
