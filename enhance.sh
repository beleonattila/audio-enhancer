#!/bin/bash
# Podcast speech restoration: Sidon (generative restoration, 48 kHz) -> DeepFilterNet3 (residual noise, 15 dB limit) -> mastering.
# Usage: ./enhance.sh input.mp3 [more files...]
#   -> "input - enhanced.wav" (48 kHz / 24-bit, -16 LUFS) and "input - enhanced.mp3" (192 kbps, tags + cover art copied)
set -e
source "$(dirname "$0")/scripts/common.sh"
[ $# -ge 1 ] || { echo "usage: $0 input.(mp3|wav|m4a|...) [...]"; exit 1; }

for IN in "$@"; do
  BASE="${IN%.*} - enhanced"
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  echo "== $IN"

  "$FFMPEG" -hide_banner -loglevel error -y -i "$IN" -map 0:a:0 -ac 1 -c:a pcm_f32le "$TMP/mono.wav"
  "$(pyenv "$ROOT/envs/sidon")" "$ROOT/scripts/sidon_enhance.py" "$TMP/mono.wav" "$TMP/sidon.wav" --seg 30 | tail -1
  "$(pyenv "$ROOT/envs/dfn")" "$ROOT/scripts/dfn_enhance.py" "$TMP/sidon.wav" "$TMP/clean.wav" --atten 15 | tail -1
  "$ROOT/scripts/master.sh" "$TMP/clean.wav" "$TMP/master"
  mv "$TMP/master.wav" "$BASE.wav"

  if "$FFMPEG" -hide_banner -i "$IN" 2>&1 | grep -q "attached pic"; then
    "$FFMPEG" -hide_banner -loglevel error -y -i "$BASE.wav" -i "$IN" -map 0:a -map 1:v -map_metadata 1 \
      -c:a libmp3lame -b:a 192k -c:v copy -id3v2_version 3 -disposition:v attached_pic "$BASE.mp3"
  else
    "$FFMPEG" -hide_banner -loglevel error -y -i "$BASE.wav" -i "$IN" -map 0:a -map_metadata 1 \
      -c:a libmp3lame -b:a 192k -id3v2_version 3 "$BASE.mp3"
  fi
  rm -rf "$TMP"
  echo "done: $BASE.wav / $BASE.mp3"
done
