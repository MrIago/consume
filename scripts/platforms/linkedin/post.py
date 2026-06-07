#!/usr/bin/env python3
"""LinkedIn — consume an image/text post: text + images.

LinkedIn hides post content behind login and renders it with JavaScript, so
yt-dlp (which only does video) can't get image posts. But the authenticated HTML
page (fetched with your logged-in browser cookies) embeds the post text and the
full-res `feedshare` image URLs — we extract those.

For VIDEO posts, use video.py (yt-dlp handles those, even without login).

Cookies: WATCH_COOKIES_FROM_BROWSER (default "chrome:Profile 1"), from a browser
logged into LinkedIn.

Usage:
    python3 post.py "https://www.linkedin.com/posts/...<id>.../"
    python3 post.py "<url>" --no-download     # text only, list image URLs

Self-contained on purpose: no shared core yet (DRY comes later, empirically).
"""
from __future__ import annotations

import argparse
import http.cookiejar
import os
import re
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
    ckfile = Path(tempfile.gettempdir()) / "watch-linkedin-cookies.txt"
    subprocess.run(["yt-dlp", "--cookies-from-browser", COOKIES, "--cookies", str(ckfile),
                    "--skip-download", "--simulate", "https://www.linkedin.com"],
                   capture_output=True)
    if not ckfile.exists():
        raise SystemExit("could not export browser cookies (logged into LinkedIn? yt-dlp installed?)")
    cj = http.cookiejar.MozillaCookieJar(str(ckfile))
    cj.load(ignore_discard=True, ignore_expires=True)
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA)]
    return op


def fetch_page(url: str) -> str:
    try:
        html = _opener().open(url, timeout=40).read().decode("utf-8", "ignore")
    except Exception as exc:
        print(f"[watch] {exc}", file=sys.stderr)
        raise SystemExit("could not load the LinkedIn page — is the cookie browser logged in?")
    if len(html) < 500_000:
        print("[watch] warning: page looks like a logged-out shell; text/images may be missing. "
              "Check WATCH_COOKIES_FROM_BROWSER is logged into LinkedIn.", file=sys.stderr)
    return html.replace("\\u0026", "&")


def extract_text(html: str) -> str:
    """Reconstruct the post text from the RSC children fragments LinkedIn embeds.

    The text is split into many `\"children\":[null,\"...\"]` pieces; join them in
    order. Imperfect (LinkedIn's internal format), but recovers the gist.
    """
    frags = re.findall(r'\\"children\\":\[null,\\"((?:[^"\\]|\\.)*?)\\"\]', html)
    parts = []
    for f in frags:
        t = (f.replace('\\\\n', '\n').replace('\\n', '\n')
              .replace('\\"', '"').replace('\\\\', '\\').strip())
        if t and t not in parts:
            parts.append(t)
    return "\n".join(parts).strip()


def extract_images(html: str) -> list[str]:
    """Full-res feedshare image URLs, in order, deduped."""
    urls = re.findall(r'https://media\.licdn\.com/dms/image/v2/\S+?feedshare-image-high-res\S+?t=[A-Za-z0-9_-]+', html)
    if not urls:  # fall back to largest shrink variant
        urls = re.findall(r'https://media\.licdn\.com/dms/image/v2/\S+?feedshare-shrink_\d+\S+?t=[A-Za-z0-9_-]+', html)
    return list(dict.fromkeys(urls))


def main() -> int:
    ap = argparse.ArgumentParser(prog="post", description="Consume a LinkedIn image/text post.")
    ap.add_argument("url", help="LinkedIn post URL")
    ap.add_argument("--no-download", action="store_true", help="Text only; list image URLs, don't download")
    args = ap.parse_args()

    print(f"[watch] reading LinkedIn post (cookies={COOKIES})…", file=sys.stderr)
    html = fetch_page(args.url)
    text = extract_text(html)
    imgs = extract_images(html)

    print("# LinkedIn post\n")
    print(f"- **URL:** {args.url}")
    print(f"- **Images:** {len(imgs)}")
    print("\n## Text\n```")
    print(text or "(could not extract post text — see images below / try video.py if it's a video)")
    print("```")

    if not imgs:
        print("\n_No images found. If this is a video post, use video.py instead._")
        return 0

    if args.no_download:
        print("\n## Image URLs\n")
        for i, u in enumerate(imgs, 1):
            print(f"- image {i}: {u}")
        return 0

    print(f"\n[watch] downloading {len(imgs)} image(s)…", file=sys.stderr)
    out_dir = Path(tempfile.mkdtemp(prefix="watch-li-"))
    op = _opener()
    files = []
    for i, u in enumerate(imgs):
        out = out_dir / f"image_{i:02d}.jpg"
        try:
            out.write_bytes(op.open(u, timeout=40).read())
            files.append(out)
        except Exception as exc:
            print(f"[watch] image {i} failed: {exc}", file=sys.stderr)
    if files:
        print("\n## Images\n**Read each path to see the image.**\n")
        for i, f in enumerate(files, 1):
            print(f"- image {i}: `{f}`")
        print(f"\n_Media in: `{out_dir}` — delete when done._")
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
