#!/usr/bin/env python3
"""TikTok — consume a video post: caption/stats, transcription, frames.

For a normal TikTok video (not the photo/slideshow mode — use post.py for that).
yt-dlp reads TikTok without login. There are no native captions, so speech comes
from transcription (the shared core: Groq/OpenAI/local). Frames are seeked from
the stream URL — no full download.

    python3 video.py "<url>"               # caption + stats + hints
    python3 video.py "<url>" --transcribe
    python3 video.py "<url>" --frames 2,6,10

Notes learned empirically:
- TikTok formats are combined video+audio mp4 (no separate `bestaudio`); use
  `-f "ba/b"` to pull audio.
- The extractor uses impersonation — `curl_cffi` makes it more robust (optional).
Self-contained on purpose: only transcription is shared (DRY came empirically).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def parse_time(value: str) -> float:
    parts = str(value).strip().split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    raise SystemExit(f"Cannot parse time: {value!r}")


def fmt_time(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _need_ytdlp():
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed")


def meta(url: str) -> dict:
    _need_ytdlp()
    r = subprocess.run(["yt-dlp", "--skip-download", "--no-warnings", "-J", "--", url],
                       capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        print(r.stderr[-400:], file=sys.stderr)
        raise SystemExit("could not read this TikTok (blocked or removed?)")
    return {
        "title": d.get("title") or "",
        "description": d.get("description") or "",
        "uploader": d.get("uploader"),
        "duration": d.get("duration"),
        "view_count": d.get("view_count"),
        "like_count": d.get("like_count"),
        "comment_count": d.get("comment_count"),
        "repost_count": d.get("repost_count"),
        "track": d.get("track"),
        "is_slideshow": bool(d.get("images")),
    }


def fetch_audio(url: str, out_dir: Path) -> Path:
    """TikTok has no separate audio track — pull it from the combined mp4."""
    _need_ytdlp()
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "-N", "8", "-f", "ba/b", "-x", "--audio-format", "mp3",
           "--postprocessor-args", "-ar 16000 -ac 1 -b:a 64k", "--no-playlist",
           "-o", str(out_dir / "audio.%(ext)s"), "--", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    audio = out_dir / "audio.mp3"
    if not audio.exists():
        print(result.stderr[-400:], file=sys.stderr)
        raise SystemExit("could not download audio")
    return audio


def fetch_video(url: str, out_dir: Path) -> Path:
    """Download the video locally. TikTok stream URLs return 403 on remote seek
    (they need the session headers), so for frames we download (clips are short)
    and seek the local file."""
    _need_ytdlp()
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "-N", "8", "-f", "bv*[height<=720]+ba/b", "--no-playlist",
           "-o", str(out_dir / "video.%(ext)s"), "--", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    vids = sorted(out_dir.glob("video.*"))
    if not vids:
        print(result.stderr[-400:], file=sys.stderr)
        raise SystemExit("could not download the video (is this a photo post? use post.py)")
    return vids[0]


def _seek_one(src, out_path, ts, resolution):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{ts:.3f}",
           "-i", src, "-frames:v", "1", "-vf", f"scale={resolution}:-2", "-q:v", "4", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out_path.exists():
        print(f"[watch] frame at {ts:.1f}s failed: {r.stderr.strip()[:100]}", file=sys.stderr)
        return None
    return {"timestamp_seconds": round(ts, 2), "path": str(out_path)}


def extract_frames(src, out_dir: Path, timestamps, resolution=512):
    import concurrent.futures
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_sorted = sorted(timestamps)
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(ts_sorted) or 1)) as ex:
        futs = {ex.submit(_seek_one, src, out_dir / f"frame_{i:04d}.jpg", ts, resolution): i
                for i, ts in enumerate(ts_sorted)}
        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            if r:
                results[futs[fut]] = r
    return [results[i] for i in sorted(results)]


# Transcription is shared across platforms — use the core (Groq/OpenAI/local +
# chunking). See scripts/lib/transcribe.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from transcribe import transcribe, format_transcript as fmt_transcript  # noqa: E402


def _print_meta(m: dict) -> None:
    print(f"- **Uploader:** @{m['uploader']}" if m.get("uploader") else "")
    if m.get("duration"):
        print(f"- **Duration:** {fmt_time(m['duration'])}")
    stats = []
    for label, key in [("views", "view_count"), ("likes", "like_count"),
                       ("comments", "comment_count"), ("reposts", "repost_count")]:
        if m.get(key) is not None:
            stats.append(f"{m[key]:,} {label}")
    if stats:
        print(f"- **Stats:** {' · '.join(stats)}")
    if m.get("track"):
        print(f"- **Sound:** {m['track']}")


def main() -> int:
    ap = argparse.ArgumentParser(prog="video", description="Consume a TikTok video on demand.")
    ap.add_argument("url", help="TikTok video URL")
    ap.add_argument("--transcribe", action="store_true")
    ap.add_argument("--frames", help="Comma-separated timestamps (e.g. 2,6,10)")
    ap.add_argument("--resolution", type=int, default=512)
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="watch-tt-vid-"))
    m = meta(args.url)

    if m["is_slideshow"]:
        print("[watch] this is a TikTok photo/slideshow post — use post.py for the images.",
              file=sys.stderr)

    if args.frames:
        ts = [parse_time(t) for t in args.frames.split(",") if t.strip()]
        # TikTok blocks remote seek (403), so download then seek the local file.
        print(f"[watch] downloading video to grab {len(ts)} frame(s)…", file=sys.stderr)
        local = fetch_video(args.url, work)
        frames = extract_frames(str(local), work / "frames", ts, args.resolution)
        print("# TikTok video — frames\n")
        print("**Read each frame path to see the screen at that moment.**\n")
        for f in frames:
            print(f"- `{f['path']}` (t={fmt_time(f['timestamp_seconds'])})")
        print(f"\n_Frames in: `{work}` — delete when done._")
        return 0

    if args.transcribe:
        print("[watch] downloading audio…", file=sys.stderr)
        audio = fetch_audio(args.url, work)
        segs = transcribe(audio)
        print("# TikTok video — transcript\n")
        _print_meta(m)
        if m.get("description"):
            print(f"\n**Caption:** {m['description']}")
        print("\n## Spoken\n```")
        print(fmt_transcript(segs) if segs else "(no speech detected — likely music-only; use --frames.)")
        print("```")
        print(f"\n_Work dir: `{work}` — delete when done._")
        return 0

    print("# TikTok video\n")
    print(f"- **URL:** {args.url}")
    _print_meta(m)
    if m.get("description"):
        print(f"\n**Caption:** {m['description']}")
    print("\nTo go deeper: `--transcribe` for the speech, `--frames 2,6` to see moments.")
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
