#!/usr/bin/env python3
"""Shared transcription core for /consume — the first piece of empirical DRY.

Every platform needs the same thing: turn an audio file into timestamped
segments. This module is that one implementation, used by all platforms.

Backends, in order of preference (auto):
  1. Groq       — if GROQ_API_KEY set. whisper-large-v3-turbo, fast, generous
     free tier, keeps per-segment timestamps. 25 MB / request → long audio is
     chunked. ALWAYS preferred when its key exists.
  2. OpenAI     — if OPENAI_API_KEY set. whisper-1 (the only OpenAI model that
     returns timestamps via verbose_json). Also 25 MB / request → chunked.
  3. OpenRouter — if OPENROUTER_API_KEY set. Audio-capable chat model (default
     google/gemini-2.5-flash). NO per-segment timestamps — each chunk returns
     one block stamped with the chunk's start time. Coarse; fallback only.
  4. Local      — faster-whisper on the GPU (CPU fallback). Free, no size
     limit, keeps timestamps. Two modes: `local` = best quality
     (large-v3-turbo) and `local-fast` = quick-and-rough (whisper-small).

Override with WATCH_TRANSCRIBE = auto | groq | openai | openrouter | local |
local-fast (default auto: first backend whose key exists, else local).

API calls retry on 429/5xx with growing backoff — a rate limit must produce a
wait, never a silent hole in the transcript. Note Groq's retry-after header
lies (says 3s while the audio-seconds/hour budget is exhausted), hence the
attempt-scaled floor on the wait.

All backends return the same shape:
    [{"start": float, "end": float, "text": str}, ...]  (absolute seconds)
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config  # noqa: E402  — reads env vars OR ~/.config/consume/.env

# --- config (env var first, then ~/.config/consume/.env) ------------------
PREF = (config.get("WATCH_TRANSCRIBE", "auto") or "auto").lower()
GROQ_KEY = config.get("GROQ_API_KEY", "") or ""
OPENAI_KEY = config.get("OPENAI_API_KEY", "") or ""

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = config.get("WATCH_GROQ_MODEL", "whisper-large-v3-turbo")
OPENAI_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_MODEL = "whisper-1"  # only OpenAI model that returns timestamps

OPENROUTER_KEY = config.get("OPENROUTER_API_KEY", "") or ""
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = config.get("WATCH_OPENROUTER_MODEL", "google/gemini-2.5-flash")

# Local models (faster-whisper): `local` = best quality, `local-fast` = quick
LOCAL_MODEL_ID = config.get(
    "WATCH_WHISPER_MODEL",
    config.get("VOICEFLOW_MODEL_ID", "deepdml/faster-whisper-large-v3-turbo-ct2"),
)
LOCAL_FAST_MODEL_ID = config.get("WATCH_WHISPER_FAST_MODEL", "Systran/faster-whisper-small")
LOCAL_DEVICE = config.get("WATCH_WHISPER_DEVICE", "cuda")
LOCAL_COMPUTE = config.get("WATCH_WHISPER_COMPUTE", "int8_float16")

# API file limit is 25 MB; mono 16k 64kbps ≈ 0.31 MB/min ≈ 80 min. Chunk well
# under that to stay safe across bitrates/formats.
CHUNK_SECONDS = int(config.get("WATCH_CHUNK_SECONDS", "1200"))  # 20 min
# Each chunk starts OVERLAP seconds before the previous one ends, so a sentence
# straddling a boundary is captured whole in at least one chunk. Duplicate
# segments in the overlap are dropped by absolute timestamp on merge.
CHUNK_OVERLAP = int(os.environ.get("WATCH_CHUNK_OVERLAP", "10"))  # seconds


# --- backend selection ----------------------------------------------------
def choose_backend() -> str:
    """Resolve which backend to use from WATCH_TRANSCRIBE + available keys."""
    if PREF in ("groq", "openai", "openrouter", "local", "local-fast"):
        return PREF
    # auto: first backend with a key (plug-and-play for no-GPU users), else local.
    # Groq is ALWAYS preferred when its key exists — fastest + timestamps + free.
    if GROQ_KEY:
        return "groq"
    if OPENAI_KEY:
        return "openai"
    if OPENROUTER_KEY:
        return "openrouter"
    return "local"


# --- audio helpers --------------------------------------------------------
def _duration(audio_path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(audio_path)],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _slice_audio(audio_path: Path, start: float, dur: float, out: Path) -> Path:
    """Cut [start, start+dur) into its own mp3 (re-encode keeps it small)."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", str(audio_path),
         "-ar", "16000", "-ac", "1", "-b:a", "64k", str(out)],
        capture_output=True)
    return out


# --- HTTP (Groq / OpenAI) -------------------------------------------------
def _multipart(fields: dict, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----watch{uuid.uuid4().hex}"
    eol = b"\r\n"
    buf = bytearray()
    for k, v in fields.items():
        buf += f"--{boundary}".encode() + eol
        buf += f'Content-Disposition: form-data; name="{k}"'.encode() + eol + eol
        buf += str(v).encode() + eol
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    buf += f"--{boundary}".encode() + eol
    buf += f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode() + eol
    buf += f"Content-Type: {mime}".encode() + eol + eol
    buf += file_path.read_bytes() + eol
    buf += f"--{boundary}--".encode() + eol
    return bytes(buf), boundary


RETRIES = 6


def _post(endpoint: str, key: str, model: str, audio_path: Path) -> dict:
    """POST with retry/backoff on 429/5xx. The wait has an attempt-scaled floor
    because rate-limit headers can lie (Groq answers retry-after: 3s while its
    audio-seconds/hour budget is exhausted — retrying in 3s just fails again)."""
    body, boundary = _multipart(
        {"model": model, "response_format": "verbose_json", "temperature": "0"},
        audio_path)
    last_exc: Exception | None = None
    for attempt in range(RETRIES):
        req = urllib.request.Request(
            endpoint, data=body, method="POST",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": f"multipart/form-data; boundary={boundary}",
                     "User-Agent": "consume-skill/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and exc.code < 500:
                raise
            retry_after = float(exc.headers.get("retry-after") or 0)
            wait = max(retry_after, 15.0 * (attempt + 1))
            print(f"[watch] HTTP {exc.code}, retry in {wait:.0f}s "
                  f"(attempt {attempt + 1}/{RETRIES})", file=sys.stderr)
            last_exc = exc
            time.sleep(wait)
    raise last_exc  # retries exhausted — let the caller decide (never silent)


def _segs_from_verbose(data: dict, offset: float) -> list[dict]:
    out = []
    for s in data.get("segments") or []:
        t = (s.get("text") or "").strip()
        if t:
            out.append({"start": round(float(s.get("start", 0)) + offset, 2),
                        "end": round(float(s.get("end", 0)) + offset, 2),
                        "text": t})
    if not out and (data.get("text") or "").strip():  # no segments → one block
        out.append({"start": round(offset, 2), "end": round(offset, 2),
                    "text": data["text"].strip()})
    return out


def _transcribe_api(audio_path: Path, endpoint: str, key: str, model: str, label: str) -> list[dict]:
    """Transcribe via an HTTP backend, chunking if the audio is long."""
    dur = _duration(audio_path)
    if dur <= CHUNK_SECONDS:
        data = _post(endpoint, key, model, audio_path)
        segs = _segs_from_verbose(data, 0.0)
        print(f"[watch] {label}: {len(segs)} segments", file=sys.stderr)
        return segs

    # long audio → overlapping chunks so no sentence is cut at a boundary.
    # Each chunk covers [start, start+CHUNK]; the next starts CHUNK-OVERLAP later.
    # The chunks are independent HTTP calls, so we transcribe them IN PARALLEL
    # (Groq allows ~20 req/min) — far faster than sequential. We then sort by
    # offset and dedupe the overlap by absolute timestamp.
    import concurrent.futures

    step = max(1, CHUNK_SECONDS - CHUNK_OVERLAP)
    starts = []
    s = 0.0
    while s < dur:
        starts.append(s)
        s += step
    print(f"[watch] {label}: audio is {dur/60:.0f}min — {len(starts)} chunks of "
          f"{CHUNK_SECONDS//60}min (overlap {CHUNK_OVERLAP}s), in parallel…", file=sys.stderr)

    tmp = audio_path.parent

    def do_chunk(idx_start):
        idx, start = idx_start
        piece = _slice_audio(audio_path, start, CHUNK_SECONDS, tmp / f"chunk_{idx}.mp3")
        try:
            return start, _segs_from_verbose(_post(endpoint, key, model, piece), start)
        except urllib.error.HTTPError as exc:
            print(f"[watch] {label} chunk @{int(start)}s failed: {exc}", file=sys.stderr)
            return start, []

    # cap concurrency: stay under Groq's 20 req/min and avoid hammering
    workers = min(8, len(starts))
    results: list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(do_chunk, list(enumerate(starts))))

    # merge in chronological order, dropping overlap duplicates by timestamp
    results.sort(key=lambda r: r[0])
    all_segs: list[dict] = []
    last_end = 0.0
    for _start, segs in results:
        for seg in segs:
            if seg["start"] >= last_end - 0.5:
                all_segs.append(seg)
                last_end = max(last_end, seg["end"])
    print(f"[watch] {label}: {len(all_segs)} segments ({len(starts)} chunks)", file=sys.stderr)
    return all_segs


# --- OpenRouter (audio-capable chat model — no per-segment timestamps) ----
def _transcribe_openrouter(audio_path: Path) -> list[dict]:
    """Transcribe via an audio-capable chat model on OpenRouter. The chat API
    returns plain text, so each chunk becomes ONE segment stamped with the
    chunk's start time — coarse timestamps, fine for reading, not for
    frame-alignment. Chunks run in parallel with the same retry policy."""
    import base64
    import concurrent.futures

    dur = _duration(audio_path)
    starts = [float(s) for s in range(0, max(int(dur), 1), CHUNK_SECONDS)]
    tmp = audio_path.parent
    if len(starts) > 1:
        print(f"[watch] openrouter/{OPENROUTER_MODEL}: audio is {dur/60:.0f}min — "
              f"{len(starts)} chunks, in parallel…", file=sys.stderr)

    def do_chunk(idx_start):
        idx, start = idx_start
        # always re-encode the slice to mp3 so the declared format is true
        piece = _slice_audio(audio_path, start, CHUNK_SECONDS, tmp / f"or_chunk_{idx}.mp3")
        payload = json.dumps({
            "model": OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text":
                 "Transcribe this audio verbatim, in its original language. "
                 "Output ONLY the transcription text, nothing else."},
                {"type": "input_audio",
                 "input_audio": {"data": base64.b64encode(piece.read_bytes()).decode(),
                                 "format": "mp3"}},
            ]}],
        }).encode()
        last_exc: Exception | None = None
        for attempt in range(RETRIES):
            req = urllib.request.Request(
                OPENROUTER_ENDPOINT, data=payload, method="POST",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}",
                         "Content-Type": "application/json",
                         "User-Agent": "consume-skill/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    data = json.loads(r.read().decode())
                text = (((data.get("choices") or [{}])[0].get("message") or {})
                        .get("content") or "").strip()
                piece.unlink(missing_ok=True)
                if not text:
                    return start, []
                return start, [{"start": round(start, 2),
                                "end": round(min(start + CHUNK_SECONDS, dur), 2),
                                "text": text}]
            except urllib.error.HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise
                wait = max(float(exc.headers.get("retry-after") or 0), 15.0 * (attempt + 1))
                print(f"[watch] openrouter HTTP {exc.code}, retry in {wait:.0f}s "
                      f"(attempt {attempt + 1}/{RETRIES})", file=sys.stderr)
                last_exc = exc
                time.sleep(wait)
        raise last_exc

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(starts))) as ex:
        results = list(ex.map(do_chunk, list(enumerate(starts))))
    results.sort(key=lambda r: r[0])
    segs = [seg for _start, chunk_segs in results for seg in chunk_segs]
    print(f"[watch] openrouter/{OPENROUTER_MODEL}: {len(segs)} blocks "
          f"(no per-segment timestamps)", file=sys.stderr)
    return segs


# --- local (faster-whisper) ----------------------------------------------
def _load_local_model(model_id: str = LOCAL_MODEL_ID):
    """Load faster-whisper once (GPU, CPU fallback). On no GPU it falls back to
    CPU — slow; warn so the user knows to set an API key instead."""
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(model_id, device=LOCAL_DEVICE, compute_type=LOCAL_COMPUTE), f"{LOCAL_DEVICE}/{LOCAL_COMPUTE}"
    except Exception as exc:
        print(f"[watch] no GPU for local transcription ({exc}). Falling back to CPU "
              "(slow). Tip: set GROQ_API_KEY (free) to offload transcription — "
              "see config.py.", file=sys.stderr)
        return WhisperModel(model_id, device="cpu", compute_type="int8"), "cpu/int8"


def _transcribe_local(audio_path: Path, model=None, fast: bool = False) -> list[dict]:
    if model is None:
        model, used = _load_local_model(LOCAL_FAST_MODEL_ID if fast else LOCAL_MODEL_ID)
        print(f"[watch] local model loaded ({used}, {'fast' if fast else 'best'})", file=sys.stderr)
    segments, info = model.transcribe(
        str(Path(audio_path).resolve()), language=None, beam_size=1,
        vad_filter=True, condition_on_previous_text=False)
    out = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
           for s in segments if (s.text or "").strip()]
    print(f"[watch] local transcribe: {len(out)} segments, lang={info.language}", file=sys.stderr)
    return out


# --- public API -----------------------------------------------------------
def transcribe(audio_path) -> list[dict]:
    """Transcribe an audio file to absolute-timestamped segments.

    Picks the backend per WATCH_TRANSCRIBE / available keys. Always returns
    [{start, end, text}] with timestamps (Groq, OpenAI whisper-1, and local
    all preserve them).
    """
    audio_path = Path(audio_path)
    backend = choose_backend()
    if backend == "groq":
        if not GROQ_KEY:
            raise SystemExit("WATCH_TRANSCRIBE=groq but GROQ_API_KEY is not set")
        return _transcribe_api(audio_path, GROQ_ENDPOINT, GROQ_KEY, GROQ_MODEL, f"groq/{GROQ_MODEL}")
    if backend == "openai":
        if not OPENAI_KEY:
            raise SystemExit("WATCH_TRANSCRIBE=openai but OPENAI_API_KEY is not set")
        return _transcribe_api(audio_path, OPENAI_ENDPOINT, OPENAI_KEY, OPENAI_MODEL, "openai/whisper-1")
    if backend == "openrouter":
        if not OPENROUTER_KEY:
            raise SystemExit("WATCH_TRANSCRIBE=openrouter but OPENROUTER_API_KEY is not set")
        return _transcribe_openrouter(audio_path)
    return _transcribe_local(audio_path, fast=(backend == "local-fast"))


def transcribe_many(audio_paths: list) -> list[list[dict]]:
    """Transcribe several files efficiently, returning one segment-list each
    (same order as input). For batch use (e.g. every reel in a profile):
      - local backend: load the model ONCE and reuse it across files (the GPU
        fits one model), transcribing sequentially.
      - API backend: fire the per-file calls in parallel (independent HTTP).
    A file that fails yields an empty list rather than aborting the batch.
    """
    paths = [Path(p) for p in audio_paths]
    backend = choose_backend()
    if backend.startswith("local"):
        model, used = _load_local_model(
            LOCAL_FAST_MODEL_ID if backend == "local-fast" else LOCAL_MODEL_ID)
        print(f"[watch] local model loaded once ({used}) for {len(paths)} file(s)", file=sys.stderr)
        results = []
        for p in paths:
            try:
                results.append(_transcribe_local(p, model=model))
            except Exception as exc:
                print(f"[watch] transcribe failed for {p.name}: {exc}", file=sys.stderr)
                results.append([])
        return results

    # API: independent calls → parallel
    import concurrent.futures

    def one(p):
        try:
            return transcribe(p)
        except Exception as exc:
            print(f"[watch] transcribe failed for {p.name}: {exc}", file=sys.stderr)
            return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(paths) or 1)) as ex:
        return list(ex.map(one, paths))


def format_transcript(segs: list[dict]) -> str:
    return "\n".join(f"[{int(s['start'])//60:02d}:{int(s['start'])%60:02d}] {s['text']}" for s in segs)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: transcribe.py <audio-file>", file=sys.stderr)
        raise SystemExit(2)
    print(f"[watch] backend: {choose_backend()}", file=sys.stderr)
    print(format_transcript(transcribe(sys.argv[1])))
