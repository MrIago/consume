# /consume 🍿

**Give Claude eyes and ears for online content — on demand.**

Claude can read a webpage and run a script, but it can't *watch a video* or
*scroll a feed*. `/consume` gives it that, across **YouTube, Instagram,
Twitter/X, Reddit, and LinkedIn** — and does it lazily: it pulls the
transcript/caption/text first, and only fetches the specific frames or images it
actually needs to answer you.

```
/consume https://youtu.be/… summarize the 3 main arguments
/consume https://www.instagram.com/ocopyqvende/ what makes their hooks work?
/consume <tweet-url> and turn this thread into a carousel
```

---

## The idea: fetch on demand

Most "watch a video" tools are eager — they download everything, sample dozens
of frames, transcribe the whole thing up front, and burn time and tokens on
material you may never use.

`/consume` is the opposite. There's an **orchestrator** that reads your intent
and climbs only as far as the task needs:

1. **Transcript / caption / text** first — cheap, often instant, usually enough.
2. **Sharper local transcription** — only for the stretch a question actually
   hinges on (not the whole video).
3. **Frames or images** — only the specific timestamps/slides you must *see*.

The trigger to fetch anything is always a real need — your task or your
question — never speculation. Depth is proportional to the goal: never more,
never less.

## Platforms

| Platform | What it consumes |
|----------|------------------|
| **YouTube** | videos (captions → local transcription → frames), channel metadata & ranking by views, thumbnails |
| **Instagram** | reels, single images, carousels (incl. mixed), profiles; captions + likes |
| **Twitter/X** | tweets (text + full metrics + images), threads/replies/quotes, tweet videos |
| **Reddit** | post + the comment tree, images/galleries, video posts |
| **LinkedIn** | video posts (text + transcription + frames), image/text posts |

## Requirements

`/consume` runs on your machine (Claude Code) and uses your browser session for
private content — so it does **not** run on claude.ai (the web sandbox has no
binaries or your cookies). Works on **Linux, macOS, and Windows** (on Windows use
`python` instead of `python3`).

> **Easiest setup: just ask.** After installing the plugin, say *"set up
> consume"* (or just use it). Claude inspects your system — OS, package manager,
> Python, GPU — and installs **only what's missing**, with the right commands for
> your machine. No manual checklist needed. The list below is just the reference.

**Tools** (run `python3 scripts/check.py` to verify):
- `yt-dlp` + `ffmpeg` — required (download, frames, audio)
- `gallery-dl` — for Instagram / Reddit
- `curl_cffi` — for Twitter/X (impersonation)
- `faster-whisper` — only for **local** transcription (see below)
- `secretstorage` — **Linux only**, to read Chrome cookies (macOS/Windows don't need it)

```bash
pipx install yt-dlp
pip install gallery-dl curl_cffi
pip install faster-whisper            # only if you want local transcription
pip install secretstorage            # Linux only
# ffmpeg: sudo apt install ffmpeg   (or: brew install ffmpeg)
```

### Transcription — pick what fits your machine

Transcription auto-selects a backend; **all keep per-segment timestamps** (so
frame-alignment works either way):

| Your setup | What happens | Setup |
|---|---|---|
| **No GPU** (most people) | Cloud transcription via **Groq** (free tier, fast) | set `GROQ_API_KEY` |
| Have an NVIDIA GPU | Free **local** transcription (faster-whisper) | nothing — it's the default |
| Prefer OpenAI | Cloud via `whisper-1` | set `OPENAI_API_KEY` |
| No GPU **and** no key | Local on CPU — works but **slow** | set a key to speed up |

Long audio is auto-chunked (overlapping + deduped) for the cloud backends. Force
a choice with `WATCH_TRANSCRIBE=auto|groq|openai|local`.

### Configuring keys & login (persists across sessions)

Settings live in env vars **or** `~/.config/consume/.env`. Easiest: just tell
Claude your key in chat and it saves it — or run:

```bash
python3 scripts/lib/config.py GROQ_API_KEY=...                    # transcription (no GPU needed)
python3 scripts/lib/config.py WATCH_COOKIES_FROM_BROWSER=chrome   # default profile; or "chrome:Profile 1", "firefox", "edge"
python3 scripts/lib/config.py WATCH_YOUTUBE_API_KEY=...           # optional: rich YouTube channel ranking
python3 scripts/lib/config.py                                     # show current (masked)
```

**Login (cookies).** Instagram, Reddit, and LinkedIn image posts need a
logged-in browser session — set `WATCH_COOKIES_FROM_BROWSER` to a browser/profile
where you're logged in (default `chrome`).

## Install

**As a plugin (recommended):**

```
/plugin marketplace add MrIago/consume
/plugin install consume@consume
```

**Manual / developer:**

```bash
git clone https://github.com/MrIago/consume.git ~/.claude/skills/consume
```

Then just paste a link and ask. Claude loads the skill automatically, or you can
invoke it with `/consume <url> [what you want]`.

## How it works

The skill (`SKILL.md`) is the orchestrator; the real work is in small,
self-contained per-platform scripts under `scripts/platforms/<platform>/`. Each
platform is independent on purpose — its own quirks, its own scripts — so they're
easy to tweak without touching the others.

```
scripts/
├── check.py                 # dependency preflight
├── lib/                     # shared core
│   ├── transcribe.py        # Groq / OpenAI / local, timestamps, auto-chunking
│   └── config.py            # keys & settings (env var or ~/.config/consume/.env)
└── platforms/
    ├── youtube/    captions · transcribe · frames · meta
    ├── instagram/  profile · post · reel
    ├── twitter/    tweet · video
    ├── reddit/     post · video
    └── linkedin/   post · video
```

Transcription runs via Groq, OpenAI, or local faster-whisper — whichever fits
your machine — and **all keep per-segment timestamps**, so frames line up with
the words. Local has no upload limit (a 2.5h video works); the cloud backends
auto-chunk long audio (overlapping + deduped) and run the chunks in parallel.
Frames are pulled by seeking directly to the timestamps you need (no full-video
decode), ~167× faster than scanning the whole file.

## License

MIT © [Iago Lima Toledo](https://github.com/MrIago)
