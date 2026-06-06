#!/usr/bin/env python3
"""Twitter/X — consume a tweet's video: text, transcription, frames.

For tweets that contain a video. The tweet text comes along (it's the framing),
speech comes from local transcription (X videos have no captions), and frames
are seeked directly from the stream URL (no full download). All on demand.

    # default: tweet text + video duration hint
    python3 video.py "<tweet-url>"
    # transcribe the audio locally:
    python3 video.py "<tweet-url>" --transcribe
    # grab frames at timestamps (seek, no download):
    python3 video.py "<tweet-url>" --frames 3,8,15

Access: needs `curl_cffi` (impersonation). Self-contained on purpose.
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
MODEL_ID = os.environ.get(
    "WATCH_WHISPER_MODEL",
    os.environ.get("VOICEFLOW_MODEL_ID", "deepdml/faster-whisper-large-v3-turbo-ct2"),
)
DEVICE = os.environ.get("WATCH_WHISPER_DEVICE", "cuda")
COMPUTE = os.environ.get("WATCH_WHISPER_COMPUTE", "int8_float16")


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


def tweet_text(url: str) -> str:
    if shutil.which("gallery-dl") is None:
        return ""
    out = subprocess.run(["gallery-dl", "--no-download", "-j", "--", url],
                         capture_output=True, text=True).stdout
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return ""
    for el in data:
        if isinstance(el, list) and len(el) >= 3 and isinstance(el[-1], dict):
            if el[-1].get("content") is not None:
                return el[-1]["content"].strip()
    return ""


def video_url(url: str) -> str:
    """Direct stream URL for the tweet's video (for remote seek / yt-dlp)."""
    if shutil.which("gallery-dl") is not None:
        out = subprocess.run(["gallery-dl", "-g", "--", url], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if line.startswith("http") and (".mp4" in line or "video" in line):
                return line.strip()
    # fallback: yt-dlp can resolve it too
    if shutil.which("yt-dlp") is not None:
        out = subprocess.run(["yt-dlp", "-g", "--no-playlist", "--", url],
                             capture_output=True, text=True).stdout.strip().splitlines()
        if out and out[0].startswith("http"):
            return out[0]
    raise SystemExit("could not get the tweet's video URL (is there a video? curl_cffi installed?)")


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


def fetch_audio(url: str, out_dir: Path) -> Path:
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed")
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "-N", "8", "-f", "bestaudio/best", "-x", "--audio-format", "mp3",
           "--postprocessor-args", "-ar 16000 -ac 1 -b:a 64k", "--no-playlist",
           "-o", str(out_dir / "audio.%(ext)s"), "--", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    audio = out_dir / "audio.mp3"
    if not audio.exists():
        print(result.stderr, file=sys.stderr)
        raise SystemExit("could not download tweet audio")
    return audio


def transcribe(audio_path: Path) -> list[dict]:
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel(MODEL_ID, device=DEVICE, compute_type=COMPUTE)
    except Exception as exc:
        print(f"[watch] GPU load failed ({exc}); CPU fallback", file=sys.stderr)
        model = WhisperModel(MODEL_ID, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(Path(audio_path).resolve()), language=None,
                                      beam_size=1, vad_filter=True, condition_on_previous_text=False)
    out = [{"start": round(s.start, 2), "text": s.text.strip()} for s in segments if s.text.strip()]
    print(f"[watch] transcribed {len(out)} segments, lang={info.language}", file=sys.stderr)
    return out


def fmt_transcript(segs):
    return "\n".join(f"[{int(s['start'])//60:02d}:{int(s['start'])%60:02d}] {s['text']}" for s in segs)


def main() -> int:
    ap = argparse.ArgumentParser(prog="video", description="Consume a tweet's video on demand.")
    ap.add_argument("url", help="Tweet URL with a video")
    ap.add_argument("--transcribe", action="store_true", help="Transcribe the audio locally")
    ap.add_argument("--frames", help="Comma-separated timestamps (e.g. 3,8,15)")
    ap.add_argument("--resolution", type=int, default=512)
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="watch-tw-vid-"))

    if args.frames:
        ts = [parse_time(t) for t in args.frames.split(",") if t.strip()]
        print(f"[watch] seeking {len(ts)} frame(s) (no download)…", file=sys.stderr)
        src = video_url(args.url)
        frames = extract_frames(src, work / "frames", ts, args.resolution)
        print("# Tweet video — frames\n")
        print("**Read each frame path to see the screen at that moment.**\n")
        for f in frames:
            print(f"- `{f['path']}` (t={fmt_time(f['timestamp_seconds'])})")
        print(f"\n_Frames in: `{work}` — delete when done._")
        return 0

    if args.transcribe:
        print("[watch] downloading tweet audio…", file=sys.stderr)
        audio = fetch_audio(args.url, work)
        segs = transcribe(audio)
        print("# Tweet video — transcript\n")
        print("## Tweet text\n```")
        print(tweet_text(args.url) or "(no text)")
        print("```\n## Spoken (local transcription)\n```")
        if segs:
            print(fmt_transcript(segs))
        else:
            print("(no speech detected — many X videos are silent UI demos / "
                  "screen recordings. Use --frames to see the screen instead.)")
        print("```")
        print(f"\n_Work dir: `{work}` — delete when done._")
        return 0

    # default
    print("# Tweet video\n")
    print(f"- **URL:** {args.url}")
    print("\n## Tweet text\n```")
    print(tweet_text(args.url) or "(no text)")
    print("```")
    print("\nTo go deeper: `--transcribe` for the speech, `--frames 3,8` to see moments.")
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
