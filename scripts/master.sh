#!/bin/bash
# Mastering: master.sh in.wav out_basename -> out_basename.wav (48 kHz / 24-bit stereo, -16 LUFS integrated, -1.5 dBTP)
#
# Chain: pre-gain to -21.5 LUFS (makes the expander threshold level-independent) -> 80 Hz high-pass ->
# tonal EQ (-1.5 dB @250 Hz boxiness, +2 dB @1.2 kHz, +2.5 dB @4.2 kHz presence, +1.5 dB air shelf) ->
# de-esser -> soft downward expander for pauses -> gentle 2:1 compressor to level the speakers ->
# two-pass linear loudnorm.
set -e
source "$(dirname "$0")/common.sh"
IN="$1"; OUT="$2"

I0=$("$FFMPEG" -hide_banner -nostats -i "$IN" -af ebur128 -f null - 2>&1 | grep -E "^\s+I:" | tail -1 | awk '{print $2}')
PRE=$(awk "BEGIN{print -21.5 - ($I0)}")
echo "input I=$I0 LUFS, pre-gain ${PRE} dB"
CHAIN="volume=${PRE}dB,highpass=f=80:poles=2,equalizer=f=250:t=o:w=1.2:g=-1.5,equalizer=f=1200:t=o:w=1.5:g=2,equalizer=f=4200:t=o:w=1.3:g=2.5,highshelf=f=10000:g=1.5,deesser=i=0.35:m=0.5:f=0.5,agate=threshold=0.008:ratio=1.6:range=0.3:attack=8:release=250:knee=4,acompressor=threshold=-20dB:ratio=2:attack=10:release=200:knee=8:makeup=1.5"

J=$("$FFMPEG" -hide_banner -nostats -i "$IN" -af "$CHAIN,loudnorm=I=-16:TP=-1.5:LRA=8:print_format=json" -f null - 2>&1 | sed -n '/^{/,/^}/p')
g() { echo "$J" | grep "\"$1\"" | sed 's/.*: "\(.*\)".*/\1/'; }
LN="loudnorm=I=-16:TP=-1.5:LRA=8:measured_I=$(g input_i):measured_TP=$(g input_tp):measured_LRA=$(g input_lra):measured_thresh=$(g input_thresh):offset=$(g target_offset):linear=true"
"$FFMPEG" -hide_banner -loglevel error -y -i "$IN" -af "$CHAIN,$LN,aresample=48000:filter_size=64:cutoff=0.97" -ac 2 -c:a pcm_s24le "$OUT.wav"
