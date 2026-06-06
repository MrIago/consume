#!/usr/bin/env python3
"""YouTube — Level 2: grab frames at specific timestamps, via remote seek.

Used when the transcript isn't enough and the model needs to *see* the screen
at certain moments. It identifies the timestamps it cares about (from the
transcript) and asks for just those frames.

Key trick: ffmpeg seeks directly to each timestamp on YouTube's stream URL
WITHOUT downloading the video. Seeking is ~167x faster than the old fps-filter
(which decoded the entire video to sample frames), and pulling a handful of
frames from a 2.5h video costs about the same as from a 1min clip.

Usage:
    python3 frames.py <youtube-url> --at 60,180,360 [--resolution 512]

Prints frame paths with absolute t=MM:SS timestamps. The model then Reads each
path to see the image.
Self-contained on purpose: no shared core yet (DRY comes later, empirically).
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def parse_time(value: str) -> float:
    """Parse SS, MM:SS, or HH:MM:SS into seconds."""
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
    raise SystemExit(f"Cannot parse time: {value!r} (expected SS, MM:SS, or HH:MM:SS)")


def format_time(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def stream_url(url: str) -> str:
    """Direct stream URL so ffmpeg can seek frames without downloading."""
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed (brew install yt-dlp / pipx install yt-dlp)")
    cmd = ["yt-dlp", "-g", "-f", "bv*[height<=720]/best[height<=720]/best",
           "--no-playlist", "--", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    lines = (result.stdout or "").strip().splitlines()
    if not lines or not lines[0].startswith("http"):
        print(result.stderr, file=sys.stderr)
        raise SystemExit("could not get a stream URL for this video")
    return lines[0]


def _seek_one(src: str, out_path: Path, ts: float, resolution: int) -> dict | None:
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{ts:.3f}", "-i", src,
        "-frames:v", "1", "-vf", f"scale={resolution}:-2", "-q:v", "4",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out_path.exists():
        print(f"[watch] frame at {ts:.1f}s failed: {result.stderr.strip()[:120]}", file=sys.stderr)
        return None
    return {"timestamp_seconds": round(ts, 2), "path": str(out_path)}


def extract_at(src: str, out_dir: Path, timestamps: list[float], resolution: int = 512) -> list[dict]:
    """One frame per timestamp via per-frame seek (no full decode).

    Each seek is independent, so they run in parallel — N frames take about as
    long as one. Capped at 8 concurrent ffmpeg processes to stay friendly.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed (brew install ffmpeg)")
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(timestamps)
    jobs = [(i, ts) for i, ts in enumerate(ordered)]

    import concurrent.futures
    results: dict[int, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(jobs) or 1)) as ex:
        futs = {
            ex.submit(_seek_one, src, out_dir / f"frame_{i:04d}.jpg", ts, resolution): i
            for i, ts in jobs
        }
        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            if r is not None:
                results[futs[fut]] = r
    # return in chronological order
    return [results[i] for i in sorted(results)]


def main() -> int:
    ap = argparse.ArgumentParser(prog="frames", description="Grab YouTube frames at timestamps via seek.")
    ap.add_argument("url", help="YouTube URL")
    ap.add_argument("--at", required=True, help="Comma-separated timestamps (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument("--resolution", type=int, default=512, help="Frame width in px (default 512)")
    args = ap.parse_args()

    timestamps = [parse_time(t) for t in args.at.split(",") if t.strip()]
    if not timestamps:
        print("no timestamps given (--at 60,180,...)", file=sys.stderr)
        return 2

    out_dir = Path(tempfile.mkdtemp(prefix="watch-yt-frames-"))
    print("[watch] resolving stream URL (no video download)…", file=sys.stderr)
    src = stream_url(args.url)
    print(f"[watch] seeking {len(timestamps)} frame(s)…", file=sys.stderr)
    frames = extract_at(src, out_dir, timestamps, resolution=args.resolution)

    print()
    print("## Frames")
    print()
    print("**Read each frame path below with the Read tool to view the image.** "
          "`t=MM:SS` is the absolute timestamp in the source video.")
    print()
    for f in frames:
        print(f"- `{f['path']}` (t={format_time(f['timestamp_seconds'])})")
    print()
    print(f"_Frames in: `{out_dir}` — delete when done._")
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
