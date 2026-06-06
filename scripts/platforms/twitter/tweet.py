#!/usr/bin/env python3
"""Twitter/X — consume one tweet: text, metrics, images, and context.

On X a "post" can be a plain tweet, a tweet with up to 4 images, a tweet with a
video, a reply (needs the parent to make sense), a quote (embeds another tweet),
or the head of a thread (the author continues in self-replies). The text is
always central and always available.

By default this reads the single tweet (text + metrics + images). When the tweet
is a reply/quote, or you need the whole thread to follow it, pass --thread to
pull the conversation (needs login cookies).

Access: X needs `curl_cffi` (impersonation) installed. Threads/replies also need
login cookies via WATCH_COOKIES_FROM_BROWSER (default "chrome:Profile 1").

Usage:
    python3 tweet.py "https://x.com/<user>/status/<id>"
    python3 tweet.py "<url>" --thread        # include the conversation/thread
    python3 tweet.py "<url>" --no-download    # text + metrics only, skip images

Videos are not transcribed here — use video.py. Self-contained on purpose.
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

COOKIES = os.environ.get("WATCH_COOKIES_FROM_BROWSER", "chrome:Profile 1")
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _need_gallery_dl():
    if shutil.which("gallery-dl") is None:
        raise SystemExit("gallery-dl is not installed (pip install gallery-dl)")


def _run_json(url: str, thread: bool) -> list:
    _need_gallery_dl()
    cmd = ["gallery-dl", "--no-download", "-j"]
    if thread:
        cmd += ["--cookies-from-browser", COOKIES, "-o", "conversations=true"]
    cmd += ["--", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        low = (result.stderr or "").lower()
        print(result.stderr, file=sys.stderr)
        if "impersonat" in low:
            print("[watch] X needs impersonation — `pip install curl_cffi`.", file=sys.stderr)
        elif "login" in low or "403" in low or "empty" in low:
            print("[watch] X needs login for this — set WATCH_COOKIES_FROM_BROWSER to a "
                  "logged-in browser (default chrome:Profile 1).", file=sys.stderr)
        raise SystemExit("could not read tweet")


def _tweets(data: list) -> list[dict]:
    """Distinct tweets in order (gallery-dl repeats a tweet per media item)."""
    out, seen = [], set()
    for el in data:
        if isinstance(el, list) and len(el) >= 3 and isinstance(el[-1], dict):
            m = el[-1]
            tid = m.get("tweet_id")
            if tid and m.get("content") is not None and tid not in seen:
                seen.add(tid)
                out.append(m)
    return out


def _fmt_metrics(m: dict) -> str:
    parts = []
    for label, key in [("views", "view_count"), ("likes", "favorite_count"),
                       ("retweets", "retweet_count"), ("replies", "reply_count"),
                       ("quotes", "quote_count"), ("bookmarks", "bookmark_count")]:
        if m.get(key) is not None:
            parts.append(f"{m[key]} {label}")
    return " · ".join(parts)


def _print_tweet(m: dict, prefix: str = "") -> None:
    author = m.get("author", {}) or {}
    handle = author.get("nick") or author.get("name") or m.get("user", {}).get("name", "?")
    print(f"{prefix}**@{handle}** · {str(m.get('date',''))[:16]}")
    ctx = []
    if m.get("reply_id"):
        ctx.append(f"reply to tweet {m['reply_id']}")
    if m.get("quote_id"):
        ctx.append(f"quotes tweet {m['quote_id']}")
    if ctx:
        print(f"{prefix}_({'; '.join(ctx)})_")
    print(f"{prefix}{m.get('content') or '(no text)'}")
    met = _fmt_metrics(m)
    if met:
        print(f"{prefix}_{met}_")


def download_images(url: str, out_dir: Path) -> list[Path]:
    _need_gallery_dl()
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["gallery-dl", "-D", str(out_dir), "--", url]
    subprocess.run(cmd, capture_output=True, text=True)
    return sorted(p for p in out_dir.iterdir() if p.suffix.lower() in IMG_EXTS)


def main() -> int:
    ap = argparse.ArgumentParser(prog="tweet", description="Consume one tweet (text + metrics + images + context).")
    ap.add_argument("url", help="Tweet URL, e.g. https://x.com/user/status/123")
    ap.add_argument("--thread", action="store_true",
                    help="Also pull the conversation/thread (needs login cookies)")
    ap.add_argument("--no-download", action="store_true", help="Text + metrics only, skip images")
    args = ap.parse_args()

    print(f"[watch] reading tweet{' + thread' if args.thread else ''}…", file=sys.stderr)
    data = _run_json(args.url, thread=args.thread)
    tweets = _tweets(data)
    if not tweets:
        raise SystemExit("no tweet content found")

    main_tweet = tweets[0]
    has_video = any(t.get("type") == "video" for t in tweets)

    print("# Tweet\n")
    _print_tweet(main_tweet)

    # thread / conversation
    if args.thread and len(tweets) > 1:
        print("\n## Conversation / thread\n")
        for t in tweets[1:]:
            _print_tweet(t, prefix="> ")
            print(">")

    # images
    if not args.no_download and main_tweet.get("type") == "photo":
        print(f"\n[watch] downloading image(s)…", file=sys.stderr)
        out_dir = Path(tempfile.mkdtemp(prefix="watch-tw-"))
        imgs = download_images(args.url, out_dir)
        if imgs:
            print("\n## Images\n")
            print("**Read each path to see the image.**\n")
            for i, f in enumerate(imgs, 1):
                print(f"- image {i}: `{f}`")
            print(f"\n_Media in: `{out_dir}` — delete when done._")

    if has_video:
        print("\n## Video\n")
        print(f"This tweet has video. For speech/frames, run video.py on {args.url}")

    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
