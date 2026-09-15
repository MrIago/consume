#!/usr/bin/env python3
"""YouTube: top comments, sorted by likes.

The comment section is a second, independent source on the same subject. It
routinely carries what the video itself does not: a fix for a friction the
author showed but never solved, a viewer who tried the same thing and failed
(the negative case), a correction of something the author got wrong, and the
question everybody has (visible as the most-upvoted one).

Not part of the transcript cascade: comments are a *different* source, not a
deeper level of the same one. Pull them when the task is to study a subject
rather than to answer a single question about the video.

Usage:
    python3 comments.py <youtube-url> [--limit 30] [--min-likes 2]

Prints comments sorted by like count, replies nested under their parent.
Self-contained on purpose, matching the other scripts in this platform dir.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def fail(msg: str, code: int = 1):
    print(f"[watch] {msg}", file=sys.stderr)
    sys.exit(code)


def fetch(url: str, limit: int) -> dict:
    if not shutil.which("yt-dlp"):
        fail("yt-dlp not found. Install with: pip install --upgrade --break-system-packages yt-dlp")

    with tempfile.TemporaryDirectory(prefix="consume-yt-comments-") as td:
        out = Path(td) / "c"
        # max_comments takes 4 values: total, per-thread-root, replies, per-root-replies.
        # Sorting by "top" is what makes a small limit worth reading.
        cmd = [
            "yt-dlp", "--no-update", "--no-warnings", "--skip-download",
            "--write-comments",
            "--extractor-args",
            f"youtube:comment_sort=top;max_comments={limit},all,{limit}",
            "-o", str(out), url,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        meta = out.with_suffix(".info.json")
        if not meta.exists():
            tail = (r.stderr or "").strip().splitlines()[-3:]
            fail("could not fetch comments. " + " | ".join(tail))
        return json.loads(meta.read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--limit", type=int, default=30,
                    help="how many comments to fetch (default 30)")
    ap.add_argument("--min-likes", type=int, default=0,
                    help="skip comments below this like count")
    a = ap.parse_args()

    print("[watch] fetching YouTube comments (no video download)...", file=sys.stderr)
    data = fetch(a.url, a.limit)
    comments = data.get("comments") or []
    if not comments:
        print("NO_COMMENTS")
        return

    # Split roots from replies so replies can be nested under their parent.
    # yt-dlp encodes the parent as an id for replies, "root" for top-level.
    roots = [c for c in comments if (c.get("parent") in (None, "root"))]
    replies: dict[str, list] = {}
    for c in comments:
        p = c.get("parent")
        if p and p != "root":
            replies.setdefault(p, []).append(c)

    roots.sort(key=lambda c: -(c.get("like_count") or 0))

    print(f"\n# Comments: {data.get('title', '?')}")
    print(f"\n_{len(comments)} fetched, {data.get('comment_count', '?')} total on the video._\n")

    shown = 0
    for c in roots:
        likes = c.get("like_count") or 0
        if likes < a.min_likes:
            continue
        shown += 1
        pin = " 📌" if c.get("is_pinned") else ""
        fav = " ❤️author" if c.get("is_favorited") else ""
        print(f"**[{likes} likes]{pin}{fav} {c.get('author', '?')}**")
        print((c.get("text") or "").strip())
        for rep in sorted(replies.get(c.get("id", ""), []),
                          key=lambda r: -(r.get("like_count") or 0)):
            rl = rep.get("like_count") or 0
            print(f"\n  > **[{rl}] {rep.get('author', '?')}:** "
                  + (rep.get("text") or "").strip().replace("\n", " "))
        print()

    if not shown:
        print(f"_No comment reached --min-likes {a.min_likes}._")


if __name__ == "__main__":
    main()
