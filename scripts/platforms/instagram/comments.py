#!/usr/bin/env python3
"""Instagram: comments on a post or reel.

The comment section is a second, independent source on the same subject. On
Instagram it also carries something YouTube's does not: the author frequently
answers in the comments, so a question the caption left open is often resolved
there.

Needs login cookies, like every other Instagram script here
(WATCH_COOKIES_FROM_BROWSER, default "chrome:Profile 1"). Instagram exposes
comment text, author and like count, but no reply threading.

Usage:
    python3 comments.py <post-or-reel-url> [--limit 40] [--min-likes 1]

Self-contained on purpose, matching the other scripts in this platform dir.
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

DEFAULT_BROWSER = "chrome:Profile 1"


def fail(msg: str, code: int = 1):
    print(f"[watch] {msg}", file=sys.stderr)
    sys.exit(code)


def fetch(url: str, limit: int, browser: str) -> dict:
    if not shutil.which("yt-dlp"):
        fail("yt-dlp not found. Install with: pip install --upgrade --break-system-packages yt-dlp")

    with tempfile.TemporaryDirectory(prefix="consume-ig-comments-") as td:
        out = Path(td) / "c"
        cmd = [
            "yt-dlp", "--no-update", "--no-warnings", "--skip-download",
            "--write-comments",
            "--extractor-args", f"instagram:max_comments={limit}",
            "--cookies-from-browser", browser,
            "-o", str(out), url,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        meta = out.with_suffix(".info.json")
        if not meta.exists():
            err = (r.stderr or "")
            if "login" in err.lower() or "cookies" in err.lower() or "rate" in err.lower():
                fail(f"Instagram needs login. Set WATCH_COOKIES_FROM_BROWSER to a browser "
                     f"where you are logged in (current: {browser}). On Linux, Chrome cookies "
                     f"also need: pip install secretstorage")
            tail = err.strip().splitlines()[-3:]
            fail("could not fetch comments. " + " | ".join(tail))
        return json.loads(meta.read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--min-likes", type=int, default=0)
    a = ap.parse_args()

    browser = os.environ.get("WATCH_COOKIES_FROM_BROWSER", DEFAULT_BROWSER)
    print(f"[watch] fetching Instagram comments (cookies={browser})...", file=sys.stderr)

    data = fetch(a.url, a.limit, browser)
    comments = data.get("comments") or []
    if not comments:
        print("NO_COMMENTS")
        return

    uploader = (data.get("uploader") or data.get("channel") or "").lstrip("@").lower()
    comments.sort(key=lambda c: -(c.get("like_count") or 0))

    print(f"\n# Comments on {data.get('webpage_url', a.url)}")
    print(f"\n_{len(comments)} fetched, {data.get('comment_count', '?')} total._\n")

    shown = 0
    for c in comments:
        likes = c.get("like_count") or 0
        if likes < a.min_likes:
            continue
        author = (c.get("author") or c.get("author_id") or "?").lstrip("@")
        # The author replying in the comments is the highest-signal line here.
        mark = " ⭐AUTHOR" if author.lower() == uploader else ""
        shown += 1
        lk = f"[{likes} likes] " if likes else ""
        print(f"**{lk}{author}{mark}**")
        print((c.get("text") or "").strip())
        print()

    if not shown:
        print(f"_No comment reached --min-likes {a.min_likes}._")


if __name__ == "__main__":
    main()
