#!/usr/bin/env python3
"""Config for /consume — read settings from env vars OR a persistent .env file.

Keys/settings can come from two places, in priority order:
  1. Environment variables (for people who already manage env vars)
  2. ~/.config/consume/.env  (persistent — set once, survives across sessions)

This lets a non-technical user just say "my Groq key is X" and have it saved to
the .env, instead of editing shell profiles. Works on Linux, macOS, and Windows
(uses Path.home()).

Recognized settings:
  GROQ_API_KEY              transcription via Groq (free tier, fast, timestamps)
  OPENAI_API_KEY            transcription via OpenAI whisper-1 (timestamps)
  WATCH_TRANSCRIBE          auto | groq | openai | local   (default auto)
  WATCH_YOUTUBE_API_KEY     richer/faster YouTube channel metrics (optional)
  WATCH_COOKIES_FROM_BROWSER  e.g. "chrome:Profile 1" — for IG/Reddit/LinkedIn/threads
  WATCH_WHISPER_MODEL/DEVICE/COMPUTE  local faster-whisper overrides (advanced)

CLI:
  python3 config.py                      # show current config (values masked)
  python3 config.py GROQ_API_KEY=gsk_... # set one or more KEY=VALUE
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "consume"
CONFIG_FILE = CONFIG_DIR / ".env"

# Settings the skill knows about (used for `config.py` listing/validation).
KNOWN = [
    "GROQ_API_KEY", "OPENAI_API_KEY", "WATCH_TRANSCRIBE",
    "WATCH_YOUTUBE_API_KEY", "WATCH_COOKIES_FROM_BROWSER",
    "WATCH_WHISPER_MODEL", "WATCH_WHISPER_DEVICE", "WATCH_WHISPER_COMPUTE",
    "WATCH_CHUNK_SECONDS", "WATCH_CHUNK_OVERLAP", "WATCH_CONVERTEAI_REFERER",
    "WATCH_GROQ_MODEL",
]


def _read_env_file() -> dict[str, str]:
    if not CONFIG_FILE.exists():
        return {}
    out: dict[str, str] = {}
    for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
            v = v[1:-1]
        out[k.strip()] = v
    return out


def get(name: str, default: str | None = None) -> str | None:
    """Env var first, then the .env file, then default."""
    v = os.environ.get(name)
    if v and v.strip():
        return v.strip()
    v = _read_env_file().get(name)
    return v.strip() if v and v.strip() else default


def load_into_environ() -> None:
    """Populate os.environ from the .env for any keys not already set, so the
    rest of the scripts (which read os.environ) pick them up transparently."""
    for k, v in _read_env_file().items():
        os.environ.setdefault(k, v)


def set_values(pairs: dict[str, str]) -> None:
    """Write/update KEY=VALUE pairs in the .env (created at 0600)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = _read_env_file()
    existing.update({k: v for k, v in pairs.items() if v})
    lines = ["# /consume config — written by config.py. Values are secrets; keep private.\n"]
    for k, v in existing.items():
        lines.append(f"{k}={v}\n")
    CONFIG_FILE.write_text("".join(lines), encoding="utf-8")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass  # Windows may not support chmod the same way


def _mask(v: str) -> str:
    if len(v) <= 8:
        return "****"
    return v[:4] + "…" + v[-4:]


def main() -> int:
    args = sys.argv[1:]
    if not args:
        # show current config (masked)
        print(f"# config file: {CONFIG_FILE}{'  (exists)' if CONFIG_FILE.exists() else '  (not created yet)'}\n")
        any_set = False
        for k in KNOWN:
            v = get(k)
            if v:
                any_set = True
                src = "env" if os.environ.get(k) else "file"
                masked = v if k in ("WATCH_TRANSCRIBE", "WATCH_COOKIES_FROM_BROWSER",
                                    "WATCH_WHISPER_MODEL", "WATCH_WHISPER_DEVICE",
                                    "WATCH_WHISPER_COMPUTE", "WATCH_CHUNK_SECONDS",
                                    "WATCH_CHUNK_OVERLAP") else _mask(v)
                print(f"  {k} = {masked}  [{src}]")
        if not any_set:
            print("  (nothing configured — using defaults: local transcription)")
        return 0

    pairs: dict[str, str] = {}
    for a in args:
        if "=" not in a:
            print(f"skipping {a!r} (expected KEY=VALUE)", file=sys.stderr)
            continue
        k, _, v = a.partition("=")
        k = k.strip()
        if k not in KNOWN:
            print(f"[config] warning: {k} is not a recognized setting (saving anyway)", file=sys.stderr)
        pairs[k] = v.strip()
    if pairs:
        set_values(pairs)
        print(f"[config] saved {', '.join(pairs)} to {CONFIG_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
