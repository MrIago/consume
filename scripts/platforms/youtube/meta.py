#!/usr/bin/env python3
"""YouTube — marketing metadata: title, description, thumbnail, stats, tags.

This is the "packaging" of a video, not its content. Use it on demand when the
task is about marketing — why a title/thumbnail works, a channel's strategy,
ranking a channel's videos by views — rather than what the video says (that's
captions.py / transcribe.py).

Everything here is fetched WITHOUT downloading the video.

Usage:
    # one video: title, description, stats, tags, + thumbnail saved for you to Read
    python3 meta.py "<video-url>"

    # a channel: list videos. Fast mode = titles only; --stats = with view/like
    # counts so you can rank by views (slower, ~1.2s/video).
    python3 meta.py "<channel-url-or-@handle>" --channel [--limit N] [--stats] [--sort views|date]

Self-contained on purpose: no shared core yet (DRY comes later, empirically).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


# --- YouTube Data API v3 (optional, preferred for channel stats) -------------
# Set WATCH_YOUTUBE_API_KEY to use the official API: faster, structured metrics,
# and channel ranking by views in a couple of batched calls instead of ~1.2s
# per video via yt-dlp. Without the key, everything falls back to yt-dlp.
API_KEY = os.environ.get("WATCH_YOUTUBE_API_KEY", "").strip()
API_BASE = "https://www.googleapis.com/youtube/v3"

_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")
_CHANNEL_ID_RE = re.compile(r"/channel/(UC[A-Za-z0-9_-]{22})")


def _api_get(path: str, **params) -> dict:
    params["key"] = API_KEY
    url = f"{API_BASE}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def _extract_video_id(url: str) -> str | None:
    m = _VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


def _resolve_channel_id(url: str) -> str | None:
    """Get the UC… channel id from a channel URL or @handle (via API)."""
    m = _CHANNEL_ID_RE.search(url)
    if m:
        return m.group(1)
    # @handle or /c/custom — ask the API to resolve it
    handle = None
    hm = re.search(r"@([A-Za-z0-9._-]+)", url)
    if hm:
        handle = "@" + hm.group(1)
    try:
        if handle:
            d = _api_get("channels", part="id", forHandle=handle)
            if d.get("items"):
                return d["items"][0]["id"]
        # last resort: search
        d = _api_get("search", part="snippet", type="channel", q=url, maxResults=1)
        if d.get("items"):
            return d["items"][0]["snippet"]["channelId"]
    except Exception as exc:
        print(f"[watch] could not resolve channel via API: {exc}", file=sys.stderr)
    return None


def _need_ytdlp():
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed (brew install yt-dlp / pipx install yt-dlp)")


def _video_meta_api(url: str) -> dict | None:
    """Video metadata via the Data API. Returns None to signal fallback."""
    if not API_KEY:
        return None
    vid = _extract_video_id(url)
    if not vid:
        return None
    try:
        d = _api_get("videos", part="snippet,statistics,contentDetails", id=vid)
        if not d.get("items"):
            return None
        it = d["items"][0]
        sn, st = it["snippet"], it.get("statistics", {})
        return {
            "title": sn.get("title"),
            "description": sn.get("description"),
            "channel": sn.get("channelTitle"),
            "channel_followers": None,  # needs a separate channels call; skip
            "views": int(st["viewCount"]) if "viewCount" in st else None,
            "likes": int(st["likeCount"]) if "likeCount" in st else None,
            "comments": int(st["commentCount"]) if "commentCount" in st else None,
            "duration": it.get("contentDetails", {}).get("duration"),  # ISO 8601
            "upload_date": (sn.get("publishedAt") or "")[:10],
            "tags": sn.get("tags") or [],
            "url": f"https://www.youtube.com/watch?v={vid}",
        }
    except Exception as exc:
        print(f"[watch] Data API video lookup failed ({exc}); using yt-dlp", file=sys.stderr)
        return None


def video_meta(url: str) -> dict:
    """Full marketing metadata for one video (no video download).

    Prefers the YouTube Data API (if WATCH_YOUTUBE_API_KEY is set), falls back
    to yt-dlp.
    """
    api = _video_meta_api(url)
    if api is not None:
        return api
    _need_ytdlp()
    cmd = ["yt-dlp", "--skip-download", "--dump-single-json", "--no-playlist", "--", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and not result.stdout.strip():
        print(result.stderr, file=sys.stderr)
        raise SystemExit("could not fetch video metadata")
    raw = json.loads(result.stdout)
    return {
        "title": raw.get("title"),
        "description": raw.get("description"),
        "channel": raw.get("channel") or raw.get("uploader"),
        "channel_followers": raw.get("channel_follower_count"),
        "views": raw.get("view_count"),
        "likes": raw.get("like_count"),
        "comments": raw.get("comment_count"),
        "duration": raw.get("duration_string") or raw.get("duration"),
        "upload_date": raw.get("upload_date"),
        "tags": raw.get("tags") or [],
        "url": raw.get("webpage_url") or url,
    }


def fetch_thumbnail(url: str, out_dir: Path) -> Path | None:
    """Download the thumbnail as jpg (no video download). The model Reads it."""
    _need_ytdlp()
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp", "--skip-download", "--write-thumbnail",
        "--convert-thumbnails", "jpg", "--no-playlist",
        "-o", str(out_dir / "thumb.%(ext)s"), "--", url,
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    hits = sorted(out_dir.glob("thumb*.jpg"))
    return hits[0] if hits else None


def _channel_videos_api(url: str, limit: int) -> list[dict] | None:
    """Channel videos with metrics via the Data API (always includes stats —
    they come essentially for free in batches of 50). Returns None to fall back.
    """
    if not API_KEY:
        return None
    ch = _resolve_channel_id(url)
    if not ch:
        return None
    try:
        cd = _api_get("channels", part="contentDetails", id=ch)
        if not cd.get("items"):
            return None
        uploads = cd["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        # page through the uploads playlist until we have `limit` ids
        ids: list[str] = []
        page = None
        while len(ids) < limit:
            kw = {"part": "contentDetails", "playlistId": uploads,
                  "maxResults": min(50, limit - len(ids))}
            if page:
                kw["pageToken"] = page
            pl = _api_get("playlistItems", **kw)
            ids += [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
            page = pl.get("nextPageToken")
            if not page:
                break
        out: list[dict] = []
        for i in range(0, len(ids), 50):  # videos endpoint: up to 50 ids/call
            batch = ids[i:i + 50]
            v = _api_get("videos", part="snippet,statistics", id=",".join(batch))
            for it in v.get("items", []):
                sn, st = it["snippet"], it.get("statistics", {})
                out.append({
                    "id": it["id"],
                    "url": f"https://www.youtube.com/watch?v={it['id']}",
                    "views": int(st["viewCount"]) if "viewCount" in st else None,
                    "likes": int(st["likeCount"]) if "likeCount" in st else None,
                    "upload_date": (sn.get("publishedAt") or "")[:10],
                    "title": sn.get("title"),
                })
        return out
    except Exception as exc:
        print(f"[watch] Data API channel lookup failed ({exc}); using yt-dlp", file=sys.stderr)
        return None


def channel_videos(url: str, limit: int, with_stats: bool) -> list[dict]:
    """List a channel's videos. Without stats it's fast (titles only); with
    stats it pulls view/like counts per video so the caller can rank.

    With the Data API key set, always returns stats (they're cheap there) and
    `with_stats` only affects how the yt-dlp fallback behaves.
    """
    api = _channel_videos_api(url, limit)
    if api is not None:
        return api
    _need_ytdlp()
    if "/videos" not in url and "/streams" not in url:
        url = url.rstrip("/") + "/videos"
    if with_stats:
        # full extraction per entry → view_count/like_count populated (~1.2s/video)
        fmt = "%(id)s\t%(view_count)s\t%(like_count)s\t%(upload_date)s\t%(title)s"
        cmd = ["yt-dlp", "--skip-download", "--playlist-end", str(limit),
               "--print", fmt, "--", url]
    else:
        fmt = "%(id)s\t\t\t\t%(title)s"
        cmd = ["yt-dlp", "--flat-playlist", "--playlist-end", str(limit),
               "--print", fmt, "--", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    out: list[dict] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        vid, views, likes, date, title = parts[0], parts[1], parts[2], parts[3], parts[4]
        out.append({
            "id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "views": int(views) if views.isdigit() else None,
            "likes": int(likes) if likes.isdigit() else None,
            "upload_date": date if date and date != "NA" else None,
            "title": title,
        })
    return out


def _fmt_duration(dur) -> str:
    """Normalize duration to M:SS / H:MM:SS. Accepts ISO 8601 (PT17M50S),
    a string like '17:50', or seconds."""
    if dur is None:
        return "?"
    s = str(dur)
    iso = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s)
    if iso and any(iso.groups()):
        h, m, sec = (int(g) if g else 0 for g in iso.groups())
        total = h * 3600 + m * 60 + sec
    elif s.replace(".", "").isdigit():
        total = int(float(s))
    else:
        return s  # already human-readable
    h, rem = divmod(total, 3600)
    mm, ss = divmod(rem, 60)
    return f"{h}:{mm:02d}:{ss:02d}" if h else f"{mm}:{ss:02d}"


def _print_video(m: dict, thumb: Path | None) -> None:
    print("# YouTube video — marketing metadata\n")
    print(f"- **Title:** {m['title']}")
    chan = m["channel"]
    if m.get("channel_followers"):
        chan += f" ({m['channel_followers']} subscribers)"
    print(f"- **Channel:** {chan}")
    stats = f"{m['views']} views · {m['likes']} likes"
    if m.get("comments") is not None:
        stats += f" · {m['comments']} comments"
    print(f"- **Stats:** {stats}")
    print(f"- **Duration:** {_fmt_duration(m['duration'])} · **Uploaded:** {m['upload_date']}")
    if m["tags"]:
        print(f"- **Tags:** {', '.join(m['tags'][:20])}")
    if thumb:
        print(f"- **Thumbnail:** `{thumb}` — Read it to see the cover the channel used to sell the click.")
    print("\n## Description\n")
    print("```")
    print(m["description"] or "(empty)")
    print("```")


def _print_channel(videos: list[dict], sort: str) -> None:
    # Show stats whenever they're actually present (the Data API always provides
    # them; the yt-dlp flat path doesn't).
    have_stats = any(v.get("views") is not None for v in videos)
    if have_stats and sort == "views":
        videos = sorted(videos, key=lambda v: v["views"] or -1, reverse=True)
    elif sort == "date":
        videos = sorted(videos, key=lambda v: v["upload_date"] or "", reverse=True)
    print(f"# YouTube channel — {len(videos)} videos\n")
    for v in videos:
        if v.get("views") is not None:
            print(f"- {v['views']} views · {v['likes']} likes · {v['upload_date']} — "
                  f"{v['title']}  ({v['url']})")
        else:
            print(f"- {v['title']}  ({v['url']})")


def main() -> int:
    ap = argparse.ArgumentParser(prog="meta", description="YouTube marketing metadata (video or channel).")
    ap.add_argument("url", help="Video URL, or channel URL/@handle with --channel")
    ap.add_argument("--channel", action="store_true", help="Treat URL as a channel and list its videos")
    ap.add_argument("--limit", type=int, default=10, help="Channel mode: how many videos to list (default 10)")
    ap.add_argument("--stats", action="store_true", help="Channel mode: include view/like counts (slower)")
    ap.add_argument("--sort", choices=["views", "date"], default="date", help="Channel mode: ranking (needs --stats for views)")
    ap.add_argument("--no-thumb", action="store_true", help="Video mode: skip downloading the thumbnail")
    args = ap.parse_args()

    if args.channel:
        src = "Data API" if API_KEY else "yt-dlp"
        print(f"[watch] listing channel videos via {src} (stats={args.stats or bool(API_KEY)})…", file=sys.stderr)
        videos = channel_videos(args.url, limit=args.limit, with_stats=args.stats)
        _print_channel(videos, sort=args.sort)
        return 0

    print("[watch] fetching video marketing metadata (no video download)…", file=sys.stderr)
    m = video_meta(args.url)
    thumb = None if args.no_thumb else fetch_thumbnail(args.url, Path(tempfile.mkdtemp(prefix="watch-yt-meta-")))
    _print_video(m, thumb)
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
