#!/usr/bin/env python3
"""TikTok — consume a photo/slideshow post: caption + the slide images.

TikTok's "photo mode" is a carousel of still images with a background sound
(like an Instagram carousel). yt-dlp exposes the images in the post JSON. This
pulls the caption/stats and downloads each slide so Claude can Read them.

    python3 post.py "<url>"                 # caption + stats + download slides
    python3 post.py "<url>" --transcribe    # also transcribe the background sound

For a normal TikTok video, use video.py instead. If the URL turns out to be a
video (no images), this points you there.
Self-contained on purpose: only transcription is shared (DRY came empirically).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


def _need_ytdlp():
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed")


def post_meta(url: str) -> dict:
    _need_ytdlp()
    r = subprocess.run(["yt-dlp", "--skip-download", "--no-warnings", "-J", "--", url],
                       capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        print(r.stderr[-400:], file=sys.stderr)
        raise SystemExit("could not read this TikTok (blocked or removed?)")
    images = d.get("images") or []
    img_urls = [im.get("url") if isinstance(im, dict) else im for im in images]
    return {
        "description": d.get("description") or "",
        "uploader": d.get("uploader"),
        "view_count": d.get("view_count"),
        "like_count": d.get("like_count"),
        "comment_count": d.get("comment_count"),
        "track": d.get("track"),
        "images": [u for u in img_urls if u],
    }


def download_images(img_urls: list[str], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    req_headers = {"User-Agent": "Mozilla/5.0"}
    for i, u in enumerate(img_urls):
        ext = ".jpg"
        for cand in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
            if cand in u.lower():
                ext = cand
                break
        dst = out_dir / f"slide_{i + 1:02d}{ext}"
        try:
            req = urllib.request.Request(u, headers=req_headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                dst.write_bytes(r.read())
            paths.append(dst)
        except Exception as exc:  # noqa: BLE001
            print(f"[watch] slide {i + 1} download failed: {exc}", file=sys.stderr)
    return paths


def fetch_audio(url: str, out_dir: Path) -> Path | None:
    """The slideshow's background sound (a single mp3 in yt-dlp)."""
    _need_ytdlp()
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "-N", "8", "-f", "ba/b", "-x", "--audio-format", "mp3",
           "--postprocessor-args", "-ar 16000 -ac 1 -b:a 64k", "--no-playlist",
           "-o", str(out_dir / "audio.%(ext)s"), "--", url]
    subprocess.run(cmd, capture_output=True, text=True)
    audio = out_dir / "audio.mp3"
    return audio if audio.exists() else None


# Transcription is shared across platforms — use the core. See scripts/lib/transcribe.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from transcribe import transcribe, format_transcript as fmt_transcript  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(prog="post", description="Consume a TikTok photo/slideshow post.")
    ap.add_argument("url", help="TikTok photo-mode URL")
    ap.add_argument("--transcribe", action="store_true", help="Also transcribe the background sound")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="watch-tt-post-"))
    m = post_meta(args.url)

    if not m["images"]:
        print("[watch] no images — this looks like a video, not a photo post. Use video.py.",
              file=sys.stderr)
        print("# TikTok post\n\nThis URL has no slide images — it's likely a video. "
              "Use `video.py` for it.")
        return 0

    print(f"[watch] downloading {len(m['images'])} slide(s)…", file=sys.stderr)
    paths = download_images(m["images"], work / "slides")

    print("# TikTok photo post\n")
    if m.get("uploader"):
        print(f"- **Uploader:** @{m['uploader']}")
    stats = []
    for label, key in [("likes", "like_count"), ("comments", "comment_count"),
                       ("views", "view_count")]:
        if m.get(key) is not None:
            stats.append(f"{m[key]:,} {label}")
    if stats:
        print(f"- **Stats:** {' · '.join(stats)}")
    if m.get("track"):
        print(f"- **Sound:** {m['track']}")
    if m.get("description"):
        print(f"\n**Caption:** {m['description']}")

    print(f"\n## Slides ({len(paths)}) — read each to see the image\n")
    for p in paths:
        print(f"- `{p}`")

    if args.transcribe:
        print("\n[watch] transcribing background sound…", file=sys.stderr)
        audio = fetch_audio(args.url, work)
        if audio:
            segs = transcribe(audio)
            print("\n## Background sound (spoken)\n```")
            print(fmt_transcript(segs) if segs else "(music only — no speech.)")
            print("```")

    print(f"\n_Slides in: `{work}` — delete when done._")
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
