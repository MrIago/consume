#!/usr/bin/env python3
"""YouTube — Level 1: transcribe the audio with local faster-whisper.

Used when the video has no captions, or the captions weren't enough. Downloads
ONLY the audio (mono 16k mp3, ~0.5 MB/min) instead of the full video, then
transcribes on the GPU. Local transcription is free, keeps per-segment
timestamps (cloud STT APIs drop them), and has no upload size limit — so even a
2.5h video works.

Usage:
    python3 transcribe.py <youtube-url>

Prints a timestamped transcript to stdout.
Self-contained on purpose: no shared core yet (DRY comes later, empirically).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Same local model mr-whisper uses; fits a 4GB GPU at int8_float16 (~1.9GB).
MODEL_ID = os.environ.get(
    "WATCH_WHISPER_MODEL",
    os.environ.get("VOICEFLOW_MODEL_ID", "deepdml/faster-whisper-large-v3-turbo-ct2"),
)
DEVICE = os.environ.get("WATCH_WHISPER_DEVICE", "cuda")
COMPUTE = os.environ.get("WATCH_WHISPER_COMPUTE", "int8_float16")


def fetch_audio(url: str, out_dir: Path, start: float | None = None,
                end: float | None = None, name: str = "audio") -> Path:
    """Download only the audio as mono 16k mp3 (the shape Whisper wants).

    When start/end are given, download ONLY that slice (yt-dlp --download-sections)
    — this is the on-demand path: transcribe just the ambiguous stretch, not the
    whole video. `name` lets several slices coexist in the same dir.
    """
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed (brew install yt-dlp / pipx install yt-dlp)")
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "-N", "8",
        "-f", "bestaudio/best",
        "-x", "--audio-format", "mp3",
        "--postprocessor-args", "-ar 16000 -ac 1 -b:a 64k",
        "--no-playlist",
    ]
    if start is not None or end is not None:
        # yt-dlp section syntax: *START-END (seconds). Open-ended on either side.
        s = f"{start:.2f}" if start is not None else "0"
        e = f"{end:.2f}" if end is not None else "inf"
        cmd += ["--download-sections", f"*{s}-{e}"]
    cmd += ["-o", str(out_dir / f"{name}.%(ext)s"), "--", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    audio = out_dir / f"{name}.mp3"
    if not audio.exists():
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"yt-dlp did not produce audio (exit {result.returncode})")
    return audio


def _parse_segments(spec: str) -> list[tuple[float, float | None]]:
    """Parse '3:30-5:00,12:00-13:00' into [(210, 300), (720, 780)].
    An open end ('50:00-') means to the end of the video."""
    out: list[tuple[float, float | None]] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" not in chunk:
            raise SystemExit(f"segment {chunk!r} must be START-END (e.g. 3:30-5:00)")
        a, b = chunk.split("-", 1)
        out.append((parse_time(a), parse_time(b) if b.strip() else None))
    return out


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


def _load_model():
    """Load faster-whisper once, GPU with CPU fallback. The 4GB GPU fits one
    model — so we load a single model and reuse it across slices, rather than
    running several in parallel (which would OOM)."""
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel(MODEL_ID, device=DEVICE, compute_type=COMPUTE)
        return model, f"{DEVICE}/{COMPUTE}"
    except Exception as exc:
        print(f"[watch] GPU load failed ({exc}); falling back to CPU", file=sys.stderr)
        return WhisperModel(MODEL_ID, device="cpu", compute_type="int8"), "cpu/int8"


def _transcribe_one(model, audio_path: Path, offset: float = 0.0) -> list[dict]:
    """Transcribe one audio file with an already-loaded model. `offset` shifts
    timestamps to absolute video time so slices line up with captions/frames."""
    segments, info = model.transcribe(
        str(Path(audio_path).resolve()),
        language=None,            # auto-detect; pt/en code-switching just works
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    out: list[dict] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if text:
            out.append({
                "start": round(seg.start + offset, 2),
                "end": round(seg.end + offset, 2),
                "text": text,
            })
    print(f"[watch]   slice@{int(offset)}s: {len(out)} segments, lang={info.language}", file=sys.stderr)
    return out


def transcribe_local(audio_path: Path, offset: float = 0.0) -> list[dict]:
    """Convenience: load model + transcribe one file (whole-video path)."""
    model, used = _load_model()
    print(f"[watch] model loaded ({used})", file=sys.stderr)
    return _transcribe_one(model, audio_path, offset)


def format_transcript(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        start = int(seg["start"])
        lines.append(f"[{start // 60:02d}:{start % 60:02d}] {seg['text']}")
    return "\n".join(lines)


def main() -> int:
    import argparse
    import concurrent.futures
    ap = argparse.ArgumentParser(prog="transcribe",
        description="Transcribe YouTube audio locally — whole video, one slice, or several slices.")
    ap.add_argument("url", help="YouTube URL")
    ap.add_argument("--start", help="Single slice start (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument("--end", help="Single slice end")
    ap.add_argument("--segments", help="Several slices at once, e.g. '3:30-5:00,12:00-13:00'. "
                                       "Model loads once and transcribes them all (absolute timestamps).")
    args = ap.parse_args()

    out_dir = Path(tempfile.mkdtemp(prefix="watch-yt-tx-"))

    # Resolve the list of (start, end) slices to transcribe.
    if args.segments:
        slices = _parse_segments(args.segments)
    elif args.start or args.end:
        slices = [(parse_time(args.start) if args.start else 0.0,
                   parse_time(args.end) if args.end else None)]
    else:
        slices = [(0.0, None)]  # whole video

    # Download each slice's audio (I/O-bound → parallel is safe and helps).
    print(f"[watch] downloading {len(slices)} audio slice(s)…", file=sys.stderr)

    def dl(idx_slice):
        idx, (s, e) = idx_slice
        a = fetch_audio(url=args.url, out_dir=out_dir,
                        start=(s if s else None), end=e, name=f"a{idx}")
        return idx, s, a

    audios: list[tuple[int, float, Path]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(slices))) as ex:
        for idx, s, a in ex.map(dl, list(enumerate(slices))):
            audios.append((idx, s, a))
    audios.sort()

    # Load the model ONCE, transcribe every slice with it (GPU fits one model).
    model, used = _load_model()
    print(f"[watch] model loaded ({used}); transcribing {len(audios)} slice(s)…", file=sys.stderr)
    all_segs: list[dict] = []
    for _idx, s, a in audios:
        all_segs += _transcribe_one(model, a, offset=s or 0.0)

    all_segs.sort(key=lambda x: x["start"])
    if not all_segs:
        print("NO_SPEECH")
        return 0
    print(format_transcript(all_segs))
    return 0


if __name__ == "__main__":
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    raise SystemExit(main())
