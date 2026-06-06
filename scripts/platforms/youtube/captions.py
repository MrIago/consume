#!/usr/bin/env python3
"""YouTube — Level 0: native captions only, without downloading the video.

This is the cheapest step of the cascade. yt-dlp pulls the subtitle track
(manual first, then auto-generated) in ~3s and never touches the video stream.
If the video has usable captions, the model can often answer from the
transcript alone — no audio download, no frames, no GPU.

Usage:
    python3 captions.py <youtube-url>

Prints a timestamped transcript to stdout, or "NO_CAPTIONS" if the video has
no subtitle track (caller should fall through to transcribe.py — Level 1).
Self-contained on purpose: no shared core yet (DRY comes later, empirically).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# English + Portuguese, manual and auto. "*-orig" catches the original-language
# auto track when the UI language differs.
SUB_LANGS = "en.*,pt.*,en-orig,pt-orig"

TS_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")
# Inline per-word timing tag, e.g. <00:00:02.240> — marks the line as the NEW
# speech of a cue (vs an echo of the previous line that YouTube repeats).
TIMING_RE = re.compile(r"<\d{2}:\d{2}:\d{2}[.,]\d{3}>")


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(path: str) -> list[dict]:
    """Parse a WebVTT file into clean {start, end, text} segments.

    YouTube auto-subs scroll: every cue repeats the previous line as plain text
    and adds the new line carrying inline timing tags (<00:00:02.240>). Keeping
    only the timing-tagged lines yields each phrase exactly once. Files without
    any timing tags (manual subs) fall back to plain de-duplication.
    """
    raw = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = raw.splitlines()
    has_timing = bool(TIMING_RE.search(raw))

    segments: list[dict] = []
    recent: set[str] = set()  # plain lines already seen, to skip scroll echoes
    i = 0
    while i < len(lines):
        match = TS_RE.match(lines[i])
        if not match:
            i += 1
            continue
        start = _to_seconds(*match.groups()[:4])
        end = _to_seconds(*match.groups()[4:])
        i += 1
        cue_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            line = lines[i]
            cleaned = TAG_RE.sub("", line).strip()
            if cleaned:
                if not has_timing or TIMING_RE.search(line):
                    # New speech of this cue (timing-tagged), always keep.
                    cue_lines.append(cleaned)
                elif cleaned not in recent:
                    # Plain line that's not an echo — e.g. the opening cue that
                    # has no timing tag yet. Keep it once.
                    cue_lines.append(cleaned)
                recent.add(cleaned)
            i += 1
        text = " ".join(cue_lines).strip()
        if text:
            segments.append({"start": round(start, 2), "end": round(end, 2), "text": text})
        i += 1
    return _dedupe(segments)


def _dedupe(segments: list[dict]) -> list[dict]:
    """Collapse any leftover exact/prefix repeats (manual-sub fallback path)."""
    out: list[dict] = []
    for seg in segments:
        if out and seg["text"] == out[-1]["text"]:
            out[-1]["end"] = seg["end"]
            continue
        if out and seg["text"].startswith(out[-1]["text"] + " "):
            out[-1]["text"] = seg["text"]
            out[-1]["end"] = seg["end"]
            continue
        out.append(seg)
    return out


def format_transcript(segments: list[dict], group_seconds: float = 12.0) -> str:
    """Render as [MM:SS] lines, grouping short cues into ~group_seconds blocks.

    YouTube cues are one short phrase each (one per breath); grouping them keeps
    the transcript readable and token-light while preserving a timestamp per
    block so the model can still locate a moment.
    """
    lines: list[str] = []
    buf: list[str] = []
    block_start: float | None = None
    for seg in segments:
        if block_start is None:
            block_start = seg["start"]
        buf.append(seg["text"])
        if seg["end"] - block_start >= group_seconds:
            s = int(block_start)
            lines.append(f"[{s // 60:02d}:{s % 60:02d}] {' '.join(buf)}")
            buf, block_start = [], None
    if buf and block_start is not None:
        s = int(block_start)
        lines.append(f"[{s // 60:02d}:{s % 60:02d}] {' '.join(buf)}")
    return "\n".join(lines)


def _pick_subtitle(out_dir: Path) -> Path | None:
    candidates = sorted(out_dir.glob("video*.vtt"))
    if not candidates:
        return None
    # Prefer English, then anything available.
    preferred = [c for c in candidates if ".en" in c.name]
    return preferred[0] if preferred else candidates[0]


def fetch_captions(url: str, out_dir: Path) -> Path | None:
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed (brew install yt-dlp / pipx install yt-dlp)")
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", SUB_LANGS,
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "--ignore-errors",
        "-o", str(out_dir / "video.%(ext)s"),
        "--", url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return _pick_subtitle(out_dir)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: captions.py <youtube-url>", file=sys.stderr)
        return 2
    url = sys.argv[1]
    out_dir = Path(tempfile.mkdtemp(prefix="watch-yt-cap-"))
    print(f"[watch] fetching YouTube captions (no video download)…", file=sys.stderr)
    sub = fetch_captions(url, out_dir)
    if not sub:
        print("NO_CAPTIONS", file=sys.stderr)
        print("NO_CAPTIONS")
        return 0
    segments = parse_vtt(str(sub))
    if not segments:
        print("NO_CAPTIONS")
        return 0
    print(f"[watch] {len(segments)} caption segments", file=sys.stderr)
    print(format_transcript(segments))
    return 0


if __name__ == "__main__":
    # Exit quietly if our stdout pipe is closed early (e.g. `| head`), instead
    # of dumping a BrokenPipeError traceback.
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass  # SIGPIPE not available (e.g. Windows)
    raise SystemExit(main())
