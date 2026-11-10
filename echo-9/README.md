# Echo-9: Echo Turbo

> **THE TURBOQUANT ENGINE IS EXPERIMENTAL AND OFF BY DEFAULT.** Echo-9 can run Echo's own brain in a
> llama.cpp build with Google's TurboQuant, which squeezes the brain's context memory to about a third
> of its full size. All the plumbing is in and tested, but at release the engine itself **had not yet
> been run and measured on a real PC**: the memory figures below are estimates. With it off, echo-9
> runs exactly like echo-8. Windows and an NVIDIA graphics card only.

For what Echo can do and how each part works, see [`../echo-6/README.md`](../echo-6/README.md), then
[`../echo-8/README.md`](../echo-8/README.md) for Echo Mini, the calendar, sound outputs and brain memory.
echo-9 keeps all of it. This page covers what changed.

## Setting up

Same as before: double-click **`SETUP.bat`**, then start Echo with **`ECHO.bat`**. The dashboard is at
`http://127.0.0.1:8765`.

**Using browser control?** The Chrome extension changed again (version 1.2.0). Open `chrome://extensions`
and click the round reload arrow on **Echo Browser Control** once. An older copy keeps working; it just
opens new tabs on Google instead of empty ones.

## TurboQuant engine (experimental)

Echo's brain has two parts in graphics memory: the model itself (Hermes 3 8B, about 4.6 GB, already
stored at 4 bits) and its **context**, everything it can keep in view while it answers: its rules, the
tools, your saved facts, the conversation and any page it's reading. The context is the part that grows
with the window size, and with two full Echos at once.

- **Ollama's compact memory** (on by default since echo-8) stores the context at 8 bits: half its
  16-bit size.
- **TurboQuant** first rotates the context's numbers (a Walsh-Hadamard rotation), so they survive being
  stored at about 3 bits. Echo uses the pair the engine's authors recommend: keys at 8 bits, values in
  "turbo3" (3.1 bits). That puts the context at about **a third** of its 16-bit size.

TurboQuant isn't in Ollama or in the official llama.cpp (both declined it in 2026). Echo-9 can run Echo's
brain in [TheTom/llama-cpp-turboquant](https://github.com/TheTom/llama-cpp-turboquant), a fork of the
official llama.cpp that has it.

**Turning it on:** **Setup -> Brain memory -> TurboQuant engine (experimental)**.

1. Click **Download the engine (about 840 MB)**. It downloads the fork's prebuilt Windows release (CUDA
   12.4), built by GitHub's own build service. The file is checked against its published SHA-256, then
   unpacked to `tools/turboquant/` (about 1.5 GB). Nothing is downloaded until you click.
2. Tick the box. Echo takes its brain out of Ollama and starts the engine with the **same Hermes 3 file
   Ollama already has**, so there's no second copy of the brain. The engine listens only on this PC
   (`127.0.0.1:8767`).
3. Untick it to go back. The engine stops and Ollama loads Echo's brain again.

**What stays the same:**

- Only Echo's own brain moves. The vision model and Echo Mini stay on Ollama.
- Echo still talks to the brain the way it talks to Ollama. Each request is translated to the engine's
  (OpenAI-style) form and back, so tools, phone calls, projects and everything else work unchanged.
- **If the engine isn't answering, Echo's brain falls back to Ollama by itself.**
- Making a 3D model pauses the engine to free graphics memory, then starts it again, as echo-8 does with
  Ollama.
- The context window size and "two full Echos" settings apply to the engine too.

**Estimated graphics memory** for Echo's brain. The compact numbers were measured on an RTX 3090; the
TurboQuant numbers are worked out from them:

| Context window | One Echo (Echo Mini on): compact | One Echo: **TurboQuant** | Two full Echos: compact | Two full Echos: **TurboQuant** |
|---|---|---|---|---|
| 16K tokens | 5.8 GB | ~5.4 GB | 6.9 GB | ~6.2 GB |
| 32K tokens | 6.9 GB | ~6.2 GB | 8.9 GB | ~7.9 GB |
| 64K tokens | ~9.1 GB | ~7.9 GB | ~13.6 GB | ~11.2 GB |

The saving grows with the context: about 1 GB at 32K with two full Echos, about 2.4 GB at 64K. Speed and
answer quality on Echo's own tests haven't been measured yet. That's the next step, along with storing
the context entirely in 3-bit form (about 6.4 GB at 32K with two Echos).

**What TurboQuant doesn't shrink:** the brain itself. Hermes 3 is already a 4-bit file. The fork also
has TurboQuant-style formats for the model file (TQ4_1S and TQ3_1S), which could make the brain smaller
too. Those aren't in echo-9.

## Websites by name, and new tabs

- **"Open GitHub"**, "open Excalidraw", "go to Gmail" open the site itself. Echo knows about 25 common
  sites by name (GitHub, Excalidraw, Gmail, Google Docs, Sheets, Drive and Maps, ChatGPT, Claude, Reddit,
  Wikipedia, YouTube, Netflix, Figma, Canva, Stack Overflow, Hugging Face and more). Your own saved
  shortcuts come first. This used to answer "I couldn't find an app called GitHub".
- **A site by a name Echo doesn't know** ("go to the Godot docs") goes straight to the top search result
  through DuckDuckGo. Anything you ask to *search* for ("search for cheap flights", "pull up the
  weather") is still a Google search.
- **"Open a new tab"** opens an empty tab. "Open GitHub in a new tab", "switch to my YouTube tab" and
  "close this tab" work as in echo-8.
- **Echo never navigates its own dashboard away.** If the dashboard is the tab you're on, the page opens
  in a new tab beside it.
- **"Open GitHub" means the site**, even if GitHub Desktop is installed. Say "open GitHub Desktop" for
  the app. An installed app with exactly the name you say still wins ("open Discord").

With **Browser control** on and the extension connected, sites open in a new tab that Echo can then read
or use ("what does it say?", "click sign in"). With it off, they open in your normal browser.

## Also fixed

- **"Open YouTube" sometimes answered "Which app should I open?"** About 1 time in 3, the brain picked
  the right tool but left out which app. When the name is missing, Echo now takes it from what you said.
- The conversation test (`tests\sim_world.py`) brings its own made-up phone book contact. Before, its
  "call Danie" scenario only passed on a PC whose phone book already had a Danie.

## Tests

| Test | Result | What it covers |
|---|---|---|
| `tests\sim_world.py` | 83/83 | New: open Excalidraw, open GitHub, go to a site by name, open a new tab |
| `tests\router_coverage.py` | 105/105 | |
| `tests\turbo_engine_routing.py` | 20/20 | New. Requests translated to the engine's form and back; only Echo's own brain goes to the engine; falls back to Ollama when the engine isn't answering. Runs without the engine. |
| `tests\brain_priority.py` | 14/14 | |
| `tests\calendar_parsing.py` | 56/56 | |
| `tests\sound_alarms.py` | 16/16 | |
| `tests\phone_silence.py` | 8/8 | |
| `tests\agent_projects.py` | 4/4 | |
| `tests\sandbox_safety.py` | 8/8 | |

The brain varies a little from run to run. Across repeated full runs, about one scenario in 80 missed
once and then passed when run again: Echo asked "you want me to shut down your computer?" before asking
for the passcode, or searched the web before starting a project.

## License

MIT - see [`LICENSE`](../LICENSE). Use it, change it, share it or sell it; keep the copyright notice that
credits **Rayden DeJesus**.
