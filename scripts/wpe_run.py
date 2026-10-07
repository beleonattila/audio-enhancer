"""WPE dereverberation (nara_wpe, classical linear prediction), STFT domain, segmented for long files.

usage: wpe_run.py in.wav out.wav [--taps 10 --delay 3 --iters 3] [--mono] [--seg 60]   env: .venv
Multichannel input is processed jointly (better: reverb differs between microphones); output keeps all channels.
"""
import argparse
import numpy as np, soundfile as sf
from nara_wpe.wpe import wpe
from nara_wpe.utils import stft, istft

ap = argparse.ArgumentParser(); ap.add_argument('inp'); ap.add_argument('out')
ap.add_argument('--taps', type=int, default=10); ap.add_argument('--delay', type=int, default=3)
ap.add_argument('--iters', type=int, default=3); ap.add_argument('--mono', action='store_true')
ap.add_argument('--seg', type=float, default=60.0); ap.add_argument('--ov', type=float, default=1.0)
a = ap.parse_args()
size, shift = 1024, 256
info = sf.info(a.inp); sr = info.samplerate; N = info.frames
D = 1 if a.mono else info.channels
seg, ov = int(a.seg * sr), int(a.ov * sr)
fade = (np.sin(np.linspace(0, np.pi / 2, ov, endpoint=False)) ** 2)[:, None]
tail, pos = None, 0
with sf.SoundFile(a.out, 'w', samplerate=sr, channels=D, subtype='FLOAT') as fo:
    while pos < N:
        end = min(N, pos + seg)
        x, _ = sf.read(a.inp, start=pos, stop=end, dtype='float64', always_2d=True)
        x = x.mean(1, keepdims=True).T if a.mono else x.T                      # (D, T)
        Y = stft(x, size=size, shift=shift).transpose(2, 0, 1)                 # (F, D, frames)
        Z = wpe(Y, taps=a.taps, delay=a.delay, iterations=a.iters, statistics_mode='full').transpose(1, 2, 0)
        z = istft(Z, size=size, shift=shift)[:, :x.shape[1]].T                  # (T, D)
        if tail is not None:
            k = min(ov, len(z)); z[:k] = tail[:k] * (1 - fade[:k]) + z[:k] * fade[:k]
        last = end >= N
        fo.write((z if last else z[:len(z) - ov]).astype(np.float32))
        tail = z[len(z) - ov:]
        print(f"seg {end / sr:.1f}/{N / sr:.1f}s", flush=True)
        if last: break
        pos = end - ov
