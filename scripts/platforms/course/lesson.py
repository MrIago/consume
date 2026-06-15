#!/usr/bin/env python3
"""Course platforms with a Panda Video / converteai player — consume a lesson.

Many course platforms (cademi, hotmart-hosted, members areas) embed their video
through a **Panda Video** player served by converteai. The player is a
cross-origin iframe like:

    https://scripts.converteai.net/<accountId>/players/<playerId>/v4/embed.html

and the actual stream is on a **public CDN** (no course login needed):

    https://cdn.converteai.net/<accountId>/<videoId>/main.m3u8

The catch: the course page itself is usually **IP-bound** (the session cookie is
tied to the logged-in device, so cloud/headless browsers with exported cookies
just bounce to the login page). So the iframe src has to be read from the user's
*real logged-in browser* — that step is done by the orchestrator with the
Claude-in-Chrome extension, NOT by this script:

    // in the logged-in tab on the lesson page:
    [...document.querySelectorAll('iframe')].map(f => f.src)

Then pass that converteai iframe URL (or a direct .m3u8) to this script. From the
embed it resolves the videoId, pulls the stream straight from the CDN with ffmpeg
(only a `Referer` header is needed — no cookies), extracts audio, and transcribes
via the shared core (Groq/OpenAI/local).

    python3 lesson.py "<converteai-embed-url-or-m3u8>"               # info + duration
    python3 lesson.py "<url>" --transcribe                            # the transcript
    python3 lesson.py "<url>" --transcribe --title "1. Introdução"    # labelled
    python3 lesson.py "<url1>" "<url2>" "<url3>" --transcribe         # batch
    python3 lesson.py "<url>" --frames 30,120                         # see the screen

Self-contained on purpose (DRY with the rest comes later, empirically), except
transcription which already shares scripts/lib/transcribe.py.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
import config  # noqa: E402  — reads env var OR ~/.config/consume/.env

# converteai's CDN checks the Referer; this value is enough (no login/cookies).
REFERER = config.get("WATCH_CONVERTEAI_REFERER", "https://scripts.converteai.net/")
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

ACCOUNT_RE = re.compile(r"converteai\.net/([0-9a-f-]{36})/", re.I)
VIDEO_RE = re.compile(r"/([0-9a-f]{24})/(?:poster\.jpg|main\.m3u8|playlist|hls)", re.I)
ANY_24HEX_RE = re.compile(r"\b([0-9a-f]{24})\b", re.I)
M3U8_RE = re.compile(r"https://cdn\.converteai\.net/[0-9a-f-]{36}/[0-9a-f]{24}/[^\"'\s]*\.m3u8", re.I)


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


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Referer": REFERER})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def resolve_m3u8(url: str) -> str:
    """Turn an input URL into a converteai main.m3u8.

    Accepts: a direct .m3u8 (returned as-is), a converteai embed URL, or any
    converteai URL containing the accountId — fetches the embed and extracts the
    videoId (the 24-hex id that appears in poster.jpg / the player config)."""
    if ".m3u8" in url:
        return url

    account = None
    m = ACCOUNT_RE.search(url)
    if m:
        account = m.group(1)

    # The embed page is public CDN — fetch it and look for the stream / videoId.
    try:
        html = _http_get(url)
    except Exception as exc:
        raise SystemExit(f"could not fetch the converteai embed ({exc}). "
                         f"Pass the iframe src from the logged-in browser, or a direct .m3u8 URL.")

    direct = M3U8_RE.search(html)
    if direct:
        return direct.group(0)

    if not account:
        m = ACCOUNT_RE.search(html)
        account = m.group(1) if m else None

    video = None
    m = VIDEO_RE.search(html)          # videoId next to poster.jpg / main.m3u8
    if m:
        video = m.group(1)
    if not video:                       # fallback: first 24-hex that isn't the playerId
        player = None
        pm = re.search(r"/players/([0-9a-f]{24})/", url + " " + html, re.I)
        if pm:
            player = pm.group(1)
        for cand in ANY_24HEX_RE.findall(html):
            if cand.lower() != (player or "").lower():
                video = cand
                break

    if not account or not video:
        raise SystemExit("could not resolve the converteai videoId from the embed. "
                         "Pass a direct cdn.converteai.net/<account>/<video>/main.m3u8 URL.")
    return f"https://cdn.converteai.net/{account}/{video}/main.m3u8"


def download(m3u8: str, out_dir: Path, audio_only: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    headers = f"Referer: {REFERER}\r\nUser-Agent: {_UA}\r\n"
    if audio_only:
        out = out_dir / "audio.mp3"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-headers", headers, "-i", m3u8,
               "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(out)]
    else:
        out = out_dir / "video.mp4"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-headers", headers, "-i", m3u8, "-c", "copy", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        print(r.stderr[-600:], file=sys.stderr)
        raise SystemExit("ffmpeg could not download the stream (m3u8 expired or wrong Referer?).")
    return out


def duration_of(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


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


# Shared transcription core (Groq/OpenAI/local + chunking). See lib/transcribe.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from transcribe import transcribe, transcribe_many, format_transcript as fmt_transcript  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(prog="lesson", description="Consume a Panda Video / converteai course lesson.")
    ap.add_argument("urls", nargs="+", help="converteai iframe src (or direct .m3u8). Get the iframe src from the logged-in browser.")
    ap.add_argument("--transcribe", action="store_true", help="download audio + transcribe")
    ap.add_argument("--frames", help="comma-separated timestamps (e.g. 30,120)")
    ap.add_argument("--resolution", type=int, default=512)
    ap.add_argument("--title", help="label for the lesson in the output heading")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="watch-course-"))

    # BATCH transcribe: resolve each, download audio, transcribe together (the
    # core loads the model once / fires API calls in parallel).
    if args.transcribe and len(args.urls) > 1:
        audios = []
        for i, u in enumerate(args.urls):
            try:
                m3u8 = resolve_m3u8(u)
                d = work / f"lesson_{i:02d}"
                audios.append(download(m3u8, d, audio_only=True))
            except SystemExit as exc:
                print(f"[watch] lesson {i} failed: {exc}", file=sys.stderr)
                audios.append(None)
        ok = [a for a in audios if a]
        results = transcribe_many(ok) if ok else []
        it = iter(results)
        print("# Course lessons — transcripts\n")
        for i, (u, a) in enumerate(zip(args.urls, audios)):
            print(f"## Lesson {i + 1}{(' — ' + args.title) if args.title and len(args.urls) == 1 else ''}\n")
            if a is None:
                print("_(failed to download)_\n"); continue
            segs = next(it)
            print("```")
            print(fmt_transcript(segs) if segs else "(no speech detected)")
            print("```\n")
        print(f"_Work dir: `{work}` — delete when done._")
        return 0

    url = args.urls[0]
    m3u8 = resolve_m3u8(url)

    if args.frames:
        ts = [parse_time(t) for t in args.frames.split(",") if t.strip()]
        print(f"[watch] downloading lesson to grab {len(ts)} frame(s)…", file=sys.stderr)
        local = download(m3u8, work, audio_only=False)
        frames = extract_frames(str(local), work / "frames", ts, args.resolution)
        print(f"# {args.title or 'Course lesson'} — frames\n")
        print("**Read each frame path to see the screen at that moment.**\n")
        for f in frames:
            print(f"- `{f['path']}` (t={fmt_time(f['timestamp_seconds'])})")
        print(f"\n_Frames in: `{work}` — delete when done._")
        return 0

    if args.transcribe:
        print("[watch] downloading audio…", file=sys.stderr)
        audio = download(m3u8, work, audio_only=True)
        segs = transcribe(audio)
        print(f"# {args.title or 'Course lesson'} — transcript\n```")
        print(fmt_transcript(segs) if segs else "(no speech detected — the lesson may be silent.)")
        print("```")
        print(f"\n_Work dir: `{work}` — delete when done._")
        return 0

    # default: resolve + info, no heavy work
    local = download(m3u8, work, audio_only=False)
    dur = duration_of(local)
    print(f"# {args.title or 'Course lesson'}\n")
    print(f"- **Stream:** `{m3u8}`")
    print(f"- **Duration:** {fmt_time(dur)}")
    print("\nTo go deeper: `--transcribe` for the speech, `--frames 30,120` to see the screen.")
    print(f"\n_Work dir: `{work}` — delete when done._")
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
