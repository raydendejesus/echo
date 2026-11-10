"""
Offers the brain only the tools that fit what sir said.

Echo has about seventy tools. An 8-billion-parameter brain choosing from all of them at
once picks badly - in testing it answered "make me a tip calculator app" by creating a
Google Slides deck. So before each request, sir's words (and his last couple of
messages, so "pause it" after "put X on the TV" still means the TV) pick the tool GROUPS
that could apply; the brain sees those plus a few basics. Fewer, relevant choices make
for better picks and a shorter prompt. When nothing matches clearly, every tool is
offered, exactly as before - the router can only narrow a clear request, never block one.
"""
import re

# group -> words that call for it
TRIGGERS = {
    "tv": r"\btvs?\b|television|telly|chromecast|\bcast\b|\blive\b|stream|youtube|youtuber|channel|on the tv",
    "music": r"spotify|\bsong|music|playlist|album|artist|track|liked songs|\bplay\b|shuffle|repeat|\bskip|next|previous|\bpause|resume|volume|louder|quieter|\bmute",
    "netflix": r"netflix|\bshow\b|episode|movie|\bwatch",
    "pc": r"\bopen\b|\bclose\b|launch|running|\bapps?\b|program|\bquit\b|\bkill\b|force close|steam|\bgame|shortcut|website|"
          r"\bgo to\b|take me to|pull up|bring up",
    "phone": r"\bcall(s|ing|ed)?\b|phone|\bring\b|\bdial|hang up|end the call",
    "eyes": r"screen|camera|webcam|\bsee\b|\blook\b|looking at|what's on my|read this|in front of",
    "browser": r"\bpage\b|\btabs?\b|\bclick|scroll|\btype\b|browser|this site|sign in|log in|\bgo to\b|pull up|\bchrome\b|"
               r"go back|go forward|\breload|refresh|press (enter|escape|tab)|\bsearch (for|up)\b",
    "repos": r"github|git hub|\brepos?\b|repositor",
    "places": r"\bwhere\b|direction|address|navigate|how do i get|\bmap|\bsave\b.*\bas\b|\bplaces?\b|location",
    "make": r"\bbuild|\bapp\b|game|animation|animate|website|web page|\bcode\b|script|program|canvas|research|project|calculator|timer|tracker|\bclock\b|interactive",
    # pictures and 3D models only when sir says so - otherwise "make me an animation" drifts to them
    "picture": r"picture|image|photo|drawing|\bdraw\b|painting|wallpaper|\b3d\b|3-d|three d|\bmodel\b|sculpt|render",
    "projects": r"project|approv|waiting on|waiting for|go ahead|\bdeny|decline|stop working|status|how's it going|how is it going|working on",
    "google": r"e-?mail|gmail|inbox|calendar|schedule|meeting|\bdrive\b|\bdocs?\b|slides|presentation",
    "calendar": r"calendar|schedul|appointment|\bevents?\b|meeting|agenda|\bremind(er)?s?\b|\bplans?\b|"
                r"what(?:'s| is| do i have) (on|going on|coming up|planned)|\b(am i|i'm) (free|busy)\b|"
                r"\b(dentist|doctor|birthday|party|dinner|lunch|interview|class|practice)\b.*\b(on|at|tomorrow|today|tonight|next|this)\b",
    "sound": r"\bnoise\b|white noise|rain sound|fan sound|sleep sound|\balarms?\b|wake me|wake up|\bstop\b|\bsilence\b|\bquiet\b",
    "power": r"shut ?down|turn off my (pc|computer)|turn off the (pc|computer)|power off|restart my|cancel the shutdown|\[passcode\]",
    "money": r"spent|\bspend|expense|bought|budget|\bcost|\bpaid\b|\bpay for",
    "accounts": r"github|\brepo|pull request|\bissue|discord|\bserver\b|notion",
    "memory": r"remember|forget|\bnote\b|what do you know|did i tell you",
    # A plain factual question needs only the basics (web search, knowledge, memory) - without this
    # group it matched nothing and the brain got all ~55 tools, and sometimes answered with nothing.
    "lookup": r"\bwho (won|is|was|are|were|made|wrote|plays|played)\b|\bwhat (is|was|are|were|does|did)\b|"
              r"\bwhen (is|was|did|does|will)\b|\bhow (many|much|old|tall|far|long|big)\b|\blatest\b|\bnews\b|"
              r"\bscores?\b|weather|forecast|\bprices?\b|\bstocks?\b|\btoday'?s\b|\b20\d\d\b|look (it|that) up|search (for|the web)",
    "health": r"\bstats\b|statistics|\bcpu\b|processor|\bgpu\b|graphics card|\bram\b|temperature|\btemps?\b|\bhot\b|overheat|"
              r"disk space|storage|space left|uptime|how('s| is) my (pc|computer)|how are you (doing|running)|"
              r"report on yourself|your (health|status|performance)|how much memory|vram|slow",
}

# tool -> group (tools not listed are basics, always offered)
GROUP_OF = {}
for _group, _names in {
    "tv": ["tv_youtube_creator", "tv_play_youtube", "tv_play_netflix", "tv_open_app", "tv_control", "tv_seek",
           "tv_whats_on", "tv_look", "tv_set_volume", "tv_sleep_timer"],
    "music": ["spotify_play", "spotify_my_playlists", "spotify_playback", "spotify_now_playing", "media_control"],
    "netflix": ["netflix_play", "netflix_control"],
    "pc": ["open_app", "close_app", "list_running_apps", "launch_steam_game", "open_shortcut", "add_shortcut",
           "remove_shortcut", "list_shortcuts", "media_control"],
    "phone": ["call_phone_number", "hang_up_call"],
    "eyes": ["view_screen", "view_camera"],
    "browser": ["browser_scan_page", "browser_read_page", "browser_click", "browser_type", "browser_scroll",
                "browser_click_by_sight", "browser_go_to", "browser_history", "browser_new_tab", "browser_list_tabs",
                "browser_switch_tab",
                "browser_close_tab", "browser_press_key"],
    "repos": ["check_github_repo", "open_github_repo", "save_github_repo", "list_github_repos", "forget_github_repo"],
    "places": ["save_address", "get_address", "list_addresses", "forget_address", "directions", "get_location"],
    "make": ["write_canvas_code", "start_project"],
    "picture": ["generate_image", "generate_3d_model"],
    "projects": ["start_project", "project_status", "list_projects", "stop_project", "list_requests",
                 "approve_request", "deny_request"],
    "google": ["search_gmail", "list_calendar_events", "search_drive_files", "create_google_slides"],
    "calendar": ["calendar_add_event", "calendar_events", "calendar_change_event", "calendar_remove_event"],
    "sound": ["play_noise", "stop_sounds", "set_alarm", "list_alarms", "cancel_alarm"],
    "power": ["shutdown_pc", "cancel_shutdown"],
    "health": ["pc_stats", "echo_self_report"],
    "money": ["log_expense", "get_spending_summary"],
    "accounts": ["list_repo_issues", "list_repo_pull_requests", "get_repo_activity", "list_discord_servers",
                 "list_discord_channels", "read_discord_channel", "search_notion"],
}.items():
    for _name in _names:
        GROUP_OF.setdefault(_name, set()).add(_group)

_COMPILED = {g: re.compile(p, re.I) for g, p in TRIGGERS.items()}


def groups_for(text: str) -> set[str]:
    return {g for g, rx in _COMPILED.items() if rx.search(text or "")}


def select(schemas: list[dict], said: str, recent: list[str] | None = None) -> list[dict]:
    """The schemas worth offering for this request. Sir's own words decide; his previous couple
    of messages add their groups so follow-ups ("pause it", "turn it up") keep their context."""
    wanted = groups_for(said)
    if not wanted:
        return schemas  # nothing clear: offer everything, as before
    for earlier in (recent or [])[-2:]:
        wanted |= groups_for(earlier)
    chosen = [s for s in schemas
              if s["function"]["name"] not in GROUP_OF or GROUP_OF[s["function"]["name"]] & wanted]
    return chosen if len(chosen) >= 3 else schemas
