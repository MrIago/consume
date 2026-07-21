#!/usr/bin/env python3
"""/consume — LOCAL files: transcribe (and see) audio/video already on disk.

Any media path works — meeting recordings (.mp4), voice notes (.ogg/.opus),
podcasts (.mp3), screen captures (.mkv/.webm), .wav, .m4a, …

Usage:
  file.py <path> [<path>...]                  # transcribe each file (parallel)
  file.py <path> --start 3:30 --end 5:00      # just one stretch
  file.py <path> --segments 3:30-5:00,1:02:00-1:04:00
  file.py <video> --frames 30,1:02:10 [--resolution 1024]   # SEE the screen
  file.py <path> --out <dir>                  # also save <name>.md per file

Backend comes from lib/transcribe (Groq preferred; WATCH_TRANSCRIBE overrides —
see SKILL.md). Timestamps print as [HH:MM:SS] since local files are often
hours long. Frames are extracted with local ffmpeg (instant, no network).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import transcribe as T  # noqa: E402

AUDIO_ONLY = {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".aac", ".wma"}


def parse_ts(v: str) -> float:
    parts = [float(p) for p in v.replace(",", ".").split(":")]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def hms(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def extract_audio(src: Path, tmp: Path, start: float | None, end: float | None) -> Path:
    """Extract mono 16k mp3 (small enough for API backends). Slices honor
    --start/--end so only the needed stretch is encoded/uploaded."""
    out = tmp / (src.stem + (f"_{int(start or 0)}" if start or end else "") + ".mp3")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start:
        cmd += ["-ss", f"{start:.2f}"]
    if end:
        cmd += ["-to", f"{end:.2f}"]
    cmd += ["-i", str(src), "-vn", "-ar", "16000", "-ac", "1", "-b:a", "64k", str(out)]
    subprocess.run(cmd, check=True)
    return out


def grab_frames(src: Path, tmp: Path, stamps: list[float], resolution: int) -> list[Path]:
    paths = []
    for t in stamps:
        out = tmp / f"{src.stem}_t{int(t)}.png"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
             "-vf", f"scale={resolution}:-2", str(out)],
            check=True)
        if out.exists():
            paths.append(out)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+", help="local audio/video paths")
    ap.add_argument("--start", type=parse_ts, default=None)
    ap.add_argument("--end", type=parse_ts, default=None)
    ap.add_argument("--segments", default=None, help="comma list of A-B stretches")
    ap.add_argument("--frames", default=None, help="comma list of timestamps to SEE")
    ap.add_argument("--resolution", type=int, default=768)
    ap.add_argument("--out", default=None, help="dir to also save <name>.md transcripts")
    args = ap.parse_args()

    srcs = [Path(f).expanduser() for f in args.files]
    missing = [str(s) for s in srcs if not s.exists()]
    if missing:
        print(f"NOT_FOUND: {', '.join(missing)}", file=sys.stderr)
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="consume-local-"))
    print(f"[watch] temp dir: {tmp}", file=sys.stderr)

    # frames mode — no transcription unless also asked
    if args.frames:
        stamps = [parse_ts(t) for t in args.frames.split(",") if t.strip()]
        for src in srcs:
            if src.suffix.lower() in AUDIO_ONLY:
                print(f"[watch] {src.name}: audio file, no frames to grab", file=sys.stderr)
                continue
            for p in grab_frames(src, tmp, stamps, args.resolution):
                print(f"FRAME {p}")
        return 0

    # transcription — build the audio jobs (whole file, one stretch, or N stretches)
    jobs: list[tuple[Path, float, Path]] = []  # (src, offset, audio)
    for src in srcs:
        if args.segments:
            for seg in args.segments.split(","):
                a, _, b = seg.partition("-")
                start, end = parse_ts(a), parse_ts(b)
                jobs.append((src, start, extract_audio(src, tmp, start, end)))
        else:
            start = args.start or 0.0
            jobs.append((src, start, extract_audio(src, tmp, args.start, args.end)))

    results = T.transcribe_many([audio for _src, _off, audio in jobs])

    by_src: dict[Path, list[dict]] = {}
    for (src, off, _audio), segs in zip(jobs, results):
        by_src.setdefault(src, []).extend(
            {**s, "start": s["start"] + off, "end": s["end"] + off} for s in segs)

    for src in srcs:
        segs = sorted(by_src.get(src, []), key=lambda s: s["start"])
        lines = [f"[{hms(s['start'])}] {s['text']}" for s in segs]
        print(f"\n===== {src.name} ({len(segs)} segments) =====")
        print("\n".join(lines) if lines else "(no speech detected)")
        if args.out:
            out_dir = Path(args.out).expanduser()
            out_dir.mkdir(parents=True, exist_ok=True)
            md = out_dir / f"{src.stem}.md"
            md.write_text(f"# Transcrição — {src.name}\n\n" + "\n".join(lines) + "\n",
                          encoding="utf-8")
            print(f"[watch] saved {md}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
