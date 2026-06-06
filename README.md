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

`/consume` is **local-first by design** — it runs on your machine, uses your GPU
for free transcription, and your browser session for private content. That makes
it great in Claude Code, but it does **not** run on claude.ai (the web sandbox
has no GPU, binaries, or your cookies).

**Tools** (run `python3 scripts/check.py` to verify):
- `yt-dlp` + `ffmpeg` — required (download, frames, audio)
- `faster-whisper` — for local GPU transcription (free, keeps timestamps)
- `gallery-dl` — for Instagram / Reddit
- `curl_cffi` — for Twitter/X (impersonation)
- `secretstorage` — on Linux, to read Chrome cookies

```bash
pipx install yt-dlp
pip install faster-whisper gallery-dl curl_cffi secretstorage
# ffmpeg: sudo apt install ffmpeg   (or: brew install ffmpeg)
```

**Login (cookies).** Instagram, Reddit, and LinkedIn image posts need a
logged-in browser session. Set the browser/profile to read cookies from:

```bash
export WATCH_COOKIES_FROM_BROWSER="chrome:Profile 1"   # default
```

**Optional.** `export WATCH_YOUTUBE_API_KEY=…` for fast, rich YouTube channel
ranking via the official Data API.

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
└── platforms/
    ├── youtube/    captions · transcribe · frames · meta
    ├── instagram/  profile · post · reel
    ├── twitter/    tweet · video
    ├── reddit/     post · video
    └── linkedin/   post · video
```

Transcription is local (faster-whisper, e.g. `large-v3-turbo`), so it's free,
private, keeps per-segment timestamps, and has no upload size limit — a 2.5h
video works. Frames are pulled by seeking directly to the timestamps you need
(no full-video decode), which is ~167× faster than scanning the whole file.

## License

MIT © [Iago Lima Toledo](https://github.com/MrIago)
