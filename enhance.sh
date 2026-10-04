#!/bin/bash
# Convenience wrapper: runs enhance.py with the project's .venv python. All arguments are passed through, e.g.
#   ./enhance.sh raw/episode.mp3 --start 10:00 --preset clean,natural,warm --include-original   # 3-min A/B snippets
#   ./enhance.sh raw/episode.mp3 --preset natural --mix 30 --set orig_source=raw                  # full file
source "$(dirname "$0")/scripts/common.sh"
exec "$(pyenv "$ROOT/.venv")" "$ROOT/enhance.py" "$@"
