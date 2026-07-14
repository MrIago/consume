# consume

A Claude Code skill that gives Claude eyes and ears for online video and social content, fetching the minimum the question needs.

## What it does

Paste a link and ask a question. The skill pulls the transcript, caption, or text, and when the answer depends on the screen, the exact frames at the timestamps that matter. Claude then answers as if it had watched the content itself.

Seven sources:

| Platform | What it consumes |
|----------|------------------|
| **YouTube** | videos (captions, audio transcription, frames), channel listings ranked by views, thumbnails and metadata |
| **Instagram** | reels, single images, carousels (including mixed), profile indexes; captions + likes |
| **TikTok** | videos (caption/stats, transcription, frames), photo slideshows (slide images + sound) |
| **Twitter/X** | tweets (text + full metrics + images), threads/replies/quotes, tweet videos |
| **Reddit** | post + the comment tree, images/galleries, video posts |
| **LinkedIn** | video posts (text + transcription + frames), image/text posts |
| **Course platforms** | lessons behind a Panda Video / converteai player (cademi, members areas), via a hybrid browser + CDN flow |

Real workload: the pipeline transcribed 11h23m of course video in about one hour on a consumer NVIDIA GPU, at zero API cost, using the batch mode of the course tool.

```
/consume https://youtu.be/… summarize the 3 main arguments
/consume https://www.instagram.com/<user>/ what makes their hooks work?
/consume <tweet-url> turn this thread into a carousel
```

## How it works

- **An orchestrator plus small per-platform scripts.** `SKILL.md` reads the intent and picks tools; each platform is a self-contained script under `scripts/platforms/<platform>/`, so one platform's quirks never leak into another. The shared core (`scripts/lib/`) holds transcription and config.

- **Fetch on demand.** The trigger to fetch anything is a concrete need, never speculation. Transcript first (YouTube captions arrive in ~3s with no download). Audio transcription covers the whole video only when no captions exist; when captions are wrong in one stretch, `--start/--end` transcribes that slice alone, and `--segments 3:30-5:00,12:00-13:00` batches several slices under one model load. Frames come last, seeked straight from the stream at chosen timestamps (~1s per frame, no video download).

- **A transcription backend cascade.** Groq `whisper-large-v3-turbo` when `GROQ_API_KEY` is set, OpenAI `whisper-1` when `OPENAI_API_KEY` is set, local faster-whisper on the GPU otherwise (CPU fallback). All three return the same shape, timestamped segments in absolute seconds, so frame alignment works with any backend and platforms stay backend-agnostic.

- **Long audio survives the 25 MB API limit.** The cloud backends split audio into 20-minute chunks that overlap by 10 seconds, so a sentence straddling a boundary lands whole in at least one chunk; the merge step drops the duplicated segments by absolute timestamp. Chunks upload in parallel. The local backend has no size limit and skips chunking.

- **The course flow is hybrid because the auth model splits in two.** The lesson page session is bound to the logged-in device, so headless browsers with exported cookies bounce to the login screen. The video stream sits on a public CDN (`cdn.converteai.net/.../main.m3u8`) that checks one `Referer` header and nothing else. So Claude reads the player iframe `src` from the user's real logged-in Chrome tab (one line of JS via the browser extension), and the script does the rest server-side with no cookies: embed URL → videoId → CDN m3u8 → audio → timestamped transcript. Batch mode takes one iframe src per lesson and transcribes a whole module in one call.

- **Login rides the user's own browser.** Platforms that need auth (Instagram, Reddit, X threads, LinkedIn image posts) read cookies from a browser profile the user is already logged into (`WATCH_COOKIES_FROM_BROWSER`). The skill stores no passwords and performs no logins.

## Usage

Runs in Claude Code on your machine. It does not run on claude.ai (the web sandbox has no binaries and no access to your browser cookies). Works on Linux, macOS, and Windows (on Windows use `python` instead of `python3`).

### Install

As a plugin (recommended):

```
/plugin marketplace add MrIago/consume
/plugin install consume@consume
```

Manual / developer:

```bash
git clone https://github.com/MrIago/consume.git ~/.claude/skills/consume
```

Then paste a link and ask. Claude loads the skill on its own, or invoke it with `/consume <url> [what you want]`.

### Setup

Easiest path: after installing, say "set up consume". Claude inspects your system (OS, package manager, Python, GPU) and installs what's missing, with your confirmation, building the right commands for your machine. The list below is the reference.

Tools (run `python3 scripts/check.py` to verify):

- `yt-dlp` + `ffmpeg`: required (download, frames, audio)
- `gallery-dl`: Instagram / Reddit
- `curl_cffi`: Twitter/X (impersonation)
- `faster-whisper`: local transcription only
- `secretstorage`: Linux only, to read Chrome cookies

```bash
pipx install yt-dlp
pip install gallery-dl curl_cffi
pip install faster-whisper            # only if you want local transcription
pip install secretstorage             # Linux only
# ffmpeg: sudo apt install ffmpeg   (or: brew install ffmpeg)
```

### Transcription: pick what fits your machine

The backend auto-selects; all keep per-segment timestamps, so frame alignment works either way:

| Your setup | What happens | Setup |
|---|---|---|
| No GPU (most people) | Cloud transcription via Groq (free tier, fast) | set `GROQ_API_KEY` |
| NVIDIA GPU | Free local transcription (faster-whisper) | nothing, it is the default |
| Prefer OpenAI | Cloud via `whisper-1` | set `OPENAI_API_KEY` |
| No GPU and no key | Local on CPU, works but slow | set a key to speed up |

Force a choice with `WATCH_TRANSCRIBE=auto|groq|openai|local`.

### Keys and login (persist across sessions)

Settings live in env vars or `~/.config/consume/.env`. Tell Claude your key in chat and it saves it, or run:

```bash
python3 scripts/lib/config.py GROQ_API_KEY=...                    # transcription without a GPU
python3 scripts/lib/config.py WATCH_COOKIES_FROM_BROWSER=chrome   # default profile; or "chrome:Profile 1", "firefox", "edge"
python3 scripts/lib/config.py WATCH_YOUTUBE_API_KEY=...           # optional: rich YouTube channel ranking
python3 scripts/lib/config.py                                     # show current (masked)
```

### Layout

```
scripts/
├── check.py                 # dependency preflight
├── lib/                     # shared core
│   ├── transcribe.py        # Groq / OpenAI / local, timestamps, chunking
│   └── config.py            # keys & settings (env var or ~/.config/consume/.env)
└── platforms/
    ├── youtube/    captions · transcribe · frames · meta
    ├── instagram/  profile · post · reel
    ├── tiktok/     video · post (slideshow)
    ├── twitter/    tweet · video
    ├── reddit/     post · video
    ├── linkedin/   post · video
    └── course/     lesson (Panda Video / converteai)
```

## Scope and honest limits

- Instagram, Reddit, and LinkedIn image posts need a logged-in browser session on your machine. Instagram exposes likes and captions but no view counts.
- Twitter/X needs `curl_cffi`; threads also need cookies. Many X videos are silent UI demos: transcription returns nothing and frames are the right tool.
- LinkedIn image-post text reconstruction is best-effort against LinkedIn's internal format; the images come through in full resolution regardless.
- Course support covers the Panda Video / converteai player. Other course players are out of scope. The iframe-src step needs the Claude-in-Chrome extension and your logged-in browser; downloading a whole course is heavy and the skill asks before doing it.
- These are scrapers, and platforms change. When a script fails it prints what broke and a hint (login, rate limit, missing dependency) instead of a bare stack trace, but breakage on platform updates is a fact of this category.
- CPU-only local transcription works and is slow. Set a Groq key (free tier) if you have no GPU.

## License

MIT © [Iago Lima Toledo](https://github.com/MrIago)
