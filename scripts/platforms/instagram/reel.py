#!/usr/bin/env python3
"""Instagram — consume a reel (video): caption, thumbnail, transcription, frames.

A reel has no native captions, so speech comes from local transcription. Like
YouTube, frames are seeked directly from the stream URL (no full download). All
on demand — pick what the task needs:

    # default: caption + cover thumbnail (Read it) + duration
    python3 reel.py "<post-url>"

    # transcribe the audio locally (whole reel, or a slice / several slices):
    python3 reel.py "<post-url>" --transcribe
    python3 reel.py "<post-url>" --transcribe --segments 0:05-0:20,1:00-1:15

    # grab frames at specific timestamps (seek, no download):
    python3 reel.py "<post-url>" --frames 5,35,60

Cookies: WATCH_COOKIES_FROM_BROWSER (default "chrome:Profile 1").
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


# ---- time helpers ----------------------------------------------------------
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


# ---- gallery-dl: reel metadata (video_url, thumb, caption) -----------------
def reel_meta(url: str) -> dict:
    if shutil.which("gallery-dl") is None:
        raise SystemExit("gallery-dl is not installed (pip install gallery-dl)")
    cmd = ["gallery-dl", "--cookies-from-browser", COOKIES, "--no-download", "-j", "--", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(result.stderr, file=sys.stderr)
        raise SystemExit("could not read reel (login/rate-limit? check cookies)")
    for el in data:
        if isinstance(el, list) and len(el) >= 3 and isinstance(el[-1], dict):
            m = el[-1]
            if m.get("video_url"):
                return {
                    "video_url": m["video_url"],
                    "thumb_url": m.get("display_url"),
                    "caption": (m.get("description") or "").strip(),
                    "likes": m.get("likes"),
                    "duration": m.get("audio_duration"),
                }
    raise SystemExit("no video found in this post (is it a reel?)")


def fetch_thumb(thumb_url: str, out_dir: Path) -> Path | None:
    if not thumb_url:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "thumb.jpg"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", thumb_url, str(out)]
    subprocess.run(cmd, capture_output=True, text=True)
    return out if out.exists() else None


# ---- frames via remote seek (no download) ----------------------------------
def _seek_one(src: str, out_path: Path, ts: float, resolution: int) -> dict | None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-ss", f"{ts:.3f}", "-i", src, "-frames:v", "1",
           "-vf", f"scale={resolution}:-2", "-q:v", "4", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not out_path.exists():
        print(f"[watch] frame at {ts:.1f}s failed: {r.stderr.strip()[:100]}", file=sys.stderr)
        return None
    return {"timestamp_seconds": round(ts, 2), "path": str(out_path)}


def extract_frames(video_url: str, out_dir: Path, timestamps: list[float], resolution: int = 512) -> list[dict]:
    import concurrent.futures
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_sorted = sorted(timestamps)
    results: dict[int, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(ts_sorted) or 1)) as ex:
        futs = {ex.submit(_seek_one, video_url, out_dir / f"frame_{i:04d}.jpg", ts, resolution): i
                for i, ts in enumerate(ts_sorted)}
        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            if r:
                results[futs[fut]] = r
    return [results[i] for i in sorted(results)]


# ---- transcription (download audio, local faster-whisper) ------------------
def fetch_audio(url: str, out_dir: Path) -> Path:
    """Download just the reel's audio as mono 16k mp3 via yt-dlp + cookies."""
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed")
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "-N", "8", "-f", "bestaudio/best", "-x", "--audio-format", "mp3",
           "--postprocessor-args", "-ar 16000 -ac 1 -b:a 64k", "--no-playlist",
           "--cookies-from-browser", COOKIES,
           "-o", str(out_dir / "audio.%(ext)s"), "--", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    audio = out_dir / "audio.mp3"
    if not audio.exists():
        print(result.stderr, file=sys.stderr)
        raise SystemExit("could not download reel audio (login/rate-limit?)")
    return audio


# Transcription is shared across platforms — use the core (Groq/OpenAI/local +
# chunking; batch reuses one local model or parallelizes API calls). See
# scripts/lib/transcribe.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from transcribe import transcribe, transcribe_many, format_transcript as fmt_transcript  # noqa: E402


def _transcribe_batch(urls: list[str], work: Path) -> int:
    """Transcribe several reels with the model loaded ONCE.

    This is the on-demand batch path for analyzing a profile: pass many reel
    URLs and the heavy model load happens a single time, with audio downloads
    running in parallel. Far faster than invoking the script per reel.
    """
    import concurrent.futures

    # Audio download is I/O-bound (yt-dlp) → parallel. Fetch caption too.
    print(f"[watch] downloading audio for {len(urls)} reel(s)…", file=sys.stderr)

    def dl(idx_url):
        idx, url = idx_url
        d = work / f"r{idx}"
        try:
            audio = fetch_audio(url, d)
            cap = reel_meta(url).get("caption", "")
            return idx, url, audio, cap, None
        except SystemExit as exc:
            return idx, url, None, "", str(exc)

    fetched: list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(urls))) as ex:
        for res in ex.map(dl, list(enumerate(urls))):
            fetched.append(res)
    fetched.sort()

    # Transcribe all successfully-fetched reels via the core (one local model
    # reused, or parallel API calls). Map results back by index.
    ok = [(idx, audio) for idx, _u, audio, _c, err in fetched if audio and not err]
    print(f"[watch] transcribing {len(ok)} reel(s)…", file=sys.stderr)
    segs_by_idx = {}
    if ok:
        many = transcribe_many([a for _i, a in ok])
        segs_by_idx = {idx: segs for (idx, _a), segs in zip(ok, many)}

    print(f"# Instagram reels — {len(urls)} transcripts\n")
    for idx, url, audio, cap, err in fetched:
        print(f"## Reel {idx + 1}: {url}\n")
        if err:
            print(f"_(skipped — {err})_\n")
            continue
        if cap:
            print(f"**Caption:** {cap}\n")
        segs = segs_by_idx.get(idx, [])
        print("```")
        print(fmt_transcript(segs) if segs else "(no speech — likely a silent/visual reel; use --frames)")
        print("```\n")
    print(f"_Work dir: `{work}` — delete when done._")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="reel", description="Consume one or more Instagram reels on demand.")
    ap.add_argument("url", nargs="+", help="Reel/post URL(s). Pass several to batch-transcribe.")
    ap.add_argument("--transcribe", action="store_true", help="Transcribe the audio locally")
    ap.add_argument("--frames", help="Comma-separated timestamps to grab (e.g. 5,35,60)")
    ap.add_argument("--resolution", type=int, default=512, help="Frame width (default 512)")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="watch-ig-reel-"))

    # --- batch transcribe: several reels, model loaded once ---
    if len(args.url) > 1:
        if not args.transcribe:
            print("[watch] multiple URLs given — batch-transcribing them.", file=sys.stderr)
        return _transcribe_batch(args.url, work)

    url = args.url[0]
    print(f"[watch] reading reel (cookies={COOKIES})…", file=sys.stderr)
    meta = reel_meta(url)

    # --- frames mode ---
    if args.frames:
        ts = [parse_time(t) for t in args.frames.split(",") if t.strip()]
        print(f"[watch] seeking {len(ts)} frame(s) (no download)…", file=sys.stderr)
        frames = extract_frames(meta["video_url"], work / "frames", ts, args.resolution)
        print("# Instagram reel — frames\n")
        print("**Read each frame path to see the screen at that moment.**\n")
        for f in frames:
            print(f"- `{f['path']}` (t={fmt_time(f['timestamp_seconds'])})")
        print(f"\n_Frames in: `{work}` — delete when done._")
        return 0

    # --- transcribe mode (single reel) ---
    if args.transcribe:
        print("[watch] downloading reel audio…", file=sys.stderr)
        audio = fetch_audio(url, work)
        segs = transcribe(audio)
        print("# Instagram reel — transcript\n")
        print("## Caption\n```")
        print(meta["caption"] or "(no caption)")
        print("```\n## Spoken (local transcription)\n```")
        print(fmt_transcript(segs) if segs else "(no speech)")
        print("```")
        print(f"\n_Work dir: `{work}` — delete when done._")
        return 0

    # --- default: caption + thumbnail + duration ---
    thumb = fetch_thumb(meta["thumb_url"], work)
    print("# Instagram reel\n")
    print(f"- **URL:** {url}")
    if meta.get("likes") is not None:
        print(f"- **Likes:** {meta['likes']}")
    if meta.get("duration"):
        print(f"- **Duration:** {fmt_time(meta['duration'])}")
    if thumb:
        print(f"- **Cover thumbnail:** `{thumb}` — Read it to see the cover frame that sells the click.")
    print("\n## Caption\n```")
    print(meta["caption"] or "(no caption)")
    print("```")
    print("\nTo go deeper: `--transcribe` for the speech, `--frames 5,35` to see specific moments.")
    print(f"\n_Work dir: `{work}` — delete when done._")
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
