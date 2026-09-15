#!/usr/bin/env python3
"""TikTok: comments on a video or photo slideshow.

yt-dlp reports comment_count but extracts zero comments (its TikTok extractor
never implemented the fetch). So this goes to the same internal endpoint the
web player uses, /api/comment/list/, authenticated with the browser session:
the msToken cookie is what unlocks it, so login cookies are required even
though the rest of the TikTok scripts here work without them.

The comment section is a second, independent source on the same subject: the
fix for a friction the video never solved, the viewer who tried it and failed,
a correction, and the question everybody has (visible as the top-liked one).

Usage:
    python3 comments.py <tiktok-url> [--limit 50] [--min-likes 100]

Self-contained on purpose, matching the other scripts in this platform dir.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

DEFAULT_BROWSER = "chrome:Profile 1"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
ID_RE = re.compile(r"/(?:video|photo)/(\d+)")


def fail(msg: str, code: int = 1):
    print(f"[watch] {msg}", file=sys.stderr)
    sys.exit(code)


def export_cookies(url: str, browser: str) -> str:
    """yt-dlp is the portable way to read the browser cookie jar."""
    if not shutil.which("yt-dlp"):
        fail("yt-dlp not found. Install with: pip install --upgrade --break-system-packages yt-dlp")
    path = tempfile.mktemp(suffix=".txt")
    subprocess.run(
        ["yt-dlp", "--no-update", "--no-warnings", "--cookies-from-browser", browser,
         "--cookies", path, "--skip-download", "--print", "id", url],
        capture_output=True, text=True, timeout=300,
    )
    if not os.path.exists(path):
        fail(f"could not read cookies from {browser}. Set WATCH_COOKIES_FROM_BROWSER to a "
             f"browser where you are logged into TikTok. On Linux, Chrome cookies also "
             f"need: pip install secretstorage")
    return path


def fetch_page(opener, aweme: str, url: str, cursor: int, ms_token: str | None) -> dict:
    q = {
        "aweme_id": aweme, "count": "20", "cursor": str(cursor),
        "aid": "1988", "app_name": "tiktok_web", "device_platform": "web_pc",
        "region": "BR", "os": "linux", "cookie_enabled": "true",
        "screen_width": "1920", "screen_height": "1080",
        "browser_language": "pt-BR", "browser_platform": "Linux x86_64",
        "browser_name": "Mozilla", "browser_version": "5.0",
        "browser_online": "true", "channel": "tiktok_web",
    }
    if ms_token:
        q["msToken"] = ms_token
    req = urllib.request.Request(
        "https://www.tiktok.com/api/comment/list/?" + urllib.parse.urlencode(q),
        headers={"User-Agent": UA, "Referer": url,
                 "Accept": "application/json, text/plain, */*"},
    )
    raw = opener.open(req, timeout=30).read().decode()
    if not raw.strip():
        fail("TikTok returned an empty response. The session cookie is probably stale: "
             "open tiktok.com in the browser, then retry.")
    return json.loads(raw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--min-likes", type=int, default=0)
    a = ap.parse_args()

    m = ID_RE.search(a.url)
    if not m:
        fail("could not find the video id in the URL (expected .../video/<id>)")
    aweme = m.group(1)

    browser = os.environ.get("WATCH_COOKIES_FROM_BROWSER", DEFAULT_BROWSER)
    print(f"[watch] fetching TikTok comments (cookies={browser})...", file=sys.stderr)

    cf = export_cookies(a.url, browser)
    jar = http.cookiejar.MozillaCookieJar(cf)
    jar.load(ignore_discard=True, ignore_expires=True)
    ms_token = next((c.value for c in jar if c.name == "msToken"), None)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    collected, cursor, total = [], 0, None
    while len(collected) < a.limit:
        data = fetch_page(opener, aweme, a.url, cursor, ms_token)
        batch = data.get("comments") or []
        if total is None:
            total = data.get("total")
        if not batch:
            break
        collected.extend(batch)
        if not data.get("has_more"):
            break
        cursor = data.get("cursor") or (cursor + len(batch))
    os.unlink(cf)

    if not collected:
        print("NO_COMMENTS")
        return

    collected = collected[:a.limit]
    collected.sort(key=lambda c: -(c.get("digg_count") or 0))

    print(f"\n# Comments on {a.url}")
    print(f"\n_{len(collected)} fetched, {total} total on the video._\n")

    shown = 0
    for c in collected:
        likes = c.get("digg_count") or 0
        if likes < a.min_likes:
            continue
        shown += 1
        user = (c.get("user") or {}).get("unique_id") or "?"
        # author_pin / is_author_digged are the platform's own signal that the
        # creator engaged with the comment: highest-value lines in the section.
        mark = ""
        if c.get("author_pin"):
            mark += " 📌pinned"
        if c.get("is_author_digged"):
            mark += " ❤️author"
        print(f"**[{likes} likes]{mark} {user}**")
        print((c.get("text") or "").strip())
        rc = c.get("reply_comment") or []
        for r in rc[:3]:
            ru = (r.get("user") or {}).get("unique_id") or "?"
            print(f"\n  > **[{r.get('digg_count', 0)}] {ru}:** "
                  + (r.get("text") or "").strip().replace("\n", " "))
        print()

    if not shown:
        print(f"_No comment reached --min-likes {a.min_likes}._")


if __name__ == "__main__":
    main()
