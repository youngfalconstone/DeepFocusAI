# DeepFocus AI

A personal focus tool for Windows. Pick the services and files that pull you away, set the time they should come back,
and DeepFocus blocks the domains, hides the files, closes the apps, and restores everything on schedule, even if the
app is closed, crashes, or the PC reboots.

Ships as a standalone Windows app (`dist\DeepFocus\DeepFocus.exe`, own icon, no console) built with PyInstaller.
The source needs no third-party packages: plain Python 3 and tkinter, with a custom widget kit. The look is a deep
indigo "night sky" theme: a hero panel with a gradient sky, stars and mountain ridges drawn in code, violet
gradient buttons and rings, Segoe UI Variable type and Segoe Fluent Icons. A lavender light theme and the Windows
accent colour are available in Settings.

## Run

Start Menu or Desktop: **DeepFocus AI**. The executable is `dist\DeepFocus\DeepFocus.exe`. Only one copy runs at a
time; launching it again brings the open window forward.

From source:

```
cd <folder you cloned into>
pythonw -m deepfocus
```

Install location matters. The scheduled tasks run the helper with administrator rights from wherever the exe
lives, so on a machine shared with other accounts, or if you do not trust every program that runs under your own
account, place `dist\DeepFocus` somewhere only administrators can write (for example under `C:\Program Files`) before
starting a session. The default per-user location is fine for a single-user PC.

Rebuild the exe and shortcuts after changes:

```
powershell -ExecutionPolicy Bypass -File build\build.ps1
```

Undo everything from outside the app at any time with `EmergencyRestore.cmd`, or
`DeepFocus.exe helper restore` from an elevated prompt.

## What it does

| Step | Mechanism | Needs admin? |
|---|---|---|
| Block domains | Appends a marked block of `0.0.0.0` / `::1` lines to `C:\Windows\System32\drivers\etc\hosts`, after backing the file up | Yes |
| Close the DoH bypass | Sets the Chrome, Edge, Brave and Firefox enterprise policies that turn DNS-over-HTTPS off, recording the previous values | Yes |
| Hide files | Sets the Hidden + System attributes on each chosen file or folder, recording the original bits. Nothing is moved, copied or modified | No |
| Strict hide (optional) | Adds one read-deny permission entry per hidden file so it cannot be opened even if found; removed at restore | No |
| Close apps | While the window is open, closes known distracting processes (Discord, Steam, Roblox, ...) every 2 seconds | No |
| Scheduled restore | Registers the task "DeepFocus AI Restore" with a time trigger, a logon trigger, "run as soon as possible if missed" and "wake the computer" | Yes |
| Enforcement | Registers "DeepFocus AI Enforce", which runs every minute (and at logon): re-applies the hosts block, file attributes, DoH policies and the restore task if any were undone by hand, switches the blocks off and on at the free windows of a weekly schedule, and performs the restore itself once the session has expired | Yes |

The app itself never runs elevated. It lists exactly what it needs on the Review screen, then launches
`DeepFocus.exe helper apply` through a single UAC prompt. The helper writes `~\.deepfocus\session.json`; the scheduled
tasks later run `helper restore --if-expired` and `helper enforce`, which strip the hosts block, unlock and restore
file attributes exactly, put the DoH policies back, delete both tasks and archive the session to `history.json`.

## Safety design

- **Independent restore.** Task Scheduler, not this app, ends the session. A crash or reboot cannot leave anything blocked.
- **Two lines of defence.** The restore task fires at the end time; the enforcement task also restores once the time has passed, and cleans up any leftovers if the session record is gone.
- **Idempotent, defensive restore.** `restore` removes any DeepFocus block it finds in the hosts file even if the session record is missing, and never touches lines outside the two marker comments.
- **Exact file restore.** Original attribute bits are recorded per file and reapplied; the lock entry is removed before that.
- **Path guard.** Paths inside Windows, Program Files, ProgramData, AppData, the profile root, the Desktop folder itself, the hosts file, the app folder and the state folder are refused by both the GUI and the helper, so a mistaken or hand-edited plan can never hide what the system or the restore depends on. Paths that are, or lie under, a symbolic link or junction are refused too (a link could be re-pointed at something protected after the check).
- **The helper trusts nothing.** `helper apply` re-validates the plan itself: session id format, restore time, list shapes and sizes, option types, and every hostname (normalised, protected names dropped) and path (guard) again at use time. The plan file must live inside the state folder and not behind a link, and all three helper commands refuse to run if the state folder itself has been replaced by a link or junction. At restore and enforce time the recorded file paths go through the guard again, and DoH restore only writes to the five known browser policy values, so an edited `session.json` cannot point the elevated helper at other files or registry keys. System tools (`schtasks`, `icacls`, `ipconfig`, `whoami`) are always run from `System32` by absolute path. Warnings from every step are written to the log.
- **AI features.** Only the typed name (AI Block) or your messages plus an optional short session summary (Assistant) leave the machine. Endpoints must be https (http only for localhost), responses are size-capped (2 MB JSON, 120k characters streamed, 1 MB per event), messages are capped at 8,000 characters and history at 48,000, Stop closes the socket, model text is stripped of control and bidi characters before it reaches a label or menu, and the key lives only in the Windows Credential Manager (never in settings, logs, history or error messages).
- **Hosts backup** before every change, kept in `~\.deepfocus\hosts_backups` (newest 20). The hosts file is written atomically (temp file, then replace) with its original bytes preserved and CRLF line endings; a read-only hosts file is handled.
- **Protected list.** Hostnames that would break Windows or sign-in (localhost, microsoft.com, google.com, ...) are refused.
- **Locked settings.** Defaults that affect how a session can be ended, and saved presets, are read-only while a session is active.
- **Startup check.** Opening the app with an overdue session or an orphaned hosts block offers to clean up immediately.
- **End early** needs a cooling-off countdown (default 30 s). Strict mode also requires typing a phrase.
- **Atomic JSON writes**, log rotation at 1 MB, and a single-instance mutex so two windows cannot fight over the state.

What is not protected: anyone with administrator rights can still stop a session by deleting the tasks and editing
the hosts file within the same minute. This is a focus aid for you, not a parental control.

## Features

- **Block by name** with a 62-service catalog (domains, CDNs, extra hostnames, desktop process names) across Video, Social, Gaming, News, Shopping and Music, plus a generic expansion for unknown names and direct hostname entry. Every list is shown for review and editing before anything happens.
- **AI Block**: type the name of an app or service ("Discord", "Steam", "TikTok") and a language model proposes a complete block list, grouped by purpose: main site, API and content delivery, login, downloads and updates; the desktop app with its launcher, helper and updater executables; and shortcuts or installers to hide. Every entry carries a reason and a confidence. The review screen has checkboxes per entry, Edit, Flag, "add your own" fields and All/None per group. Protected names (Windows, sign-in, certificate and AI-provider hosts, system processes) are refused outright; shared infrastructure (cloudfront.net, googleapis.com, amazonaws.com, company-wide apex domains, browsers) is marked "may affect other sites" and starts unchecked; low-confidence guesses start unchecked; files are accepted only under {Desktop}, {PublicDesktop}, {Downloads}, {Documents} or as absolute paths, re-checked by the same path guard as the rest of the app, and only existing files are preselected. Saved lists ("Discord (AI)") appear in the Block dropdown and the builder and work with timers, long-term blocks and weekly schedules exactly like built-in services. Only the typed name is sent to the model; the API key lives in the Windows Credential Manager (Settings > AI Block, Claude API by default or any OpenAI-compatible endpoint); the call runs off the UI thread with a timeout, retries and plain-language errors; without a key or network the built-in catalog is used instead.
- **Presets**: built-in groups (Social media, Video, Gaming, News, Shopping, Deep work) and your own saved presets with services, files, options and length.
- **Focus Session page**: a goal box, the length shown in a big ring on its own dark disc, one Start button, and two rows of chips. Row one holds dropdowns for Pomodoro (off, 25/5, 50/10, 90/15), Block Notifications and Focus Sounds plus Customize (the full builder); row two the block preset, the length, Weekly schedule, Long-term and AI Block. Chip rows wrap on narrow windows, the hero grows to fit, and the timer font is the largest that fits inside the ring. Below it, this week's focus bars with the focus rate and weekly hours.
- **Type scale**: 9 pt captions (the minimum anywhere), 10 pt body, 11 / 12 / 14 / 18 / 20 / 24 pt for headings and display text, scaled by the Windows display scaling. `DEEPFOCUS_DPI=96|120|144` emulates other scalings for test captures.
- **Block Windows notifications** for the session: the helper turns the Windows notifications switch off (current user's registry, no admin needed), records the old value and restores it exactly; the enforcement task re-applies it if it is turned back on by hand.
- **Focus sounds**: rain, brown noise, white noise or a deep hum, synthesised once into the state folder and looped while a session runs and the window is open. Switchable from the session screen.
- **Assistant**: a chat page (Ctrl+7) that talks to the AI you configured under Settings > AI Block (Claude API or any OpenAI-compatible endpoint). Ask it to plan the afternoon, split a task into Pomodoro chunks, talk through distractions or explain a feature. Replies stream in, Stop cancels, the conversation is kept in `~\.deepfocus\chat.json` (last 60 messages, New chat clears it) and the last 24 messages go to the model. By default the system prompt includes a short summary of the current session (goal, what is blocked, time left, schedule phase), today's and total focus time, the streak and the names of saved lists, never file paths or hostnames; a Settings toggle turns that off. Quick prompts appear on an empty chat.
- **Hide files** individually, by folder, or "everything on the Desktop" in one click; optional strict hide.
- **Three kinds of session length.** *Timer*: quick picks (+25 min ... +8 h, until 12:00, until 17:00, tomorrow 08:00) or an exact date and time. *Long-term*: 2 days to 3 months, or any date within a year, with a warning that ending early always needs the cooling-off delay. *Weekly schedule*: blocked all week and lifted only in free windows you pick per weekday (blocked, free from a given hour, or free all day), repeating for 1 to 52 weeks. The default is free from Friday 15:00 plus all of Saturday and Sunday.
- **Automatic switching.** During a weekly schedule the enforcement task removes the hosts block, unhides and unlocks the files and restores the DoH policies within a minute of a free window starting, and puts them all back when it ends. The app never has to be open. Every switch is recorded in the session, and the session screen shows the time to the next switch with a green ring during free windows.
- **Session screen**: progress ring, countdown, goal, counts of blocked hostnames / hidden files / closed apps / tamper attempts undone, and a scratchpad saved with the session.
- **Pomodoro reminders** (work / break lengths configurable) via Windows notifications. Blocks stay on during breaks.
- **Close browsers at start** so open tabs cannot keep a connection alive and the DoH policy applies at once.
- **Analytics**: total focus time, today, completed vs early, day streak, 14-day bar chart, per-session table.
- **Help page** with how it works, the safety design, shortcuts, and buttons for Emergency restore and the log folder.
- **Navigation**: Focus Session, Assistant, Analytics, Settings and Help in the sidebar; the session builder and Presets open from the Focus Session page and have a back link. Ctrl+1 to Ctrl+7 jump to every page (2 = builder, 3 = presets, 7 = Assistant).
- **Themes**: dark (default) or light, optional Windows accent colour, Fluent icons, no emoji anywhere.
- **Status check and emergency restore** in Settings.

## Known limits

- The hosts file blocks exact hostnames; it cannot wildcard. Sites and apps stop loading and signing in, but a service that streams media from random subdomains might still deliver an already-open stream.
- Browsers read the DoH policy when they start, so a browser that was already open keeps its old setting until restarted (or use "Close all browsers when the session starts").
- Desktop-app closing only runs while the window is open.
- Weekly free windows run from a chosen hour to midnight, or cover a whole day. A switch happens on the enforcement task's next minute, so it can be up to a minute late.
- Strict hide uses a permission entry; OneDrive may report sync errors for locked files until restore.
- AI Block suggestions are only as good as the model's knowledge: obscure or very new services, regional CDNs, and
  per-user install paths may be missing or wrong, and the model cannot see this PC, so file suggestions are
  templates that are checked for existence locally. Review every list; the hosts file still blocks exact
  hostnames only.
- Scrolling, page slides and window resizes move hundreds of child windows. Tk draws them lazily, and Windows
  copies not-yet-drawn pixels along with a moved window, which leaves duplicated or torn text behind. Animated
  moves therefore use `ui.animate_synced` (the next frame waits until the previous one is painted), invalidate
  the band that just moved in (`ui.invalidate` with a rect) and repaint the whole view once scrolling settles.
  A full repaint of a busy page costs about 60 ms, so never repaint everything on every frame.

## Layout

```
deepfocus/
  __main__.py   entry: GUI, or `helper ...` for the elevated worker
  app.py        tkinter application (pages, dialogs)
  ui.py         theme tokens and the widget kit (hero night-sky canvas, gradient buttons and rings, cards, text
                boxes, toggle switches, selector bars, number boxes, smooth scroll, info bars)
  catalog.py    services, presets, hostname expansion; saved AI lists exposed as "ai:<slug>" services
  aiblock.py    AI Block: schema, prompt, validation, normalisation, protected and broad lists, offline fallback
  llm.py        provider-agnostic JSON completion and streaming chat over urllib (Claude Messages API,
                OpenAI-compatible), with user-facing error kinds
  credstore.py  API keys in the Windows Credential Manager (CredWrite / CredRead / CredDelete)
  schedule.py   one-shot, long-term and weekly schedules: free windows, next/previous switch, descriptions
  hostsfile.py  build / strip / verify the marked hosts block
  filehide.py   attribute-based hide / exact restore, optional ACL lock
  guard.py      refuses paths that must never be hidden
  doh.py        browser DNS-over-HTTPS policies on/off with exact restore
  notif.py      Windows notifications switch off/on with exact restore
  sounds.py     generated ambient focus sounds (winsound loop)
  state.py      session, history, settings, presets (atomic JSON), log rotation, backup pruning
  helper.py     elevated worker: apply / restore / enforce / status, Task Scheduler XML
  elevate.py    UAC launch of the helper and result hand-off
  procwatch.py  background process closer, browser list
  notify.py     Windows toast notifications
build/
  DeepFocus.spec, version.txt   PyInstaller build
  make_icon.py                  generates assets/deepfocus.ico without third-party libraries
  build.ps1                     rebuild exe + Start Menu / Desktop shortcuts
tests/
  test_core.py            hosts, catalog, hide/lock, guard, DoH policies (HKCU sandbox), state, schedules
  test_aiblock.py         AI Block schema validation, domain/app/file normalisation, protected and broad lists,
                          offline fallback, the provider client against a local mock server (both wire formats,
                          401/429/5xx/timeouts/retries, streaming chat with refusal and cancel), the credential
                          store, saved-list and chat-history round trips
  test_helper_sandbox.py  apply / tamper / enforce / restore against a temporary hosts file, the weekly on/off cycle,
                          plus real Task Scheduler runs
  smoke_gui.py            opens every page and exercises the builder
  capture.py              renders a page to PNG (works even when a fullscreen game covers the desktop)
```
