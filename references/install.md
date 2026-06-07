# Guided setup — inspect the machine, install only what's missing

Read this the **first time** the skill is used on a machine, or whenever
`check.py` reports something missing. Don't run a canned script — **inspect the
user's actual system and build the exact commands for it**, installing only
what's absent. Confirm with the user before running anything that needs sudo or a
system change.

## Step 1 — See what's already there

Run the preflight first; it tells you what's missing and the platform:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/check.py" || python "${CLAUDE_SKILL_DIR}/scripts/check.py"
```

Then probe the environment yourself (only what you still need to know):

```bash
uname -s                          # Linux / Darwin (macOS); on Windows you'll be in PowerShell
python3 --version 2>/dev/null || python --version 2>/dev/null || echo "no python"
command -v ffmpeg yt-dlp gallery-dl 2>/dev/null
# package manager available:
command -v apt dnf pacman zypper brew winget choco 2>/dev/null
# GPU (decides local-vs-API transcription):
command -v nvidia-smi >/dev/null && nvidia-smi -L 2>/dev/null || echo "no NVIDIA GPU"
```

On **Windows**, the equivalents are PowerShell: `Get-Command python,ffmpeg,yt-dlp`,
`winget --version`, and `Get-CimInstance Win32_VideoController` for the GPU.

## Step 2 — Decide the plan from what you found

Install **only the missing pieces**, globally (no venv — keep it simple). Pick
the package manager that actually exists on the machine. Map:

| Need | Linux (apt/dnf/pacman) | macOS (brew) | Windows (winget) |
|------|------------------------|--------------|------------------|
| python3 | `sudo apt install python3 python3-pip` / `sudo dnf install python3 python3-pip` / `sudo pacman -S python python-pip` | `brew install python` | `winget install Python.Python.3.12` |
| ffmpeg (+ffprobe) | `sudo apt install ffmpeg` / `dnf` / `pacman -S ffmpeg` | `brew install ffmpeg` | `winget install Gyan.FFmpeg` |
| yt-dlp | `sudo apt install yt-dlp` or `pipx install yt-dlp` | `brew install yt-dlp` | `winget install yt-dlp.yt-dlp` |
| gallery-dl (IG/Reddit/X) | `pipx install gallery-dl` | `brew install gallery-dl` | `pip install gallery-dl` |

Python libs (always via pip — global is fine here):
```bash
pip install curl_cffi            # Twitter/X
pip install secretstorage        # LINUX ONLY (read Chrome cookies); skip on mac/win
```

- If `pip` complains about an externally-managed environment (PEP 668, common on
  Debian/Ubuntu/Homebrew), use `pip install --user --break-system-packages <pkg>`
  or prefer `pipx` for the CLI tools. Explain the flag before using it.
- If no package manager exists: on macOS offer to install Homebrew first
  (`/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`);
  on Windows winget ships with Windows 10/11; on Linux it's whatever the distro has.

## Step 3 — Transcription: ask before installing the heavy bit

`faster-whisper` pulls in PyTorch/CUDA (~2 GB) — don't install it blindly. Decide
with the user based on the GPU probe:

- **Has an NVIDIA GPU and wants free local transcription** → `pip install faster-whisper`.
- **No GPU, or prefers cloud** → skip faster-whisper entirely; set a key instead:
  ```bash
  python3 "${CLAUDE_SKILL_DIR}/scripts/lib/config.py" GROQ_API_KEY=...
  ```
  Groq has a generous free tier and needs no GPU — the best default for most
  people. (See the Setup section of SKILL.md.)

Ask: *"Do you have an NVIDIA GPU and want free local transcription, or should we
use a free Groq API key (no GPU needed)?"* — then install only that path.

## Step 4 — Verify

Re-run the preflight; it should be clean for what the user chose:
```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/check.py"
```
Optional pieces (faster-whisper if they picked cloud; secretstorage off Linux)
showing as "missing (optional)" is fine — that's expected for their setup.

## Notes

- **Only install what's missing.** If `check.py` already shows ffmpeg present,
  don't touch it.
- **Confirm before sudo / system changes.** Show the user the exact command and
  let them approve.
- This is on purpose a *doc*, not a script: every machine differs, and you can
  read the system and assemble the right commands far better than a static
  installer that guesses one package manager.
