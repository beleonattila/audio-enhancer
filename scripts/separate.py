"""2-speaker separation with ClearerVoice MossFormer2_SS_16K, segmented for long files.

usage: python separate.py in.wav out_prefix [--seg 8] [--ov 2]   -> out_prefix_1.wav, out_prefix_2.wav (16 kHz mono)
env:   envs/clearvoice

- Each segment's two streams are rescaled (least squares) so that s1 + s2 reproduces the mixture; ClearerVoice
  otherwise normalises every stream to the mixture's RMS, which hugely amplifies a speaker who is silent in a segment.
- Speaker order is kept consistent across segments by matching the overlap with the previous segment.
- Segments are crossfaded (sin²/cos²) over the overlap.
"""
import argparse, os, sys, time
import numpy as np, soundfile as sf, soxr, torch

ap = argparse.ArgumentParser()
ap.add_argument('inp'); ap.add_argument('out_prefix')
ap.add_argument('--seg', type=float, default=8.0, help='segment length in s (model was trained on short mixtures)')
ap.add_argument('--ov', type=float, default=2.0, help='overlap in s, used for speaker-order matching and crossfade')
a = ap.parse_args()
out1, out2 = (os.path.abspath(a.out_prefix + f'_{i}.wav') for i in (1, 2))
inp = os.path.abspath(a.inp)

os.chdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models', 'clearvoice'))  # checkpoint dir
from clearvoice import ClearVoice
m = ClearVoice(task='speech_separation', model_names=['MossFormer2_SS_16K']).models[0]
m.args.one_time_decode_length = 1e6          # we segment ourselves

SR = 16000
x, sr = sf.read(inp, dtype='float32', always_2d=True)
x = soxr.resample(x.mean(1), sr, SR, quality='VHQ').astype(np.float32)
N, seg, ov = len(x), int(a.seg * SR), int(a.ov * SR)
fade = (np.sin(np.linspace(0, np.pi / 2, ov, endpoint=False)) ** 2).astype(np.float32)
y = np.zeros((2, N), np.float32)
t0, pos, prev_end = time.time(), 0, 0
while pos < N:
    end = min(N, pos + seg)
    mix = x[pos:end]
    with torch.no_grad():
        s = np.asarray(m.decode_data(mix[None, :])).reshape(2, -1)[:, :len(mix)].astype(np.float32)
    # rescale streams so that g1*s1 + g2*s2 ≈ mix
    A = s.T.astype(np.float64)
    g, *_ = np.linalg.lstsq(A, mix.astype(np.float64), rcond=None)
    s = (s * np.clip(g, 0, None)[:, None]).astype(np.float32)
    if pos > 0:
        k = min(ov, prev_end - pos, len(mix))
        old = y[:, pos:pos + k]
        same = np.abs(np.dot(old[0], s[0, :k])) + np.abs(np.dot(old[1], s[1, :k]))
        swap = np.abs(np.dot(old[0], s[1, :k])) + np.abs(np.dot(old[1], s[0, :k]))
        if swap > same:
            s = s[::-1].copy()
        y[:, pos:pos + k] = old * (1 - fade[:k]) + s[:, :k] * fade[:k]
        y[:, pos + k:end] = s[:, k:]
    else:
        y[:, pos:end] = s
    prev_end = end
    print(f"seg {end / SR:.1f}/{N / SR:.1f}s", flush=True)
    if end >= N:
        break
    pos = end - ov

sf.write(out1, y[0], SR, subtype='FLOAT'); sf.write(out2, y[1], SR, subtype='FLOAT')
res = x - y.sum(0)
print(f"done {N / SR:.1f}s audio in {time.time() - t0:.1f}s; mixture residual {20 * np.log10(np.sqrt((res ** 2).mean()) / np.sqrt((x ** 2).mean())):.1f} dB; "
      f"stream levels {20 * np.log10(np.sqrt((y ** 2).mean(1)) + 1e-9).round(1)} dBFS")
