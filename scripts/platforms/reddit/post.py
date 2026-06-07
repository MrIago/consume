#!/usr/bin/env python3
"""Reddit — consume a post: title, body, score, comments, and media.

Reddit blocks anonymous scraping, but its native `.json` endpoint works when you
hit it with your logged-in browser cookies. That returns the whole thing
structured: the post (title + selftext + score) AND the comment tree — which on
Reddit is often the real value. Media (image / gallery / video) is detected from
the post and fetched on demand.

A post can be: text (selftext), a link, an image, a gallery of images, or a
video. The comment discussion comes along by default (trim with --top-comments).

Cookies: WATCH_COOKIES_FROM_BROWSER (default "chrome:Profile 1"), from a browser
logged into Reddit. Video uses video.py.

Usage:
    python3 post.py "https://www.reddit.com/r/<sub>/comments/<id>/<slug>/"
    python3 post.py "<url>" --top-comments 10      # limit comments shown
    python3 post.py "<url>" --no-download           # skip downloading images

Self-contained on purpose: no shared core yet (DRY comes later, empirically).
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
import config  # noqa: E402  — reads env var OR ~/.config/consume/.env
# Browser/profile for login cookies. Default "chrome" = Chrome's default profile
# (most common). Use "chrome:Profile 1" for a named profile, or "firefox"/"edge".
COOKIES = config.get("WATCH_COOKIES_FROM_BROWSER", "chrome")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def _opener():
    """Build a urllib opener carrying the browser's Reddit cookies."""
    ckfile = Path(tempfile.gettempdir()) / "watch-reddit-cookies.txt"
    subprocess.run(
        ["yt-dlp", "--cookies-from-browser", COOKIES, "--cookies", str(ckfile),
         "--skip-download", "--simulate", "https://www.reddit.com"],
        capture_output=True,
    )
    if not ckfile.exists():
        raise SystemExit("could not export browser cookies (is yt-dlp installed? "
                         "is WATCH_COOKIES_FROM_BROWSER a logged-in browser?)")
    cj = http.cookiejar.MozillaCookieJar(str(ckfile))
    cj.load(ignore_discard=True, ignore_expires=True)
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA)]
    return op


def fetch(url: str) -> tuple[dict, list]:
    """Return (post_data, comments) from Reddit's authenticated .json."""
    op = _opener()
    json_url = url.split("?")[0].rstrip("/") + "/.json"
    try:
        raw = op.open(json_url, timeout=30).read().decode()
        data = json.loads(raw)
    except Exception as exc:
        print(f"[watch] {exc}", file=sys.stderr)
        raise SystemExit("could not read Reddit post — check that the cookie browser "
                         "is logged into Reddit (login/rate-limit otherwise)")
    post = data[0]["data"]["children"][0]["data"]
    comments = data[1]["data"]["children"] if len(data) > 1 else []
    return post, comments


def classify(post: dict) -> str:
    if post.get("is_video"):
        return "video"
    if post.get("is_gallery"):
        n = len(post.get("gallery_data", {}).get("items", []))
        return f"gallery ({n} images)"
    if post.get("post_hint") == "image" or (post.get("url", "").rsplit(".", 1)[-1].lower()
                                            in ("jpg", "jpeg", "png", "gif", "webp")):
        return "image"
    if post.get("selftext"):
        return "text"
    return "link"


def _gallery_urls(post: dict) -> list[str]:
    urls = []
    meta = post.get("media_metadata") or {}
    for item in post.get("gallery_data", {}).get("items", []):
        mid = item.get("media_id")
        m = meta.get(mid, {})
        s = m.get("s", {})
        u = s.get("u") or s.get("gif")
        if u:
            urls.append(u.replace("&amp;", "&"))
    return urls


def download_images(post: dict, kind: str, out_dir: Path, op) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    urls = _gallery_urls(post) if kind.startswith("gallery") else [post.get("url", "")]
    files = []
    for i, u in enumerate(urls):
        if not u.startswith("http"):
            continue
        ext = u.split("?")[0].rsplit(".", 1)[-1].lower()
        if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
            ext = "jpg"
        out = out_dir / f"image_{i:02d}.{ext}"
        try:
            out.write_bytes(op.open(u, timeout=30).read())
            files.append(out)
        except Exception as exc:
            print(f"[watch] image {i} failed: {exc}", file=sys.stderr)
    return files


def _print_comments(comments: list, limit: int) -> None:
    shown = 0
    for c in comments:
        cd = c.get("data", {})
        body = (cd.get("body") or "").strip()
        if not body:
            continue
        print(f"- **[{cd.get('score', '?')}↑] u/{cd.get('author', '?')}:** {body}")
        # one level of replies, briefly
        replies = cd.get("replies")
        if isinstance(replies, dict):
            for r in replies.get("data", {}).get("children", [])[:2]:
                rd = r.get("data", {})
                rb = (rd.get("body") or "").strip()
                if rb:
                    print(f"  - [{rd.get('score','?')}↑] u/{rd.get('author','?')}: {rb[:200]}")
        shown += 1
        if shown >= limit:
            break


def main() -> int:
    ap = argparse.ArgumentParser(prog="post", description="Consume a Reddit post (text + comments + media).")
    ap.add_argument("url", help="Reddit post URL")
    ap.add_argument("--top-comments", type=int, default=8, help="How many top comments to show (default 8)")
    ap.add_argument("--no-download", action="store_true", help="Don't download images")
    args = ap.parse_args()

    print(f"[watch] reading Reddit post (cookies={COOKIES})…", file=sys.stderr)
    post, comments = fetch(args.url)
    kind = classify(post)

    print("# Reddit post\n")
    print(f"- **Title:** {post.get('title')}")
    print(f"- **r/{post.get('subreddit')}** · u/{post.get('author')} · "
          f"{post.get('score')} points ({int((post.get('upvote_ratio') or 0)*100)}% upvoted) · "
          f"{post.get('num_comments')} comments")
    print(f"- **Type:** {kind}")
    if post.get("selftext"):
        print("\n## Body\n```")
        print(post["selftext"])
        print("```")
    elif kind == "link":
        print(f"- **Link:** {post.get('url')}")

    # media
    is_image_post = kind == "image" or kind.startswith("gallery")
    if is_image_post and not args.no_download:
        print("\n[watch] downloading image(s)…", file=sys.stderr)
        out_dir = Path(tempfile.mkdtemp(prefix="watch-rd-"))
        imgs = download_images(post, kind, out_dir, _opener())
        if imgs:
            print("\n## Images\n**Read each path to see the image.**\n")
            for i, f in enumerate(imgs, 1):
                print(f"- image {i}: `{f}`")
            print(f"\n_Media in: `{out_dir}` — delete when done._")
    if kind == "video":
        print(f"\n## Video\nThis post is a video. For speech/frames run video.py on {args.url}")

    if comments:
        print(f"\n## Top comments\n")
        _print_comments(comments, args.top_comments)
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
