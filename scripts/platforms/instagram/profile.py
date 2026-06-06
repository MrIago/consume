#!/usr/bin/env python3
"""Instagram — list a profile's posts (the index for studying a profile).

This is the on-demand starting point for "study this profile": it lists posts
with their type (reel / image / carousel), caption, likes and date — without
downloading any media. From this index, the orchestrator decides which posts to
actually consume (via post.py / reel.py), and how many — never the whole profile
by reflex.

Instagram requires login: cookies come from your browser via
WATCH_COOKIES_FROM_BROWSER (default "chrome:Profile 1"). Needs gallery-dl, and
on Linux `secretstorage` to read Chrome's encrypted cookies.

Usage:
    python3 profile.py "https://www.instagram.com/<user>/" [--limit N] [--sort likes|date]

Self-contained on purpose: no shared core yet (DRY comes later, empirically).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

COOKIES = os.environ.get("WATCH_COOKIES_FROM_BROWSER", "chrome:Profile 1")


def _need_gallery_dl():
    if shutil.which("gallery-dl") is None:
        raise SystemExit("gallery-dl is not installed (pip install gallery-dl)")


def list_posts(profile_url: str, limit: int) -> list[dict]:
    """Group gallery-dl's per-slide entries into posts (by post_shortcode).

    gallery-dl flattens a carousel into one entry per slide; the real post is
    identified by `post_shortcode`, with `count` = number of slides and each
    slide carrying a video_url (video) or not (image).
    """
    _need_gallery_dl()
    url = profile_url.rstrip("/")
    if not url.endswith("/posts"):
        url += "/posts"
    # gallery-dl's --range counts SLIDES, not posts; carousels expand to many.
    # Most posts are 1-2 slides, so ~3x covers the typical mix. Cap the range so
    # a big --limit doesn't fetch hundreds of slides and trip Instagram's
    # rate-limit. For deep ranking, raise --limit in steps rather than all at once.
    span = min(max(limit * 3, limit + 5), 60)
    cmd = ["gallery-dl", "--cookies-from-browser", COOKIES,
           "--range", f"1-{span}", "--no-download", "-j", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and not result.stdout.strip():
        _report_error(result.stderr, profile_url)
        raise SystemExit("could not list profile posts")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        _report_error(result.stderr, profile_url)
        raise SystemExit("gallery-dl returned no parseable data (login/rate-limit?)")

    posts: dict[str, dict] = {}
    order: list[str] = []
    for el in data:
        if not (isinstance(el, list) and len(el) >= 3 and isinstance(el[-1], dict)):
            continue
        m = el[-1]
        ps = m.get("post_shortcode") or m.get("shortcode")
        if not ps:
            continue
        if ps not in posts:
            posts[ps] = {
                "shortcode": ps,
                "url": f"https://www.instagram.com/p/{ps}/",
                "slides": [],
                "caption": (m.get("description") or "").strip(),
                "likes": m.get("likes"),
                "date": str(m.get("date") or m.get("post_date") or "")[:10],
                "count": m.get("count"),
            }
            order.append(ps)
        posts[ps]["slides"].append("video" if m.get("video_url") else "image")

    out = []
    for ps in order[:limit]:
        p = posts[ps]
        p["type"] = _classify(p["slides"])
        out.append(p)
    return out


def _classify(slides: list[str]) -> str:
    if len(slides) == 1:
        return "reel" if slides[0] == "video" else "image"
    if "video" in slides:
        return f"carousel-mixed ({slides.count('image')}img+{slides.count('video')}vid)"
    return f"carousel ({len(slides)} images)"


def _report_error(stderr: str, url: str) -> None:
    low = (stderr or "").lower()
    print(stderr, file=sys.stderr)
    if "login" in low or "403" in low or "empty" in low or "challenge" in low:
        print("[watch] Instagram needs login — set WATCH_COOKIES_FROM_BROWSER to a "
              "browser logged into Instagram (default chrome:Profile 1). On Linux, "
              "`pip install secretstorage` to read Chrome cookies.", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(prog="profile", description="List an Instagram profile's posts.")
    ap.add_argument("url", help="Profile URL, e.g. https://www.instagram.com/user/")
    ap.add_argument("--limit", type=int, default=12, help="How many posts to list (default 12)")
    ap.add_argument("--sort", choices=["date", "likes"], default="date",
                    help="Order by most recent (date) or most liked (likes)")
    args = ap.parse_args()

    print(f"[watch] listing Instagram profile (cookies={COOKIES})…", file=sys.stderr)
    posts = list_posts(args.url, limit=args.limit)
    if args.sort == "likes":
        posts.sort(key=lambda p: p["likes"] or -1, reverse=True)

    print(f"# Instagram profile — {len(posts)} posts\n")
    print("Each line: type · likes · date · caption. Use post.py/reel.py on the ones the task needs.\n")
    for p in posts:
        cap = p["caption"].replace("\n", " ")[:70]
        likes = f"{p['likes']}♥" if p["likes"] is not None else "?♥"
        print(f"- **{p['type']}** · {likes} · {p['date']} — {cap}  ({p['url']})")
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
