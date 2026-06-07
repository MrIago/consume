#!/usr/bin/env python3
"""Preflight: verify the tools /consume needs are installed.

Run before the cascade so a missing dependency produces a clear, actionable
message instead of a raw error deep inside a script.

    python3 check.py            # human-readable, exit 0 if all good
    python3 check.py --json     # machine-readable status

Checks:
  - yt-dlp   (required: download, captions, metadata, stream URLs)
  - ffmpeg   (required: frames, audio extraction)
  - faster-whisper (optional: LOCAL transcription only — NOT needed if a Groq or
    OpenAI key is configured, since transcription then runs via the API)
  - curl_cffi / cookies are platform-specific and checked by those platforms.

Transcription needs one of: a GPU + faster-whisper (local, free), or GROQ_API_KEY
/ OPENAI_API_KEY (cloud, works without a GPU). Configure keys with
scripts/lib/config.py. secretstorage is Linux-only (macOS/Windows read cookies
without it).
"""
from __future__ import annotations

import json
import shutil
import sys

# (name, kind, required, install hint)
DEPS = [
    ("yt-dlp", "bin", True, "pipx install yt-dlp   (or: brew install yt-dlp)"),
    ("ffmpeg", "bin", True, "brew install ffmpeg   (or: sudo apt install ffmpeg)"),
    ("ffprobe", "bin", True, "comes with ffmpeg"),
    ("faster_whisper", "py", False, "pip install faster-whisper   (needed for local transcription)"),
    ("gallery-dl", "bin", False, "pip install gallery-dl   (needed for Instagram and Twitter/X)"),
    ("secretstorage", "py", False, "pip install secretstorage   (Linux: read Chrome cookies for Instagram/threads)"),
    ("curl_cffi", "py", False, "pip install curl_cffi   (needed for Twitter/X impersonation)"),
]


def _present(name: str, kind: str) -> bool:
    if kind == "bin":
        return shutil.which(name) is not None
    try:
        __import__(name)
        return True
    except Exception:
        return False


def status() -> dict:
    results = []
    ok_required = True
    for name, kind, required, hint in DEPS:
        present = _present(name, kind)
        if required and not present:
            ok_required = False
        results.append({"name": name, "required": required, "present": present, "hint": hint})
    return {"ready": ok_required, "deps": results}


def main() -> int:
    s = status()
    if "--json" in sys.argv:
        json.dump(s, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if s["ready"] else 1

    missing_req = [d for d in s["deps"] if d["required"] and not d["present"]]
    missing_opt = [d for d in s["deps"] if not d["required"] and not d["present"]]

    if s["ready"] and not missing_opt:
        # Silent-ish success: one line so the caller knows it ran.
        print("[check] all dependencies present.", file=sys.stderr)
        return 0

    for d in missing_req:
        print(f"[check] MISSING (required): {d['name']} — {d['hint']}", file=sys.stderr)
    for d in missing_opt:
        print(f"[check] missing (optional): {d['name']} — {d['hint']}", file=sys.stderr)
    return 0 if s["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
