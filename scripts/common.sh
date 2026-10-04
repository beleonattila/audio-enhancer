# Sourced by the shell scripts: resolves the repo root, venv pythons and ffmpeg.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# venv python for Windows (Scripts/python.exe) or POSIX (bin/python)
pyenv() { if [ -x "$1/Scripts/python.exe" ]; then echo "$1/Scripts/python.exe"; else echo "$1/bin/python"; fi; }

# ffmpeg: $FFMPEG, else ffmpeg on PATH, else the imageio-ffmpeg binary installed in .venv by setup.sh
if [ -z "$FFMPEG" ]; then
  if command -v ffmpeg >/dev/null 2>&1; then FFMPEG=ffmpeg
  else FFMPEG="$("$(pyenv "$ROOT/.venv")" -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"; fi
fi
