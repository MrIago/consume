#!/usr/bin/env python3
"""Instagram — consume one post: caption + its media (image / carousel / mixed).

Downloads the post's media so you can Read the images directly, and always
surfaces the caption (the copy — often the most important part). A post can be:
  - a single image
  - a carousel of N images (slides)
  - a "mixed" carousel with one or more videos among the images

Images are downloaded and listed for you to Read. Videos inside the post are
NOT transcribed here — that's reel.py's job; this script points you at them.

Cookies: WATCH_COOKIES_FROM_BROWSER (default "chrome:Profile 1"). Needs
gallery-dl (and `secretstorage` on Linux to read Chrome cookies).

Usage:
    python3 post.py "https://www.instagram.com/p/<shortcode>/"

Self-contained on purpose: no shared core yet (DRY comes later, empirically).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
import config  # noqa: E402  — reads env var OR ~/.config/consume/.env
# Browser/profile for login cookies. Default "chrome" = Chrome's default profile
# (most common). Use "chrome:Profile 1" for a named profile, or "firefox"/"edge".
COOKIES = config.get("WATCH_COOKIES_FROM_BROWSER", "chrome")
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VID_EXTS = {".mp4", ".mov", ".webm"}


def _need_gallery_dl():
    if shutil.which("gallery-dl") is None:
        raise SystemExit("gallery-dl is not installed (pip install gallery-dl)")


def post_info(url: str) -> dict:
    """Metadata for the post (caption + slide types) without downloading."""
    _need_gallery_dl()
    cmd = ["gallery-dl", "--cookies-from-browser", COOKIES, "--no-download", "-j", "--", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stderr, file=sys.stderr)
        raise SystemExit("could not read post (login/rate-limit? check cookies)")
    slides, caption = [], ""
    for el in data:
        if isinstance(el, list) and len(el) >= 3 and isinstance(el[-1], dict):
            m = el[-1]
            slides.append("video" if m.get("video_url") else "image")
            if not caption and m.get("description"):
                caption = m["description"].strip()
    return {"slides": slides, "caption": caption}


def download_media(url: str, out_dir: Path) -> list[Path]:
    """Download all of the post's media into out_dir, return the files in order."""
    _need_gallery_dl()
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["gallery-dl", "--cookies-from-browser", COOKIES, "-D", str(out_dir), "--", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    files = sorted(p for p in out_dir.iterdir()
                   if p.suffix.lower() in IMG_EXTS | VID_EXTS)
    if not files:
        print(result.stderr, file=sys.stderr)
        raise SystemExit("no media downloaded (login/rate-limit? check cookies)")
    return files


def main() -> int:
    ap = argparse.ArgumentParser(prog="post", description="Consume one Instagram post (caption + media).")
    ap.add_argument("url", help="Post URL, e.g. https://www.instagram.com/p/<shortcode>/")
    ap.add_argument("--no-download", action="store_true", help="Only show caption + media types, don't download")
    args = ap.parse_args()

    print(f"[watch] reading Instagram post (cookies={COOKIES})…", file=sys.stderr)
    info = post_info(args.url)
    slides = info["slides"]
    n_img, n_vid = slides.count("image"), slides.count("video")
    if len(slides) <= 1:
        kind = "reel" if slides == ["video"] else "single image"
    elif n_vid:
        kind = f"mixed carousel ({n_img} images + {n_vid} videos)"
    else:
        kind = f"carousel ({n_img} images)"

    print("# Instagram post\n")
    print(f"- **Type:** {kind}")
    print(f"- **URL:** {args.url}")
    print("\n## Caption\n")
    print("```")
    print(info["caption"] or "(no caption)")
    print("```")

    if args.no_download:
        return 0

    print(f"\n[watch] downloading {len(slides)} media item(s)…", file=sys.stderr)
    out_dir = Path(tempfile.mkdtemp(prefix="watch-ig-post-"))
    files = download_media(args.url, out_dir)
    images = [f for f in files if f.suffix.lower() in IMG_EXTS]
    videos = [f for f in files if f.suffix.lower() in VID_EXTS]

    if images:
        print("\n## Images\n")
        print("**Read each path below to see the slide.** They're in carousel order.\n")
        for i, f in enumerate(images, 1):
            print(f"- slide {i}: `{f}`")
    if videos:
        print("\n## Videos in this post\n")
        print("This post contains video slide(s). To hear/see them, run reel.py on "
              "the post URL (transcription + frames):\n")
        for f in videos:
            print(f"- `{f}` (already downloaded; or use reel.py on {args.url})")

    print(f"\n_Media in: `{out_dir}` — delete when done._")
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
