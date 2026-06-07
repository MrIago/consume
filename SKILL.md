---
name: consume
description: Consume and study any content the user links — YouTube videos/channels, Instagram reels/posts/carousels/profiles, Twitter/X tweets and threads, Reddit posts with comments, and LinkedIn posts. Pulls the transcript/caption/text and, only when needed, the specific frames or images you must see, so you can answer questions, summarize, or analyze it as if you had watched/read it yourself. Use this whenever the user pastes a YouTube, Instagram, Twitter/X, Reddit, or LinkedIn URL, or asks you to watch, study, summarize, analyze, or pull quotes/moments from a video, reel, post, carousel, tweet, thread, or profile — even if they don't say "consume" or "watch".
allowed-tools: Bash, Read
---

# /consume — study content on demand

You can't watch video directly. This skill gives you eyes and ears, but the
guiding principle is **fetch on demand: the trigger to fetch anything is always
a real need — the user's task or question — never speculative pre-fetching.**

You start by getting the transcript and nothing else. Everything heavier (a
sharper local transcription, frames of the screen) is pulled **only when, and
only as much as, a concrete need requires it.** Most requests never go past the
transcript. Depth is proportional to the goal — never more, never less.

## The orchestrator — read the intent first

Before running anything, decide which situation you're in:

**A. Pure consumption** — the user just said "consume / watch / study this
<url>" with no further task.
→ Get the transcript (below), then **stop and wait** for the next step. Do not
go hunting for gaps. When the user then asks something, re-evaluate: does what
you already have answer it? If yes, answer. If no, fetch *only* the specific
thing that question needs.

**B. There's a task** — e.g. "study this video and make a carousel teaching the
same thing", "summarize the 3 main arguments", "what tools does she recommend?".
→ Work autonomously. Derive from *the task* what you need to know, get the
transcript, and then fetch — pointwise — only the gaps **that this task
requires**. Different tasks need different gaps: a step-by-step teaching carousel
needs the exact on-screen config (frames) and precise wording (sharper local
transcription); a funny tweet about the video needs neither. Stop as soon as you
have enough to deliver the task well.

In both cases: never transcribe the whole video "just in case", never grab a
broad scan of frames. Fetch the slice/frame the need points at, nothing more.

## Platform

Detect the platform from the URL and use the matching scripts in
`${CLAUDE_SKILL_DIR}/scripts/platforms/<platform>/`:

- **YouTube** (`youtube.com`, `youtu.be`) — `youtube/`. No login needed.
- **Instagram** (`instagram.com`) — `instagram/`. Needs login cookies (see below).
- **Twitter/X** (`x.com`, `twitter.com`) — `twitter/`. Needs `curl_cffi`; threads
  also need cookies.
- **Reddit** (`reddit.com`) — `reddit/`. Needs login cookies.
- **LinkedIn** (`linkedin.com`) — `linkedin/`. Image/text posts need login cookies;
  video posts work without.

For other platforms, tell the user it's not supported yet.

Several platforms read login cookies from a logged-in browser via
`WATCH_COOKIES_FROM_BROWSER` (default `chrome:Profile 1`). On Linux, reading
Chrome cookies needs `secretstorage`.

## The YouTube tools — fetch only what a need points at

### 1. Transcript from captions — your starting point, almost always

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/youtube/captions.py" "<url>"
```

Native captions in ~3s, no video download. Each line is `[MM:SS] text`. This is
enough for most consumption and most questions. If it prints `NO_CAPTIONS`, get
the transcript from audio instead (tool 2, whole video).

### 2. Sharper transcription from audio — local, and slice-able

```bash
# whole video (use when there are no captions at all):
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/youtube/transcribe.py" "<url>"

# just one stretch (use when captions are wrong/ambiguous in a specific part the
# task or question cares about — e.g. a misheard technical term, a spoken email):
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/youtube/transcribe.py" "<url>" --start 3:30 --end 5:00

# several stretches at once — the model loads ONCE and does them all:
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/youtube/transcribe.py" "<url>" --segments 3:30-5:00,12:00-13:00
```

Downloads only the audio (the whole track, or just the slices), transcribes
locally on the GPU with faster-whisper. Free, auto-detects language (pt/en),
keeps absolute `[MM:SS]` timestamps even for slices. The slice forms are the
on-demand path: transcribe just the parts a need points at, not the whole video.
When you need more than one stretch, pass them together with `--segments` — the
model is loaded once and the slices download in parallel, so it's much faster
than calling the script per stretch.

### 3. Frames at specific timestamps — when you must SEE the screen

Only when audio/transcript genuinely can't answer the need — on-screen code,
config values, UI, a thumbnail, a visual bug. **Pick the exact timestamps from
the transcript**, then:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/youtube/frames.py" "<url>" --at 250,610
```

`--at` takes comma-separated timestamps (`SS`, `MM:SS`, `HH:MM:SS`). Seeks each
frame directly from the stream (~1s/frame, no video download). Then **Read each
printed frame path** and align it to the transcript by its `t=MM:SS`. Request
only the few frames the need points at — never a broad scan. Add `--resolution
1024` if the user must read small on-screen text.

### 4. Marketing metadata — title, description, thumbnail, stats

For tasks about the *packaging* of a video rather than its content — why a
title or thumbnail works, a channel's strategy, ranking a channel by views.
None of this downloads the video.

```bash
# one video: title, description, stats, tags, + thumbnail saved to Read:
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/youtube/meta.py" "<url>"

# a channel: list videos (fast, titles only):
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/youtube/meta.py" "<channel-url-or-@handle>" --channel --limit 10

# paginate by video: --offset N skips the first N (e.g. videos 11-20):
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/youtube/meta.py" "<channel>" --channel --limit 10 --offset 10

# rank a channel by views (add --stats; ~1.2s/video, so keep --limit sane):
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/youtube/meta.py" "<channel>" --channel --limit 15 --stats --sort views
```

`--offset` paginates in both modes (Data API and the yt-dlp fallback).

The video mode prints a thumbnail path — **Read it** to see the cover the
channel used to sell the click; title and thumbnail often differ on purpose.

Metrics source: if `WATCH_YOUTUBE_API_KEY` is set, channel listing uses the
official YouTube Data API — fast and always with view/like counts, so ranking a
channel by views costs ~1 call per 50 videos. Without the key it falls back to
yt-dlp, where `--stats` is the slow path (~1.2s/video) and plain listing has no
counts. Either way, keep `--limit` to what the question needs — don't rank a
whole 500-video channel when "the last 15" answers it.

## The Instagram tools — fetch only what a need points at

Instagram requires login. The scripts read cookies from a logged-in browser via
`WATCH_COOKIES_FROM_BROWSER` (default `chrome:Profile 1`). On Linux, Chrome
cookies need `secretstorage` (`pip install secretstorage`). If a script reports
a login/rate-limit error, that's the cookies — relay the hint it printed. Note
Instagram exposes likes + caption + date, but **not** view counts.

A post is a reel (1 video), a single image, a carousel (N images), or a mixed
carousel (images + video). The caption (the copy) is always available and often
the most important part.

### Profile index — the starting point for "study this profile"

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/instagram/profile.py" "https://www.instagram.com/<user>/" --limit 12 [--offset N] [--sort likes|date]
```

Lists posts with type · likes · date · caption — without downloading media. This
is the index: from it, decide which posts the task actually needs, then consume
those. `--sort likes` ranks by most-liked (likes is the only engagement metric
Instagram gives). `--offset N` paginates by post — e.g. `--limit 9 --offset 9`
gives posts 10-18. Keep `--limit` to what the task needs (large limits fetch
many slides and can hit Instagram's rate-limit).

### A post — caption + media (image / carousel / mixed)

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/instagram/post.py" "https://www.instagram.com/p/<shortcode>/"
```

Prints the caption and downloads the post's images, listing each slide path —
**Read each** to see the carousel in order. If the post has video slides, it
points you at reel.py for those.

### A reel — caption, cover, transcription, frames (on demand)

```bash
# default: caption + cover thumbnail (Read it) + duration:
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/instagram/reel.py" "<post-url>"
# transcribe the speech locally (reels have no native captions):
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/instagram/reel.py" "<post-url>" --transcribe
# see specific moments (seek, no download):
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/instagram/reel.py" "<post-url>" --frames 5,35,60
# BATCH — transcribe several reels at once (e.g. all reels from a profile index):
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/instagram/reel.py" "<url1>" "<url2>" "<url3>"
```

Same on-demand discipline as YouTube: start with caption + cover, and only
transcribe or pull frames when the task needs the speech or the screen. When a
task needs several reels transcribed (analyzing a profile), pass all the URLs in
one call — the model loads once and audio downloads in parallel, far faster than
one call per reel.

## The Twitter/X tools — fetch only what a need points at

X needs `curl_cffi` installed (`pip install curl_cffi`) for impersonation.
Pulling a thread/conversation also needs login cookies
(`WATCH_COOKIES_FROM_BROWSER`, default `chrome:Profile 1`). X exposes full
metrics (views, likes, retweets, replies, quotes, bookmarks) — richer than IG.

A tweet can be plain text, text + up to 4 images, text + a video, a reply (needs
the parent for context), a quote (embeds another tweet), or the head of a thread
(the author continues in self-replies). The text is always central. Many X
videos are silent UI demos — transcription returns nothing and frames are what
you want.

### A tweet — text + metrics + images + context

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/twitter/tweet.py" "https://x.com/<user>/status/<id>"
# when the tweet is a reply/quote or the sense needs the whole thread:
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/twitter/tweet.py" "<url>" --thread
```

Prints the text, metrics, and any images (Read each). It flags when the tweet is
a reply/quote (so you know context exists) and whether it has a video. Use
`--thread` only when the meaning depends on the surrounding conversation — not by
reflex.

### A tweet's video — text, transcription, frames

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/twitter/video.py" "<tweet-url>"            # text + hints
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/twitter/video.py" "<tweet-url>" --transcribe
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/twitter/video.py" "<tweet-url>" --frames 3,8,15
```

Speech via local transcription (X videos have no captions); frames via remote
seek (no download). If transcription comes back empty, the video is silent —
switch to `--frames`.

## The Reddit tools — fetch only what a need points at

Reddit needs login cookies (`WATCH_COOKIES_FROM_BROWSER`). It's the richest for
structure: the post AND the comment tree come back together — and on Reddit the
discussion is often the real value. A post is text, link, image, gallery, or
video.

### A post — title, body, comments, media

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/reddit/post.py" "https://www.reddit.com/r/<sub>/comments/<id>/<slug>/"
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/reddit/post.py" "<url>" --top-comments 10
```

Prints title, score, body (selftext), and the top comments with their replies.
Downloads image/gallery posts (Read each). If it's a video post, it points you
at video.py.

### A video post — transcription, frames

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/reddit/video.py" "<post-url>" --transcribe
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/reddit/video.py" "<post-url>" --frames 3,8,15
```

Reddit video is HLS, which doesn't seek reliably over the network, so frames
download the (short) clip first, then seek locally.

## The LinkedIn tools — fetch only what a need points at

LinkedIn splits by post type:
- **Video posts** → `video.py` (yt-dlp; works even without login). Pulls post
  text + transcription + frames.
- **Image / text posts** → `post.py` (needs login cookies; reads the
  authenticated page to get the text and full-res images).

```bash
# image / text post:
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/linkedin/post.py" "<post-url>"
# video post:
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/linkedin/video.py" "<post-url>"
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/linkedin/video.py" "<post-url>" --transcribe
python3 "${CLAUDE_SKILL_DIR}/scripts/platforms/linkedin/video.py" "<post-url>" --frames 3,30
```

If you're unsure whether a LinkedIn post is video or image, try `video.py`
first; if it reports no video, use `post.py`. The post-text reconstruction for
image posts is best-effort (LinkedIn's internal format) — the images always come
through.

## Why on demand

Pulling the whole video, transcribing all of it, and sampling dozens of frames
up front is slow (minutes on a long video) and burns tokens on material you may
never use. Fetching only what the task or question points at keeps the common
case at seconds and a few cents of context.

## Cleanup

Each script prints a temp directory. If the user won't ask follow-ups, delete it
with `rm -rf <dir>`. If they might, leave it so you can reuse what you fetched.

## Setup

Run the preflight to confirm dependencies before the cascade (clear messages
instead of mid-run errors):

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/check.py"
```

Requires `yt-dlp` and `ffmpeg` (always), and `faster-whisper` (only for local
transcription). If a script reports one is missing, relay the install command it
printed. Optional: set `WATCH_YOUTUBE_API_KEY` for richer/faster channel
metrics (see the marketing tool).
