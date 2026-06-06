#!/usr/bin/env python3
"""LinkedIn — consume a video post: text, transcription, frames.

For LinkedIn posts that contain a video. yt-dlp handles these well even without
login: it pulls the post text (description) and the video. Speech via local
transcription (no captions); frames via remote seek on the stream URL.

    python3 video.py "<post-url>"               # text + hints
    python3 video.py "<post-url>" --transcribe
    python3 video.py "<post-url>" --frames 3,10,30

For image/text-only LinkedIn posts, use post.py instead.
Self-contained on purpose: no shared core yet (DRY comes later, empirically).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

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


def _need_ytdlp():
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed")


def post_text(url: str) -> str:
    _need_ytdlp()
    out = subprocess.run(["yt-dlp", "--skip-download", "--print", "%(description)s", "--", url],
                         capture_output=True, text=True).stdout.strip()
    return out


def video_url(url: str) -> str:
    _need_ytdlp()
    out = subprocess.run(["yt-dlp", "-g", "-f", "bv*[height<=720]/best", "--no-playlist", "--", url],
                         capture_output=True, text=True).stdout.strip().splitlines()
    if out and out[0].startswith("http"):
        return out[0]
    raise SystemExit("could not get the video URL (is there a video in this post?)")


def fetch_audio(url: str, out_dir: Path) -> Path:
    _need_ytdlp()
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "-N", "8", "-f", "bestaudio/best", "-x", "--audio-format", "mp3",
           "--postprocessor-args", "-ar 16000 -ac 1 -b:a 64k", "--no-playlist",
           "-o", str(out_dir / "audio.%(ext)s"), "--", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    audio = out_dir / "audio.mp3"
    if not audio.exists():
        print(result.stderr, file=sys.stderr)
        raise SystemExit("could not download audio")
    return audio


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
    ap = argparse.ArgumentParser(prog="video", description="Consume a LinkedIn video post on demand.")
    ap.add_argument("url", help="LinkedIn post URL with a video")
    ap.add_argument("--transcribe", action="store_true")
    ap.add_argument("--frames", help="Comma-separated timestamps (e.g. 3,10,30)")
    ap.add_argument("--resolution", type=int, default=512)
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="watch-li-vid-"))

    if args.frames:
        ts = [parse_time(t) for t in args.frames.split(",") if t.strip()]
        print(f"[watch] seeking {len(ts)} frame(s) (no download)…", file=sys.stderr)
        frames = extract_frames(video_url(args.url), work / "frames", ts, args.resolution)
        print("# LinkedIn video — frames\n")
        print("**Read each frame path to see the screen at that moment.**\n")
        for f in frames:
            print(f"- `{f['path']}` (t={fmt_time(f['timestamp_seconds'])})")
        print(f"\n_Frames in: `{work}` — delete when done._")
        return 0

    if args.transcribe:
        print("[watch] downloading audio…", file=sys.stderr)
        audio = fetch_audio(args.url, work)
        segs = transcribe(audio)
        print("# LinkedIn video — transcript\n")
        print("## Post text\n```")
        print(post_text(args.url) or "(no text)")
        print("```\n## Spoken (local transcription)\n```")
        if segs:
            print(fmt_transcript(segs))
        else:
            print("(no speech detected — the video may be silent. Use --frames to see it.)")
        print("```")
        print(f"\n_Work dir: `{work}` — delete when done._")
        return 0

    print("# LinkedIn video\n")
    print(f"- **URL:** {args.url}")
    print("\n## Post text\n```")
    print(post_text(args.url) or "(no text)")
    print("```")
    print("\nTo go deeper: `--transcribe` for the speech, `--frames 3,10` to see moments.")
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
