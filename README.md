# consume

A Claude Code skill that gives Claude eyes and ears for video, social content, and media files on your disk, fetching the minimum the question needs.

## What it does

Paste a link or a file path and ask a question. The skill pulls the transcript, caption, or text, and when the answer depends on the screen, the exact frames at the timestamps that matter. Claude then answers as if it had watched the content itself.

Eight sources:

| Platform | What it consumes |
|----------|------------------|
| **YouTube** | videos (captions, audio transcription, frames), channel listings ranked by views, thumbnails and metadata |
| **Instagram** | reels, single images, carousels (including mixed), profile indexes; captions + likes |
| **TikTok** | videos (caption/stats, transcription, frames), photo slideshows (slide images + sound) |
| **Twitter/X** | tweets (text + full metrics + images), threads/replies/quotes, tweet videos |
| **Reddit** | post + the comment tree, images/galleries, video posts |
| **LinkedIn** | video posts (text + transcription + frames), image/text posts |
| **Course platforms** | lessons behind a Panda Video / converteai player (cademi, members areas), via a hybrid browser + CDN flow |
| **Local files** | any media path on disk: meeting recordings, voice notes, podcasts, screen captures (`.mp4 .mkv .webm .mp3 .wav .m4a .ogg .opus`…) |

Real workload: the pipeline transcribed 11h23m of course video in about one hour on a consumer NVIDIA GPU, at zero API cost, using the batch mode of the course tool.

```
/consume https://youtu.be/… summarize the 3 main arguments
/consume https://www.instagram.com/<user>/ what makes their hooks work?
/consume <tweet-url> turn this thread into a carousel
/consume ~/Downloads/standup.mp4 what did we decide and who owns what?
```

## How it works

- **An orchestrator plus small per-platform scripts.** `SKILL.md` reads the intent and picks tools; each platform is a self-contained script under `scripts/platforms/<platform>/`, so one platform's quirks never leak into another. The shared core (`scripts/lib/`) holds transcription and config.

- **Fetch on demand.** The trigger to fetch anything is a concrete need, never speculation. Transcript first (YouTube captions arrive in ~3s with no download). Audio transcription covers the whole video only when no captions exist; when captions are wrong in one stretch, `--start/--end` transcribes that slice alone, and `--segments 3:30-5:00,12:00-13:00` batches several slices under one model load. Frames come last, seeked straight from the stream at chosen timestamps (~1s per frame, no video download).

- **A transcription backend cascade.** Groq `whisper-large-v3-turbo` when `GROQ_API_KEY` is set, OpenAI `whisper-1` when `OPENAI_API_KEY` is set, an audio-capable chat model on OpenRouter when `OPENROUTER_API_KEY` is set, local faster-whisper on the GPU otherwise (CPU fallback). Every backend returns the same shape, timestamped segments in absolute seconds, so frame alignment works with any of them and platforms stay backend-agnostic.

- **Long audio survives the 25 MB API limit.** The cloud backends split audio into 20-minute chunks that overlap by 10 seconds, so a sentence straddling a boundary lands whole in at least one chunk; the merge step drops the duplicated segments by absolute timestamp. Chunks upload in parallel, and 429/5xx responses retry with attempt-scaled backoff — Groq's `retry-after` understates the wait once the hourly audio budget is gone, and without the retry that turned into silent holes in a long transcript rather than an error.

- **The course flow is hybrid because the auth model splits in two.** The lesson page session is bound to the logged-in device, so headless browsers with exported cookies bounce to the login screen. The video stream sits on a public CDN (`cdn.converteai.net/.../main.m3u8`) that checks one `Referer` header and nothing else. So Claude reads the player iframe `src` from the user's real logged-in Chrome tab (one line of JS via the browser extension), and the script does the rest server-side with no cookies: embed URL → videoId → CDN m3u8 → audio → timestamped transcript. Batch mode takes one iframe src per lesson and transcribes a whole module in one call.

- **Local files skip the network entirely.** A path on disk goes straight to ffmpeg: audio extracted as mono 16k mp3 so it fits the API limits, frames pulled instantly, timestamps printed as `[HH:MM:SS]` because meeting recordings run for hours. Several files transcribe in parallel, `--start/--end` and `--segments` slice the same way as the online tools, and `--out <dir>` writes one markdown file per input.

- **Login rides the user's own browser.** Platforms that need auth (Instagram, Reddit, X threads, LinkedIn image posts) read cookies from a browser profile the user is already logged into (`WATCH_COOKIES_FROM_BROWSER`). The skill stores no passwords and performs no logins.

## Install

Runs in Claude Code on your machine, which includes the **Code tab of the Claude Desktop app**, the `claude` CLI, and the IDE extension. It does not run on claude.ai, and Cowork and cloud sessions don't read `~/.claude/skills/` from your machine. Works on Linux, macOS, and Windows (on Windows use `python` instead of `python3`).

### Let Claude install it

Open Claude Code and paste this. It clones the skill, inspects your machine, and installs only what's missing:

```
Install the `consume` skill from https://github.com/MrIago/consume on this machine:

1. Clone it: `git clone https://github.com/MrIago/consume.git ~/.claude/skills/consume`
   (if that directory already exists, `git pull` in it instead).
2. Run the preflight: `python3 ~/.claude/skills/consume/scripts/check.py`, and read
   `~/.claude/skills/consume/references/install.md`.
3. Follow that guide: probe my actual OS, package manager, Python and GPU, then install
   ONLY the missing pieces, using the package manager this machine really has. Show me
   any sudo command before running it.
4. Transcription backend: if I have no NVIDIA GPU, don't install faster-whisper (it pulls
   ~2 GB of PyTorch and runs slowly on CPU) — tell me to get a free key at
   https://console.groq.com/keys instead, and save it with
   `python3 ~/.claude/skills/consume/scripts/lib/config.py GROQ_API_KEY=...` (never print
   the key back).
5. Verify: run `check.py` again (optional pieces I opted out of may still show as
   missing, that's fine), then ask me for a YouTube link and transcribe it with
   `python3 ~/.claude/skills/consume/scripts/platforms/youtube/captions.py "<url>"`.
6. Tell me to restart Claude Code so the new skill directory is picked up, and show me
   two or three example prompts.

If a step fails, tell me exactly what is still missing instead of saying it's ready.
```

### Install it yourself

```bash
git clone https://github.com/MrIago/consume.git ~/.claude/skills/consume
python3 ~/.claude/skills/consume/scripts/check.py     # says what's missing
```

Restart Claude Code afterwards: it detects new skills live, but a `~/.claude/skills/` directory that didn't exist when the session started is only watched after a restart. Then paste a link and ask — Claude loads the skill on its own, or invoke it with `/consume <url> [what you want]`. Update later with `cd ~/.claude/skills/consume && git pull`.

### Dependencies

`check.py` reports what's missing and what each piece is for. Install only those:

| Dependency | Needed for | Linux | macOS | Windows |
|---|---|---|---|---|
| `ffmpeg` | audio + frames — **required** | `sudo apt install ffmpeg` | `brew install ffmpeg` | `winget install Gyan.FFmpeg` |
| `yt-dlp` | downloads — **required** | `pipx install yt-dlp` | `brew install yt-dlp` | `winget install yt-dlp.yt-dlp` |
| `gallery-dl` | Instagram, Reddit | `pipx install gallery-dl` | `brew install gallery-dl` | `pip install gallery-dl` |
| `curl_cffi` | Twitter/X (impersonation) | `pip install curl_cffi` | `pip install curl_cffi` | `pip install curl_cffi` |
| `faster-whisper` | local transcription only | `pip install faster-whisper` | skip — no NVIDIA GPU on a Mac | `pip install faster-whisper` |
| `secretstorage` | reading Chrome cookies | `pip install secretstorage` | not needed | not needed |

If `pip` refuses with *externally-managed-environment* (PEP 668, common on Debian/Ubuntu and Homebrew Python), add `--user --break-system-packages`, or use `pipx` for the CLI tools.

### Transcription: pick what fits your machine

The backend auto-selects, in this order: Groq → OpenAI → OpenRouter → local faster-whisper.

| Your setup | What happens | Setup |
|---|---|---|
| No GPU (most people, every Mac) | Cloud via Groq `whisper-large-v3-turbo` (free tier, fast) | set `GROQ_API_KEY` |
| NVIDIA GPU | Free local transcription (faster-whisper), no key needed | nothing, it is the default |
| Prefer OpenAI | Cloud via `whisper-1` | set `OPENAI_API_KEY` |
| Only an OpenRouter key | Audio-capable chat model — fallback, **no per-segment timestamps** | set `OPENROUTER_API_KEY` |
| No GPU and no key | Local on CPU, works but slow | set a key to speed up |

Groq, OpenAI and local keep per-segment timestamps, so frame alignment works with any of them; OpenRouter returns one block per chunk stamped with the chunk's start, which is why it sits last.

Force a choice with `WATCH_TRANSCRIBE=auto|groq|openai|openrouter|local|local-fast`. The `local-fast` mode swaps `large-v3-turbo` for `whisper-small` when speed beats accuracy.

### Keys and login (persist across sessions)

Settings live in env vars or `~/.config/consume/.env`. Tell Claude your key in chat and it saves it, or run:

```bash
cd ~/.claude/skills/consume
python3 scripts/lib/config.py GROQ_API_KEY=...                    # transcription without a GPU
python3 scripts/lib/config.py OPENROUTER_API_KEY=...              # fallback backend
python3 scripts/lib/config.py WATCH_COOKIES_FROM_BROWSER=chrome   # default profile; or "chrome:Profile 1", "firefox", "edge"
python3 scripts/lib/config.py WATCH_YOUTUBE_API_KEY=...           # optional: rich YouTube channel ranking
python3 scripts/lib/config.py                                     # show current (masked)
```

### Layout

```
scripts/
├── check.py                 # dependency preflight
├── lib/                     # shared core
│   ├── transcribe.py        # Groq / OpenAI / OpenRouter / local, timestamps, chunking, retry
│   └── config.py            # keys & settings (env var or ~/.config/consume/.env)
└── platforms/
    ├── youtube/    captions · transcribe · frames · meta
    ├── instagram/  profile · post · reel
    ├── tiktok/     video · post (slideshow)
    ├── twitter/    tweet · video
    ├── reddit/     post · video
    ├── linkedin/   post · video
    ├── course/     lesson (Panda Video / converteai)
    └── local/      file (any media path on disk)
```

## Scope and honest limits

- Instagram, Reddit, and LinkedIn image posts need a logged-in browser session on your machine. Instagram exposes likes and captions but no view counts.
- Twitter/X needs `curl_cffi`; threads also need cookies. Many X videos are silent UI demos: transcription returns nothing and frames are the right tool.
- LinkedIn image-post text reconstruction is best-effort against LinkedIn's internal format; the images come through in full resolution regardless.
- Course support covers the Panda Video / converteai player. Other course players are out of scope. The iframe-src step needs the Claude-in-Chrome extension and your logged-in browser; downloading a whole course is heavy and the skill asks before doing it.
- These are scrapers, and platforms change. When a script fails it prints what broke and a hint (login, rate limit, missing dependency) instead of a bare stack trace, but breakage on platform updates is a fact of this category.
- CPU-only local transcription works and is slow. Set a Groq key (free tier) if you have no GPU.
- The OpenRouter backend is a fallback: it returns one block per chunk instead of per-segment timestamps, so frame alignment against a transcript is coarse. Prefer Groq, OpenAI, or local when timing matters.
- Local files are read straight from disk with ffmpeg, so whatever ffmpeg can decode works. A file it can't decode fails at that step, not inside the skill.

## License

MIT © [Iago Lima Toledo](https://github.com/MrIago)
