# Echo-8: Echo over

> **ECHO MINI IS ON BY DEFAULT.** Echo's own brain is kept for **you** - at the dashboard, or calling
> from a number marked **sir** in the phone book. **Everyone else** (callers, contacts) talks to
> **Echo Mini**: a small 3-billion-parameter brain (Qwen 2.5 3B with Echo's personality) that loads only
> while it's in use - about 3 GB of graphics memory then, none the rest of the time - and is a one-time
> 2 GB download. A caller keeps their whole conversation, whichever brain answers.
> Turn it off in **Switches -> Echo itself -> Echo Mini** to get **two full Echos at once** instead, so
> callers get the full Echo too - at about twice the context memory (see below).

For what Echo can do and how each part works, see [`../echo-6/README.md`](../echo-6/README.md); echo-8
keeps everything from echo-6 and echo-7. This page covers what changed.

## Setting up

Same as before: double-click **`SETUP.bat`**, then start Echo with **`ECHO.bat`**. The dashboard is at
`http://127.0.0.1:8765`. SETUP now also downloads Echo Mini and sizes Echo's memory to fit your graphics
card (see below). The first time Echo starts it may restart Ollama once, so Ollama runs with Echo's
brain settings (compact memory, one or two conversations at once).

**Updating from an earlier version with browser control?** The Chrome extension changed. Open
`chrome://extensions` and click the round reload arrow on **Echo Browser Control** once. Until you do,
Echo says so instead of failing quietly.

## The biggest fix: Echo now actually gets its own rules - and they're a third the size

Echo's personality and rulebook live in its Ollama model. Every dashboard request started with a block
of saved facts (your memories, phone book, address book), and **when a request starts that way, Ollama
silently drops the model's built-in instructions.** So at the dashboard, Echo had been running without
its rulebook - including "never make up what a tool returned". Now Echo sends its rules itself, first,
on every request. (Phone calls were not affected: they always sent their own instructions.)

The rulebook itself was rewritten from **about 2,800 tokens to about 800**: the same personality and the
same rules (honesty about tools first, how to use the canvas, the browser, the phone), without the
repetition - and the per-tool details now live with each tool. Every test passed the same before and
after. A shorter rulebook leaves more room in view for the conversation and starts every reply sooner.

**Echo also knows the date and time now** - it had no clock at all, so "what's on Friday?" or "remind me
tomorrow" had nothing to count from.

**And it knows *when* things were said.** Wherever time passed in a conversation, Echo is told so
("3 days passed - everything above was said Saturday at 4 PM"), and each saved memory carries the day it
was saved. A new rule says it never claims you said something unless it's really there - and never
calls something from days ago "just now". Asked "what did I ask you a minute ago?" after a three-day
gap, it used to have nothing to go on; now it answers from the conversation itself.

## Brain memory - choose how much Echo can keep in view

**Setup -> Brain memory (context window)** chooses how much Echo can see while it answers: its rules,
the tools, your saved facts, the conversation so far and any page it's reading. Your saved memories are
kept at every size - this is only how much fits in view at once. Measured on an RTX 3090 (speed doesn't
change, only graphics memory):

| Context window | Normal (16-bit) | **Compact memory (8-bit, the default)** |
|---|---|---|
| 8K tokens | 5.7 GB | 5.2 GB |
| **16K tokens (default)** | 6.8 GB | **5.8 GB** |
| 32K tokens | 9.0 GB | 6.9 GB |
| 64K tokens | ~13.6 GB | ~9.1 GB |

- **Compact memory is on by default.** It stores the context at 8 bits instead of 16 - about half its
  graphics memory, about 4% slower replies, and every test answered the same. It's an Ollama-wide
  setting (it restarts Ollama). Untick it on the Setup page to go back.
- **SETUP picks a size that fits your card**: with compact memory, 8 GB cards and up get 16K. Change it any
  time; sizes that won't fit your card are greyed out.
- **Echo Mini off = two full Echos**, which keeps room for two conversations all the time: that doubles
  the context part of the memory above (32K with compact memory: 6.9 GB with Echo Mini, 8.9 GB measured
  with two full Echos).
- Pages Echo reads (web pages, GitHub repos, browser tabs) get more room as the context grows.

Why these sizes: in Steam's October 2026 hardware survey the most common graphics cards have **8 GB**
(27% of PCs), then 16 GB (26%) and 12 GB (13%). Echo's brain alone is 4.7 GB, so echo-8 is tuned to work
well on an 8-12 GB card.

**What about TurboQuant?** Google's TurboQuant squeezes this same context memory down to about 3-4 bits.
As of this release it isn't in Ollama or in the official llama.cpp that Ollama runs on (both declined
the changes in mid-2026) - only in unofficial llama.cpp forks and in vLLM, a Linux server engine.
Compact memory is the official 8-bit version of the same idea. The brain itself (the model's weights)
is already stored at 4 bits.

## Echo Mini, and who comes first

With Echo Mini on (the default):

1. **The owner gets Echo's own brain** - at the dashboard, or on a call from a number marked **sir**.
2. **Everyone else talks to Echo Mini** - phone book contacts, callers, people Echo calls for you. A
   caller keeps the whole call; the conversation belongs to the call, not to the brain.
3. **Background projects** use Echo's own brain, and pause while the owner is talking.

With Echo Mini off, Ollama runs two conversations at once on Echo's own brain and everyone gets the
full Echo. If Echo Mini isn't downloaded yet, everyone gets Echo's own brain.

Echo Mini is meant for simple things - small talk, quick look-ups, taking a message. Why Qwen 2.5 3B, on
eight phone-style requests (look-ups, "remember...", small talk, goodbyes):

| Small model | Right tool or plain answer | Notes |
|---|---|---|
| **Qwen 2.5 3B** | **7 / 8** | about half a second a reply |
| Llama 3.2 3B | 5 / 8 | saved "memories" it wasn't asked to |
| Hermes 3 3B | 1 / 8 | wrote its tool calls as broken text |

## Calendar

A new **Calendar** page: a month view, and events with a **name, day, start and end time, place, what
it's about**, and an optional **reminder** (when it starts, 10 or 30 minutes, an hour or a day before).
Or just tell Echo:

- "Put the dentist on my calendar for Friday at 3 at Main Street Dental"
- "Add a meeting with Sam next Tuesday at 10 about the budget"
- "What do I have going on this week?" - "Move the dentist to 4" - "Cancel the dentist"

Days and times are worked out from your own words by Echo, not guessed by the brain: "tomorrow", "next
Friday", "November 14th", "11/14", "the 20th", "3pm", "half past 2", "noon". A reminder is said out loud
by the always-on listener (its microphone pauses while it speaks), shown on the dashboard, and phoned
through if **Sir Page** is on. Echo also keeps the next seven days in view, so it can mention what's
coming up. The calendar is kept on your PC in `backend/calendar.json` - private, never committed.

## Sound outputs, white noise and alarms

**Setup -> Sound outputs** has two outputs:

- **Echo's voice** - always on: the Windows default (your headphones, as before), or any output you pick.
- **Speaker** - switch it on and pick one: white noise and wake-up alarms play there instead, while
  Echo's voice stays on your headphones. Each has a **Test** button that plays a short chime.

Then:

- **"Play white noise"**, "rain sounds for an hour", "fan noise" - white, pink (like rain) or brown (like a
  fan or a waterfall) noise, made on the PC as a seamless loop - nothing is downloaded. "Stop" stops it.
- **"Wake me up at 7"**, "an alarm for 6:30 every weekday" - a chime, then Echo says the time and the
  day's first events from your calendar, again every minute for five minutes until you say stop. Alarms
  are also on the Setup page, to add or remove.

## Phone calls

- **"Call me"** (or "call sir") rings your own phone - it used to answer "I couldn't find 'me' in the
  phone book". A phone book entry named "sir" comes first; otherwise the number set up for calling.
  Echo says "Calling you now, sir" instead of reading your number out loud.
- **Voicemail is hung up on, not talked to.** Declined, a call to you went to voicemail and Echo kept
  saying "Sorry, I didn't catch that" into it until the voicemail cut off. Now the answering-machine
  result ends the call, and on any call silence gets "didn't catch that" at most twice, then a goodbye.
- On a call from you, Echo knows the date, the time and your next few days of calendar, and can use the
  calendar, alarms and white noise.

## 3D models no longer squeeze Echo

A 3D model needs most of a 24 GB graphics card, and Echo's brain held 7-14 GB of it at the same time.
Now Echo's brain steps out of graphics memory while a 3D model is made (conversations meanwhile go to
Echo Mini when it's on), and ComfyUI lets go of its models afterwards so the brain fits again.

## Browser control, a lot more of it

With **Browser control** on, Echo can now drive the whole browser, not just the page in front of you:

- **Go anywhere** - "go to github.com/...", "pull up the weather", "search for cheap flights in my browser"
  (words that aren't an address become a Google search). Works from any tab, even a new tab page.
- **Back, forward, reload.**
- **Tabs** - "what tabs do I have open?", "switch to my YouTube tab", "close this tab". You name tabs your
  own way ("the github one"); Echo matches the name, not a number.
- **Keys** - Enter, Escape, arrows, Page Down; and typing into a search box can press Enter to submit.
- Scroll to the very top or bottom, and read longer pages when the context window has room.

## GitHub page

A new **GitHub** tile saves repos under your own names. Then:

- **"Check the echo GitHub"** - Echo reads it through GitHub's public API (no account needed for public
  repos): what's in it, the newest commits and the README.
- **"Go to the echo GitHub"** - opens it in your browser.
- Any github.com address works the same way without saving it. Saved repos are private to your PC.

## Reading links you give it

Give Echo an address and ask what's there ("check github.com/...", "what's on example.com/news") and it
reads that exact page first. It used to search the web for the address instead - once with the address
mistyped - and mixed in other sites' results. Long pages are cut at a line break and marked as cut: cut
mid-word, the brain finished the sentence instead of answering.

## Also fixed

- **"Put Stranger Things on the TV" sometimes played a YouTube clip** - an almost-empty answer from the
  search engine was taken as "not on Netflix". Now a near-empty answer proves nothing, and a netflix.com
  page for the show counts as proof it's there. YouTubers still go to YouTube.
- **"I couldn't find that on YouTube"** when the search engine returned nothing - YouTube's own search is
  now the last fallback.
- The phone instructions no longer include an example line that small brains repeated word for word.

## Tests

`tests\sim_world.py` 79/79 (new: browser go-to, search, tabs, back; GitHub repos checked and opened;
calendar add, list, move and remove; white noise and alarms; "call me"; a three-day-old request that
must not be called "just now"), `tests\router_coverage.py` 96/96, `tests\brain_priority.py` 14/14 (who
gets Echo's own brain and who gets Echo Mini), `tests\calendar_parsing.py` 56/56 (days and times from
your words, reminders), `tests\sound_alarms.py` 16/16 (noise, chimes and alarms, nothing played),
`tests\phone_silence.py` 8/8 (voicemail is hung up on; silence ends the call), `tests\agent_projects.py`
4/4, `tests\sandbox_safety.py` 8/8.

## License

MIT - see [`LICENSE`](../LICENSE). Use it, change it, share it or sell it; keep the copyright notice that
credits **Rayden DeJesus**.
